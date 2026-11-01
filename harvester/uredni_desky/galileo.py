"""Adaptér pro weby obcí na redakčním systému Galileo Corporation (igalileo.cz).

Úřední deska je na <web obce>/urad/uredni-deska/. Seznam obsahuje bloky
<div class="item"> s odkazem a.item-href, „Vyvěšeno: …“ a „Datum sejmutí: …“.
Detail položky obsahuje přílohy jako a.file (/e_download.php?…&original=<soubor>).
RSS ani otevřená data systém nenabízí, adaptér proto čte HTML a sestaví data ve tvaru OFN.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx

from . import cache, webpage
from .imunis import czech_date


@dataclass
class ListItem:
    url: str
    title: str
    posted: str | None = None
    until: str | None = None
    category: str | None = None


def _clean_url(url: str) -> str:
    """Bez query (?kshow=… se liší podle kategorie, identifikátor položky musí být stabilní)."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))



def _classes(attrs) -> set[str]:
    return set((dict(attrs).get("class") or "").split())


class _ListParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.items: list[ListItem] = []
        self._in_link = False
        self._date: str | None = None  # "from" / "to"
        self._buf: list[str] = []
        self._category: str | None = None
        self._in_cat_title = False
        self._odate_depth = 0  # šablona lego3: div.official-desk__date

    def handle_starttag(self, tag, attrs):
        cls = _classes(attrs)
        if self._odate_depth and tag == "div":
            self._odate_depth += 1
        if tag == "div" and any(c.startswith("official-desk__date") for c in cls) and not self._odate_depth:
            self._odate_depth, self._buf = 1, []
            return
        if tag == "a" and ("item-href" in cls or "official-desk__body-heading" in cls):
            href = dict(attrs).get("href") or ""
            self.items.append(ListItem(_clean_url(urljoin(self.base, href)), "", category=self._category))
            self._in_link, self._buf = True, []
        elif tag == "span" and "item-date" in cls:
            self._date = "from" if "item-date-from" in cls else "to" if "item-date-to" in cls else None
            self._buf = []
        elif tag in ("h2", "h3", "h4") and ({"card-title", "category-title"} & cls or "card-header" in cls):
            self._in_cat_title, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "div" and self._odate_depth:
            self._odate_depth -= 1
            if not self._odate_depth and self.items:
                text = " ".join(self._buf)
                value = czech_date(text)
                if value:
                    if "sejmut" in text.lower() or "do:" in text.lower():
                        self.items[-1].until = value
                    else:
                        self.items[-1].posted = value
            return
        if tag == "a" and self._in_link:
            self.items[-1].title = " ".join("".join(self._buf).split())
            self._in_link = False
        elif tag == "span" and self._date and self.items:
            value = czech_date("".join(self._buf))
            if self._date == "from":
                self.items[-1].posted = value
            else:
                self.items[-1].until = value
            self._date = None
        elif tag in ("h2", "h3", "h4") and self._in_cat_title:
            self._category = " ".join("".join(self._buf).split()) or None
            self._in_cat_title = False

    def handle_data(self, data):
        if self._in_link or self._date or self._in_cat_title or self._odate_depth:
            self._buf.append(data)


def parse_list(html: str, base: str) -> list[ListItem]:
    p = _ListParser(base)
    p.feed(html)
    seen, out = set(), []
    for it in p.items:
        if it.title and it.url not in seen:
            seen.add(it.url)
            out.append(it)
    return out


@dataclass
class Detail:
    files: list[tuple[str, str]] = field(default_factory=list)  # (název, url)
    posted: str | None = None
    until: str | None = None


class _DetailParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.detail = Detail()
        self._link: str | None = None
        self._date: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        cls = _classes(attrs)
        a = dict(attrs)
        if tag == "a" and ("file" in cls or "e_download.php" in (a.get("href") or "")):
            self._link, self._buf = urljoin(self.base, a.get("href") or ""), []
        elif tag == "span" and ("post-date" in cls or "removal-date" in cls):
            self._date, self._buf = ("from" if "post-date" in cls else "to"), []

    def handle_endtag(self, tag):
        if tag == "a" and self._link:
            name = " ".join("".join(self._buf).split())
            if not name:
                original = parse_qs(urlsplit(self._link).query).get("original", [""])[0]
                name = original
            if self._link not in {u for _, u in self.detail.files}:
                self.detail.files.append((name, self._link))
            self._link = None
        elif tag == "span" and self._date:
            value = czech_date("".join(self._buf))
            if self._date == "from":
                self.detail.posted = value
            else:
                self.detail.until = value
            self._date = None

    def handle_data(self, data):
        if self._link or self._date:
            self._buf.append(data)


def parse_detail(html: str, base: str) -> Detail:
    p = _DetailParser(base)
    p.feed(html)
    return p.detail


def to_informace(item: ListItem, detail: Detail | None) -> dict:
    info: dict = {"iri": item.url, "url": item.url, "název": {"cs": item.title}}
    posted = item.posted or (detail.posted if detail else None)
    until = item.until or (detail.until if detail else None)
    if posted:
        info["vyvěšení"] = {"datum": posted}
    if until:
        info["relevantní_do"] = {"datum": until}
    if item.category:
        info["agenda"] = [{"název": {"cs": item.category}}]
    if detail and detail.files:
        info["dokument"] = [{"název": {"cs": name or item.title}, "url": url} for name, url in detail.files]
    return info


async def fetch_board(client: httpx.AsyncClient, list_url: str, detail_concurrency: int = 2) -> dict:
    # Accept: application/json (výchozí u sběru) vrací Galileo jako 404
    resp = await client.get(list_url, headers=webpage.HTML_HEADERS)
    resp.raise_for_status()
    if "official-desk__item" in resp.text and "/-/aktualni/" in resp.text:
        # šablona lego3: titulní stránka desky ukazuje jen 5 nejnovějších, celý seznam je na -/aktualni/
        resp = await client.get(str(resp.url).split("#")[0].rstrip("/") + "/-/aktualni/", headers=webpage.HTML_HEADERS)
        resp.raise_for_status()
    items = parse_list(resp.text, str(resp.url))
    if not items:
        # jiná šablona Galilea – obecný HTML adaptér (detaily s přílohami se pak stáhnou jako obvykle)
        generic, _ = await webpage.items_from_response(client, resp)
        items = [ListItem(webpage._clean_url(i.url.split("#")[0]), i.title, i.posted, i.until) for i in generic]
        if not items:
            if infos := await webpage.alis_infos(client, resp.text):
                return {"informace": infos}
            raise ValueError("Na stránce úřední desky nebyly nalezeny žádné položky (změnila se šablona?)")
    sem = asyncio.Semaphore(detail_concurrency)

    async def detail(item: ListItem) -> Detail | None:
        if item.url in cache.DETAILS or webpage._is_doc(item.url) or not cache.take_detail_slot():
            return None  # doplní se z mezipaměti níže, případně v příštím běhu
        async with sem:
            try:
                r = await client.get(item.url, headers=webpage.HTML_HEADERS)
                r.raise_for_status()
                return parse_detail(r.text, str(r.url))
            except httpx.HTTPError:
                return None

    details = await asyncio.gather(*(detail(i) for i in items))
    infos = [to_informace(i, d) for i, d in zip(items, details)]
    for info, d in zip(infos, details):
        if d is None:
            cache.apply(info)
    return {"informace": infos}


GALILEO_PATH = re.compile(r"^https?://[^/]+/urad/uredni-deska/?$")
