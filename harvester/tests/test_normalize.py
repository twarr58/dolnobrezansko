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


@pytest.mark.parametrize("raw", [b"<html>", b"[]", b"\xff\xfe"])
def test_parse_payload_rejects_garbage(raw):
    with pytest.raises(BoardFormatError):
        parse_payload(raw)


def test_informace_must_be_list():
    with pytest.raises(BoardFormatError):
        normalize_board("b1", {"informace": {"název": "x"}})
