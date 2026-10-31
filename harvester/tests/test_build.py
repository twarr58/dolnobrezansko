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
