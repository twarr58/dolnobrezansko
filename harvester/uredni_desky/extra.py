"""Zdroje desek mimo NKOD (seznam v data/extra_sources.json)."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

from .nkod import BoardSource, board_id

_SUBDOMAIN = re.compile(r"^[a-z0-9-]{1,63}$")


def load_extra_sources(discovered: Path | None = None) -> list[BoardSource]:
    """Ručně vedené zdroje (data/extra_sources.json) + automaticky nalezené (discovered.json)."""
    raw = json.loads(resources.files(__package__).joinpath("data/extra_sources.json").read_text("utf-8"))
    if discovered and discovered.exists():
        found = json.loads(discovered.read_text("utf-8")).get("sources", [])
        raw["imunis"] = raw.get("imunis", []) + [s for s in found if s.get("fmt") == "imunis"]
        raw["galileo"] = raw.get("galileo", []) + [s for s in found if s.get("fmt") == "galileo"]
        raw["ofn"] = [s for s in found if s.get("fmt") == "ofn"]
        raw["rss"] = [s for s in found if s.get("fmt") == "rss"]
        raw["vismo"] = [s for s in found if s.get("fmt") == "vismo"]
        raw["html"] = [s for s in found if s.get("fmt") == "html"]
    return sources_from_entries(raw)


def sources_from_entries(raw: dict) -> list[BoardSource]:
    """Zdroje ze záznamů seskupených podle formátu: {"imunis": [...], "galileo": [...], "rss": [...], …}."""
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
            area=entry.get("area", ""), qid=entry.get("qid", ""),
        ))
    for entry in raw.get("galileo", []):
        url = entry["url"].strip()
        if not url.startswith(("https://", "http://")) or not re.search(r"/uredni-deska(-\d+)?/?$", url):
            raise ValueError(f"Neplatná adresa desky Galileo: {url!r}")
        url = url.rstrip("/") + "/"
        sources.append(BoardSource(
            id=board_id(f"galileo:{url}"),
            dataset_iri=url,
            title="Úřední deska (web obce)",
            publisher=entry["publisher"],
            access_url=url,
            fmt="galileo",
            area=entry.get("area", ""), qid=entry.get("qid", ""),
        ))
    for entry in raw.get("ofn", []):  # export OFN na webu obce, který není v NKOD (např. Vismo)
        url = entry["url"].strip()
        if url.startswith("https://") or url.startswith("http://"):
            sources.append(BoardSource(id=board_id(f"web:{url}"), dataset_iri=url,
                                       title="Úřední deska (otevřená data na webu obce)",
                                       publisher=entry["publisher"], access_url=url,
                                       area=entry.get("area", ""), qid=entry.get("qid", "")))
    titles = {"rss": "Úřední deska (RSS kanál webu obce)", "vismo": "Úřední deska (web obce)",
              "html": "Úřední deska (web obce)", "edesky": "Úřední deska (přes edesky.cz)"}
    for fmt, title in titles.items():
        for entry in raw.get(fmt, []):
            url = entry["url"].strip()
            if fmt == "html":  # adresa z iframu GINIS může nést identifikátor relace /(S(…))/
                url = re.sub(r"/\([A-Z]\([a-z0-9]{10,}\)\)", "", url, flags=re.I)
            if url.startswith(("https://", "http://")):
                sources.append(BoardSource(id=board_id(f"{fmt}:{url}"), dataset_iri=url, title=title,
                                           publisher=entry["publisher"], access_url=url, fmt=fmt,
                                           area=entry.get("area", ""), qid=entry.get("qid", "")))
    unique: dict[str, BoardSource] = {}
    for src in sources:
        unique.setdefault(src.id, src)
    return list(unique.values())
