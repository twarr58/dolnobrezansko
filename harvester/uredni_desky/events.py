"""Kalendář akcí obcí – kulturní a společenské akce, svozy, zasedání. Bez AI, jen datum, čas, název a odkaz.

Nastavení u obce v data/region.json pod klíčem „events“:
  url    stránka s kalendářem na webu obce (na ni vede odkaz „Kalendář na webu obce“)
  browser (volitelně) true = hlavičky běžného prohlížeče (web odmítá roboty podle identifikace)
  fmt    jak číst akce:
         vismo   redakční systém vismo (Dolní Břežany, Libeř) – JSON API kalendáře
                 /api/services/app/ObjectOverview/GetEventsByDay
         rdc     seznam <li class="rdc-event" data-date="MM/DD/YYYY"> (Ohrobec)
         article seznam <div class="article"> s datem „pátek, 06. listopad 2026“ (Vrané nad Vltavou)
         linklist seznam odkazů na akce (adresa vyhoví „match“), datum a čas v textu odkazu nebo hned za ním
                 („17:00 - 07.11.2026“, „Farmářské trhy 21.11.2026“) – Psáry, Vestec
                 volitelně range_url s {od} a {do} (d.m.yyyy) pro výpis za celé okno – Jesenice
         link    akce nečteme (web nejde nebo nemá čitelný kalendář) – jen odkaz na kalendář
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import httpx
import lxml.html

from .fetch import needs_proxy
from .webpage import BROWSER_HEADERS

log = logging.getLogger(__name__)

REFRESH_HOURS = 6
WINDOW_DAYS = 60  # akce na příštích N dní
MAX_EVENTS = 300  # na obec

_MONTHS = {m: n for n, ms in enumerate([
    ("leden", "ledna"), ("únor", "února"), ("březen", "března"), ("duben", "dubna"), ("květen", "května"),
    ("červen", "června"), ("červenec", "července"), ("srpen", "srpna"), ("září",), ("říjen", "října"),
    ("listopad", "listopadu"), ("prosinec", "prosince")], 1) for m in ms}
_CZ_DATE = re.compile(r"(\d{1,2})\.\s*(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\s+(\d{4})", re.I)
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")
_DM_RANGE = re.compile(r"^\s*(\d{1,2})\.\s*(\d{1,2})\.(?:\s*[-–]\s*(\d{1,2})\.\s*(\d{1,2})\.)?\s*")


def _clean(s: str) -> str:
    return " ".join((s or "").split())


async def _get(client: httpx.AsyncClient, url: str, fallback: httpx.AsyncClient | None, **kw) -> httpx.Response:
    if kw.pop("browser", False):
        kw["headers"] = BROWSER_HEADERS
    try:
        resp = await client.get(url, **kw)
        resp.raise_for_status()
    except Exception as exc:
        if fallback is None or not needs_proxy(exc):
            raise
        resp = await fallback.get(url, **kw)
        resp.raise_for_status()
    return resp


# ---------- vismo ----------

def vismo_ids(html: str) -> tuple[str, str]:
    """Stránka kalendáře vismo → (id objektu, id bloku) pro API."""
    obj = re.search(r'data-id-object="(\d+)"', html)
    block = re.search(r"element-type-of-object-type-block-id\D{0,3}(\d+)", html)
    if not obj:
        raise ValueError("stránka kalendáře vismo bez data-id-object")
    return obj.group(1), block.group(1) if block else ""


def parse_vismo(data: dict, base_url: str) -> list[dict]:
    """Odpověď GetEventsByDay → akce. Vícedenní akce je v každém dni zvlášť (isStartDay … isEndDay) – spojí se;
    opakovaná akce (týž objekt další víkend) je nový výskyt."""
    out: list[dict] = []
    open_: dict[str, dict] = {}
    for day in (data.get("result") or {}).get("items") or []:
        d = (day.get("date") or "")[:10]
        for e in day.get("events") or []:
            if e.get("IsCanceled"):
                continue
            key = str(e.get("v-id") or e.get("name"))
            ev = open_.get(key)
            if ev is not None and not e.get("isStartDay"):
                ev["end"] = d
            else:
                tags = e.get("codeListItems") if isinstance(e.get("codeListItems"), list) else []
                ev = {k: v for k, v in {
                    "t": _clean(e.get("name")), "d": d, "time": e.get("fromTime"),  # isAllDay bývá i u akcí s časem
                    "url": e.get("v-viewUrlAbsolute") or urljoin(base_url, e.get("v-viewUrl") or ""),
                    "tags": [t["name"] for t in tags if t.get("name") and not t.get("isHiddenName")][:3],
                }.items() if v}
                out.append(ev)
                open_[key] = ev
            if e.get("isEndDay") or not e.get("isMultiDay"):
                open_.pop(key, None)
    return out


# ---------- rdc (Ohrobec) ----------

def parse_rdc(html: str, base_url: str) -> list[dict]:
    doc = lxml.html.fromstring(html)
    out: dict[str, dict] = {}
    for li in doc.xpath('//li[contains(concat(" ", normalize-space(@class), " "), " rdc-event ")]'):
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})$", li.get("data-date") or "")
        a = li.find(".//a")
        if not m or a is None:
            continue
        d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        text = _clean(a.text_content())
        r = _DM_RANGE.match(text)
        title = text[r.end():] if r else text
        url = urljoin(base_url, a.get("href") or "")
        end = None
        if r and r.group(3):
            end_d = date(d.year, int(r.group(4)), int(r.group(3)))
            end = (end_d if end_d >= d else end_d.replace(year=d.year + 1)).isoformat()
        ev = out.get(url)
        if ev is None or d.isoformat() < ev["d"]:  # vícedenní akce je v seznamu pro každý den – stačí začátek
            out[url] = {k: v for k, v in {"t": title, "d": d.isoformat(), "end": end, "url": url}.items() if v}
            if ev and ev.get("end") and not end:
                out[url]["end"] = ev["end"]
    return list(out.values())


# ---------- article (Vrané nad Vltavou) ----------

def parse_article(html: str, base_url: str) -> list[dict]:
    doc = lxml.html.fromstring(html)
    out = []
    for art in doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " article ")]'):
        a = art.xpath('.//*[contains(@class, "article-title")]//a')
        meta = art.xpath('.//*[contains(@class, "article-meta")]')
        if not a or not meta:
            continue
        when = _clean(" ".join(meta[0].xpath('.//*[contains(@class, "datetime")]//text()')))
        m = _CZ_DATE.search(when)
        if not m:
            continue
        d = date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        t = _TIME.search(when[m.end():])
        place = _clean(meta[0].text_content().replace(when, "", 1)).strip(" |")
        out.append({k: v for k, v in {
            "t": _clean(a[0].text_content()), "d": d.isoformat(), "time": f"{int(t.group(1)):02d}:{t.group(2)}" if t else None,
            "place": place or None, "url": urljoin(base_url, a[0].get("href") or ""),
        }.items() if v})
    return out


# ---------- linklist (Psáry, Vestec) ----------

_CLOCK = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_NUM_DATE = re.compile(r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.\s?(20\d\d)(?!\d)")


def parse_linklist(html: str, base_url: str, match: str) -> list[dict]:
    """Odkazy na akce v pořadí stránky; datum, čas a místo z textu odkazu a z textů hned za ním."""
    from lxml import etree

    doc = lxml.html.fromstring(html)
    for bad in doc.xpath("//script|//style|//nav|//footer"):
        bad.drop_tree()
    pat = re.compile(match, re.I)
    rows: list[dict] = []
    current = None
    inside = None
    for action, el in etree.iterwalk(doc, events=("start", "end")):
        if action == "start":
            if inside is None and el.tag == "a" and pat.search(el.get("href") or ""):
                current = {"a": el, "texts": []}
                rows.append(current)
                inside = el
            elif inside is None and el.tag == "a":
                inside = el  # jiný odkaz (menu, sdílení): jeho text k akci nepatří
            elif inside is None and current is not None and el.text and el.text.strip():
                current["texts"].append(_clean(el.text))
        else:
            if el is inside:
                inside = None
            if inside is None and current is not None and el.tail and el.tail.strip():
                current["texts"].append(_clean(el.tail))
    out: dict[str, dict] = {}
    for r in rows:
        a = r["a"]
        url = urljoin(base_url, a.get("href") or "")
        title = _clean(a.text_content())
        texts = r["texts"][:4]
        m = _NUM_DATE.search(title) or next((x for x in (_NUM_DATE.search(t) for t in texts) if x), None)
        if not title or not m or url in out:
            continue
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        title = _clean(_NUM_DATE.sub("", title)).strip(" -–|")
        # čas jen s dvojtečkou a mimo datum („6.11.2026“ není 06:11)
        t = next((x for x in (_CLOCK.search(_NUM_DATE.sub(" ", t)) for t in [a.text_content(), *texts]) if x), None)
        # místo: za datem v témž řádku („7.11.2026 9:00, Jesenice - Hodkovice“), jinak krátký text před datem (Psáry)
        date_line = next((x for x in texts if _NUM_DATE.search(x)), "")
        after = re.match(r"^[\d.:\s–-]+,\s*(?:\d{1,2}:\d\d\s*)?(.+)$", date_line)
        before = texts[:texts.index(date_line)] if date_line in texts else []
        place = _clean(after.group(1))[:80] if after else next(
            (x for x in before if not _CLOCK.search(x) and len(x) < 80), None)
        out[url] = {k: v for k, v in {"t": title, "d": d, "time": f"{int(t.group(1)):02d}:{t.group(2)}" if t else None,
                                      "place": place, "url": url}.items() if v}
    return list(out.values())


# ---------- sběr ----------

async def fetch_events(client: httpx.AsyncClient, cfg: dict, today: date,
                       fallback: httpx.AsyncClient | None = None) -> list[dict]:
    fmt = cfg.get("fmt")
    end = today + timedelta(days=WINDOW_DAYS)
    if fmt == "vismo":
        page = await _get(client, cfg["url"], fallback, browser=cfg.get("browser"))
        obj, block = vismo_ids(page.text)
        api = urljoin(str(page.url), "/api/services/app/ObjectOverview/GetEventsByDay")
        resp = await _get(client, api, fallback, browser=cfg.get("browser"), params={
            "From": f"{today.isoformat()}T00:00:00", "To": f"{end.isoformat()}T23:59:59",
            "ObjectId": obj, "ElementTypeOfObjectTypeBlockId": block})
        events = parse_vismo(resp.json(), str(page.url))
    elif fmt == "linklist":
        # range_url: výpis akcí za celé okno ({od}/{do} = d.m.yyyy), jinak stránka kalendáře
        dm = lambda d: f"{d.day}.{d.month}.{d.year}"  # noqa: E731
        url = cfg["range_url"].format(od=dm(today), do=dm(end)) if cfg.get("range_url") else cfg["url"]
        page = await _get(client, url, fallback, browser=cfg.get("browser"))
        events = parse_linklist(page.text, str(page.url), cfg["match"])
    elif fmt in ("rdc", "article"):
        page = await _get(client, cfg["url"], fallback, browser=cfg.get("browser"))
        events = (parse_rdc if fmt == "rdc" else parse_article)(page.text, str(page.url))
    else:
        return []
    lo, hi = today.isoformat(), end.isoformat()
    events = [e for e in events if (e.get("end") or e["d"]) >= lo and e["d"] <= hi]
    events.sort(key=lambda e: (e["d"], e.get("time") or "", e["t"]))
    return events[:MAX_EVENTS]


async def collect(client: httpx.AsyncClient, members: list[dict], state_dir: Path, now: datetime,
                  fallback: httpx.AsyncClient | None = None) -> dict:
    """Akce všech obcí: {"sources": [...], "items": [...]}; při chybě zůstanou naposledy známé akce."""
    path = state_dir / "events.json"
    try:
        cached = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        cached = {}
    today = now.date()
    sources, items = [], []
    for m in members:
        cfg = m.get("events")
        if not cfg or not cfg.get("url"):
            continue
        entry = cached.get(m["name"]) or {}
        fresh = entry.get("checked") and entry.get("cfg") == cfg and \
            datetime.fromisoformat(entry["checked"]) > now - timedelta(hours=REFRESH_HOURS)
        if not fresh and cfg.get("fmt") != "link":
            try:
                events = await fetch_events(client, cfg, today, fallback)
                entry = {"cfg": cfg, "checked": now.isoformat(timespec="seconds"), "events": events, "ok": True}
                log.info("Akce %s: %d", m["name"], len(events))
            except Exception as exc:  # web obce nejde – nechají se naposledy známé akce
                log.warning("Akce %s: %r", m["name"], exc)
                entry = {**entry, "cfg": cfg, "checked": now.isoformat(timespec="seconds"), "ok": False}
            cached[m["name"]] = entry
        live = [e for e in entry.get("events", []) if (e.get("end") or e["d"]) >= today.isoformat()]
        items += [{"o": m["name"], **e} for e in live]
        sources.append({k: v for k, v in {
            "obec": m["name"], "url": cfg["url"], "read": cfg.get("fmt") != "link",
            "ok": entry.get("ok", False) if cfg.get("fmt") != "link" else None, "n": len(live)}.items() if v is not None})
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cached, ensure_ascii=False, indent=1), "utf-8")
    items.sort(key=lambda e: (e["d"], e.get("time") or "", e["o"], e["t"]))
    return {"sources": sources, "items": items}
