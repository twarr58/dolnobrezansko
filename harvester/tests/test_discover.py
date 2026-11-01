import json

from uredni_desky import cache
from uredni_desky.discover import classify_html, publisher_name, registered_domain
from uredni_desky.extra import load_extra_sources


def test_classify_imunis():
    html = '<a href="https://brezany.imunis.cz/edeska/">Úřední deska</a>'
    assert classify_html(html, "https://www.dolnibrezany.cz/") == {"fmt": "imunis", "subdomain": "brezany"}


def test_classify_galileo():
    html = '<a href="/urad/uredni-deska/">Úřední deska</a> <a href="//www.igalileo.cz">Galileo</a>'
    assert classify_html(html, "https://www.semtes.cz/") == {"fmt": "galileo", "url": "https://www.semtes.cz/urad/uredni-deska/"}


def test_classify_unknown_records_cms_and_link():
    html = '<meta name="generator" content="WordPress 6.5"><a href="/obecni-urad/uredni-deska/">Deska</a>'
    info = classify_html(html, "https://www.obec.cz/")
    assert info == {"fmt": None, "cms": "WordPress 6.5", "board_link": "https://www.obec.cz/obecni-urad/uredni-deska/"}


def test_vismo_detected():
    info = classify_html('<meta name="application-name" content="X" data-application="Vismo6">', "https://www.x.cz/")
    assert info["cms"] == "Vismo6" and info["fmt"] is None


def test_helpers():
    assert publisher_name("Kolín", "město v Česku|obec v Česku") == "Město Kolín"
    assert publisher_name("Bílé Podolí", "městys v Česku") == "Městys Bílé Podolí"
    assert publisher_name("Semtěš", "obec v Česku") == "Obec Semtěš"
    assert registered_domain("https://www.mesto-kromeriz.cz/fileadmin/x.json") == "mesto-kromeriz.cz"


def test_discovered_sources_loaded_and_deduplicated(tmp_path):
    p = tmp_path / "discovered.json"
    p.write_text(json.dumps({"sources": [
        {"fmt": "galileo", "url": "https://www.semtes.cz/urad/uredni-deska/", "publisher": "Obec Semtěš"},
        {"fmt": "galileo", "url": "https://www.obec-x.cz/urad/uredni-deska/", "publisher": "Obec X"},
        {"fmt": "imunis", "subdomain": "jine", "publisher": "Obec Jiná"},
        {"fmt": "ofn", "url": "https://www.vismo-obec.cz/opendata-uredni-deska", "publisher": "Obec V"},
    ]}), "utf-8")
    srcs = load_extra_sources(p)
    pubs = [s.publisher for s in srcs]
    assert pubs.count("Obec Semtěš") == 1  # ruční i nalezený zdroj → jen jednou
    assert {"Obec X", "Obec Jiná", "Obec V"} <= set(pubs)
    assert next(s for s in srcs if s.publisher == "Obec V").fmt == "ofn"


def test_cache_apply():
    cache.load_from_state([{"url": "https://x/1", "docs": [{"name": "A", "url": "https://x/a.pdf"}], "until": "2026-11-30"},
                           {"url": "https://x/2"}])
    info = {"url": "https://x/1"}
    assert cache.apply(info) and info["dokument"][0]["url"] == "https://x/a.pdf"
    assert info["relevantní_do"] == {"datum": "2026-11-30"}
    assert not cache.apply({"url": "https://x/2"})
    cache.DETAILS.clear()


def test_classify_galileo_other_paths():
    html = ('<a href="/obecni-urad/uredni-deska/">Úřední deska</a>'
            '<a href="/obecni-urad/uredni-deska/oznameni-123.html">x</a>'
            '<a href="/obecni-urad/uredni-deska/">Více</a> <a href="//www.igalileo.cz">Galileo</a>')
    assert classify_html(html, "https://www.obec.cz/") == {"fmt": "galileo", "url": "https://www.obec.cz/obecni-urad/uredni-deska/"}
    html2 = '<a href="https://www.obec2.cz/urad-2/uredni-deska-1/">Deska</a> igalileo.cz'
    assert classify_html(html2, "https://www.obec2.cz/")["url"] == "https://www.obec2.cz/urad-2/uredni-deska-1/"


def test_detail_budget():
    cache.reset_budget(2)
    assert cache.take_detail_slot() and cache.take_detail_slot() and not cache.take_detail_slot()
    cache.reset_budget()


def test_board_link_whitespace_stripped():
    info = classify_html('<a href="/uredni-deska\r\n">Deska</a>', "https://www.obec.cz/")
    assert info["board_link"] == "https://www.obec.cz/uredni-deska"


def test_classify_municipality_survives_invalid_url():
    import asyncio
    import httpx
    from uredni_desky.discover import classify_municipality

    def handler(request):
        return httpx.Response(200, text='<a href="/uredni-deska">x</a>')

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await classify_municipality(c, {"qid": "Q1", "name": "X", "types": "", "webs": ["https://bad\r.cz/"]},
                                               asyncio.Semaphore(1))

    res = asyncio.run(go())
    assert res["status"].startswith("chyba")


def test_board_link_by_anchor_text():
    from uredni_desky.discover import classify_html
    html = '<a href="/obec/">Obec</a><a href="/index.php?id=12&amp;x=1"><span>Úřední</span> deska</a>'
    assert classify_html(html, "https://www.obec.cz/")["board_link"] == "https://www.obec.cz/index.php?id=12&x=1"


def test_deska_asp_link_and_shortest_choice():
    from uredni_desky.discover import classify_html
    html = ('<a href="urad/user/deska/2024/x.pdf">PDF</a><a href="urad/deska.asp">Deska</a>'
            '<a href="/deskohrani-1.html">Deskovky</a>')
    assert classify_html(html, "https://www.obec.cz/obec/")["board_link"] == "https://www.obec.cz/obec/urad/deska.asp"
    html = '<a href="/uredni-deska/oznameni-o-necem-123.html">x</a><a href="/uredni-deska/">Úřední deska</a>'
    assert classify_html(html, "https://o.cz/")["board_link"] == "https://o.cz/uredni-deska/"
