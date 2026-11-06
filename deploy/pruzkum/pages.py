#!/usr/bin/env python3
"""Průzkum stránek obcí: stáhne každou adresu (přímo, při chybě přes HARVEST_FALLBACK_PROXY) a do logu vypíše
čitelný text stránky a odkazy, které vypadají jako kontakty, úřad, zápisy a usnesení. Slouží k ladění zdrojů
u webů, které z cloudu ani z runneru přímo nejdou.

  python deploy/pruzkum/pages.py URL [URL …]      (nebo adresy po řádcích v souboru: --file urls.txt)
"""
import os
import re
import sys

import httpx
import lxml.html

UA = "Mozilla/5.0 (X11; Linux x86_64) uredni-desky-pruzkum (+https://github.com/twarr58/dolnobrezansko)"
LINK_RE = re.compile(r"kontakt|úřad|urad|povinn|usnesen|zápis|zapis|rad[ay]\b|rada|zastupitel|zasedání|zasedani|samospr", re.I)
MAX_TEXT = int(os.environ.get("MAX_TEXT", "6000"))


def get(url: str) -> tuple[httpx.Response, str]:
    common = dict(headers={"User-Agent": UA, "Accept-Language": "cs"}, follow_redirects=True, timeout=30)
    try:
        r = httpx.get(url, **common)
        if r.status_code < 400:
            return r, "přímo"
        err = f"HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        err = type(exc).__name__
    proxy = os.environ.get("HARVEST_FALLBACK_PROXY")
    if not proxy:
        raise RuntimeError(f"{err}, proxy není nastavená")
    with httpx.Client(proxy=proxy, **common) as c:
        return c.get(url), f"přes proxy (přímo: {err})"


def dump(url: str) -> None:
    print(f"\n========== {url}")
    try:
        r, how = get(url)
    except Exception as exc:  # noqa: BLE001
        print(f"!! nejde: {exc}")
        return
    print(f"-- {r.status_code} {how} {r.headers.get('content-type', '')} {len(r.content)} B, konečná adresa {r.url}")
    if "html" not in r.headers.get("content-type", ""):
        return
    root = lxml.html.fromstring(r.content, base_url=str(r.url))
    root.make_links_absolute(str(r.url))
    for bad in root.xpath("//script|//style|//noscript|//svg"):
        bad.drop_tree()
    links, seen = [], set()
    for a in root.iter("a"):
        href, text = a.get("href") or "", " ".join(a.text_content().split())
        if href.startswith("http") and href not in seen and LINK_RE.search(f"{text} {href}"):
            seen.add(href)
            links.append(f"   {text[:70]:<70} {href}")
    print(f"-- odkazy ({len(links)}):")
    print("\n".join(links[:120]))
    main = (root.xpath("//main") or root.xpath("//*[@id='content' or @id='main' or contains(@class,'content')]") or [root])[0]
    text = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", main.text_content()))
    print(f"-- text ({len(text)} znaků, prvních {MAX_TEXT}):")
    print(text[:MAX_TEXT])


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--file"]:
        args = [u.strip() for u in open(args[1], encoding="utf-8") if u.strip() and not u.startswith("#")]
    for u in args:
        dump(u)
