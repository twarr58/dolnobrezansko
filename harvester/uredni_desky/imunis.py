"""Adaptér pro iMunis eDeska (<obec>.imunis.cz/edeska).

Obce na této službě nepublikují úřední desku jako otevřená data, ale eDeska má RSS kanál
s aktuálně vyvěšenými položkami a stránku detailu každé položky (data vyvěšení, značka,
přílohy). Adaptér z obojího sestaví data ve tvaru OFN Úřední desky, takže zbytek
harvesteru (normalizace, archiv, web) funguje beze změn.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx

_DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")


@dataclass
class RssItem:
    url: str
    title: str
    category: str | None
    published: str | None  # YYYY-MM-DD


def parse_rss(content: bytes) -> list[RssItem]:
    root = ElementTree.fromstring(content.lstrip(b"\xef\xbb\xbf").strip())
    items = []
    for it in root.iter("item"):
        url = (it.findtext("link") or it.findtext("guid") or "").strip()
        title = " ".join((it.findtext("title") or "").split())
        if not url or not title:
            continue
        published = None
        if raw := it.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(raw.strip()).date().isoformat()
            except (TypeError, ValueError):
                pass
        category = " ".join((it.findtext("category") or "").split()) or None
        items.append(RssItem(url, title, category, published))
    return items


def czech_date(text: str | None) -> str | None:
    """„9. 11. 2026“ / „09.11.2026 12:00“ → 2026-11-09."""
    if not text or not (m := _DATE.search(text)):
        return None
    d, mth, y = (int(x) for x in m.groups())
    if not (1 <= d <= 31 and 1 <= mth <= 12 and 1990 <= y <= 2100):
        return None
    return f"{y:04d}-{mth:02d}-{d:02d}"


@dataclass
class Detail:
    fields: dict[str, str] = field(default_factory=dict)
    files: list[tuple[str, str]] = field(default_factory=list)  # (název, url)


class _DetailParser(HTMLParser):
    """Vytáhne z detailu dvojice <th>popisek</th><td>hodnota</td> a odkazy na soubory."""

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.detail = Detail()
        self._cell: str | None = None  # "th" / "td"
        self._buf: list[str] = []
        self._label: str | None = None
        self._link: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("th", "td"):
            self._cell, self._buf = tag, []
        elif tag == "a" and "/edeska/file" in (a.get("href") or ""):
            self._link, self._link_text = urljoin(self.base, a["href"]), []

    def handle_endtag(self, tag):
        if tag == "a" and self._link:
            name = " ".join("".join(self._link_text).split())
            self.detail.files.append((name, self._link))
            self._link = None
        elif tag in ("th", "td") and self._cell == tag:
            text = " ".join("".join(self._buf).split())
            if tag == "th":
                self._label = text
            elif self._label:
                # Stejné popisky se opakují ve více tabulkách; platí první výskyt.
                self.detail.fields.setdefault(self._label, text)
                self._label = None
            self._cell = None

    def handle_data(self, data):
        if self._cell:
            self._buf.append(data)
        if self._link:
            self._link_text.append(data)


def parse_detail(html: str, base: str) -> Detail:
    p = _DetailParser(base)
    p.feed(html)
    return p.detail


def _first(fields: dict[str, str], *labels: str) -> str | None:
    lowered = {k.lower(): v for k, v in fields.items()}
    for label in labels:
        if (v := lowered.get(label.lower())) and v.strip("- "):
            return v
    return None


def to_informace(item: RssItem, detail: Detail | None) -> dict:
    """Jedna položka ve tvaru OFN Úřední desky."""
    info: dict = {"iri": item.url, "url": item.url, "název": {"cs": item.title}}
    fields = detail.fields if detail else {}
    posted = czech_date(_first(fields, "Vyvěšeno od", "Datum od")) or item.published
    until = czech_date(_first(fields, "Vyvěšeno do", "Má být vyvěšeno do", "Datum do"))
    if posted:
        info["vyvěšení"] = {"datum": posted}
    if until:
        info["relevantní_do"] = {"datum": until}
    if znacka := _first(fields, "Značka", "Číslo jednací", "Spisová značka"):
        info["číslo_jednací"] = znacka
    if agenda := _first(fields, "Typ") or item.category:
        info["agenda"] = [{"název": {"cs": agenda}}]
    files = detail.files if detail else []
    if files:
        # Text odkazu bývá jen značka („-/1“), proto se dokumenty pojmenují podle položky.
        info["dokument"] = [
            {"název": {"cs": item.title if len(files) == 1 else f"{item.title} – příloha {n}"}, "url": url}
            for n, (_, url) in enumerate(files, 1)
        ]
    return info


async def fetch_board(client: httpx.AsyncClient, rss_url: str, detail_concurrency: int = 4) -> dict:
    """Stáhne RSS a detaily položek a vrátí data desky ve tvaru OFN."""
    resp = await client.get(rss_url)
    resp.raise_for_status()
    items = parse_rss(resp.content)
    sem = asyncio.Semaphore(detail_concurrency)

    async def detail(item: RssItem) -> Detail | None:
        async with sem:
            try:
                r = await client.get(item.url)
                r.raise_for_status()
                return parse_detail(r.text, item.url)
            except httpx.HTTPError:
                return None  # bez detailu zůstanou údaje z RSS

    details = await asyncio.gather(*(detail(i) for i in items))
    return {"informace": [to_informace(i, d) for i, d in zip(items, details)]}
