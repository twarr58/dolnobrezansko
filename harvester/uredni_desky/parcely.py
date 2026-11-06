"""Odkazy na parcely do Nahlížení do katastru nemovitostí.

V textech shrnutí (úřední deska i samospráva) se najdou čísla parcel („parc. č. 259/13“, „pozemky p. č. 970/5,
970/7 a 983“, „st. 217“) a k nim katastrální území – z textu („k. ú. Oleško u Zvole“, kód „628808“, „v Dolních
Břežanech“), z jiných údajů téhož oznámení, nebo z obce, když má jediné k. ú. Číslo parcely se ve veřejné službě
ČÚZK (RÚIAN, ArcGIS REST) převede na ID parcely a web z něj udělá odkaz
https://nahlizenidokn.cuzk.gov.cz/ZobrazObjekt.aspx?typ=parcela&id=<ID>.

Výsledek je u oznámení / zasedání pod klíčem „pl“: {„259/13“: [ID, „Dolní Břežany“]}. Nalezená ID se pamatují
ve stavu (parcely.json), dotazů na ČÚZK je za běh nejvýš QUERY_BUDGET.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

RUIAN = "https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/Prohlizeci_sluzba_nad_daty_RUIAN/MapServer"
KN_URL = "https://nahlizenidokn.cuzk.gov.cz/ZobrazObjekt.aspx?typ=parcela&id={id}"
QUERY_BUDGET = 60
KU_REFRESH_DAYS = 30
MISS_RETRY_DAYS = 30

_NUM = re.compile(r"(st\.\s*)?(\d{1,5})(?:\s*/\s*(\d{1,4}))?(?![\d/,]\d)")
_TRIGGER = re.compile(
    r"(?<![\w])(?:parc(?:ela|ely|elách|ele|elu|elní|\.)?|p\.\s*č\.?|pozem(?:ek|ku|ky|cích|ků)|stavební\s+parcel\w*)"
    r"\s*(?:(?:č(?:\.|íslo|ísla|ísel)?|p\.\s*č\.|parc\.\s*č\.|parcelní\s+č(?:\.|íslo)?)\s*)?(?::\s*)?", re.I)
_SEP = re.compile(r"\s*(?:,|;|\+|\ba\b|\bi\b|\ba\s+dalš\w+)\s*(?:(?:parc\.\s*č\.|p\.\s*č\.|č\.)\s*)?", re.I)
_PAREN_KU = re.compile(r"\s*\(\s*(?:k\.\s*ú\.|kat\.\s*ú\.|katastrální\s+území)\s*([^)]{3,60})\)", re.I)
_KU_MARK = re.compile(r"(?:k\.\s*ú\.|kat\.\s*(?:ú\.|území)|katastráln\w*\s+územ\w*)\s*:?\s*", re.I)
_STOP = {"u", "nad", "pod", "v", "na"}


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if not unicodedata.combining(c)).lower()


def _words(s: str) -> list[str]:
    return [w[:4] for w in re.findall(r"[a-z]+", fold(s)) if w not in _STOP]


class KU:
    """Katastrální území regionu: kód, název, obec."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.by_code = {k["kod"]: k for k in items}
        self.names = sorted(items, key=lambda k: -len(k["nazev"]))

    def of_obec(self, obec: str) -> list[dict]:
        return [k for k in self.items if k["obec_nazev"] == obec]

    def explicit(self, text: str) -> list[tuple[int, dict]]:
        """K. ú. jmenovaná v textu: (pozice, k. ú.) – za značkou „k. ú.“ / „katastrální území“, nebo jako kód."""
        out = []
        low = fold(text)
        for m in _KU_MARK.finditer(text):
            pos = m.end()
            while True:  # „Katastrální území: Dolní Břežany, Písnice a Vestec u Prahy“ – všechna jména výčtu
                k = next((k for k in self.names if low[pos:pos + 60].startswith(fold(k["nazev"]))), None)
                if k:
                    out.append((pos, k))
                    pos += len(k["nazev"])
                else:
                    other = re.match(r"[^\W\d][\w ]{1,40}?(?=,|\s+a\s|;|\)|$)", text[pos:pos + 60])
                    if not other:
                        break
                    out.append((pos, None))  # jiné k. ú. (mimo region) – ve výčtu se počítá
                    pos += other.end()
                sep = re.match(r"\s*(?:,|\s+a\s|;)\s*", text[pos:pos + 6])
                if not sep:
                    break
                pos += sep.end()
        for m in re.finditer(r"(?<!\d)(\d{6})(?!\d)", text):
            if int(m.group(1)) in self.by_code:
                out.append((m.start(), self.by_code[int(m.group(1))]))
        return out

    def by_name(self, name: str) -> dict | None:
        f = fold(name).strip()
        return next((k for k in self.names if f.startswith(fold(k["nazev"]))), None)

    def mentioned(self, text: str, candidates: list[dict]) -> list[dict]:
        """K. ú. obce zmíněná místním jménem („v Dolních Břežanech“, „Lhota“): všechna slova názvu v textu."""
        have = set(_words(text))
        hits = [k for k in candidates if _words(k["nazev"]) and set(_words(k["nazev"])) <= have]
        # „Lhota u Dolních Břežan“ obsahuje i slova „Dolní Břežany“ – delší (konkrétnější) shoda vyhrává
        return [k for k in hits if not any(o is not k and set(_words(k["nazev"])) < set(_words(o["nazev"])) for o in hits)]


def find_parcels(text: str) -> list[dict]:
    """Čísla parcel v textu: [{"key": „259/13“ | „st. 217“, "kmen", "podd", "st", "ku_name"?, "pos"}]."""
    out = []
    for t in _TRIGGER.finditer(text or ""):
        pos = t.end()
        while True:
            m = _NUM.match(text, pos)
            if not m or (not m.group(3) and not m.group(1) and len(m.group(2)) > 4):
                break
            after = text[m.end():m.end() + 4]
            if re.match(r"\s*(?:Kč|m2|m²|m |ks|%|\.\d|Sb)", after):  # částka, výměra, zákon – ne parcela
                break
            key = (f"st. {m.group(2)}" if m.group(1) else m.group(2)) + (f"/{m.group(3)}" if m.group(3) else "")
            p = {"key": key, "kmen": int(m.group(2)), "podd": int(m.group(3)) if m.group(3) else None,
                 "st": bool(m.group(1)), "pos": m.start(), "lit": text[m.start():m.end()], "trigger": t.start()}
            pos = m.end()
            pk = _PAREN_KU.match(text, pos)
            if pk:
                p["ku_name"] = pk.group(1)
                pos = pk.end()
            out.append(p)
            sep = _SEP.match(text, pos)
            if not sep or not _NUM.match(text, sep.end()):
                break
            pos = sep.end()
    return out


def _label_before(text: str, trigger: int, kus: KU):
    """K. ú. napsané těsně před výčtem („Dolní Břežany: parc. č.“): dict, None = jiné (mimo region), "" = žádné."""
    m = re.search(r"([^\W\d][\w .-]{1,40}?)\s*:\s*$", text[max(0, trigger - 50):trigger])
    if not m:
        return ""
    name = m.group(1).strip()
    if re.fullmatch(r"(?i)(parcely?|pozemky?|dotčené parcely|parcelní čísla|katastrální území|k\.\s*ú\.?|stavba|místo)", name):
        return ""
    k = kus.by_name(name) or next((k for k in kus.names if fold(name).endswith(fold(k["nazev"]))), None)
    return k if k else None


def assign(texts: list[str], obec: str, kus: KU) -> list[dict]:
    """Parcely všech textů jednoho oznámení s přiřazeným k. ú. (ku = dict) nebo seznamem kandidátů (cands)."""
    named_all = [k for t in texts for _, k in kus.explicit(t)]
    explicit_all = {k["kod"]: k for k in named_all if k} if None not in named_all else {}
    own = kus.of_obec(obec)
    found = []
    for text in texts:
        exp = [(pos, k) for pos, k in kus.explicit(text)]
        for p in find_parcels(text):
            label = _label_before(text, p["trigger"], kus) if p.get("trigger") is not None else ""
            if label is None:  # „Písnice: parc. č. …“ – k. ú. mimo region: bez odkazu
                found.append({**p, "ku": None, "cands": []})
                continue
            ku = kus.by_name(p["ku_name"]) if p.get("ku_name") else (label or None)
            if ku is None and exp:
                ku = min(exp, key=lambda e: abs(e[0] - p["pos"]))[1]
                if ku is None:  # nejbližší jmenované k. ú. je mimo region
                    found.append({**p, "ku": None, "cands": []})
                    continue
            if ku is None and len(explicit_all) == 1:
                ku = next(iter(explicit_all.values()))
            if ku is None:
                named = kus.mentioned(text, own)
                ku = named[0] if len(named) == 1 else None
            if ku is None and len(own) == 1:
                ku = own[0]
            found.append({**p, "ku": ku, "cands": [] if ku else own})
    return found


async def _query(client: httpx.AsyncClient, layer: int, where: str, fields: str) -> list[dict]:
    r = await client.get(f"{RUIAN}/{layer}/query", params={"where": where, "outFields": fields,
                                                          "returnGeometry": "false", "f": "json"}, timeout=40)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise ValueError(f"RÚIAN: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


async def load_ku(client: httpx.AsyncClient, obce: list[str], state: dict, now: datetime) -> KU:
    """Katastrální území obcí regionu (z RÚIAN, pamatují se KU_REFRESH_DAYS dní)."""
    cached = state.get("ku") or {}
    if cached.get("obce") == sorted(obce) and cached.get("checked", "") > (now - timedelta(days=KU_REFRESH_DAYS)).isoformat():
        return KU(cached["items"])
    names = ", ".join("'" + o.replace("'", "''") + "'" for o in obce)
    rows = await _query(client, 12, f"nazev in ({names})", "kod,nazev,okres")
    okres = Counter(r["okres"] for r in rows).most_common(1)[0][0] if rows else None  # stejnojmenné obce jinde pryč
    codes = {r["kod"]: r["nazev"] for r in rows if r["okres"] == okres}
    kus = await _query(client, 7, f"obec in ({','.join(map(str, codes))})", "kod,nazev,obec") if codes else []
    items = [{"kod": k["kod"], "nazev": k["nazev"], "obec": k["obec"], "obec_nazev": codes[k["obec"]]} for k in kus]
    state["ku"] = {"obce": sorted(obce), "checked": now.isoformat(timespec="seconds"), "items": items}
    log.info("Parcely: %d katastrálních území pro %d obcí", len(items), len(codes))
    return KU(items)


def _ckey(ku: int, p: dict) -> str:
    return f"{ku}|{'st' if p['st'] else 'p'}|{p['kmen']}|{p['podd'] or 0}"


async def resolve(client: httpx.AsyncClient, wanted: dict[int, list[dict]], cache: dict, now: datetime,
                  budget: dict) -> None:
    """Doplní do cache ID parcel {k. ú.: [parcely]} – jeden dotaz na k. ú. a skupinu čísel."""
    retry = (now - timedelta(days=MISS_RETRY_DAYS)).isoformat()
    for ku, plist in wanted.items():
        todo = [p for p in plist if _ckey(ku, p) not in cache
                or not cache[_ckey(ku, p)].get("id") and cache[_ckey(ku, p)].get("t", "") < retry]
        kmeny = sorted({p["kmen"] for p in todo})
        for i in range(0, len(kmeny), 40):
            if budget["n"] <= 0:
                return
            budget["n"] -= 1
            chunk = kmeny[i:i + 40]
            try:
                rows = await _query(client, 5, f"katastralniuzemi={ku} and kmenovecislo in ({','.join(map(str, chunk))})",
                                    "id,kmenovecislo,poddelenicisla,druhcislovanikod")
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Parcely: RÚIAN nejde (%r) – odkazy se doplní příště", exc)
                budget["n"] = 0
                return
            found = {(r["druhcislovanikod"] == 1, r["kmenovecislo"], r["poddelenicisla"] or 0): int(r["id"]) for r in rows}
            stamp = now.isoformat(timespec="seconds")
            for p in todo:
                if p["kmen"] in chunk:
                    pid = found.get((p["st"], p["kmen"], p["podd"] or 0))
                    cache[_ckey(ku, p)] = {"id": pid, "t": stamp} if pid else {"t": stamp}


def _texts_item(it: dict) -> list[str]:
    s = it.get("s") or {}
    out = [s.get(k) or "" for k in ("tldr", "action", "impact", "affects", "where", "contact")]
    out += [b for b in s.get("bullets") or [] if isinstance(b, str)]
    out += [f"{f.get('label', '')}: {f.get('value', '')}" for f in s.get("facts") or [] if isinstance(f, dict)]
    out += [x.get("what", "") for x in s.get("dates") or [] if isinstance(x, dict)]
    out.append(it.get("t") or "")
    return [t for t in out if t]


def _texts_meeting(m: dict) -> list[str]:
    return [m.get("tldr") or ""] + [p.get("t") or "" for p in m.get("points") or []]


async def annotate(client: httpx.AsyncClient, data: dict, state_dir: Path, now: datetime,
                   fallback: httpx.AsyncClient | None = None) -> int:
    """Doplní oznámením a zasedáním odkazy na parcely („pl“). Vrátí počet odkazů."""
    path = state_dir / "parcely.json"
    try:
        state = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        state = {}
    cache = state.setdefault("parcels", {})
    obce = [m["name"] for m in data.get("members", [])]
    try:
        kus = await load_ku(client, obce, state, now)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        if fallback is None:
            log.warning("Parcely: katastrální území nejdou načíst (%r)", exc)
            return 0
        try:
            client = fallback
            kus = await load_ku(client, obce, state, now)
        except (httpx.HTTPError, ValueError, KeyError) as exc2:
            log.warning("Parcely: katastrální území nejdou načíst (%r)", exc2)
            return 0
    targets = [(it, it.get("o", ""), _texts_item(it)) for it in data.get("items", [])]
    targets += [(m, m.get("o", ""), _texts_meeting(m)) for m in (data.get("council") or {}).get("meetings", [])]
    plans = [(obj, assign(texts, obec, kus)) for obj, obec, texts in targets]
    wanted: dict[int, list[dict]] = {}
    for _, parcels in plans:
        for p in parcels:
            for k in ([p["ku"]] if p["ku"] else p["cands"]):
                wanted.setdefault(k["kod"], []).append(p)
    budget = {"n": QUERY_BUDGET}
    await resolve(client, wanted, cache, now, budget)
    links = 0
    for obj, parcels in plans:
        pl = {}
        for p in parcels:
            if p["ku"]:
                hit = cache.get(_ckey(p["ku"]["kod"], p), {}).get("id")
                ku = p["ku"]
            else:  # obec s víc k. ú. a z textu nejde poznat které: odkaz jen když parcela existuje v jediném
                hits = [(cache.get(_ckey(k["kod"], p), {}).get("id"), k) for k in p["cands"]]
                hits = [(h, k) for h, k in hits if h]
                hit, ku = hits[0] if len(hits) == 1 else (None, None)
            if hit and p["key"] not in pl:
                pl[p["key"]] = [hit, ku["nazev"]]
        if pl:
            obj["pl"] = pl
            links += len(pl)
        else:
            obj.pop("pl", None)
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    log.info("Parcely: %d odkazů do katastru", links)
    return links
