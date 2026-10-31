"""Převod JSON-LD dle OFN Úřední desky na interní, kompaktní model položky."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from typing import Any

import json_repair

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class BoardFormatError(ValueError):
    """Data desky neodpovídají OFN natolik, že je nelze zpracovat."""


@dataclass
class Document:
    name: str | None
    url: str


@dataclass
class Item:
    id: str
    board: str
    title: str
    url: str | None = None
    posted: str | None = None  # YYYY-MM-DD
    until: str | None = None  # YYYY-MM-DD, None = nespecifikováno
    cj: str | None = None  # číslo jednací
    sz: str | None = None  # spisová značka
    agendas: list[str] = field(default_factory=list)
    docs: list[Document] = field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    removed: str | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        # Vynechat prázdné hodnoty kvůli velikosti výstupu.
        return {k: v for k, v in data.items() if v not in (None, [], "")}


def text_cs(value: Any) -> str | None:
    """Vrátí český text z jazykové mapy {"cs": ...} nebo prostého řetězce."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("cs", "en"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        for v in value.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def date_of(value: Any) -> str | None:
    """Časový okamžik OFN ({"datum": ...} / {"datum_a_čas": ...}) → YYYY-MM-DD."""
    if not isinstance(value, dict):
        return None
    for key in ("datum", "datum_a_čas"):
        raw = value.get(key)
        if isinstance(raw, str) and _ISO_DATE.match(raw) and 1990 <= int(raw[:4]) <= 2100:
            return raw[:10]
    return None


def safe_url(value: Any) -> str | None:
    """Propustí jen http(s) URL – data jsou nedůvěryhodná a jdou do odkazů v UI."""
    if isinstance(value, str):
        v = value.strip()
        if v.lower().startswith(("https://", "http://")):
            return v
    return None


def _first_str(value: Any) -> str | None:
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str)), None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def item_id(board: str, raw: dict, title: str, posted: str | None) -> str:
    key = safe_url(raw.get("iri")) or safe_url(raw.get("url")) or f"{title}|{posted}"
    return hashlib.sha1(f"{board}|{key}".encode("utf-8")).hexdigest()[:16]


def _unzip(content: bytes, warnings: list[str]) -> bytes:
    """Některé úřady registrují místo JSON souboru ZIP archiv – vezme se první JSON uvnitř."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        json_names = [n for n in names if n.lower().endswith((".json", ".jsonld"))] or names
        if not json_names:
            raise BoardFormatError("ZIP archiv neobsahuje žádný soubor")
        warnings.append("data jsou v ZIP archivu")
        return zf.read(json_names[0])


def _as_board(data: Any, warnings: list[str]) -> dict:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        boards = [d for d in data if isinstance(d, dict) and isinstance(d.get("informace"), list)]
        if boards:
            if len(boards) > 1 or len(data) > 1:
                warnings.append("kořen JSON je pole desek místo objektu")
            return {"informace": [i for b in boards for i in b["informace"]]}
        if data and all(isinstance(d, dict) for d in data) and any("název" in d for d in data):
            warnings.append("kořen JSON je pole položek místo objektu desky")
            return {"informace": data}
        if not data:
            return {"informace": []}
    raise BoardFormatError("Kořen JSON není objekt úřední desky")


def decode_board(content: bytes) -> tuple[dict, list[str]]:
    """Dekóduje data desky co nejtolerantněji a vrátí (data, upozornění).

    Zvládá: UTF-8 BOM, cp1250, ZIP archiv, řídicí znaky v řetězcích, data za koncem JSON,
    pole místo objektu a jako poslední možnost automatickou opravu poškozeného JSON.
    """
    warnings: list[str] = []
    if content[:4] == b"PK\x03\x04":
        try:
            content = _unzip(content, warnings)
        except zipfile.BadZipFile as exc:
            raise BoardFormatError(f"Poškozený ZIP archiv: {exc}") from exc
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1250", errors="replace")
        warnings.append("soubor není v UTF-8 (načten jako cp1250)")
    text = text.strip()
    if not text:
        raise BoardFormatError("Prázdná odpověď")
    if text.startswith("<"):
        raise BoardFormatError("Místo dat vrací HTML stránku")

    try:
        return _as_board(json.loads(text, strict=False), warnings), warnings
    except json.JSONDecodeError as exc:
        first_error = exc
    try:
        data, end = json.JSONDecoder(strict=False).raw_decode(text)
        warnings.append("za koncem JSON jsou nadbytečná data")
        return _as_board(data, warnings), warnings
    except json.JSONDecodeError:
        pass
    repaired = json_repair.loads(text)
    if isinstance(repaired, (dict, list)) and repaired:
        warnings.append(f"nevalidní JSON opraven automaticky ({first_error.msg}, řádek {first_error.lineno})")
        return _as_board(repaired, warnings), warnings
    raise BoardFormatError(f"Neplatný JSON: {first_error}")


def parse_payload(content: bytes) -> dict:
    return decode_board(content)[0]


def normalize_board(board: str, payload: dict) -> list[Item]:
    info = payload.get("informace")
    if info is None:
        info = []
    if not isinstance(info, list):
        raise BoardFormatError("Pole 'informace' není seznam")

    items: dict[str, Item] = {}
    for raw in info:
        if not isinstance(raw, dict):
            continue
        title = text_cs(raw.get("název"))
        if not title:
            continue
        posted = date_of(raw.get("vyvěšení"))
        docs = []
        for d in raw.get("dokument") or []:
            if isinstance(d, dict) and (u := safe_url(d.get("url"))):
                docs.append(Document(name=text_cs(d.get("název")), url=u))
        agendas = [
            a for a in (text_cs(x.get("název")) for x in raw.get("agenda") or [] if isinstance(x, dict)) if a
        ]
        iid = item_id(board, raw, title, posted)
        items[iid] = Item(
            id=iid,
            board=board,
            title=title,
            url=safe_url(raw.get("url")),
            posted=posted,
            until=date_of(raw.get("relevantní_do")),
            cj=_first_str(raw.get("číslo_jednací")),
            sz=_first_str(raw.get("spisová_značka")),
            agendas=agendas,
            docs=docs,
        )
    return list(items.values())
