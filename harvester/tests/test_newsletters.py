import asyncio
import json
from datetime import datetime, timezone

import httpx

from uredni_desky import newsletters

PAGE = """<html><body>
<a href="/zpravodaj">Zpravodaj</a>
<a href="/files/Zrcadlo%20229_web.pdf"></a>
<a href="/files/Zrcadlo%20229_web.pdf">Zrcadlo č. 229 (3/2026)</a>
<a href="/files/Zrcadlo%20228.pdf">Zrcadlo č. 228 (2/2026) (11 MB)</a>
<a href="/files/Cenik%20inzerce%20Zrcadlo.pdf">Ceník inzerce Zrcadlo</a>
<a href="/files/Zrcadlo%20186.pdf">Zrcadlo č. 186 (1/2016)</a>
</body></html>"""


def test_parse_archive_titles_years_and_exclude():
    cfg = {"name": "Zrcadlo", "url": "https://obec.cz/z/", "match": "/files/.*zrcadlo", "exclude": "cen[ií]k"}
    issues = newsletters.parse_archive(PAGE, "https://obec.cz/z/", cfg)
    assert [i["t"] for i in issues] == ["Zrcadlo č. 229 (3/2026)", "Zrcadlo č. 228 (2/2026)", "Zrcadlo č. 186 (1/2016)"]
    assert issues[0] == {"t": "Zrcadlo č. 229 (3/2026)", "url": "https://obec.cz/files/Zrcadlo%20229_web.pdf", "y": 2026}


def test_generic_link_text_uses_file_name_and_sorts_newest_first():
    page = """<a href="/up/2026/01/VL_01-26_web.pdf">Prohlédnout</a><a href="/up/2026/01/VL_01-26_web.pdf">Stáhnout</a>
              <a href="/up/2026/02/VL_02-26_web.pdf">Stáhnout</a><a href="/up/2021/04/vl_12_2005.pdf">Stáhnout</a>"""
    cfg = {"name": "Listy", "url": "https://v.cz/", "match": r"/vl_\d", "num_re": r"vl_(?P<num>\d{1,2})[-_](?P<year>\d{4}|\d{2})"}
    issues = newsletters.parse_archive(page, "https://v.cz/", cfg)
    assert [(i["t"], i["y"]) for i in issues] == [("č. 2/2026", 2026), ("č. 1/2026", 2026), ("č. 12/2005", 2005)]


def test_collect_pages_cache_and_failure(tmp_path):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if "zle" in req.url.host:
            return httpx.Response(500)
        n = req.url.params.get("p", "1")
        return httpx.Response(200, text=f'<a href="/r-cislo-{n}">Rozhled číslo {n}</a>')

    members = [
        {"name": "A", "newsletter": {"name": "Rozhled", "url": "https://a.cz/arch", "match": "-cislo-",
                                     "page_param": "p", "pages": 2}},
        {"name": "B", "newsletter": {"name": "Listy", "url": "https://zle.cz/", "match": "x"}},
        {"name": "C"},
    ]
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await newsletters.collect(client, members, tmp_path, now)

    out = asyncio.run(go())
    assert [i["t"] for i in out[0]["issues"]] == ["Rozhled číslo 1", "Rozhled číslo 2"]
    assert out[1]["ok"] is False and out[1]["issues"] == [] and out[1]["archive"] == "https://zle.cz/"
    assert out[2] == {"obec": "C"}
    assert "https://a.cz/arch?p=2" in calls
    n = len(calls)
    asyncio.run(go())  # do REFRESH_HOURS se archiv znovu nestahuje
    assert len(calls) == n
    assert json.loads((tmp_path / "newsletters.json").read_text())["A"]["ok"] is True


def test_psary_and_jesenice_match_only_issue_files():
    import json as _json
    from pathlib import Path

    region = _json.loads((Path(__file__).resolve().parent.parent / "uredni_desky" / "data" / "region.json").read_text())
    cfg = {m["name"]: m.get("newsletter") for m in region["members"]}
    psary = """<a href="https://psary.cz/blizke-udalosti/">Kalendář akcí</a>
      <a href="https://psary.cz/psarsky-zpravodaj/">Psárský zpravodaj</a>
      <a href="https://psary.cz/psarsky-zpravodaj/o-psarskem-zpravodaji/">O Psárském zpravodaji</a>
      <a href="https://psary.cz/psarsky-zpravodaj/cerven-2011/">Červen 2011</a>
      <a href="https://psary.cz/psarsky-zpravodaj/psarsky-zpravodaj-3-2023/">Psárský zpravodaj 3/2023</a>"""
    got = newsletters.parse_archive(psary, "https://psary.cz/psarsky-zpravodaj/", cfg["Psáry"])
    assert [i["t"] for i in got] == ["Psárský zpravodaj 3/2023", "Červen 2011"]
    jes = """<a href="/jesenicky-kuryr/ms-4026/p1=4026">Jesenický kurýr</a>
      <a href="/assets/File.ashx?id_org=1&id_dokumenty=9">Jesenický kurýr 10/2026</a>"""
    got = newsletters.parse_archive(jes, "https://www.mujesenice.cz/jesenicky-kuryr/ms-4026/p1=4026", cfg["Jesenice"])
    assert [(i["t"], i["y"]) for i in got] == [("Jesenický kurýr 10/2026", 2026)]
    assert cfg["Okrouhlo"] is False


def test_detail_page_gives_pdf_and_issue_date():
    page = """<html><body><nav>Kalendář 2026</nav><main><h1>Dolnobřežanský Rozkvět číslo 77</h1><p>prosinec 2021</p>
      <a href="/file/111">Dolnobřežanský Rozkvět - vydání č. 77 ve formátu PDF</a>
      <a href="/file/222">Kalendář akcí ve formátu PDF</a></main></body></html>"""
    got = newsletters.parse_detail(page, "https://www.dolnibrezany.cz/dolnobrezansky-rozkvet-cislo-77",
                                   {"match": r"/file/\d+", "exclude": "kalend"})
    assert got == {"pdf": "https://www.dolnibrezany.cz/file/111", "when": "prosinec 2021", "y": 2021}

    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, text=page)

    async def go(resolved):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await newsletters.resolve_details(
                client, [{"t": "Rozkvět číslo 77", "url": "https://www.dolnibrezany.cz/r-77"}],
                {"match": r"/file/\d+", "exclude": "kalend"}, resolved)

    resolved = {}
    out = asyncio.run(go(resolved))
    assert out == [{"t": "Rozkvět číslo 77 (prosinec 2021)", "url": "https://www.dolnibrezany.cz/file/111",
                    "page": "https://www.dolnibrezany.cz/r-77", "y": 2021}]
    asyncio.run(go(resolved))  # podruhé z paměti
    assert len(calls) == 1


def test_browser_option_sends_browser_headers(tmp_path):
    seen = []

    def handler(req):
        seen.append(req.headers.get("user-agent", ""))
        return httpx.Response(200, text='<a href="/f/vl_01-26.pdf">Stáhnout</a>')

    members = [{"name": "V", "newsletter": {"name": "Listy", "url": "https://v.cz/listy/", "match": r"/vl_\d",
                                            "browser": True}}]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": "harvester"}) as c:
            return await newsletters.collect(c, members, tmp_path, datetime(2026, 11, 5, tzinfo=timezone.utc))

    asyncio.run(go())
    assert seen and seen[0].startswith("Mozilla/5.0")


def test_year_as_loose_text_between_tags_jesenice():
    """Skutečná struktura archivu Jesenického kurýru: rok je volný text („<br />2026<br />“, „</div>2024<br />“)."""
    import json as _json
    from pathlib import Path

    region = _json.loads((Path(__file__).resolve().parent.parent / "uredni_desky" / "data" / "region.json").read_text())
    cfg = {m["name"]: m.get("newsletter") for m in region["members"]}["Jesenice"]
    html = """<td><h2>VYDANÁ ČÍSLA - ARCHIV</h2>
      <br />2026<br /><ul><li><a href="https://zpravodaj.munipolis.cz/jesenice/leden-2026/">Leden  </a></li>
      <li><a href="/assets/File.ashx?id_org=5861&amp;id_dokumenty=17562">Únor</a></li>
      <li><a href=" https://zpravodaj.munipolis.cz/jesenice/rijen-2026/">Říjen</a></li></ul>
      <div><hr /></div><div><span style="color: rgb(51, 51, 51)">2025</span></div>
      <ul><li><a href="/assets/File.ashx?id_org=5861&amp;id_dokumenty=16696">Září</a></li>
      <li><a href="https://zpravodaj.munipolis.cz/jesenice/prosinec-2025/">Prosinec</a></li></ul>
      <div><hr /></div><div></div>2024<br /><ul><li><a href="/assets/File.ashx?id_org=5861&amp;id_dokumenty=1">Leden</a></li></ul></td>"""
    got = newsletters.parse_archive(html, cfg["url"], cfg)
    assert [i["t"] for i in got] == ["Říjen 2026", "Únor 2026", "Leden 2026", "Prosinec 2025", "Září 2025", "Leden 2024"]


def test_first_seen_marks_only_issues_added_after_first_run(tmp_path):
    pages = {"n": PAGE}

    def handler(req):
        return httpx.Response(200, html=pages["n"])

    members = [{"name": "Vrané", "newsletter": {"name": "Zrcadlo", "url": "https://obec.cz/z/", "match": "/files/.*zrcadlo",
                                                  "exclude": "cen[ií]k"}}]

    async def run(now):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await newsletters.collect(client, members, tmp_path, now)

    first = asyncio.run(run(datetime(2026, 11, 1, tzinfo=timezone.utc)))
    assert all("fs" not in i for i in first[0]["issues"])  # první průchod: datum neznámé, nic není „nové“
    pages["n"] = PAGE.replace("<body>", '<body><a href="/files/Zrcadlo%20230.pdf">Zrcadlo č. 230 (4/2026)</a>')
    later = asyncio.run(run(datetime(2026, 11, 2, 13, tzinfo=timezone.utc)))
    issues = later[0]["issues"]
    assert issues[0]["t"] == "Zrcadlo č. 230 (4/2026)" and issues[0]["fs"].startswith("2026-11-02")
    assert all("fs" not in i for i in issues[1:])
