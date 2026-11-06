import asyncio
import json
from datetime import datetime, timezone

import httpx

from uredni_desky import ulice as U

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
ST = {
    "Vestec": {"Vestecká": [1, 1], "K Jahodárně": [2, 2], "Průběžná": [3, 3], "Na Průhoně": [4, 4], "Hrnčířská": [5, 5]},
    "Dolní Břežany": {"Pražská": [6, 6], "Dolní": [7, 7], "Lipová": [8, 8], "K Lesní školce": [9, 9], "U Břehu": [10, 10],
                      "U Klínku": [11, 11]},
    "Ohrobec": {"Lipová": [12, 12], "Jarovská": [13, 13]},
}


def _names(text, obec):
    return sorted(U.find_streets([text], ST, obec))


def test_lists_after_trigger():
    t = "Omezený provoz v ulicích Vestecká, K Jahodárně, Průběžná, Na Průhoně a Hrnčířská."
    assert _names(t, "Vestec") == ["Hrnčířská", "K Jahodárně", "Na Průhoně", "Průběžná", "Vestecká"]
    # ulice, kterou RÚIAN nezná (Záslepská), výčet nepřeruší; velikost písmen v názvu se nerozlišuje
    assert _names("v ulicích Záslepská, U Břehu, K lesní školce a U Klínku", "Dolní Břežany") == [
        "K Lesní školce", "U Břehu", "U Klínku"]
    assert _names("parkoviště prodejny (ulice Pražská, parc. č. 241/191)", "Dolní Břežany") == ["Pražská"]


def test_no_link_without_street_context():
    assert _names("Pražská plynárenská a ulice Dolní Břežany", "Dolní Břežany") == []  # firma; „Dolní“ v názvu obce
    assert _names("Pražská a Vestecká bez spouštěče", "Vestec") == []
    assert _names("ulice je jinde. Pražská", "Dolní Břežany") == []


def test_other_municipality_named_before_trigger():
    t = "Vypnutí v obcích Dolní Břežany (ulice Pražská), Ohrobec (ulice Jarovská, Lipová)."
    assert U.find_streets([t], ST, "Dolní Břežany") == {"Pražská": [6, 6], "Jarovská": [13, 13], "Lipová": [12, 12]}
    assert U.find_streets(["v ulici Lipová"], ST, "Dolní Břežany") == {"Lipová": [8, 8]}
    # stejný název v obou obcích jednoho textu → bez odkazu (web zná ke jménu jen jeden bod)
    t2 = "Dolní Břežany (ulice Lipová, Pražská), Ohrobec (ulice Lipová, Jarovská)"
    assert U.find_streets([t2], ST, "Dolní Břežany") == {"Pražská": [6, 6], "Jarovská": [13, 13]}


def test_point_lies_on_street():
    paths = [[[14.0, 50.0], [14.1, 50.0]], [[14.1, 50.0], [14.2, 50.0]]]
    assert U._point(paths) == [50.0, 14.05]  # [šířka, délka], střed úseku nejblíž středu ulice (ne křižovatka)
    assert U._point([[[14.5, 49.97]]]) == [49.97, 14.5]  # jediný bod
    assert U._point([]) is None


def test_annotate_uses_ruian_and_cache(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path.endswith("/11/query"):  # části obce
            return httpx.Response(200, json={"features": [{"attributes": {"nazev": "Vestec"}}]})
        if req.url.path.endswith("/12/query"):
            return httpx.Response(200, json={"features": [
                {"attributes": {"kod": 513458, "nazev": "Vestec", "okres": 3210}},
                {"attributes": {"kod": 537942, "nazev": "Vestec", "okres": 3208}}]})  # stejnojmenná obec jinde
        assert req.url.path.endswith("/4/query") and "537942" not in req.url.params["where"]
        return httpx.Response(200, json={"features": [
            {"attributes": {"nazev": "Vestecká", "obec": 513458}, "geometry": {"paths": [[[14.51, 49.98], [14.512, 49.984]]]}},
            {"attributes": {"nazev": "Průběžná", "obec": 513458}, "geometry": {"paths": [[[14.5, 49.97]]]}},
            {"attributes": {"nazev": "Vestec", "obec": 513458}, "geometry": {"paths": [[[14.4, 49.9]]]}}]})  # = část obce

    data = {"members": [{"name": "Vestec"}],
            "items": [{"o": "Vestec", "s": {"tldr": "Uzavírka v ulicích Vestecká a Průběžná."}},
                      {"o": "Vestec", "s": {"tldr": "Bez ulic."}, "ul": {"stará": [0, 0]}}],
            "council": {"meetings": [{"o": "Vestec", "points": [{"t": "Oprava komunikace Průběžná."}]}]}}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await U.annotate(c, data, tmp_path, NOW)

    assert asyncio.run(go()) == 3
    assert data["items"][0]["ul"] == {"Vestecká": [49.982, 14.511], "Průběžná": [49.97, 14.5]}
    assert "ul" not in data["items"][1]  # stará hodnota pryč
    assert data["council"]["meetings"][0]["ul"] == {"Průběžná": [49.97, 14.5]}
    saved = json.loads((tmp_path / "ulice.json").read_text("utf-8"))
    assert saved["streets"]["items"]["Vestec"]["Vestecká"] == [49.982, 14.511]
    assert "Vestec" not in saved["streets"]["items"]["Vestec"]  # ulice pojmenovaná jako obec vynechána

    calls.clear()  # další běh: ulice z mezipaměti
    asyncio.run(go())
    assert calls == []
