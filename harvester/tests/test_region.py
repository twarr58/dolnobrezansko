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
            body = {"results": {"bindings": [{"name": {"value": "Jesenice"}, "w": {"value": "https://www.jesenice.example/"}}]}}
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
    assert by_name["Jesenice"]["board"] and by_name["Jesenice"]["status"] == "deska nalezena automaticky"
    assert by_name["Okrouhlo"]["board"] is None and by_name["Okrouhlo"]["status"] == "web obce nenalezen"
    assert by_name["Vestec"]["board"]  # deska Galileo zadaná v region.json
    assert by_name["Březová-Oleško"]["board"] is None
    jes = next(s for s in sources if s.publisher == "Město Jesenice")
    assert jes.fmt == "imunis" and jes.access_url == "https://jesenice.imunis.cz/edeska/rss.asp"
    # ID desky Dolních Břežan se nemění – zůstává archiv z dřívějších sběrů
    assert by_name["Dolní Břežany"]["board"] == "41897ce19d1b"

    # druhý běh: výsledek hledání se vezme z mezipaměti, bez dalších dotazů
    calls.clear()
    asyncio.run(go())
    assert calls == []
    saved = json.loads((tmp_path / "region_sources.json").read_text("utf-8"))
    assert saved["Jesenice"]["entry"] == {"fmt": "imunis", "subdomain": "jesenice"}


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
