"""Sestavení statických výstupů (data/*.json) včetně historie sejmutých položek.

Výstup pro web (out_dir):
  boards.json       – seznam desek, zobrazovaný název a stav posledního sběru
  recent.json       – odlehčené položky vyvěšené za posledních RECENT_DAYS dní (pro hledání)
  boards/<id>.json  – všechny položky jedné desky (aktuální i archiv)
  meta.json         – souhrnné statistiky běhu
Stav (state_dir):
  items.json        – plný seznam položek; vstup pro další běh (historie)
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from urllib.parse import urlsplit
import shutil
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import geo
from .fetch import FetchResult
from .nkod import BoardSource
from .normalize import BoardFormatError, Document, Item, normalize_board

log = logging.getLogger(__name__)

RECENT_DAYS = 30
_BOARD_PREFIX = re.compile(r"^(obsah\s+)?úřední\s+desk[ay]\s+(organizace\s+)?", re.IGNORECASE)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_state(state_dir: Path) -> list[dict]:
    gz = state_dir / "items.json.gz"
    if gz.exists():
        return json.loads(gzip.decompress(gz.read_bytes()).decode("utf-8"))
    return _load_json(state_dir / "items.json", [])  # starší formát stavu


def _item_from_json(d: dict) -> Item:
    d = dict(d)
    d["docs"] = [Document(**x) for x in d.get("docs", [])]
    return Item(**d)


def merge(
    sources: list[BoardSource],
    results: dict[str, FetchResult],
    previous_items: list[dict],
    now: datetime,
    archive_days: int = 365,
) -> tuple[list[dict], list[Item]]:
    """Sloučí nový sběr s předchozím stavem.

    - Položka nově nalezená dostane first_seen = now.
    - Položka z úspěšně stažené desky, která zmizela, dostane removed = now.
    - U desky, kterou se nepodařilo stáhnout, zůstávají předchozí položky beze změny.
    """
    stamp = now.isoformat(timespec="seconds")
    prev = {d["id"]: _item_from_json(d) for d in previous_items if "id" in d}
    by_board: dict[str, list[Item]] = {}
    for it in prev.values():
        by_board.setdefault(it.board, []).append(it)

    boards: list[dict] = []
    out: dict[str, Item] = {}
    for src in sources:
        res = results.get(src.id) or FetchResult(False, error="nestaženo")
        board = {**asdict(src), "ok": res.ok, "error": res.error, "kind": res.kind, "http_status": res.http_status,
                 "elapsed_ms": res.elapsed_ms, "fetched_at": stamp, "count": 0, "warnings": res.warnings,
                 "used_url": res.url, "via_proxy": res.via_proxy}
        current: list[Item] = []
        if res.ok:
            try:
                current = normalize_board(src.id, res.payload or {})
            except BoardFormatError as exc:
                board.update(ok=False, error=str(exc), kind="format")
        if not board["ok"]:
            for it in by_board.get(src.id, []):
                out[it.id] = it
            boards.append(board)
            continue

        seen = set()
        current_ids = {it.id for it in current}
        # Záložní párování podle (název, vyvěšeno): některé desky mezi exporty mění URL položky
        # (např. stránka oznámení ↔ přímý odkaz na PDF), a tím i její identifikátor.
        by_key: dict[tuple, list[Item]] = {}
        for old in by_board.get(src.id, []):
            if old.id not in current_ids and not old.removed:
                by_key.setdefault((old.title, old.posted), []).append(old)
        for it in current:
            old = prev.get(it.id)
            if old is None and (candidates := by_key.get((it.title, it.posted))):
                old = candidates.pop()
                seen.add(old.id)
            it.first_seen = old.first_seen if old else stamp
            it.last_seen = stamp
            out[it.id] = it
            seen.add(it.id)
        for old in by_board.get(src.id, []):
            if old.id not in seen:
                old.removed = old.removed or stamp
                out[old.id] = old
        board["count"] = len(current)
        boards.append(board)

    # Desky, které z NKOD zmizely, a příliš staré archivní položky zahodit.
    known = {s.id for s in sources}
    cutoff = (now - timedelta(days=archive_days)).isoformat(timespec="seconds")
    active_keys = {(it.board, it.title, it.posted) for it in out.values() if not it.removed}
    items = [
        it for it in out.values()
        if it.board in known and not (it.removed and it.removed < cutoff)
        # „Sejmutá“ položka, která má aktivní dvojče, vznikla změnou URL – nepatří do archivu.
        and not (it.removed and (it.board, it.title, it.posted) in active_keys)
    ]
    items.sort(key=lambda i: (i.posted or "", i.first_seen or ""), reverse=True)
    return boards, items


_LOWER_WORDS = {"město", "města", "městská", "městys", "obec", "obce", "část", "kraj", "úřad", "pro", "na", "nad",
                "pod", "v", "ve", "u", "a", "s", "se", "z", "ze", "do", "při"}


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) > 4 and s.isupper():  # „HLAVNÍ MĚSTO PRAHA“ → „Hlavní město Praha“
        words = s.title().split(" ")
        s = " ".join([words[0]] + [w.lower() if w.lower() in _LOWER_WORDS else w for w in words[1:]])
    return s


_NAME_RULES = [
    re.compile(r"^(obsah\s+)?úřední\s+desk[ay]\s*[-–:]?\s*", re.IGNORECASE),
    re.compile(r"^(organizace|úřadu:?)\s+", re.IGNORECASE),
    re.compile(r"^městské\s+části\s+", re.IGNORECASE),
    re.compile(r"\s*-\s*elektronická\s+úřední\s+deska$", re.IGNORECASE),
    re.compile(r"\s*\[\d+\]$"),
]


def _hint(src: BoardSource) -> str:
    if src.area:
        return f"okres {src.area}"
    host = (urlsplit(src.access_url).hostname or "").removeprefix("www.")
    return host


def display_name(src: BoardSource, ambiguous: bool, namesake: bool = False) -> str:
    """Název desky pro UI.

    Většinou je to poskytovatel (obec, úřad). Pokud jeden poskytovatel publikuje víc desek
    (např. Ministerstvo spravedlnosti za všechny soudy), odvodí se název z titulku desky.
    Obce se stejným názvem (několik „Obec Lhota“) se odliší okresem, případně doménou webu.
    """
    publisher = _clean(src.publisher)
    if src.fmt != "ofn" or (namesake and not ambiguous):
        # deska z webu obce: jedna obec = jedna deska, shodný název znamená jinou obec
        if (namesake or ambiguous) and (hint := _hint(src)):
            return f"{publisher} ({hint})"
        return publisher or _clean(src.title) or "Neznámá deska"
    if ambiguous and src.title:
        name = _clean(src.title)
        for rule in _NAME_RULES:
            name = rule.sub("", name).strip(" -–—:")
        if not name or name.lower().startswith(("města ", "(")):
            return publisher or _clean(src.title)
        if not re.search(r"[^\W\d_]", name):  # např. „Úřední deska 2025“
            return f"{publisher} ({name})"
        return name[0].upper() + name[1:]
    return publisher or _clean(src.title) or "Neznámá deska"


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")


def _slim(it: Item) -> dict:
    d = {"id": it.id, "b": it.board, "t": it.title, "p": it.posted, "u": it.until, "d": len(it.docs)}
    if it.removed:
        d["r"] = 1
    return {k: v for k, v in d.items() if v not in (None, 0)}


def write_outputs(out_dir: Path, boards: list[dict], items: list[Item], now: datetime) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.isoformat(timespec="seconds")

    ofn_pubs: dict[str, int] = {}  # víc desek jednoho poskytovatele v NKOD (ministerstvo za soudy)
    all_pubs: dict[str, int] = {}
    for b in boards:
        all_pubs[b["publisher"]] = all_pubs.get(b["publisher"], 0) + 1
        if b.get("fmt", "ofn") == "ofn":
            ofn_pubs[b["publisher"]] = ofn_pubs.get(b["publisher"], 0) + 1
    for b in boards:
        src = BoardSource(**{k: b[k] for k in ("id", "dataset_iri", "title", "publisher", "access_url")},
                          fmt=b.get("fmt", "ofn"), area=b.get("area", ""))
        b["name"] = display_name(src, ofn_pubs.get(b["publisher"], 0) > 1, all_pubs[b["publisher"]] > 1)

    since = (now - timedelta(days=RECENT_DAYS)).date().isoformat()
    recent = [_slim(i) for i in items if i.posted and i.posted >= since and not i.removed]

    board_dir = out_dir / "boards"
    shutil.rmtree(board_dir, ignore_errors=True)
    board_dir.mkdir()
    per_board: dict[str, list[dict]] = {}
    for i in items:
        per_board.setdefault(i.board, []).append(i.to_json())
    for b in boards:
        _dump(board_dir / f"{b['id']}.json", per_board.get(b["id"], []))

    meta = {
        "generated_at": stamp,
        "recent_days": RECENT_DAYS,
        "boards_total": len(boards),
        "boards_ok": sum(1 for b in boards if b["ok"]),
        "items_active": sum(1 for i in items if not i.removed),
        "items_archived": sum(1 for i in items if i.removed),
        "items_recent": len(recent),
    }
    _dump(out_dir / "boards.json", boards)
    _dump(out_dir / "recent.json", recent)
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    return meta


def run_build(
    sources: list[BoardSource],
    results: dict[str, FetchResult],
    out_dir: Path,
    state_dir: Path | None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    previous = load_state(state_dir) if state_dir else []
    boards, items = merge(sources, results, previous, now)
    if state_dir:
        geo.attach(boards, state_dir / "obce.json")
    meta = write_outputs(out_dir, boards, items, now)
    if state_dir:
        state_dir.mkdir(parents=True, exist_ok=True)
        # Komprimovaně: GitHub odmítá soubory nad 100 MB a nekomprimovaný stav má desítky MB.
        data = json.dumps([i.to_json() for i in items], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (state_dir / "items.json.gz").write_bytes(gzip.compress(data, mtime=0))
        (state_dir / "items.json").unlink(missing_ok=True)
        _dump(state_dir / "boards.json", boards)
    log.info("Hotovo: %s", meta)
    return meta
