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
