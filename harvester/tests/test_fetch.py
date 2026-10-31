import asyncio

import httpx

from uredni_desky.fetch import fetch_all
from uredni_desky.nkod import BoardSource


def src(i, url):
    return BoardSource(f"b{i}", f"https://example.org/ds/{i}", "", "", url)


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/ok":
        return httpx.Response(200, content=b'\xef\xbb\xbf{"informace": []}')
    if path == "/missing":
        return httpx.Response(404)
    if path == "/html":
        return httpx.Response(200, content=b"<html>chyba</html>")
    return httpx.Response(503)


def test_fetch_all_classifies_results():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sources = [src(1, "https://x/ok"), src(2, "https://x/missing"), src(3, "https://x/html"), src(4, "https://x/down")]
            return await fetch_all(client, sources, retries=0)

    res = asyncio.run(run())
    assert res["b1"].ok and res["b1"].payload == {"informace": []}
    assert not res["b2"].ok and res["b2"].error == "HTTP 404"
    assert not res["b3"].ok and "Neplatný JSON" in res["b3"].error
    assert not res["b4"].ok and res["b4"].http_status == 503
