import asyncio
import json
from datetime import datetime, timezone

import httpx

from uredni_desky import events

VISMO = {"result": {"items": [
    {"date": "2026-11-06T00:00:00+01:00", "events": [
        {"name": "Svoz bioodpadu", "fromTime": "13:00", "isStartDay": True, "isEndDay": False, "isAllDay": True,
         "isMultiDay": True, "v-id": 1, "v-viewUrl": "/svoz", "codeListItems": ""},
        {"name": "Kino", "fromTime": "19:00", "isStartDay": True, "isMultiDay": False, "isAllDay": True, "v-id": 2,
         "v-viewUrlAbsolute": "https://obec.cz/kino", "codeListItems": [{"name": "Kino", "isHiddenName": False}]},
        {"name": "Zrušeno", "IsCanceled": True, "isStartDay": True, "v-id": 3}]},
    {"date": "2026-11-07T00:00:00+01:00", "events": [
        {"name": "Svoz bioodpadu", "fromTime": "13:00", "isStartDay": False, "isEndDay": True, "isMultiDay": True, "v-id": 1}]},
    {"date": "2026-11-13T00:00:00+01:00", "events": [
        {"name": "Svoz bioodpadu", "fromTime": "13:00", "isStartDay": True, "isEndDay": False, "isMultiDay": True, "v-id": 1,
         "v-viewUrl": "/svoz"}]},
]}}


def test_vismo_joins_multi_day_and_keeps_repeats_apart():
    got = events.parse_vismo(VISMO, "https://obec.cz/kalendar")
    assert got == [
        {"t": "Svoz bioodpadu", "d": "2026-11-06", "time": "13:00", "url": "https://obec.cz/svoz", "end": "2026-11-07"},
        {"t": "Kino", "d": "2026-11-06", "time": "19:00", "url": "https://obec.cz/kino", "tags": ["Kino"]},
        {"t": "Svoz bioodpadu", "d": "2026-11-13", "time": "13:00", "url": "https://obec.cz/svoz"},
    ]


def test_rdc_list_with_ranges():
    html = """<ul>
      <li class="rdc-event" data-date="10/03/2026"><a href="/udalost/volby">3.10. - 4.10. Volby</a></li>
      <li class="rdc-event" data-date="10/04/2026"><a href="/udalost/volby">4.10. Volby</a></li>
      <li class="rdc-event" data-date="12/30/2026"><a href="/udalost/silvestr">30.12. - 2.1. Silvestr</a></li></ul>"""
    got = events.parse_rdc(html, "https://www.ohrobec.cz/volny-cas/udalosti")
    assert got == [
        {"t": "Volby", "d": "2026-10-03", "end": "2026-10-04", "url": "https://www.ohrobec.cz/udalost/volby"},
        {"t": "Silvestr", "d": "2026-12-30", "end": "2027-01-02", "url": "https://www.ohrobec.cz/udalost/silvestr"},
    ]


def test_article_list_date_time_place():
    html = """<div class="article"><div class="article-title"><a href="/udalosti/lampion/">Háčkovaný lampion</a></div>
      <div class="article-meta"><span class="article-meta-datetime datetime-udalost">sobota, 07. listopad 2026 | 16:30</span>Stará škola</div></div>
      <div class="article"><div class="article-title"><a href="/x/">Bez data</a></div><div class="article-meta">brzy</div></div>"""
    got = events.parse_article(html, "https://vranenadvltavou.cz/udalosti/")
    assert got == [{"t": "Háčkovaný lampion", "d": "2026-11-07", "time": "16:30", "place": "Stará škola",
                    "url": "https://vranenadvltavou.cz/udalosti/lampion/"}]


def test_collect_vismo_window_link_only_and_failure(tmp_path):
    seen = []

    def handler(req):
        seen.append(req.url)
        if req.url.host == "zle.cz":
            return httpx.Response(503)
        if req.url.path.startswith("/api/"):
            return httpx.Response(200, json=VISMO)
        return httpx.Response(200, text='<meta data-id-object="99"><div data-x="element-type-of-object-type-block-id=140">')

    members = [
        {"name": "A", "events": {"url": "https://obec.cz/kalendar", "fmt": "vismo"}},
        {"name": "B", "events": {"url": "https://zle.cz/akce", "fmt": "rdc"}},
        {"name": "C", "events": {"url": "https://c.cz/akce", "fmt": "link"}},
        {"name": "D"},
    ]
    now = datetime(2026, 11, 7, 8, tzinfo=timezone.utc)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await events.collect(client, members, tmp_path, now)

    out = asyncio.run(go())
    api = next(u for u in seen if u.path.startswith("/api/"))
    assert api.params["ObjectId"] == "99" and api.params["ElementTypeOfObjectTypeBlockId"] == "140"
    assert api.params["From"] == "2026-11-07T00:00:00"
    # akce z 6. 11. končící 7. 11. ještě platí, Kino z 6. 11. už ne
    assert [(e["o"], e["t"], e["d"]) for e in out["items"]] == [("A", "Svoz bioodpadu", "2026-11-06"),
                                                                ("A", "Svoz bioodpadu", "2026-11-13")]
    assert out["sources"] == [
        {"obec": "A", "url": "https://obec.cz/kalendar", "read": True, "ok": True, "n": 2},
        {"obec": "B", "url": "https://zle.cz/akce", "read": True, "ok": False, "n": 0},
        {"obec": "C", "url": "https://c.cz/akce", "read": False, "n": 0},
    ]
    assert "A" in json.loads((tmp_path / "events.json").read_text())


def test_linklist_psary_and_vestec():
    psary = """<h1>Kalendář akcí</h1><table>
      <tr><td><a href="https://psary.cz/udalosti/slavnosti-svetel-2/">Slavnosti světel</a></td><td></td><td>17:00 - 07.11.2026</td></tr>
      <tr><td><a href="https://psary.cz/udalosti/jana-poncarova/">Jana Poncarová v naší knihovně</a></td><td>Knihovna</td>
          <td>17:00 - 12.11.2026</td></tr>
      <tr><td><a href="https://psary.cz/udalosti/zajezd/">Zájezd adventní trhy</a></td><td>28.11.2026</td></tr></table>
      <a href="https://psary.cz/kalendar-seniori/">Aktivní senioři</a>"""
    got = events.parse_linklist(psary, "https://psary.cz/blizke-udalosti/", r"psary\.cz/udalosti/[a-z0-9-]+")
    assert got == [
        {"t": "Slavnosti světel", "d": "2026-11-07", "time": "17:00", "url": "https://psary.cz/udalosti/slavnosti-svetel-2/"},
        {"t": "Jana Poncarová v naší knihovně", "d": "2026-11-12", "time": "17:00", "place": "Knihovna",
         "url": "https://psary.cz/udalosti/jana-poncarova/"},
        {"t": "Zájezd adventní trhy", "d": "2026-11-28", "url": "https://psary.cz/udalosti/zajezd/"},
    ]
    vestec = """<article class="gallery-item"><a href="https://vestec.cz/events/farmarske-trhy-24/">Farmářské trhy 21.11.2026</a></article>
      <article class="gallery-item"><a href="https://vestec.cz/events/betlemske-svetlo-6/">Betlémské světlo 23.12.2026</a></article>"""
    got = events.parse_linklist(vestec, "https://vestec.cz/udalosti/", r"vestec\.cz/events/")
    assert [(e["t"], e["d"]) for e in got] == [("Farmářské trhy", "2026-11-21"), ("Betlémské světlo", "2026-12-23")]


def test_linklist_jesenice_place_after_date():
    html = """<h1>Kalendář akcí</h1>
      <a href="/strasidelna%2Dstezka/a-1684">Strašidelná stezka</a>
      <p>6.11.2026, 17:30 strašidelná stezka od Zdiměřické louky</p><p>Zveme všechny malé i velké na strašidelný podvečer!</p>
      <p>Typ akce:</p><a href="/ap/hledani=1&amp;typ_akce=1035">Dětské akce</a>
      <a href="/komunitni%2Dvysadba/a-1697">Komunitní výsadba</a><p>7.11.2026 9:00, Jesenice - Hodkovice</p>
      <a href="/kafe%2Dse%2Dstarostou/a-1702">Kafe se starostou</a><p>23.11.2026 16:00 - 23.11.2026 18:00, Obřadní síň</p>
      <a href="/adventni%2Dkoncert/a-1688">Adventní koncert ZUŠ</a><p>3.12.2026</p>"""
    got = events.parse_linklist(html, "https://www.mujesenice.cz/vismo/kalendar-akci.asp", r"/a-\d+$")
    assert [(e["t"], e["d"], e.get("time"), e.get("place")) for e in got] == [
        ("Strašidelná stezka", "2026-11-06", "17:30", "strašidelná stezka od Zdiměřické louky"),
        ("Komunitní výsadba", "2026-11-07", "09:00", "Jesenice - Hodkovice"),
        ("Kafe se starostou", "2026-11-23", "16:00", "Obřadní síň"),
        ("Adventní koncert ZUŠ", "2026-12-03", None, None),
    ]


def test_munipolis_cards():
    html = """<div data-test="timeline-item"><h2 data-test="timeline-item-title">Strašidelná BU-BU stezka ve Zlatníkách!</h2>
      <div data-test="event-info-published">Zveřejněno od: 4. 11. 2026 13:16</div>
      <div data-test="event-info-date">7. 11. 2026 17:30 - 7. 11. 2026 20:30</div>
      <div data-test="event-info-place">Zlatníky V remízku</div></div>
      <div data-test="timeline-item"><h2 data-test="timeline-item-title">Zájezd</h2>
      <div data-test="event-info-date">14. 11. 2026 09:30 - 15. 11. 2026 20:00</div></div>"""
    got = events.parse_munipolis(html, "https://zlatniky-hodkovice.cz/event-calendar")
    assert got == [
        {"t": "Strašidelná BU-BU stezka ve Zlatníkách!", "d": "2026-11-07", "time": "17:30", "place": "Zlatníky V remízku",
         "url": "https://zlatniky-hodkovice.cz/event-calendar"},
        {"t": "Zájezd", "d": "2026-11-14", "end": "2026-11-15", "time": "09:30", "url": "https://zlatniky-hodkovice.cz/event-calendar"},
    ]
