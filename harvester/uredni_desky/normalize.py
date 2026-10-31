"""Převod JSON-LD dle OFN Úřední desky na interní, kompaktní model položky."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

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


def parse_payload(content: bytes) -> dict:
    """Dekóduje JSON; toleruje UTF-8 BOM a soubory v cp1250, které některé servery posílají."""
    try:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1250")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoardFormatError(f"Neplatný JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BoardFormatError("Kořen JSON není objekt")
    return data


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
