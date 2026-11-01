"""CLI: python -m uredni_desky {harvest,sample}."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import json
import logging
from pathlib import Path

import httpx

from . import USER_AGENT
from . import cache
from .build import load_state, run_build
from .extra import load_extra_sources
from .fetch import FetchResult, fetch_all
from .nkod import BoardSource, board_id, discover_boards, discover_extra, is_municipal

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "deska.json"


async def _harvest(args: argparse.Namespace) -> None:
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    timeout = httpx.Timeout(args.timeout, connect=20)
    common = dict(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/ld+json;q=0.9, */*;q=0.1"},
        follow_redirects=True, timeout=timeout, limits=limits,
    )
    # Záložní proxy (např. http://uzivatel:heslo@proxy.example.cz:3128) pro servery, které blokují zahraniční IP.
    proxy = os.environ.get("HARVEST_FALLBACK_PROXY") or None
    async with httpx.AsyncClient(**common) as client, \
            httpx.AsyncClient(**common, proxy=proxy) if proxy else contextlib.nullcontext() as fallback:
        sources = await discover_boards(client)
        sources += await discover_extra(client, {s.dataset_iri for s in sources})
        known_ids = {s.id for s in sources}
        sources += [s for s in load_extra_sources(Path(args.state) / "discovered.json") if s.id not in known_ids]
        cache.load_from_state(load_state(Path(args.state)))
        cache.reset_budget(args.detail_budget)
        if not args.all_publishers:
            sources = [s for s in sources if is_municipal(s)]
            logging.info("Jen obce a městské části: %d desek", len(sources))
        if args.limit:
            sources = sources[: args.limit]
        results = await fetch_all(client, sources, concurrency=args.concurrency, retries=args.retries,
                                  fallback=fallback)
    run_build(sources, results, Path(args.out), Path(args.state))


def _sample(args: argparse.Namespace) -> None:
    """Vygeneruje ukázková data z testovací fixture – pro vývoj frontendu bez sítě."""
    payload = json.loads(FIXTURE.read_text("utf-8"))
    iri = "https://example.org/datové-sady/ukázková-deska"
    src = BoardSource(board_id(iri), iri, "Úřední deska (ukázka)", "Obec Ukázkov", "https://example.org/deska.json")
    run_build([src], {src.id: FetchResult(True, payload)}, Path(args.out), None)


def main() -> None:
    p = argparse.ArgumentParser(prog="uredni-desky")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="Stáhne všechny desky z NKOD a vytvoří statická data")
    h.add_argument("--out", default="site/data")
    h.add_argument("--state", default="state", help="Adresář se stavem (items.json); čte se i zapisuje")
    h.add_argument("--concurrency", type=int, default=24)
    h.add_argument("--timeout", type=float, default=30)
    h.add_argument("--retries", type=int, default=1)
    h.add_argument("--limit", type=int, help="Zpracovat jen prvních N desek (ladění)")
    h.add_argument("--detail-budget", type=int, default=6000, help="Max. počet stažených detailů oznámení za běh")
    h.add_argument("--all-publishers", action="store_true", help="Sbírat i desky krajů, ministerstev, soudů apod.")

    d = sub.add_parser("diagnose", help="Postupně ověří nedostupné desky (DNS, TCP, HTTP)")
    d.add_argument("--boards", default="state/boards.json")
    d.add_argument("--kinds", default="timeout,connect", help="Druhy chyb oddělené čárkou; prázdné = všechny")

    e = sub.add_parser("explore", help="Průzkum NKOD přes SPARQL (desky bez deklarace OFN apod.)")
    e.add_argument("--title", default="úřední desk")
    e.add_argument("--find", default="", help="Hledané názvy oddělené čárkou, např. Břežany")

    g = sub.add_parser("discover", help="Najde desky všech obcí (Wikidata + rozpoznání systému webu)")
    g.add_argument("--out", default="state/discovered.json")
    g.add_argument("--nkod", default="state/boards.json", help="boards.json z posledního sběru (desky v NKOD)")
    g.add_argument("--concurrency", type=int, default=32)
    g.add_argument("--limit", type=int)

    pr = sub.add_parser("probe", help="Vyzkouší adaptéry na stránkách (ladění)")
    pr.add_argument("urls", nargs="*")
    pr.add_argument("--state", default="state")
    pr.add_argument("--unknown", type=int, default=0, help="Přidat N náhodných nepodporovaných desek z discovered.json")
    pr.add_argument("--failed", default="", help="Přidat nenačtené desky daného fmt z boards.json (např. galileo)")
    pr.add_argument("--seed", type=int, default=0)

    s = sub.add_parser("sample", help="Ukázková data z fixture (bez sítě)")
    s.add_argument("--out", default="web/data")

    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.cmd == "harvest":
        asyncio.run(_harvest(args))
    elif args.cmd == "discover":
        from .discover import run as discover
        asyncio.run(discover(Path(args.out), Path(args.nkod), args.concurrency, args.limit))
    elif args.cmd == "explore":
        from .explore import run as explore
        explore(args.title, [x for x in args.find.split(",") if x])
    elif args.cmd == "probe":
        import random
        from .probe import run as probe
        urls = [u for u in args.urls if u.startswith(("http://", "https://"))]
        state = Path(args.state)
        if args.unknown and (state / "discovered.json").exists():
            unknown = [u["board_link"] for u in json.loads((state / "discovered.json").read_text("utf-8"))["unknown"]
                       if u.get("board_link")]
            urls += random.Random(args.seed).sample(unknown, min(args.unknown, len(unknown)))
        if args.failed and (state / "boards.json").exists():
            urls += [b["access_url"] for b in json.loads((state / "boards.json").read_text("utf-8"))
                     if b.get("fmt") == args.failed and not b.get("ok")]
        asyncio.run(probe(urls))
    elif args.cmd == "diagnose":
        from .diagnose import run
        asyncio.run(run(Path(args.boards), {k for k in args.kinds.split(",") if k}))
    else:
        _sample(args)


if __name__ == "__main__":
    main()
