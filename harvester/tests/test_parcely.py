import asyncio
import json
from datetime import datetime, timezone

import httpx

from uredni_desky import parcely as P

NOW = datetime(2026, 11, 6, 9, tzinfo=timezone.utc)
KUS = P.KU([
    {"kod": 628794, "nazev": "Dolní Břežany", "obec": 539210, "obec_nazev": "Dolní Břežany"},
    {"kod": 628808, "nazev": "Lhota u Dolních Břežan", "obec": 539210, "obec_nazev": "Dolní Břežany"},
    {"kod": 781029, "nazev": "Vestec u Prahy", "obec": 513458, "obec_nazev": "Vestec"},
    {"kod": 794040, "nazev": "Oleško u Zvole", "obec": 599735, "obec_nazev": "Březová-Oleško"},
    {"kod": 794023, "nazev": "Březová u Zvole", "obec": 599735, "obec_nazev": "Březová-Oleško"},
])


def _keys(text, obec):
    return [(p["key"], p["ku"] and p["ku"]["nazev"], [k["nazev"] for k in p["cands"]]) for p in P.assign([text], obec, KUS)]


def test_find_parcels_lists_and_not_amounts():
    t = "kat. ú. Oleško u Zvole, pozemky parc. č. 215/1, 207/5, 223 a st. 217; ČOV na pozemku parc. č. 67/2"
    assert [p["key"] for p in P.find_parcels(t)] == ["215/1", "207/5", "223", "st. 217", "67/2"]
    assert P.find_parcels("pozemek o výměře 1 153 m2; zákona č. 128/2000 Sb.; kancelář č. 1.04") == []
    assert [p["key"] for p in P.find_parcels("pozemky p. č. 970/5, 970/7 a 983 v k.ú. Vestec u Prahy")] == ["970/5", "970/7", "983"]


def test_assign_cadastral_area_from_text():
    assert _keys("parc. č. 58/18 (k. ú. Oleško u Zvole), 267/2 (k. ú. Březová u Zvole)", "Březová-Oleško") == [
        ("58/18", "Oleško u Zvole", []), ("267/2", "Březová u Zvole", [])]
    assert _keys("Dotčená parcela: kat. území Lhota u Dolních Břežan (kód 628808), parcelní č. 61/9.", "Dolní Břežany") == [
        ("61/9", "Lhota u Dolních Břežan", [])]
    assert _keys("pozemky parc. č. 259/13 a 259/10 v Dolních Břežanech", "Dolní Břežany") == [
        ("259/13", "Dolní Břežany", []), ("259/10", "Dolní Břežany", [])]
    # obec s víc k. ú. a z textu nejde poznat které → kandidáti (odkaz jen když parcela existuje v jediném)
    assert _keys("Parcela: 61/9", "Dolní Břežany") == [("61/9", None, ["Dolní Břežany", "Lhota u Dolních Břežan"])]
    # obec s jediným k. ú.
    assert _keys("na pozemku parc. č. 241/230", "Vestec") == [("241/230", "Vestec u Prahy", [])]


def test_labels_and_lists_with_foreign_cadastral_area():
    t = "Parcely: Dolní Břežany: parc. č. 241/230; Písnice: parc. č. 1015/36 až 1016/42; Vestec u Prahy: parc. č. 197/9"
    facts = "Katastrální území: Dolní Břežany, Písnice, Vestec u Prahy"
    got = [(p["key"], p["ku"] and p["ku"]["nazev"]) for p in P.assign([facts, t], "Vestec", KUS)]
    assert got == [("241/230", "Dolní Břežany"), ("1015/36", None), ("197/9", "Vestec u Prahy")]


def test_annotate_links_and_caches(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        where = req.url.params["where"]
        if req.url.path.endswith("/12/query"):
            return httpx.Response(200, json={"features": [
                {"attributes": {"kod": 539210, "nazev": "Dolní Břežany", "okres": 3210}},
                {"attributes": {"kod": 513458, "nazev": "Vestec", "okres": 3210}},
                {"attributes": {"kod": 537942, "nazev": "Vestec", "okres": 3208}}]})
        if req.url.path.endswith("/7/query"):
            assert "537942" not in where  # stejnojmenná obec z jiného okresu pryč
            return httpx.Response(200, json={"features": [
                {"attributes": {"kod": 628794, "nazev": "Dolní Břežany", "obec": 539210}},
                {"attributes": {"kod": 628808, "nazev": "Lhota u Dolních Břežan", "obec": 539210}},
                {"attributes": {"kod": 781029, "nazev": "Vestec u Prahy", "obec": 513458}}]})
        assert req.url.path.endswith("/5/query")
        rows = {"katastralniuzemi=628794": [{"id": 1273569210.0, "kmenovecislo": 259, "poddelenicisla": 13, "druhcislovanikod": 2}],
                "katastralniuzemi=628808": [{"id": 1337400210.0, "kmenovecislo": 61, "poddelenicisla": 9, "druhcislovanikod": 2}],
                "katastralniuzemi=781029": []}
        return httpx.Response(200, json={"features": [{"attributes": a} for a in rows[where.split(" ")[0]]]})

    data = {"members": [{"name": "Dolní Břežany"}, {"name": "Vestec"}],
            "items": [{"id": "a", "o": "Dolní Břežany", "t": "Oznámení",
                       "s": {"tldr": "Smlouva na pozemky parc. č. 259/13 v Dolních Břežanech.", "facts": [{"label": "Parcela", "value": "61/9"}]}},
                      {"id": "b", "o": "Vestec", "t": "Nic", "s": {"tldr": "Pozemek parc. č. 5/5 v k. ú. Vestec u Prahy."}}],
            "council": {"meetings": [{"id": "m", "o": "Dolní Břežany", "points": [{"t": "Prodej pozemku parc. č. 259/13."}]}]}}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await P.annotate(c, data, tmp_path, NOW)

    assert asyncio.run(go()) == 3
    assert data["items"][0]["pl"] == {"259/13": [1273569210, "Dolní Břežany"], "61/9": [1337400210, "Lhota u Dolních Břežan"]}
    assert "pl" not in data["items"][1]  # parcela v katastru není → bez odkazu
    assert data["council"]["meetings"][0]["pl"] == {"259/13": [1273569210, "Dolní Břežany"]}
    saved = json.loads((tmp_path / "parcely.json").read_text("utf-8"))
    assert saved["parcels"]["628794|p|259|13"]["id"] == 1273569210

    calls.clear()  # další běh: k. ú. i parcely z mezipaměti
    asyncio.run(go())
    assert calls == []
