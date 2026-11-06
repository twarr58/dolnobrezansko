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


def _num(text):
    i = text.index("|")
    n = U.house_number(text.replace("|", ""), i)
    return n and (n["kind"], n["a"], n["b"], n["label"])


def test_house_number_after_street():
    assert _num("v ulici Nádražní| u č.p. 137 v k.ú.") == ("p", 137, None, "č. p. 137")
    assert _num("ulice 5. Května| (č. p. 32, 33)") == ("p", 32, None, "č. p. 32")
    assert _num("ul. Vltavská| č. o. 5") == ("o", 5, None, "č. o. 5")
    assert _num("ulice Pražská| 12/3, Jesenice") == (None, 12, 3, "12/3")
    assert _num("ulice Pražská| 40, Jesenice") == (None, 40, None, "40")
    assert _num("v ulici Polní| 999.") == (None, 999, None, "999")  # konec věty, ne datum
    # počet, jednotka, datum, evidenční číslo, nic – bez čísla domu
    for t in ["v ulicích Březovská| 20 čísel popisných", "ulice Dlouhá| 7 domů", "ulice Pražská| 20. 11. 2026", "ulice Polní| 5. listopadu",
              "ulice Polní| 300 m od křižovatky", "ulice Polní| č. ev. 587", "ulice Polní| a Luční"]:
        assert _num(t) is None, t


def test_address_point_and_fallback(tmp_path):
    queries = []

    def handler(req: httpx.Request) -> httpx.Response:
        path, where = req.url.path, req.url.params.get("where", "")
        if path.endswith("/11/query"):
            return httpx.Response(200, json={"features": []})
        if path.endswith("/12/query"):
            return httpx.Response(200, json={"features": [{"attributes": {"kod": 539848, "nazev": "Vrané nad Vltavou", "okres": 3210}}]})
        if path.endswith("/4/query"):
            return httpx.Response(200, json={"features": [
                {"attributes": {"kod": 441503, "nazev": "Nádražní", "obec": 539848}, "geometry": {"paths": [[[14.38, 49.94]]]}},
                {"attributes": {"kod": 441504, "nazev": "Polní", "obec": 539848}, "geometry": {"paths": [[[14.37, 49.93]]]}}]})
        assert path.endswith("/1/query")
        queries.append(where)
        if where == "ulice=441503 and cislodomovni=137":
            return httpx.Response(200, json={"features": [{"attributes": {"cislodomovni": 137}, "geometry": {"x": 14.382411, "y": 49.939046}}]})
        return httpx.Response(200, json={"features": []})  # Polní 999 neexistuje

    data = {"members": [{"name": "Vrané nad Vltavou"}],
            "items": [{"o": "Vrané nad Vltavou", "s": {"tldr": "Vrak v ulici Nádražní u č.p. 137, další v ulici Polní 999."}}]}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await U.annotate(c, data, tmp_path, NOW)

    assert asyncio.run(go()) == 2
    assert data["items"][0]["ul"] == {"Nádražní": [49.939046, 14.382411, "č. p. 137"],  # bod na domě
                                      "Polní": [49.93, 14.37]}  # dům nenalezen → bod na ulici
    assert sorted(queries) == ["ulice=441503 and cislodomovni=137",
                               "ulice=441504 and (cislodomovni=999 or cisloorientacni=999)"]
    queries.clear()  # další běh: adresy z mezipaměti (i nenalezená, do ADDR_RETRY_DAYS)
    asyncio.run(go())
    assert queries == []


def test_location_texts_crossings_and_unique_street_elsewhere():
    st = {"Březová-Oleško": {"Zvolská": [1, 1], "Pražská": [2, 2], "Hlavní": [3, 3]},
          "Zvole": {"Černická": [4, 4], "Pražská": [5, 5]}}
    where = "Zvolská x Pražská (u zahrádkářů); Pražská, restaurace U Vlastence; Stará Březová – Hlavní, náves"
    assert U.find_streets([where], st, "Březová-Oleško", [where]) == {"Zvolská": [1, 1], "Pražská": [2, 2], "Hlavní": [3, 3]}
    kde = "Obřadní síň Obecního úřadu Dolní Břežany, Pražská 78, 252 41 Dolní Břežany"
    assert U.find_streets([kde], {"Dolní Břežany": {"Pražská": [9, 9]}}, "Dolní Břežany", [kde]) == {"Pražská": [9, 9]}
    # křižovatka „A x B“ i mimo údaje o místě; „hlavní silnice“ (malé písmeno) není ulice Hlavní
    assert U.find_streets(["Svoz: Zvolská x Pražská v 10:10, pak hlavní silnice"], st, "Březová-Oleško") == {
        "Zvolská": [1, 1], "Pražská": [2, 2]}
    # ulice jen v jiné obci regionu (Černická ve Zvoli), jediná v regionu → bod ze Zvole; Pražská ne (je i v obci oznámení)
    assert U.find_streets(["KC Jurta na konci ul. Černická v Nové Zvoli"], st, "Březová-Oleško") == {"Černická": [4, 4]}


def test_roads_found_and_nearest_section():
    assert U.find_roads(["Uzavírka II/101, objížďka po III/0031 a III/10115; ne 2/2026"]) == {
        "II/101": "II/101", "III/0031": "III/31", "III/10115": "III/10115"}
    assert U.nearest([[50.0, 14.0], [49.9, 14.4], [49.95, 14.45]], [49.94, 14.43]) == [49.95, 14.45]


def test_street_list_continues_after_house_numbers():
    st = {"Dolní Břežany": {"Májová": [1, 1], "Větrná": [2, 2]}}
    for text in ["Část obce Lhota: ulice Májová (č. p. 131) a Větrná (č. p. 93, 122); kat. území Lhota",
                 "Dotčené adresy: Májová č. p. 131; Větrná č. p. 93 a 122."]:
        hits = U.scan([text], st, "Dolní Břežany")
        assert {n: h["num"] and h["num"]["label"] for n, h in hits.items()} == {"Májová": "č. p. 131", "Větrná": "č. p. 93"}, text
    # ulice bez čísla v nadpisu, číslo až v dalším textu – číslo se doplní
    hits = U.scan(["přerušit elektřinu (ulice Májová a Větrná)", "ulice Májová (č. p. 131) a Větrná (č. p. 93, 122)"], st, "Dolní Břežany")
    assert hits["Větrná"]["num"]["a"] == 93 and hits["Májová"]["num"]["a"] == 131

