"""Kalendáře k odběru (iCalendar, RFC 5545): data/kalendar/vse.ics a data/kalendar/<slug-obce>.ics.
Navíc data/kalendar/u/<uid>.ics pro jednu akci (tlačítko „Přidat do kalendáře“; iPhone otevře rovnou
dialog kalendáře) – web zná uid z pole „cal“ u akce a oznámení.

Obsahují akce z kalendářů obcí a oznámení s termínem jednání nebo akce (zasedání, odstávka, uzavírka).
Aplikace kalendáře (Apple, Outlook, Google) si soubor po přihlášení odběru samy obnovují.
UID je stálé (akce: obec|odkaz|datum, oznámení: id), takže aktualizace akci přepíše a nezdvojí.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SITE_URL = "https://twarr58.github.io/dolnobrezansko/"
DOMAIN = "dolnobrezansko"
DURATION = timedelta(hours=2)  # zdroje konec akce většinou neuvádějí
PAST_DAYS = 7  # proběhlé akce ještě týden v kalendáři zůstanou

# pravidla střední Evropy (od 1996), aby časy seděly i kolem změny času
VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Prague
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE""".split("\n")


def _esc(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", ""))


def _fold(line: str) -> str:
    """Řádky delší než 75 bajtů se zalomí (pokračování začíná mezerou); znaky UTF-8 se nedělí."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def _iso(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _time(value) -> tuple[int, int] | None:
    try:
        hh, mm = str(value).split(":")[:2]
        h, m = int(hh), int(mm)
        return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None
    except (ValueError, AttributeError):
        return None


def vevent(uid: str, title: str, start: date, *, end: date | None = None, time: str | None = None,
           place: str | None = None, url: str | None = None, desc: str | None = None, stamp: datetime) -> list[str]:
    """Jedna událost. Bez času (nebo vícedenní) celodenní, jinak od času na DURATION v pražském čase."""
    lines = ["BEGIN:VEVENT", f"UID:{uid}@{DOMAIN}", f"DTSTAMP:{stamp.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"]
    hm = _time(time)
    multi = end is not None and end > start
    if hm and not multi:
        begin = datetime(start.year, start.month, start.day, *hm)
        finish = begin + DURATION
        lines += [f"DTSTART;TZID=Europe/Prague:{begin:%Y%m%dT%H%M%S}", f"DTEND;TZID=Europe/Prague:{finish:%Y%m%dT%H%M%S}"]
    else:
        last = end if multi else start
        lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}",
                  "TRANSP:TRANSPARENT"]  # celodenní akce neblokuje čas v kalendáři
        if hm:
            desc = f"Začátek v {hm[0]}:{hm[1]:02d}.\n{desc or ''}".strip()
    lines.append(f"SUMMARY:{_esc(title)}")
    if place:
        lines.append(f"LOCATION:{_esc(place)}")
    if desc:
        lines.append(f"DESCRIPTION:{_esc(desc)}")
    if url:
        lines.append(f"URL:{url}")
    lines.append("END:VEVENT")
    return lines


def calendar(name: str, events: list[list[str]]) -> str:
    head = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{DOMAIN}//Uredni deska//CS", "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(name)}", "X-WR-TIMEZONE:Europe/Prague",
            "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    lines = head + VTIMEZONE + [x for ev in events for x in ev] + ["END:VCALENDAR"]
    return "".join(_fold(x) + "\r\n" for x in lines)


def ev_name(ev: list[str]) -> str:
    return next((x[8:] for x in ev if x.startswith("SUMMARY:")), "Akce").replace("\\", "")


def _uid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:20]


def entries(data: dict, now: datetime, site_url: str = SITE_URL) -> list[tuple[str, tuple, list[str]]]:
    """(obec, klíč řazení, VEVENT) pro akce obcí a oznámení s termínem akce; zahrnutým doplní „cal“ (uid)."""
    lo = now.date() - timedelta(days=PAST_DAYS)
    out = []
    for e in (data.get("events") or {}).get("items", []):
        start, end = _iso(e.get("d")), _iso(e.get("end"))
        if not start or not e.get("t") or (end or start) < lo:
            continue
        url = e.get("url") if str(e.get("url", "")).startswith("http") else None
        desc = "\n".join(x for x in [f"Akce obce {e['o']}.", url and f"Podrobnosti: {url}",
                                     f"Kalendář akcí: {site_url}#/akce"] if x)
        e["cal"] = _uid("akce", e["o"], e.get("url") or e["t"], e["d"])
        ev = vevent(e["cal"], e["t"], start, end=end, time=e.get("time"), place=e.get("place"), url=url, desc=desc, stamp=now)
        out.append((e["o"], (start, e.get("time") or "", e["t"]), ev))
    for it in data.get("items", []):
        s = it.get("s") or {}
        start, end = _iso(s.get("event")), _iso(s.get("event_end"))
        if it.get("r") or not start or (end or start) < lo:
            continue
        detail = f"{site_url}#/o/{it['id']}"
        desc = "\n".join(x for x in [s.get("tldr"), s.get("action"), f"Oznámení na úřední desce obce {it['o']}: {detail}"] if x)
        title = it.get("t") or s.get("tldr") or "Oznámení"
        it["cal"] = _uid("oznameni", it["id"])
        ev = vevent(it["cal"], f"{title} ({it['o']})", start, end=end, time=s.get("time"),
                    place=s.get("where"), url=detail, desc=desc, stamp=now)
        out.append((it["o"], (start, s.get("time") or "", title), ev))
    out.sort(key=lambda x: x[1])
    return out


def write(data: dict, out_dir: Path, now: datetime, site_url: str = SITE_URL) -> int:
    """Zapíše kalendář celého regionu, každé obce a každé události zvlášť. Volá se před uložením region.json
    (doplní do dat „cal“). Vrátí počet událostí v celém kalendáři."""
    cal_dir = out_dir / "kalendar"
    shutil.rmtree(cal_dir / "u", ignore_errors=True)  # bez starých akcí
    (cal_dir / "u").mkdir(parents=True, exist_ok=True)
    all_entries = entries(data, now, site_url)
    for _, _, ev in all_entries:
        uid = ev[1].split(":", 1)[1].split("@", 1)[0]
        (cal_dir / "u" / f"{uid}.ics").write_text(calendar(ev_name(ev), [ev]), "utf-8", newline="")
    region = (data.get("region") or {}).get("name") or "Dolnobřežansko"
    (cal_dir / "vse.ics").write_text(calendar(f"{region} – akce a termíny", [e for _, _, e in all_entries]),
                                     "utf-8", newline="")
    for m in data.get("members", []):
        if not m.get("slug"):
            continue
        mine = [e for o, _, e in all_entries if o == m["name"]]
        (cal_dir / f"{m['slug']}.ics").write_text(calendar(f"{m['name']} – akce a termíny", mine), "utf-8", newline="")
    return len(all_entries)
