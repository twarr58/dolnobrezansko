"""Ladění adaptérů na skutečných stránkách: co z nich vyčte Galileo / obecný HTML adaptér / RSS.

python -m uredni_desky probe URL [URL …]
U stránek, kde adaptér nic nenajde, vypíše „kostru“: řádky textu s datem a cestu prvků k nim.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import lxml.html

from . import USER_AGENT, galileo, webpage
from .discover import classify_html, feed_candidates


def _path(el) -> str:
    parts = []
    while el is not None and el.tag not in ("body", "html") and len(parts) < 6:
        cls = ".".join((el.get("class") or "").split()[:2])
        parts.append(el.tag + (f".{cls}" if cls else ""))
        el = el.getparent()
    return " < ".join(parts)


def skeleton(html: bytes, limit: int = 12) -> list[str]:
    try:
        root = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001
        return ["(nečitelné HTML)"]
    for bad in root.xpath("//script|//style"):
        bad.drop_tree()
    out = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        own = " ".join((el.text or "").split())
        if own and webpage._DATE.search(own):
            out.append(f"{_path(el)} :: {own[:140]}")
            if len(out) >= limit:
                break
    return out


async def probe(client: httpx.AsyncClient, url: str, out: list[str]) -> None:
    out.append(f"\n=== {url}")
    try:
        r = await client.get(url)
    except httpx.HTTPError as exc:
        out.append(f"  chyba: {type(exc).__name__} {exc}")
        return
    out.append(f"  HTTP {r.status_code} → {r.url} ({len(r.content)} B, {r.headers.get('content-type')})")
    if r.status_code >= 400:
        return
    base = str(r.url)
    info = classify_html(r.text, base)
    out.append(f"  classify: {info}")
    g = galileo.parse_list(r.text, base)
    out.append(f"  galileo: {len(g)} položek" + (f", 1.: {g[0]}" if g else ""))
    items, src = await webpage.fetch_items(client, base)
    out.append(f"  webpage: {len(items)} položek (z {src})")
    for it in items[:4]:
        out.append(f"    - {it.posted}…{it.until} | {it.title[:90]} | {it.url[:100]} | docs={len(it.docs)}")
    if not items:
        frames = webpage.iframe_sources(r.text, base)
        if frames:
            out.append(f"  iframe: {frames}")
        out.append(f"  feedy: {feed_candidates(r.text, base)}")
        links = sorted(set(re.findall(r'href="([^"]*(?:desk|uredni|archiv|aktualni)[^"]*)"', r.text, re.I)))[:15]
        out.append(f"  odkazy: {links}")
        out.append("  kostra:")
        for line in skeleton(r.content):
            out.append(f"    {line}")


async def run(urls: list[str]) -> None:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=25) as client:
        sem = asyncio.Semaphore(8)

        async def one(u: str) -> list[str]:
            out: list[str] = []
            async with sem:
                try:
                    await probe(client, u, out)
                except Exception as exc:  # noqa: BLE001
                    out.append(f"  výjimka: {type(exc).__name__}: {exc}")
            return out

        for lines in await asyncio.gather(*(one(u) for u in urls)):
            print("\n".join(lines), flush=True)
