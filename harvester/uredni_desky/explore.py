"""Průzkum NKOD přes SPARQL: datové sady úředních desek včetně těch, které nedeklarují soulad s OFN.

Slouží k zjištění, proč některé obce chybí (nejsou v katalogu vůbec, nebo jen bez dct:conformsTo).
"""

from __future__ import annotations

import collections
import json

import httpx

from . import OFN_URI, USER_AGENT

SPARQL_URL = "https://data.gov.cz/sparql"

QUERY = """
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct: <http://purl.org/dc/terms/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?ds ?title ?publisher ?pubName ?conf WHERE {
  ?ds a dcat:Dataset ; dct:title ?title .
  FILTER(CONTAINS(LCASE(STR(?title)), "%s"))
  OPTIONAL { ?ds dct:publisher ?publisher . OPTIONAL { ?publisher foaf:name|skos:prefLabel ?pubName } }
  OPTIONAL { ?ds dct:conformsTo ?conf }
}
"""


def run(title_term: str, needles: list[str]) -> None:
    resp = httpx.post(SPARQL_URL, data={"query": QUERY % title_term.lower()},
                      headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT},
                      timeout=120)
    print(f"SPARQL HTTP {resp.status_code}", flush=True)
    resp.raise_for_status()
    rows = resp.json()["results"]["bindings"]
    datasets: dict[str, dict] = {}
    for r in rows:
        d = datasets.setdefault(r["ds"]["value"], {"title": r["title"]["value"], "publisher": "", "conf": set()})
        if "pubName" in r:
            d["publisher"] = r["pubName"]["value"]
        elif "publisher" in r and not d["publisher"]:
            d["publisher"] = r["publisher"]["value"]
        if "conf" in r:
            d["conf"].add(r["conf"]["value"])
    ofn = sum(1 for d in datasets.values() if OFN_URI in d["conf"])
    print(f"Datových sad s „{title_term}“ v názvu: {len(datasets)}, z toho deklaruje OFN: {ofn}")
    confs = collections.Counter(c for d in datasets.values() for c in (d["conf"] or {"(žádné)"}))
    print("conformsTo:", json.dumps(confs.most_common(10), ensure_ascii=False))
    print("\nBez deklarace OFN (prvních 80):")
    for iri, d in list((k, v) for k, v in datasets.items() if OFN_URI not in v["conf"])[:80]:
        print(f"  {d['publisher'][:60]} | {d['title'][:70]} | {iri}")
    for needle in needles:
        n = needle.lower()
        hits = [(k, v) for k, v in datasets.items() if n in v["title"].lower() or n in v["publisher"].lower()]
        print(f"\nHledání „{needle}“: {len(hits)}")
        for iri, d in hits:
            print(f"  {d['publisher']} | {d['title']} | conformsTo={sorted(d['conf'])} | {iri}")
