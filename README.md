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
  6. znaky obcí: web/erby (z webů obcí), ostatní z Wikimedia Commons podle Wikidat (P94), ve stavu erby.json
web/ (statická stránka bez buildu): Úřední desky · Kalendář akcí · Zpravodaje · Obce (stránka každé obce:
     oznámení, akce, zpravodaj); znak a barva obce u každé položky, stejné oznámení více obcí jednou
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
jen při změně oznámení. Limit shrnutí za běh: proměnná repozitáře `AI_LIMIT` (výchozí 100),
model: `AI_MODEL` (výchozí `claude-haiku-5-5`; pečlivější `claude-sonnet-5-5`, nejsilnější `claude-opus-5-5`), hloubka čtení: `AI_EFFORT` (výchozí `medium`).
Změna obsahu shrnutí zvýší `ai.VERSION` – stará shrnutí se pak postupně přepočítají.

Kdy se shrnutí dělá znovu: každé oznámení má otisk z názvu, data vyvěšení a lhůty z desky a obsahu
příloh (SHA-256). Přílohy se jednou za 24 hodin ověří podmíněným dotazem (`ETag` / `Last-Modified`,
nezměněný soubor se nestahuje). Když úřad vymění PDF na stejné adrese, přidá přílohu nebo posune lhůtu,
otisk se změní a Claude oznámení shrne znovu; web u něj ukáže „změna“ a datum aktualizace. Do té doby
zůstává předchozí shrnutí s poznámkou, že se připravuje nové. U nezměněných oznámení se Claude znovu
nevolá. Claude dostane prvních 10 příloh; PDF do 100 stran celé, delší jako text z celého dokumentu.

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

## Zpravodaje obcí

Sekce „Zpravodaje“ na webu (vedle výchozích úředních desek) ukazuje pro každou obec nejnovější číslo
zpravodaje a archiv po letech. Bez AI – zpravodaj má desítky stran a každého zajímá něco jiného, takže
jsou to jen odkazy na čísla na webu obce. Nastavení je v `data/region.json` u obce pod klíčem
`newsletter` (popis polí v `uredni_desky/newsletters.py`); archiv se prochází nejvýš jednou za 12 hodin
a při chybě zůstává poslední známý seznam. Obce, u kterých zpravodaj zatím nemáme (Psáry, Libeř,
Jesenice, Zlatníky-Hodkovice, Březová-Oleško, Okrouhlo), mají v sekci jen zmínku.

## Kalendář akcí

Záložka „Kalendář akcí“ spojuje kalendáře z webů obcí do jednoho (příštích 60 dní, filtr podle obce
a „dnes / tento týden / víkend“). Bez AI – název, datum, čas, místo a odkaz na akci na webu obce.
Nastavení v `data/region.json` u obce pod klíčem `events` (`fmt`: `vismo` – JSON API kalendáře
redakčního systému vismo, `rdc`, `article`, nebo `link` = jen odkaz); popis v `uredni_desky/events.py`.
Kalendáře se procházejí nejvýš jednou za 6 hodin, při chybě zůstanou naposledy známé akce.

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

### Desky přes edesky.cz (Vestec, Jesenice, Okrouhlo, Březová-Oleško)

Weby těchto obcí z runneru GitHubu (USA) nejdou. Jejich desky proto harvester bere z API
[edesky.cz](https://edesky.cz/api), které desky obcí samo stahuje z Česka a přílohy i jejich
rozpoznaný text hostuje u sebe (adaptér `edesky.py`, zdroj `{"fmt": "edesky", "url": "https://edesky.cz/desky/<id>"}`).
Potřebuje klíč z profilu na edesky.cz v secretu **`EDESKY_API_KEY`** (v logu se maskuje).
API neuvádí „vyvěšeno do“ a místo data vyvěšení dává datum načtení na edesky.cz; bere se okno
posledních 60 dní. Ostatní obce zůstávají na přímém čtení desky (víc údajů).

### Servery blokující zahraniční IP

Runnery GitHub Actions běží v USA a část obecních webů je nepustí: Jesenice a Okrouhlo nepřijmou
spojení (timeout), Vestec vrací HTTP 403. Z Česka fungují. Dvě řešení:

1. **Záložní proxy v ČR** – secret `HARVEST_FALLBACK_PROXY` (např. `http://uzivatel:heslo@proxy.example.cz:3128`,
   squid nebo tinyproxy na malém VPS v ČR). Harvester ji použije jen tam, kde přímý přístup selže
   (timeout, chyba spojení, HTTP 401/403/451) – pro desku, její přílohy i stránky oznámení; server,
   který jednou přímo nejde, pak v běhu jede rovnou přes proxy.
   Nastavení na čerstvém Debianu 12 (VPS s českou IP) jedním skriptem
   `deploy/proxy/setup-squid-debian12.sh`: squid s heslem, jen weby obcí mikroregionu (žádná otevřená
   proxy), bez mezipaměti, firewall nftables (SSH + port proxy), automatické bezpečnostní aktualizace;
   na konci vypíše hodnotu secretu. Heslo jde k proxy nešifrovaně (HTTP), proto proxy pouští jen weby obcí.
2. **Self-hosted runner v ČR** (vlastní server, NAS, OpenShift s actions-runner-controller) a v jobu
   `harvest` `runs-on: [self-hosted, linux]`. Repozitář je veřejný – sběr se spouští jen z `main`
   (plán, ruční spuštění, push), ne z pull requestů; ve Settings → Actions nech zapnuté schvalování
   workflow z forků.

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
