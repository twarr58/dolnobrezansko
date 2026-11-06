"""Zpravodaje obcí – jen odkazy na čísla (aktuální i archiv), bez AI.

Zpravodaj mívá desítky stran a každého zajímá něco jiného, takže se nic neshrnuje: z archivní stránky
na webu obce se vytáhnou odkazy na jednotlivá čísla (PDF nebo stránka čísla), jejich název a rok.

Nastavení je u obce v data/region.json pod klíčem „newsletter“:
  name        název zpravodaje
  url         stránka s archivem
  match       regulární výraz – odkaz je číslo zpravodaje, když mu vyhoví adresa nebo text odkazu
  exclude     (volitelně) odkazy, které vynechat
  page_param, pages  (volitelně) stránkování archivu: ?<page_param>=2 … <pages>
  num_re      (volitelně) skupiny num a year, když text odkazu nic neříká („Stáhnout“) – např. z názvu souboru
  browser     (volitelně) true = hlavičky běžného prohlížeče (web odmítá roboty podle identifikace)
  year_headings (volitelně) true = rok čísla z nadpisu („2026“) nad seznamem měsíců
  also        (volitelně) další stránky se stejnými odkazy (např. „aktuální číslo“ mimo archiv)
  detail      (volitelně) {"match", "exclude"}: archiv odkazuje na stránku čísla, PDF je až na ní – odkaz se
              nahradí prvním souborem, který vyhoví „match“; z textu stránky se vezme i měsíc a rok vydání.
              Vyřešené stránky se pamatují, každá se otevře jen jednou.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
import lxml.html
from lxml import etree

from .fetch import needs_proxy
from .webpage import BROWSER_HEADERS

log = logging.getLogger(__name__)

REFRESH_HOURS = 12  # zpravodaj vychází jednou za měsíc až čtvrtletí – archiv stačí projít párkrát denně
PARSER_VERSION = 2  # zvýšit při změně čtení – uložené seznamy čísel se pak načtou znovu hned
MAX_ISSUES = 400
_GENERIC = re.compile(r"^(stáhnout|prohlédnout|zobrazit|otevřít|pdf|ke stažení|download|více|detail)?$", re.I)
_SIZE = re.compile(r"\s*(?:\(\s*)?(?:pdf\s*)?\d+(?:[.,]\d+)?\s*[kMG]B\s*\)?\s*$", re.I)
_NUM = re.compile(r"(?:č\.|číslo)\s*(\d{1,4})", re.I)
_YEAR = re.compile(r"(?<!\d)(19[89]\d|20\d\d)(?!\d)")


def _clean(text: str) -> str:
    text = " ".join(text.split())
    return _SIZE.sub("", text).strip(" -–|")


def _year(text: str) -> int | None:
    years = [int(y) for y in _YEAR.findall(text)]
    return years[-1] if years else None


_MONTH_SLUGS = {"leden": "leden", "unor": "únor", "brezen": "březen", "duben": "duben", "kveten": "květen",
                "cervenec": "červenec", "cerven": "červen", "srpen": "srpen", "zari": "září", "rijen": "říjen",
                "listopad": "listopad", "prosinec": "prosinec"}
_MONTH_ORDER = ["leden", "únor", "březen", "duben", "květen", "červen", "červenec", "srpen", "září", "říjen",
                "listopad", "prosinec"]


def _title_from_url(url: str) -> str:
    """Název z adresy, když odkaz nemá text (obrázek): …/rijen-2026/ → „Říjen 2026“."""
    stem = re.sub(r"[_-]+", " ", Path(unquote(urlsplit(url).path.rstrip("/"))).stem).strip()
    words = [_MONTH_SLUGS.get(w.lower(), w) for w in stem.split()]
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def _month_no(title: str) -> int | None:
    m = re.match(r"\s*(" + "|".join(sorted(_MONTH_ORDER, key=len, reverse=True)) + r")\b", title, re.I)
    return str(_MONTH_ORDER.index(m.group(1).lower()) + 1) if m else None


def parse_archive(html: bytes | str, base_url: str, cfg: dict) -> list[dict]:
    """Archivní stránka → čísla zpravodaje v pořadí ze stránky (archivy bývají od nejnovějšího)."""
    doc = lxml.html.fromstring(html)
    match = re.compile(cfg["match"], re.I)
    exclude = re.compile(cfg["exclude"], re.I) if cfg.get("exclude") else None
    num_re = re.compile(cfg["num_re"], re.I) if cfg.get("num_re") else None
    found: dict[str, dict] = {}
    heading_year = None
    is_year = re.compile(r"(19|20)\d\d")
    # Procházení v pořadí dokumentu: text prvku na začátku, text za prvkem (tail) na konci. Rok bývá i jako
    # volný text mezi značkami („<br />2026<br />“, „<hr />2017<br />“), nejen v samostatném nadpisu.
    for action, a in etree.iterwalk(doc, events=("start", "end")):
        if not isinstance(a.tag, str):
            continue
        if action == "end":
            tail = (a.tail or "").strip()
            if cfg.get("year_headings") and is_year.fullmatch(tail):
                heading_year = int(tail)
            continue
        own = (a.text or "").strip()
        if cfg.get("year_headings") and a.tag != "a" and is_year.fullmatch(own):
            heading_year = int(own)  # archiv po letech: „2026“ nad seznamem měsíců
            continue
        if a.tag != "a":
            continue
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        if not url.startswith(("https://", "http://")):
            continue
        text = _clean(a.text_content())
        haystack = unquote(url) + " " + text
        if not match.search(haystack) or (exclude and exclude.search(haystack)):
            continue
        title = "" if _GENERIC.match(text) else text
        from_text = bool(title)
        year = _year(title)
        if title and not year and (heading_year or _year(unquote(urlsplit(url).path))):
            year = heading_year or _year(unquote(urlsplit(url).path))  # nadpis roku, jinak rok v adrese
            title = f"{title} {year}"
        num = None
        if num_re and (m := num_re.search(unquote(url).rsplit("/", 1)[-1] + " " + text)):
            num, y = m.groupdict().get("num"), m.groupdict().get("year")
            if y:
                year = int(y) + (2000 if len(y) == 2 else 0)
            if not title and num:
                title = f"č. {int(num)}/{year}" if year else f"č. {int(num)}"
                from_text = True
        if num is None and (m := _NUM.search(title)):
            num = m.group(1)
        if not title:
            title = _title_from_url(url)
            year = year or _year(title)
        if num is None and (mon := _month_no(title)):
            num = mon  # „Říjen 2026“: pořadí v roce podle měsíce
        issue = {"t": title, "url": url, "y": year, "n": int(num) if num else None, "_text": from_text}
        old = found.get(url)
        if old is None or (from_text and not old["_text"]):
            found[url] = issue
    # totéž číslo nahrané na web dvakrát (jiná adresa, stejný název) stačí jednou
    titles: set[str] = set()
    issues = []
    for i in found.values():
        if i["t"] in titles:
            continue
        titles.add(i["t"])
        issues.append(i)
    # nejnovější nahoře: podle roku a čísla; číslo bez roku přebírá rok předchozího v pořadí stránky
    last_year = next((i["y"] for i in issues if i["y"]), None)  # úvodní čísla bez roku: rok nejbližšího
    for i in issues:
        i["_y"] = i["y"] or last_year or 0
        last_year = i["_y"] or last_year
    if sum(1 for i in issues if i["y"]) >= len(issues) / 2:
        issues.sort(key=lambda i: (i["_y"], i["n"] or 0), reverse=True)
    return [{k: i[k] for k in ("t", "url", "y") if i[k]} for i in issues[:MAX_ISSUES]]


_MONTHS = ("leden|únor|březen|duben|květen|červen|červenec|srpen|září|říjen|listopad|prosinec|"
           "jaro|léto|podzim|zima")
_ISSUE_DATE = re.compile(rf"(?<!\w)({_MONTHS})\w*\s+((?:19|20)\d\d)(?!\d)", re.I)
DETAIL_BUDGET = 60  # stránek čísel za běh (první běh projde archiv postupně)


def parse_detail(html: str, base_url: str, detail: dict) -> dict:
    """Stránka čísla → {"pdf": odkaz na soubor, "y": rok, "when": „prosinec 2021“} (co se najde)."""
    doc = lxml.html.fromstring(html)
    match = re.compile(detail["match"], re.I)
    exclude = re.compile(detail["exclude"], re.I) if detail.get("exclude") else None
    out: dict = {}
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        text = " ".join(a.text_content().split())
        hay = unquote(href) + " " + text
        if href and match.search(hay) and not (exclude and exclude.search(hay)):
            out["pdf"] = urljoin(base_url, href)
            break
    for bad in doc.xpath("//script|//style|//nav|//footer"):  # <header> ne – bývá v něm i datum čísla
        bad.drop_tree()
    if m := _ISSUE_DATE.search(" ".join(" ".join(doc.itertext()).split())):
        out["when"] = f"{m.group(1).lower()} {m.group(2)}"
        out["y"] = int(m.group(2))
    return out


async def _get(client: httpx.AsyncClient, url: str, fallback: httpx.AsyncClient | None,
               browser: bool = False) -> httpx.Response:
    headers = BROWSER_HEADERS if browser else None
    try:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
    except Exception as exc:
        if fallback is None or not needs_proxy(exc):
            raise
        resp = await fallback.get(url, headers=headers)
        resp.raise_for_status()
    return resp


async def resolve_details(client: httpx.AsyncClient, issues: list[dict], detail: dict, resolved: dict,
                          fallback: httpx.AsyncClient | None = None, budget: int = DETAIL_BUDGET) -> list[dict]:
    """Odkazy na stránky čísel → přímé odkazy na PDF (resolved = paměť stránka → výsledek, mění se na místě)."""
    out = []
    for issue in issues:
        page = issue["url"]
        if page not in resolved and budget > 0:
            budget -= 1
            try:
                resp = await _get(client, page, fallback)
                resolved[page] = parse_detail(resp.text, str(resp.url), detail)
            except Exception as exc:
                log.info("Zpravodaj – stránka čísla %s: %r", page, exc)
        info = resolved.get(page) or {}
        # datum ze stránky čísla jen když ho název nemá (u Psár je rok v názvu, datum na stránce může být z postranní lišty)
        when = info.get("when") if not issue.get("y") else None
        title = issue["t"] + (f" ({when})" if when and when not in issue["t"] else "")
        out.append({k: v for k, v in {"t": title, "url": info.get("pdf") or page, "page": page if info.get("pdf") else None,
                                      "y": issue.get("y") or info.get("y")}.items() if v})
    return out


def archive_pages(cfg: dict) -> list[str]:
    pages = [cfg["url"], *cfg.get("also", [])]
    if cfg.get("page_param"):
        sep = "&" if "?" in cfg["url"] else "?"
        pages += [f"{cfg['url']}{sep}{cfg['page_param']}={n}" for n in range(2, int(cfg.get("pages", 1)) + 1)]
    return pages


async def fetch_newsletter(client: httpx.AsyncClient, cfg: dict,
                           fallback: httpx.AsyncClient | None = None) -> list[dict]:
    issues: list[dict] = []
    seen: set[str] = set()
    for page in archive_pages(cfg):
        resp = await _get(client, page, fallback, bool(cfg.get("browser")))
        for issue in parse_archive(resp.text, str(resp.url), cfg):
            if issue["url"] not in seen:
                seen.add(issue["url"])
                issues.append(issue)
    return issues


async def collect(client: httpx.AsyncClient, members: list[dict], state_dir: Path, now: datetime,
                  fallback: httpx.AsyncClient | None = None) -> list[dict]:
    """Zpravodaje všech obcí. Archiv se znovu prochází po REFRESH_HOURS; při chybě zůstane poslední známý stav."""
    path = state_dir / "newsletters.json"
    try:
        cached = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        cached = {}
    out = []
    for m in members:
        cfg = m.get("newsletter")
        if not cfg or not cfg.get("url"):
            # newsletter: false = obec zpravodaj nevydává (ověřeno), chybějící klíč = zatím nevíme
            out.append({"obec": m["name"], "none": True} if cfg is False else {"obec": m["name"]})
            continue
        entry = cached.get(m["name"]) or {}
        fresh = entry.get("checked") and entry.get("cfg") == cfg and entry.get("v") == PARSER_VERSION and \
            datetime.fromisoformat(entry["checked"]) > now - timedelta(hours=REFRESH_HOURS)
        if not fresh:
            try:
                issues = await fetch_newsletter(client, cfg, fallback)
                resolved = entry.get("resolved", {}) if entry.get("cfg") == cfg else {}
                if cfg.get("detail"):
                    issues = await resolve_details(client, issues, cfg["detail"], resolved, fallback)
                entry = {"cfg": cfg, "checked": now.isoformat(timespec="seconds"), "issues": issues, "ok": True,
                         "resolved": resolved, "v": PARSER_VERSION,
                         **({"seen": entry["seen"]} if "seen" in entry else {})}
                log.info("Zpravodaj %s (%s): %d čísel", m["name"], cfg["name"], len(issues))
            except Exception as exc:  # web obce nejde – nechá se poslední známý seznam čísel
                log.warning("Zpravodaj %s: %r", m["name"], exc)
                entry = {**entry, "cfg": cfg, "checked": now.isoformat(timespec="seconds"), "ok": False, "v": PARSER_VERSION}
            cached[m["name"]] = entry
        # kdy jsme číslo uviděli poprvé (štítek „nové“ na webu); čísla z prvního průchodu mají datum neznámé ("")
        first = "seen" not in entry
        seen = entry.setdefault("seen", {})
        stamp = now.isoformat(timespec="seconds")
        for i in entry.get("issues", []):
            seen.setdefault(i["url"], "" if first else stamp)
        cached[m["name"]] = entry
        issues = [{**i, "fs": seen[i["url"]]} if seen.get(i["url"]) else i for i in entry.get("issues", [])]
        out.append({k: v for k, v in {
            "obec": m["name"], "name": cfg["name"], "archive": cfg["url"],
            "issues": issues, "ok": entry.get("ok", False),
        }.items() if v not in (None, "")})
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cached, ensure_ascii=False, indent=1), "utf-8")
    return out
