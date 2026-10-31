"""Sestavení statických výstupů (data/*.json) včetně historie sejmutých položek.

Výstup:
  boards.json – seznam desek a stav posledního sběru
  items.json  – všechny položky (aktuální i archiv sejmutých)
  meta.json   – souhrnné statistiky běhu
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .fetch import FetchResult
from .nkod import BoardSource
from .normalize import BoardFormatError, Document, Item, normalize_board

log = logging.getLogger(__name__)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


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
        board = {**asdict(src), "ok": res.ok, "error": res.error, "http_status": res.http_status,
                 "elapsed_ms": res.elapsed_ms, "fetched_at": stamp, "count": 0}
        current: list[Item] = []
        if res.ok:
            try:
                current = normalize_board(src.id, res.payload or {})
            except BoardFormatError as exc:
                board.update(ok=False, error=str(exc))
        if not board["ok"]:
            for it in by_board.get(src.id, []):
                out[it.id] = it
            boards.append(board)
            continue

        seen = set()
        for it in current:
            old = prev.get(it.id)
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
    items = [
        it for it in out.values()
        if it.board in known and not (it.removed and it.removed < cutoff)
    ]
    items.sort(key=lambda i: (i.posted or "", i.first_seen or ""), reverse=True)
    return boards, items


def write_outputs(out_dir: Path, boards: list[dict], items: list[Item], now: datetime) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.isoformat(timespec="seconds")
    meta = {
        "generated_at": stamp,
        "boards_total": len(boards),
        "boards_ok": sum(1 for b in boards if b["ok"]),
        "items_active": sum(1 for i in items if not i.removed),
        "items_archived": sum(1 for i in items if i.removed),
    }
    dump = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    (out_dir / "boards.json").write_text(dump(boards), "utf-8")
    (out_dir / "items.json").write_text(dump([i.to_json() for i in items]), "utf-8")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    return meta


def run_build(
    sources: list[BoardSource],
    results: dict[str, FetchResult],
    out_dir: Path,
    previous_dir: Path | None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    previous = _load_json(previous_dir / "items.json", []) if previous_dir else []
    boards, items = merge(sources, results, previous, now)
    meta = write_outputs(out_dir, boards, items, now)
    log.info("Hotovo: %s", meta)
    return meta
