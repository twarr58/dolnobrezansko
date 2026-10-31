"""CLI: python -m uredni_desky {harvest,sample}."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx

from . import USER_AGENT
from .build import run_build
from .fetch import FetchResult, fetch_all
from .nkod import BoardSource, board_id, discover_boards

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "deska.json"


async def _harvest(args: argparse.Namespace) -> None:
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    timeout = httpx.Timeout(args.timeout, connect=20)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/ld+json;q=0.9, */*;q=0.1"},
        follow_redirects=True, timeout=timeout, limits=limits,
    ) as client:
        sources = await discover_boards(client)
        if args.limit:
            sources = sources[: args.limit]
        results = await fetch_all(client, sources, concurrency=args.concurrency, retries=args.retries)
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
    h.add_argument("--concurrency", type=int, default=32)
    h.add_argument("--timeout", type=float, default=30)
    h.add_argument("--retries", type=int, default=2)
    h.add_argument("--limit", type=int, help="Zpracovat jen prvních N desek (ladění)")

    s = sub.add_parser("sample", help="Ukázková data z fixture (bez sítě)")
    s.add_argument("--out", default="web/data")

    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.cmd == "harvest":
        asyncio.run(_harvest(args))
    else:
        _sample(args)


if __name__ == "__main__":
    main()
