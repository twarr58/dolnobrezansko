# Úřední deska Dolnobřežansko

Oznámení z úředních desek 11 obcí **Mikroregionu Dolnobřežansko** (Březová-Oleško, Dolní Břežany,
Jesenice, Libeř, Ohrobec, Okrouhlo, Psáry, Vestec, Vrané nad Vltavou, Zlatníky-Hodkovice, Zvole),
vysvětlená jednou větou: co se děje, co s tím můžu udělat a do kdy. Harvester čte i přílohy
oznámení (PDF, mapy a výkresy, skeny, DOCX).

## Jak to funguje

```
GitHub Actions (každou hodinu) – python -m uredni_desky region
  1. desky obcí: známé v harvester/uredni_desky/data/region.json; obce bez známé desky se hledají
     automaticky (web obce z Wikidat → rozpoznání systému), výsledek v region_sources.json (týden)
  2. stažení desek adaptéry (iMunis, Galileo, Vismo, RSS/ALIS, obecné HTML), archiv sejmutých
  3. přílohy aktivních oznámení: stažení, text (pypdf, DOCX/ODT), druh: text / mapa nebo výkres
     (formát nad A3, málo textu, název) / sken (OCR tesseract) – každá příloha jen jednou
  4. popis: pravidla (téma, lhůty, termíny, parcely, částky) + volitelně shrnutí od Claude
  5. výstup: site/data/region.json → GitHub Pages; stav do větve `data`
web/ (statická stránka bez buildu): Teď řešit · Nové tento týden · filtr obcí · detail s přílohami
```

Web: https://twarr58.github.io/dolnobrezansko/ · návrh designu: https://claude.ai/artifact/7PsxZ3855TvghPqKjtp8g9

## Lokální vývoj

```bash
make venv          # .venv + harvester v editable režimu
make test          # pytest
make sample        # ukázková data → web/data/region.json (bez sítě)
make harvest       # skutečný sběr mikroregionu (do .state a web/data)
make serve         # http://localhost:8000
```

## Shrnutí od Claude (volitelné)

Bez přihlášení se popisy dělají jen pravidly. S přihlášením Claude přečte u každého **nového**
oznámení přílohy (až 10 příloh; PDF včetně map a skenů posílá celé, delší než 100 stran jako
vytažený text; u oznámení bez příloh v seznamu i přílohy a text ze stránky oznámení) a vrátí dvě vrstvy:
rychlou (jedna věta, co udělat a do kdy, důležitost) a rozbor příloh (co to znamená, koho se to týká,
co přesně – parametry, částky, změny, klíčové údaje, data, kontakt, obsah každé přílohy). Hotová shrnutí se ukládají (`summaries.json.gz` ve větvi `data`), znovu se platí
jen při změně oznámení. Limit shrnutí za běh: proměnná repozitáře `AI_LIMIT` (výchozí 40),
model: `AI_MODEL` (výchozí `claude-opus-5-5`), hloubka čtení: `AI_EFFORT` (výchozí `medium`).
Změna obsahu shrnutí zvýší `ai.VERSION` – stará shrnutí se pak postupně přepočítají.

### Přihlášení bez klíče (doporučeno): Workload Identity Federation

GitHub Actions se u Claude prokáže svým krátkodobým tokenem (OIDC). V repozitáři není žádný klíč,
který by mohl uniknout. Pravidlo v Claude Console přijme jen token z repozitáře
`twarr58/dolnobrezansko` a větve `main`; do `main` může zapisovat jen vlastník repozitáře,
pull requesty (i z forků) mají jiný ref a neprojdou.

1. **Claude Console:** Settings → Workload identity → **Connect workload** → **GitHub Actions**.
   - Rule name `uredni-desky-main`, organization `twarr58`, repository `dolnobrezansko`
   - Branch / ref `refs/heads/main` (vypsat, prázdné = jakákoli větev)
   - Allowed events: ne „Only push“ – sběr běží podle plánu (`schedule`) a ručně (`workflow_dispatch`)
   - Target: nový service account (např. `uredni-desky`), role developer, výchozí workspace
2. **GitHub:** Settings → Secrets and variables → Actions → záložka **Variables** → přidat ID,
   která průvodce ukáže (nejsou tajná):
   - `ANTHROPIC_FEDERATION_RULE_ID` (`fdrl_…`)
   - `ANTHROPIC_ORGANIZATION_ID` (UUID organizace)
   - `ANTHROPIC_SERVICE_ACCOUNT_ID` (`svac_…`)
   - `ANTHROPIC_WORKSPACE_ID` (`wrkspc_…`, jen když pravidlo platí pro víc workspaců)
3. Actions → *Sběr desek mikroregionu a nasazení* → **Run workflow** (větev `main`). V logu kroku
   sběru má být „přihlášení přes Workload Identity Federation“ a „Claude: N shrnutí“.

Harvester si token GitHubu vyžádá sám (workflow má `id-token: write`) a každé 4 minuty ho obnovuje.

### Alternativa: API klíč

Console → Settings → API keys → **Create key** (s expirací) → GitHub: Settings → Secrets and
variables → Actions → **Secrets** → `ANTHROPIC_API_KEY`. Klíč má přednost před federací.
Secrety GitHub nedává workflow spuštěným z pull requestů z forků.

API kredit: předplatné Max obsahuje měsíční kredit na API (organizaci v Claude Console je třeba
propojit s plánem). Útratu lze omezit limitem v Console; počet shrnutí za běh `AI_LIMIT`.

## Celostátní režim (vypnutý)

Původní sběr všech desek z NKOD a webů obcí (`python -m uredni_desky harvest`, `discover`,
`probe`, `diagnose`) zůstává v kódu a slouží jako zdroj adaptérů. Workflow `discover.yml`
jde spustit ručně; hodinový sběr běží jen pro mikroregion.

### Adaptéry: obce mimo katalog (iMunis eDeska, Galileo)

Do NKOD má desku zapsanou jen zlomek obcí. Obce na službě **iMunis eDeska**
(`<obec>.imunis.cz/edeska`, např. Dolní Břežany) se načítají z jejího RSS kanálu a stránek
detailu (data vyvěšení, značka, přílohy). Seznam je v
`harvester/uredni_desky/data/extra_sources.json` – další obec se přidá řádkem
`{"subdomain": "…", "publisher": "Obec …"}`.

Weby obcí na systému **Galileo Corporation** (deska na `<web>/urad/uredni-deska/`, např. Semtěš,
Bílé Podolí) se čtou z HTML seznamu a detailu položek – v sekci `galileo` stejného souboru:
`{"url": "https://www.obec.cz/urad/uredni-deska/", "publisher": "Obec …"}`.

### Hledání desek všech obcí

Workflow `discover.yml` (týdně a ručně) vezme z Wikidat všechny obce ČR a jejich weby, stáhne
titulní stránky a rozpozná systém desky: iMunis, Galileo (libovolná cesta …/uredni-deska/),
Vismo (export OFN nebo HTML deska), RSS/Atom kanál desky (např. `?action=atom`, ALIS). Nalezené zdroje
uloží do `discovered.json` ve větvi `data`, harvester je přidá ke zdrojům z NKOD. Statistika
nepodporovaných systémů ukazuje, pro které systémy psát další adaptéry. Detail oznámení se
u adaptérů stahuje jen u nových položek (mezipaměť ze stavu), aby sběr nezatěžoval weby obcí.

### Servery blokující zahraniční IP

Diagnostika (`diagnose.yml`, 31. 10. 2026) ukázala, že 73 desek nepřijme z runneru GitHubu (USA)
ani TCP spojení. 54 z nich běží u jednoho českého hostingu (78.156.158.x), zbytek na vlastních
serverech úřadů. Z Česka fungují, jde o blokování zahraničních IP.

Řešení: harvester umí pro tyto servery použít záložní proxy v ČR. Stačí v repozitáři nastavit
secret `HARVEST_FALLBACK_PROXY` (např. `http://uzivatel:heslo@proxy.example.cz:3128`).
Proxy se použije jen pro desky, které selžou na timeout nebo chybu spojení.
Alternativa: spouštět sběr ze self-hosted runneru GitHub Actions v ČR.

## Formát dat pro web

`region.json`: `region`, `members[]` (obec, odkaz na desku, `connected`, `ok`, `status`),
`categories`, `items[]`:
`id`, `o` (obec), `t` (úřední název), `url`, `p` (vyvěšeno), `u` (do), `cj`, `r` (sejmuto),
`docs[{n, url, k: text|mapa|sken|obrazek, x: přípona, pg: strany, ocr, w: co v ní je}]`,
`s` (popis): `cat`, `label`, `tldr`, `action`, `deadline`, `event`, `time`, `event_end`, `where`,
`bullets[]`, `src` (`pravidla` | `claude`).

## TODO

- [ ] Ověřit desky Vestce, Jesenice, Zlatníků-Hodkovic, Březové-Oleška a Okrouhla po prvním běhu
- [ ] Náhled první strany mapy v detailu (obrázek z pdftoppm)
- [ ] Odběr novinek (RSS / e-mail) pro jednu obec
