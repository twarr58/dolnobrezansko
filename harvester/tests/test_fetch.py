import asyncio

import httpx

from uredni_desky.fetch import fetch_all
from uredni_desky.nkod import BoardSource
from uredni_desky.tls import is_missing_issuer


def src(i, url, *alt):
    return BoardSource(f"b{i}", f"https://example.org/ds/{i}", "", "", url, tuple(alt))


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/ok":
        return httpx.Response(200, content=b'\xef\xbb\xbf{"informace": []}')
    if path == "/missing":
        return httpx.Response(404)
    if path == "/html":
        return httpx.Response(200, content=b"<html>chyba</html>")
    if path == "/slow":
        raise httpx.ConnectTimeout("timeout", request=request)
    return httpx.Response(503)


def run(sources):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_all(client, sources, retries=0)
    return asyncio.run(go())


def test_fetch_all_classifies_results():
    res = run([src(1, "https://x/ok"), src(2, "https://x/missing"), src(3, "https://x/html"),
               src(4, "https://x/down"), src(5, "https://x/slow")])
    assert res["b1"].ok and res["b1"].payload == {"informace": []}
    assert (res["b2"].ok, res["b2"].kind, res["b2"].error) == (False, "not_found", "HTTP 404")
    assert res["b3"].kind == "html"
    assert res["b4"].kind == "server" and res["b4"].http_status == 503
    assert res["b5"].kind == "timeout"


def test_falls_back_to_alternative_distribution():
    res = run([src(1, "https://x/missing", "https://y/ok")])["b1"]
    assert res.ok and res.url == "https://y/ok"
    assert any("záložní" in w for w in res.warnings)


def test_missing_issuer_detection():
    err = Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1000)")
    assert is_missing_issuer(err)
    assert not is_missing_issuer(Exception("certificate has expired"))


def test_fallback_proxy_used_on_timeout():
    def via_proxy(request):
        return httpx.Response(200, content=b'{"informace": []}')

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client, \
                httpx.AsyncClient(transport=httpx.MockTransport(via_proxy)) as fallback:
            return await fetch_all(client, [src(1, "https://x/slow"), src(2, "https://x/missing")],
                                   retries=0, fallback=fallback)

    res = asyncio.run(go())
    assert res["b1"].ok and res["b1"].via_proxy
    assert not res["b2"].ok and res["b2"].kind == "not_found"  # 404 se přes proxy nezkouší


def test_board_timeout_and_total_budget():
    async def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drip":
            await asyncio.sleep(5)
        return httpx.Response(200, content=b'{"informace": []}')

    async def go(**kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            return await fetch_all(client, [src(1, "https://x/drip"), src(2, "https://y/ok")], retries=0, **kw)

    res = asyncio.run(go(board_timeout=0.2))
    assert res["b1"].kind == "timeout" and "déle" in res["b1"].error and res["b2"].ok
    res = asyncio.run(go(total_budget=0))
    assert not res["b2"].ok and "vyčerpán" in res["b2"].error
