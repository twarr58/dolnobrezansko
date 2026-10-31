import asyncio

import httpx

from uredni_desky.discover import feed_candidates
from uredni_desky.extra import load_extra_sources
from uredni_desky.feed import clean_url, parse_feed
from uredni_desky.fetch import fetch_all
from uredni_desky.normalize import normalize_board
from uredni_desky.vismo import parse_board

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
<title>Úřední deska | Městys Březno</title><link>https://www.brezno.cz/uredni-deska</link>
<item>
 <title>Pozvánka na ustavující zasedání nově zvoleného Zastupitelstva městyse Března 3.11.2026</title>
 <link>https://www.brezno.cz/uredni-deska?id=1317&amp;utm_source=%C3%9A%C5%99edn%C3%AD_deska&amp;utm_medium=rss&amp;utm_campaign=1317&amp;action=detail</link>
 <description>&lt;p&gt;Pozvánka na ustavující schůzi.&lt;/p&gt;&lt;p&gt;Přílohy:&lt;/p&gt;&lt;ul&gt;&lt;li&gt;&lt;a href=&quot;https://www.brezno.cz/file.php?oid=14004992&amp;amp;utm_source=%C3%9A%C5%99edn%C3%AD_deska&amp;amp;utm_medium=rss&quot;&gt;Příloha 1&lt;/a&gt;&lt;/li&gt;&lt;/ul&gt;</description>
 <guid>https://www.brezno.cz/uredni-deska?id=1317&amp;action=detail</guid>
 <pubDate>Mon, 26 Oct 2026 00:00:00 +0100</pubDate>
</item>
</channel></rss>""".encode()

VISMO = """<html><body><div class="site_board">
<ul class="site_board__list"><li class="site_board__item">
 <div class="site_board__container"><h3 class="site_board__title"><a href="/oznameni-o-zasedani" class="site_board__link">Oznámení o zasedání zastupitelstva</a></h3>
 <span class="site_board__folder">Rubrika: <a href="/dokumenty-z-obecniho-uradu" class="site_board__folder__link">Dokumenty z obecního úřadu</a></span></div>
 <div class="site_board__container -dates">
  <span class="site_board__date -from">Vyvěšeno od: <time class="site_board__date" datetime="2026-10-22">22. 10. 2026</time></span>
  <span class="site_board__date -to">Vyvěšeno do: <time class="site_board__date" datetime="2026-11-02">2. 11. 2026</time></span>
 </div></li></ul>
<a href="/uredni-deska?page=2" class="site_board__pager">Další</a>
</div></body></html>"""


def test_parse_rss_feed():
    [info] = parse_feed(RSS, "https://www.brezno.cz/uredni-deska?action=atom")
    assert info["url"] == "https://www.brezno.cz/uredni-deska?id=1317&action=detail"
    assert info["vyvěšení"] == {"datum": "2026-10-26"}
    assert info["dokument"] == [{"název": {"cs": "Příloha 1"}, "url": "https://www.brezno.cz/file.php?oid=14004992"}]
    [norm] = normalize_board("b", {"informace": [info]})
    assert norm.posted == "2026-10-26" and len(norm.docs) == 1


def test_clean_url():
    assert clean_url("https://x.cz/a?id=1&utm_source=x&utm_medium=rss") == "https://x.cz/a?id=1"


def test_parse_vismo():
    [info] = parse_board(VISMO, "https://www.orlickepodhuri.cz/uredni-deska")
    assert info["url"] == "https://www.orlickepodhuri.cz/oznameni-o-zasedani"
    assert info["vyvěšení"] == {"datum": "2026-10-22"} and info["relevantní_do"] == {"datum": "2026-11-02"}
    assert info["agenda"][0]["název"]["cs"] == "Dokumenty z obecního úřadu"


def test_feed_candidates():
    alis = feed_candidates("", "https://urednideska.alis.cz/obec-patokryje/kategorie/rozpocet?inputSearch=")
    assert alis == ["https://urednideska.alis.cz/obec-patokryje/rss"]
    html = '<link rel="alternate" type="application/rss+xml" href="/deska/rss.xml"><a href="/style.css">'
    c = feed_candidates(html, "https://www.obec.cz/uredni-deska")
    assert c[0] == "https://www.obec.cz/deska/rss.xml" and "https://www.obec.cz/uredni-deska?action=atom" in c


def test_rss_and_vismo_sources_fetch(tmp_path):
    import json
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"sources": [
        {"fmt": "rss", "url": "https://www.brezno.cz/uredni-deska?action=atom", "publisher": "Městys Březno"},
        {"fmt": "vismo", "url": "https://www.orlickepodhuri.cz/uredni-deska", "publisher": "Obec Orlické Podhůří"},
    ]}), "utf-8")
    srcs = [s for s in load_extra_sources(p) if s.fmt in ("rss", "vismo")]

    def handler(request):
        return httpx.Response(200, content=RSS if "brezno" in str(request.url) else VISMO.encode())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await fetch_all(c, srcs, retries=0)

    res = asyncio.run(go())
    assert all(r.ok and len(r.payload["informace"]) == 1 for r in res.values())
