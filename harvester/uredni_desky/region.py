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

from urllib.parse import urlsplit

import httpx

from . import USER_AGENT, ai, cache, digest, documents, edesky, erby, events, newsletters, parcely, samosprava, webpage
from .build import load_state, run_build
from .extra import sources_from_entries
from .fetch import fetch_all, needs_proxy
from .nkod import BoardSource

log = logging.getLogger(__name__)

REDETECT_DAYS = 7
DOC_BUDGET = 200  # max. stažených příloh za běh
SHOW_REMOVED_DAYS = 30
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"


def load_region() -> dict:
    return json.loads(resources.files(__package__).joinpath("data/region.json").read_text("utf-8"))


CONTACT_KEYS = ("office", "address", "phone", "email", "form", "ds", "ico", "hours", "hours_note", "hours_url",
                "source", "checked")


def load_contacts() -> dict[str, dict]:
    """Kontakty úřadů obcí (data/contacts.json) – jen známé klíče, bez poznámek souboru."""
    raw = json.loads(resources.files(__package__).joinpath("data/contacts.json").read_text("utf-8"))
    return {name: {k: v for k, v in c.items() if k in CONTACT_KEYS}
            for name, c in raw.items() if not name.startswith("_") and isinstance(c, dict)}


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
            fresh = prev.get("checked", "") > (now - timedelta(days=REDETECT_DAYS)).isoformat() \
                and not prev.get("status", "").startswith("web nedostupný")  # výpadek webu: zkusit příště znovu
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


SIG_PREFIX = "s2:"  # podpis z obsahu příloh a termínů desky (starší podpisy jen z názvu a adres)
REVALIDATE_HOURS = 24  # jak často se ověřuje, že se příloha na stejné adrese nezměnila
# Verze čtení příloh: zvýšit, když se mění, jak přílohy čteme (ne úřad) – nové shrnutí pak není „změna oznámení“.
SOURCE_VERSION = 2  # 2: přílohy z edesky.cz jako rozpoznaný text místo stránky edesky.cz


def _signature(item: dict, doc_cache: dict[str, dict] | None = None) -> str:
    """Otisk toho, co Claude o oznámení čte: název, termíny z desky a obsah příloh (SHA-256).

    Změní se, když úřad oznámení upraví – i když vymění PDF na stejné adrese nebo posune lhůtu.
    """
    cache = doc_cache or {}
    docs = sorted(f"{d['url']}#{cache.get(d['url'], {}).get('sha', '')}" for d in item.get("docs", []))
    key = "|".join([item["title"], item.get("posted") or "", item.get("until") or "", *docs])
    return SIG_PREFIX + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


async def enrich(client: httpx.AsyncClient, items: list[dict], state_dir: Path, ai_limit: int,
                 now: datetime, fallback: httpx.AsyncClient | None = None,
                 proxy_hosts: set[str] | None = None) -> tuple[dict[str, dict], dict[str, dict]]:
    """Přílohy a popisy aktivních oznámení. Vrátí (přílohy podle URL, popisy podle id oznámení).

    fallback: klient přes proxy v ČR pro servery, které z runneru GitHubu nejdou (timeout, 401/403/451);
    proxy_hosts: servery, u kterých už je jasné, že jdou jen přes proxy (deska se stáhla přes ni).
    """
    proxy_hosts = set(proxy_hosts or ())

    async def via_fallback(url: str, call):
        """Zavolá call(klient) přímo; když server nejde, zkusí záložní proxy a server si zapamatuje."""
        host = urlsplit(url).hostname or ""
        if fallback is not None and host in proxy_hosts:
            return await call(fallback)
        try:
            return await call(client)
        except httpx.HTTPError as exc:
            if fallback is None or not needs_proxy(exc):
                raise
            proxy_hosts.add(host)
            log.info("Server %s přímo nejde (%s), dál přes záložní proxy", host, type(exc).__name__)
            return await call(fallback)

    doc_cache: dict[str, dict] = _read_json(state_dir / "docs.json.gz", {})
    summaries: dict[str, dict] = _read_json(state_dir / "summaries.json.gz", {})
    summarizer = None
    refresher = None
    if ai.enabled() and ai_limit > 0:
        try:
            refresher = await ai.prepare_github_federation()
            summarizer = ai.Summarizer(ai_limit)
        except Exception as exc:  # noqa: BLE001 – bez přihlášení jen popisy pravidly
            log.warning("Claude: přihlášení se nepovedlo (%s) – jen popisy pravidly", exc)
    budget = {"docs": DOC_BUDGET}
    dl_sem = asyncio.Semaphore(4)
    ai_sem = asyncio.Semaphore(5)

    stamp = now.isoformat(timespec="seconds")
    recheck_before = (now - timedelta(hours=REVALIDATE_HOURS)).isoformat()
    fresh_bytes: dict[str, tuple[bytes, str]] = {}  # stažené v tomto běhu (pro Claude se nestahuje znovu)

    async def edesky_text_doc(url: str, ref: dict | None, name: str, cached: dict | None) -> dict:
        """Příloha z edesky.cz jako rozpoznaný text (veřejný soubor edesky_text_url nebo text z API)."""
        text, orig = edesky.TEXTS.get(url, ""), ""
        if ref is not None:
            try:
                async with dl_sem:
                    att = await edesky.attachment(client, ref)
                text, orig = text or att["text"], att["orig"]
            except httpx.HTTPError as exc:
                log.info("Text přílohy z edesky.cz %s: %s %s", url, type(exc).__name__, exc)
                if not text:
                    return cached or {"url": url, "error": f"{type(exc).__name__}: {exc}"[:200]}
        full = documents._clean(text)
        ext = documents.sniff(b"", orig or url)
        if ext == "html" and edesky.is_page(url) and not orig:
            ext = ""
        kind = documents.KIND_MAP if documents._MAP_NAME.search(f"{name} {url}") and len(full) < 3000 \
            else documents.KIND_TEXT
        info = documents.DocInfo(url=url, ext=ext, kind=kind, chars=len(full), text=full[:documents.EXCERPT],
                                 sha=documents.fingerprint(full.encode("utf-8")), checked=stamp)
        # orig: originál na webu obce – odkaz pro čtenáře místo stránky edesky.cz, která chce přihlášení
        doc_cache[url] = {**info.to_cache(), "src": "edesky", **({"orig": orig} if orig else {})}
        return {**doc_cache[url], "full_text": full}

    async def get_doc(doc: dict, need_bytes: bool) -> dict:
        """Příloha z mezipaměti; jednou za REVALIDATE_HOURS se podmíněně ověří (ETag / Last-Modified)."""
        url = doc["url"]
        cached = doc_cache.get(url)
        if url in fresh_bytes:
            content, full_text = fresh_bytes[url]
            return {**(cached or {}), "bytes": content, "full_text": full_text}
        ref = edesky.ref_for(url)
        if ref is not None and (fallback is None or edesky.is_page(url)):
            # příloha z edesky.cz: rozpoznaný text z veřejného souboru edesky_text_url. Stránka dokumentu na
            # edesky.cz se nestahuje nikdy (ani s proxy) – bez přihlášení je to jen obal s výzvou k přihlášení
            # a při každém stažení vypadá jinak, takže by se oznámení zbytečně shrnovalo pořád znovu.
            # Starší záznam se staženou stránkou (HTML s menu, bez src=edesky) se nahradí hned.
            if cached and cached.get("src") == "edesky" and cached.get("checked", "") > recheck_before and cached.get("chars"):
                return cached
            return await edesky_text_doc(url, ref, doc.get("name") or "", cached)
        if cached and not need_bytes and cached.get("checked", "") > recheck_before:
            return cached
        if budget["docs"] <= 0:
            return cached or {"url": url}
        budget["docs"] -= 1
        conditional = cached and cached.get("sha") and not need_bytes
        async with dl_sem:
            try:
                dl = await via_fallback(url, lambda c: documents.download(
                    c, url, etag=cached.get("etag", "") if conditional else "",
                    last_modified=cached.get("lm", "") if conditional else ""))
            except (httpx.HTTPError, ValueError) as exc:
                if ref is not None or url in edesky.TEXTS:  # originál nejde ani přes proxy – text z edesky.cz
                    return await edesky_text_doc(url, ref, doc.get("name") or "", cached)
                log.info("Příloha %s: %s %s", url, type(exc).__name__, exc)
                return cached or {"url": url, "error": f"{type(exc).__name__}: {exc}"[:200]}
        if dl.content is None:  # 304 – beze změny
            cached["checked"] = stamp
            return cached
        sha = documents.fingerprint(dl.content)
        if cached and cached.get("sha") == sha and not need_bytes:
            cached.update(checked=stamp, etag=dl.etag, lm=dl.lm)
            return cached
        info = await asyncio.to_thread(documents.analyze, dl.content, url, doc.get("name") or "", dl.ctype)
        info.sha, info.etag, info.lm, info.checked = sha, dl.etag, dl.lm, stamp
        if cached and cached.get("sha") and cached["sha"] != sha:
            log.info("Příloha se změnila (nový obsah na stejné adrese): %s", url)
        doc_cache[url] = info.to_cache()
        fresh_bytes[url] = (dl.content, info.full_text)
        return {**info.to_cache(), "bytes": dl.content, "full_text": info.full_text}

    active = [i for i in items if not i.get("removed")]
    active.sort(key=lambda i: (i.get("posted") or "", i.get("first_seen") or ""), reverse=True)

    async def load_page(it: dict) -> None:
        """Oznámení bez příloh v seznamu (ALIS, Vismo…): přílohy a text ze stránky oznámení."""
        url = it.get("url") or ""
        if it.get("docs") or not url.startswith(("http://", "https://")) or documents._FILE_EXT.search(url):
            return
        key = f"page:{url}"
        cached = doc_cache.get(key)
        stale = not cached or (not cached.get("links")
                               and cached.get("checked", "") < (now - timedelta(days=1)).isoformat())
        if stale and budget["docs"] > 0:
            budget["docs"] -= 1
            async with dl_sem:
                try:
                    async def get_page(c):
                        resp = await c.get(url, headers=webpage.HTML_HEADERS, timeout=30)
                        resp.raise_for_status()
                        return resp

                    r = await via_fallback(url, get_page)
                    links, text = documents.page_attachments(webpage.response_text(r), str(r.url))
                    cached = {"links": links, "text": text, "checked": now.isoformat(timespec="seconds")}
                    doc_cache[key] = cached
                except (httpx.HTTPError, ValueError) as exc:
                    log.info("Stránka oznámení %s: %s %s", url, type(exc).__name__, exc)
        if cached:
            it["docs"] = cached.get("links", [])
            it["_body"] = cached.get("text", "")

    await asyncio.gather(*(load_page(it) for it in active))

    async def process(it: dict) -> None:
        docs = [await get_doc(d, False) for d in it.get("docs", [])]  # nejdřív ověřit obsah příloh
        sig = _signature(it, doc_cache)
        prev = summaries.get(it["id"]) or {}
        same = prev.get("sig") == sig
        current = bool(same and prev.get("ai") and prev.get("v") == ai.VERSION)
        result = dict(prev)  # bez nového shrnutí zůstává předchozí (i u změněného oznámení – web ukáže, že se aktualizuje)
        result.setdefault("sig", sig)
        if summarizer is not None and not current and summarizer.available():
            docs = [await get_doc(d, True) for d in it.get("docs", [])]
            meta = {"Název": it["title"], "Vyvěšeno": it.get("posted"), "Vyvěšeno do": it.get("until"),
                    "Číslo jednací": it.get("cj"), "Dnešní datum": now.date().isoformat(),
                    "Text na stránce oznámení": (it.get("_body") or "")[:4000]}
            payload = [{"name": d0.get("name"), "ext": d.get("ext"), "pages": d.get("pages", 0),
                        "bytes": d.get("bytes"), "text": d.get("full_text") or d.get("text", "")}
                       for d0, d in zip(it.get("docs", []), docs)]
            async with ai_sem:
                out = await summarizer.summarize(meta, payload)
            if out:
                # „aktualizováno“ jen při skutečné změně oznámení (ne při změně formátu podpisu nebo schématu)
                changed = bool(prev.get("ai") and str(prev.get("sig", "")).startswith(SIG_PREFIX) and not same
                               and prev.get("src_v") == SOURCE_VERSION)
                result.update(sig=sig, ai=out, v=ai.VERSION, at=stamp, model=ai.MODEL, src_v=SOURCE_VERSION)
                result.setdefault("first_at", prev.get("at") or stamp)
                if changed:
                    result["updated_at"] = stamp
        summaries[it["id"]] = result
        it["_sig"] = sig

    try:
        await asyncio.gather(*(process(it) for it in active))
    finally:
        if refresher:
            refresher.cancel()
    if summarizer:
        log.info("Claude: %d shrnutí, tokeny %d vstup / %d výstup (model %s)",
                 summarizer.used, summarizer.tokens_in, summarizer.tokens_out, ai.MODEL)
    keep = {i["id"] for i in items}
    summaries = {k: v for k, v in summaries.items() if k in keep}
    live_docs = {d["url"] for i in items for d in i.get("docs", [])} | {f"page:{i.get('url')}" for i in items}
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
    s = digest.describe(it["title"], it.get("posted"), it.get("until"), docs, it.get("agendas"),
                        body=it.get("_body", ""))
    a = (summary or {}).get("ai")
    if a:
        bullets = a.get("details") or a.get("bullets") or []  # verze 1 měla jen bullets
        s.update(src="claude", tldr=a.get("tldr") or s["tldr"], bullets=[b for b in bullets if b][:10],
                 action=a.get("action") or s.get("action", ""))
        if a.get("category") in digest.LABELS and a["category"] != "ostatni" and s["cat"] not in digest.RULE_PREFERRED:
            s["cat"] = a["category"]
        for key in ("deadline", "event"):
            if d := _valid_date(a.get(key)):
                s[key] = d
        if a.get("event_time"):
            s["time"] = a["event_time"][:5]
        for key in ("where", "impact", "affects", "contact", "importance", "importance_why"):
            if a.get(key):
                s[key] = a[key]
        if facts := [f for f in a.get("facts", []) if f.get("label") and f.get("value")]:
            s["facts"] = facts[:14]
        if dates := [{"date": d, "what": x["what"]} for x in a.get("dates", [])
                     if (d := _valid_date(x.get("date"))) and x.get("what")]:
            s["dates"] = sorted(dates, key=lambda x: x["date"])[:12]
    if a and summary:
        s["summarized"] = (summary.get("at") or "")[:10] or None
        s["updated"] = (summary.get("updated_at") or "")[:10] or None
        s["summarized_at"] = summary.get("at") or None  # plný čas zpracování (ISO s časovou zónou)
        s["updated_at"] = summary.get("updated_at") or None
        s["model"] = summary.get("model") or None
        if it.get("_sig") and summary.get("sig") != it["_sig"]:
            s["pending"] = True  # oznámení se změnilo, nové shrnutí přijde v dalším běhu
    s["label"] = digest.LABELS.get(s["cat"], "Ostatní")
    return {k: v for k, v in s.items() if v not in (None, "", [], False)}


def build_output(region: dict, members: list[dict], boards: list[dict], items: list[dict],
                 doc_cache: dict, summaries: dict, now: datetime, erb_paths: dict[str, str] | None = None) -> dict:
    by_board = {b["id"]: b for b in boards}
    name_of = {m["board"]: m["name"] for m in members if m.get("board")}
    conf = {m["name"]: m for m in region.get("members", [])}
    contacts = load_contacts()
    erb_paths = erb_paths if erb_paths is not None else {n: c["erb"] for n, c in conf.items() if c.get("erb")}
    out_members = []
    for m in members:
        b = by_board.get(m.get("board") or "", {})
        c = conf.get(m["name"], {})
        out_members.append({k: v for k, v in {
            "name": m["name"], "slug": c.get("slug"), "color": c.get("color"), "erb": erb_paths.get(m["name"]),
            "publisher": m["publisher"], "web": m.get("web"),
            "board_url": b.get("access_url") or m.get("board_link"), "fmt": b.get("fmt"),
            "ok": bool(b.get("ok")), "count": b.get("count", 0), "error": b.get("error"),
            "status": m.get("status"), "connected": bool(m.get("board")), "contact": contacts.get(m["name"]),
        }.items() if v not in (None, "")})
    since = (now - timedelta(days=SHOW_REMOVED_DAYS)).isoformat()
    out_items = []
    for it in items:
        if it["board"] not in name_of or (it.get("removed") and it["removed"] < since):
            continue
        summary = summaries.get(it["id"])
        ai_docs = {d.get("index"): d for d in ((summary or {}).get("ai") or {}).get("docs", [])}
        docs = []
        for n, d in enumerate(it.get("docs", []), 1):
            info = doc_cache.get(d["url"], {})
            link = info.get("orig") if edesky.is_page(d["url"]) and str(info.get("orig", "")).startswith("http") else d["url"]
            docs.append({k: v for k, v in {
                "n": d.get("name"), "url": link, "k": info.get("kind"), "x": info.get("ext"),
                "pg": info.get("pages"), "ocr": info.get("ocr"), "w": ai_docs.get(n, {}).get("what"),
                "sum": ai_docs.get(n, {}).get("summary")}.items() if v})
        page = it.get("url")
        if edesky.is_page(page or "") and docs:  # stránka edesky.cz chce přihlášení – původní znění je originál přílohy
            page = next((x["url"] for x in docs if not edesky.is_page(x["url"])), page)
        out_items.append({k: v for k, v in {
            "id": it["id"], "o": name_of[it["board"]], "t": it["title"], "url": page,
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


async def collect_council(client: httpx.AsyncClient, members: list[dict], state_dir: Path, now: datetime,
                          fallback: httpx.AsyncClient | None) -> dict:
    """Usnesení a zápisy rad a zastupitelstev; body shrne Claude (vlastní limit COUNCIL_AI_LIMIT za běh)."""
    limit = int(os.environ.get("COUNCIL_AI_LIMIT") or "40")
    summarizer = refresher = None
    if ai.enabled() and limit > 0:
        try:
            refresher = await ai.prepare_github_federation()
            summarizer = ai.Summarizer(limit)
        except Exception as exc:  # noqa: BLE001 – bez přihlášení jen odkazy na dokumenty
            log.warning("Samospráva: přihlášení ke Claude se nepovedlo (%s)", exc)
    try:
        out = await samosprava.collect(client, members, state_dir, now, fallback, summarizer)
    finally:
        if refresher:
            refresher.cancel()
    if summarizer is not None:
        log.info("Samospráva – Claude: %d shrnutí, tokeny %d vstup / %d výstup", summarizer.used,
                 summarizer.tokens_in, summarizer.tokens_out)
    log.info("Samospráva: %d zasedání z %d zdrojů", len(out["meetings"]), len(out["sources"]))
    return out


async def run(out_dir: Path, state_dir: Path, ai_limit: int = 100, concurrency: int = 8,
              assets_dir: Path | None = None) -> dict:
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
        fallback = httpx.AsyncClient(**common, proxy=proxy) if proxy else None
        try:
            results = await fetch_all(client, sources, concurrency=concurrency, retries=1, fallback=fallback,
                                      total_budget=20 * 60)
            run_build(sources, results, out_dir, state_dir, now=now)
            items = load_state(state_dir)
            boards = _read_json(state_dir / "boards.json", [])
            # přílohy ze serverů, jejichž deska šla jen přes proxy, se stahují rovnou přes ni
            proxy_hosts = {urlsplit(s.access_url).hostname or "" for s in sources
                           if results.get(s.id) and results[s.id].via_proxy}
            doc_cache, summaries = await enrich(client, items, state_dir, ai_limit, now, fallback, proxy_hosts)
            news = await newsletters.collect(client, region["members"], state_dir, now, fallback)
            event_data = await events.collect(client, region["members"], state_dir, now, fallback)
            council = await collect_council(client, region["members"], state_dir, now, fallback)
        finally:
            if fallback:
                await fallback.aclose()
    erb_paths = erby.load(region["members"], assets_dir)  # znaky z větve assets (workflow erby.yml)
    data = build_output(region, members, boards, items, doc_cache, summaries, now, erb_paths)
    data["newsletters"] = news
    data["events"] = event_data
    data["council"] = council
    # odkazy na parcely do katastru (ČÚZK); bez ČÚZK zůstanou texty bez odkazů
    async with httpx.AsyncClient(**common) as client:
        fallback = httpx.AsyncClient(**common, proxy=proxy) if proxy else None
        try:
            await parcely.annotate(client, data, state_dir, now, fallback)
        except Exception as exc:  # noqa: BLE001 – odkazy jsou doplněk, sběr kvůli nim nesmí spadnout
            log.warning("Parcely: %r", exc)
        finally:
            if fallback:
                await fallback.aclose()
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
    members, boards, items, docs, summaries = [], [], [], {}, {}
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
        if r.get("ai"):
            raw_ai = re.sub(r"\{iso([+-]\d+)\}", lambda m: day(int(m.group(1))).isoformat(), json.dumps(r["ai"]))
            summaries[f"u{n}"] = {"ai": json.loads(fill(raw_ai)), "at": now.isoformat(timespec="seconds"),
                                   "model": "claude-haiku-5-5",
                                   "updated_at": now.isoformat(timespec="seconds") if r.get("updated") else None}
    data = build_output(region, members, boards, items, docs, summaries, now)
    data["sample"] = True
    data["newsletters"] = [
        {"obec": m["name"], "name": m["newsletter"]["name"], "archive": m["newsletter"]["url"], "ok": True,
         "issues": [{"t": f"č. {k}/{y}", "url": f"https://example.org/zpravodaj/{n}/{y}-{k}.pdf", "y": y}
                    for y in (today.year, today.year - 1, today.year - 2) for k in (4, 3, 2, 1)]}
        if m.get("newsletter") else {"obec": m["name"]} for n, m in enumerate(region["members"])]
    sample_events = ["Svoz bioodpadu", "Promítání pro seniory", "Zasedání zastupitelstva", "Lampionový průvod",
                     "Taneční zábava", "Vernisáž výstavy", "Sběr elektroodpadu", "Dětský den"]
    readable = [m for m in region["members"] if (m.get("events") or {}).get("fmt") not in (None, "link")]
    data["events"] = {
        "sources": [{"obec": m["name"], "url": m["events"]["url"], "read": m["events"]["fmt"] != "link", "ok": True}
                    for m in region["members"] if m.get("events")],
        "items": sorted(({"o": m["name"], "t": sample_events[(k + n) % len(sample_events)],
                          "d": day(k * 2 + n % 3).isoformat(), "time": f"{16 + n % 4}:00",
                          "url": "https://example.org/akce"} for n, m in enumerate(readable) for k in range(6)),
                        key=lambda e: (e["d"], e["time"]))}
    sample_points = [
        ("schvaluje", "Rozpočtové opatření č. 6/2026: víc peněz na opravu chodníků v ulici Pražská.", "rozpocet",
         "1 200 000 Kč", "vysoka"),
        ("bere na vědomí", "Zprávu o hospodaření mateřské školy za první pololetí.", "ostatni", "", "nizka"),
        ("pověřuje", "Starostu podpisem smlouvy o dílo na rekonstrukci hřiště.", "smlouvy", "", "stredni"),
        ("schvaluje", "Pronájem sálu obecního domu spolku na taneční kurzy.", "majetek", "", "nizka")]
    councils = [(m, c) for m in region["members"] for c in m.get("council") or []]
    data["council"] = {
        "sources": [{"obec": m["name"], "body": c["body"], "url": c["url"], "ok": True, "n": 3} for m, c in councils],
        "meetings": sorted(({"id": f"c{n}-{k}", "o": m["name"], "body": c["body"], "kind": "usneseni",
                             "date": day(-7 * k - n).isoformat(), "no": f"{20 - k}/{today.year}",
                             "t": f"Usnesení – {'rada' if c['body'] == 'rada' else 'zastupitelstvo'} {20 - k}/{today.year}",
                             "url": "https://example.org/usneseni.pdf", "x": "pdf",
                             "tldr": "Ukázka: rada schválila víc peněz na opravu chodníků a pronájem sálu.",
                             "points": [{"v": v, "t": t, "cat": cat, "amt": amt, "imp": imp}
                                        for v, t, cat, amt, imp in sample_points[: 2 + k % 3]],
                             "src": "claude"}
                            for n, (m, c) in enumerate(councils) for k in range(3)),
                           key=lambda x: x["date"], reverse=True)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "region.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return data
