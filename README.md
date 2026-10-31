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
       5. výstup pro web: site/data/boards.json, recent.json (30 dní), boards/<id>.json, meta.json
  ├─ plný stav state/items.json se ukládá do větve `data` (jeden commit, bez historie)
  └─ web/ + data → GitHub Pages
web/ (statický frontend, bez buildu, vanilla JS)
  └─ hledání v oznámeních za 30 dní, stránka desky (aktuální i sejmuté), detail, seznam desek, pokrytí
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

## Servery blokující zahraniční IP

Diagnostika (`diagnose.yml`, 31. 10. 2026) ukázala, že 73 desek nepřijme z runneru GitHubu (USA)
ani TCP spojení. 54 z nich běží u jednoho českého hostingu (78.156.158.x), zbytek na vlastních
serverech úřadů. Z Česka fungují, jde o blokování zahraničních IP.

Řešení: harvester umí pro tyto servery použít záložní proxy v ČR. Stačí v repozitáři nastavit
secret `HARVEST_FALLBACK_PROXY` (např. `http://uzivatel:heslo@proxy.example.cz:3128`).
Proxy se použije jen pro desky, které selžou na timeout nebo chybu spojení.
Alternativa: spouštět celý sběr z ČR (self-hosted runner, CronJob v OpenShiftu apod.).

## Formát dat pro web

- `boards.json` – desky: `id`, `name` (zobrazovaný název), `publisher`, `title`, `access_url`, `dataset_iri`, `ok`, `error`, `count`, `fetched_at`
- `recent.json` – odlehčené položky za posledních 30 dní: `id`, `b` (deska), `t` (název), `p` (vyvěšeno), `u` (do), `d` (počet příloh)
- `boards/<id>.json` – všechny položky desky: `id`, `title`, `url`, `posted`, `until`, `cj`, `sz`, `agendas`, `docs[{name,url}]`, `first_seen`, `last_seen`, `removed`

První ostrý sběr (31. 10. 2026): 560 desek v NKOD, 449 načteno, 148 248 oznámení,
z toho 40 569 vyvěšených za posledních 30 dní (`recent.json` ≈ 6 MB, gzip ≈ 1 MB).

## TODO

- [ ] Ověřit normalizaci proti JSON schématu OFN
- [ ] Proxy / runner v ČR pro 73 desek blokujících zahraniční IP
- [ ] Kraj/okres: mapování IČO poskytovatele → obec (RÚIAN / RPP), filtry a mapa
- [ ] Fulltext i ve starších oznámeních (index po deskách nebo sql.js-httpvfs)
- [ ] RSS/Atom pro obec a hledání, „sledovat obec“
- [ ] Stránka pokrytí: chyby v čase, kontakt na poskytovatele
