"""Automatické dohledání úředních desek všech obcí.

1. Seznam obcí ČR a jejich oficiálních webů z Wikidat (SPARQL).
2. Stažení titulní stránky každého webu a rozpoznání redakčního systému / služby desky:
   - iMunis eDeska (odkaz na <sub>.imunis.cz/edeska) → adaptér imunis
   - Galileo Corporation (igalileo.cz, /urad/uredni-deska/) → adaptér galileo
   - Vismo (data-application="Vismo…") → export OFN na /opendata-uredni-deska, pokud existuje
   - ostatní: zaznamená odkaz na desku a generátor webu (statistika pro další adaptéry)
3. Výstup discovered.json: zdroje pro harvester + statistika pokrytí.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from . import USER_AGENT
from .feed import parse_feed
from .normalize import BoardFormatError, decode_board

log = logging.getLogger(__name__)

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
# Q5153359 = obec v Česku (města a městyse jsou její podtřídy).
WIKIDATA_QUERY = """
SELECT ?item ?name ?web (GROUP_CONCAT(DISTINCT ?typeLabel; separator="|") AS ?types) WHERE {
  ?item wdt:P31/wdt:P279* wd:Q5153359 .
  FILTER NOT EXISTS { ?item wdt:P576 [] }
  ?item rdfs:label ?name . FILTER(LANG(?name) = "cs")
  OPTIONAL { ?item wdt:P856 ?web }
  OPTIONAL { ?item wdt:P31 ?type . ?type rdfs:label ?typeLabel . FILTER(LANG(?typeLabel) = "cs") }
}
GROUP BY ?item ?name ?web
"""

_IMUNIS = re.compile(r"https?://([a-z0-9-]+)\.imunis\.cz/edeska", re.I)
_BOARD_LINK = re.compile(r'href="([^"]*(?:uredni[-_]?desk|edesk|e-desk|uredni%20desk)[^"]*)"', re.I)
_GENERATOR = re.compile(r'<meta[^>]+name="generator"[^>]+content="([^"]+)"', re.I)
_VISMO = re.compile(r'data-application="(Vismo[^"]*)"', re.I)
# Galileo: deska bývá na /urad/uredni-deska/, /obecni-urad/uredni-deska/, /urad-obce/uredni-deska-1/ …
_GALILEO_BOARD = re.compile(r'href="((?:https?://[^"/]+)?/(?:[a-z0-9-]+/){0,3}uredni-deska(?:-\d+)?/)"', re.I)


def publisher_name(name: str, types: str) -> str:
    t = types.lower()
    if "statutární město" in t:
        return f"Statutární město {name}"
    if "městys" in t:
        return f"Městys {name}"
    if "město" in t:
        return f"Město {name}"
    return f"Obec {name}"


def registered_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


async def fetch_municipalities(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.post(WIKIDATA_SPARQL, data={"query": WIKIDATA_QUERY},
                             headers={"Accept": "application/sparql-results+json"}, timeout=180)
    resp.raise_for_status()
    by_item: dict[str, dict] = {}
    for b in resp.json()["results"]["bindings"]:
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        m = by_item.setdefault(qid, {"qid": qid, "name": b["name"]["value"], "types": "", "webs": []})
        m["types"] = b.get("types", {}).get("value", m["types"])
        if (w := b.get("web", {}).get("value")) and w not in m["webs"]:
            m["webs"].append(w)
    return list(by_item.values())


def classify_html(html: str, base: str) -> dict:
    """Rozpozná systém desky z HTML titulní stránky."""
    if m := _IMUNIS.search(html):
        return {"fmt": "imunis", "subdomain": m.group(1).lower()}
    low = html.lower()
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base))
    if "igalileo.cz" in low:
        paths = [urlsplit(urljoin(base, m)).path for m in _GALILEO_BOARD.findall(html)]
        if paths:
            # nejčastější cesta (stránka desky je v menu i v náhledu na titulní stránce)
            best = max(set(paths), key=lambda p: (paths.count(p), -len(p)))
            return {"fmt": "galileo", "url": origin + best}
    info: dict = {"fmt": None}
    if m := _VISMO.search(html):
        info["cms"] = m.group(1)
    elif m := _GENERATOR.search(html):
        info["cms"] = m.group(1)[:60]
    elif "igalileo.cz" in low:
        info["cms"] = "Galileo"
    if m := _BOARD_LINK.search(html):
        info["board_link"] = urljoin(base, m.group(1))
    return info


_FEED_LINK = re.compile(r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*href="([^"]+)"', re.I)
_FEED_A = re.compile(r'href="([^"]*(?:rss|atom|feed)[^"]*)"', re.I)


def feed_candidates(html: str, board_url: str) -> list[str]:
    """Možné adresy RSS/Atom kanálu úřední desky (z HTML stránky desky)."""
    host = urlsplit(board_url).hostname or ""
    if host == "urednideska.alis.cz":  # ALIS: urednideska.alis.cz/<obec>/rss
        seg = urlsplit(board_url).path.strip("/").split("/")[0]
        return [f"https://urednideska.alis.cz/{seg}/rss"] if seg else []
    found = [urljoin(board_url, h) for h in _FEED_LINK.findall(html)]
    found += [urljoin(board_url, h) for h in _FEED_A.findall(html)
              if not h.lower().endswith((".css", ".js")) and "co-je-rss" not in h.lower()]
    found.append(board_url.split("?")[0].rstrip("/") + "?action=atom")
    return list(dict.fromkeys(u.replace("&amp;", "&") for u in found if u.startswith("http")))[:5]


async def _probe_board(client: httpx.AsyncClient, info: dict) -> None:
    """U nerozpoznaného webu prozkoumá stránku desky: RSS/Atom kanál nebo deska Vismo."""
    board = info.get("board_link")
    if not board:
        return
    try:
        r = await client.get(board, timeout=20)
        r.raise_for_status()
    except httpx.HTTPError:
        return
    if info.get("cms", "").startswith("Vismo") and "site_board" in r.text:
        info.update(fmt="vismo", url=str(r.url))
        return
    for cand in feed_candidates(r.text, str(r.url)):
        try:
            fr = await client.get(cand, timeout=20)
            if fr.status_code == 200 and parse_feed(fr.content, str(fr.url)):
                info.update(fmt="rss", url=cand)
                return
        except (httpx.HTTPError, ElementTree.ParseError):
            continue


async def _probe_vismo_ofn(client: httpx.AsyncClient, base: str) -> str | None:
    url = "{0.scheme}://{0.netloc}/opendata-uredni-deska".format(urlsplit(base))
    try:
        r = await client.get(url, timeout=20)
        if r.status_code == 200:
            decode_board(r.content)
            return url
    except (httpx.HTTPError, BoardFormatError):
        pass
    return None


async def classify_municipality(client: httpx.AsyncClient, m: dict, sem: asyncio.Semaphore) -> dict:
    result = {"qid": m["qid"], "name": m["name"], "publisher": publisher_name(m["name"], m["types"]),
              "web": m["webs"][0] if m["webs"] else None}
    if not result["web"]:
        result["status"] = "bez webu ve Wikidatech"
        return result
    async with sem:
        try:
            r = await client.get(result["web"], timeout=20)
            r.raise_for_status()
            info = classify_html(r.text, str(r.url))
            if info.get("cms", "").startswith("Vismo") and not info.get("fmt"):
                if ofn := await _probe_vismo_ofn(client, str(r.url)):
                    info.update(fmt="ofn", url=ofn)
            if not info.get("fmt"):
                await _probe_board(client, info)
            result.update(info)
            result["status"] = "ok"
        except httpx.TimeoutException:
            result["status"] = "timeout"
        except httpx.HTTPError as exc:
            result["status"] = f"chyba: {type(exc).__name__}"
    return result


async def run(out: Path, nkod_boards: Path | None, concurrency: int = 32, limit: int | None = None) -> dict:
    known_domains: set[str] = set()
    if nkod_boards and nkod_boards.exists():
        for b in json.loads(nkod_boards.read_text("utf-8")):
            if b.get("fmt", "ofn") == "ofn" and b.get("ok"):
                known_domains.add(registered_domain(b.get("access_url", "")))
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                                 limits=httpx.Limits(max_connections=concurrency)) as client:
        municipalities = await fetch_municipalities(client)
        log.info("Wikidata: %d obcí, s webem %d", len(municipalities), sum(1 for m in municipalities if m["webs"]))
        if limit:
            municipalities = municipalities[:limit]
        sem = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(*(classify_municipality(client, m, sem) for m in municipalities))

    sources, unknown = [], []
    for r in results:
        in_nkod = bool(r.get("web")) and registered_domain(r["web"]) in known_domains
        r["in_nkod"] = in_nkod
        if r.get("fmt") and not in_nkod:
            src = {k: r[k] for k in ("fmt", "publisher", "qid") if k in r}
            src.update({k: r[k] for k in ("subdomain", "url") if k in r})
            sources.append(src)
        elif not r.get("fmt") and not in_nkod and r.get("status") == "ok":
            unknown.append({k: r.get(k) for k in ("name", "web", "cms", "board_link")})

    stats = {
        "obci": len(results),
        "s_webem": sum(1 for r in results if r.get("web")),
        "v_nkod_podle_domeny": sum(1 for r in results if r["in_nkod"]),
        "adapter": dict(collections.Counter(s["fmt"] for s in sources)),
        "stav_webu": dict(collections.Counter(r.get("status") for r in results)),
        "nepodporovane_cms": collections.Counter((u.get("cms") or "neznámý") for u in unknown).most_common(40),
        "nepodporovane_odkazy_na_desku": collections.Counter(
            (urlsplit(u["board_link"]).hostname or "?") if u.get("board_link") else "bez odkazu"
            for u in unknown).most_common(40),
    }
    data = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats": stats, "sources": sources, "unknown": unknown}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return data
