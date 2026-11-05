"""Adaptér pro API edesky.cz – úřední desky obcí, jejichž weby z runneru GitHubu nejdou.

edesky.cz desky obcí sám pravidelně stahuje (z Česka) a přílohy i jejich rozpoznaný text hostuje
u sebe, takže odpadá blokování zahraničních IP. API: https://github.com/edesky/edesky_api
(XML; klíč z profilu na edesky.cz v parametru api_key – v proměnné EDESKY_API_KEY).

Omezení oproti přímému čtení desky: API neuvádí „vyvěšeno do“ a místo data vyvěšení má datum,
kdy edesky.cz dokument načetl (obvykle týž nebo další den). Lhůty se doplní z textu příloh.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx

API_URL = "https://edesky.cz/api/v1/documents"
WINDOW_DAYS = 60  # dokumenty načtené za posledních N dní; starší se v archivu označí jako sejmuté
MAX_PAGES = 5  # stránka = 200 dokumentů
_DASHBOARD = re.compile(r"edesky\.cz/desky/(\d+)")

# Rozpoznané texty příloh z API (URL přílohy → text) – použijí se, když soubor nejde stáhnout.
TEXTS: dict[str, str] = {}


class _RedactApiKey(logging.Filter):
    """API klíč je v adrese dotazu – v logu (veřejné logy Actions) se nahradí hvězdičkami."""

    _KEY = re.compile(r"(api_key=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(self._KEY.sub(r"\1***", str(a)) if isinstance(a, (str, httpx.URL)) else a
                                for a in record.args)
        record.msg = self._KEY.sub(r"\1***", str(record.msg))
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_RedactApiKey())


def dashboard_id(url: str) -> str:
    m = _DASHBOARD.search(url)
    if not m:
        raise ValueError(f"Neplatná adresa desky edesky.cz: {url!r}")
    return m.group(1)


def api_key() -> str:
    key = (os.environ.get("EDESKY_API_KEY") or "").strip()
    if not key:
        raise ValueError("chybí EDESKY_API_KEY (klíč z profilu na edesky.cz)")
    return key


def parse_documents(content: bytes) -> tuple[list[dict], int]:
    """XML odpověď API → (položky ve tvaru OFN Úřední desky, počet stránek)."""
    root = ElementTree.fromstring(content)
    if root.tag != "edesky_search_api":
        raise ValueError("Odpověď edesky.cz není výsledek hledání")
    page = root.find("meta/page")
    total_pages = int(page.get("total_pages", "1")) if page is not None else 1
    infos = []
    for doc in root.iter("document"):
        name = " ".join((doc.get("name") or "").split())
        edesky_url = doc.get("edesky_url") or ""
        if not name or not edesky_url:
            continue
        info: dict = {"iri": edesky_url, "url": doc.get("orig_url") or edesky_url, "název": {"cs": name}}
        if (created := (doc.get("created_at") or "")[:10]) and re.match(r"\d{4}-\d{2}-\d{2}$", created):
            info["vyvěšení"] = {"datum": created}
        docs = []
        for att in doc.iter("attachment"):
            url = att.get("url") or att.get("orig_url")
            if not url:
                continue
            docs.append({"název": {"cs": " ".join((att.get("name") or name).split())}, "url": url})
            if att.text and att.text.strip():
                TEXTS[url] = unquote(att.text.strip())
        if docs:
            info["dokument"] = docs
        infos.append(info)
    return infos, total_pages


async def fetch_board(client: httpx.AsyncClient, url: str) -> dict:
    """Dokumenty jedné desky edesky.cz (adresa https://edesky.cz/desky/<id>) za posledních WINDOW_DAYS dní."""
    params = {
        "keywords": "*", "search_with": "sql", "dashboard_id": dashboard_id(url), "include_texts": "1",
        "order": "date", "created_from": (date.today() - timedelta(days=WINDOW_DAYS)).isoformat(),
        "api_key": api_key(),
    }
    infos: list[dict] = []
    page = 1
    while page <= MAX_PAGES:
        resp = await client.get(API_URL, params={**params, "page": str(page)}, timeout=60)
        resp.raise_for_status()
        found, total = parse_documents(resp.content)
        infos += found
        if page >= total or not found:
            break
        page += 1
    return {"informace": infos}
