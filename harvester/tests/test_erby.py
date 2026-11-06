import asyncio
import json
from datetime import datetime, timezone

import httpx

from uredni_desky import erby

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def test_collect_uses_repo_crest_downloads_missing_and_caches(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        if req.url.host == "query.wikidata.org":
            assert "wd:Q640046" in req.url.params["query"] and "Q2697750" not in req.url.params["query"]
            return httpx.Response(200, json={"results": {"bindings": [
                {"item": {"value": "http://www.wikidata.org/entity/Q640046"},
                 "coa": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/Psary%20CZ%20CoA.svg"}}]}})
        assert req.url.host == "commons.wikimedia.org" and req.url.params["width"] == "120"
        assert "Psary%20CZ%20CoA.svg" in str(req.url)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    members = [
        {"name": "Dolní Břežany", "slug": "dolni-brezany", "qid": "Q2697750", "erb": "erby/dolni-brezany.svg"},
        {"name": "Psáry", "slug": "psary", "qid": "Q640046"},
        {"name": "Okrouhlo", "slug": "okrouhlo", "erb_qid": "Q2659689"},  # znak ve Wikidatech není
    ]
    site, state = tmp_path / "site", tmp_path / "state"

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await erby.collect(c, members, site, state, NOW)

    out = asyncio.run(go())
    assert out == {"Dolní Břežany": "erby/dolni-brezany.svg", "Psáry": "erby/psary.png"}
    assert (site / "erby" / "psary.png").read_bytes() == PNG
    assert json.loads((state / "erby.json").read_text("utf-8"))["psary"]["file"] == "Psary CZ CoA.svg"

    # druhý běh do nového webu: znak ze stavu, Wikidata ani Commons se do týdne znovu nevolají
    calls.clear()
    site2 = tmp_path / "site2"

    async def go2():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await erby.collect(c, members, site2, state, NOW)

    assert asyncio.run(go2())["Psáry"] == "erby/psary.png"
    assert calls == [] and (site2 / "erby" / "psary.png").exists()


def test_failed_wikidata_keeps_previous_crests(tmp_path):
    def handler(req):
        return httpx.Response(503)

    members = [{"name": "Psáry", "slug": "psary", "qid": "Q640046"}]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await erby.collect(c, members, tmp_path / "site", tmp_path / "state", NOW)

    assert asyncio.run(go()) == {}


def test_commons_file_from_config_skips_wikidata(tmp_path):
    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        assert "Liber%20CZ%20CoA.gif" in str(req.url)
        return httpx.Response(200, content=b"GIF89a" + b"0" * 50, headers={"content-type": "image/gif"})

    members = [{"name": "Libeř", "slug": "liber", "qid": "Q1863455", "erb_commons": "Liber CZ CoA.gif"}]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await erby.collect(c, members, tmp_path / "site", tmp_path / "state", NOW)

    assert asyncio.run(go()) == {"Libeř": "erby/liber.gif"}
    assert hosts == ["commons.wikimedia.org"]
