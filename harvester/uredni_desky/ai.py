"""Volitelné shrnutí oznámení od Claude (zapne se proměnnou ANTHROPIC_API_KEY).

Claude dostane metadata oznámení a přílohy: PDF přímo (přečte i mapy a skeny), obrázky jako obrázky,
ostatní jako vytažený text. Vrátí strukturovaný popis pro rychlé čtení.
Shrnutí se dělá jen u nových oznámení (mezipaměť ve stavu) a s limitem počtu za běh.
"""

from __future__ import annotations

import base64
import json
import logging
import os

from .digest import LABELS

log = logging.getLogger(__name__)

MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")
PDF_MAX_PAGES = 40  # delší PDF jdou jako vytažený text (cena a limity požadavku)
PDF_MAX_BYTES = 15 * 1024 * 1024
IMAGE_MAX_BYTES = 4 * 1024 * 1024
MAX_DOCS = 6
TEXT_MAX_CHARS = 120_000
_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}

SYSTEM = """Jsi pomocník, který lidem z obcí mikroregionu Dolnobřežansko vysvětluje oznámení z úředních desek.
Čtenáři nemají čas ani trpělivost na úřední jazyk (mysli na člověka s ADHD): piš česky, krátce, konkrétně,
v tykání, bez úředních obratů a bez zkratek, které běžný člověk nezná.

- tldr: jedna věta (max. 140 znaků), co se děje a koho se to týká. Začni tím podstatným.
- bullets: 0–4 krátké body s tím, co je navíc důležité (co přesně, kde, kolik, kdo). Nic neopakuj z tldr.
- action: co může nebo musí čtenář udělat a do kdy (jedna věta). Pokud nic, prázdný řetězec.
- deadline: poslední den lhůty pro reakci veřejnosti (YYYY-MM-DD), jinak prázdný řetězec.
  Lhůtu počítej jen když ji text uvádí; „do 15 dnů od doručení“ u veřejné vyhlášky = vyvěšení + 15 + 15 dní.
- event / event_time: datum (YYYY-MM-DD) a čas (HH:MM) akce, zasedání nebo začátku uzavírky, jinak prázdné.
- where: místo (ulice, katastrální území, parcely), jinak prázdné.
- category: nejbližší kategorie.
- docs: ke každé příloze (podle čísla) krátce, co v ní je (např. „mapa s vyznačenou objížďkou“).

Nic si nevymýšlej. Když údaj v podkladech není, nech pole prázdné. Jména soukromých osob nevypisuj."""

SCHEMA = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "action": {"type": "string"},
        "deadline": {"type": "string"},
        "event": {"type": "string"},
        "event_time": {"type": "string"},
        "where": {"type": "string"},
        "category": {"type": "string", "enum": sorted(LABELS)},
        "docs": {"type": "array", "items": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "what": {"type": "string"}},
            "required": ["index", "what"], "additionalProperties": False}},
    },
    "required": ["tldr", "bullets", "action", "deadline", "event", "event_time", "where", "category", "docs"],
    "additionalProperties": False,
}


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


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
    content.append({"type": "text", "text": "Napiš popis podle pokynů."})
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
            model=MODEL, max_tokens=4000, system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
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
        return None
