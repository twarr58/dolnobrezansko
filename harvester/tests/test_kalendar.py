from datetime import date, datetime, timezone

from uredni_desky import kalendar as K

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
DATA = {
    "region": {"name": "Dolnobřežansko"},
    "members": [{"name": "Dolní Břežany", "slug": "dolni-brezany"}, {"name": "Zvole", "slug": "zvole"}],
    "events": {"items": [
        {"o": "Zvole", "t": "Lampionový průvod", "d": "2026-11-11", "time": "17:30", "place": "Náves, u hasičárny",
         "url": "https://zvole.cz/akce/1"},
        {"o": "Dolní Břežany", "t": "Vánoční trhy", "d": "2026-12-05", "end": "2026-12-06", "time": "10:00",
         "url": "https://dolnibrezany.cz/trhy"},
        {"o": "Zvole", "t": "Dávno pryč", "d": "2026-10-01", "url": "https://zvole.cz/akce/0"}]},
    "items": [
        {"id": "6b1ed21c092c35c7", "o": "Dolní Břežany", "t": "Oznámení o přerušení dodávky elektrické energie",
         "s": {"event": "2026-11-25", "time": "08:30", "where": "Lhota: Májová; Větrná", "tldr": "Bez proudu 8:30–14:30."}},
        {"id": "x", "o": "Zvole", "t": "Sejmuto", "r": "2026-11-01", "s": {"event": "2026-11-20"}},
        {"id": "y", "o": "Zvole", "t": "Bez termínu", "s": {}}],
}


def _unfold(text):
    return text.replace("\r\n ", "")


def test_files_per_region_and_municipality(tmp_path):
    assert K.write(DATA, tmp_path, NOW) == 3
    vse = (tmp_path / "kalendar" / "vse.ics").read_bytes().decode("utf-8")
    assert vse.startswith("BEGIN:VCALENDAR\r\n") and vse.endswith("END:VCALENDAR\r\n")
    assert vse.count("BEGIN:VEVENT") == 3 and "Dávno pryč" not in vse and "Sejmuto" not in vse
    assert all(len(line.encode("utf-8")) <= 75 for line in vse.split("\r\n"))  # zalomení řádků
    zvole = _unfold((tmp_path / "kalendar" / "zvole.ics").read_bytes().decode("utf-8"))
    assert zvole.count("BEGIN:VEVENT") == 1 and "X-WR-CALNAME:Zvole – akce a termíny" in zvole
    assert _unfold((tmp_path / "kalendar" / "dolni-brezany.ics").read_bytes().decode("utf-8")).count("BEGIN:VEVENT") == 2


def test_event_times_places_and_stable_uid(tmp_path):
    K.write(DATA, tmp_path, NOW)
    vse = _unfold((tmp_path / "kalendar" / "vse.ics").read_bytes().decode("utf-8"))
    assert "DTSTART;TZID=Europe/Prague:20261111T173000\r\nDTEND;TZID=Europe/Prague:20261111T193000" in vse
    assert "LOCATION:Náves\\, u hasičárny" in vse  # čárka escapovaná
    # vícedenní akce celodenně (konec výhradně o den později), čas začátku v popisu
    assert "DTSTART;VALUE=DATE:20261205\r\nDTEND;VALUE=DATE:20261207" in vse and "Začátek v 10:00." in vse
    assert "URL:https://twarr58.github.io/dolnobrezansko/#/o/6b1ed21c092c35c7" in vse
    assert "LOCATION:Lhota: Májová\\; Větrná" in vse
    uids = [line for line in vse.split("\r\n") if line.startswith("UID:")]
    K.write(DATA, tmp_path, datetime(2026, 11, 7, tzinfo=timezone.utc))
    again = _unfold((tmp_path / "kalendar" / "vse.ics").read_bytes().decode("utf-8"))
    assert uids == [line for line in again.split("\r\n") if line.startswith("UID:")]


def test_all_day_without_time_and_fold():
    ev = K.vevent("u", "Svoz", date(2026, 11, 30), stamp=NOW)
    assert "DTSTART;VALUE=DATE:20261130" in ev and "DTEND;VALUE=DATE:20261201" in ev
    long = K._fold("DESCRIPTION:" + "ž" * 100)
    assert all(len(p.encode("utf-8")) <= 75 for p in long.split("\r\n"))
    assert long.replace("\r\n ", "") == "DESCRIPTION:" + "ž" * 100


def test_single_event_files_and_cal_ids(tmp_path):
    import copy
    data = copy.deepcopy(DATA)
    (tmp_path / "kalendar" / "u").mkdir(parents=True)
    (tmp_path / "kalendar" / "u" / "stara.ics").write_text("x")
    K.write(data, tmp_path, NOW)
    ids = [e.get("cal") for e in data["events"]["items"]] + [i.get("cal") for i in data["items"]]
    assert ids[0] and ids[1] and ids[2] is None and ids[3] and ids[4] is None and ids[5] is None
    files = sorted(p.stem for p in (tmp_path / "kalendar" / "u").iterdir())
    assert files == sorted([ids[0], ids[1], ids[3]])  # stará akce smazaná
    one = (tmp_path / "kalendar" / "u" / f"{ids[0]}.ics").read_bytes().decode("utf-8")
    assert one.count("BEGIN:VEVENT") == 1 and "X-WR-CALNAME:Lampionový průvod" in one


def test_events_sharing_calendar_url_get_separate_files(tmp_path):
    first = {"o": "Zvole", "t": "Koncert", "d": "2026-11-11", "url": "https://obec.cz/akce"}
    second = {**first, "t": "Výstava"}
    data = {"events": {"items": [first, second]}}
    assert K.write(data, tmp_path, NOW) == 2
    ids = first["cal"], second["cal"]
    assert ids[0] != ids[1]
    root = tmp_path / "kalendar"
    assert (root / "vse.ics").read_text("utf-8").count("BEGIN:VEVENT") == 2
    for event in (first, second):
        assert f'SUMMARY:{event["t"]}' in (root / "u" / f'{event["cal"]}.ics').read_text("utf-8")
    data["events"]["items"].reverse()
    assert K.write(data, tmp_path, NOW) == 2
    assert (first["cal"], second["cal"]) == ids


def test_foreign_issuer_not_in_calendar(tmp_path):
    import copy
    data = copy.deepcopy(DATA)
    data["items"][0]["s"]["foreign"] = "Obec Průhonice"
    data["items"][0].pop("cal", None)  # sdílená DATA mohou mít „cal“ z předchozích testů
    K.write(data, tmp_path, NOW)
    assert "cal" not in data["items"][0]
