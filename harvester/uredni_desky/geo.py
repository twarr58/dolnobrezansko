"""Poloha desek (pro hledání v okolí): souřadnice obcí z Wikidat (obce.json z hledání obcí).

Desky nalezené hledáním obcí mají qid. Desky z NKOD se napárují podle poskytovatele
(„Obec Semtěš“), podle holého názvu obce, nebo podle domény webu obce.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_PREFIX = re.compile(r"^(obec|město|městys|statutární\s+město)\s+", re.I)


def _domain(url: str | None) -> str:
    host = (urlsplit(url or "").hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _key(s: str) -> str:
    return " ".join((s or "").lower().split())


def attach(boards: list[dict], obce_path: Path | None) -> int:
    """Doplní deskám lat/lon (zaokrouhleno na ~100 m). Vrátí počet desek s polohou."""
    if not obce_path or not obce_path.exists():
        return 0
    obce = [o for o in json.loads(obce_path.read_text("utf-8")) if o.get("lat") is not None]
    by_qid = {o["qid"]: o for o in obce}
    by_pub, by_name, by_dom = defaultdict(list), defaultdict(list), defaultdict(list)
    for o in obce:
        by_pub[_key(o["publisher"])].append(o)
        by_name[_key(o["name"])].append(o)
        if o.get("web"):
            by_dom[_domain(o["web"])].append(o)
    placed = 0
    for b in boards:
        o = by_qid.get(b.get("qid") or "")
        if o is None:
            dom = _domain(b.get("access_url"))
            cands = by_dom.get(dom, []) if dom and not dom.endswith(("gov.cz", "imunis.cz", "alis.cz")) else []
            if len(cands) != 1:
                cands = by_pub.get(_key(b.get("publisher", "")), [])
            if len(cands) != 1:
                cands = by_name.get(_key(_PREFIX.sub("", b.get("publisher", ""))), [])
            o = cands[0] if len(cands) == 1 else None
        if o:
            b["lat"], b["lon"] = round(o["lat"], 3), round(o["lon"], 3)
            if not b.get("area") and o.get("area"):
                b["area"] = o["area"]
            placed += 1
    log.info("Poloha doplněna %d z %d desek", placed, len(boards))
    return placed
