"""Volitelné shrnutí oznámení od Claude.

Zapne se API klíčem (ANTHROPIC_API_KEY), nebo bez klíče přes Workload Identity Federation
(ANTHROPIC_FEDERATION_RULE_ID, ANTHROPIC_ORGANIZATION_ID, ANTHROPIC_SERVICE_ACCOUNT_ID; v GitHub Actions
si token GitHubu obstará prepare_github_federation()).

Claude dostane metadata oznámení a přílohy: PDF přímo (přečte i mapy a skeny), obrázky jako obrázky,
ostatní jako vytažený text. Vrátí strukturovaný popis pro rychlé čtení.
Shrnutí se dělá jen u nových oznámení (mezipaměť ve stavu) a s limitem počtu za běh.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

from .digest import LABELS

log = logging.getLogger(__name__)

MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")
PDF_MAX_PAGES = 100  # delší PDF jdou jako vytažený text (cena a limity požadavku)
PDF_MAX_BYTES = 20 * 1024 * 1024
IMAGE_MAX_BYTES = 4 * 1024 * 1024
MAX_DOCS = 10
TEXT_MAX_CHARS = 150_000
EFFORT = os.environ.get("AI_EFFORT", "medium")
# Verze obsahu shrnutí: zvýšení přepočítá shrnutí všech aktivních oznámení (postupně, AI_LIMIT za běh).
VERSION = 2
_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}

SYSTEM = """Jsi pomocník, který lidem z obcí mikroregionu Dolnobřežansko vysvětluje oznámení z úředních desek.
Dostaneš oznámení a jeho přílohy (PDF, mapy, výkresy, skeny, dokumenty). Přílohy si důkladně přečti –
podstata bývá v nich, ne v názvu oznámení. Výsledek má dvě vrstvy:

1) RYCHLÁ VRSTVA pro člověka bez času a trpělivosti (mysli na čtenáře s ADHD):
- tldr: jedna věta (max. 160 znaků): co se konkrétně děje, kde a koho se to týká. Začni tím podstatným.
- action: co může nebo musí čtenář udělat, do kdy a kde (jedna věta). Pokud nic, prázdný řetězec.
- deadline: poslední den lhůty pro reakci veřejnosti (YYYY-MM-DD), jinak prázdný řetězec.
  Lhůtu počítej jen když ji text uvádí; „do 15 dnů od doručení“ u veřejné vyhlášky = vyvěšení + 15 + 15 dní.
- event / event_time: datum (YYYY-MM-DD) a čas (HH:MM) akce, zasedání nebo začátku uzavírky, jinak prázdné.
- where: místo (ulice, katastrální území, parcely), jinak prázdné.
- category: nejbližší kategorie.
- importance: "vysoka" (zásah do okolí, peníze, lhůta pro veřejnost, omezení provozu), "stredni", nebo
  "nizka" (formalita, týká se jen adresáta, informace bez dopadu); importance_why: proč, jednou větou.

2) ROZBOR PŘÍLOH pro toho, kdo chce vědět víc:
- impact: 1–3 věty, co to v praxi znamená pro lidi v obci (co se změní oproti dnešku, co uvidí, co je omezí).
- affects: koho se to týká (např. „sousedé ulice Na Vršku“, „všichni, kdo jezdí do Prahy po silnici II/105“,
  „jen adresát písemnosti“).
- details: 3–10 bodů s podstatou z příloh: co přesně se plánuje/rozhoduje, konkrétní parametry (rozměry,
  počty, kapacity, termíny prací, částky a o kolik se mění), podmínky, důvody, co rozhodl úřad nebo
  zastupitelstvo. Každý bod jedna konkrétní věc; žádné obecné fráze.
- facts: klíčové údaje jako dvojice label/value (např. Stavebník – firma nebo úřad, Stavba, Parcely,
  Katastrální území, Termín, Částka, Úřad, Spisová značka). Jen co v podkladech skutečně je.
- dates: všechna podstatná data jako dvojice date (YYYY-MM-DD) a what (lhůty, jednání, platnost, práce).
- contact: kam se obrátit (úřad, adresa podatelny, úřední e-mail/telefon), jinak prázdné.
- docs: ke každé příloze (podle čísla): what = krátký název, co to je („situační výkres“, „rozpočet 2027“);
  summary = 2–4 věty, co v ní konkrétně je; u mapy či výkresu co je zakreslené a kde.

Piš česky, konkrétně, v tykání, bez úředních obratů; zkratky vysvětli. Nic si nevymýšlej – když údaj
v podkladech není, pole nech prázdné. Jména a adresy soukromých osob nevypisuj (firmy a úřady ano)."""

_PAIR = {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
         "required": ["label", "value"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string"},
        "action": {"type": "string"},
        "deadline": {"type": "string"},
        "event": {"type": "string"},
        "event_time": {"type": "string"},
        "where": {"type": "string"},
        "category": {"type": "string", "enum": sorted(LABELS)},
        "importance": {"type": "string", "enum": ["vysoka", "stredni", "nizka"]},
        "importance_why": {"type": "string"},
        "impact": {"type": "string"},
        "affects": {"type": "string"},
        "details": {"type": "array", "items": {"type": "string"}},
        "facts": {"type": "array", "items": _PAIR},
        "dates": {"type": "array", "items": {
            "type": "object", "properties": {"date": {"type": "string"}, "what": {"type": "string"}},
            "required": ["date", "what"], "additionalProperties": False}},
        "contact": {"type": "string"},
        "docs": {"type": "array", "items": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "what": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["index", "what", "summary"], "additionalProperties": False}},
    },
    "additionalProperties": False,
}
SCHEMA["required"] = list(SCHEMA["properties"])


GHA_AUDIENCE = "https://api.anthropic.com"
GHA_REFRESH_SECONDS = 240  # token GitHubu platí ~5 min a jde použít jen jednou


def _federation() -> bool:
    return bool(os.environ.get("ANTHROPIC_FEDERATION_RULE_ID"))


def enabled() -> bool:
    """Shrnutí běží s API klíčem, nebo s Workload Identity Federation (bez klíče, např. z GitHub Actions)."""
    # Prázdná proměnná (nenastavený secret ve workflow) by v SDK zastínila federaci – pryč s ní.
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_FEDERATION_RULE_ID",
                "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_SERVICE_ACCOUNT_ID", "ANTHROPIC_WORKSPACE_ID"):
        if var in os.environ and not os.environ[var].strip():
            del os.environ[var]
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or _federation()


async def _github_oidc_token() -> str:
    import httpx

    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"audience": GHA_AUDIENCE},
                        headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
        r.raise_for_status()
        return r.json()["value"]


async def prepare_github_federation() -> asyncio.Task | None:
    """V GitHub Actions s federací: zapíše čerstvý OIDC token do souboru a průběžně ho obnovuje.

    SDK si soubor přečte při každé výměně tokenu; token GitHubu je jednorázový a krátkodobý.
    """
    import tempfile

    if not _federation() or os.environ.get("ANTHROPIC_API_KEY") or "ACTIONS_ID_TOKEN_REQUEST_URL" not in os.environ:
        return None
    path = os.environ.get("ANTHROPIC_IDENTITY_TOKEN_FILE") or os.path.join(tempfile.mkdtemp(), "gha-jwt")
    os.environ["ANTHROPIC_IDENTITY_TOKEN_FILE"] = path

    async def write() -> None:
        token = await _github_oidc_token()
        with open(path, "w", encoding="utf-8") as f:
            f.write(token)

    await write()

    async def loop() -> None:
        while True:
            await asyncio.sleep(GHA_REFRESH_SECONDS)
            try:
                await write()
            except Exception as exc:  # noqa: BLE001 – další pokus za chvíli
                log.info("Obnova tokenu GitHub OIDC selhala: %s", exc)

    log.info("Claude: přihlášení přes Workload Identity Federation (GitHub OIDC)")
    return asyncio.create_task(loop())


def build_content(meta: dict, docs: list[dict], text_only: bool = False) -> list[dict]:
    """docs: [{"name", "ext", "pages", "bytes", "text"}]; bytes mohou chybět."""
    head = "\n".join(f"{k}: {v}" for k, v in meta.items() if v)
    content: list[dict] = [{"type": "text", "text": f"Oznámení z úřední desky:\n{head}"}]
    for n, d in enumerate(docs[:MAX_DOCS], 1):
        label = f"Příloha {n}: {d.get('name') or 'bez názvu'} ({d.get('ext') or '?'}"
        label += f", {d['pages']} str.)" if d.get("pages") else ")"
        raw = d.get("bytes")
        if not text_only and raw and d.get("ext") == "pdf" and d.get("pages", 0) <= PDF_MAX_PAGES \
                and len(raw) <= PDF_MAX_BYTES:
            content.append({"type": "text", "text": label})
            content.append({"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf", "data": base64.b64encode(raw).decode()}})
        elif not text_only and raw and d.get("ext") in _IMAGE_TYPES and len(raw) <= IMAGE_MAX_BYTES:
            content.append({"type": "text", "text": label})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": _IMAGE_TYPES[d["ext"]], "data": base64.b64encode(raw).decode()}})
        elif d.get("text"):
            text = d["text"]
            note = ""
            if len(text) > TEXT_MAX_CHARS:
                text, note = text[:TEXT_MAX_CHARS], " [text zkrácen – dokument je příliš dlouhý]"
            content.append({"type": "text", "text": f"{label}, vytažený text{note}:\n{text}"})
        else:
            content.append({"type": "text", "text": f"{label}: obsah nebyl k dispozici."})
    if len(docs) > MAX_DOCS:
        rest = ", ".join(d.get("name") or "?" for d in docs[MAX_DOCS:])
        content.append({"type": "text", "text": f"Další přílohy (nepřiloženy): {rest}"})
    content.append({"type": "text", "text": "Přečti přílohy a napiš popis podle pokynů (rychlá vrstva i rozbor příloh)."})
    return content


class Summarizer:
    def __init__(self, limit: int):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(max_retries=3)
        self.limit = limit
        self.used = 0
        self.stopped = False
        self.tokens_in = 0
        self.tokens_out = 0

    def available(self) -> bool:
        return not self.stopped and self.used < self.limit

    async def _call(self, content: list[dict]) -> dict | None:
        kwargs: dict = dict(
            model=MODEL, max_tokens=12000, system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if not MODEL.startswith("claude-haiku"):
            # při odmítnutí bezpečnostním filtrem požadavek dokončí jiný model
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        resp = await self.client.beta.messages.create(**kwargs)
        self.tokens_in += resp.usage.input_tokens
        self.tokens_out += resp.usage.output_tokens
        if resp.stop_reason in ("refusal", "max_tokens"):
            log.info("Claude nevrátil popis (%s)", resp.stop_reason)
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text)

    async def summarize(self, meta: dict, docs: list[dict]) -> dict | None:
        if not self.available():
            return None
        self.used += 1
        a = self._anthropic
        try:
            try:
                return await self._call(build_content(meta, docs))
            except a.BadRequestError as exc:  # např. poškozené PDF – zkusit jen s textem
                log.info("Claude odmítl přílohy (%s), zkouším jen text", exc.message[:200])
                return await self._call(build_content(meta, docs, text_only=True))
        except (a.AuthenticationError, a.PermissionDeniedError) as exc:
            log.warning("Claude: neplatný klíč nebo chybí oprávnění (%s) – shrnutí vypnuta", exc.message[:200])
            self.stopped = True
        except a.RateLimitError:
            log.warning("Claude: limit požadavků – zbytek oznámení se shrne v dalším běhu")
            self.stopped = True
        except a.APIStatusError as exc:
            if "credit" in str(exc.message).lower():
                log.warning("Claude: došel kredit – shrnutí vypnuta")
                self.stopped = True
            else:
                log.info("Claude: chyba %s %s", exc.status_code, exc.message[:200])
        except a.APIConnectionError as exc:
            log.info("Claude: chyba spojení %s", exc)
        except (json.JSONDecodeError, StopIteration) as exc:
            log.info("Claude: nečitelná odpověď %s", exc)
        except Exception as exc:  # noqa: BLE001 – např. odmítnuté přihlášení přes federaci (WorkloadIdentityError)
            log.warning("Claude: přihlášení nebo požadavek selhal (%s: %s) – zbytek běhu jen popisy pravidly",
                        type(exc).__name__, str(exc)[:300])
            self.stopped = True
        return None
