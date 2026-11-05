import io
import zipfile

from uredni_desky import ai, documents
from uredni_desky.digest import clean_title, describe, find_where, parse_date


def test_parse_date_numeric_and_month_name():
    assert parse_date("dne 12. 11. 2026").isoformat() == "2026-11-12"
    assert parse_date("do 3. prosince 2026").isoformat() == "2026-12-03"
    assert parse_date("bez data") is None


def test_clean_title_strips_boilerplate_and_caps():
    assert clean_title("Veřejná vyhláška - Oznámení o zahájení řízení, č.j. MěÚ/123/2026") == \
        "Oznámení o zahájení řízení"
    assert clean_title("ZÁMĚR PRONÁJMU POZEMKU") == "Záměr pronájmu pozemku"


def test_construction_with_objection_deadline():
    text = ("Stavební úřad oznamuje zahájení stavebního řízení na stavbu rodinného domu na pozemku "
            "parc. č. 512/3 a 512/4 v k. ú. Psáry. Účastníci mohou uplatnit své námitky nejpozději "
            "do 8. 11. 2026, jinak k nim nebude přihlédnuto.")
    s = describe("Oznámení o zahájení stavebního řízení", "2026-11-03", "2026-11-19",
                 [{"kind": "text", "text": text}])
    assert s["cat"] == "stavba"
    assert s["deadline"] == "2026-11-08"
    assert s["action"] == "Námitky můžeš podat do 8. 11. 2026."
    assert s["where"] == "k. ú. Psáry · parc. č. 512/3, 512/4"


def test_council_meeting_event_and_time():
    text = "Zasedání zastupitelstva obce Zvole se koná dne 7. 11. 2026 v 18:00 hod. v sále obecního úřadu."
    s = describe("Informace o konání zasedání zastupitelstva", "2026-10-30", None, [{"kind": "text", "text": text}])
    assert s["cat"] == "zastupitelstvo"
    assert s["event"] == "2026-11-07" and s["time"] == "18:00"
    assert "7. 11. 2026 v 18:00" in s["action"]


def test_road_closure_range_and_map():
    text = "Úplná uzavírka silnice III/0031 od 11. 11. do 22. 11. 2026. Objízdná trasa dle přiložené mapy."
    s = describe("Stanovení přechodné úpravy provozu – uzavírka", "2026-11-04", None,
                 [{"kind": "text", "text": text}, {"kind": "mapa", "text": ""}])
    assert s["cat"] == "doprava"
    assert (s["event"], s["event_end"]) == ("2026-11-11", "2026-11-22")
    assert "V příloze je mapa nebo výkres." in s["bullets"]


def test_relative_deadline_for_public_notice():
    text = "Doručuje se veřejnou vyhláškou. Námitky lze podat ve lhůtě 10 dnů ode dne doručení."
    s = describe("Oznámení o zahájení řízení", "2026-11-01", None, [{"kind": "text", "text": text}])
    assert s["deadline"] == "2026-11-26"  # 1. 11. + 15 dní doručení + 10 dní
    assert s["action"].startswith("Námitky můžeš podat do přibližně")


def test_unknown_notice_has_no_action():
    s = describe("Výroční zpráva o poskytování informací", "2026-02-01", None, [])
    assert s["cat"] == "ostatni" and s["action"] == ""


def test_where_without_matches():
    assert find_where("Bez místa.") is None


def _docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


def test_docx_text_and_sniff():
    info = documents.analyze(_docx("Záměr pronájmu pozemku parc. č. 77"), "https://x.cz/soubor?id=5")
    assert info.ext == "docx" and info.kind == "text"
    assert "parc. č. 77" in info.text


def _blank_pdf(width: float, height: float) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=width, height=height)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_without_text_is_scan_or_map(monkeypatch):
    monkeypatch.setattr(documents, "_ocr_pdf", lambda content, pages: "")
    scan = documents.analyze(_blank_pdf(595, 842), "https://x.cz/a.pdf", "Oznámení")
    assert (scan.ext, scan.kind, scan.pages, scan.ocr) == ("pdf", "sken", 1, False)
    big = documents.analyze(_blank_pdf(1684, 1191), "https://x.cz/b.pdf", "Příloha")
    assert big.kind == "mapa"
    named = documents.analyze(_blank_pdf(595, 842), "https://x.cz/c.pdf", "Situační výkres")
    assert named.kind == "mapa"


def test_ai_content_sends_small_pdf_as_document_and_large_as_text():
    pdf = _blank_pdf(595, 842)
    content = ai.build_content({"Název": "X"}, [
        {"name": "a", "ext": "pdf", "pages": 2, "bytes": pdf, "text": ""},
        {"name": "b", "ext": "pdf", "pages": 300, "bytes": pdf, "text": "dlouhý text"},
    ])
    kinds = [c["type"] for c in content]
    assert kinds.count("document") == 1
    assert any("dlouhý text" in c.get("text", "") for c in content)
    text_only = ai.build_content({"Název": "X"}, [{"name": "a", "ext": "pdf", "pages": 2, "bytes": pdf,
                                                   "text": "t"}], text_only=True)
    assert all(c["type"] == "text" for c in text_only)


def test_ai_schema_is_strict():
    assert ai.SCHEMA["additionalProperties"] is False
    assert set(ai.SCHEMA["required"]) == set(ai.SCHEMA["properties"])


def test_summarizer_request_and_response(monkeypatch):
    import asyncio
    import json as _json

    import anthropic
    import httpx2

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = {}
    answer = {"tldr": "Soused staví dům.", "bullets": [], "action": "", "deadline": "", "event": "", "event_time": "",
              "where": "", "category": "stavba", "docs": []}

    def handler(req):
        seen["body"] = _json.loads(req.content)
        seen["beta"] = req.headers.get("anthropic-beta")
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": ai.MODEL,
            "content": [{"type": "text", "text": _json.dumps(answer)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}})

    s = ai.Summarizer(limit=1)
    s.client = anthropic.AsyncAnthropic(api_key="test", http_client=httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler)))
    out = asyncio.run(s.summarize({"Název": "X"}, []))
    assert out == answer
    body = seen["body"]
    assert body["model"] == ai.MODEL and body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in seen["beta"]
    assert asyncio.run(s.summarize({"Název": "Y"}, [])) is None  # limit za běh vyčerpán


def test_empty_api_key_does_not_shadow_federation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_FEDERATION_RULE_ID", "fdrl_x")
    import os

    assert ai.enabled() is True
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_disabled_without_credentials(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(var, raising=False)
    assert ai.enabled() is False


def test_github_federation_writes_token_file(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_FEDERATION_RULE_ID", "fdrl_x")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://gha.example/token?x=1")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "t")
    monkeypatch.setenv("ANTHROPIC_IDENTITY_TOKEN_FILE", str(tmp_path / "jwt"))

    async def fake_token():
        return "jwt-1"

    monkeypatch.setattr(ai, "_github_oidc_token", fake_token)

    async def go():
        task = await ai.prepare_github_federation()
        task.cancel()

    asyncio.run(go())
    assert (tmp_path / "jwt").read_text() == "jwt-1"
