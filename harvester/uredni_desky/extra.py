"""Zdroje desek mimo NKOD (seznam v data/extra_sources.json)."""

from __future__ import annotations

import json
import re
from importlib import resources

from .nkod import BoardSource, board_id

_SUBDOMAIN = re.compile(r"^[a-z0-9-]{1,63}$")


def load_extra_sources() -> list[BoardSource]:
    raw = json.loads(resources.files(__package__).joinpath("data/extra_sources.json").read_text("utf-8"))
    sources = []
    for entry in raw.get("imunis", []):
        sub = entry["subdomain"].strip().lower()
        if not _SUBDOMAIN.match(sub):
            raise ValueError(f"Neplatná subdoména iMunis: {sub!r}")
        base = f"https://{sub}.imunis.cz/edeska"
        sources.append(BoardSource(
            id=board_id(f"imunis:{sub}"),
            dataset_iri=base,
            title="Úřední deska (iMunis eDeska)",
            publisher=entry["publisher"],
            access_url=f"{base}/rss.asp",
            fmt="imunis",
        ))
    return sources
