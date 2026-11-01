"""Adaptér pro HTML úřední desku na webech Vismo (WEBHOUSE).

Seznam na <web>/uredni-deska obsahuje položky třídy site_board…: odkaz s názvem,
„Rubrika“ (site_board__folder__link), „Vyvěšeno od/do“ v <time datetime="YYYY-MM-DD">.
Přílohy jsou až na stránce detailu – ty adaptér zatím nestahuje.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


class _Parser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.items: list[dict] = []
        self._link: str | None = None
        self._buf: list[str] = []
        self._folder = False
        self._date: str | None = None  # "from" / "to"
        self._depth_board = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if "site_board" in cls:
            self._depth_board += 1
        if tag == "a" and self._depth_board and a.get("href"):
            if "site_board__folder__link" in cls:
                self._folder = True
            elif "Account/Login" not in a["href"]:
                self._link, self._buf = a["href"], []
        elif tag == "span" and "site_board__date" in cls:
            self._date = "from" if "-from" in cls else "to" if "-to" in cls else None
        elif tag == "time" and self._date and self.items:
            if dt := (a.get("datetime") or "")[:10]:
                self.items[-1]["posted" if self._date == "from" else "until"] = dt

    def handle_endtag(self, tag):
        if tag == "a":
            if self._link:
                title = " ".join("".join(self._buf).split())
                url = urljoin(self.base, self._link)
                p = urlsplit(url)
                if len(title) > 3 and p.netloc == urlsplit(self.base).netloc:
                    url = urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
                    self.items.append({"url": url, "title": title})
                self._link = None
            elif self._folder:
                self._folder = False
        elif tag == "span":
            self._date = None

    def handle_data(self, data):
        if self._link:
            self._buf.append(data)
        elif self._folder and self.items and "category" not in self.items[-1]:
            self.items[-1]["category"] = data.strip()


def parse_board(html: str, base: str) -> list[dict]:
    p = _Parser(base)
    p.feed(html)
    infos, seen = [], set()
    for it in p.items:
        if it["url"] in seen or "posted" not in it:
            continue  # odkazy bez data vyvěšení nejsou položky desky (navigace, stránkování)
        seen.add(it["url"])
        info = {"iri": it["url"], "url": it["url"], "název": {"cs": it["title"]},
                "vyvěšení": {"datum": it["posted"]}}
        if it.get("until"):
            info["relevantní_do"] = {"datum": it["until"]}
        if it.get("category"):
            info["agenda"] = [{"název": {"cs": it["category"]}}]
        infos.append(info)
    return infos


async def fetch_board(client: httpx.AsyncClient, url: str) -> dict:
    resp = await client.get(url, headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"})
    resp.raise_for_status()
    infos = parse_board(resp.text, str(resp.url))
    if not infos and "site_board" not in resp.text:
        raise ValueError("Stránka neobsahuje úřední desku Vismo")
    return {"informace": infos}
