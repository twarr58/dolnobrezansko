import asyncio
import json
from datetime import datetime, timezone

import httpx

from uredni_desky import region

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_resolve_sources_known_and_detected(tmp_path):
    reg = region.load_region()
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.host == "query.wikidata.org":
            body = {"results": {"bindings": [{"name": {"value": "Zlatníky-Hodkovice"}, "w": {"value": "https://www.jesenice.example/"}}]}}
            return httpx.Response(200, json=body)
        if req.url.host == "www.jesenice.example":
            return httpx.Response(200, html='<a href="https://jesenice.imunis.cz/edeska/">Úřední deska</a>')
        return httpx.Response(200, html="<html><body>Bez desky</body></html>")

    async def go():
        async with _client(handler) as c:
            return await region.resolve_sources(c, reg, tmp_path, NOW)

    sources, members = asyncio.run(go())
    by_name = {m["name"]: m for m in members}
    assert len(members) == 11
    assert by_name["Dolní Břežany"]["board"] and by_name["Psáry"]["board"]
    assert by_name["Jesenice"]["board"] and by_name["Okrouhlo"]["board"]  # desky zadané v region.json
    assert by_name["Zlatníky-Hodkovice"]["board"] and by_name["Zlatníky-Hodkovice"]["status"] == "deska nalezena automaticky"
    assert by_name["Vestec"]["board"]  # deska Galileo zadaná v region.json
    assert by_name["Březová-Oleško"]["board"] is None
    zl = next(s for s in sources if s.publisher == "Obec Zlatníky-Hodkovice")
    assert zl.fmt == "imunis" and zl.access_url == "https://jesenice.imunis.cz/edeska/rss.asp"
    # ID desky Dolních Břežan se nemění – zůstává archiv z dřívějších sběrů
    assert by_name["Dolní Břežany"]["board"] == "41897ce19d1b"

    # druhý běh: výsledek hledání se vezme z mezipaměti, bez dalších dotazů
    calls.clear()
    asyncio.run(go())
    assert calls == []
    saved = json.loads((tmp_path / "region_sources.json").read_text("utf-8"))
    assert saved["Zlatníky-Hodkovice"]["entry"] == {"fmt": "imunis", "subdomain": "jesenice"}


def test_build_output_merges_ai_summary():
    reg = region.load_region()
    members = [{"name": "Psáry", "publisher": "Obec Psáry", "board": "b1"}]
    boards = [{"id": "b1", "ok": True, "count": 1, "access_url": "https://x.cz/rss", "fmt": "rss"}]
    items = [{"id": "i1", "board": "b1", "title": "Oznámení o zahájení stavebního řízení", "posted": "2026-11-03",
              "docs": [{"name": "Výkres", "url": "https://x.cz/v.pdf"}]}]
    docs = {"https://x.cz/v.pdf": {"kind": "mapa", "ext": "pdf", "pages": 1}}
    summaries = {"i1": {"sig": "x", "ai": {
        "tldr": "Soused chce stavět dům.", "details": ["Dva byty, dvě podlaží."], "action": "Námitky do 8. 11.",
        "deadline": "2026-11-08", "event": "", "event_time": "", "where": "ul. Pražská", "category": "stavba",
        "importance": "vysoka", "importance_why": "Lhůta pro sousedy.", "impact": "Na louce vyroste dům.",
        "affects": "Sousedé ulice Pražská", "contact": "",
        "facts": [{"label": "Parcely", "value": "512/3"}, {"label": "", "value": "x"}],
        "dates": [{"date": "2026-11-08", "what": "konec lhůty"}, {"date": "neplatné", "what": "x"}],
        "docs": [{"index": 1, "what": "mapa s umístěním domu", "summary": "Výkres ukazuje dům u hranice."}]}}}
    out = region.build_output(reg, members, boards, items, docs, summaries, NOW)
    it = out["items"][0]
    assert it["o"] == "Psáry"
    assert it["s"]["tldr"] == "Soused chce stavět dům." and it["s"]["src"] == "claude"
    assert it["s"]["deadline"] == "2026-11-08" and it["s"]["where"] == "ul. Pražská"
    assert it["docs"][0] == {"n": "Výkres", "url": "https://x.cz/v.pdf", "k": "mapa", "x": "pdf", "pg": 1,
                             "w": "mapa s umístěním domu", "sum": "Výkres ukazuje dům u hranice."}
    assert it["s"]["bullets"] == ["Dva byty, dvě podlaží."] and it["s"]["importance"] == "vysoka"
    assert it["s"]["facts"] == [{"label": "Parcely", "value": "512/3"}]
    assert it["s"]["dates"] == [{"date": "2026-11-08", "what": "konec lhůty"}]
    assert it["s"]["affects"] == "Sousedé ulice Pražská" and "contact" not in it["s"]
    assert out["ai_model"] and out["members"][0]["connected"] is True


def test_enrich_downloads_each_document_once(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    hits = []

    def handler(req):
        hits.append(str(req.url))
        return httpx.Response(200, content=b"Zamer pronajmu pozemku", headers={"content-type": "text/plain"})

    items = [{"id": "i1", "board": "b1", "title": "Záměr", "posted": "2026-11-01",
              "docs": [{"name": "Záměr", "url": "https://x.cz/z.txt"}]}]

    async def go():
        async with _client(handler) as c:
            return await region.enrich(c, items, tmp_path, ai_limit=0, now=NOW)

    docs, _ = asyncio.run(go())
    assert docs["https://x.cz/z.txt"]["kind"] == "text"
    asyncio.run(go())
    assert hits == ["https://x.cz/z.txt"]


def test_enrich_loads_attachments_from_notice_page(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_FEDERATION_RULE_ID", raising=False)
    page = ('<html><nav><a href="/menu.pdf">Menu</a></nav><main><h1>Rozpočtové opatření č. 6/2026</h1>'
            '<p>Námitky lze podat do 20. 11. 2026.</p><a href="/obec-psary/soubor/77">Rozpočtové opatření</a></main></html>')

    def handler(req):
        if req.url.path.endswith("/vyveseni/390"):
            return httpx.Response(200, html=page)
        return httpx.Response(200, content=b"Rozpocet", headers={"content-type": "text/plain"})

    items = [{"id": "i1", "board": "b1", "title": "Rozpočtové opatření č. 6/2026", "posted": "2026-11-01",
              "url": "https://urednideska.alis.cz/obec-psary/vyveseni/390"}]

    async def go():
        async with _client(handler) as c:
            return await region.enrich(c, items, tmp_path, ai_limit=0, now=NOW)

    docs, _ = asyncio.run(go())
    assert items[0]["docs"] == [{"name": "Rozpočtové opatření", "url": "https://urednideska.alis.cz/obec-psary/soubor/77"}]
    assert "https://urednideska.alis.cz/obec-psary/soubor/77" in docs
    out = region.build_output(region.load_region(), [{"name": "Psáry", "publisher": "Obec Psáry", "board": "b1"}],
                              [{"id": "b1", "ok": True}], items, docs, {}, NOW)
    s = out["items"][0]["s"]
    assert s["deadline"] == "2026-11-20"  # lhůta z textu stránky oznámení
    assert out["items"][0]["docs"][0]["n"] == "Rozpočtové opatření"


def test_enrich_redoes_old_summary_versions(tmp_path, monkeypatch):
    from uredni_desky import ai

    calls = []

    class FakeSummarizer:
        used = tokens_in = tokens_out = 0
        def __init__(self, limit):
            pass

        def available(self):
            return True

        async def summarize(self, meta, docs):
            calls.append(meta["Název"])
            return {"tldr": "nové"}

    async def no_federation():
        return None

    monkeypatch.setattr(ai, "enabled", lambda: True)
    monkeypatch.setattr(ai, "Summarizer", FakeSummarizer)
    monkeypatch.setattr(ai, "prepare_github_federation", no_federation)
    items = [{"id": "old", "board": "b1", "title": "Staré", "posted": "2026-11-01"},
             {"id": "cur", "board": "b1", "title": "Aktuální", "posted": "2026-11-01"}]
    sig = {i["id"]: region._signature(i) for i in items}
    region._write_gz(tmp_path / "summaries.json.gz", {
        "old": {"sig": sig["old"], "ai": {"tldr": "staré"}},  # verze 1 (bez „v“)
        "cur": {"sig": sig["cur"], "ai": {"tldr": "hotové"}, "v": ai.VERSION}})

    async def go():
        async with _client(lambda req: httpx.Response(404)) as c:
            return await region.enrich(c, items, tmp_path, ai_limit=5, now=NOW)

    _, summaries = asyncio.run(go())
    assert calls == ["Staré"]
    assert summaries["old"]["ai"] == {"tldr": "nové"} and summaries["old"]["v"] == ai.VERSION
    assert summaries["cur"]["ai"] == {"tldr": "hotové"}


def _fake_ai(monkeypatch, calls):
    from uredni_desky import ai

    class Fake:
        used = tokens_in = tokens_out = 0

        def __init__(self, limit):
            pass

        def available(self):
            return True

        async def summarize(self, meta, docs):
            calls.append([d.get("bytes") for d in docs])
            return {"tldr": f"shrnutí {len(calls)}"}

    async def no_federation():
        return None

    monkeypatch.setattr(ai, "enabled", lambda: True)
    monkeypatch.setattr(ai, "Summarizer", Fake)
    monkeypatch.setattr(ai, "prepare_github_federation", no_federation)


def test_changed_attachment_on_same_url_triggers_new_summary(tmp_path, monkeypatch):
    from datetime import timedelta

    calls, hits = [], []
    content = {"v": b"Puvodni text"}

    def handler(req):
        hits.append(req.headers.get("if-none-match"))
        if req.headers.get("if-none-match") == content.get("etag"):
            return httpx.Response(304)
        return httpx.Response(200, content=content["v"], headers={"content-type": "text/plain", "etag": content["etag"]})

    content["etag"] = '"a"'
    _fake_ai(monkeypatch, calls)

    def run(now):
        items = [{"id": "i1", "board": "b1", "title": "Oznámení", "posted": "2026-11-01", "until": "2026-11-20",
                  "docs": [{"name": "Text", "url": "https://x.cz/a.txt"}]}]

        async def go():
            async with _client(handler) as c:
                return await region.enrich(c, items, tmp_path, ai_limit=5, now=now)
        return asyncio.run(go())

    _, s1 = run(NOW)
    assert len(calls) == 1 and calls[0] == [b"Puvodni text"]  # pro Claude se nestahuje podruhé
    assert len(hits) == 1 and s1["i1"]["sig"].startswith(region.SIG_PREFIX) and "updated_at" not in s1["i1"]

    run(NOW + timedelta(hours=2))  # do 24 h se příloha neověřuje ani neshrnuje
    assert len(hits) == 1 and len(calls) == 1

    run(NOW + timedelta(hours=25))  # po 24 h podmíněný dotaz – 304, beze změny
    assert hits[-1] == '"a"' and len(calls) == 1

    content.update(v=b"Novy text s jinou lhutou", etag='"b"')  # úřad vyměnil PDF na stejné adrese
    _, s4 = run(NOW + timedelta(hours=50))
    assert len(calls) == 2 and s4["i1"]["ai"] == {"tldr": "shrnutí 2"}
    assert s4["i1"]["updated_at"] and s4["i1"]["first_at"] == s1["i1"]["at"]


def test_signature_changes_with_deadline_and_content():
    it = {"title": "X", "posted": "2026-11-01", "until": "2026-11-20", "docs": [{"url": "https://x.cz/a.pdf"}]}
    base = region._signature(it, {"https://x.cz/a.pdf": {"sha": "1"}})
    assert base != region._signature({**it, "until": "2026-11-30"}, {"https://x.cz/a.pdf": {"sha": "1"}})
    assert base != region._signature(it, {"https://x.cz/a.pdf": {"sha": "2"}})
    assert base == region._signature(dict(it), {"https://x.cz/a.pdf": {"sha": "1"}})


def test_old_signature_format_is_not_marked_updated(tmp_path, monkeypatch):
    calls = []
    _fake_ai(monkeypatch, calls)
    items = [{"id": "i1", "board": "b1", "title": "Oznámení", "posted": "2026-11-01"}]
    region._write_gz(tmp_path / "summaries.json.gz",
                     {"i1": {"sig": "abc123", "ai": {"tldr": "staré"}, "v": 2, "at": "2026-11-01T10:00:00+00:00"}})

    async def go():
        async with _client(lambda req: httpx.Response(404)) as c:
            return await region.enrich(c, items, tmp_path, ai_limit=5, now=NOW)

    _, s = asyncio.run(go())
    assert len(calls) == 1 and "updated_at" not in s["i1"] and s["i1"]["first_at"] == "2026-11-01T10:00:00+00:00"


def test_output_marks_pending_when_notice_changed_without_new_summary():
    it = {"id": "i1", "board": "b1", "title": "X", "posted": "2026-11-01", "_sig": "s2:new"}
    s = region.item_description(it, [], {"sig": "s2:old", "ai": {"tldr": "staré"}, "at": "2026-11-02T09:00:00+00:00",
                                         "updated_at": "2026-11-03T09:00:00+00:00"})
    assert s["pending"] is True and s["summarized"] == "2026-11-02" and s["updated"] == "2026-11-03"


def test_enrich_downloads_attachments_via_proxy_for_blocked_hosts(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_FEDERATION_RULE_ID", raising=False)
    direct, proxied = [], []

    def blocked(req):
        direct.append(str(req.url))
        return httpx.Response(403)

    def proxy(req):
        proxied.append(str(req.url))
        return httpx.Response(200, content=b"Text prilohy", headers={"content-type": "text/plain"})

    items = [{"id": f"i{n}", "board": "b1", "title": f"Oznámení {n}", "posted": "2026-11-01",
              "docs": [{"name": "Text", "url": f"https://vestec.cz/soubor/{n}.txt"}]} for n in range(3)]

    async def go():
        async with _client(blocked) as c, httpx.AsyncClient(transport=httpx.MockTransport(proxy)) as p:
            return await region.enrich(c, items, tmp_path, ai_limit=0, now=NOW, fallback=p)

    docs, _ = asyncio.run(go())
    assert all(docs[f"https://vestec.cz/soubor/{n}.txt"]["kind"] == "text" for n in range(3))
    assert len(proxied) == 3 and len(direct) == 1  # po prvním odmítnutí už rovnou přes proxy
