# Úřední desky

Webový agregátor oznámení z úředních desek českých obcí, městských částí a dalších úřadů.

## Zdroj dat

- Od 1. 2. 2022 mají úřady povinnost zveřejňovat úřední desku jako otevřená data
  podle **[OFN Úřední desky](https://ofn.gov.cz/úřední-desky/2021-07-20/)** a evidovat ji
  v **Národním katalogu otevřených dat (NKOD)**.
- Seznam všech desek vrací GraphQL rozhraní NKOD `https://data.gov.cz/graphql`
  (filtr `conformsTo` = URI normy). Dotaz vychází z oficiální ukázky
  [datagov-cz/aplikace-uredni-desky](https://github.com/datagov-cz/aplikace-uredni-desky).
- Každá deska je samostatný JSON soubor na serveru obce. Centrální API se všemi položkami
  neexistuje a řada serverů nemá zapnutý CORS, proto data nesbírá prohlížeč, ale harvester.

## Architektura („statický backend“)

```
GitHub Actions (cron každou hodinu)
  └─ harvester/ (Python, httpx, asyncio)
       1. NKOD GraphQL → seznam desek
       2. paralelní stažení JSON všech desek (timeouty, retry, limit velikosti)
       3. normalizace OFN → kompaktní model, sanitace URL
       4. sloučení s předchozím stavem → first_seen / removed (archiv sejmutých)
       5. výstup site/data/{boards,items,meta}.json
  ├─ stav se ukládá do větve `data` (jeden commit, bez historie)
  └─ web/ + data → GitHub Pages
web/ (statický frontend, bez buildu, vanilla JS)
  └─ vyhledávání, filtry, detail obce, detail oznámení, pokrytí dat
```

Žádný běžící server ani databáze. Hosting zdarma.

## Lokální vývoj

```bash
make venv          # .venv + harvester v editable režimu
make test          # pytest
make sample        # ukázková data z fixture → web/data (bez sítě)
make harvest LIMIT=50   # skutečný sběr z NKOD (prvních 50 desek)
make serve         # http://localhost:8000
```

## Nasazení

1. Settings → Pages → Source: **GitHub Actions**.
2. Workflow `.github/workflows/harvest.yml` běží při push do `main`, každou hodinu a ručně.

## Návrh UI

Návrh obrazovek (přehled, deska obce, detail oznámení, mobil):
<https://claude.ai/artifact/38iZP7WvtQvdLX8MqGHn9B>

## Formát výstupu

`items.json` – pole položek:

| pole | význam |
|---|---|
| `id` | stabilní hash (deska + IRI/URL položky) |
| `board` | id desky (hash IRI datové sady v NKOD) |
| `title`, `url` | název a odkaz na originál |
| `posted`, `until` | vyvěšení / relevantní do (`YYYY-MM-DD`; chybí = nespecifikováno) |
| `cj`, `sz` | číslo jednací, spisová značka |
| `agendas`, `docs` | agendy, dokumenty `{name, url}` |
| `first_seen`, `last_seen`, `removed` | historie ze sběrů |

## TODO

- [ ] Ověřit dotaz proti živému NKOD (stránkování `offset`, aktuální počet desek)
- [ ] Ověřit normalizaci proti JSON schématu OFN a reálným deskám
- [ ] Kraj/okres: mapování IČO poskytovatele → obec (RÚIAN / RPP), filtry a mapa
- [ ] Rozdělení `items.json` po krajích nebo index (sql.js-httpvfs / Pagefind), až data narostou
- [ ] RSS/Atom pro obec a hledání, „sledovat obec“
- [ ] Stránka pokrytí: chyby v čase, kontakt na poskytovatele
