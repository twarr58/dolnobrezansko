"""Znaky (erby) obcí pro web.

Znak uložený v repozitáři (`erb` v data/region.json, soubor ve web/erby) má přednost. Ostatní znaky stahuje
samostatný workflow (`python -m uredni_desky erby --out assets`) z Wikimedia Commons – soubor `erb_commons`,
nebo znak dohledaný ve Wikidatech (P94 podle `qid` / `erb_qid`) – a ukládá je do větve `assets`:

  assets/erby/<slug>.<přípona>   obrázek (náhled 250 px)
  assets/erby/index.json         {obec: {"path": "erby/<soubor>", "file": název na Commons, "source": adresa}}

Commons z některých sítí odmítá (429 Too many requests); pak se použije záložní proxy (HARVEST_FALLBACK_PROXY).
Hodinový sběr znaky nestahuje, jen je převezme z větve assets (load).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from . import USER_AGENT
from .fetch import needs_proxy

log = logging.getLogger(__name__)

SPARQL = "https://query.wikidata.org/sparql"
FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width=250"
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/svg+xml": "svg", "image/webp": "webp"}
MAX_BYTES = 400_000
INDEX = "index.json"


def wikidata_qid(m: dict) -> str | None:
    return m.get("erb_qid") or m.get("qid")


def _commons_name(url: str) -> str:
    """http://commons.wikimedia.org/wiki/Special:FilePath/Psary%20CZ%20CoA.svg → Psary CZ CoA.svg"""
    return unquote(url.rsplit("/", 1)[-1])


def _retry_via_proxy(exc: Exception) -> bool:
    """Commons odmítá podle IP (429), ostatní jako u desek (timeout, 401/403/451)."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return True
    return needs_proxy(exc)


async def _get(client: httpx.AsyncClient, fallback: httpx.AsyncClient | None, url: str, **kw) -> httpx.Response:
    try:
        r = await client.get(url, **kw)
        r.raise_for_status()
        return r
    except httpx.HTTPError as exc:
        if fallback is None or not _retry_via_proxy(exc):
            raise
        log.info("%s přímo nejde (%r), zkouším záložní proxy", url.split("/")[2], exc)
        r = await fallback.get(url, **kw)
        r.raise_for_status()
        return r


async def coa_files(client: httpx.AsyncClient, qids: list[str], fallback: httpx.AsyncClient | None = None) -> dict[str, str]:
    """{qid: název souboru znaku na Commons} pro obce, které ho ve Wikidatech mají."""
    if not qids:
        return {}
    query = "SELECT ?item ?coa WHERE { VALUES ?item { %s } ?item wdt:P94 ?coa . }" % " ".join(f"wd:{q}" for q in qids)
    r = await _get(client, fallback, SPARQL, params={"query": query}, headers={"Accept": "application/sparql-results+json"})
    out = {}
    for b in r.json()["results"]["bindings"]:
        out.setdefault(b["item"]["value"].rsplit("/", 1)[-1], _commons_name(b["coa"]["value"]))
    return out


def _read_index(erby_dir: Path) -> dict:
    try:
        return json.loads((erby_dir / INDEX).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


async def download(client: httpx.AsyncClient, members: list[dict], out_dir: Path,
                   fallback: httpx.AsyncClient | None = None, force: bool = False) -> dict:
    """Stáhne chybějící znaky do out_dir/erby a zapíše index.json. Vrátí index."""
    erby_dir = out_dir / "erby"
    index = _read_index(erby_dir)
    todo = [m for m in members if not m.get("erb") and m.get("slug") and (m.get("erb_commons") or wikidata_qid(m))]
    lookup = sorted({wikidata_qid(m) for m in todo if not m.get("erb_commons")})
    files: dict[str, str] = {}
    if lookup:
        try:
            files = await coa_files(client, lookup, fallback)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("Znaky obcí: Wikidata nejdou (%r)", exc)
    for m in todo:
        name = m.get("erb_commons") or files.get(wikidata_qid(m))
        prev = index.get(m["name"], {})
        if not name:
            log.info("Znak %s: ve Wikidatech není", m["name"])
            continue
        if not force and prev.get("file") == name and (out_dir / prev.get("path", "-")).is_file():
            continue
        url = FILEPATH.format(name=quote(name))
        try:
            r = await _get(client, fallback, url, follow_redirects=True)
            ext = EXT.get(r.headers.get("content-type", "").split(";")[0].strip())
            if not ext or not r.content or len(r.content) > MAX_BYTES:
                raise ValueError(f"nečekaný obsah {r.headers.get('content-type')} ({len(r.content)} B)")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Znak %s (%s): %r", m["name"], name, exc)
            continue
        erby_dir.mkdir(parents=True, exist_ok=True)
        for old in erby_dir.glob(f"{m['slug']}.*"):
            old.unlink()
        (erby_dir / f"{m['slug']}.{ext}").write_bytes(r.content)
        index[m["name"]] = {"path": f"erby/{m['slug']}.{ext}", "file": name,
                            "source": "https://commons.wikimedia.org/wiki/File:" + quote(name.replace(" ", "_"))}
        log.info("Znak %s: %s (%d B)", m["name"], name, len(r.content))
    if index:
        erby_dir.mkdir(parents=True, exist_ok=True)
        (erby_dir / INDEX).write_text(json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    return index


def load(members: list[dict], assets_dir: Path | None) -> dict[str, str]:
    """Znaky pro web: {obec: cesta relativně k webu} – z repozitáře, jinak z větve assets (soubor musí existovat)."""
    out = {m["name"]: m["erb"] for m in members if m.get("erb")}
    if assets_dir:
        for name, e in _read_index(assets_dir / "erby").items():
            path = e.get("path", "")
            if name not in out and path.startswith("erby/") and (assets_dir / path).is_file():
                out[name] = path
    return out


async def run(out_dir: Path, force: bool = False) -> dict:
    from .region import load_region

    members = load_region()["members"]
    proxy = os.environ.get("HARVEST_FALLBACK_PROXY") or None
    common = dict(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=httpx.Timeout(30, connect=20))
    async with httpx.AsyncClient(**common) as client:
        fallback = httpx.AsyncClient(**common, proxy=proxy) if proxy else None
        try:
            index = await download(client, members, out_dir, fallback, force)
        finally:
            if fallback:
                await fallback.aclose()
    missing = [m["name"] for m in members if not m.get("erb") and m["name"] not in index]
    log.info("Znaky: %d v repozitáři, %d ve větvi assets, bez znaku: %s", sum(bool(m.get("erb")) for m in members),
             len(index), ", ".join(missing) or "–")
    return index
