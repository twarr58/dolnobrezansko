"""Odkazy na ulice do Google Map.

Seznam ulic obcí regionu a bod na každé ulici dává veřejná služba ČÚZK (RÚIAN, ArcGIS REST, vrstva Ulice) –
pamatuje se REFRESH_DAYS dní ve stavu (ulice.json). V textech shrnutí (úřední deska i samospráva) se pak najdou
ulice obce, o které text opravdu mluví: za „ulice / ul. / ulicích / (místní) komunikace“, případně dál ve výčtu
(„v ulicích Vestecká, K Jahodárně, Průběžná a Hrnčířská“). Samotné „Pražská“ nebo „Dolní“ (Dolní Břežany) bez
takového kontextu odkaz nedostane.

Výsledek je u oznámení / zasedání pod klíčem „ul“: {„Malebná“: [49.98587, 14.53001]}. Web z bodu udělá odkaz
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
    """{obec: {ulice: [šířka, délka]}} z RÚIAN; pamatuje se REFRESH_DAYS dní."""
    cached = state.get("streets") or {}
    if cached.get("obce") == sorted(obce) and cached.get("checked", "") > (now - timedelta(days=REFRESH_DAYS)).isoformat():
        return cached["items"]
    names = ", ".join("'" + o.replace("'", "''") + "'" for o in obce)
    rows = [f["attributes"] for f in await _get(client, 12, {"where": f"nazev in ({names})", "outFields": "kod,nazev,okres",
                                                               "returnGeometry": "false"})]
    okres = Counter(r["okres"] for r in rows).most_common(1)[0][0] if rows else None  # stejnojmenné obce jinde pryč
    codes = {r["kod"]: r["nazev"] for r in rows if r["okres"] == okres}
    items: dict[str, dict[str, list[float]]] = {name: {} for name in codes.values()}
    if codes:
        # ulice pojmenované jako obec nebo její část („Zdiměřice“) by v textu většinou znamenaly místo, ne ulici
        parts = await _get(client, 11, {"where": f"obec in ({','.join(map(str, codes))})", "outFields": "nazev",
                                        "returnGeometry": "false"})
        places = {f["attributes"]["nazev"].casefold() for f in parts} | {n.casefold() for n in codes.values()}
        feats = await _get(client, 4, {"where": f"obec in ({','.join(map(str, codes))})", "outFields": "nazev,obec",
                                       "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "5",
                                       "maxAllowableOffset": "0.0002"})
        for f in feats:
            a, pt = f["attributes"], _point((f.get("geometry") or {}).get("paths") or [])
            if pt and a.get("nazev") and a.get("obec") in codes and a["nazev"].casefold() not in places:
                items[codes[a["obec"]]][a["nazev"]] = pt
    state["streets"] = {"obce": sorted(obce), "checked": now.isoformat(timespec="seconds"), "items": items}
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


def find_streets(texts: list[str], streets: dict[str, dict[str, list[float]]], obec: str) -> dict[str, list[float]]:
    """Ulice, o kterých texty mluví: za spouštěčem nebo dál ve výčtu za ním. Ulice jsou z obce oznámení,
    nebo z jiné obce regionu, když ji text jmenuje těsně před spouštěčem („Ohrobec (ulice Lipová, …)“)."""
    pats = {o: _pattern(s) for o, s in streets.items()}
    found: dict[str, list[float]] = {}
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
                if found.setdefault(name, streets[o][name]) != streets[o][name]:
                    clash.add(name)
                last_end = m.end()
    return {n: pt for n, pt in found.items() if n not in clash}


async def annotate(client: httpx.AsyncClient, data: dict, state_dir: Path, now: datetime,
                   fallback: httpx.AsyncClient | None = None) -> int:
    """Doplní oznámením a zasedáním ulice s bodem na mapě („ul“). Vrátí počet odkazů."""
    path = state_dir / "ulice.json"
    try:
        state = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        state = {}
    obce = [m["name"] for m in data.get("members", [])]
    streets = None
    for c in [client] + ([fallback] if fallback is not None else []):
        try:
            streets = await load_streets(c, obce, state, now)
            break
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("Ulice: seznam ulic nejde načíst (%r)", exc)
    if streets is None:
        return 0
    links = 0
    targets = [(it, _texts_item(it)) for it in data.get("items", [])]
    targets += [(m, _texts_meeting(m)) for m in (data.get("council") or {}).get("meetings", [])]
    for obj, texts in targets:
        ul = find_streets(texts, streets, obj.get("o", ""))
        if ul:
            obj["ul"] = ul
            links += len(ul)
        else:
            obj.pop("ul", None)
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    log.info("Ulice: %d odkazů do map", links)
    return links
