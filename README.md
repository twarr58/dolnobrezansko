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
| Samospráva | `#/samosprava` | Co letos rozhodly rady a zastupitelstva: zasedání podle data, hlavní věta a jednotlivé body usnesení (schvaluje, bere na vědomí, ukládá…), filtr obce, orgánu a „jen důležité“. |
| Obce | `#/obce`, `#/obec/<slug>` | Karta každé obce (počty oznámení, lhůt a akcí, nejbližší termín) a stránka obce: deska, poslední usnesení, akce, zpravodaj, kontakt na úřad (adresa, telefon, e-mail, datová schránka, IČO, úřední hodiny). |

Web je statický (`web/`, bez buildu), umí světlý a tmavý vzhled a funguje na telefonu. U každé položky je
znak a barva obce.

## Obce a zdroje dat

| Obec | Úřední deska | Kalendář akcí | Zpravodaj | Usnesení | Znak |
|---|---|---|---|---|---|
| Dolní Břežany | iMunis | Vismo | Dolnobřežanský Rozhled | rada + ZO (Vismo po letech, `.doc`) | `web/erby` |
| Vrané nad Vltavou | iMunis | články na webu | Zrcadlo | zápisy ZO (PDF) | `web/erby` |
| Zvole | Galileo | příspěvky Polydomu | Zvolské noviny | rada + ZO (PDF, skeny) | `web/erby` |
| Psáry | RSS (ALIS) | seznam odkazů | Psárský zpravodaj | rada + ZO (stránky, `.doc`) | Commons |
| Libeř | Vismo | Vismo | nevydává | ZO (stránky) | Commons |
| Ohrobec | HTML (web obce) | RDC | Ohrobecký zpravodaj | ZO (PDF) | `web/erby` |
| Vestec | edesky.cz | seznam odkazů | Vestecké listy | rada + ZO (PDF) | Commons |
| Jesenice | edesky.cz | seznam odkazů | Jesenický kurýr | rada (po letech) + ZO (soubory) | Commons |
| Zlatníky-Hodkovice | hledá se automaticky | Munipolis | nevydává | zápisy ZO (Munipolis) | `web/erby` |
| Březová-Oleško | edesky.cz | seznam odkazů | nevydává | zápisy ZO (Vismo po letech) | Commons |
| Okrouhlo | edesky.cz | – | nevydává | na webu nezveřejňuje | bez znaku (štít s písmenem) |

Všechno je nastavené v [`harvester/uredni_desky/data/region.json`](harvester/uredni_desky/data/region.json),
viz [Úprava obce v konfiguraci](#úprava-obce-v-konfiguraci). Kontakty úřadů jsou v
[`data/contacts.json`](harvester/uredni_desky/data/contacts.json) (ze stránek obcí, se zdrojem a datem ověření).

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
| `harvest.yml` – Sběr desek mikroregionu a nasazení | každou hodinu, ručně, merge změny kódu harvesteru (`harvester/uredni_desky/**`) nebo webu (`web/**`) | sběr, shrnutí, nasazení webu |
| `test.yml` – Testy | pull request, merge změny závislostí, testů nebo workflow, ručně | jen `pytest` – sběr ani nasazení nespouští (merge PR od Dependabotu) |
| `erby.yml` – Znaky obcí | ručně, merge změny `region.json` nebo `erby.py` | stáhne chybějící znaky z Commons (při odmítnutí přes proxy) do větve `assets` |
| `discover.yml` – Hledání desek všech obcí | ručně | celostátní hledání desek (viz níže) |
| `pruzkum.yml` – Průzkum stránek obcí | ručně, push změny `deploy/pruzkum/**` | vypíše text a odkazy stránek obcí (při odmítnutí přes proxy) a nalezená usnesení |
| `probe.yml`, `diagnose.yml`, `explore.yml` | ručně | ladění adaptérů, diagnostika nedostupných desek, průzkum NKOD |

**Práva workflow:** kód, který zpracovává cizí soubory (PDF, obrázky, Word, OCR), běží jen s právem čtení
repozitáře. Do větví `data` a `assets` zapisuje samostatný úkol (`save-state`, `save`), který dostane hotový
výstup jako artefakt a nic cizího nespouští; web nasazuje úkol `deploy`.

**Aktualizace závislostí:** Dependabot (`.github/dependabot.yml`) otevírá každé pondělí PR s aktualizacemi
balíčků harvesteru a akcí GitHubu; testy v PR je ověří.

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
| `PROXY_SSH_KEY`, `PROXY_SSH_KNOWN_HOSTS`, `PROXY_SSH_DEST` | secret | SSH tunel k proxy (klíč, otisk serveru, cíl). Vypíše je skript proxy. Viz [Proxy v ČR](#proxy-v-čr). |
| `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`, `ANTHROPIC_SERVICE_ACCOUNT_ID`, `ANTHROPIC_WORKSPACE_ID` | variables | Přihlášení ke Claude bez klíče (doporučeno). |
| `ANTHROPIC_API_KEY` | secret | Alternativa: přihlášení API klíčem; má přednost před federací. |
| `AI_MODEL`, `AI_LIMIT`, `AI_EFFORT` | variables | Model (výchozí `claude-haiku-5-5`), nejvýš shrnutí za běh (100), hloubka čtení (`medium`). |
| `COUNCIL_AI_LIMIT` | variable | Nejvýš rozepsaných zasedání rad a zastupitelstev za běh (výchozí 40). |

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

### Odkazy na parcely do katastru

Modul `parcely.py` najde v textech shrnutí (oznámení i usnesení) čísla parcel („parc. č. 259/13“, „pozemky
p. č. 970/5, 970/7 a 983“, „st. 217“) a k nim katastrální území: z textu („k. ú. Oleško u Zvole“, kód k. ú.,
„v Dolních Břežanech“, „Dolní Břežany: parc. č. …“), z ostatních údajů oznámení, nebo z obce, když má jediné
k. ú. U obce s víc k. ú. (Dolní Břežany, Jesenice, Psáry, Libeř, Zlatníky-Hodkovice, Březová-Oleško) bez
údaje o k. ú. vznikne odkaz jen tehdy, když parcela existuje v jediném z nich. Parcely v k. ú. mimo region
odkaz nedostanou. Číslo parcely převede veřejná služba ČÚZK (RÚIAN, `ags.cuzk.gov.cz`) na ID parcely; web
z něj udělá odkaz do Nahlížení do KN (`nahlizenidokn.cuzk.gov.cz/ZobrazObjekt.aspx?typ=parcela&id=…`).
Nalezená ID se pamatují ve stavu (`parcely.json`), dotazů je nejvýš 60 za běh. Odkaz se na webu ukáže jen
tam, kde text o parcele opravdu mluví (ne např. u „kancelář č. 1“).

### Usnesení rad a zastupitelstev

Modul `samosprava.py` jednou za 12 hodin projde u každé obce stránky s usneseními a zápisy (zdroje
v `region.json` pod klíčem `council`, formáty `vismo_years`, `pages`, `files`, `munipolis`) a vezme
dokumenty letošního roku. Každý dokument stáhne jednou (PDF, DOCX i starý `.doc`) a Claude ho rozloží
na body: datum a číslo zasedání, hlavní věta a jednotlivá usnesení – co orgán udělal (schvaluje, bere
na vědomí, ukládá…), co přesně, částka a důležitost. Procedurální body (program, ověřovatelé) vynechá.
Skenovaná PDF dostane Claude celá. Shrnutí zasedání se dělá jednou; limit za běh je proměnná
`COUNCIL_AI_LIMIT` (výchozí 40), zbytek se doplní v dalších bězích. Stav je ve větvi `data`
(`council.json`). Bez přihlášení ke Claude web ukáže jen odkazy na dokumenty.

Ladění: `python -m uredni_desky samosprava [--obec Psáry]` vypíše nalezené dokumenty bez stahování
(lokálně i ve workflow `pruzkum.yml`).

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
| `council` | usnesení a zápisy: seznam `{"body": "rada" \| "zastupitelstvo", "fmt": "vismo_years" \| "pages" \| "files" \| "munipolis", "url", "match"}`; popis v `samosprava.py` |
| `erb` | znak v repozitáři, např. `erby/zvole.svg` (soubor ve `web/erby`) |
| `erb_commons`, `erb_qid` | soubor znaku na Wikimedia Commons, případně Wikidata ID, podle kterého se znak dohledá (P94); stáhne ho workflow `erby.yml` |

## Proxy v ČR

Runnery GitHub Actions běží v USA a část obecních webů je nepustí (timeout, HTTP 403). Wikimedia
Commons zase sdíleným adresám odpovídá „429 Too many requests“. Harvester proto zkusí záložní proxy
(`HARVEST_FALLBACK_PROXY`, např. `http://uzivatel:heslo@proxy.example.cz:3128`), kde přímý přístup
selže; server, který jednou přímo nejde, pak v běhu jede rovnou přes proxy.

Proxy na VPS s českou IP nastaví skript [`deploy/proxy/setup-squid-debian12.sh`](deploy/proxy/setup-squid-debian12.sh)
(spustit jako root na Debianu 12/13; jde spustit znovu):

- **squid s heslem** poslouchá jen na `127.0.0.1:3128`, pouští jen weby obcí mikroregionu (žádná otevřená
  proxy) a nic neukládá;
- **SSH tunel:** z internetu se k proxy dostane jen šifrovaným tunelem. Uživatel `harvest-tunnel` se
  přihlásí jen klíčem, nemá shell a smí jedině přesměrovat port na `127.0.0.1:3128`;
- **SSH jen s klíči:** přihlášení heslem se vypne, pokud už root nebo uživatel ze skupiny sudo klíč má
  (jinak skript varuje a hesla nechá, aby ses nezamkl); fail2ban blokuje opakované pokusy;
- **firewall nftables:** dovnitř jen SSH; automatické bezpečnostní aktualizace.

Na konci skript vypíše hodnoty secretů `PROXY_SSH_DEST`, `PROXY_SSH_KNOWN_HOSTS`, `PROXY_SSH_KEY` a
`HARVEST_FALLBACK_PROXY` (`http://harvest:…@127.0.0.1:3128`). Workflow `harvest.yml`, `erby.yml` a
`pruzkum.yml` si před sběrem otevřou tunel akcí [`.github/actions/proxy-tunnel`](.github/actions/proxy-tunnel/action.yml):
server ověří podle otisku (podvržený server tunel nedostane) a klíč po připojení smažou z disku.
Bez `PROXY_SSH_KEY` tunel nedělají a sběr jede bez proxy.

**Přechod ze starého nastavení (proxy na veřejném portu):** spustit nový skript na VPS, v GitHubu přidat
tři secrety `PROXY_SSH_*` a přepsat `HARVEST_FALLBACK_PROXY` hodnotou s `127.0.0.1`. Do té doby proxy
nefunguje (port je zavřený), weby, které z GitHubu přímo nejdou, se v tu chvíli nenačtou.

**Přehled stavu na VPS:** [`deploy/proxy/proxy-status.sh`](deploy/proxy/proxy-status.sh) vypíše služby
(squid, ssh, fail2ban, nftables, automatické aktualizace), na čem proxy poslouchá, požadavky za hodinu a 24 h,
nejčastější weby, připojení tunelu z GitHubu (poslední a počet za 24 h), stav SSH hesel, firewallu a fail2ban,
paměť a disk. Jen čte a nezávisí na setup skriptu (nic nepřenastaví). Instalace jednou jako root:
`bash proxy-status.sh --install` → příkaz `proxy-status` (s testem požadavku přes proxy) a krátký přehled
po každém přihlášení. Vypnutí: smazat `/etc/profile.d/uredni-desky-status.sh`.

Skript `deploy/proxy/probe-pages.py` na VPS vypíše strukturu stránek pro ladění adaptérů.

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
- `council`: `sources[]` (`obec`, `body`, `url`, `ok`, `n`), `meetings[]` (`id`, `o`, `body` rada \| zastupitelstvo,
  `kind` usneseni \| zapis, `date`, `no`, `t`, `url`, `x`, `tldr`, `points[]` (`v` sloveso, `t`, `cat`, `amt`, `imp`), `src`)
- `items[].pl`, `council.meetings[].pl`: odkazy na parcely `{„259/13“: [ID parcely v RÚIAN, „k. ú.“]}`
- `members[].contact`: `office`, `address`, `phone[]`, `email[]` nebo `form`, `ds`, `ico`, `hours[]`, `hours_note`,
  `hours_url`, `source`, `checked`

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
