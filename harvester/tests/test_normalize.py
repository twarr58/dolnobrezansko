import json
from pathlib import Path

import pytest

from uredni_desky.normalize import BoardFormatError, normalize_board, parse_payload

FIXTURE = Path(__file__).parent / "fixtures" / "deska.json"


@pytest.fixture
def items():
    return normalize_board("b1", json.loads(FIXTURE.read_text("utf-8")))


def test_skips_items_without_title(items):
    assert len(items) == 3


def test_basic_fields(items):
    first = next(i for i in items if i.cj)
    assert first.title.startswith("Oznámení o konání")
    assert first.posted == "2026-10-30"
    assert first.until == "2026-11-12"
    assert first.agendas == ["Samospráva"]
    assert first.docs[0].url.endswith("pozvanka.pdf")


def test_datetime_and_unspecified_until(items):
    second = next(i for i in items if i.sz)
    assert second.posted == "2026-10-29"
    assert second.until is None


def test_unsafe_urls_are_dropped(items):
    second = next(i for i in items if i.sz)
    assert [d.url for d in second.docs] == ["https://example.org/dokumenty/vyhlaska.pdf"]


def test_ids_are_stable():
    payload = json.loads(FIXTURE.read_text("utf-8"))
    a = {i.id for i in normalize_board("b1", payload)}
    b = {i.id for i in normalize_board("b1", payload)}
    assert a == b and len(a) == 3


def test_parse_payload_tolerates_bom():
    assert parse_payload(b'\xef\xbb\xbf{"informace": []}') == {"informace": []}


@pytest.mark.parametrize("raw", [b"<html>", b"", b"  \n", b"\xff\xfe", b"[1, 2]"])
def test_parse_payload_rejects_garbage(raw):
    with pytest.raises(BoardFormatError):
        parse_payload(raw)


def test_informace_must_be_list():
    with pytest.raises(BoardFormatError):
        normalize_board("b1", {"informace": {"název": "x"}})


def test_parse_payload_cp1250_fallback():
    raw = '{"informace": [{"název": {"cs": "Žádost"}}]}'.encode("cp1250")
    assert parse_payload(raw)["informace"][0]["název"]["cs"] == "Žádost"


def test_decode_zip():
    import io, zipfile
    from uredni_desky.normalize import decode_board
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ODTURD02.json", '{"informace": []}')
    data, warnings = decode_board(buf.getvalue())
    assert data == {"informace": []} and "ZIP" in warnings[0]


def test_decode_control_characters_in_strings():
    from uredni_desky.normalize import decode_board
    data, _ = decode_board('{"informace": [{"název": {"cs": "a\tb\nc"}}]}'.replace("\\t", "\t").encode())
    assert data["informace"][0]["název"]["cs"].startswith("a")


def test_decode_repairs_broken_json():
    from uredni_desky.normalize import decode_board
    raw = b'{"informace": [{"n\xc3\xa1zev": {"cs": "Oznameni "Tisk" obce"}}]}'
    data, warnings = decode_board(raw)
    assert len(data["informace"]) == 1 and any("opraven" in w for w in warnings)


def test_decode_list_root():
    from uredni_desky.normalize import decode_board
    data, warnings = decode_board(b'[{"informace": [{"n\xc3\xa1zev": {"cs": "A"}}]}]')
    assert len(data["informace"]) == 1


def test_decode_trailing_garbage():
    from uredni_desky.normalize import decode_board
    data, warnings = decode_board(b'{"informace": []}\n{"x": 1}')
    assert data == {"informace": []} and warnings


def test_short_error_text_is_reported():
    with pytest.raises(BoardFormatError, match="chybovou hlášku"):
        parse_payload(b"Oops, an error occurred! Request: d3755651692fc")
