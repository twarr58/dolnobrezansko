"""Vyhledání úředních desek v Národním katalogu otevřených dat (NKOD) přes GraphQL.

Dotaz vychází z oficiální ukázkové aplikace datagov-cz/aplikace-uredni-desky.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

import httpx

from . import OFN_URI

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://data.gov.cz/graphql"
PAGE_SIZE = 5000

_FIELDS = "data { iri title { cs } publisher { title { cs } } distribution { accessURL format } } pagination { totalCount }"


@dataclass(frozen=True)
class BoardSource:
    id: str
    dataset_iri: str
    title: str
    publisher: str
    access_url: str


def board_id(dataset_iri: str) -> str:
    """Stabilní krátký identifikátor desky odvozený z IRI datové sady."""
    return hashlib.sha1(dataset_iri.encode("utf-8")).hexdigest()[:12]


def _query(limit: int, offset: int | None) -> str:
    args = [f"limit: {limit}"]
    if offset:
        args.append(f"offset: {offset}")
    args.append(f"filters: {{ conformsTo: {json.dumps(OFN_URI, ensure_ascii=False)} }}")
    return f"query {{ datasets({', '.join(args)}) {{ {_FIELDS} }} }}"


def _pick_access_url(distributions: list[dict]) -> str | None:
    urls = [d.get("accessURL") for d in distributions or [] if d.get("accessURL")]
    json_like = [u for u in urls if "json" in u.lower()]
    return (json_like or urls or [None])[0]


def parse_datasets(raw: list[dict]) -> list[BoardSource]:
    sources: list[BoardSource] = []
    for ds in raw:
        iri = ds.get("iri")
        url = _pick_access_url(ds.get("distribution") or [])
        if not iri or not url:
            log.warning("Datová sada bez IRI nebo distribuce: %s", iri)
            continue
        sources.append(
            BoardSource(
                id=board_id(iri),
                dataset_iri=iri,
                title=((ds.get("title") or {}).get("cs") or "").strip(),
                publisher=(((ds.get("publisher") or {}).get("title") or {}).get("cs") or "").strip(),
                access_url=url,
            )
        )
    return sources


async def _post(client: httpx.AsyncClient, query: str) -> dict:
    resp = await client.post(GRAPHQL_URL, json={"query": query, "variables": None})
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"NKOD GraphQL vrátil chybu: {body['errors']}")
    return body["data"]["datasets"]


async def discover_boards(client: httpx.AsyncClient) -> list[BoardSource]:
    """Vrátí všechny datové sady v NKOD deklarující soulad s OFN Úřední desky."""
    page = await _post(client, _query(PAGE_SIZE, None))
    raw = list(page["data"])
    total = page["pagination"]["totalCount"]
    # Stránkování jen pokud katalog vrátí méně než celkový počet.
    while len(raw) < total:
        page = await _post(client, _query(PAGE_SIZE, len(raw)))
        if not page["data"]:
            break
        raw.extend(page["data"])
    log.info("NKOD: nalezeno %d datových sad (totalCount=%d)", len(raw), total)
    return parse_datasets(raw)
