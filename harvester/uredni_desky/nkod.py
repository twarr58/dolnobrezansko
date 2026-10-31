"""Vyhledání úředních desek v Národním katalogu otevřených dat (NKOD) přes GraphQL.

Dotaz vychází z oficiální ukázkové aplikace datagov-cz/aplikace-uredni-desky.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
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
    alt_urls: tuple[str, ...] = ()

    def urls(self) -> list[str]:
        return [self.access_url, *[u for u in self.alt_urls if u != self.access_url]]


_MUNICIPAL = re.compile(
    r"^(obec|město|městys|statutární\s+město|hlavní\s+město\s+praha|městská\s+část|městský\s+obvod)\b",
    re.IGNORECASE,
)


def is_municipal(src: BoardSource) -> bool:
    """Deska obce, města, městyse, statutárního města nebo jeho městské části/obvodu.

    Rozhoduje název poskytovatele z NKOD (kraje, ministerstva, soudy apod. sem nepatří).
    """
    return bool(_MUNICIPAL.match(" ".join(src.publisher.split())))


def board_id(dataset_iri: str) -> str:
    """Stabilní krátký identifikátor desky odvozený z IRI datové sady."""
    return hashlib.sha1(dataset_iri.encode("utf-8")).hexdigest()[:12]


def _query(limit: int, offset: int | None) -> str:
    args = [f"limit: {limit}"]
    if offset:
        args.append(f"offset: {offset}")
    args.append(f"filters: {{ conformsTo: {json.dumps(OFN_URI, ensure_ascii=False)} }}")
    return f"query {{ datasets({', '.join(args)}) {{ {_FIELDS} }} }}"


def _access_urls(distributions: list[dict]) -> list[str]:
    """Všechny URL distribucí; ty, které vypadají jako JSON, napřed."""
    urls = list(dict.fromkeys(d.get("accessURL") for d in distributions or [] if d.get("accessURL")))
    return sorted(urls, key=lambda u: "json" not in u.lower())


def parse_datasets(raw: list[dict]) -> list[BoardSource]:
    sources: list[BoardSource] = []
    for ds in raw:
        iri = ds.get("iri")
        urls = _access_urls(ds.get("distribution") or [])
        if not iri or not urls:
            log.warning("Datová sada bez IRI nebo distribuce: %s", iri)
            continue
        sources.append(
            BoardSource(
                id=board_id(iri),
                dataset_iri=iri,
                title=((ds.get("title") or {}).get("cs") or "").strip(),
                publisher=(((ds.get("publisher") or {}).get("title") or {}).get("cs") or "").strip(),
                access_url=urls[0],
                alt_urls=tuple(urls[1:]),
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


SPARQL_URL = "https://data.gov.cz/sparql"

# Doplňkové hledání: desky, které soulad s OFN nedeklarují vůbec, nebo jen s překlepem či jinou
# variantou URI normy (procentové kódování, bez lomítka, URL schématu…). GraphQL filtr je nenajde.
SPARQL_QUERY = """
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct: <http://purl.org/dc/terms/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?ds ?title ?pubName ?access ?download WHERE {
  ?ds a dcat:Dataset ; dct:title ?title ; dcat:distribution ?dist .
  OPTIONAL { ?ds dct:conformsTo ?c }
  FILTER(CONTAINS(LCASE(STR(?title)), "úřední desk")
         || CONTAINS(LCASE(STR(?c)), "desky") || CONTAINS(STR(?c), "%C3%BA%C5%99edn%C3%AD"))
  FILTER(!CONTAINS(STR(?ds), "podněty"))
  OPTIONAL { ?dist dcat:accessURL ?access }
  OPTIONAL { ?dist dcat:downloadURL ?download }
  OPTIONAL { ?ds dct:publisher ?p . ?p foaf:name|skos:prefLabel ?pubName }
}
"""


def parse_sparql(bindings: list[dict]) -> list[BoardSource]:
    by_ds: dict[str, dict] = {}
    for b in bindings:
        iri = b["ds"]["value"]
        d = by_ds.setdefault(iri, {"title": "", "publisher": "", "urls": []})
        title = b.get("title", {})
        if title.get("xml:lang", "cs") == "cs" or not d["title"]:
            d["title"] = title.get("value", d["title"]).strip()
        pub = b.get("pubName", {})
        if pub and (pub.get("xml:lang", "cs") == "cs" or not d["publisher"]):
            d["publisher"] = pub["value"].strip()
        for key in ("download", "access"):  # downloadURL je přímo soubor, accessURL bývá stránka
            u = b.get(key, {}).get("value")
            if u and u.startswith(("http://", "https://")) and u not in d["urls"]:
                d["urls"].append(u)
    sources = []
    for iri, d in by_ds.items():
        urls = sorted(d["urls"], key=lambda u: "json" not in u.lower())
        if urls:
            sources.append(BoardSource(board_id(iri), iri, d["title"], d["publisher"], urls[0], tuple(urls[1:])))
    return sources


async def discover_extra(client: httpx.AsyncClient, known: set[str]) -> list[BoardSource]:
    """Desky dohledané přes SPARQL, které GraphQL dotaz na OFN nevrátil. Při chybě vrátí []."""
    try:
        resp = await client.post(SPARQL_URL, data={"query": SPARQL_QUERY},
                                 headers={"Accept": "application/sparql-results+json"}, timeout=120)
        resp.raise_for_status()
        extra = [s for s in parse_sparql(resp.json()["results"]["bindings"]) if s.dataset_iri not in known]
    except Exception as exc:  # noqa: BLE001 – doplňkový zdroj, sběr nesmí kvůli němu spadnout
        log.warning("NKOD SPARQL: doplňkové hledání selhalo: %s", exc)
        return []
    log.info("NKOD SPARQL: navíc %d desek bez (správné) deklarace OFN", len(extra))
    return extra
