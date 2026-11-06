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
    assert saved["Psáry"] == {"path": "erby/psary.png", "file": "Psary CZ CoA.svg", "v": erby.CLEAN_VERSION,
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


def test_clear_background_keeps_white_inside_shield():
    from io import BytesIO

    from PIL import Image, ImageDraw

    im = Image.new("RGB", (60, 70), (250, 250, 250))  # světlé pozadí jako náhledy z Commons
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, 49, 59), fill=(200, 0, 0), outline=(0, 0, 0))  # štít s obrysem
    d.rectangle((25, 25, 34, 44), fill=(255, 255, 255))  # bílá figura uvnitř
    buf = BytesIO()
    im.save(buf, "PNG")
    out = Image.open(BytesIO(erby.clear_background(buf.getvalue())))
    assert out.mode == "RGBA" and out.size == (40, 50)  # oříznuto na štít
    assert out.getpixel((20, 25))[:3] == (255, 255, 255) and out.getpixel((20, 25))[3] == 255  # figura zůstává
    full = Image.open(BytesIO(erby.clear_background(buf.getvalue(), light=300)))  # nic není pozadí
    assert full.size == (60, 70)


def test_download_saves_crest_without_background(tmp_path):
    from io import BytesIO

    from PIL import Image

    im = Image.new("RGB", (30, 30), (255, 255, 255))
    im.paste((0, 0, 200), (5, 5, 25, 25))
    buf = BytesIO()
    im.save(buf, "GIF")

    def handler(req):
        return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "image/gif"})

    members = [{"name": "Libeř", "slug": "liber", "erb_commons": "Liber CZ CoA.gif"}]

    async def go():
        async with _client(handler) as c:
            return await erby.download(c, members, tmp_path)

    index = asyncio.run(go())
    assert index["Libeř"]["path"] == "erby/liber.png" and index["Libeř"]["v"] == erby.CLEAN_VERSION
    saved = Image.open(tmp_path / "erby" / "liber.png")
    assert saved.size == (20, 20) and not (tmp_path / "erby" / "liber.gif").exists()
