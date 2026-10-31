from datetime import datetime, timedelta, timezone

from uredni_desky.build import merge
from uredni_desky.fetch import FetchResult
from uredni_desky.nkod import BoardSource, parse_datasets

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)

SRC = BoardSource("b1", "https://example.org/ds/1", "Úřední deska", "Obec A", "https://example.org/a.json")


def payload(*titles):
    return {"informace": [{"název": {"cs": t}, "vyvěšení": {"datum": "2026-10-30"}} for t in titles]}


def as_json(items):
    return [i.to_json() for i in items]


def test_first_seen_kept_and_removed_marked():
    _, items0 = merge([SRC], {"b1": FetchResult(True, payload("A", "B"))}, [], T0)
    _, items1 = merge([SRC], {"b1": FetchResult(True, payload("A"))}, as_json(items0), T1)
    by_title = {i.title: i for i in items1}
    assert by_title["A"].first_seen == T0.isoformat(timespec="seconds")
    assert by_title["A"].last_seen == T1.isoformat(timespec="seconds")
    assert by_title["A"].removed is None
    assert by_title["B"].removed == T1.isoformat(timespec="seconds")


def test_failed_board_keeps_previous_items():
    _, items0 = merge([SRC], {"b1": FetchResult(True, payload("A"))}, [], T0)
    boards, items1 = merge([SRC], {"b1": FetchResult(False, error="HTTP 500")}, as_json(items0), T1)
    assert boards[0]["ok"] is False and boards[0]["error"] == "HTTP 500"
    assert [i.title for i in items1] == ["A"] and items1[0].removed is None


def test_invalid_format_reported_as_error():
    boards, items = merge([SRC], {"b1": FetchResult(True, {"informace": "x"})}, [], T0)
    assert boards[0]["ok"] is False and items == []


def test_old_archive_dropped():
    _, items0 = merge([SRC], {"b1": FetchResult(True, payload("A"))}, [], T0)
    _, items1 = merge([SRC], {"b1": FetchResult(True, payload())}, as_json(items0), T1)
    _, items2 = merge([SRC], {"b1": FetchResult(True, payload())}, as_json(items1), T1 + timedelta(days=400))
    assert items2 == []


def test_parse_datasets_prefers_json_distribution():
    raw = [{
        "iri": "https://example.org/ds/1",
        "title": {"cs": "Úřední deska"},
        "publisher": {"title": {"cs": "Obec A"}},
        "distribution": [{"accessURL": "https://example.org/deska.html"}, {"accessURL": "https://example.org/deska.json"}],
    }, {"iri": "https://example.org/ds/2", "distribution": []}]
    srcs = parse_datasets(raw)
    assert len(srcs) == 1 and srcs[0].access_url.endswith(".json") and srcs[0].publisher == "Obec A"


def test_display_name_for_shared_publisher():
    from uredni_desky.build import display_name
    s = BoardSource("x", "i", "Obsah úřední desky organizace Okresní soud v Náchodě", "Ministerstvo spravedlnosti", "u")
    assert display_name(s, True) == "Okresní soud v Náchodě"
    assert display_name(s, False) == "Ministerstvo spravedlnosti"
    assert display_name(BoardSource("x", "i", "Úřední deska", "Obec A", "u"), True) == "Obec A"


def test_write_outputs_layout(tmp_path):
    from uredni_desky.build import run_build
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    p = {"informace": [
        {"název": {"cs": "Nové"}, "vyvěšení": {"datum": "2026-10-30"}},
        {"název": {"cs": "Staré"}, "vyvěšení": {"datum": "2025-01-01"}},
        {"název": {"cs": "Nulové datum"}, "vyvěšení": {"datum": "0000-00-00"}},
    ]}
    meta = run_build([SRC], {"b1": FetchResult(True, p)}, tmp_path / "out", tmp_path / "state", now)
    import json
    recent = json.loads((tmp_path / "out" / "recent.json").read_text())
    board = json.loads((tmp_path / "out" / "boards" / "b1.json").read_text())
    from uredni_desky.build import load_state
    state = load_state(tmp_path / "state")
    assert [r["t"] for r in recent] == ["Nové"]
    assert len(board) == 3 and len(state) == 3
    assert next(i for i in board if i["title"] == "Nulové datum").get("posted") is None
    assert meta["items_recent"] == 1


def test_display_name_rules():
    from uredni_desky.build import display_name
    cases = {
        ("Úřední deska 2025", "Ministerstvo pro místní rozvoj"): "Ministerstvo pro místní rozvoj (2025)",
        ("Úřední deska úřadu: Katastrální úřad pro Zlínský kraj [770]", "ČÚZK"): "Katastrální úřad pro Zlínský kraj",
        ("Úřední deska městské části Brno-sever", "Statutární město Brno"): "Brno-sever",
        ("Úřední deska města Brna", "Statutární město Brno"): "Statutární město Brno",
        ("Úřad městského obvodu Plzeň 3 - elektronická úřední deska", "Statutární město Plzeň"): "Úřad městského obvodu Plzeň 3",
        ("Obsah úřední desky organizace Okresní soud v\xa0Teplicích", "Ministerstvo spravedlnosti"): "Okresní soud v Teplicích",
    }
    cases[("Úřední deska", "HLAVNÍ MĚSTO PRAHA")] = "Hlavní město Praha"
    for (title, pub), expected in cases.items():
        assert display_name(BoardSource("x", "i", title, pub, "u"), True) == expected


def test_is_municipal():
    from uredni_desky.nkod import is_municipal
    yes = ["Obec Dolní Břežany", "OBEC CHVALEČ", "Město Kolín", "MĚSTO NÁCHOD", "Městys Pecka",
           "Statutární město Brno", "HLAVNÍ MĚSTO PRAHA", "Městská část Praha 7", "Městský obvod Nová Bělá"]
    no = ["Ministerstvo spravedlnosti", "Jihomoravský kraj", "Vojenský újezd Libavá", "Ústavní soud",
          "Český úřad zeměměřický a katastrální"]
    assert all(is_municipal(BoardSource("x", "i", "t", p, "u")) for p in yes)
    assert not any(is_municipal(BoardSource("x", "i", "t", p, "u")) for p in no)


def test_parse_sparql_prefers_download_url_and_czech_title():
    from uredni_desky.nkod import parse_sparql
    rows = [
        {"ds": {"value": "https://data.gov.cz/zdroj/datové-sady/00235440/1"},
         "title": {"value": "Official board", "xml:lang": "en"},
         "pubName": {"value": "Město Kolín", "xml:lang": "cs"},
         "access": {"value": "https://www.kolin.cz/uredni-deska/"},
         "download": {"value": "https://www.kolin.cz/opendata/deska.json"}},
        {"ds": {"value": "https://data.gov.cz/zdroj/datové-sady/00235440/1"},
         "title": {"value": "Úřední deska města Kolín", "xml:lang": "cs"}},
    ]
    [s] = parse_sparql(rows)
    assert s.title == "Úřední deska města Kolín" and s.publisher == "Město Kolín"
    assert s.access_url.endswith("deska.json") and s.alt_urls == ("https://www.kolin.cz/uredni-deska/",)


def test_discover_extra_merges_three_queries():
    import asyncio
    import httpx
    from uredni_desky.nkod import discover_extra
    ds_new = "https://data.gov.cz/zdroj/datové-sady/00235440/1"
    ds_known = "https://data.gov.cz/zdroj/datové-sady/1/known"

    def handler(request):
        q = request.content.decode()
        if "VALUES" in q:
            rows = [{"ds": {"value": ds_new}, "download": {"value": "https://www.kolin.cz/deska.json"}}]
        elif "conformsTo" in q:
            rows = [{"ds": {"value": ds_known}, "title": {"value": "Úřední deska"}}]
        else:
            rows = [{"ds": {"value": ds_new}, "title": {"value": "Úřední deska města Kolín", "xml:lang": "cs"},
                     "pubName": {"value": "Město Kolín"}},
                    {"ds": {"value": "https://data.gov.cz/zdroj/podněty-na-data-k-otevření/R1"},
                     "title": {"value": "Úřední deska X"}}]
        return httpx.Response(200, json={"results": {"bindings": rows}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await discover_extra(c, {ds_known})

    [s] = asyncio.run(go())
    assert s.publisher == "Město Kolín" and s.access_url == "https://www.kolin.cz/deska.json"


def test_url_change_does_not_archive_item():
    def p(url):
        return {"informace": [{"název": {"cs": "Vyhláška"}, "url": url, "vyvěšení": {"datum": "2026-10-30"}}]}
    _, items0 = merge([SRC], {"b1": FetchResult(True, p("https://x/detail?id=1"))}, [], T0)
    _, items1 = merge([SRC], {"b1": FetchResult(True, p("https://x/files/1.pdf"))}, as_json(items0), T1)
    assert len(items1) == 1 and items1[0].removed is None
    assert items1[0].first_seen == T0.isoformat(timespec="seconds")


def test_stale_archived_twins_are_dropped():
    _, items0 = merge([SRC], {"b1": FetchResult(True, payload("A"))}, [], T0)
    twin = as_json(items0)[0] | {"id": "stary", "removed": T0.isoformat(timespec="seconds")}
    _, items1 = merge([SRC], {"b1": FetchResult(True, payload("A"))}, as_json(items0) + [twin], T1)
    assert [i.id for i in items1] == [items0[0].id]
