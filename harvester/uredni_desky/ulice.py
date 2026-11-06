"""Odkazy na ulice do Google Map.

Seznam ulic obcí regionu a bod na každé ulici dává veřejná služba ČÚZK (RÚIAN, ArcGIS REST, vrstva Ulice) –
pamatuje se REFRESH_DAYS dní ve stavu (ulice.json). V textech shrnutí (úřední deska i samospráva) se pak najdou
ulice obce, o které text opravdu mluví: za „ulice / ul. / ulicích / (místní) komunikace“, případně dál ve výčtu
(„v ulicích Vestecká, K Jahodárně, Průběžná a Hrnčířská“). Samotné „Pražská“ nebo „Dolní“ (Dolní Břežany) bez
takového kontextu odkaz nedostane.

Když za ulicí následuje číslo domu („Nádražní u č. p. 137“, „Pražská 12/3“, „ul. Vltavská č. o. 5“), bod je přímo
na adresním místě z RÚIAN (vrstva AdresniMisto); když dům nenajde, zůstane bod na ulici. Nalezené adresy se
pamatují ve stavu, dotazů na adresy je za běh nejvýš ADDR_BUDGET.

Výsledek je u oznámení / zasedání pod klíčem „ul“: {„Malebná“: [49.98587, 14.53001]}, s adresou
{„Nádražní“: [49.939046, 14.382411, „č. p. 137“]}. Web z bodu udělá odkaz
https://www.google.com/maps/search/?api=1&query=<šířka>,<délka> – bod, ne hledání podle jména (Jesenic,
Pražských ulic… je v ČR víc).
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from .parcely import RUIAN, _texts_item, _texts_meeting

log = logging.getLogger(__name__)

REFRESH_DAYS = 30
ADDR_BUDGET = 80        # dotazů na adresní místa za běh
ADDR_RETRY_DAYS = 30    # nenalezenou adresu zkusit znovu po
CACHE_VERSION = 2       # 2: ukládají se i kódy ulic (pro adresní místa)

# číslo domu hned za ulicí: „u č. p. 137“, „č. o. 5“, „137“, „12/3“, „(č. p. 453–457)“ (bere se první)
NUMBER = re.compile(
    r"\s*,?\s*\(?\s*(?:u\s+|na\s+)?(?:(?P<kind>č\.\s*p\.|čp\.?|č\.\s*o\.|čo\.?|č\.\s*ev\.|č\.)\s*)?"
    r"(?P<a>\d{1,4})(?:\s*/\s*(?P<b>\d{1,4})[a-zA-Z]?)?(?![\d,]\d)", re.I)
# za číslem nesmí být jednotka, počet nebo datum („Březovská 20 čísel popisných“, „Dlouhá 7 domů“, „Pražská 20. 11.“)
DATE_AFTER = re.compile(r"\.\s*(?:\d|(?:ledna|února|března|dubna|května|června|července|srpna|září|října|listopadu|prosince)\b)", re.I)
NOT_HOUSE = re.compile(r"\s*(?:čís|dom|adres|objekt|parcel|byt|m\b|m2|m²|km|kč|%|hod|min|let|osob|metr|ks|x\b)", re.I)

# „ulice“ ve všech pádech, „ul.“, „(místní) komunikace“, „třída“
TRIGGER = re.compile(r"(?<!\w)(?:ul\.|ulic(?:e|i|í|ích|emi|ím)\b|komunikac(?:e|i|í|ích)\b|tříd(?:a|ě|u)\b)", re.I)
# mezi spouštěčem / předchozí ulicí a další ulicí ve výčtu smí být jen oddělovače a jiné položky výčtu
# (název s velkým písmenem, nejvýš tři slova, za ním čárka) – ulice, kterou RÚIAN nezná, výčet nepřeruší
_ITEM = r"[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ0-9][\w.]*(?:\s+[\w.]+){0,2}\s*,\s*"
GAP = re.compile(rf"[\s,;:]*(?:{_ITEM})*(?:(?:a|i|nebo|či)\s+)?(?:(?:ul\.|ulic\w*)\s*)?")


def _point(paths: list[list[list[float]]]) -> list[float] | None:
    """Bod přímo na ulici: střed úseku nejbližší průměru všech vrcholů (ne vrchol – ten bývá křižovatkou
    s jinou ulicí). Vrací [šířka, délka]."""
    pts = [p for path in paths for p in path]
    if not pts:
        return None
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for path in paths for a, b in zip(path, path[1:])] or \
           [(p[0], p[1]) for p in pts]
    x, y = min(mids, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    return [round(y, 5), round(x, 5)]


async def _get(client: httpx.AsyncClient, layer: int, params: dict) -> list[dict]:
    r = await client.get(f"{RUIAN}/{layer}/query", params={"f": "json", **params}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise ValueError(f"RÚIAN: {data['error']}")
    return data.get("features", [])


async def load_streets(client: httpx.AsyncClient, obce: list[str], state: dict, now: datetime) -> dict:
    """{obec: {ulice: [šířka, délka]}} z RÚIAN; pamatuje se REFRESH_DAYS dní. Kódy ulic (pro adresní místa)
    jsou ve stavu pod streets.kody."""
    cached = state.get("streets") or {}
    if cached.get("v") == CACHE_VERSION and cached.get("obce") == sorted(obce) and \
            cached.get("checked", "") > (now - timedelta(days=REFRESH_DAYS)).isoformat():
        return cached["items"]
    names = ", ".join("'" + o.replace("'", "''") + "'" for o in obce)
    rows = [f["attributes"] for f in await _get(client, 12, {"where": f"nazev in ({names})", "outFields": "kod,nazev,okres",
                                                               "returnGeometry": "false"})]
    okres = Counter(r["okres"] for r in rows).most_common(1)[0][0] if rows else None  # stejnojmenné obce jinde pryč
    codes = {r["kod"]: r["nazev"] for r in rows if r["okres"] == okres}
    items: dict[str, dict[str, list[float]]] = {name: {} for name in codes.values()}
    kody: dict[str, dict[str, int]] = {name: {} for name in codes.values()}
    if codes:
        # ulice pojmenované jako obec nebo její část („Zdiměřice“) by v textu většinou znamenaly místo, ne ulici
        parts = await _get(client, 11, {"where": f"obec in ({','.join(map(str, codes))})", "outFields": "nazev",
                                        "returnGeometry": "false"})
        places = {f["attributes"]["nazev"].casefold() for f in parts} | {n.casefold() for n in codes.values()}
        feats = await _get(client, 4, {"where": f"obec in ({','.join(map(str, codes))})", "outFields": "kod,nazev,obec",
                                       "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "5",
                                       "maxAllowableOffset": "0.0002"})
        for f in feats:
            a, pt = f["attributes"], _point((f.get("geometry") or {}).get("paths") or [])
            if pt and a.get("nazev") and a.get("obec") in codes and a["nazev"].casefold() not in places:
                items[codes[a["obec"]]][a["nazev"]] = pt
                kody[codes[a["obec"]]][a["nazev"]] = a.get("kod")
    state["streets"] = {"v": CACHE_VERSION, "obce": sorted(obce), "checked": now.isoformat(timespec="seconds"),
                        "items": items, "kody": kody}
    log.info("Ulice: %d ulic v %d obcích", sum(map(len, items.values())), len(codes))
    return items


def _pattern(streets: dict[str, list[float]]) -> re.Pattern | None:
    if not streets:
        return None
    alt = "|".join(re.escape(n) for n in sorted(streets, key=len, reverse=True))
    # za názvem nesmí pokračovat další slovo s velkým písmenem („Dolní“ v „Dolní Břežany“)
    # velikost písmen se nerozlišuje jen u názvu („K lesní školce“ i „K Lesní školce“), ne v kontrole za ním
    return re.compile(rf"(?<!\w)((?i:{alt}))(?!\w)(?!\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]\w)")


def _obec_before(text: str, pos: int, obce: list[str]) -> str | None:
    """Obec jmenovaná těsně před spouštěčem: „Ohrobec (ulice …“, „Vestec: ul. …“, „Psáry, ulice …“."""
    if not obce:
        return None
    alt = "|".join(re.escape(o) for o in sorted(obce, key=len, reverse=True))
    m = re.search(rf"(?<!\w)({alt})\s*[(:,–-]?\s*(?:v\s+|ve\s+|na\s+)?$", text[max(0, pos - 60):pos])
    return m.group(1) if m else None


def house_number(text: str, pos: int) -> dict | None:
    """Číslo domu hned za ulicí (od pozice pos): {"kind": "p" | "o" | None, "a": 137, "b": None, "label": "č. p. 137"}.
    None, když tam číslo není, je evidenční, nebo jde o počet / jednotku / datum."""
    m = NUMBER.match(text, pos)
    if not m:
        return None
    kind_raw = (m.group("kind") or "").replace(" ", "").lower()
    if kind_raw.startswith("č.ev"):
        return None  # evidenční číslo se v RÚIAN snadno splete s popisným téhož čísla
    rest = text[m.end():]
    if NOT_HOUSE.match(rest):
        return None
    a, b = int(m.group("a")), int(m.group("b")) if m.group("b") else None
    kind = "p" if kind_raw in ("č.p.", "čp", "čp.") else "o" if kind_raw in ("č.o.", "čo", "čo.") else None
    if kind is None and b is None and a <= 31 and DATE_AFTER.match(rest):
        return None  # „Pražská 20. 11.“, „Polní 5. listopadu“ – datum, ne číslo domu
    label = f"č. p. {a}" if kind == "p" else f"č. o. {a}" if kind == "o" else f"{a}/{b}" if b else str(a)
    return {"kind": kind, "a": a, "b": b, "label": label}


def scan(texts: list[str], streets: dict[str, dict[str, list[float]]], obec: str) -> dict[str, dict]:
    """Ulice, o kterých texty mluví: za spouštěčem nebo dál ve výčtu za ním. Ulice jsou z obce oznámení,
    nebo z jiné obce regionu, když ji text jmenuje těsně před spouštěčem („Ohrobec (ulice Lipová, …)“).
    Vrací {ulice: {"obec": …, "num": číslo domu nebo None}}; u ulice zmíněné víckrát platí první číslo domu."""
    pats = {o: _pattern(s) for o, s in streets.items()}
    found: dict[str, dict] = {}
    clash: set[str] = set()  # stejný název ve dvou obcích jednoho textu – web by nevěděl, který bod je který
    for text in texts:
        if not text or not TRIGGER.search(text):
            continue
        anchors: dict[str, list[int]] = {}  # obec → konce spouštěčů, které k ní patří
        for t in TRIGGER.finditer(text):
            anchors.setdefault(_obec_before(text, t.start(), list(streets)) or obec, []).append(t.end())
        for o, ends in anchors.items():
            pat, by_fold = pats.get(o), {n.casefold(): n for n in streets.get(o, {})}
            if pat is None:
                continue
            last_end = -1
            for m in pat.finditer(text):
                anchor = max([e for e in ends if e <= m.start()] + [last_end])
                if anchor < 0 or not GAP.fullmatch(text, anchor, m.start()):
                    continue
                name = by_fold[m.group(1).casefold()]
                hit = found.setdefault(name, {"obec": o, "num": None})
                if hit["obec"] != o:
                    clash.add(name)
                elif hit["num"] is None:
                    hit["num"] = house_number(text, m.end())
                last_end = m.end()
    return {n: h for n, h in found.items() if n not in clash}


def find_streets(texts: list[str], streets: dict[str, dict[str, list[float]]], obec: str) -> dict[str, list[float]]:
    """Ulice textu s bodem na ulici (bez adresních míst)."""
    return {n: streets[h["obec"]][n] for n, h in scan(texts, streets, obec).items()}


def _akey(kod: int, num: dict) -> str:
    return f"{kod}|{num['kind'] or '-'}|{num['a']}|{num['b'] or 0}"


async def resolve_addresses(client: httpx.AsyncClient, wanted: dict[str, tuple[int, dict]], cache: dict,
                            now: datetime) -> None:
    """Doplní do cache body adresních míst {klíč: (kód ulice, číslo domu)} – jeden dotaz na adresu."""
    retry = (now - timedelta(days=ADDR_RETRY_DAYS)).isoformat()
    budget = ADDR_BUDGET
    for key, (kod, num) in wanted.items():
        hit = cache.get(key)
        if hit and (hit.get("pt") or hit.get("t", "") >= retry):
            continue
        if budget <= 0:
            return
        budget -= 1
        a, b = num["a"], num["b"]
        cond = (f"cislodomovni={a}" if num["kind"] == "p" else f"cisloorientacni={a}" if num["kind"] == "o"
                else f"cislodomovni={a} and cisloorientacni={b}" if b else f"(cislodomovni={a} or cisloorientacni={a})")
        try:
            feats = await _get(client, 1, {"where": f"ulice={int(kod)} and {cond}", "outFields": "cislodomovni,cisloorientacni",
                                           "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "6"})
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Ulice: adresní místa nejdou (%r) – doplní se příště", exc)
            return
        # číslo bez označení: přednost má orientační (tak se adresa v ulici běžně píše), jinak popisné
        feats.sort(key=lambda f: 0 if num["kind"] is None and f["attributes"].get("cisloorientacni") == a else 1)
        pts = [[round(f["geometry"]["y"], 6), round(f["geometry"]["x"], 6)] for f in feats if f.get("geometry")]
        cache[key] = {"pt": pts[0], "t": now.isoformat(timespec="seconds")} if pts else {"t": now.isoformat(timespec="seconds")}


async def annotate(client: httpx.AsyncClient, data: dict, state_dir: Path, now: datetime,
                   fallback: httpx.AsyncClient | None = None) -> int:
    """Doplní oznámením a zasedáním ulice s bodem na mapě („ul“). Vrátí počet odkazů."""
    path = state_dir / "ulice.json"
    try:
        state = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        state = {}
    obce = [m["name"] for m in data.get("members", [])]
    streets, ruian = None, client
    for c in [client] + ([fallback] if fallback is not None else []):
        try:
            streets, ruian = await load_streets(c, obce, state, now), c
            break
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("Ulice: seznam ulic nejde načíst (%r)", exc)
    if streets is None:
        return 0
    kody = (state.get("streets") or {}).get("kody") or {}
    addr = state.setdefault("addr", {})
    targets = [(it, _texts_item(it)) for it in data.get("items", [])]
    targets += [(m, _texts_meeting(m)) for m in (data.get("council") or {}).get("meetings", [])]
    plans = [(obj, scan(texts, streets, obj.get("o", ""))) for obj, texts in targets]
    wanted = {}
    for _, hits in plans:
        for name, h in hits.items():
            kod = kody.get(h["obec"], {}).get(name)
            if h["num"] and kod:
                wanted[_akey(kod, h["num"])] = (kod, h["num"])
    await resolve_addresses(ruian, wanted, addr, now)
    links = houses = 0
    for obj, hits in plans:
        ul = {}
        for name, h in hits.items():
            pt = list(streets[h["obec"]][name])
            kod = kody.get(h["obec"], {}).get(name)
            house = addr.get(_akey(kod, h["num"]), {}).get("pt") if h["num"] and kod else None
            if house:
                pt = [*house, h["num"]["label"]]
                houses += 1
            ul[name] = pt
        if ul:
            obj["ul"] = ul
            links += len(ul)
        else:
            obj.pop("ul", None)
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    log.info("Ulice: %d odkazů do map (z toho %d na dům)", links, houses)
    return links
