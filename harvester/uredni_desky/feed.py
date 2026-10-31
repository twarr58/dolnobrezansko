"""Obecný adaptér pro úřední desky s RSS/Atom kanálem.

Pokrývá např. systém s deskou na /uredni-deska?action=atom (stovky obcí) a službu ALIS
(urednideska.alis.cz/<obec>/rss). Z kanálu se bere název, odkaz, datum zveřejnění a přílohy
(odkazy v popisu položky). Datum sejmutí kanály obvykle neuvádějí.
"""

from __future__ import annotations

import html
import re
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from .imunis import czech_date

_ATOM = "{http://www.w3.org/2005/Atom}"
_HREF = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_UNTIL = re.compile(r"(?:sejmut[íi]|vyvěšeno do|relevantní do)[^0-9]{0,20}(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})", re.I)


def clean_url(url: str) -> str:
    """Odstraní sledovací parametry (utm_*), aby byl identifikátor položky stabilní."""
    p = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))


def _date(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        pass
    if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    return czech_date(raw)


def _text(s: str | None) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", s or "")).split())


def _attachments(description: str, base: str) -> list[dict]:
    docs, seen = [], set()
    for href, label in _HREF.findall(html.unescape(description or "")):
        url = clean_url(urljoin(base, html.unescape(href)))
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        docs.append({"název": {"cs": _text(label) or "Příloha"}, "url": url})
    return docs


def parse_feed(content: bytes, base: str) -> list[dict]:
    """RSS 2.0 nebo Atom → položky ve tvaru OFN Úřední desky."""
    root = ElementTree.fromstring(content.lstrip(b"\xef\xbb\xbf").strip())
    infos = []
    entries = list(root.iter("item")) or list(root.iter(f"{_ATOM}entry"))
    for e in entries:
        title = _text(e.findtext("title") or e.findtext(f"{_ATOM}title"))
        link = (e.findtext("link") or "").strip()
        if not link:
            el = e.find(f"{_ATOM}link")
            link = el.get("href", "") if el is not None else ""
        guid = (e.findtext("guid") or e.findtext(f"{_ATOM}id") or "").strip()
        url = clean_url(urljoin(base, guid if guid.startswith("http") else link)) if (guid or link) else None
        if not title or not url:
            continue
        desc = e.findtext("description") or e.findtext(f"{_ATOM}summary") or e.findtext(f"{_ATOM}content") or ""
        info: dict = {"iri": url, "url": url, "název": {"cs": title}}
        posted = _date(e.findtext("pubDate") or e.findtext(f"{_ATOM}published") or e.findtext(f"{_ATOM}updated"))
        if posted:
            info["vyvěšení"] = {"datum": posted}
        if (m := _UNTIL.search(_text(desc))) and (until := czech_date(m.group(1))):
            info["relevantní_do"] = {"datum": until}
        if cat := _text(e.findtext("category")):
            info["agenda"] = [{"název": {"cs": cat}}]
        if docs := _attachments(desc, base):
            info["dokument"] = docs
        infos.append(info)
    return infos


async def fetch_board(client: httpx.AsyncClient, feed_url: str) -> dict:
    resp = await client.get(feed_url)
    resp.raise_for_status()
    return {"informace": parse_feed(resp.content, str(resp.url))}
