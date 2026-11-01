import json

from uredni_desky.geo import attach


def test_attach_by_qid_publisher_and_domain(tmp_path):
    obce = [
        {"qid": "Q1", "publisher": "Obec Semtěš", "name": "Semtěš", "area": "Kutná Hora",
         "web": "https://www.semtes.cz/", "lat": 49.95, "lon": 15.53},
        {"qid": "Q2", "publisher": "Obec Lhota", "name": "Lhota", "area": "Plzeň-jih", "web": "https://lhota.cz", "lat": 49.5, "lon": 13.4},
        {"qid": "Q3", "publisher": "Obec Lhota", "name": "Lhota", "area": "Zlín", "web": "https://lhota-zl.cz", "lat": 49.2, "lon": 17.7},
        {"qid": "Q4", "publisher": "Město Kroměříž", "name": "Kroměříž", "area": "Kroměříž", "web": "https://www.kromeriz.eu", "lat": 49.298, "lon": 17.393},
    ]
    p = tmp_path / "obce.json"
    p.write_text(json.dumps(obce), "utf-8")
    boards = [
        {"publisher": "Obec Lhota", "qid": "Q3", "access_url": "https://x.cz"},
        {"publisher": "Obec Lhota", "access_url": "https://lhota.cz/deska.json"},
        {"publisher": "Obec Lhota", "access_url": "https://e-deska.cz/lhota"},  # nejednoznačné
        {"publisher": "Kroměříž", "access_url": "https://edeska.kromeriz.eu/x.json"},
        {"publisher": "Obec Semtěš", "access_url": "https://cms.example.org/semtes"},
    ]
    assert attach(boards, p) == 4
    assert (boards[0]["lat"], boards[0]["area"]) == (49.2, "Zlín")
    assert boards[1]["area"] == "Plzeň-jih"
    assert "lat" not in boards[2]
    assert boards[3]["lon"] == 17.393 and boards[4]["lat"] == 49.95
