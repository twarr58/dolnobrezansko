from uredni_desky.webpage import parse_board, pick_dates, to_informace

BASE = "https://www.obec.cz/uredni-deska/"

TABLE = """<html><body><nav><a href="/">Úvod</a><a href="/obec">Obec</a></nav>
<div class="sidebar"><h3>Aktuality</h3><ul><li><a href="/akt/1">Pouť 2026</a> 1. 9. 2026</li>
<li><a href="/akt/2">Svoz odpadu</a> 2. 9. 2026</li><li><a href="/akt/3">Hasiči</a> 3. 9. 2026</li></ul></div>
<main><table><tr><th>Název</th><th>Vyvěšeno</th><th>Sejmuto</th></tr>
<tr><td><a href="/files/rozpocet.pdf">Návrh rozpočtu 2027</a></td><td>20. 10. 2026</td><td>5. 11. 2026</td></tr>
<tr><td><a href="/files/zasedani.pdf">Pozvánka na zasedání zastupitelstva</a></td><td>25.10.2026</td><td>2.11.2026</td></tr>
<tr><td><a href="/files/vyhlaska.pdf">Veřejná vyhláška – oznámení o návrhu</a></td><td>27. 10. 2026</td><td>12. 11. 2026</td></tr>
<tr><td><a href="/files/dalsi.pdf">Oznámení o výběrovém řízení</a></td><td>28. 10. 2026</td><td>13. 11. 2026</td></tr>
</table></main><footer><a href="/kontakt">Kontakt</a> 2026</footer></body></html>"""

LIST = """<html><body><div id="content"><h1>Úřední deska</h1>
<div class="item"><h3><a href="/uredni-deska/1-rozpocet">Rozpočtové opatření č. 5</a></h3>
<p>Vyvěšeno: 01.10.2026 | Sejmuto: 31.12.2026</p><a href="/download/123">Stáhnout (PDF, 120 kB)</a></div>
<div class="item"><h3><a href="/uredni-deska/2-pozvanka">Pozvánka na zasedání</a></h3>
<p>Vyvěšeno: 15.10.2026 | Sejmuto: 30.10.2026</p></div>
<div class="item"><h3><a href="/uredni-deska/3-zamer">Záměr pronájmu pozemku</a></h3>
<p>Datum zveřejnění: 20.10.2026</p></div>
<div class="pager"><a href="?page=2">další</a></div></div></body></html>"""


def test_table_board_ignores_sidebar():
    items = parse_board(TABLE, BASE)
    assert [i.title for i in items][:2] == ["Návrh rozpočtu 2027", "Pozvánka na zasedání zastupitelstva"]
    assert len(items) == 4
    first = items[0]
    assert first.posted == "2026-10-20" and first.until == "2026-11-05"
    assert first.url == "https://www.obec.cz/files/rozpocet.pdf"
    assert first.docs and first.docs[0][1].endswith("rozpocet.pdf")


def test_div_list_with_labels_and_attachment():
    items = parse_board(LIST, BASE)
    assert len(items) == 3
    a = items[0]
    assert a.title == "Rozpočtové opatření č. 5"
    assert a.url == "https://www.obec.cz/uredni-deska/1-rozpocet"
    assert (a.posted, a.until) == ("2026-10-01", "2026-12-31")
    assert a.docs == [("Stáhnout (PDF, 120 kB)", "https://www.obec.cz/download/123")]
    assert items[2].posted == "2026-10-20" and items[2].until is None
    info = to_informace(a)
    assert info["vyvěšení"]["datum"] == "2026-10-01" and info["dokument"][0]["url"].endswith("/download/123")


def test_pick_dates_labels_and_order():
    assert pick_dates("Sejmuto: 5. 11. 2026, vyvěšeno 20. 10. 2026") == ("2026-10-20", "2026-11-05")
    assert pick_dates("20.10.2026 - 5.11.2026") == ("2026-10-20", "2026-11-05")
    assert pick_dates("bez data") == (None, None)


def test_no_board_on_plain_page():
    assert parse_board("<html><body><p>Ahoj 1. 1. 2026</p><a href='/x'>Odkaz</a></body></html>", BASE) == []


def test_volatile_params_and_subpages():
    from uredni_desky.webpage import _clean_url, subpages
    assert _clean_url("https://o.cz/d/x-1.html?kshow=2#a") == "https://o.cz/d/x-1.html"
    assert _clean_url("https://o.cz/d.php?id=5&utm_source=x") == "https://o.cz/d.php?id=5"
    html = ('<a href="/urad/uredni-deska/stavebni/">S</a><a href="/urad/uredni-deska/">D</a>'
            '<a href="/urad/">U</a><a href="/urad/uredni-deska/archiv-2019/">A</a><a href="/urad/uredni-deska/a/b/">X</a>')
    assert subpages(html, "https://o.cz/urad/uredni-deska/") == ["https://o.cz/urad/uredni-deska/stavebni/"]


def test_item_wrapped_in_link_and_date_in_title():
    html = """<main><div class="grid">
    <a class="card" href="/uredni-deska/abc"><h3>Pozvánka na zasedání dne 9.11.2026</h3><span>Vyvěšeno 29. 10. 2026</span><span>Do 31.12.2026</span></a>
    <a class="card" href="/uredni-deska/def"><h3>Rozpočtové opatření</h3><span>Vyvěšeno 27. 10. 2026</span></a>
    </div></main>"""
    items = parse_board(html, BASE)
    assert [(i.url, i.posted, i.until) for i in items] == [
        ("https://www.obec.cz/uredni-deska/abc", "2026-10-29", "2026-12-31"),
        ("https://www.obec.cz/uredni-deska/def", "2026-10-27", None)]


def test_form_wrapper_and_cp1250():
    html = ("<html><head><meta charset='windows-1250'></head><body class='has-sidebar'><form><table>"
            "<tr><td><a href='/d/1.pdf'>Oznámení o vyhlášení</a></td><td>1.10.2026</td></tr>"
            "<tr><td><a href='/d/2.pdf'>Záměr prodeje (120 kB)</a></td><td>2.10.2026</td></tr>"
            "</table></form></body></html>").encode("cp1250")
    items = parse_board(html, BASE)
    assert [i.title for i in items] == ["Oznámení o vyhlášení", "Záměr prodeje"]


def test_czech_month_names_and_card_title():
    assert pick_dates("Vyvěšeno 27. října 2026, sejmuto 1. prosince 2026") == ("2026-10-27", "2026-12-01")
    assert pick_dates("9. červenec 2026") == ("2026-07-09", None)
    html = """<ul><li><a href="/d/1"><h3>Pozvánka</h3><p>Vyvěšeno: 30. 10. 2026</p></a></li>
    <li><a href="/d/2"><h3>Opis výsledků</h3><p>Vyvěšeno: 19. 10. 2026</p></a></li></ul>"""
    assert [i.title for i in parse_board(html, BASE)] == ["Pozvánka", "Opis výsledků"]


def test_card_date_element_beats_date_in_title_and_aspnet_session():
    from uredni_desky.webpage import _clean_url
    html = """<div><a class="newsItem" href="/n/1"><div class="t">Zasedání dne 4.11.2026</div><div class="newsDate">27. říjen 2026</div></a>
    <a class="newsItem" href="/n/2"><div class="t">Usnesení ZO 28/26 ze dne 24.9.2026</div><div class="newsDate">30. září 2026</div></a></div>"""
    items = parse_board(html, BASE)
    assert [(i.title, i.posted) for i in items] == [("Zasedání dne 4.11.2026", "2026-10-27"),
                                                   ("Usnesení ZO 28/26 ze dne 24.9.2026", "2026-09-30")]
    assert _clean_url("https://x.cz/Ginis/App/UDE01/(S(krhsdzkwaca44abc))/Detail.aspx?id=5") == \
        "https://x.cz/Ginis/App/UDE01/Detail.aspx?id=5"


def test_single_future_date_is_until_and_date_titles_dropped():
    html = """<table><tr><td><a href="/d/1">Změna úředních hodin</a></td><td>8.10.2099</td></tr>
    <tr><td><a href="/d/2">Závěrečný účet</a></td><td>30.6.2099</td></tr>
    <tr><td><a href="/d/3">2026-12-31</a></td><td>2026-12-31</td></tr></table>"""
    items = parse_board(html, BASE)
    assert [(i.title, i.posted, i.until) for i in items] == [
        ("Změna úředních hodin", None, "2099-10-08"), ("Závěrečný účet", None, "2099-06-30")]
