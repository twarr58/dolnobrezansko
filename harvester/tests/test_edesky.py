import asyncio
import logging
from urllib.parse import quote

import httpx
import pytest

from uredni_desky import edesky, region


def _xml(page=1, total_pages=1, docs=1):
    items = "".join(
        f"""<document created_at='2026-11-0{n + 1} 17:57:26 +0100' dashboard_id='2334' dashboard_name='Obec Okrouhlo'
            edesky_url='https://edesky.cz/dokument/{page}{n}' edesky_text_url='https://edesky.cz/dokument/{page}{n}.txt'
            name='ČEZ - plánovaná odstávka {page}{n}' orig_url='http://www.okrouhlo.cz/file/{page}{n}.pdf'>
          <attachments>
            <attachment contains_text='1' mimetype='application/pdf' name='Odstávka'
              url='https://edesky.cz/dokument/{page}{n}#priloha_99{page}{n}'
              orig_url='http://www.okrouhlo.cz/file/{page}{n}.pdf'>{quote('Odstávka elektřiny 13. 11. 2026 od 7:30 do 15:00')}</attachment>
          </attachments>
        </document>""" for n in range(docs))
    return f"""<?xml version='1.0' encoding='utf-8' ?>
<edesky_search_api version='1.0'><meta><timestamp>2026-11-05 07:46:14 +0100</timestamp><user>u</user>
<documents_count total="{docs}">{docs}</documents_count><page total_pages="{total_pages}">{page}</page></meta>
<documents>{items}</documents></edesky_search_api>""".encode()


def test_parse_documents_with_texts():
    edesky.TEXTS.clear()
    infos, pages = edesky.parse_documents(_xml(docs=2))
    assert pages == 1 and len(infos) == 2
    i = infos[0]
    assert i["název"]["cs"] == "ČEZ - plánovaná odstávka 10"
    assert i["vyvěšení"]["datum"] == "2026-11-01"
    assert i["url"] == "http://www.okrouhlo.cz/file/10.pdf" and i["iri"] == "https://edesky.cz/dokument/10"
    att = i["dokument"][0]["url"]
    assert att == "http://www.okrouhlo.cz/file/10.pdf"  # odkaz pro čtenáře: originál na webu obce
    assert edesky.TEXTS[att] == "Odstávka elektřiny 13. 11. 2026 od 7:30 do 15:00"
    assert edesky.ATTACHMENTS[att] == {"text_url": "https://edesky.cz/dokument/10.txt", "att_id": "9910",
                                       "doc_id": "10"}


def test_fetch_board_pages_and_key(monkeypatch):
    monkeypatch.setenv("EDESKY_API_KEY", "tajny-klic")
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        page = int(req.url.params["page"])
        return httpx.Response(200, content=_xml(page=page, total_pages=2, docs=2))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await edesky.fetch_board(c, "https://edesky.cz/desky/2334")

    out = asyncio.run(go())
    assert len(out["informace"]) == 4 and [p["page"] for p in seen] == ["1", "2"]
    p = seen[0]
    assert p["dashboard_id"] == "2334" and p["keywords"] == "*" and p["include_texts"] == "1"
    assert p["api_key"] == "tajny-klic"


def test_missing_key_is_clear_error(monkeypatch):
    monkeypatch.delenv("EDESKY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="EDESKY_API_KEY"):
        edesky.api_key()


def test_api_key_is_redacted_in_httpx_log(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "GET",
                                         httpx.URL("https://edesky.cz/api/v1/documents?keywords=*&api_key=tajny-klic"),
                                         "HTTP/1.1 200 OK")
    assert "tajny-klic" not in caplog.text and "api_key=***" in caplog.text


def test_attachment_text_used_when_download_blocked(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_FEDERATION_RULE_ID", raising=False)
    url = "https://edesky.cz/attachments/2026_w45/2334_1/odstavka"
    edesky.TEXTS[url] = "Odstávka elektřiny dne 13. 11. 2026 od 7:30 hod."
    items = [{"id": "i1", "board": "b1", "title": "ČEZ - plánovaná odstávka", "posted": "2026-11-01",
              "docs": [{"name": "Odstávka", "url": url}]}]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as c:
            from datetime import datetime, timezone
            return await region.enrich(c, items, tmp_path, ai_limit=0, now=datetime(2026, 11, 5, tzinfo=timezone.utc))

    docs, _ = asyncio.run(go())
    assert docs[url]["kind"] == "text" and "13. 11. 2026" in docs[url]["text"]


def _empty():
    return b"""<?xml version='1.0' encoding='utf-8' ?><edesky_search_api version='1.0'><meta>
<documents_count total="0">0</documents_count><page total_pages="0">1</page></meta><documents></documents></edesky_search_api>"""


def test_fallback_strategy_when_first_query_finds_nothing(monkeypatch, caplog):
    monkeypatch.setenv("EDESKY_API_KEY", "tajny-klic")
    seen = []

    def handler(req):
        p = dict(req.url.params)
        seen.append((p["search_with"], "created_from" in p))
        if p["search_with"] == "es":
            return httpx.Response(200, content=_xml(docs=3))
        return httpx.Response(200, content=_empty())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await edesky.fetch_board(c, "https://edesky.cz/desky/2334")

    with caplog.at_level(logging.INFO, logger="uredni_desky.edesky"):
        out = asyncio.run(go())
    assert len(out["informace"]) == 3 and seen == [("sql", True), ("es", True)]
    assert "API uvádí 0 dokumentů" in caplog.text and "tajny-klic" not in caplog.text


def test_alternative_attribute_names():
    xml = """<?xml version='1.0' encoding='utf-8' ?><edesky_search_api version='1.0'><meta>
<documents_count total="1">1</documents_count><page total_pages="1">1</page></meta><documents>
<document loaded_at='2026-11-04 10:00:00 +0100' url='https://edesky.cz/dokument/5' title='Rozpočtové opatření'/>
</documents></edesky_search_api>""".encode()
    page = edesky.parse_page(xml)
    assert page.raw == 1 and page.infos[0]["název"]["cs"] == "Rozpočtové opatření"
    assert page.infos[0]["vyvěšení"]["datum"] == "2026-11-04" and page.sample_attrs == ["loaded_at", "title", "url"]


TEXT_FILE = (
    "49866785 38492238 Mapa_uzavirky.pdf https://vestec.cz/wp-content/uploads/mapa.pdf Lucie Novakova\n"
    "Digitálně podepsal\n  2026-11-05 08:48:40 +0100 "
    "49866786 38492238 Oznameni.pdf https://vestec.cz/wp-content/uploads/oznameni.pdf  \n \n"
    "Městský úřad Černošice stanovuje přechodnou úpravu provozu dne 28. 11. 2026\n&lt;br&gt; sídlo: Mnichovice\n"
)


def test_split_text_file_per_attachment():
    parts = edesky.split_text_file(TEXT_FILE, "38492238")
    assert set(parts) == {"49866785", "49866786"}
    assert parts["49866786"].startswith("Městský úřad Černošice") and "<br>" not in parts["49866786"]
    assert "28. 11. 2026" not in parts["49866785"]


def test_edesky_attachment_uses_public_text_without_downloading_original(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_FEDERATION_RULE_ID", raising=False)
    edesky.ATTACHMENTS.clear(), edesky._TEXT_FILES.clear(), edesky.TEXTS.clear()
    orig = "https://vestec.cz/wp-content/uploads/oznameni.pdf"
    edesky.ATTACHMENTS[orig] = {"text_url": "https://edesky.cz/dokument/38492238.txt", "att_id": "49866786",
                                "doc_id": "38492238"}
    hits = []

    def handler(req):
        hits.append(str(req.url))
        if req.url.host == "edesky.cz":
            return httpx.Response(200, text=TEXT_FILE)
        return httpx.Response(403)

    items = [{"id": "i1", "board": "b1", "title": "OOP č. 423/2026", "posted": "2026-11-05",
              "docs": [{"name": "Oznameni.pdf", "url": orig}]}]

    async def go():
        from datetime import datetime, timezone
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await region.enrich(c, items, tmp_path, ai_limit=0, now=datetime(2026, 11, 5, tzinfo=timezone.utc))

    docs, _ = asyncio.run(go())
    assert hits == ["https://edesky.cz/dokument/38492238.txt"]  # originál na blokovaném webu se nezkouší
    assert docs[orig]["kind"] == "text" and docs[orig]["ext"] == "pdf" and "28. 11. 2026" in docs[orig]["text"]


def test_old_cached_edesky_page_is_replaced_by_text(tmp_path, monkeypatch):
    """Příloha uložená dřív jako stránka edesky.cz (HTML s menu) se hned nahradí rozpoznaným textem."""
    from datetime import datetime, timezone

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_FEDERATION_RULE_ID", raising=False)
    edesky.ATTACHMENTS.clear(), edesky._TEXT_FILES.clear(), edesky.TEXTS.clear()
    url = "https://edesky.cz/dokument/38492238#priloha_49866786"
    edesky.ATTACHMENTS[url] = {"text_url": "https://edesky.cz/dokument/38492238.txt", "att_id": "49866786",
                               "doc_id": "38492238"}
    region._write_gz(tmp_path / "docs.json.gz", {url: {
        "url": url, "ext": "html", "kind": "text", "chars": 2274, "sha": "old", "checked": "2026-11-05T00:00:00+00:00",
        "text": "(function(w,d,s,l,i){w[l]=w[l]||[];"}})
    hits = []

    def handler(req):
        hits.append(str(req.url))
        return httpx.Response(200, text=TEXT_FILE)

    items = [{"id": "i1", "board": "b1", "title": "OOP", "posted": "2026-11-05", "docs": [{"name": "OOP", "url": url}]}]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await region.enrich(c, items, tmp_path, ai_limit=0, now=datetime(2026, 11, 5, 12, tzinfo=timezone.utc))

    docs, _ = asyncio.run(go())
    assert hits == ["https://edesky.cz/dokument/38492238.txt"]
    assert docs[url]["src"] == "edesky" and "function(w" not in docs[url]["text"]
