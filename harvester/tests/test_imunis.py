import asyncio

import httpx

from uredni_desky.extra import load_extra_sources
from uredni_desky.fetch import fetch_all
from uredni_desky.imunis import czech_date, parse_detail, parse_rss, to_informace
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
