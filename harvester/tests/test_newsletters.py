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
    psary = """<a href="/psarsky-zpravodaj/">Psárský zpravodaj</a>
      <a href="/wp-content/uploads/2026/10/psarskyzpravodajrijen2026web.pdf">Psárský zpravodaj říjen 2026</a>
      <a href="/wp-content/uploads/2026/01/cenik-inzerce.pdf">Ceník inzerce</a>"""
    got = newsletters.parse_archive(psary, "https://psary.cz/psarsky-zpravodaj/", cfg["Psáry"])
    assert [i["t"] for i in got] == ["Psárský zpravodaj říjen 2026"]
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
