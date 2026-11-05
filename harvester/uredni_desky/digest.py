"""Jednoduchý popis oznámení pravidly (bez AI): téma, co se děje, co můžu udělat a do kdy, kde.

Cíl je rychlé čtení: jedna věta nahoře, lhůta zvlášť, podrobnosti až v detailu.
Pravidla běží vždy; volitelné shrnutí od Claude (ai.py) je přepíše tam, kde je lepší.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

# (id, štítek, vzory v textu bez diakritiky). Pořadí rozhoduje: první shoda v názvu, jinak v textu příloh.
CATEGORIES: list[tuple[str, str, str]] = [
    ("zastupitelstvo", "Zastupitelstvo", r"zasedani zastupitelstva|zastupitelstvo obce|zastupitelstva mesta|verejne zasedani"),
    ("uzemni", "Územní plán", r"uzemni(ho|m)? plan|uzemni studi|regulacni plan|zmen[ay] c\. ?\d+ uzemni"),
    ("doprava", "Doprava a uzavírky", r"uzavirk|objizd|omezeni provozu|dopravni opatreni|prechodn\w* uprav\w* provozu|"
                                     r"mistni uprav\w* provozu|uzavreni (silnice|komunikace)"),
    ("drazby", "Dražby a exekuce", r"drazb|drazebn|exekuc|insolvenc"),
    ("zakazky", "Veřejné zakázky", r"verejn\w* zakazk|zakazk\w* mal\w* rozsah|vyzv\w* k podani nabid|zadavaci (rizeni|dokumentac)|"
                                  r"poptavkov\w* rizeni|vyberov\w* rizeni na (dodavatel|zhotovitel)"),
    ("zamestnani", "Zaměstnání", r"vyberov\w* rizeni na (obsazeni|pozic|pracovni|misto|funkci|referent|vedouci|reditel)|"
                                r"voln\w* (pracovni )?mist|hleda\w* (noveho |novou )?(referent|pracovni|kolegu|kolegyni|zamestnan|"
                                r"ucetn|technik|pracovnik|uklizec|ucitel)|nabidka zamestnani|pracovni pozic"),
    ("stavba", "Stavba", r"stavebni\w* (rizeni|povoleni|zamer)|povoleni (stavby|zameru)|umisten\w* stavby|rodinn\w* dum|"
                        r"rodinneho domu|kolaudac|spolecne (uzemni a stavebni )?povoleni|dodatecn\w* povoleni|"
                        r"odstraneni stavby|zmen[ay] stavby|stavba |stavby "),
    ("majetek", "Pozemky a majetek", r"zamer\w* (obce )?(prodat|prodej|pronaj|pronajmout|propacht|pacht|vypujc|smen|darov)|"
                                   r"pronajem|pacht|prodej pozemk|vypujcka|bezuplatn\w* prevod"),
    ("smlouvy", "Smlouvy a dotace", r"smlouv\w* o poskytnuti (dotace|dotaci|daru|prispevku)|verejnopravni smlouv|darovaci smlouv|"
                                   r"poskytnuti (dotace|dotaci)|dotac\w* z rozpoctu|zverejneni smlouv"),
    ("rozpocet", "Rozpočet", r"rozpoct|rozpocet|zaverecn\w* ucet|rozpoctov\w* opatreni|strednedob\w* vyhled"),
    ("vyhlasky", "Vyhlášky obce", r"obecne zavazn\w* vyhlas|nariz\w* obce|\bozv\b"),
    ("volby", "Volby", r"\bvolb|volic|okrskov\w* volebn"),
    ("voda", "Voda a kanalizace", r"vodovod|kanaliz|vodopravni|vodni\w* zakon|odber\w* (podzemnich |povrchovych )?vod|"
                                 r"cistirn|\bcov\b|vodni dil"),
    ("priroda", "Životní prostředí a EIA", r"kaceni|dreviny|\beia\b|vliv\w* na zivotni prostredi|posuzovani vlivu|odpad|"
                                          r"ochran\w* prirody|myslivost|honitb|lesni"),
    ("energie", "Elektřina a plyn", r"preruseni dodavky|preruseni distribuce|odstavk\w* (elektr|plyn)|"
                                  r"distribuce elektriny|dodavk\w* (elektriny|plynu)"),
    ("dane", "Daně a poplatky", r"dan[eiy]? z nemovit|mistni\w* poplat|financni urad|hromadn\w* predpisn"),
    ("pisemnost", "Písemnost k vyzvednutí", r"ulozen\w* pisemnost|oznameni o ulozeni|k vyzvednuti|"
                                          r"neznam\w* pobyt|nezastizen"),
]
# Kategorie, kde má přednost určení pravidly (spolehlivé podle názvu; starší shrnutí od Claude je neznala).
RULE_PREFERRED = {"drazby", "zakazky", "zamestnani", "smlouvy"}
LABELS = {cid: label for cid, label, _ in CATEGORIES} | {"ostatni": "Ostatní"}
_CAT_RE = [(cid, re.compile(rx)) for cid, _, rx in CATEGORIES]

_MONTHS = {"ledna": 1, "unora": 2, "brezna": 3, "dubna": 4, "kvetna": 5, "cervna": 6, "cervence": 7, "srpna": 8,
           "zari": 9, "rijna": 10, "listopadu": 11, "prosince": 12}
_DATE = re.compile(r"(?<!\d)(\d{1,2})\.\s*(?:(\d{1,2})\.|(" + "|".join(_MONTHS) + r"))\s*(\d{4})(?!\d)")
_TIME = re.compile(r"(?<![\d.])([01]?\d|2[0-3])[:.]([0-5]\d)\s*(?:hod|h\b)|(?:\bv|\bod|\bve)\s+([01]?\d|2[0-3])[:.]([0-5]\d)\b")
_ACTION_WORDS = {
    "namit": "Námitky můžeš podat do {d}.",
    "pripomin": "Připomínky můžeš podat do {d}.",
    "nabid": "Nabídky a žádosti se podávají do {d}.",
    "zadost": "Žádosti se podávají do {d}.",
    "vyjadr": "Vyjádřit se můžeš do {d}.",
    "stanovis": "Stanoviska se podávají do {d}.",
    "prihlas": "Přihlásit se můžeš do {d}.",
}
_DEADLINE = re.compile(r"(namit|pripomin|nabid|zadost|vyjadr|stanovis|prihlas|uplatn|podat|dorucit)[^;]{0,220}?"
                       r"\bdo\s+(?:dne\s+)?(\d{1,2}\.\s*(?:\d{1,2}\.|[a-z]+)\s*\d{4})")
_RELATIVE = re.compile(r"(namit|pripomin|vyjadr|stanovis|uplatn)[^;]{0,200}?(?:do|ve lhute|lhute)\s+(\d{1,3})\s*dn")
_RANGE = re.compile(r"\bod\s+(\d{1,2}\.\s*\d{1,2}\.\s*(?:\d{4})?)\s*(?:\(?[a-z ]{0,10}\)?\s*)?(?:do|-|–)\s*"
                    r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})")
_KU = re.compile(r"k\.\s*ú\.\s*([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][^\n,;()]{2,40}?)(?=\s*(?:[,;()\n]|\.(?:\s|$)|parc|p\.\s*č|$))")
_PARC = re.compile(r"parc(?:el[aey]?|\.)?\s*(?:č\.|čísl[oa]|č)?\s*(?:st\.\s*)?(\d{1,5}(?:/\d{1,4})?"
                   r"(?:\s*(?:,|a)\s*(?:st\.\s*)?\d{1,5}(?:/\d{1,4})?){0,5})", re.I)
_MONEY = re.compile(r"(\d{1,3}(?:[ . ]\d{3})+|\d{2,})(?:,\d{1,2}|,-)?\s*Kč")
_TITLE_NOISE = [
    re.compile(r"^\s*(veřejná vyhláška|vyhláška|oznámení|informace|upozornění|výzva)\s*[-–:,]\s*", re.I),
    re.compile(r"\s*[-–(,]?\s*(č\.\s*j\.|čj\.?|č\.j\.|sp\.\s*zn\.)\s*[:\s]*[\w/.\-–]+\)?", re.I),
    re.compile(r"\s*\(\s*\d+\s*\)\s*$"),
]


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def parse_date(raw: str) -> date | None:
    m = _DATE.search(fold(raw))
    if not m:
        return None
    d, mm, mname, y = m.groups()
    month = int(mm) if mm else _MONTHS.get(mname or "", 0)
    try:
        return date(int(y), month, int(d))
    except ValueError:
        return None


def category(title: str, text: str) -> str:
    for hay in (fold(title), fold(text[:6000])):
        for cid, rx in _CAT_RE:
            if rx.search(hay):
                return cid
    return "ostatni"


def clean_title(title: str, limit: int = 160) -> str:
    t = " ".join((title or "").split())
    for rx in _TITLE_NOISE:
        t = rx.sub("", t).strip(" -–:,")
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        t = t.lower()
    t = t[:1].upper() + t[1:]
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:-–") + "…"
    return t or (title or "").strip()


def _fmt(d: date) -> str:
    return f"{d.day}. {d.month}. {d.year}"


def find_deadline(text: str, posted: date | None) -> tuple[date | None, str | None, bool]:
    """(lhůta, klíčové slovo, přibližná) – nejbližší budoucí lhůta pro reakci veřejnosti."""
    folded = fold(text)
    found = []
    for m in _DEADLINE.finditer(folded):
        d = parse_date(m.group(2))
        if d and (not posted or d >= posted):
            span = m.group(0)
            word = next((k for k in _ACTION_WORDS if k in span), m.group(1))
            found.append((d, word, False))
    if found:
        return min(found)
    if posted and (m := _RELATIVE.search(folded)):
        days = int(m.group(2))
        if 3 <= days <= 60:
            # u veřejné vyhlášky se písemnost považuje za doručenou 15. dnem po vyvěšení
            base = posted + timedelta(days=15 if "verejnou vyhlaskou" in folded else 0)
            word = next((k for k in _ACTION_WORDS if k in m.group(0)), m.group(1))
            return base + timedelta(days=days), word, True
    return None, None, False


def find_event(text: str, posted: date | None) -> tuple[date | None, str | None]:
    """První datum (ne dříve než vyvěšení) a čas – termín zasedání apod."""
    folded = fold(text)
    for m in _DATE.finditer(folded):
        d = parse_date(m.group(0))
        if d and (not posted or d >= posted):
            near = folded[m.end(): m.end() + 60]
            t = _TIME.search(near)
            when = None
            if t:
                h, mi = (t.group(1), t.group(2)) if t.group(1) else (t.group(3), t.group(4))
                when = f"{int(h)}:{mi}"
            return d, when
    return None, None


_NAME_LINKS = {"nad", "pod", "u", "v", "ve", "na", "při", "n.", "p."}


def find_where(text: str) -> str | None:
    parts = []
    if m := _KU.search(text):
        words = []
        for w in m.group(1).split():  # název k. ú.: slova s velkým písmenem, spojky „nad“, „u“…
            if w[:1].isupper() or w in _NAME_LINKS:
                words.append(w)
            else:
                break
        while words and words[-1] in _NAME_LINKS:
            words.pop()
        if words:
            parts.append(f"k. ú. {' '.join(words)}")
    parcels: list[str] = []
    for m in _PARC.finditer(text):
        for p in re.split(r"\s*(?:,|\ba\b)\s*", m.group(1)):
            p = p.replace("st.", "").strip()
            if p and p not in parcels:
                parcels.append(p)
        if len(parcels) >= 4:
            break
    if parcels:
        more = " a další" if len(parcels) > 4 else ""
        parts.append("parc. č. " + ", ".join(parcels[:4]) + more)
    return " · ".join(parts) or None


def find_money(text: str) -> str | None:
    m = _MONEY.search(text)
    if not m:
        return None
    return re.sub(r"[ . ]", " ", m.group(1)) + " Kč"


def describe(title: str, posted: str | None, until: str | None, docs: list[dict], agendas: list[str] | None = None,
             body: str = "") -> dict:
    """Popis oznámení. docs: [{"kind", "text", "pages", "ocr"}] z documents.analyze()."""
    texts = "\n".join(d.get("text") or "" for d in docs)
    full = f"{title}\n{body}\n{texts}"
    posted_d = date.fromisoformat(posted) if posted else None
    cat = category(f"{title} {' '.join(agendas or [])}", f"{body}\n{texts}")
    s: dict = {"cat": cat, "tldr": clean_title(title), "bullets": [], "src": "pravidla"}

    deadline, word, approx = find_deadline(full, posted_d)
    if deadline:
        s["deadline"] = deadline.isoformat()
        template = next((v for k, v in _ACTION_WORDS.items() if word and word.startswith(k)), "Lhůta je do {d}.")
        s["action"] = template.format(d=("přibližně " if approx else "") + _fmt(deadline))

    if cat == "zastupitelstvo":
        ev, when = find_event(f"{title}\n{texts[:3000]}", posted_d)
        if ev:
            s["event"] = ev.isoformat()
            if when:
                s["time"] = when
            s.setdefault("action", f"Zasedání je {_fmt(ev)}{' v ' + when if when else ''}. Veřejnost může přijít.")
    elif cat == "doprava":
        if m := _RANGE.search(fold(full)):
            end = parse_date(m.group(2))
            start = parse_date(m.group(1) if re.search(r"\d{4}", m.group(1)) else f"{m.group(1)} {end.year if end else ''}")
            if start and end:
                s["event"], s["event_end"] = start.isoformat(), end.isoformat()
                s.setdefault("action", f"Platí od {_fmt(start)} do {_fmt(end)}.")
    elif cat == "energie":
        ev, when = find_event(full, posted_d)
        if ev:
            s["event"] = ev.isoformat()
            if when:
                s["time"] = when
            s.setdefault("action", f"Bez dodávky {_fmt(ev)}{' od ' + when if when else ''}.")
    elif cat == "pisemnost":
        s.setdefault("action", "Úřad má pro někoho dopis. Pokud čekáš poštu z úřadu, podívej se, jestli není pro tebe.")

    if where := find_where(full):
        s["where"] = where
    if money := find_money(full):
        s["bullets"].append(f"Částka: {money}")
    kinds = [d.get("kind") for d in docs]
    if "mapa" in kinds:
        s["bullets"].append("V příloze je mapa nebo výkres.")
    if any(d.get("kind") == "sken" and not d.get("ocr") for d in docs):
        s["bullets"].append("Část příloh je sken, text z něj nešel přečíst.")
    if until:
        s["bullets"].append(f"Visí na desce do {_fmt(date.fromisoformat(until))}.")
    if not s.get("action"):
        s["action"] = ""
    return s
