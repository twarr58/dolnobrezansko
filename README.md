# Úřední deska Dolnobřežansko

Web, který na jednom místě ukazuje, co se děje v 11 obcích **Mikroregionu Dolnobřežansko**: oznámení
z úředních desek vysvětlená jednou větou (co se děje, co s tím můžu udělat a do kdy), kalendář akcí,
zpravodaje a stránku každé obce.

**Web:** https://twarr58.github.io/dolnobrezansko/

## Obsah

- [Co web ukazuje](#co-web-ukazuje)
- [Obce a zdroje dat](#obce-a-zdroje-dat)
- [Jak to funguje](#jak-to-funguje)
- [Lokální vývoj](#lokální-vývoj)
- [Nastavení repozitáře](#nastavení-repozitáře)
- [Shrnutí od Claude](#shrnutí-od-claude)
- [Úprava obce v konfiguraci](#úprava-obce-v-konfiguraci)
- [Proxy v ČR](#proxy-v-čr)
- [Formát dat pro web](#formát-dat-pro-web)
- [Celostátní režim (vypnutý)](#celostátní-režim-vypnutý)
- [Známá omezení a nápady](#známá-omezení-a-nápady)

## Co web ukazuje

| Sekce | Adresa | Obsah |
|---|---|---|
| Úřední desky | `#/` | Časová osa: co právě platí (uzavírky, odstávky), lhůty a termíny na 14 dní dopředu, pozdější a nová oznámení. Hledání, filtr obce, tématu a dne. Stejné oznámení vyvěšené víc obcemi je jen jednou. |
| Detail oznámení | `#/o/<id>` | Co můžu udělat a do kdy, co to znamená, klíčové údaje a data, přílohy s popisem obsahu, odkaz na původní znění. |
| Kalendář akcí | `#/akce` | Akce z kalendářů na webech obcí na 60 dní: měsíc nebo seznam, filtr obcí. |
| Zpravodaje | `#/zpravodaje` | Nejnovější číslo každého zpravodaje a archiv po ročnících. Jen odkazy na weby obcí, bez AI. |
| Obce | `#/obce`, `#/obec/<slug>` | Karta každé obce (počty oznámení, lhůt a akcí, nejbližší termín) a stránka obce: deska, akce, zpravodaj, odkaz na web a úřední desku. |

Web je statický (`web/`, bez buildu), umí světlý a tmavý vzhled a funguje na telefonu. U každé položky je
znak a barva obce.

## Obce a zdroje dat

| Obec | Úřední deska | Kalendář akcí | Zpravodaj | Znak |
|---|---|---|---|---|
| Dolní Břežany | iMunis | Vismo | Dolnobřežanský Rozhled | `web/erby` |
| Vrané nad Vltavou | iMunis | články na webu | Zrcadlo | `web/erby` |
| Zvole | Galileo | příspěvky Polydomu | Zvolské noviny | `web/erby` |
| Psáry | RSS (ALIS) | seznam odkazů | Psárský zpravodaj | Commons |
| Libeř | Vismo | Vismo | nevydává | Commons |
| Ohrobec | HTML (web obce) | RDC | Ohrobecký zpravodaj | `web/erby` |
| Vestec | edesky.cz | seznam odkazů | Vestecké listy | Commons |
| Jesenice | edesky.cz | seznam odkazů | Jesenický kurýr | Commons |
| Zlatníky-Hodkovice | hledá se automaticky | Munipolis | nevydává | `web/erby` |
| Březová-Oleško | edesky.cz | seznam odkazů | nevydává | Commons |
| Okrouhlo | edesky.cz | – | nevydává | bez znaku (štít s písmenem) |

Všechno je nastavené v [`harvester/uredni_desky/data/region.json`](harvester/uredni_desky/data/region.json),
viz [Úprava obce v konfiguraci](#úprava-obce-v-konfiguraci).

## Jak to funguje

Hodinový workflow **Sběr desek mikroregionu a nasazení** (`harvest.yml`) spustí
`python -m uredni_desky region`:

1. **Desky obcí:** zdroje z `region.json`; obce bez známé desky se hledají automaticky (web obce
   z Wikidat → rozpoznání systému), výsledek se pamatuje týden (`region_sources.json`).
2. **Stažení desek** adaptéry: iMunis, Galileo, Vismo, RSS/ALIS, obecné HTML a API edesky.cz.
   Sejmutá oznámení zůstávají v archivu, web je ukazuje ještě 30 dní.
3. **Přílohy** aktivních oznámení: text z PDF, DOCX a ODT; druh přílohy (text, mapa nebo výkres, sken
   s OCR přes tesseract). Každá příloha se stahuje jednou a pak se jen jednou za 24 hodin ověří
   podmíněným dotazem (`ETag` / `Last-Modified`). U desek z edesky.cz se bere rozpoznaný text
   z veřejného souboru `edesky.cz/dokument/<id>.txt` a odkaz na originál na webu obce.
4. **Popis oznámení:** pravidla (téma, lhůty, termíny, parcely, částky) a k tomu volitelně
   [shrnutí od Claude](#shrnutí-od-claude).
5. **Zpravodaje** (archivy na webech obcí, nejvýš jednou za 12 hodin) a **akce** (kalendáře obcí,
   nejvýš jednou za 6 hodin). Při chybě zůstane poslední známý stav.
6. **Výstup:** `site/data/region.json` + web z `web/` + znaky z větve `assets` → GitHub Pages.
   Stav sběru se ukládá do větve `data`.

### Větve

| Větev | Obsah |
|---|---|
| `main` | Kód a web. Sběr a nasazení běží jen odsud. |
| `data` | Stav sběru (jeden commit bez historie): oznámení, přílohy, shrnutí, zpravodaje, akce. |
| `assets` | Znaky obcí stažené z Wikimedia Commons (`erby/*.png`, `erby/index.json`). |

### Workflow

| Workflow | Spouštění | Co dělá |
|---|---|---|
| `harvest.yml` – Sběr desek mikroregionu a nasazení | každou hodinu, ručně, push do `main` | sběr, shrnutí, nasazení webu |
| `erby.yml` – Znaky obcí | ručně, změna `region.json` | stáhne chybějící znaky z Commons (při odmítnutí přes proxy) do větve `assets` |
| `discover.yml` – Hledání desek všech obcí | ručně | celostátní hledání desek (viz níže) |
| `probe.yml`, `diagnose.yml`, `explore.yml` | ručně | ladění adaptérů, diagnostika nedostupných desek, průzkum NKOD |

## Lokální vývoj

| Příkaz | Co dělá |
|---|---|
| `make venv` | `.venv` a harvester v editable režimu |
| `make test` | pytest |
| `make sample` | ukázková data do `web/data/region.json` (bez sítě) |
| `make harvest` | skutečný sběr mikroregionu (stav do `.state`, data do `web/data`) |
| `make serve` | web na http://localhost:8000 |

Lokálně chybí větev `assets`, takže se zobrazí jen znaky z `web/erby`. Znaky z Commons stáhne
`python -m uredni_desky erby --out assets`.

## Nastavení repozitáře

Settings → Secrets and variables → Actions:

| Název | Druh | K čemu |
|---|---|---|
| `EDESKY_API_KEY` | secret | API edesky.cz (desky Vestce, Jesenice, Březové-Oleška a Okrouhla). Klíč z profilu na edesky.cz; v logu se maskuje. |
| `HARVEST_FALLBACK_PROXY` | secret | Záložní proxy v ČR pro weby, které z GitHubu nejdou, a pro Wikimedia Commons. Viz [Proxy v ČR](#proxy-v-čr). |
| `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`, `ANTHROPIC_SERVICE_ACCOUNT_ID`, `ANTHROPIC_WORKSPACE_ID` | variables | Přihlášení ke Claude bez klíče (doporučeno). |
| `ANTHROPIC_API_KEY` | secret | Alternativa: přihlášení API klíčem; má přednost před federací. |
| `AI_MODEL`, `AI_LIMIT`, `AI_EFFORT` | variables | Model (výchozí `claude-haiku-5-5`), nejvýš shrnutí za běh (100), hloubka čtení (`medium`). |

### Přihlášení ke Claude bez klíče (Workload Identity Federation)

GitHub Actions se u Claude prokáže krátkodobým tokenem (OIDC), v repozitáři není žádný klíč.
Pravidlo přijme jen token z `twarr58/dolnobrezansko` a větve `main`; pull requesty (i z forků) mají
jiný ref a neprojdou.

1. **Claude Console:** Settings → Workload identity → **Connect workload** → **GitHub Actions**.
   - Rule name `uredni-desky-main`, organization `twarr58`, repository `dolnobrezansko`
   - Branch / ref `refs/heads/main`
   - Allowed events: ne „Only push“ (sběr běží i podle plánu a ručně)
   - Target: nový service account (např. `uredni-desky`), role developer, výchozí workspace
2. **GitHub:** do **Variables** přidat ID, která průvodce ukáže (nejsou tajná): `ANTHROPIC_FEDERATION_RULE_ID`
   (`fdrl_…`), `ANTHROPIC_ORGANIZATION_ID`, `ANTHROPIC_SERVICE_ACCOUNT_ID` (`svac_…`) a
   `ANTHROPIC_WORKSPACE_ID` (`wrkspc_…`, jen když pravidlo platí pro víc workspaců).
3. Actions → *Sběr desek mikroregionu a nasazení* → **Run workflow**. V logu kroku sběru má být
   „přihlášení přes Workload Identity Federation“ a „Claude: N shrnutí“.

Harvester si token vyžádá sám (workflow má `id-token: write`) a každé 4 minuty ho obnovuje.

**API klíčem:** Console → Settings → API keys → **Create key** (s expirací) → secret `ANTHROPIC_API_KEY`.
Útratu omezí limit v Console a `AI_LIMIT`.

## Shrnutí od Claude

Bez přihlášení se popisy dělají jen pravidly. S přihlášením Claude u nového nebo změněného oznámení
přečte přílohy (prvních 10; PDF do 100 stran celé včetně map a skenů, delší jako text; u oznámení bez
příloh i text ze stránky oznámení) a vrátí:

- **rychlou vrstvu:** jedna věta, co udělat a do kdy, důležitost;
- **rozbor:** co to znamená, koho se to týká, parametry, částky, změny, klíčové údaje, data, kontakt
  a obsah každé přílohy.

**Kdy se shrnuje znovu:** každé oznámení má otisk z názvu, data vyvěšení, lhůty a obsahu příloh
(SHA-256). Když úřad vymění přílohu na stejné adrese, přidá přílohu nebo posune lhůtu, otisk se změní
a Claude oznámení shrne znovu; web u něj ukáže „změna“. Do té doby zůstává předchozí shrnutí. Nezměněná
oznámení se znovu neshrnují. Shrnutí jsou ve větvi `data` (`summaries.json.gz`).

Změna obsahu shrnutí (prompt, schéma) zvýší `ai.VERSION` a stará shrnutí se pak postupně přepočítají.

## Úprava obce v konfiguraci

Obec v `harvester/uredni_desky/data/region.json`:

| Klíč | Význam |
|---|---|
| `name`, `publisher`, `qid` | název, úřad, Wikidata ID |
| `slug`, `color` | adresa stránky obce (`#/obec/<slug>`) a barva obce na webu |
| `web` | web obce |
| `source` | úřední deska: `{"fmt": "imunis", "subdomain": …}`, `{"fmt": "galileo" \| "vismo" \| "rss" \| "html" \| "edesky", "url": …}`; bez `source` se deska hledá automaticky |
| `events` | kalendář akcí: `fmt` `vismo`, `rdc`, `article`, `linklist`, `posts`, `munipolis` nebo `link` (jen odkaz); popis v `events.py` |
| `newsletter` | zpravodaj (`name`, `url`, `match` …; popis v `newsletters.py`), `false` = obec zpravodaj nevydává |
| `erb` | znak v repozitáři, např. `erby/zvole.svg` (soubor ve `web/erby`) |
| `erb_commons`, `erb_qid` | soubor znaku na Wikimedia Commons, případně Wikidata ID, podle kterého se znak dohledá (P94); stáhne ho workflow `erby.yml` |

## Proxy v ČR

Runnery GitHub Actions běží v USA a část obecních webů je nepustí (timeout, HTTP 403). Wikimedia
Commons zase sdíleným adresám odpovídá „429 Too many requests“. Harvester proto zkusí záložní proxy
(`HARVEST_FALLBACK_PROXY`, např. `http://uzivatel:heslo@proxy.example.cz:3128`), kde přímý přístup
selže; server, který jednou přímo nejde, pak v běhu jede rovnou přes proxy.

Proxy na VPS s českou IP nastaví skript [`deploy/proxy/setup-squid-debian12.sh`](deploy/proxy/setup-squid-debian12.sh):
squid s heslem, jen weby obcí mikroregionu (žádná otevřená proxy), bez mezipaměti, firewall nftables,
automatické bezpečnostní aktualizace; na konci vypíše hodnotu secretu. Heslo jde k proxy nešifrovaně
(HTTP), proto proxy pouští jen vybrané weby. Skript `deploy/proxy/probe-pages.py` na VPS vypíše
strukturu stránek pro ladění adaptérů.

Druhá možnost je self-hosted runner v ČR (`runs-on: [self-hosted, linux]` v jobu `harvest`).
Repozitář je veřejný, proto ho spouštět jen z `main`, ne z pull requestů.

## Formát dat pro web

`region.json`:

- `generated_at`, `region`, `ai_model`, `categories` (témata)
- `members[]`: `name`, `slug`, `color`, `erb`, `publisher`, `web`, `board_url`, `fmt`, `ok`, `count`,
  `connected`, `status`, `error`
- `items[]`: `id`, `o` (obec), `t` (úřední název), `url` (původní znění), `p` (vyvěšeno), `u` (do),
  `cj`, `r` (sejmuto), `fs` (poprvé viděno),
  `docs[]` (`n` název, `url`, `k` text \| mapa \| sken \| obrazek, `x` přípona, `pg` strany, `ocr`,
  `w` co v ní je, `sum` shrnutí přílohy),
  `s` (popis: `cat`, `label`, `tldr`, `action`, `deadline`, `event`, `event_end`, `time`, `where`,
  `impact`, `affects`, `bullets[]`, `facts[]`, `dates[]`, `contact`, `importance`, `src` `pravidla` \| `claude` …)
- `newsletters[]`: `obec`, `name`, `archive`, `issues[]` (`t`, `url`, `y` rok, `fs` poprvé viděno), `ok`
  nebo `none` (obec zpravodaj nevydává)
- `events`: `sources[]` (kalendáře obcí), `items[]` (`o`, `t`, `d`, `end`, `time`, `place`, `tags`, `url`)

## Celostátní režim (vypnutý)

Původní sběr všech desek ČR z NKOD a webů obcí (`python -m uredni_desky harvest`, `discover`, `probe`,
`diagnose`) zůstává v kódu a slouží jako zdroj adaptérů. Workflow `discover.yml` vezme z Wikidat všechny
obce, stáhne jejich weby a rozpozná systém desky (iMunis, Galileo, Vismo, RSS/Atom); nalezené zdroje uloží
do `discovered.json` ve větvi `data`. Statistika nepodporovaných systémů ukazuje, pro které psát další
adaptéry. Obce na iMunis a Galileo mimo NKOD jdou přidat do `data/extra_sources.json`.

## Známá omezení a nápady

- **Plánované spouštění na GitHubu je nespolehlivé:** `cron: "17 * * * *"` má spouštět sběr každou
  hodinu, ve skutečnosti GitHub spustí 3–4 běhy denně se zpožděním. Spolehlivější je volat
  `workflow_dispatch` cronem z VPS.
- API edesky.cz neuvádí „vyvěšeno do“; místo data vyvěšení dává datum načtení a bere se okno 60 dní.
- Okrouhlo nemá znak na Wikidatech ani Commons, web ukazuje štít s písmenem.
- Nápady: náhled první strany mapy v detailu, odběr novinek (RSS / e-mail) pro jednu obec.
