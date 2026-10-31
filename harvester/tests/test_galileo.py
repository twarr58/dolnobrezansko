import asyncio

import httpx

from uredni_desky.extra import load_extra_sources
from uredni_desky.fetch import fetch_all
from uredni_desky.galileo import parse_detail, parse_list, to_informace
from uredni_desky.normalize import normalize_board

BASE = "https://www.semtes.cz/urad/uredni-deska/"

LIST = """<html><body>
<div class="category card ostatni-1757"><div class="card-header"><h3 class="card-title">Zastupitelstvo</h3></div>
 <div class="card-body">
  <div class="item"><div class="item-heading">
   <a class="item-href" href="/urad/uredni-deska/informace-o-konani-ustavujiciho-zasedani-27933.html?kshow=1757">Informace o konání ustavujícího zasedání zastupitelstva obce Semtěš</a>
  </div><div class="item-data">
   <span class="item-date item-date-from">Vyvěšeno: 26. 10. 2026</span>
   <span class="item-date item-date-to">Datum sejmutí: 10. 11. 2026</span>
  </div></div>
 </div></div>
<div class="category card rozpocet-1774"><div class="card-header"><h3 class="card-title">Rozpočet</h3></div>
 <div class="card-body">
  <div class="item"><div class="item-heading">
   <a class="item-href" href="/urad/uredni-deska/rozpocty-schvaleny-strednedoby-vyhled-27719.html?kshow=1774">Rozpočty - Schválený střednědobý výhled rozpočtu 2024-2027</a>
  </div><div class="item-data"><span class="item-date item-date-from">Vyvěšeno: 16. 10. 2023</span></div></div>
  <div class="item"><div class="item-heading">
   <a class="item-href" href="/urad/uredni-deska/informace-o-konani-ustavujiciho-zasedani-27933.html?kshow=1774">Informace o konání ustavujícího zasedání zastupitelstva obce Semtěš</a>
  </div></div>
 </div></div>
</body></html>"""

DETAIL = """<html><body><div class="content">
<p><a class="file" href="/e_download.php?file=/data/uredni_deska/obsah1542_1.pdf&amp;original=Rozpoctove_zmeny%202026.2.pdf" title="Soubor ke stáhnutí">Rozpočtové opatření č.&nbsp;2</a><i> Typ: PDF dokument, Velikost: 58.21 kB</i></p>
</div><div class="text-right info">
<span class="d-block date post-date">Vyvěšeno: 29. 9. 2026</span>
<span class="d-block date removal-date">Datum sejmutí: 31. 12. 2026</span>
</div>
<a href="//www.igalileo.cz">Galileo Corporation s.r.o.</a></body></html>"""


def test_parse_list():
    items = parse_list(LIST, BASE)
    assert len(items) == 2  # duplicita v jiné kategorii (jiné ?kshow) se slučuje
    first, second = items
    assert first.url == "https://www.semtes.cz/urad/uredni-deska/informace-o-konani-ustavujiciho-zasedani-27933.html"
    assert (first.posted, first.until, first.category) == ("2026-10-26", "2026-11-10", "Zastupitelstvo")
    assert (second.posted, second.until, second.category) == ("2023-10-16", None, "Rozpočet")


def test_parse_detail_and_ofn():
    d = parse_detail(DETAIL, BASE + "x-27928.html")
    assert d.files == [("Rozpočtové opatření č. 2",
                        "https://www.semtes.cz/e_download.php?file=/data/uredni_deska/obsah1542_1.pdf&original=Rozpoctove_zmeny%202026.2.pdf")]
    assert (d.posted, d.until) == ("2026-09-29", "2026-12-31")
    item = parse_list(LIST, BASE)[0]
    [norm] = normalize_board("b", {"informace": [to_informace(item, d)]})
    assert norm.posted == "2026-10-26" and norm.until == "2026-11-10" and len(norm.docs) == 1
    assert norm.agendas == ["Zastupitelstvo"]


def test_galileo_sources_and_fetch():
    srcs = [s for s in load_extra_sources() if s.fmt == "galileo"]
    assert {s.publisher for s in srcs} >= {"Obec Semtěš", "Městys Bílé Podolí"}
    src = next(s for s in srcs if "semtes" in s.access_url)

    def handler(request):
        if request.url.path == "/urad/uredni-deska/":
            return httpx.Response(200, text=LIST)
        return httpx.Response(200, text=DETAIL)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await fetch_all(c, [src], retries=0)

    res = asyncio.run(go())[src.id]
    assert res.ok and len(res.payload["informace"]) == 2
    assert all(i.get("dokument") for i in res.payload["informace"])


def test_empty_list_is_error():
    src = next(s for s in load_extra_sources() if s.fmt == "galileo")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>"))) as c:
            return await fetch_all(c, [src], retries=0)

    res = asyncio.run(go())[src.id]
    assert not res.ok and res.kind == "format"
