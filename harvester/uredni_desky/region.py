"""Sběr úředních desek jednoho mikroregionu (data/region.json) včetně příloh a jednoduchých popisů.

Postup:
  1. zdroje: známé desky z region.json; obce bez desky se dohledají (web z Wikidat → rozpoznání systému)
  2. stažení desek existujícími adaptéry, sloučení s archivem (build.run_build)
  3. přílohy aktivních oznámení: stažení, text, druh (text / mapa / sken) – každá příloha jen jednou
  4. popis: pravidla (digest.py), volitelně shrnutí od Claude (ai.py) – každé oznámení jen jednou
  5. výstup pro web: <out>/region.json
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path

import httpx

from . import USER_AGENT, ai, cache, digest, documents
from .build import load_state, run_build
from .extra import sources_from_entries
from .fetch import fetch_all
from .nkod import BoardSource

log = logging.getLogger(__name__)

REDETECT_DAYS = 7
DOC_BUDGET = 200  # max. stažených příloh za běh
SHOW_REMOVED_DAYS = 30
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"


def load_region() -> dict:
    return json.loads(resources.files(__package__).joinpath("data/region.json").read_text("utf-8"))


def _read_json(path: Path, default):
    try:
        if path.suffix == ".gz":
            return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_gz(path: Path, obj) -> None:
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(gzip.compress(data, mtime=0))


def source_for(entry: dict, publisher: str, area: str) -> BoardSource | None:
    fmt = entry.get("fmt")
    if not fmt:
        return None
    rec = {k: v for k, v in entry.items() if k in ("subdomain", "url")}
    rec.update(publisher=publisher, area=area)
    found = sources_from_entries({fmt: [rec]})
    return found[0] if found else None


async def _wikidata_webs(client: httpx.AsyncClient, names: list[str], area: str) -> dict[str, str]:
    values = " ".join(json.dumps(n, ensure_ascii=False) + "@cs" for n in names)
    query = f"""SELECT ?name (SAMPLE(?web) AS ?w) WHERE {{
      VALUES ?name {{ {values} }}
      ?item rdfs:label ?name ; wdt:P31/wdt:P279* wd:Q5153359 ; wdt:P131 ?okres ; wdt:P856 ?web .
      ?okres rdfs:label {json.dumps("okres " + area, ensure_ascii=False)}@cs .
      FILTER NOT EXISTS {{ ?item wdt:P576 [] }}
    }} GROUP BY ?name"""
    try:
        r = await client.post(WIKIDATA_SPARQL, data={"query": query},
                              headers={"Accept": "application/sparql-results+json"}, timeout=60)
        r.raise_for_status()
        return {b["name"]["value"]: b["w"]["value"] for b in r.json()["results"]["bindings"]}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("Wikidata nedostupná: %s", exc)
        return {}


async def resolve_sources(client: httpx.AsyncClient, region: dict, state_dir: Path,
                          now: datetime) -> tuple[list[BoardSource], list[dict]]:
    """Zdroje desek a stav obcí (pro web): známé z region.json, ostatní z mezipaměti hledání nebo nově hledané."""
    from .discover import classify_municipality

    area = region.get("area", "")
    found_path = state_dir / "region_sources.json"
    found = _read_json(found_path, {})
    discovered = _read_json(state_dir / "discovered.json", {}).get("sources", [])
    members, todo = [], []
    for m in region["members"]:
        info = {"name": m["name"], "publisher": m["publisher"], "web": m.get("web", "")}
        entry = m.get("source")
        if not entry:  # nalezeno dřívějším celostátním hledáním (discover)
            entry = next((s for s in discovered if (m.get("qid") and s.get("qid") == m["qid"])
                          or (s.get("publisher") == m["publisher"] and s.get("area") == area)), None)
        if not entry and (prev := found.get(m["name"])):
            fresh = prev.get("checked", "") > (now - timedelta(days=REDETECT_DAYS)).isoformat()
            if prev.get("entry") or fresh:
                entry = prev.get("entry")
                info["web"] = info["web"] or prev.get("web", "")
                info["status"] = prev.get("status", "")
            if not fresh:
                todo.append((m, info))
        elif not entry:
            todo.append((m, info))
        info["entry"] = entry
        members.append(info)

    if todo:
        need_web = [m["name"] for m, info in todo if not info["web"]]
        webs = await _wikidata_webs(client, need_web, area) if need_web else {}
        sem = asyncio.Semaphore(4)

        async def detect(m: dict, info: dict) -> None:
            web = info["web"] or webs.get(m["name"], "")
            info["web"] = web
            if not web:
                info["status"] = "web obce nenalezen"
            else:
                res = await classify_municipality(
                    client, {"qid": m.get("qid", ""), "name": m["name"], "types": "", "webs": [web], "area": area}, sem)
                if res.get("fmt"):
                    info["entry"] = {k: res[k] for k in ("fmt", "url", "subdomain") if res.get(k)}
                    info["status"] = "deska nalezena automaticky"
                else:
                    info["status"] = "systém desky zatím neumíme číst" if res.get("status") == "ok" \
                        else f"web nedostupný ({res.get('status')})"
                    info["board_link"] = res.get("board_link", "")
            found[m["name"]] = {"checked": now.isoformat(timespec="seconds"), "entry": info.get("entry"),
                                "web": web, "status": info.get("status", ""), "board_link": info.get("board_link", "")}
            log.info("Hledání desky %s: %s", m["name"], info.get("status"))

        await asyncio.gather(*(detect(m, info) for m, info in todo))
        state_dir.mkdir(parents=True, exist_ok=True)
        found_path.write_text(json.dumps(found, ensure_ascii=False, indent=1), "utf-8")

    sources = []
    for info in members:
        info.setdefault("board_link", found.get(info["name"], {}).get("board_link", ""))
        src = source_for(info.pop("entry") or {}, info["publisher"], area)
        info["board"] = src.id if src else None
        if src:
            sources.append(src)
    return sources, members


def _signature(item: dict) -> str:
    key = item["title"] + "|" + "|".join(sorted(d["url"] for d in item.get("docs", [])))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


async def enrich(client: httpx.AsyncClient, items: list[dict], state_dir: Path, ai_limit: int,
                 now: datetime) -> tuple[dict[str, dict], dict[str, dict]]:
    """Přílohy a popisy aktivních oznámení. Vrátí (přílohy podle URL, popisy podle id oznámení)."""
    doc_cache: dict[str, dict] = _read_json(state_dir / "docs.json.gz", {})
    summaries: dict[str, dict] = _read_json(state_dir / "summaries.json.gz", {})
    summarizer = ai.Summarizer(ai_limit) if ai.enabled() and ai_limit > 0 else None
    budget = {"docs": DOC_BUDGET}
    dl_sem = asyncio.Semaphore(4)
    ai_sem = asyncio.Semaphore(3)

    async def get_doc(doc: dict, need_bytes: bool) -> dict:
        url = doc["url"]
        cached = doc_cache.get(url)
        if cached and not need_bytes:
            return cached
        if budget["docs"] <= 0:
            return cached or {"url": url}
        budget["docs"] -= 1
        async with dl_sem:
            try:
                content, ctype = await documents.download(client, url)
            except (httpx.HTTPError, ValueError) as exc:
                log.info("Příloha %s: %s", url, exc)
                return cached or {"url": url, "error": str(exc)[:200]}
        info = await asyncio.to_thread(documents.analyze, content, url, doc.get("name") or "", ctype)
        doc_cache[url] = info.to_cache()
        return {**info.to_cache(), "bytes": content, "full_text": info.full_text}

    active = [i for i in items if not i.get("removed")]
    active.sort(key=lambda i: (i.get("posted") or "", i.get("first_seen") or ""), reverse=True)

    async def process(it: dict) -> None:
        sig = _signature(it)
        prev = summaries.get(it["id"])
        want_ai = summarizer is not None and not (prev and prev.get("sig") == sig and prev.get("ai"))
        docs = [await get_doc(d, want_ai) for d in it.get("docs", [])]
        result = {"sig": sig, "ai": prev.get("ai") if prev and prev.get("sig") == sig else None}
        if want_ai and summarizer.available():
            meta = {"Název": it["title"], "Vyvěšeno": it.get("posted"), "Vyvěšeno do": it.get("until"),
                    "Číslo jednací": it.get("cj"), "Dnešní datum": now.date().isoformat()}
            payload = [{"name": d0.get("name"), "ext": d.get("ext"), "pages": d.get("pages", 0),
                        "bytes": d.get("bytes"), "text": d.get("full_text") or d.get("text", "")}
                       for d0, d in zip(it.get("docs", []), docs)]
            async with ai_sem:
                out = await summarizer.summarize(meta, payload)
            if out:
                result["ai"] = out
                result["at"] = now.isoformat(timespec="seconds")
        summaries[it["id"]] = result

    await asyncio.gather(*(process(it) for it in active))
    if summarizer:
        log.info("Claude: %d shrnutí, tokeny %d vstup / %d výstup (model %s)",
                 summarizer.used, summarizer.tokens_in, summarizer.tokens_out, ai.MODEL)
    keep = {i["id"] for i in items}
    summaries = {k: v for k, v in summaries.items() if k in keep}
    live_docs = {d["url"] for i in items for d in i.get("docs", [])}
    doc_cache = {k: v for k, v in doc_cache.items() if k in live_docs}
    _write_gz(state_dir / "docs.json.gz", doc_cache)
    _write_gz(state_dir / "summaries.json.gz", summaries)
    return doc_cache, summaries


def _valid_date(s) -> str | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat() if s else None
    except (TypeError, ValueError):
        return None


def item_description(it: dict, docs: list[dict], summary: dict | None) -> dict:
    s = digest.describe(it["title"], it.get("posted"), it.get("until"), docs, it.get("agendas"))
    a = (summary or {}).get("ai")
    if a:
        s.update(src="claude", tldr=a.get("tldr") or s["tldr"], bullets=[b for b in a.get("bullets", []) if b][:4],
                 action=a.get("action") or s.get("action", ""))
        if a.get("category") in digest.LABELS:
            s["cat"] = a["category"]
        for key, src in (("deadline", "deadline"), ("event", "event")):
            if d := _valid_date(a.get(src)):
                s[key] = d
        if a.get("event_time"):
            s["time"] = a["event_time"][:5]
        if a.get("where"):
            s["where"] = a["where"]
    s["label"] = digest.LABELS.get(s["cat"], "Ostatní")
    return {k: v for k, v in s.items() if v not in (None, "", [])}


def build_output(region: dict, members: list[dict], boards: list[dict], items: list[dict],
                 doc_cache: dict, summaries: dict, now: datetime) -> dict:
    by_board = {b["id"]: b for b in boards}
    name_of = {m["board"]: m["name"] for m in members if m.get("board")}
    out_members = []
    for m in members:
        b = by_board.get(m.get("board") or "", {})
        out_members.append({k: v for k, v in {
            "name": m["name"], "publisher": m["publisher"], "web": m.get("web"),
            "board_url": b.get("access_url") or m.get("board_link"), "fmt": b.get("fmt"),
            "ok": bool(b.get("ok")), "count": b.get("count", 0), "error": b.get("error"),
            "status": m.get("status"), "connected": bool(m.get("board")),
        }.items() if v not in (None, "")})
    since = (now - timedelta(days=SHOW_REMOVED_DAYS)).isoformat()
    out_items = []
    for it in items:
        if it["board"] not in name_of or (it.get("removed") and it["removed"] < since):
            continue
        summary = summaries.get(it["id"])
        ai_docs = {d.get("index"): d.get("what") for d in ((summary or {}).get("ai") or {}).get("docs", [])}
        docs = []
        for n, d in enumerate(it.get("docs", []), 1):
            info = doc_cache.get(d["url"], {})
            docs.append({k: v for k, v in {
                "n": d.get("name"), "url": d["url"], "k": info.get("kind"), "x": info.get("ext"),
                "pg": info.get("pages"), "ocr": info.get("ocr"), "w": ai_docs.get(n)}.items() if v})
        out_items.append({k: v for k, v in {
            "id": it["id"], "o": name_of[it["board"]], "t": it["title"], "url": it.get("url"),
            "p": it.get("posted"), "u": it.get("until"), "cj": it.get("cj"), "r": it.get("removed"),
            "fs": it.get("first_seen"), "docs": docs,
            "s": item_description(it, [doc_cache.get(d["url"], {}) for d in it.get("docs", [])], summary),
        }.items() if v not in (None, "", [])})
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "region": {k: region[k] for k in ("id", "name", "web")},
        "ai_model": ai.MODEL if any(i["s"].get("src") == "claude" for i in out_items) else None,
        "members": out_members,
        "categories": digest.LABELS,
        "items": out_items,
    }


async def run(out_dir: Path, state_dir: Path, ai_limit: int = 40, concurrency: int = 8) -> dict:
    now = datetime.now(timezone.utc)
    region = load_region()
    proxy = os.environ.get("HARVEST_FALLBACK_PROXY") or None
    common = dict(headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                  timeout=httpx.Timeout(30, connect=20))
    async with httpx.AsyncClient(**common) as client:
        sources, members = await resolve_sources(client, region, state_dir, now)
        log.info("Mikroregion %s: %d obcí, %d desek", region["name"], len(members), len(sources))
        cache.load_from_state(load_state(state_dir))
        cache.reset_budget(2000)
        fallback_ctx = httpx.AsyncClient(**common, proxy=proxy) if proxy else None
        try:
            results = await fetch_all(client, sources, concurrency=concurrency, retries=1, fallback=fallback_ctx,
                                      total_budget=20 * 60)
        finally:
            if fallback_ctx:
                await fallback_ctx.aclose()
        run_build(sources, results, out_dir, state_dir, now=now)
        items = load_state(state_dir)
        boards = _read_json(state_dir / "boards.json", [])
        doc_cache, summaries = await enrich(client, items, state_dir, ai_limit, now)
    data = build_output(region, members, boards, items, doc_cache, summaries, now)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "region.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    log.info("region.json: %d oznámení, %d/%d obcí s deskou", len(data["items"]),
             sum(m.get("connected", False) for m in data["members"]), len(data["members"]))
    return data


def sample(out_dir: Path) -> dict:
    """Ukázková data bez sítě (pro vývoj webu) z tests/fixtures/region_sample.json; data se posunou k dnešku."""
    import re

    region = load_region()
    now = datetime.now(timezone.utc)
    today = now.date()
    raw = json.loads((Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "region_sample.json")
                     .read_text("utf-8"))
    day = lambda n: (today + timedelta(days=n))  # noqa: E731
    fill = lambda t: re.sub(r"\{d([+-]\d+)\}", lambda m: f"{day(int(m.group(1))).day}. {day(int(m.group(1))).month}. "
                                                       f"{day(int(m.group(1))).year}", t)  # noqa: E731
    members, boards, items, docs = [], [], [], {}
    for m in region["members"]:
        bid = f"ukazka-{m['name']}" if m.get("source") else None
        members.append({"name": m["name"], "publisher": m["publisher"], "web": m.get("web", ""), "board": bid,
                        "status": None if bid else "ukázka: deska se hledá"})
        if bid:
            boards.append({"id": bid, "ok": True, "count": sum(r["o"] == m["name"] for r in raw), "fmt": "ukazka"})
    for n, r in enumerate(raw):
        item_docs = []
        for k, d in enumerate(r.get("docs", [])):
            url = f"https://example.org/ukazka/{n}/{k}.{d.get('ext', 'pdf')}"
            docs[url] = {"kind": d["kind"], "ext": d.get("ext"), "pages": d.get("pages"), "text": fill(d.get("text", ""))}
            item_docs.append({"name": d["name"], "url": url})
        items.append({"id": f"u{n}", "board": f"ukazka-{r['o']}", "title": r["title"], "url": "https://example.org/",
                      "posted": day(r["pd"]).isoformat(), "until": day(r["ud"]).isoformat(), "cj": r.get("cj"),
                      "docs": item_docs, "first_seen": now.isoformat(timespec="seconds")})
    data = build_output(region, members, boards, items, docs, {}, now)
    data["sample"] = True
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "region.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return data
