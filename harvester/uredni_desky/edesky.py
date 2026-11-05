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
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx

log = logging.getLogger(__name__)

API_URL = "https://edesky.cz/api/v1/documents"
WINDOW_DAYS = 60  # dokumenty načtené za posledních N dní; starší se v archivu označí jako sejmuté
MAX_PAGES = 5  # stránka = 200 dokumentů
_DASHBOARD = re.compile(r"edesky\.cz/desky/(\d+)")

# Rozpoznané texty příloh z API (URL přílohy → text) – použijí se, když soubor nejde stáhnout.
TEXTS: dict[str, str] = {}
# Přílohy z edesky.cz: URL přílohy → kde je její rozpoznaný text (veřejný soubor edesky_text_url).
ATTACHMENTS: dict[str, dict] = {}
_TEXT_FILES: dict[str, str] = {}  # stažené edesky_text_url v tomto běhu


def _http(url: str) -> bool:
    return url.startswith(("https://", "http://"))


def _clean_text(raw: str) -> str:
    from html import unescape

    return re.sub(r"<[^>]+>", " ", unescape(unescape(raw)))


def split_text_file(content: str, doc_id: str) -> dict[str, str]:
    """Soubor edesky_text_url → {id přílohy: text}. Každá příloha začíná „<id přílohy> <id dokumentu> <soubor> <url>“."""
    starts = list(re.finditer(rf"(?<!\d)(\d{{5,}}) {re.escape(doc_id)} (\S+) (\S+) ?", content))
    out = {}
    for n, m in enumerate(starts):
        end = starts[n + 1].start() if n + 1 < len(starts) else len(content)
        out[m.group(1)] = _clean_text(content[m.end():end]).strip()
    return out


async def attachment_text(client: httpx.AsyncClient, ref: dict) -> str:
    """Rozpoznaný text jedné přílohy z veřejného souboru edesky_text_url (jeden soubor na dokument)."""
    url = ref["text_url"]
    if url not in _TEXT_FILES:
        resp = await client.get(url, timeout=60)
        resp.raise_for_status()
        _TEXT_FILES[url] = resp.text
    parts = split_text_file(_TEXT_FILES[url], ref["doc_id"])
    return parts.get(ref["att_id"]) or ("\n\n".join(parts.values()) if len(parts) <= 1 else "")


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


_DOC_URL_ATTRS = ("edesky_url", "url", "link")
_NAME_ATTRS = ("name", "title", "nazev")
_DATE_ATTRS = ("created_at", "loaded_at", "published_at", "date")


def _attr(el: ElementTree.Element, names: tuple[str, ...]) -> str:
    for n in names:
        if (v := (el.get(n) or "").strip()):
            return v
    return ""


@dataclass
class Page:
    infos: list[dict]
    total_pages: int
    api_total: int | None  # počet dokumentů podle API (meta/documents_count)
    raw: int  # elementů <document> v odpovědi
    sample_attrs: list[str]  # názvy atributů prvního dokumentu (pro ladění, bez hodnot)


def parse_page(content: bytes) -> Page:
    """XML odpověď API → položky ve tvaru OFN Úřední desky + údaje pro log."""
    root = ElementTree.fromstring(content)
    if root.tag != "edesky_search_api":
        raise ValueError(f"Odpověď edesky.cz není výsledek hledání (kořen <{root.tag}>)")
    page = root.find("meta/page")
    total_pages = int(page.get("total_pages", "1") or 1) if page is not None else 1
    count = root.find("meta/documents_count")
    api_total = None
    if count is not None:
        try:
            api_total = int(count.get("total") or (count.text or "").strip())
        except ValueError:
            pass
    docs_el = list(root.iter("document"))
    infos = []
    for doc in docs_el:
        name = " ".join(_attr(doc, _NAME_ATTRS).split())
        edesky_url = _attr(doc, _DOC_URL_ATTRS)
        if not name or not edesky_url:
            continue
        orig = doc.get("orig_url") or ""
        info: dict = {"iri": edesky_url, "url": orig if _http(orig) else edesky_url, "název": {"cs": name}}
        if (created := _attr(doc, _DATE_ATTRS)[:10]) and re.match(r"\d{4}-\d{2}-\d{2}$", created):
            info["vyvěšení"] = {"datum": created}
        text_url = doc.get("edesky_text_url") or ""
        doc_id = doc.get("edesky_id") or (re.search(r"/dokument/(\d+)", edesky_url) or [None, ""])[1]
        docs = []
        for att in doc.iter("attachment"):
            att_url = att.get("url") or ""
            att_orig = att.get("orig_url") or ""
            # Odkaz pro čtenáře: originální soubor na webu obce. Stránka edesky.cz (/dokument/…#priloha_…)
            # je bez přihlášení skrytá, takže jen když originál chybí.
            link = att_orig if _http(att_orig) else att_url
            if not _http(link):
                continue
            m = re.search(r"#priloha_(\d+)", att_url)
            att_id = att.get("edesky_id") or (m.group(1) if m else "")
            docs.append({"název": {"cs": " ".join((att.get("name") or name).split())}, "url": link})
            if att.text and att.text.strip():
                TEXTS[link] = unquote(att.text.strip())
            if _http(text_url) and att_id and doc_id:
                ATTACHMENTS[link] = {"text_url": text_url, "att_id": att_id, "doc_id": doc_id}
        if docs:
            info["dokument"] = docs
        infos.append(info)
    return Page(infos, total_pages, api_total, len(docs_el), sorted(docs_el[0].attrib) if docs_el else [])


def parse_documents(content: bytes) -> tuple[list[dict], int]:
    page = parse_page(content)
    return page.infos, page.total_pages


# Varianty dotazu: první, která vrátí dokumenty, vyhrává (API je v testovacím provozu a „*“ v hledání
# podle názvu nemusí fungovat vždy).
STRATEGIES = (
    {"keywords": "*", "search_with": "sql", "window": True},
    {"keywords": "*", "search_with": "es", "window": True},
    {"keywords": "*", "search_with": "sql", "window": False},
    {"keywords": "", "search_with": "sql", "window": False},
)


async def fetch_board(client: httpx.AsyncClient, url: str) -> dict:
    """Dokumenty jedné desky edesky.cz (adresa https://edesky.cz/desky/<id>) za posledních WINDOW_DAYS dní."""
    board = dashboard_id(url)
    key = api_key()
    since = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    for strategy in STRATEGIES:
        params = {"keywords": strategy["keywords"], "search_with": strategy["search_with"], "dashboard_id": board,
                  "include_texts": "1", "order": "date", "api_key": key}
        if strategy["window"]:
            params["created_from"] = since
        infos: list[dict] = []
        page_no = 1
        while page_no <= MAX_PAGES:
            resp = await client.get(API_URL, params={**params, "page": str(page_no)}, timeout=60)
            resp.raise_for_status()
            page = parse_page(resp.content)
            if page_no == 1:
                log.info("edesky.cz deska %s (%s, keywords=%r, okno %s): API uvádí %s dokumentů, v odpovědi %d, "
                         "přečteno %d; atributy dokumentu: %s", board, strategy["search_with"], strategy["keywords"],
                         "60 dní" if strategy["window"] else "bez omezení", page.api_total, page.raw,
                         len(page.infos), ",".join(page.sample_attrs) or "-")
            infos += page.infos
            if page_no >= page.total_pages or not page.infos:
                break
            page_no += 1
        if infos:
            if not strategy["window"]:  # bez okna API vrací i staré dokumenty – vzít jen posledních WINDOW_DAYS dní
                infos = [i for i in infos if i.get("vyvěšení", {}).get("datum", since) >= since] or infos[:200]
            return {"informace": infos}
    return {"informace": []}
