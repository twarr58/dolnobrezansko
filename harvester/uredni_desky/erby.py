"""Znaky (erby) obcí pro web.

Znak uložený v repozitáři (`erb` v data/region.json, soubor ve web/erby) má přednost. Ostatním obcím se stáhne
soubor `erb_commons` z Wikimedia Commons, nebo se znak najde ve Wikidatech (P94 – vyobrazení znaku, podle `qid` nebo `erb_qid`) a stáhne se z Wikimedia Commons
jako náhled 120 px. Stažené znaky se drží ve stavu (state/erby.json), takže Commons se ptáme jednou za týden.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote

import httpx


log = logging.getLogger(__name__)

SPARQL = "https://query.wikidata.org/sparql"
FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width=120"
REFRESH_DAYS = 7
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/svg+xml": "svg", "image/webp": "webp"}


def wikidata_qid(m: dict) -> str | None:
    return m.get("erb_qid") or m.get("qid")


def _commons_name(url: str) -> str:
    """http://commons.wikimedia.org/wiki/Special:FilePath/Psary%20CZ%20CoA.svg → Psary CZ CoA.svg"""
    return unquote(url.rsplit("/", 1)[-1])


async def coa_files(client: httpx.AsyncClient, qids: list[str]) -> dict[str, str]:
    """{qid: název souboru znaku na Commons} pro obce, které ho ve Wikidatech mají."""
    if not qids:
        return {}
    query = "SELECT ?item ?coa WHERE { VALUES ?item { %s } ?item wdt:P94 ?coa . }" % " ".join(f"wd:{q}" for q in qids)
    r = await client.get(SPARQL, params={"query": query}, headers={"Accept": "application/sparql-results+json"})
    r.raise_for_status()
    out = {}
    for b in r.json()["results"]["bindings"]:
        out.setdefault(b["item"]["value"].rsplit("/", 1)[-1], _commons_name(b["coa"]["value"]))
    return out


async def collect(client: httpx.AsyncClient, members: list[dict], site_dir: Path, state_dir: Path,
                  now: datetime) -> dict[str, str]:
    """Znaky obcí: {obec: cesta k obrázku relativně k webu}. Stažené znaky zapíše do site_dir/erby."""
    path = state_dir / "erby.json"
    try:
        cache = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        cache = {}
    out = {m["name"]: m["erb"] for m in members if m.get("erb")}
    todo = [m for m in members if not m.get("erb") and m.get("slug") and (m.get("erb_commons") or wikidata_qid(m))]
    # znovu se ptáme jednou za týden – i u obcí, které znak ve Wikidatech zatím nemají
    stale = [m for m in todo if cache.get(m["slug"], {}).get("checked", "") < (now - timedelta(days=REFRESH_DAYS)).isoformat()]
    files: dict[str, str] = {}
    wd_ok = False
    lookup = sorted({wikidata_qid(m) for m in stale if not m.get("erb_commons")})
    if lookup:
        try:
            files = await coa_files(client, lookup)
            wd_ok = True
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("Znaky obcí: Wikidata nejdou (%r), zůstávají uložené", exc)
    for m in stale:
        name = m.get("erb_commons") or files.get(wikidata_qid(m))
        prev = cache.get(m["slug"], {})
        if not name:
            if wd_ok:  # znak ve Wikidatech není – zeptáme se zase za týden; při výpadku Wikidat hned příště
                cache[m["slug"]] = {**prev, "checked": now.isoformat(timespec="seconds")}
            continue
        if prev.get("file") == name and prev.get("b64"):
            cache[m["slug"]] = {**prev, "checked": now.isoformat(timespec="seconds")}
            continue
        try:
            r = await client.get(FILEPATH.format(name=quote(name)), follow_redirects=True)
            r.raise_for_status()
            ext = EXT.get(r.headers.get("content-type", "").split(";")[0].strip())
            if not ext or len(r.content) > 300_000:
                raise ValueError(f"nečekaný obsah {r.headers.get('content-type')} ({len(r.content)} B)")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Znak %s (%s): %r", m["name"], name, exc)
            continue
        cache[m["slug"]] = {"file": name, "ext": ext, "b64": base64.b64encode(r.content).decode(),
                            "checked": now.isoformat(timespec="seconds")}
        log.info("Znak %s: %s (%d B)", m["name"], name, len(r.content))
    erby_dir = site_dir / "erby"
    for m in todo:
        c = cache.get(m["slug"])
        if not c or not c.get("b64"):
            continue
        erby_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{m['slug']}.{c['ext']}"
        (erby_dir / fname).write_bytes(base64.b64decode(c["b64"]))
        out[m["name"]] = f"erby/{fname}"
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), "utf-8")
    return out
