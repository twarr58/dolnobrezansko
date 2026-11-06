import asyncio
import json

import httpx

from uredni_desky import erby

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_download_wikidata_and_commons_file_index_and_skip(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        if req.url.host == "query.wikidata.org":
            q = req.url.params["query"]
            assert "wd:Q640046" in q and "Q2697750" not in q and "Q1863455" not in q  # Libeř má erb_commons
            return httpx.Response(200, json={"results": {"bindings": [
                {"item": {"value": "http://www.wikidata.org/entity/Q640046"},
                 "coa": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/Psary%20CZ%20CoA.svg"}}]}})
        assert req.url.host == "commons.wikimedia.org" and req.url.params["width"] == "250"
        if "Liber" in str(req.url):
            return httpx.Response(200, content=b"GIF89a" + b"0" * 50, headers={"content-type": "image/gif"})
        assert "Psary%20CZ%20CoA.svg" in str(req.url)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    members = [
        {"name": "Dolní Břežany", "slug": "dolni-brezany", "qid": "Q2697750", "erb": "erby/dolni-brezany.svg"},
        {"name": "Psáry", "slug": "psary", "qid": "Q640046"},
        {"name": "Libeř", "slug": "liber", "qid": "Q1863455", "erb_commons": "Liber CZ CoA.gif"},
        {"name": "Okrouhlo", "slug": "okrouhlo", "erb_qid": "Q2659689"},  # znak ve Wikidatech není
    ]

    async def go():
        async with _client(handler) as c:
            return await erby.download(c, members, tmp_path)

    index = asyncio.run(go())
    assert set(index) == {"Psáry", "Libeř"}
    assert (tmp_path / "erby" / "psary.png").read_bytes() == PNG and (tmp_path / "erby" / "liber.gif").exists()
    saved = json.loads((tmp_path / "erby" / "index.json").read_text("utf-8"))
    assert saved["Psáry"] == {"path": "erby/psary.png", "file": "Psary CZ CoA.svg",
                              "source": "https://commons.wikimedia.org/wiki/File:Psary_CZ_CoA.svg"}

    # druhý běh: soubory jsou – Commons se znovu nestahuje
    calls.clear()
    asyncio.run(go())
    assert "commons.wikimedia.org" not in calls

    # web: znak z repozitáře + z větve assets
    assert erby.load(members, tmp_path) == {"Dolní Břežany": "erby/dolni-brezany.svg", "Psáry": "erby/psary.png",
                                            "Libeř": "erby/liber.gif"}
    assert erby.load(members, None) == {"Dolní Břežany": "erby/dolni-brezany.svg"}


def test_too_many_requests_goes_through_proxy(tmp_path):
    def direct(req):
        return httpx.Response(429, text="Too many requests")

    via = []

    def proxy(req):
        via.append(str(req.url))
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    members = [{"name": "Vestec", "slug": "vestec", "erb_commons": "Vestec PZ CZ CoA.svg"}]

    async def go():
        async with _client(direct) as c, _client(proxy) as fb:
            return await erby.download(c, members, tmp_path, fb)

    assert asyncio.run(go())["Vestec"]["path"] == "erby/vestec.png"
    assert len(via) == 1


def test_failure_without_proxy_keeps_nothing(tmp_path):
    members = [{"name": "Vestec", "slug": "vestec", "erb_commons": "Vestec PZ CZ CoA.svg"}]

    async def go():
        async with _client(lambda req: httpx.Response(429)) as c:
            return await erby.download(c, members, tmp_path)

    assert asyncio.run(go()) == {}
    assert erby.load(members, tmp_path) == {}
