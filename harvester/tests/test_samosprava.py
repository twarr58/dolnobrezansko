import asyncio
import json
from datetime import datetime, timezone

import httpx

from uredni_desky import documents, samosprava as S

NOW = datetime(2026, 11, 6, 9, tzinfo=timezone.utc)


def _client(routes: dict, hits: list | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        if hits is not None:
            hits.append(str(req.url))
        body = routes.get(str(req.url))
        if body is None:
            return httpx.Response(404)
        if isinstance(body, bytes):
            return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})
        return httpx.Response(200, html=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_dates_from_titles_and_files():
    assert S._date("Zápis ze zasedání a usnesení Rady obce Zvole 13.08.2026") == "2026-08-13"
    assert S._date("16. 9. 2026 - Zápis z jednání zastupitelstva č. 4/2026") == "2026-09-16"
    assert S._date("Vypis-rada-59_schuze-2026_10_07_na-web.pdf") == "2026-10-07"
    assert S._date("261014_usneseni_RO_27_2026.doc") == "2026-10-14"
    assert S._date("Zápis č. 12 ze dne 25 11 2024.pdf") == "2024-11-25"
    assert S._date("usnesení č. 4/2026") == ""


def test_files_listing_current_year_only_and_titles():
    page = """<html><body><nav><a href="/x/old.pdf">Výpis usnesení 2026 v menu</a></nav><h1>Usnesení rady</h1>
      <a href="/up/2026/10/Vypis-rada-59_schuze-2026_10_07_na-web.pdf">Prohlédnout</a>
      <a href="/up/2026/10/Vypis-rada-59_schuze-2026_10_07_na-web.pdf">Stáhnout</a>
      <a href="/up/2025/12/Vypis-rada-40_schuze-2025_12_01_na-web.pdf">Stáhnout</a>
      <a href="/files/4-2026.pdf">usnesení č. 4/2026 (203 kB)</a></body></html>"""

    async def go():
        async with _client({"https://obec.cz/rada/": page}) as c:
            cfg = {"body": "rada", "fmt": "files", "url": "https://obec.cz/rada/", "match": "vypis|usnesení"}
            return await S.list_documents(S.Fetcher(c, None), cfg, {2026})

    docs = asyncio.run(go())
    assert [(d["title"], d["date"], d["kind"]) for d in docs] == [
        ("Usnesení rady – 59. schůze", "2026-10-07", "usneseni"),
        ("usnesení č. 4/2026", "", "usneseni")]  # menu před <h1> se nepočítá, loňské pryč, bez duplicit


def test_vismo_years_picks_year_page_from_content_not_menu():
    index = """<html><body><nav><a href="https://obec.cz/usneseni-zastupitelstva-2026">Usnesení ze zasedání 2026</a></nav>
      <h1>Usnesení ze zasedání Rady</h1><a href="https://obec.cz/usneseni-rady-2026">Usnesení ze zasedání 2026</a>
      <a href="https://obec.cz/usneseni-rady-2025">Usnesení ze zasedání 2025</a></body></html>"""
    year = """<html><body><h1>Usnesení ze zasedání 2026</h1>
      <a href="https://obec.cz/file/1">09-R-26 usnesení.doc doc 54 kB</a>
      <a href="https://obec.cz/file/2">09-R-26 usnesení - Příloha č. 1.docx docx 16 kB</a>
      <a href="https://obec.cz/page-zasedani">24.9.2026_Zápis ze zasedání</a></body></html>"""
    meeting = """<html><body><h1>Zápis</h1><a href="https://obec.cz/file/9">Zápis 24.9.2026 [PDF, 200 kB]</a></body></html>"""

    async def go():
        routes = {"https://obec.cz/rada": index, "https://obec.cz/usneseni-rady-2026": year,
                  "https://obec.cz/page-zasedani": meeting}
        async with _client(routes) as c:
            cfg = {"body": "rada", "fmt": "vismo_years", "url": "https://obec.cz/rada", "match": "usnesen|zápis"}
            return await S.list_documents(S.Fetcher(c, None), cfg, {2026})

    docs = asyncio.run(go())
    assert [d["url"] for d in docs] == ["https://obec.cz/file/1", "https://obec.cz/file/9"]
    assert docs[0]["title"] == "09-R-26 usnesení" and docs[1]["date"] == "2026-09-24" and docs[1]["kind"] == "zapis"


def test_pages_with_files_or_text():
    listing = """<html><body><h1>Usnesení rady</h1>
      <a href="https://psary.cz/usneseni/rada-27-2026/">Usnesení rady obce Psáry č. 27-2026</a>
      <a href="https://psary.cz/usneseni/rada-3-2025/">Usnesení rady obce Psáry č. 3-2025</a>
      <a href="https://psary.cz/usneseni/zo-1-2026/">Usnesení a zápis ze zasedání zastupitelstva č. 1 ze dne 2.11.2026</a></body></html>"""
    page_doc = """<html><body><h1>Usnesení rady obce Psáry č. 27-2026</h1><p>Datum: 14.10.2026</p>
      <a href="https://psary.cz/wp-content/uploads/2026/11/261014_usneseni_RO_27_2026.doc">261014_usneseni_RO_27_2026 DOC 71 KB</a></body></html>"""
    page_text = "<html><body><h1>Usnesení č. 1</h1><main>" + "Zastupitelstvo obce schvaluje rozpočet. " * 20 + "</main></body></html>"

    async def go():
        routes = {"https://psary.cz/usneseni-rady/": listing, "https://psary.cz/usneseni/rada-27-2026/": page_doc,
                  "https://psary.cz/usneseni/zo-1-2026/": page_text}
        async with _client(routes) as c:
            cfg = {"body": "rada", "fmt": "pages", "url": "https://psary.cz/usneseni-rady/", "match": "/usneseni/"}
            return await S.list_documents(S.Fetcher(c, None), cfg, {2026})

    docs = asyncio.run(go())
    assert docs[0]["url"].endswith("261014_usneseni_RO_27_2026.doc") and docs[0]["date"] == "2026-10-14"
    assert docs[0]["title"] == "Usnesení rady obce Psáry č. 27-2026"
    assert docs[1]["url"] == "https://psary.cz/usneseni/zo-1-2026/" and docs[1]["html"] and docs[1]["date"] == "2026-11-02"
    assert len(docs) == 2  # loňské usnesení se neotevírá


def test_munipolis_files_from_script():
    html = ('<script>window.__x="\\u003Ca href=\\"https://admin-storage.munipolis.com/3060/custom_pages/a.pdf\\" download>'
            '\\u003Csvg>\\u003C/svg>\\n  \\u003Cspan>Zápis č. 5-2026 ze dne 24.09.2026.pdf\\u003C/span>\\u003C/a>"</script>')
    assert S.parse_munipolis(html, "https://obec.cz/") == [
        ("https://admin-storage.munipolis.com/3060/custom_pages/a.pdf", "Zápis č. 5-2026 ze dne 24.09.2026.pdf")]


class FakeSummarizer:
    def __init__(self):
        self.calls = []

    def available(self):
        return True

    async def summarize_custom(self, content, system, schema):
        self.calls.append(content)
        return {"date": "2026-10-14", "number": "27/2026", "tldr": "Rada schválila opravu chodníku.",
                "points": [{"verb": "schvaluje", "text": "Opravu chodníku v ulici Pražská za 1,2 mil. Kč.",
                            "category": "doprava", "amount": "1 200 000 Kč", "importance": "vysoka"}]}


def test_collect_downloads_once_and_summarizes_once(tmp_path):
    listing = """<html><body><h1>Usnesení</h1>
      <a href="https://obec.cz/files/usneseni-14.10.2026.pdf">Usnesení rady 14.10.2026</a></body></html>"""
    pdf = b"%PDF-1.4 fake"
    hits = []
    members = [{"name": "Psáry", "council": [{"body": "rada", "fmt": "files", "url": "https://obec.cz/rada", "match": "usnesen"}]}]

    def analyze(content, url, name="", ctype=""):
        info = documents.DocInfo(url=url, ext="pdf", pages=1)
        info.full_text = "Rada obce schvaluje opravu chodníku. " * 30
        return info

    import uredni_desky.documents as docs_mod
    orig = docs_mod.analyze
    docs_mod.analyze = analyze
    try:
        fake = FakeSummarizer()

        async def go():
            async with _client({"https://obec.cz/rada": listing, "https://obec.cz/files/usneseni-14.10.2026.pdf": pdf}, hits) as c:
                return await S.collect(c, members, tmp_path, NOW, None, fake)

        out = asyncio.run(go())
        meeting = out["meetings"][0]
        assert meeting["o"] == "Psáry" and meeting["body"] == "rada" and meeting["date"] == "2026-10-14"
        assert meeting["tldr"] == "Rada schválila opravu chodníku." and meeting["points"][0]["v"] == "schvaluje"
        assert meeting["points"][0]["amt"] == "1 200 000 Kč" and meeting["src"] == "claude"
        assert out["sources"] == [{"obec": "Psáry", "body": "rada", "url": "https://obec.cz/rada", "ok": True, "n": 1}]
        assert len(fake.calls) == 1
        saved = json.loads((tmp_path / "council.json").read_text("utf-8"))
        assert not any(k.startswith("_") for d in saved["docs"].values() for k in d)  # obsah souboru se neukládá

        # další běh: seznam je čerstvý, dokument stažený a shrnutý → nic se nestahuje ani neshrnuje
        hits.clear()
        out2 = asyncio.run(go())
        assert hits == [] and len(fake.calls) == 1 and out2["meetings"][0]["tldr"]
    finally:
        docs_mod.analyze = orig


def test_doc_text_from_old_word():
    text = "Souhrnné usnesení z jednání Rady obce usnesení číslo 09/R/2026 ze dne 23.02.2026 schvaluje účetní závěrku"
    raw = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504 + (text * 3).encode("utf-16le") + b"\x00\x00" + \
        "\nStandardní písmo odstavce".encode("utf-16le")
    info = documents.analyze(raw, "https://obec.cz/file/1", "usnesení.doc")
    assert info.ext == "doc" and "účetní závěrku" in info.full_text and "Standardní písmo" not in info.full_text


def test_title_from_file():
    assert S._title_from_file("vypis-usneseni-z-22_zasedani_na-web.pdf", "zastupitelstvo", "usneseni") == \
        "Usnesení zastupitelstva – 22. zasedání"
    assert S._title_from_file("Zapis_c_4_2026.pdf", "zastupitelstvo", "zapis") == "Zápis zastupitelstva č. 4/2026"
    assert S._title_from_file("dokument.pdf", "rada", "usneseni") == "Usnesení rady"


def test_pages_skip_share_links_and_take_one_file_per_meeting(monkeypatch):
    monkeypatch.setattr(S, "RETRY_DELAY", 0)
    listing = """<html><body><h1>Zápisy 2026</h1>
      <a href="https://www.facebook.com/sharer.php?u=https://obec.cz/usneseni-2026">Sdílet na Facebooku</a>
      <a href="http://www.obec.cz/AA/index.asp?ReturnUrl=/usneseni">Přihlásit se</a>
      <a href="https://jiny-web.cz/zapis-2026">Zápis jinde 2026</a>
      <a href="https://www.obec.cz/24-9-2026-zapis/d-1">24.9.2026_Zápis ze zasedání zastupitelstva</a></body></html>"""
    meeting = """<html><body><h1>Zápis</h1>
      <a href="https://www.obec.cz/assets/File.ashx?id=1">Prezenční listina</a>
      <a href="https://www.obec.cz/assets/File.ashx?id=2">Zápis ze zasedání 24.9.2026</a>
      <a href="https://www.obec.cz/assets/File.ashx?id=3">Hlasování</a></body></html>"""
    calls = {"n": 0}

    def handler(req):
        if str(req.url) == "https://www.obec.cz/zapisy":
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500)  # web krátce spadl – druhý pokus projde
            return httpx.Response(200, html=listing)
        if str(req.url) == "https://www.obec.cz/24-9-2026-zapis/d-1":
            return httpx.Response(200, html=meeting)
        raise AssertionError(f"neočekávaný dotaz {req.url}")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            cfg = {"body": "zastupitelstvo", "fmt": "pages", "url": "https://www.obec.cz/zapisy", "match": "zápis|usnesen"}
            return await S.list_documents(S.Fetcher(c, None), cfg, {2026})

    docs = asyncio.run(go())
    assert [d["url"] for d in docs] == ["https://www.obec.cz/assets/File.ashx?id=2"]
    assert docs[0]["date"] == "2026-09-24"
