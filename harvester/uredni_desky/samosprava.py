"""Samospráva: usnesení a zápisy rad a zastupitelstev obcí, rozložené na jednotlivé body.

Každá obec zveřejňuje usnesení jinak. Nastavení je u obce v data/region.json pod klíčem „council“ – seznam
zdrojů {"body": "rada" | "zastupitelstvo", "fmt": …, "url": …}:

  vismo_years  rozcestník po letech (Vismo: Dolní Břežany, Březová-Oleško) → stránka roku → soubory
  pages        seznam odkazů na stránky zasedání (Psáry, Libeř, Jesenice) → soubory na stránce (nebo její text)
  files        stránka se seznamem souborů (Zvole, Vestec, Ohrobec, Vrané nad Vltavou)
  munipolis    stránka webu Munipolis se soubory v kódu stránky (Zlatníky-Hodkovice)

Volitelně: match (regulární výraz pro adresu nebo text odkazu na dokument / stránku zasedání), exclude,
kind ("usneseni" | "zapis"; výchozí podle názvu), browser (hlavičky běžného prohlížeče).

Sbírají se jen dokumenty letošního roku (a z konce minulého, dokud letos nic není). Seznamy se procházejí nejvýš
jednou za REFRESH_HOURS, každý dokument se stáhne jednou. Claude (volitelně) z dokumentu udělá krátké shrnutí
zasedání a jednotlivé body: co orgán schválil, vzal na vědomí, uložil… – každý jednou větou.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
import lxml.html

from . import ai, documents
from .digest import LABELS
from .fetch import needs_proxy
from .webpage import BROWSER_HEADERS, HTML_HEADERS

log = logging.getLogger(__name__)

REFRESH_HOURS = 12
RETRY_DELAY = 3.0
_SKIP = re.compile(r"facebook\.com|twitter\.com|x\.com/|linkedin\.com|/AA/index\.asp|mailto:|sharer|intent/tweet", re.I)
DOC_BUDGET = 80  # nejvýš nově stažených dokumentů za běh
PARSER_VERSION = 3  # 3: jeden dokument na stránku zasedání, jen odkazy z webu obce
VERSION = 1  # verze shrnutí bodů; zvýšení je přepočítá
BODIES = {"rada": "Rada", "zastupitelstvo": "Zastupitelstvo"}
_FILE = re.compile(r"(\.(pdf|docx?|odt|rtf)(?:$|[?#])|/file/\d+|File\.ashx|download\.php|admin-storage\.munipolis\.com/.+\.pdf)", re.I)
_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*((?:19|20)\d\d)(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)((?:19|20)\d\d)[_-](\d{2})[_-](\d{2})(?!\d)")
_DATE_SHORT = re.compile(r"(?<![\d.])(\d{2})(\d{2})(\d{2})_")  # 261014_usneseni… (Psáry)
_DATE_WORDS = re.compile(r"(?<!\d)(\d{1,2})\s+(\d{1,2})\s+((?:19|20)\d\d)(?!\d)")  # „ze dne 25 11 2024“
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
_NUMBER = re.compile(r"(?:č\.|číslo|zasedání|schůze)\s*(\d{1,3}(?:\s*[/-]\s*(?:R\s*[/-]\s*)?\d{2,4})?)", re.I)


def _date(text: str) -> str:
    """První datum v textu (název, adresa, dokument) jako ISO; prázdné, když žádné."""
    for rx, order in ((_DATE, "dmy"), (_DATE_ISO, "ymd"), (_DATE_WORDS, "dmy")):
        for m in rx.finditer(text or ""):
            parts = dict(zip(order, m.groups()))
            try:
                return datetime(int(parts["y"]), int(parts["m"]), int(parts["d"])).date().isoformat()
            except ValueError:
                continue
    m = _DATE_SHORT.search(text or "")
    if m:
        try:
            return datetime(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            pass
    return ""


def _kind(text: str, cfg: dict) -> str:
    if cfg.get("kind"):
        return cfg["kind"]
    low = text.lower()
    return "usneseni" if ("usnesen" in low or "výpis" in low or "vypis" in low) else "zapis"


def _name_from_url(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _title_from_file(name: str, body: str, kind: str) -> str:
    """Čitelný titulek místo názvu souboru: „Vypis-rada-59_schuze-2026_10_07_na-web.pdf“ → „Usnesení rady – 59. schůze“."""
    base = ("Usnesení" if kind == "usneseni" else "Zápis") + (" rady" if body == "rada" else " zastupitelstva")
    m = re.search(r"(?<!\d)(\d{1,3})[ _.-]*(schuze|schůze|zasedani|zasedání)", name, re.I)
    if m:
        return f"{base} – {int(m.group(1))}. {'schůze' if m.group(2).lower().startswith('sch') else 'zasedání'}"
    m = re.search(r"(?:č|c|cislo|číslo)[ ._-]*(\d{1,3})[ _/-]+((?:19|20)\d\d)", name, re.I)
    return f"{base} č. {int(m.group(1))}/{m.group(2)}" if m else base


def _links(html: str, base: str, content_only: bool = True) -> list[tuple[str, str]]:
    """(adresa, text) odkazů stránky; text z odkazu, jinak z title.

    content_only: jen odkazy za hlavním nadpisem stránky (<h1>) – menu webu (např. „Usnesení 2026“ rady
    i zastupitelstva) před ním se nepočítá. Bez <h1> všechny odkazy.
    """
    root = lxml.html.fromstring(html)
    root.make_links_absolute(base)
    h1 = root.find(".//h1") if content_only else None
    seen_h1 = h1 is None
    out = []
    for el in root.iter():
        if el is h1:
            seen_h1 = True
        if el.tag != "a" or not seen_h1:
            continue
        a = el
        href = (a.get("href") or "").split("#")[0]
        if not href.startswith("http"):
            continue
        text = " ".join(a.text_content().split()) or a.get("title") or ""
        out.append((href, text))
    return out


def _text_of(html: str) -> str:
    root = lxml.html.fromstring(html)
    for bad in root.xpath("//script|//style|//noscript|//nav|//header|//footer|//form"):
        bad.drop_tree()
    cands = root.xpath("//*[@id='hlobsah']") + root.xpath("//main|//article") + root.xpath("//*[@id='content']")
    main = next((c for c in cands if len(" ".join(c.text_content().split())) > 200), root)
    return documents._clean(main.text_content())


def _match(cfg: dict, href: str, text: str) -> bool:
    hay = f"{href} {text}"
    if cfg.get("exclude") and re.search(cfg["exclude"], hay, re.I):
        return False
    return bool(re.search(cfg["match"], hay, re.I)) if cfg.get("match") else True


def _wanted_year(date: str, hay: str, years: set[int]) -> bool:
    """Dokument z letošního (případně loňského) roku – podle data, jinak podle roku v názvu či adrese."""
    if date:
        return int(date[:4]) in years
    found = {int(y) for y in _YEAR.findall(hay)}
    return bool(found & years)


def unescape_munipolis(html: str) -> str:
    """Munipolis má obsah stránky jako řetězec v JavaScriptu (\\u003C…) – převést na HTML."""
    return (html.replace("\\u003C", "<").replace("\\u003E", ">").replace("\\u002F", "/")
            .replace('\\"', '"').replace("\\n", "\n"))


def parse_munipolis(html: str, base: str) -> list[tuple[str, str]]:
    """Soubory na stránce Munipolis: (adresa, název souboru)."""
    page = unescape_munipolis(html)
    out = []
    for m in re.finditer(r'href="(https://admin-storage\.munipolis\.com/[^"]+\.pdf)".*?<span>([^<]+)</span>', page, re.S):
        out.append((m.group(1), " ".join(m.group(2).split())))
    return out


class Fetcher:
    """Stahování přímo, při odmítnutí přes záložní proxy; server, který jednou přímo nejde, jede dál přes ni."""

    def __init__(self, client: httpx.AsyncClient, fallback: httpx.AsyncClient | None):
        self.client, self.fallback = client, fallback
        self.proxy_hosts: set[str] = set()

    async def get(self, url: str, browser: bool = False) -> httpx.Response:
        headers = BROWSER_HEADERS if browser else HTML_HEADERS
        host = urlsplit(url).hostname or ""
        if self.fallback is not None and host in self.proxy_hosts:
            return await _get_limited(self.fallback, url, headers)
        try:
            r = await _get_limited(self.client, url, headers, raise_status=False)
            if r.status_code >= 500:  # weby obcí občas krátce padají – jeden pokus navíc
                await asyncio.sleep(RETRY_DELAY)
                r = await _get_limited(self.client, url, headers, raise_status=False)
            r.raise_for_status()
            return r
        except httpx.HTTPError as exc:
            if self.fallback is None or not (needs_proxy(exc) or isinstance(exc, httpx.HTTPStatusError)
                                             and exc.response.status_code >= 500):
                raise
            self.proxy_hosts.add(host)
            return await _get_limited(self.fallback, url, headers)


async def _get_limited(client: httpx.AsyncClient, url: str, headers: dict, raise_status: bool = True) -> httpx.Response:
    """GET s limitem velikosti (documents.MAX_BYTES) – čte se po částech, obří odpověď se nenačte celá do paměti."""
    async with client.stream("GET", url, headers=headers, timeout=40) as r:
        if raise_status:
            r.raise_for_status()
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > documents.MAX_BYTES:
                raise ValueError(f"odpověď je větší než {documents.MAX_BYTES // 2**20} MB")
        # hlavičky bez Content-Encoding/Length – obsah už je rozbalený
        hdrs = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in ("content-encoding", "content-length")]
        return httpx.Response(r.status_code, headers=hdrs, content=bytes(buf), request=r.request)


async def list_documents(f: Fetcher, cfg: dict, years: set[int]) -> list[dict]:
    """Dokumenty jednoho zdroje: [{url, title, date, kind, page}] – jen z požadovaných let."""
    fmt, url, browser = cfg["fmt"], cfg["url"], bool(cfg.get("browser"))
    docs: list[dict] = []

    def add(href: str, text: str, page: str, extra: str = "") -> None:
        generic = not text or re.fullmatch(r"(prohlédnout|stáhnout|zobrazit|ke stažení|pdf|doc)", text, re.I)
        title = _name_from_url(href) if generic else text
        title = re.sub(r"\s+(pdf|docx?|odt)\s+[\d,.]+\s*[kM]B$|\s*\(\s*[\d,.]+\s*[kM]B\s*\)$|\s*\[[^\]]*[kM]B\]$", "",
                       title, flags=re.I).strip()
        date = _date(f"{title} {extra}") or _date(_name_from_url(href))
        if not _wanted_year(date, f"{title} {href} {extra}", years):
            return
        if any(d["url"] == href for d in docs):
            return
        kind = _kind(title, cfg)
        if generic or not re.search(r"\s", title):  # holý název souboru → čitelný titulek
            title = _title_from_file(title, cfg["body"], kind)
        title = re.sub(r"\.(pdf|docx?|odt|rtf)$", "", title, flags=re.I).strip()
        docs.append({"url": href, "title": title, "date": date, "kind": kind, "page": page})

    async def from_page(page: str, ptitle: str) -> None:
        """Stránka zasedání: soubory usnesení / zápisu na ní, jinak její text."""
        try:
            rp = await f.get(page, browser)
        except httpx.HTTPError as exc:
            log.info("Samospráva %s: %r", page, exc)
            return
        files = [(h, t) for h, t in _links(rp.text, str(rp.url))
                 if _FILE.search(h) and not re.search(r"příloha|priloha|prezenčn|jednací řád", t, re.I)
                 and (not cfg.get("file_match") or re.search(cfg["file_match"], f"{h} {t}", re.I))]
        page_text = _text_of(rp.text)
        page_date = _date(ptitle) or _date(re.sub(r"\s+", " ", page_text[:600]))
        extra = page_date and f"{int(page_date[8:])}.{int(page_date[5:7])}.{page_date[:4]}"
        if files:  # jeden dokument na zasedání: přednostně ten, jehož název odpovídá zdroji (zápis / usnesení)
            href, text = next(((h, t) for h, t in files if _match(cfg, "", t)), files[0])
            add(href, ptitle or text, page, extra=extra)
        elif len(page_text) > 300:  # usnesení přímo v textu stránky
            add(page, ptitle, page, extra=extra)
            if docs and docs[-1]["url"] == page:
                docs[-1]["html"] = True

    async def items(listing: str, html: str, base: str, extra: str = "") -> None:
        """Odkazy seznamu: soubor rovnou, stránka zasedání přes from_page."""
        pages = []
        host = (urlsplit(listing).hostname or "").removeprefix("www.")
        for href, text in _links(html, base):
            if href.rstrip("/") == listing.rstrip("/") or _SKIP.search(href) or not _match(cfg, href, text) \
                    or re.search(r"příloha|priloha|jednací řád", text, re.I):
                continue
            if not _FILE.search(href) and (urlsplit(href).hostname or "").removeprefix("www.") != host:
                continue  # stránky zasedání jen z webu obce (ne sdílení, přihlášení, cizí weby)
            if _FILE.search(href):
                add(href, text, listing, extra=extra)
            elif href not in [p for p, _ in pages] and (_wanted_year(_date(text), f"{text} {href}", years)
                                                        or not _YEAR.search(f"{text} {unquote(href)}") and extra):
                pages.append((href, text))
        for page, ptitle in pages[:40]:
            await from_page(page, ptitle)

    r = await f.get(url, browser)
    if fmt == "files":
        for href, text in _links(r.text, str(r.url)):
            if _FILE.search(href) and _match(cfg, href, text):
                add(href, text, url)
    elif fmt == "munipolis":
        for href, text in parse_munipolis(r.text, str(r.url)):
            if _match(cfg, href, text):
                add(href, text, url)
    elif fmt == "vismo_years":
        links = _links(r.text, str(r.url))
        for year in sorted(years, reverse=True):
            page = next((h for h, t in links if re.search(rf"(?<!\d){year}(?!\d)", t) and not _FILE.search(h)
                         and not _SKIP.search(h)
                         and h.rstrip("/") != url.rstrip("/") and (not cfg.get("year_match") or re.search(cfg["year_match"], t, re.I))),
                        None)
            if page:
                ry = await f.get(page, browser)
                await items(page, ry.text, str(ry.url), extra=str(year))
    elif fmt == "pages":
        await items(url, r.text, str(r.url))
    else:
        raise ValueError(f"neznámý formát zdroje samosprávy: {fmt}")
    return docs


# ---------- shrnutí od Claude ----------

VERBS = ["schvaluje", "neschvaluje", "bere na vědomí", "ukládá", "pověřuje", "souhlasí", "nesouhlasí", "zamítá",
         "volí", "jmenuje", "odvolává", "zřizuje", "ruší", "vydává", "revokuje", "doporučuje", "jiné"]

SYSTEM = """Jsi pomocník, který lidem z obcí mikroregionu Dolnobřežansko vysvětluje, co rozhodla rada nebo
zastupitelstvo jejich obce. Dostaneš usnesení nebo zápis z jednoho zasedání. Vrať:

- date: datum zasedání (YYYY-MM-DD), jen když je v dokumentu; jinak prázdné.
- number: číslo zasedání či usnesení tak, jak je v dokumentu (např. „27/2026“, „09/R/2026“), jinak prázdné.
- tldr: jedna věta (max. 180 znaků), co bylo na zasedání nejdůležitější pro lidi v obci.
- points: jednotlivé body usnesení v pořadí dokumentu. Každý bod:
  - verb: co orgán udělal (schvaluje, bere na vědomí, ukládá, pověřuje, …; „jiné“, když nic nesedí),
  - text: jedna srozumitelná věta (max. 220 znaků), co přesně – konkrétně: komu, co, kde, za kolik,
    do kdy. Bez úředních obratů, zkratky vysvětli.
  - category: nejbližší kategorie,
  - amount: částka, když v bodu je (např. „1 714 874 Kč“), jinak prázdné,
  - importance: "vysoka" (peníze obce nad 500 tis. Kč, stavby, územní plán, poplatky, vyhlášky, věci
    dotýkající se mnoha lidí), "stredni", nebo "nizka" (formality, provozní drobnosti).
  Vynech čistě procedurální body (schválení programu, ověřovatelů zápisu, zapisovatele, návrhové komise).
  U zápisu vezmi jen to, co orgán skutečně usnesl (ne celou diskusi).

Piš česky, věcně a spisovně. Nic si nevymýšlej. Jména soukromých osob nevypisuj (nahraď „žadatel“, „nájemce“
apod.); firmy, spolky, školy a úřady jmenuj."""

SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string"},
        "number": {"type": "string"},
        "tldr": {"type": "string"},
        "points": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "verb": {"type": "string", "enum": VERBS},
                "text": {"type": "string"},
                "category": {"type": "string", "enum": sorted(LABELS)},
                "amount": {"type": "string"},
                "importance": {"type": "string", "enum": ["vysoka", "stredni", "nizka"]},
            },
            "required": ["verb", "text", "category", "amount", "importance"], "additionalProperties": False}},
    },
    "required": ["date", "number", "tldr", "points"],
    "additionalProperties": False,
}


def build_content(meta: dict, doc: dict) -> list[dict]:
    head = "\n".join(f"{k}: {v}" for k, v in meta.items() if v)
    content: list[dict] = [{"type": "text", "text": f"Dokument ze samosprávy obce:\n{head}"}]
    content += ai.build_content({}, [doc])[1:-1]  # příloha stejně jako u úřední desky (PDF, text)
    content.append({"type": "text", "text": "Rozlož dokument na body usnesení podle pokynů."})
    return content


# ---------- sběr ----------

def _id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


async def collect(client: httpx.AsyncClient, members: list[dict], state_dir: Path, now: datetime,
                  fallback: httpx.AsyncClient | None = None, summarizer: ai.Summarizer | None = None) -> dict:
    """Usnesení a zápisy všech obcí: {"sources": [...], "meetings": [...]} pro web."""
    path = state_dir / "council.json"
    try:
        state = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        state = {}
    lists: dict = state.setdefault("lists", {})
    docs: dict = state.setdefault("docs", {})
    years = {now.year} | ({now.year - 1} if now.month <= 2 else set())
    f = Fetcher(client, fallback)
    sources = []
    pairs = [(m, cfg) for m in members for cfg in m.get("council") or []]
    list_sem = asyncio.Semaphore(4)  # různé obce souběžně, jedna obec postupně

    async def refresh(m: dict, cfg: dict) -> None:
        key = f"{m['name']}|{cfg['body']}|{cfg['url']}"
        entry = lists.get(key) or {}
        fresh = entry.get("checked", "") > (now - timedelta(hours=REFRESH_HOURS)).isoformat() \
            and entry.get("cfg") == cfg and entry.get("v") == PARSER_VERSION and entry.get("year") == now.year
        if fresh:
            return
        async with list_sem:
            try:
                found = await list_documents(f, cfg, years)
                entry = {"cfg": cfg, "checked": now.isoformat(timespec="seconds"), "ok": True, "v": PARSER_VERSION,
                         "year": now.year, "docs": found}
                log.info("Samospráva %s (%s): %d dokumentů", m["name"], cfg["body"], len(found))
            except Exception as exc:  # noqa: BLE001 – web obce nejde: zůstává poslední seznam, příště znovu
                log.warning("Samospráva %s (%s): %r", m["name"], cfg["body"], exc)
                entry = {**entry, "ok": False, "failed": now.isoformat(timespec="seconds")}
        lists[key] = entry

    await asyncio.gather(*(refresh(m, cfg) for m, cfg in pairs))
    for m, cfg in pairs:
        entry = lists.get(f"{m['name']}|{cfg['body']}|{cfg['url']}") or {}
        for d in entry.get("docs", []):
            did = _id(d["url"])
            # „found“ = kdy se dokument poprvé objevil (web podle něj ukazuje novinky od poslední návštěvy);
            # u dokumentů z doby před tímto polem čas stažení, jinak by se všechny tvářily jako nové
            new_doc = did not in docs
            doc = docs.setdefault(did, {})
            doc.update({**d, "o": m["name"], "body": cfg["body"], "browser": bool(cfg.get("browser"))})
            doc.setdefault("found", now.isoformat(timespec="seconds") if new_doc else doc.get("fetched"))
        sources.append({"obec": m["name"], "body": cfg["body"], "url": cfg["url"], "ok": entry.get("ok", False),
                        "n": len(entry.get("docs", []))})

    # stažení nových dokumentů a shrnutí (nejnovější první)
    budget = {"n": DOC_BUDGET}
    sem = asyncio.Semaphore(3)
    current = {_id(d["url"]) for e in lists.values() for d in e.get("docs", [])}

    async def process(did: str) -> None:
        d = docs[did]
        if not d.get("sha") and budget["n"] > 0:
            budget["n"] -= 1
            async with sem:
                try:
                    r = await f.get(d["url"], bool(d.get("browser")))
                    info = await asyncio.to_thread(documents.analyze, r.content, d["url"], d.get("title", ""),
                                                   r.headers.get("content-type", ""))
                    if d.get("html"):
                        info.full_text = _text_of(r.text)
                    d.update(sha=documents.fingerprint(r.content), ext=info.ext, pages=info.pages, chars=len(info.full_text),
                             fetched=now.isoformat(timespec="seconds"))
                    d["_bytes"], d["_text"] = r.content, info.full_text
                    if not d.get("date"):
                        d["date"] = _date(info.full_text[:1500])
                except Exception as exc:  # noqa: BLE001
                    log.info("Samospráva dokument %s: %r", d["url"], exc)
                    d["error"] = f"{type(exc).__name__}: {exc}"[:200]
                    return
        need_ai = d.get("sha") and summarizer is not None and summarizer.available() \
            and (d.get("ai_v") != VERSION or d.get("ai_sha") != d.get("sha"))
        if need_ai:
            if "_bytes" not in d:  # stažené v dřívějším běhu – pro shrnutí znovu
                try:
                    async with sem:
                        r = await f.get(d["url"], bool(d.get("browser")))
                    info = await asyncio.to_thread(documents.analyze, r.content, d["url"], d.get("title", ""),
                                                   r.headers.get("content-type", ""))
                    d["_bytes"], d["_text"] = r.content, (_text_of(r.text) if d.get("html") else info.full_text)
                except Exception as exc:  # noqa: BLE001
                    log.info("Samospráva dokument %s: %r", d["url"], exc)
                    return
            meta = {"Obec": d["o"], "Orgán": BODIES.get(d["body"], d["body"]), "Dokument": d.get("title"),
                    "Datum podle webu": d.get("date"), "Dnešní datum": now.date().isoformat()}
            doc = {"name": d.get("title"), "ext": d.get("ext"), "pages": d.get("pages", 0),
                   "bytes": d["_bytes"] if d.get("ext") == "pdf" and d.get("chars", 0) < 500 else None,
                   "text": d.get("_text", "")}
            out = await summarizer.summarize_custom(build_content(meta, doc), SYSTEM, SCHEMA)
            if out:
                d.update(ai=out, ai_v=VERSION, ai_sha=d.get("sha"), ai_at=now.isoformat(timespec="seconds"))
                if not d.get("date") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", out.get("date") or ""):
                    d["date"] = out["date"]

    order = sorted((did for did in docs if did in current), key=lambda i: docs[i].get("date") or "", reverse=True)
    await asyncio.gather(*(process(did) for did in order))
    for d in docs.values():
        d.pop("_bytes", None), d.pop("_text", None)
    # ze stavu pryč dokumenty, které už v žádném seznamu nejsou a jsou starší než rok
    year_ago = (now - timedelta(days=400)).date().isoformat()
    for did in [i for i, d in docs.items() if i not in current and (d.get("date") or "") < year_ago]:
        docs.pop(did)
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    meetings = [build_meeting(did, docs[did]) for did in order if docs[did].get("sha") or docs[did].get("ai")]
    return {"sources": sources, "meetings": meetings}


def build_meeting(did: str, d: dict) -> dict:
    a = d.get("ai") or {}
    points = [{k: v for k, v in {"v": p.get("verb"), "t": p.get("text"), "cat": p.get("category"),
                                  "amt": p.get("amount"), "imp": p.get("importance")}.items() if v}
              for p in a.get("points", []) if p.get("text")]
    # web obce datum často neuvádí (Dolní Břežany: jen „34-R-26 usnesení“) – pak datum zasedání z textu (AI)
    ai_date = a.get("date") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(a.get("date") or "")) else None
    return {k: v for k, v in {
        "id": did, "o": d["o"], "body": d["body"], "kind": d.get("kind"), "date": d.get("date") or ai_date,
        "no": a.get("number"), "t": d.get("title"), "url": d["url"], "page": d.get("page"),
        "x": d.get("ext"), "tldr": a.get("tldr"), "points": points, "src": "claude" if a else None,
        "fs": d.get("found"),
    }.items() if v not in (None, "", [])}


async def list_all(members: list[dict], only: str = "") -> None:
    """Ladění: vypíše dokumenty letošního roku ze všech zdrojů (bez stahování a bez AI)."""
    import os

    from . import USER_AGENT

    now = datetime.now()
    years = {now.year} | ({now.year - 1} if now.month <= 2 else set())
    proxy = os.environ.get("HARVEST_FALLBACK_PROXY") or None
    common = dict(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=httpx.Timeout(40, connect=20))
    async with httpx.AsyncClient(**common) as client:
        fallback = httpx.AsyncClient(**common, proxy=proxy) if proxy else None
        f = Fetcher(client, fallback)
        pairs = [(m, cfg) for m in members if not only or only.lower() in m["name"].lower()
                 for cfg in m.get("council") or []]

        async def one(m: dict, cfg: dict) -> str:
            try:
                docs = await list_documents(f, cfg, years)
            except Exception as exc:  # noqa: BLE001
                return f"!! {m['name']} ({cfg['body']}): {exc!r}"
            lines = [f"== {m['name']} ({cfg['body']}): {len(docs)} dokumentů – {cfg['url']}"]
            lines += [f"   {d['date'] or '????-??-??'} {d['kind']:<8} {d['title'][:70]:<70} {d['url']}" for d in docs[:6]]
            return "\n".join(lines)

        for text in await asyncio.gather(*(one(m, cfg) for m, cfg in pairs)):
            print(text)
        if fallback:
            await fallback.aclose()
