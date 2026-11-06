import asyncio

import httpx

from uredni_desky.extra import load_extra_sources
from uredni_desky.fetch import fetch_all
from uredni_desky import cache
from uredni_desky.imunis import czech_date, fetch_board, parse_detail, parse_listing, parse_rss, to_informace
from uredni_desky.normalize import normalize_board

RSS = """﻿<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel xmlns:atom="http://www.w3.org/2005/Atom">
  <title>Úřední deska – Dolní Břežany</title>
  <generator>iMunis eDeska</generator>
  <item>
    <guid isPermaLink="true">https://brezany.imunis.cz/edeska/detail?id=22059</guid>
    <link>https://brezany.imunis.cz/edeska/detail?id=22059</link>
    <category>Oznámení</category>
    <title>Rozhodnutí - Stavební povolení - Napojení území STAR na metro D</title>
    <pubDate>Thu, 29 Oct 2026 12:06:45 +0100</pubDate>
  </item>
  <item>
    <guid isPermaLink="true">https://brezany.imunis.cz/edeska/detail?id=22060</guid>
    <link>https://brezany.imunis.cz/edeska/detail?id=22060</link>
    <category>Oznámení o konání Zastupitelstva</category>
    <title>Pozvánka na ustavující zasedání Zastupitelstva obce Dolní Břežany</title>
    <pubDate>Fri, 30 Oct 2026 12:02:31 +0100</pubDate>
  </item>
</channel></rss>""".encode("utf-8")

DETAIL = """<html><body><main>
<h1>Rozhodnutí - Stavební povolení - Napojení území STAR na metro D</h1>
<table class="edeska table detail"><tbody>
  <tr><th>Značka</th><td>KUSK 123/2026</td></tr>
  <tr><th>Obsah vyvěšení</th><td><a href="/edeska/file?id=84785">-/1</a> <span class="nowrap">(dokument PDF, 48,2&#xA0;kB)</span></td></tr>
  <tr><th>Popis</th><td></td></tr>
  <tr><th>Typ</th><td>Rozhodnutí</td></tr>
  <tr><th>Datum do</th><td>14.&#xA0;11.&#xA0;2026 12:06:45</td></tr>
</tbody></table>
<h3>Úřední deska příslušného orgánu – Krajský úřad Středočeského kraje</h3>
<table class="edeska table detail"><tbody>
  <tr><th>Vyvěšeno od</th><td>29.&#xA0;10.&#xA0;2026</td></tr>
  <tr><th>Vyvěšeno do</th><td>14.&#xA0;11.&#xA0;2026</td></tr>
</tbody></table>
<table class="edeska table detail">
  <tr><th>Vyvěšeno od</th><td>30.&#xA0;10.&#xA0;2026</td></tr>
  <tr><th>Má být vyvěšeno do</th><td>15.&#xA0;11.&#xA0;2026</td></tr>
</table>
</main></body></html>"""


def test_parse_rss():
    items = parse_rss(RSS)
    assert [i.published for i in items] == ["2026-10-29", "2026-10-30"]
    assert items[0].category == "Oznámení" and items[0].url.endswith("id=22059")


def test_czech_date():
    assert czech_date("14.\xa011.\xa02026 12:06:45") == "2026-11-14"
    assert czech_date("9. 1. 2027") == "2027-01-09"
    assert czech_date("-") is None


def test_detail_to_ofn():
    item = parse_rss(RSS)[0]
    detail = parse_detail(DETAIL, item.url)
    info = to_informace(item, detail)
    assert info["vyvěšení"] == {"datum": "2026-10-29"}  # první výskyt „Vyvěšeno od“
    assert info["relevantní_do"] == {"datum": "2026-11-14"}
    assert info["číslo_jednací"] == "KUSK 123/2026"
    assert info["agenda"][0]["název"]["cs"] == "Rozhodnutí"
    assert info["dokument"][0]["url"] == "https://brezany.imunis.cz/edeska/file?id=84785"
    assert info["dokument"][0]["název"]["cs"] == item.title
    [norm] = normalize_board("b", {"informace": [info]})
    assert norm.posted == "2026-10-29" and norm.until == "2026-11-14" and len(norm.docs) == 1


def test_without_detail_falls_back_to_rss():
    item = parse_rss(RSS)[1]
    info = to_informace(item, None)
    assert info["vyvěšení"] == {"datum": "2026-10-30"} and "relevantní_do" not in info
    assert info["agenda"][0]["název"]["cs"] == "Oznámení o konání Zastupitelstva"


def test_extra_sources_and_fetch():
    [src] = [s for s in load_extra_sources() if "brezany" in s.access_url]
    assert src.fmt == "imunis" and src.publisher == "Obec Dolní Břežany"

    def handler(request):
        if request.url.path.endswith("rss.asp"):
            return httpx.Response(200, content=RSS)
        if request.url.params.get("id") == "22059":
            return httpx.Response(200, text=DETAIL)
        return httpx.Response(500)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_all(client, [src], retries=0)

    res = asyncio.run(go())[src.id]
    assert res.ok and len(res.payload["informace"]) == 2
    first, second = res.payload["informace"]
    assert first["relevantní_do"]["datum"] == "2026-11-14"
    assert "relevantní_do" not in second  # detail selhal (500) → jen údaje z RSS


# seznam desky (sem eDeska přesměruje detail některých starých položek) – každá položka má v řádku své soubory
LISTING = """<html><body><table class="edeska table">
<thead><tr><th>Název</th><th>Značka</th><th>Přílohy</th></tr></thead><tbody>
<tr><td><a href="/edeska/detail?id=2627">Přerušení dodávky elektřiny</a></td><td>1550/2026/OÚ</td>
    <td><a href="/edeska/file?id=59607">dokument PDF</a></td></tr>
<tr><td><a href="/edeska/detail?id=2616">DSO – závěrečný účet</a></td><td>0965/2026/OÚ</td>
    <td><a href="/edeska/detail?id=2616">7 dokumentů</a></td></tr>
<tr><td><a href="/edeska/detail?id=1660">Dočasné omezení užívání pitné vody</a></td><td>980/2019/OÚ</td>
    <td><a href="/edeska/file?id=47367">dokument PDF</a></td></tr>
</tbody></table></body></html>"""


def test_detail_redirected_to_listing_takes_only_own_row():
    rss = """<?xml version="1.0"?><rss><channel>
      <item><link>https://obec.imunis.cz/edeska/detail?id=1660</link><title>Dočasné omezení užívání pitné vody</title></item>
      <item><link>https://obec.imunis.cz/edeska/detail?id=2627</link><title>Přerušení dodávky elektřiny</title></item>
    </channel></rss>""".encode()

    def handler(request):
        if request.url.path.endswith("rss"):
            return httpx.Response(200, content=rss)
        if request.url.params.get("id") == "1660":  # starý záznam: detail přesměruje na seznam
            return httpx.Response(302, headers={"Location": "https://obec.imunis.cz/edeska"})
        if request.url.path == "/edeska":
            return httpx.Response(200, text=LISTING)
        return httpx.Response(200, text=DETAIL.replace("84785", "59607"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            return await fetch_board(client, "https://obec.imunis.cz/edeska/feed/rss")

    cache.DETAILS.clear()
    cache.reset_budget()
    old, new = asyncio.run(go())["informace"]
    assert [d["url"] for d in old["dokument"]] == ["https://obec.imunis.cz/edeska/file?id=47367"]
    assert [d["url"] for d in new["dokument"]] == ["https://obec.imunis.cz/edeska/file?id=59607"]


def test_parse_listing_rows():
    rows = parse_listing(LISTING, "https://obec.imunis.cz/edeska")
    assert rows["1660"] == [("dokument PDF", "https://obec.imunis.cz/edeska/file?id=47367")]
    assert rows["2616"] == []  # víc dokumentů → jen odkaz na detail


def test_shared_docs_dropped_and_cache_refreshed():
    f = lambda n: {"název": {"cs": "x"}, "url": f"https://o.cz/edeska/file?id={n}"}  # noqa: E731
    infos = [{"url": "a", "dokument": [f(1), f(2), f(3)]}, {"url": "b", "dokument": [f(1)]}, {"url": "c", "dokument": [f(2)]}]
    assert cache.drop_shared_docs(infos, "deska") == 2
    assert [[d["url"][-1] for d in i["dokument"]] for i in infos] == [["3"], ["1"], ["2"]]
    # v uloženém stavu: položka se zavlečenými přílohami se z mezipaměti vyřadí → detail se stáhne znovu
    cache.load_from_state([
        {"url": "a", "board": "x", "docs": [{"url": "u1"}, {"url": "u2"}]},
        {"url": "b", "board": "x", "docs": [{"url": "u1"}]},
        {"url": "c", "board": "y", "docs": [{"url": "u2"}]},
    ])
    assert set(cache.DETAILS) == {"c"}
