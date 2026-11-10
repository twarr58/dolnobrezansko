// Úřední deska Dolnobřežansko – statický frontend bez buildu, varianta „Kalendář“.
// Data: data/region.json (generuje `python -m uredni_desky region`).

const DATA = './data/region.json';
const HORIZON = 14; // dní dopředu na časové ose; pozdější termíny jdou do „Později“
const NEW_DAYS = 3; // štítek „nové“
const STEP = 20;
const DAY_MS = 86400000;
const WEEKDAYS = ['ne', 'po', 'út', 'st', 'čt', 'pá', 'so'];
const WEEKDAYS_LONG = ['neděle', 'pondělí', 'úterý', 'středa', 'čtvrtek', 'pátek', 'sobota'];
const RESTRICTIONS = new Set(['doprava', 'energie', 'voda']); // akce, která omezuje (uzavírka, odstávka)

const state = { data: null, filter: { obec: '', tema: '', q: '', den: '', lhuta: false, nove: false, moje: false }, limit: STEP, listScroll: null };

// ---------- pomocné funkce ----------

/** Vytvoří element. Text jde jen jako textový uzel – data z desek jsou nedůvěryhodná. */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false || c === '') continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

function icon(path, size = 18) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const attrs = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' };
  for (const [k, v] of Object.entries(attrs)) svg.setAttribute(k, v);
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', path);
  svg.append(p);
  return svg;
}

const ICON_BACK = 'M15 5l-7 7 7 7';
const ICON_DOC = 'M6 2h9l5 5v15H6zM14 2v6h6M9 13h8M9 17h6';
const ICON_MAP = 'M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15';
const ICON_SCAN = 'M3 4h18v16H3zM7 9h10M7 13h6';
const ICON_IMG = 'M3 4h18v16H3zM3 16l5-5 4 4 3-3 6 6';

const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);
const fold = (s) => (s || '').normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
const plural = (n, one, few, many) => (n === 1 ? one : n >= 2 && n <= 4 ? few : many);
const isIso = (s) => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s);

// Data se počítají jako kalendářní dny v UTC – přechod na letní čas neposune den.
function localTodayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
let TODAY = localTodayIso();
const utc = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
const addDays = (iso, n) => new Date(utc(iso) + n * DAY_MS).toISOString().slice(0, 10);
const daysTo = (iso) => Math.round((utc(iso) - utc(TODAY)) / DAY_MS);
const weekday = (iso) => WEEKDAYS[new Date(utc(iso)).getUTCDay()];
const weekdayLong = (iso) => WEEKDAYS_LONG[new Date(utc(iso)).getUTCDay()];
const dm = (iso) => `${+iso.slice(8, 10)}.${+iso.slice(5, 7)}`;

function dateCz(iso, { year = false, wd = false } = {}) {
  if (!isIso(iso)) return '';
  const y = +iso.slice(0, 4);
  const showYear = year || y !== +TODAY.slice(0, 4);
  return `${wd ? `${weekday(iso)} ` : ''}${+iso.slice(8, 10)}. ${+iso.slice(5, 7)}.${showYear ? ` ${y}` : ''}`;
}

/** „ 5. 11. 2026 v 18:16“ z ISO času zpracování (v místním čase prohlížeče); prázdné, když čas chybí. */
function stampCz(iso) {
  const d = typeof iso === 'string' && iso.length > 10 ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  return ` ${d.getDate()}. ${d.getMonth() + 1}. ${d.getFullYear()} v ${d.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' })}`;
}

/** claude-sonnet-5-5 → Claude Sonnet 5.5 */
function modelName(id) {
  const m = /^claude-([a-z]+)-(\d+)(?:-(\d+))?$/.exec(id || '');
  if (!m) return 'Claude';
  return `Claude ${m[1].charAt(0).toUpperCase()}${m[1].slice(1)} ${m[2]}${m[3] ? `.${m[3]}` : ''}`;
}

function relDay(iso) {
  const n = daysTo(iso);
  if (n === 0) return 'dnes';
  if (n === 1) return 'zítra';
  if (n === 2) return 'pozítří';
  if (n > 2) return `za ${n} ${plural(n, 'den', 'dny', 'dní')}`;
  if (n === -1) return 'včera';
  return `před ${-n} dny`;
}

const label = (it) => (it.s?.label || 'Ostatní').toLowerCase();

/**
 * Nejbližší budoucí termín oznámení: {date, kind, type, until?}.
 * kind = barva (warn: lhůta/omezení, event: jednání/akce, start: probíhá), type = co to je.
 */
function when(it, { all = false } = {}) {
  const s = it.s || {};
  if (it.r) return null;
  if (s.foreign && !all) return null; // písemnost jiného úřadu: v přehledu bez lhůt a termínů (detail je ukáže)
  const c = [];
  if (isIso(s.deadline) && daysTo(s.deadline) >= 0) c.push({ date: s.deadline, kind: 'warn', type: 'deadline' });
  if (isIso(s.event)) {
    const restrict = RESTRICTIONS.has(s.cat);
    if (daysTo(s.event) >= 0) {
      c.push({ date: s.event, kind: restrict ? 'warn' : 'event', type: 'event', until: isIso(s.event_end) ? s.event_end : null });
    } else if (isIso(s.event_end) && daysTo(s.event_end) >= 0) {
      c.push({ date: TODAY, kind: 'start', type: 'running', until: s.event_end });
    }
  }
  c.sort((a, b) => a.date.localeCompare(b.date) || (a.type === 'deadline' ? -1 : 1));
  return c[0] || null;
}

function deadlineWord(s) {
  const a = fold(s.action);
  if (a.includes('namit')) return 'námitky';
  if (a.includes('pripomin')) return 'připomínky';
  if (a.includes('nabid')) return 'nabídky';
  if (a.includes('zadost')) return 'žádosti';
  return 'lhůta';
}

function entryMeta(it, w) {
  const s = it.s || {};
  if (w.type === 'deadline') return `poslední den: ${deadlineWord(s)}`;
  if (w.type === 'running') return `probíhá do ${dm(w.until)}`;
  return [s.time, w.until && w.until !== w.date ? `až ${dm(w.until)}` : null].filter(Boolean).join(' · ');
}

/** „Magistrát města Kladna“ → „Kladno“ by bylo hádání; jen zkrátit dlouhé názvy. */
const shortIssuer = (x) => (x.length > 40 ? `${x.slice(0, 38)}…` : x);

function badges(it) {
  const s = it.s || {};
  return [
    s.foreign ? h('span', { class: 'badge badge--foreign', title: s.foreign_why || 'Obec oznámení jen vyvěšuje za jiný úřad' }, `jiný úřad: ${shortIssuer(s.foreign)}`)
      : s.importance === 'vysoka' ? h('span', { class: 'badge badge--hot', title: s.importance_why || null }, 'důležité') : null,
    visit.last ? (itemFresh(it) ? freshBadge() : null)
      : isIso(it.p) && daysTo(it.p) >= -NEW_DAYS && !it.r && !s.foreign ? h('span', { class: 'badge' }, 'nové') : null,
    (isIso(s.updated) && daysTo(s.updated) >= -7) || s.pending
      ? h('span', { class: 'badge badge--hot', title: 'Úřad oznámení upravil (příloha nebo termín)' }, 'změna') : null,
  ];
}

const detailHref = (it) => `#/o/${encodeURIComponent(it.id)}`;

// ---------- novinky od poslední návštěvy a „Moje obce“ ----------
// Jen v úložišti tohoto prohlížeče (localStorage), nic se neodesílá. Na jiném zařízení nebo v anonymním okně
// web novinky nezná a chová se jako při první návštěvě. Loginy nejsou a nebudou.

const LS_LAST = 'dl-last-visit'; // konec předchozí návštěvy – od něj se počítají novinky
const LS_SEEN = 'dl-seen-at';    // poslední aktivita; obnovení stránky do 30 min = pořád stejná návštěva
const LS_MINE = 'dl-mine';       // Moje obce (JSON pole názvů)
const VISIT_GAP_MS = 30 * 60 * 1000;
const visit = { last: null };    // ISO konce předchozí návštěvy; null = první návštěva nebo bez úložiště

const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => {
  try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* bez úložiště jen pro tuto návštěvu */ }
};
const touchVisit = () => lsSet(LS_SEEN, new Date().toISOString());

function startVisit() {
  const seen = Date.parse(lsGet(LS_SEEN) || '');
  if (Number.isFinite(seen) && Date.now() - seen < VISIT_GAP_MS) visit.last = lsGet(LS_LAST); // pokračuje stejná návštěva
  else {
    visit.last = Number.isFinite(seen) ? new Date(seen).toISOString() : null;
    lsSet(LS_LAST, visit.last);
  }
  touchVisit();
}

/** Přibylo od poslední návštěvy? fs = kdy se položka poprvé objevila (z harvesteru). */
function isFresh(fs) {
  if (!visit.last || typeof fs !== 'string') return false;
  const t = Date.parse(fs);
  return Number.isFinite(t) && t > Date.parse(visit.last);
}

// Sběr občas dožene starší věci: nově zprovozněná nebo změněná deska obce, dávka starších usnesení.
// Ty se poprvé objeví teď, ale novinky to nejsou – novinkou je jen věc, která je čerstvá i sama o sobě.
const FRESH_ITEM_DAYS = 7;     // oznámení: vyvěšeno nejvýš týden před tím, než ho sběr uviděl
const FRESH_MEETING_DAYS = 30; // usnesení vychází i několik týdnů po zasedání

/** Novinka pro vracejícího se návštěvníka: poprvé viděno po minulé návštěvě a datum (vyvěšení, zasedání) blízko. */
function isNewFor(fs, date, days) {
  if (!isFresh(fs)) return false;
  return !isIso(date) || Date.parse(fs) - Date.parse(date) <= days * 86400e3;
}
const itemFresh = (it) => !it.r && !it.s?.foreign && isNewFor(it.fs, it.p, FRESH_ITEM_DAYS); // za jiný úřad není novinka
// zasedání bez data nejde posoudit – radši ho za novinku nepovažovat (bývá to dohnaný starší dokument)
const meetingFresh = (m) => isIso(m.date) && isNewFor(m.fs, m.date, FRESH_MEETING_DAYS);
const inMine = (o) => { const mine = getMine(); return !mine.size || mine.has(o); };

function markAllRead() {
  visit.last = new Date().toISOString();
  lsSet(LS_LAST, visit.last);
  touchVisit();
}

function getMine() {
  try {
    const v = JSON.parse(lsGet(LS_MINE) || '[]');
    return new Set(Array.isArray(v) ? v.filter((x) => typeof x === 'string' && OBCE.has(x)) : []);
  } catch { return new Set(); }
}

function toggleMine(name) {
  const mine = getMine();
  if (mine.has(name)) mine.delete(name); else mine.add(name);
  lsSet(LS_MINE, mine.size ? JSON.stringify([...mine]) : null);
  gc({ path: `moje obce: ${mine.has(name) ? 'přidat' : 'odebrat'}`, title: name, event: true });
  return mine.has(name);
}

function forgetMe() {
  for (const k of [LS_LAST, LS_SEEN, LS_MINE, HELP_STRIP_KEY]) lsSet(k, null);
  visit.last = null;
  touchVisit();
}

/** Co přibylo od poslední návštěvy (v Mých obcích, jsou-li vybrané). null = první návštěva / bez úložiště. */
function news() {
  if (!visit.last || !state.data) return null;
  const mine = getMine();
  const ok = (o) => !mine.size || mine.has(o);
  const d = state.data;
  return {
    items: d.items.filter((it) => ok(it.o) && itemFresh(it)).length,
    events: (d.events?.items || []).filter((e) => ok(e.o) && isFresh(e.fs)).length,
    issues: (d.newsletters || []).filter((n) => ok(n.obec)).reduce((k, n) => k + (n.issues || []).filter((i) => isFresh(i.fs)).length, 0),
    meetings: (d.council?.meetings || []).filter((m) => ok(m.o) && meetingFresh(m)).length,
    mine,
  };
}

/** Štítek zapnutého filtru „jen nové pro vás“ s křížkem (akce, samospráva). */
const freshFilterChip = (off) => h('button', { type: 'button', class: 'fresh-filter', onclick: off, title: 'Zobrazit vše' },
  'jen nové pro vás', getMine().size ? ' · moje obce' : '', h('span', { 'aria-hidden': 'true' }, ' ×'));

const freshBadge = () => h('span', { class: 'badge badge--fresh', title: 'Přibylo od vaší poslední návštěvy' }, 'nové pro vás');
/** Štítek novinky: vracející se návštěvník „nové pro vás“ podle poslední návštěvy, první návštěva „nové“ podle stáří. */
const newBadge = () => (visit.last ? freshBadge() : h('span', { class: 'badge' }, 'nové'));

/** Hvězdička „Moje obce“ (na stránce obce). */
function mineStar(name) {
  const label = (on) => (on ? '★ Moje obec' : '☆ Přidat do Mých obcí');
  const btn = h('button', {
    type: 'button', class: 'band__btn band__star', 'aria-pressed': String(getMine().has(name)),
    title: 'Moje obce: novinky a přepínač „Moje obce“ na úvodu budou jen pro vybrané obce',
    onclick: () => { const on = toggleMine(name); btn.setAttribute('aria-pressed', String(on)); btn.textContent = label(on); },
  }, label(getMine().has(name)));
  return btn;
}

/** Lišta na úvodu: co přibylo od poslední návštěvy – jen když něco přibylo. */
function newsBar(setFilter) {
  const n = news();
  if (!n || !(n.items + n.events + n.issues + n.meetings)) return null;
  const dt = new Date(visit.last);
  const iso = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
  const time = dt.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' });
  const when = iso === TODAY ? `dnes ${time}` : `${weekdayLong(iso)} ${dateCz(iso)}`;
  const pill = (count, words, attrs) => (count ? h(attrs.href ? 'a' : 'button', { class: 'pill', ...attrs },
    `${count} ${plural(count, ...words)}`) : null);
  return h('aside', { class: 'newsbar', 'aria-label': 'Novinky od vaší poslední návštěvy' },
    h('p', {}, 'Od vaší poslední návštěvy ', h('strong', {}, `(${when})`), n.mine.size ? ' ve vašich obcích' : '', ' přibylo:'),
    h('div', { class: 'newsbar__row' },
      pill(n.items, ['oznámení', 'oznámení', 'oznámení'], { type: 'button', class: 'pill pill--solid', onclick: () => setFilter({ nove: true }) }),
      pill(n.issues, ['zpravodaj', 'zpravodaje', 'zpravodajů'], { href: '#/zpravodaje' }),
      pill(n.events, ['akce', 'akce', 'akcí'], { href: '#/akce?pohled=seznam&nove=1' }),
      pill(n.meetings, ['zasedání', 'zasedání', 'zasedání'], { href: '#/samosprava?nove=1' }),
      h('span', { class: 'newsbar__acts' },
        n.items ? h('button', { type: 'button', class: 'linkish', onclick: () => setFilter({ nove: true }) }, 'Ukázat jen nové') : null,
        h('button', {
          type: 'button', class: 'linkish',
          onclick: () => { markAllRead(); gc({ path: 'novinky: mám přečteno', title: 'Lišta novinek', event: true }); state.filter.nove = false; writeFilter(); route(); },
        }, 'Mám přečteno'))));
}

// ---------- obce: znak, barva, odkazy ----------

let OBCE = new Map(); // název → člen z dat (slug, color, erb, web, board_url …)
const obecOf = (name) => OBCE.get(name) || { name };
const obecHref = (name) => (obecOf(name).slug ? `#/obec/${obecOf(name).slug}` : `#/?obec=${encodeURIComponent(name)}`);
const isHexColor = (c) => typeof c === 'string' && /^#[0-9a-f]{6}$/i.test(c);
const obecStyle = (name) => (isHexColor(obecOf(name).color) ? `--oc:${obecOf(name).color}` : null);
const safeErb = (p) => (typeof p === 'string' && /^erby\/[a-z0-9-]+\.(svg|png|jpg|gif|webp)$/.test(p) ? p : null);

/** Znak obce; bez staženého znaku štít v barvě obce s počátečním písmenem. */
function crest(name, size = 20) {
  const erb = safeErb(obecOf(name).erb);
  if (erb) return h('img', { class: 'crest', src: erb, alt: '', width: size, height: Math.round(size * 1.15), loading: 'lazy', decoding: 'async' });
  return h('span', { class: 'crest crest--blank', style: `${obecStyle(name) || ''};width:${size}px;height:${Math.round(size * 1.15)}px;font-size:${Math.round(size * 0.55)}px`, 'aria-hidden': 'true' }, name.charAt(0));
}

/** Malý štítek obce do řádků: znak + název. */
const obecTag = (name) => h('span', { class: 'obec', style: obecStyle(name) }, crest(name, 14), name);
const obecTags = (names) => names.map((n, i) => [i ? h('span', { class: 'obec__sep' }, ', ') : null, obecTag(n)]);

/** Jednotné datum v textu: 2026-11-06 → 6. 11. 2026 (AI občas vrátí data v ISO tvaru). */
const czDates = (text) => String(text ?? '').replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (_, y, m, d) => `${+d}. ${+m}. ${y}`);

/** Texty shrnutí s daty v ISO tvaru převede na české (pole s datem pro výpočty – deadline, event … – zůstávají). */
function czTexts(data) {
  const TEXT = ['tldr', 'action', 'impact', 'affects', 'where', 'contact', 'importance_why'];
  for (const it of data.items || []) {
    const s = it.s;
    if (s) {
      for (const k of TEXT) if (typeof s[k] === 'string') s[k] = czDates(s[k]);
      if (Array.isArray(s.bullets)) s.bullets = s.bullets.map((b) => (typeof b === 'string' ? czDates(b) : b));
      for (const f of Array.isArray(s.facts) ? s.facts : []) if (typeof f?.value === 'string') f.value = czDates(f.value);
      for (const x of Array.isArray(s.dates) ? s.dates : []) if (typeof x?.what === 'string') x.what = czDates(x.what);
    }
    for (const d of it.docs || []) for (const k of ['sum', 'w']) if (typeof d[k] === 'string') d[k] = czDates(d[k]);
  }
}

// ---------- odkazy na parcely do katastru nemovitostí ----------

const KN_URL = (id) => `https://nahlizenidokn.cuzk.gov.cz/ZobrazObjekt.aspx?typ=parcela&id=${encodeURIComponent(id)}`;
const PARCEL_TRIGGER = /(?:parc(?:ela|ely|elách|ele|elu|elní|\.)?|p\.\s*č\.?|pozem(?:ek|ku|ky|cích|ků)|parcel\w*)/gi;
const PARCEL_FILLER = /(?<!\p{L})(?:a|i|až|st|parc|č|p|k|ú|kat|parcelní|číslo|čísla|čísel)(?!\p{L})\.?/giu;

/**
 * Text s odkazy na parcely: pl = {„259/13“: [ID parcely v RÚIAN, „k. ú.“]} z harvesteru. Odkaz jen tam, kde text
 * o parcele mluví (za „parc. č.“, „pozemky“… ve výčtu), aby se neprolinkovalo třeba „kancelář č. 1“.
 */
function withParcels(text, pl) {
  const str = String(text ?? '');
  const keys = pl && typeof pl === 'object'
    ? Object.keys(pl).filter((k) => /^(st\. )?\d{1,5}(\/\d{1,4})?$/.test(k) && Array.isArray(pl[k]) && Number.isFinite(+pl[k][0]))
    : [];
  if (!keys.length) return str;
  const alt = keys.sort((a, b) => b.length - a.length).map((k) => k.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&').replace(/\\\. /, '\\.\\s*'));
  const rx = new RegExp(`(?<![\\d/])(${alt.join('|')})(?![\\d/]|[.,]\\d)`, 'g');
  const out = [];
  let last = 0;
  for (const m of str.matchAll(rx)) {
    const before = str.slice(Math.max(0, m.index - 220), m.index);
    let trig = -1;
    for (const t of before.matchAll(PARCEL_TRIGGER)) trig = t.index + t[0].length;
    if (trig < 0) continue;
    const between = before.slice(trig).replace(/\([^)]*\)/g, ' ').replace(PARCEL_FILLER, ' ');
    if (!/^[\s\d/,;.:+–-]*$/.test(between)) continue;
    const key = m[1].replace(/\s+/g, ' ');
    const [id, ku] = pl[key] || pl[m[1]] || [];
    if (!id) continue;
    out.push(str.slice(last, m.index), h('a', {
      class: 'parcel', href: KN_URL(id), rel: 'noopener noreferrer', target: '_blank',
      title: `Parcela ${key}${ku ? `, k. ú. ${ku}` : ''} – otevřít v katastru nemovitostí`,
    }, m[1]));
    last = m.index + m[0].length;
  }
  if (!out.length) return str;
  out.push(str.slice(last));
  return out;
}

// ---------- odkazy na ulice do Google Map ----------

const MAPS_URL = (pt) => `https://www.google.com/maps/search/?api=1&query=${pt[0]},${pt[1]}`;
const STREET_NEXT_CAP = /^\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]\p{L}/u;

/**
 * Text s odkazy na ulice a silnice: ul = {„Malebná“: [šířka, délka], „Nádražní“: [šířka, délka, „č. p. 137“],
 * „II/101“: [šířka, délka]} z harvesteru (ulice.py). O tom, co je ulice, rozhodl harvester podle kontextu
 * (za „ulice“, ve výčtu, v údajích o místě, v křižovatce „A x B“) – web jen odkazuje názvy z ul. Neodkazuje,
 * když za názvem pokračuje slovo s velkým písmenem („Dolní“ v „Dolní Břežany“) nebo když nesedí velikost
 * prvního písmena („hlavní silnice“ není ulice Hlavní). Odkaz vede na bod v Google Mapách.
 */
function streetsIn(str, ul) {
  // [šířka, délka] = bod na ulici / silnici; [šířka, délka, „č. p. 137“] = bod na domě
  const names = Object.keys(ul).filter((n) => Array.isArray(ul[n]) && ul[n].length >= 2 && Number.isFinite(ul[n][0]) && Number.isFinite(ul[n][1]));
  if (!names.length) return str;
  const fold = new Map(names.map((n) => [n.toLocaleLowerCase('cs'), n]));
  const alt = names.sort((a, b) => b.length - a.length).map((n) => n.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')).join('|');
  const rx = new RegExp(`(?<![\\p{L}\\d/])(${alt})(?![\\p{L}\\d]|/\\d)`, 'giu');
  const out = [];
  let pos = 0;
  for (const m of str.matchAll(rx)) {
    const end = m.index + m[0].length;
    const name = fold.get(m[1].toLocaleLowerCase('cs'));
    if (!name || m[1][0] !== name[0] || STREET_NEXT_CAP.test(str.slice(end))) continue;
    const pt = ul[name];
    const road = /^I{1,3}\/\d/.test(name);
    out.push(str.slice(pos, m.index), h('a', {
      class: 'street', href: MAPS_URL(pt), rel: 'noopener noreferrer', target: '_blank',
      title: `${road ? 'Silnice ' : ''}${name}${typeof pt[2] === 'string' ? ` ${pt[2]}` : ''} – otevřít v Google Mapách`,
    }, m[1]));
    pos = end;
  }
  if (!out.length) return str;
  out.push(str.slice(pos));
  return out;
}

/**
 * Hledání místa v Google Mapách podle textu „KDE“ (pojmenovaná místa jako „KC Jurta u Potoka, konec ul. Černická“
 * nejsou v žádném registru): text bez závorek + obec oznámení, když text žádnou obec regionu nezmiňuje.
 */
function placeSearchUrl(where, obec) {
  let q = String(where || '').replace(/\([^)]*\)/g, ' ').replace(/\s+/g, ' ').trim();
  // obec připojit, jen když text žádnou obec regionu nezmiňuje („Nová Zvole“ už je Zvole – nepřidávat jinou)
  const named = (state.data?.members || []).some((m) => fold(q).includes(fold(m.name)));
  if (obec && !named) q = `${q}, ${obec}`;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q.slice(0, 200))}`;
}

/** Text s odkazy na parcely (katastr) i ulice (Google Mapy) podle údajů oznámení / zasedání (pl, ul). */
function withLinks(text, obj) {
  const p = withParcels(text, obj?.pl);
  const ul = obj?.ul && typeof obj.ul === 'object' ? obj.ul : null;
  if (!ul) return p;
  return (Array.isArray(p) ? p : [p]).flatMap((x) => (typeof x === 'string' ? [streetsIn(x, ul)] : [x])).flat();
}

// ---------- stejná oznámení více obcí ----------

const STOP = new Set(['obec', 'obce', 'obci', 'mezi', 'kvuli', 'bude', 'jsou', 'pro', 'oznameni', 'dolnimi', 'mesto']);
function words(it) {
  if (!it._w) it._w = new Set(fold(it.s?.tldr || it.t).split(/[^a-z0-9]+/).filter((x) => x.length >= 4 && !STOP.has(x)).map((x) => x.slice(0, 6)));
  return it._w;
}
function similar(a, b) {
  const wa = words(a);
  const wb = words(b);
  if (!wa.size || !wb.size) return false;
  let n = 0;
  for (const x of wa) if (wb.has(x)) n += 1;
  return n / Math.min(wa.size, wb.size) >= 0.6;
}
const whenKey = (w) => (w ? `${w.type}|${w.date}|${w.until || ''}` : '');

/** Sloučí stejné oznámení z více obcí (stejný termín, téma a podobný text) do jedné položky {it, w, also}. */
function mergeTwins(list) {
  const out = [];
  for (const x of list) {
    const twin = out.find((y) => whenKey(y.w) === whenKey(x.w) && y.it.s?.cat === x.it.s?.cat
      && y.it.o !== x.it.o && !y.also.some((z) => z.o === x.it.o) && similar(y.it, x.it));
    if (twin) twin.also.push(x.it); else out.push({ ...x, also: [] });
  }
  return out;
}

/** Stejné oznámení na deskách jiných obcí (pro detail). */
function twinsOf(it) {
  const k = whenKey(when(it));
  if (!k) return [];
  return state.data.items.filter((x) => x !== it && !x.r && x.o !== it.o && x.s?.cat === it.s?.cat && whenKey(when(x)) === k && similar(x, it));
}

// ---------- směrování ----------

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query = ''] = raw.split('?');
  const parts = path.split('/').filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { parts, params: new URLSearchParams(query) };
}

function mount(title, ...nodes) {
  document.title = title ? `${title} – Úřední deska Dolnobřežansko` : 'Úřední deska Dolnobřežansko';
  document.getElementById('app').replaceChildren(...nodes.flat(Infinity).filter(Boolean));
}

// ---------- návštěvnost (GoatCounter: bez cookies, bez osobních údajů) ----------
// Počítá se cesta bez parametrů (#/samosprava, #/obec/psary, #/o/<id>) – filtry ne, aby každé kliknutí
// na filtr nebylo nové zobrazení. Kliky na odkazy ven se počítají jako události podle druhu odkazu.

const SITE_TITLE = ' – Úřední deska Dolnobřežansko';
let lastCounted = null;

function gc(vars) {
  const g = window.goatcounter;
  if (g && typeof g.count === 'function') {
    try { g.count(vars); } catch { /* počítadlo nesmí rozbít web */ }
  } else {
    (window.gcQueue ||= []).push(vars); // skript se ještě načítá (index.html frontu vyprázdní)
  }
}

function countView() {
  const path = `/${parseHash().parts.join('/')}`;
  if (path === lastCounted) return;
  lastCounted = path;
  const title = document.title.endsWith(SITE_TITLE) ? document.title.slice(0, -SITE_TITLE.length) : 'Přehled';
  gc({ path, title: title.length > 120 ? `${title.slice(0, 119)}…` : title });
}

function linkKind(a) {
  if (a.classList.contains('parcel')) return 'katastr';
  if (a.classList.contains('street') || a.classList.contains('maplink')) return 'mapa';
  if (a.classList.contains('sitefoot__sponsor')) return 'podpora';
  if (a.classList.contains('doc')) return 'příloha';
  if (a.classList.contains('meeting__doc')) return 'usnesení';
  if (a.matches('.issue, .nl__latest, .nl__ext')) return 'zpravodaj';
  if (a.matches('.ev, .entry--event')) return 'akce';
  if (/^Původní znění/.test(a.textContent || '')) return 'originál';
  return 'odkaz';
}

function countOutbound(e) {
  if (e.type === 'auxclick' && e.button !== 1) return; // prostřední tlačítko = otevřít v nové kartě
  const a = e.target.closest?.('a[href^="http"]');
  if (!a) return;
  let host;
  try { host = new URL(a.href).hostname.replace(/^www\./, ''); } catch { return; }
  if (host === location.hostname) return;
  gc({ path: `${linkKind(a)}: ${host}`, title: (a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100), event: true });
}

// Počítadlo v patičce: veřejné počty návštěvníků z GoatCounteru (v jeho nastavení musí být zapnuté
// „Allow adding visitor counts on your website“). Odpovědi GoatCounter drží v mezipaměti až 4 hodiny.
// Když se počty nenačtou (vypnuté nastavení, blokátor, výpadek), patička zůstane skrytá.
const GC_COUNTER = 'https://dolnobrezansko.goatcounter.com/counter/TOTAL.json';

function visitors(n) {
  const word = n === 1 ? 'návštěvník' : n >= 2 && n <= 4 ? 'návštěvníci' : 'návštěvníků';
  return `${n.toLocaleString('cs-CZ')} ${word}`;
}

async function gcTotal(start) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);
  try {
    const res = await fetch(start ? `${GC_COUNTER}?start=${start}` : GC_COUNTER, { signal: ctrl.signal });
    if (!res.ok) return null;
    const digits = String((await res.json()).count ?? '').replace(/\D/g, ''); // „1,234“ → 1234
    return digits ? Number(digits) : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

const SPONSOR_URL = 'https://github.com/sponsors/twarr58';

/**
 * Patička na všech stránkách: odkaz na podporu provozu (vždy) a počty návštěvníků z GoatCounteru (když se
 * načtou). Neoficiální web – to říká i patička, ať nikdo nemá dojem, že přispívá obci.
 */
async function renderFooter() {
  const foot = document.getElementById('sitefoot');
  if (!foot) return;
  const stats = h('span', { class: 'sitefoot__stats', hidden: true });
  const how = h('span', { class: 'sitefoot__how', hidden: true }, 'návštěvnost měřena bez cookies a osobních údajů (',
    h('a', { href: 'https://www.goatcounter.com/', rel: 'noopener noreferrer', target: '_blank' }, 'GoatCounter'), ')');
  foot.replaceChildren(
    h('div', { class: 'sitefoot__in' },
      stats,
      h('a', { href: HELP_HREF }, 'Jak to funguje'),
      h('a', {
        class: 'sitefoot__sponsor', href: SPONSOR_URL, rel: 'noopener noreferrer', target: '_blank',
        title: 'Web je nekomerční a neoficiální. Příspěvek jde na provoz: AI shrnutí oznámení a server v ČR.',
      }, h('span', { class: 'sitefoot__heart', 'aria-hidden': 'true' }, '♥'), ' Podpořit provoz')),
    h('div', { class: 'sitefoot__in sitefoot__in--small' },
      h('span', {}, 'Neoficiální nekomerční web – nejde o stránky obcí, závazné je znění na jejich úředních deskách.'),
      how));
  foot.hidden = false;

  const monthStart = `${localTodayIso().slice(0, 7)}-01`;
  const [month, total] = await Promise.all([gcTotal(monthStart), gcTotal('')]);
  const parts = [
    month != null ? `${visitors(month)} tento měsíc` : null,
    total != null ? `${total.toLocaleString('cs-CZ')} celkem` : null,
  ].filter(Boolean);
  if (!parts.length) return;
  stats.textContent = parts.join(' · ');
  stats.hidden = false;
  how.hidden = false;
}

let onList = false;   // je teď zobrazený seznam?
let fromList = false; // přišel uživatel na detail ze seznamu (pak „Zpět“ = history.back())

function route() {
  TODAY = localTodayIso();
  const { parts, params } = parseHash();
  if (parts[0] === 'o' && parts[1]) {
    fromList = onList;
    if (onList) state.listScroll = window.scrollY;
    onList = false;
    renderDetail(parts[1]);
    window.scrollTo(0, 0);
    return;
  }
  if (parts[0] === 'akce') {
    onList = false;
    renderEvents(params);
    return;
  }
  if (parts[0] === 'zpravodaje') {
    onList = false;
    renderNewsletters(params.get('obec') || '');
    window.scrollTo(0, 0);
    return;
  }
  if (parts[0] === 'samosprava') {
    onList = false;
    renderCouncil(params);
    return;
  }
  if (parts[0] === 'jak-to-funguje') {
    onList = false;
    renderHelp();
    window.scrollTo(0, 0);
    return;
  }
  if (parts[0] === 'obce') {
    onList = false;
    renderVillages();
    window.scrollTo(0, 0);
    return;
  }
  if (parts[0] === 'obec' && parts[1]) {
    fromList = false;
    onList = false;
    renderVillage(parts[1]);
    window.scrollTo(0, 0);
    return;
  }
  state.filter.obec = params.get('obec') || '';
  state.filter.tema = params.get('tema') || '';
  state.filter.q = params.get('q') || '';
  state.filter.den = isIso(params.get('den')) ? params.get('den') : '';
  state.filter.lhuta = params.get('lhuta') === '1';
  state.filter.nove = params.get('nove') === '1';
  state.filter.moje = params.get('moje') === '1';
  onList = true;
  renderHome();
  const y = state.listScroll;
  state.listScroll = null;
  if (y != null) requestAnimationFrame(() => window.scrollTo(0, y));
}

function writeFilter() {
  const p = new URLSearchParams();
  if (state.filter.obec) p.set('obec', state.filter.obec);
  if (state.filter.tema) p.set('tema', state.filter.tema);
  if (state.filter.q) p.set('q', state.filter.q);
  if (state.filter.den) p.set('den', state.filter.den);
  if (state.filter.lhuta) p.set('lhuta', '1');
  if (state.filter.nove) p.set('nove', '1');
  if (state.filter.moje) p.set('moje', '1');
  const qs = p.toString();
  history.replaceState(null, '', qs ? `#/?${qs}` : '#/');
}

// ---------- přehled ----------

/** Text, ve kterém hledá vyhledávání: oznámení, shrnutí i popis příloh (bez diakritiky, malými písmeny). */
function searchText(it) {
  const s = it.s || {};
  return fold([
    it.t, it.o, it.cj, s.tldr, s.action, s.where, s.label, s.impact, s.affects, s.contact,
    ...(s.bullets || []), ...(s.facts || []).map((f) => `${f.label} ${f.value}`), ...(s.dates || []).map((x) => x.what),
    ...(it.docs || []).map((x) => `${x.n || ''} ${x.w || ''} ${x.sum || ''}`),
  ].filter(Boolean).join(' '));
}

/** Kmen slova pro hledání: „pražská“ najde i „pražské“, „uzavírka“ i „uzavírky“ (čísla zůstávají přesná). */
function stem(term) {
  if (term.length < 5 || /\d/.test(term)) return term;
  const cut = term.replace(/(ami|ach|ech|emu|eho|ych|ymi|ich|ove|ova|ou|em|ym|a|e|i|o|u|y)$/, '');
  return cut.length >= 4 ? cut : term;
}

function filtered() {
  const { obec, tema, q, lhuta, nove, moje } = state.filter;
  const terms = fold(q).split(/\s+/).filter(Boolean).map(stem);
  const mine = moje ? getMine() : null;
  return state.data.items.filter((it) => {
    if (obec && it.o !== obec) return false;
    if (nove && !itemFresh(it)) return false;
    if (mine?.size && !mine.has(it.o)) return false;
    if (tema && it.s?.cat !== tema) return false;
    if (lhuta && !(isIso(it.s?.deadline) && daysTo(it.s.deadline) >= 0 && !it.r)) return false;
    if (!terms.length) return true;
    if (!it._q) it._q = searchText(it);
    return terms.every((t) => it._q.includes(t));
  });
}

function entry(it, w, also = []) {
  const s = it.s || {};
  const meta = entryMeta(it, w);
  return h('a', { class: `entry entry--${w.kind}${itemFresh(it) ? ' is-fresh' : ''}`, href: detailHref(it) },
    h('span', { class: 'entry__meta' }, obecTags([it.o, ...also.map((x) => x.o)]),
      h('span', { class: w.type === 'deadline' ? 'entry__when--warn' : null }, meta ? ` · ${meta}` : ''), ` · ${label(it)}`, badges(it)),
    h('h3', { class: 'entry__title' }, s.tldr || it.t),
    s.action ? h('span', { class: 'entry__sub' }, s.action) : null);
}

function row(it, dateIso, dateNote, datePrefix, also = []) {
  const s = it.s || {};
  return h('li', {}, h('a', { class: `row${itemFresh(it) ? ' is-fresh' : ''}`, href: detailHref(it) },
    h('span', { class: 'row__date' }, datePrefix ? h('span', { class: 'row__pre' }, datePrefix) : null, isIso(dateIso) ? dm(dateIso) : '–'),
    h('span', { class: 'row__body' },
      h('span', { class: 'row__title' }, s.tldr || it.t),
      h('span', { class: 'row__meta' }, obecTags([it.o, ...also.map((x) => x.o)]),
        [dateNote, label(it)].filter(Boolean).map((x) => ` · ${x}`).join(''), badges(it)))));
}

/** Řada 7 malých štítků dní; klik vybere den (znovu klik zruší). */
function dayChips(groups, onPick) {
  return h('div', { class: 'days', role: 'group', 'aria-label': 'Nejbližších 7 dní' },
    Array.from({ length: 7 }, (_, i) => {
      const iso = addDays(TODAY, i);
      const items = groups.get(iso) || [];
      const kinds = ['warn', 'event', 'start'].filter((k) => items.some((x) => x.w.kind === k));
      const name = `${i === 0 ? 'dnes, ' : ''}${weekdayLong(iso)} ${dateCz(iso)}`;
      const on = state.filter.den === iso;
      return h('button', {
        type: 'button', class: `day${i === 0 ? ' day--today' : ''}`, 'aria-pressed': String(on), disabled: !items.length && !on,
        'aria-label': `${name}: ${items.length ? `${items.length} ${plural(items.length, 'termín', 'termíny', 'termínů')}` : 'nic'}`,
        onclick: () => onPick(on ? '' : iso),
      },
      h('span', { class: 'day__wd' }, i === 0 ? 'dnes' : weekday(iso)),
      h('span', { class: 'day__d' }, `${+iso.slice(8, 10)}.`),
      h('span', { class: 'dots', 'aria-hidden': 'true' }, kinds.map((k) => h('span', { class: `dot dot--${k}` }))));
    }));
}

function renderHome() {
  const d = state.data;
  const updated = new Date(d.generated_at);
  const connected = d.members.filter((m) => m.connected);
  const labels = d.categories || {};
  const counts = new Map();
  const catCounts = new Map();
  for (const it of d.items) {
    if (it.r) continue;
    counts.set(it.o, (counts.get(it.o) || 0) + 1);
    if (it.s?.cat) catCounts.set(it.s.cat, (catCounts.get(it.s.cat) || 0) + 1);
  }
  const total = d.items.filter((it) => !it.r).length;
  const villageNames = connected.map((m) => m.name)
    .sort((a, b) => (counts.get(b) || 0) - (counts.get(a) || 0) || a.localeCompare(b, 'cs'));
  const topicIds = [...catCounts.keys()]
    .sort((a, b) => catCounts.get(b) - catCounts.get(a) || (labels[a] || a).localeCompare(labels[b] || b, 'cs'));
  const topicName = (id) => labels[id] || id;

  const daysBox = h('div', { class: 'filters__days' });
  const body = h('div', { class: 'timeline' });
  const status = h('span', { 'aria-live': 'polite' });
  const reset = h('button', { type: 'button', class: 'linkish', hidden: true }, 'Zrušit výběr');
  const tokens = h('span', { class: 'tokens' });
  const sugBox = h('div', { class: 'sug', role: 'listbox', hidden: true, 'aria-label': 'Návrhy' });
  const vilPanel = h('div', { class: 'vilpanel', id: 'vilpanel', hidden: true });
  const topicRow = h('nav', { class: 'topics', 'aria-label': 'Témata' });
  let vilBtn;
  let lhutaBtn;

  const changed = () => { state.limit = STEP; writeFilter(); paintControls(); paint(); };
  const setFilter = (patch) => { Object.assign(state.filter, patch); changed(); };

  const paint = () => {
    const items = filtered();
    const horizonEnd = addDays(TODAY, HORIZON);
    const groups = new Map();
    const later = [];
    const undated = [];
    const running = [];
    const foreign = []; // vyvěšeno za jiný úřad (Kladno, Průhonice…) – sbalené až na konci
    for (const it of items) {
      if (it.r) continue;
      if (it.s?.foreign) { foreign.push(it); continue; }
      const w = when(it);
      if (!w) undated.push(it);
      else if (w.type === 'running') running.push({ it, w }); // už platí (uzavírka): zvlášť, ne každý den „dnes“
      else if (w.date <= horizonEnd) {
        if (!groups.has(w.date)) groups.set(w.date, []);
        groups.get(w.date).push({ it, w });
      } else later.push({ it, w });
    }
    const order = { warn: 0, event: 1, start: 2 };
    for (const [iso, list] of groups) {
      list.sort((a, b) => order[a.w.kind] - order[b.w.kind] || (a.it.s?.time || '').localeCompare(b.it.s?.time || ''));
      groups.set(iso, mergeTwins(list));
    }
    later.sort((a, b) => a.w.date.localeCompare(b.w.date));
    running.sort((a, b) => a.w.until.localeCompare(b.w.until));
    const laterM = mergeTwins(later);
    const runningM = mergeTwins(running);
    undated.sort((a, b) => (b.p || '').localeCompare(a.p || '') || (b.fs || '').localeCompare(a.fs || ''));
    const removed = items.filter((it) => it.r).sort((a, b) => (b.r || '').localeCompare(a.r || ''));

    daysBox.replaceChildren(dayChips(groups, (iso) => setFilter({ den: iso })));
    const den = state.filter.den;
    const dayKeys = [...groups.keys()].sort().filter((iso) => !den || iso === den);
    const nDated = dayKeys.reduce((n, iso) => n + groups.get(iso).length, 0);
    const shown = den ? nDated : items.filter((it) => !it.r).length;
    status.replaceChildren(h('strong', {}, String(shown)), ` ${plural(shown, 'oznámení', 'oznámení', 'oznámení')}`,
      den ? ` · ${weekdayLong(den)} ${dateCz(den)}` : '');

    const dayNodes = dayKeys.map((iso) => {
      const n = daysTo(iso);
      return h('section', { class: 'daygroup', id: `den-${iso}`, 'aria-label': `${weekdayLong(iso)} ${dateCz(iso)}` },
        h('div', { class: 'daygroup__date' },
          h('span', { class: 'daygroup__wd' }, weekday(iso)),
          h('span', { class: 'daygroup__d' }, dm(iso)),
          h('span', { class: `daygroup__rel${n <= 2 ? ' daygroup__rel--soon' : ''}` }, relDay(iso))),
        h('div', { class: 'daygroup__items' }, groups.get(iso).map(({ it, w, also }) => entry(it, w, also))));
    });

    const undatedList = h('ul', { class: 'rows' });
    const more = h('button', { type: 'button', class: 'more' });
    const paintUndated = () => {
      undatedList.replaceChildren(...undated.slice(0, state.limit).map((it) => row(it, it.p, 'vyvěšeno')));
      more.hidden = undated.length <= state.limit;
      more.textContent = `Ukázat další (${undated.length - state.limit})`;
    };
    more.addEventListener('click', () => { state.limit += STEP; paintUndated(); });
    paintUndated();

    const { q, obec, tema, lhuta, nove, moje } = state.filter;
    const nothing = !items.length
      ? h('p', { class: 'empty' }, nove ? 'Od vaší poslední návštěvy nic nového.' : q || obec || tema || lhuta || moje ? 'Nic nenalezeno. Zkus jiné slovo, obec nebo téma.' : 'Na deskách teď nic nevisí.')
      : !dayNodes.length ? h('p', { class: 'empty' }, den ? 'Na tento den nic.' : `V příštích ${HORIZON} dnech žádné lhůty ani termíny.`) : null;

    const obecBand = obec && OBCE.has(obec) ? h('a', { class: 'band band--link', href: obecHref(obec), style: obecStyle(obec) },
      crest(obec, 32), h('span', { class: 'band__text' }, h('strong', {}, obec), h('span', { class: 'band__sub' }, 'Akce, zpravodaj a odkazy na web obce')),
      h('span', { class: 'band__go' }, 'Stránka obce', icon('M9 5l7 7-7 7', 16))) : null;
    body.replaceChildren(...(den ? [obecBand, ...dayNodes, nothing] : [
      obecBand,
      running.length ? h('h2', { class: 'section-title section-title--first' }, 'PRÁVĚ PLATÍ') : null,
      running.length ? h('ul', { class: 'rows' }, runningM.map(({ it, w, also }) => row(it, w.until, `platí do ${dateCz(w.until)}`, 'do', also))) : null,
      running.length && dayNodes.length ? h('h2', { class: 'section-title' }, `PŘÍŠTÍCH ${HORIZON} DNÍ`) : null,
      ...dayNodes,
      nothing,
      later.length ? h('h2', { class: 'section-title' }, 'POZDĚJI') : null,
      later.length ? h('ul', { class: 'rows' }, laterM.map(({ it, w, also }) => row(it, w.date, w.type === 'deadline' ? `${deadlineWord(it.s || {})} do ${dateCz(w.date)}` : null, null, also))) : null,
      undated.length ? h('h2', { class: 'section-title' }, 'BEZ TERMÍNU · NEJNOVĚJŠÍ NAHOŘE') : null,
      undated.length ? undatedList : null,
      undated.length ? more : null,
      foreign.length ? h('details', { class: 'removed' },
        h('summary', {}, `VYVĚŠENO ZA JINÉ ÚŘADY (${foreign.length})`),
        h('p', { class: 'muted removed__note' }, 'Obce na desky vyvěšují i písemnosti jiných úřadů, které se jich netýkají – např. veřejné vyhlášky jiných měst pro jejich dlužníky nebo výběrová řízení sousedních obcí.'),
        h('ul', { class: 'rows' }, foreign.sort((a, b) => (b.p || '').localeCompare(a.p || '')).map((it) => row(it, it.p, 'vyvěšeno')))) : null,
      removed.length ? h('details', { class: 'removed' },
        h('summary', {}, `SEJMUTO ZA POSLEDNÍCH 30 DNÍ (${removed.length})`),
        h('ul', { class: 'rows' }, removed.map((it) => row(it, (it.r || '').slice(0, 10), 'sejmuto')))) : null,
      villages(d),
    ]).filter(Boolean));
  };

  // Výběr: štítky v poli hledání, obce, témata, lhůta
  const paintControls = () => {
    const f = state.filter;
    const tok = (kind, text, patch) => h('span', { class: 'token' },
      h('span', { class: 'token__kind' }, kind), text,
      h('button', { type: 'button', class: 'token__x', 'aria-label': `Zrušit ${kind} ${text}`, onclick: () => { setFilter(patch); search.focus(); } },
        icon('M6 6l12 12M18 6 6 18', 12)));
    tokens.replaceChildren(...[
      f.obec ? tok('obec', f.obec, { obec: '' }) : null,
      f.tema ? tok('téma', topicName(f.tema), { tema: '' }) : null,
      f.den ? tok('den', dateCz(f.den, { wd: true }), { den: '' }) : null,
      f.nove ? tok('jen', 'nové pro vás', { nove: false }) : null].filter(Boolean));
    search.placeholder = f.obec || f.tema || f.den ? 'Upřesnit…' : 'Obec, téma nebo text…';
    reset.hidden = !(f.obec || f.tema || f.den || f.q || f.lhuta || f.nove || f.moje);
    mineBtn?.setAttribute('aria-pressed', String(!!f.moje));
    vilBtn.setAttribute('aria-expanded', String(!vilPanel.hidden));
    lhutaBtn.setAttribute('aria-pressed', String(!!f.lhuta));
    vilPanel.replaceChildren(...['', ...villageNames].map((name) => h('button', {
      type: 'button', class: 'vil', 'aria-pressed': String(f.obec === name),
      onclick: () => { vilPanel.hidden = true; setFilter({ obec: name }); },
    }, h('span', { class: 'vil__name' }, name ? crest(name, 18) : null, name || 'Všechny obce'), h('span', { class: 'vil__n' }, String(name ? counts.get(name) || 0 : total)))));
    topicRow.replaceChildren(...['', ...topicIds].map((id) => h('button', {
      type: 'button', class: 'topic', 'aria-pressed': String(f.tema === id),
      onclick: () => setFilter({ tema: f.tema === id ? '' : id }),
    }, id ? topicName(id) : 'Vše')));
  };

  // Našeptávač: obec nebo téma podle psaného textu; jinak se hledá v textu oznámení
  const suggest = (text) => {
    const t = fold(text.trim());
    const hits = !t ? [] : [
      ...villageNames.filter((n) => fold(n).includes(t)).map((n) => ({ kind: 'obec', label: n, n: counts.get(n) || 0, patch: { obec: n } })),
      ...topicIds.filter((id) => fold(topicName(id)).includes(t)).map((id) => ({ kind: 'téma', label: topicName(id), n: catCounts.get(id), patch: { tema: id } })),
    ].slice(0, 6);
    sugBox.replaceChildren(...hits.map((s) => h('button', {
      type: 'button', class: 'sug__item', role: 'option',
      onclick: () => { search.value = ''; sugBox.hidden = true; setFilter({ ...s.patch, q: '' }); search.focus(); },
    }, h('span', { class: 'sug__kind' }, s.kind), h('span', { class: 'sug__label' }, s.label), h('span', { class: 'sug__n' }, String(s.n)))));
    sugBox.hidden = !hits.length;
    search.setAttribute('aria-expanded', String(!!hits.length));
  };

  let timer;
  const search = h('input', {
    type: 'search', id: 'q', class: 'tokenbox__input', value: state.filter.q, autocomplete: 'off',
    role: 'combobox', 'aria-expanded': 'false', 'aria-controls': 'sug', 'aria-autocomplete': 'list',
    oninput: (e) => {
      suggest(e.target.value);
      clearTimeout(timer);
      timer = setTimeout(() => { state.filter.q = e.target.value; state.limit = STEP; writeFilter(); reset.hidden = false; paint(); }, 160);
    },
    onkeydown: (e) => {
      if (e.key === 'ArrowDown' && !sugBox.hidden) { e.preventDefault(); sugBox.querySelector('button')?.focus(); }
      else if (e.key === 'Escape') { sugBox.hidden = true; }
      else if (e.key === 'Backspace' && !e.target.value) {
        const f = state.filter; // smaže poslední štítek
        if (f.den) setFilter({ den: '' }); else if (f.tema) setFilter({ tema: '' }); else if (f.obec) setFilter({ obec: '' });
      }
    },
  });
  sugBox.id = 'sug';
  sugBox.addEventListener('keydown', (e) => {
    const items = [...sugBox.querySelectorAll('button')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[Math.min(i + 1, items.length - 1)]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (i <= 0) search.focus(); else items[i - 1].focus(); }
    else if (e.key === 'Escape') { sugBox.hidden = true; search.focus(); }
  });
  reset.addEventListener('click', () => {
    search.value = '';
    sugBox.hidden = true;
    setFilter({ obec: '', tema: '', q: '', den: '', lhuta: false, nove: false, moje: false });
  });
  vilBtn = h('button', {
    type: 'button', class: 'fbtn', 'aria-controls': 'vilpanel', 'aria-expanded': 'false',
    onclick: () => { vilPanel.hidden = !vilPanel.hidden; paintControls(); },
  }, 'Obce', icon('m6 9 6 6 6-6', 14));
  lhutaBtn = h('button', {
    type: 'button', class: 'fbtn', 'aria-pressed': 'false',
    onclick: () => setFilter({ lhuta: !state.filter.lhuta }),
  }, 'Jen se lhůtou');
  const myObce = getMine();
  const mineBtn = myObce.size ? h('button', {
    type: 'button', class: 'fbtn fbtn--mine', 'aria-pressed': 'false', title: [...myObce].join(', '),
    onclick: () => setFilter({ moje: !state.filter.moje }),
  }, h('span', { class: 'star', 'aria-hidden': 'true' }, '★'), ` Moje obce (${myObce.size})`) : null;

  const box = h('div', { class: 'tokenbox' },
    icon('M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.5-3.5', 18),
    tokens,
    h('label', { class: 'vh', for: 'q' }, 'Hledat obec, téma nebo text v oznámeních – třeba Psáry, uzavírky, Májová'),
    search);

  const searchWrap = h('div', { class: 'filters__search' }, box, sugBox);
  searchWrap.addEventListener('focusout', (e) => { if (!searchWrap.contains(e.relatedTarget)) sugBox.hidden = true; });

  const stamp = Number.isNaN(updated.getTime()) ? ''
    : `aktualizováno ${updated.toLocaleDateString('cs-CZ')} ${updated.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' })}`;
  mount('',
    h('div', { class: 'page' },
      siteNav('desky'),
      h('header', { class: 'masthead' },
        h('h1', {}, 'Co se kdy děje'),
        h('span', { class: 'masthead__meta' }, [d.region?.name || 'Dolnobřežansko', stamp].filter(Boolean).join(' · ')),
        d.sample ? h('div', { class: 'sample' }, 'Ukázková data – nejde o skutečná oznámení.') : null),
      helpStrip(),
      newsBar(setFilter),
      h('section', { class: 'filters', 'aria-label': 'Výběr' },
        h('div', { class: 'filters__row' },
          searchWrap,
          vilBtn, lhutaBtn, mineBtn),
        vilPanel,
        topicRow,
        daysBox),
      h('div', { class: 'summary' },
        status, reset,
        h('span', { class: 'legend', 'aria-hidden': 'true' },
          h('span', {}, h('span', { class: 'dot dot--warn' }), 'lhůta / omezení'),
          h('span', {}, h('span', { class: 'dot dot--event' }), 'jednání / akce'))),
      h('div', { class: 'main' }, body)));
  paintControls();
  paint();
}

function villages(d) {
  return h('section', { class: 'villages', 'aria-labelledby': 'h-vil' },
    h('h2', { id: 'h-vil' }, 'KTERÉ DESKY ČTEME'),
    h('ul', {}, d.members.map((m) => {
      const mark = m.connected
        ? (m.ok ? h('span', { class: 'ok' }, '✓') : h('span', { class: 'miss', title: 'Poslední stažení selhalo' }, '!'))
        : h('span', { class: 'miss' }, '–');
      return h('li', { title: m.connected ? null : (m.status || 'deska zatím nenalezena') }, mark,
        h('a', { class: 'villages__obec', href: obecHref(m.name) }, crest(m.name, 16), m.name));
    })),
    h('p', {}, '✓ deska připojena · – zatím nečteme. ',
      d.ai_model ? 'Shrnutí píše AI (Claude) z textu a příloh a může se splést. ' : 'Shrnutí vznikají automaticky z textu oznámení a příloh. ',
      'Právně platí znění na úřední desce obce.'));
}

// ---------- navigace a zpravodaje ----------

/** Hlavní přepínač nahoře: úřední desky (výchozí), kalendář akcí a zpravodaje obcí. */
// ---------- vzhled: systém / světlý / tmavý ----------

const THEMES = [
  ['system', 'Podle systému', 'M3 4h18v12H3zM8 20h8M12 16v4'],
  ['light', 'Světlý', 'M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z'],
  ['dark', 'Tmavý', 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z'],
];

function currentTheme() {
  try { return localStorage.getItem('theme') || 'system'; } catch { return 'system'; }
}

function applyTheme(theme) {
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  try { if (theme === 'system') localStorage.removeItem('theme'); else localStorage.setItem('theme', theme); } catch { /* bez úložiště jen pro tuto návštěvu */ }
}

/** Přepínač vzhledu: na počítači tři tlačítka, na telefonu jedno, které je střídá. */
function themeSwitch() {
  const now = currentTheme();
  let wrap;
  const pick = (key) => { applyTheme(key); wrap.replaceWith(themeSwitch()); };
  const group = h('div', { class: 'theme', role: 'group', 'aria-label': 'Vzhled' },
    THEMES.map(([key, label, path]) => h('button', {
      type: 'button', class: 'theme__btn', 'aria-pressed': String(now === key), title: label, 'aria-label': `Vzhled: ${label}`,
      onclick: () => pick(key),
    }, icon(path, 16))));
  const i = Math.max(0, THEMES.findIndex(([key]) => key === now));
  const [, label, path] = THEMES[i];
  const [nextKey, nextLabel] = THEMES[(i + 1) % THEMES.length];
  const cycle = h('button', {
    type: 'button', class: 'theme__cycle', title: `Vzhled: ${label} (klikni: ${nextLabel.toLowerCase()})`,
    'aria-label': `Vzhled: ${label}. Přepnout na: ${nextLabel}`, onclick: () => pick(nextKey),
  }, icon(path, 18));
  wrap = h('div', { class: 'theme-wrap' }, group, cycle);
  return wrap;
}

const NAV = [
  ['desky', '#/', 'Úřední desky', 'Desky', 'M5 3h14v18H5zM9 8h6M9 12h6M9 16h4'],
  ['akce', '#/akce', 'Kalendář akcí', 'Akce', 'M4 5h16v15H4zM4 10h16M9 3v4M15 3v4'],
  ['zpravodaje', '#/zpravodaje', 'Zpravodaje', 'Zpravodaje', 'M6 3h9l4 4v14H6zM9 11h7M9 15h7'],
  ['samosprava', '#/samosprava', 'Samospráva', 'Usnesení', 'M3 21h18M5 21V10M10 21V10M14 21V10M19 21V10M2 10l10-6 10 6'],
  ['obce', '#/obce', 'Obce', 'Obce', 'M5 4h14v7c0 5-3.5 8-7 10-3.5-2-7-5-7-10z'],
];

function siteNav(active) {
  const nw = news();
  const fresh = { desky: nw?.items, akce: nw?.events, zpravodaje: nw?.issues, samosprava: nw?.meetings };
  const nObce = state.data?.members?.length || 11;
  return h('nav', { class: 'sitenav', 'aria-label': 'Sekce' },
    h('a', { class: 'sitenav__logo', href: '#/', title: 'Mikroregion Dolnobřežansko – úvod' },
      h('img', { src: 'logo.png', width: 96, height: 96, alt: 'Dolnobřežansko' })),
    h('a', { class: 'sitenav__brand', href: '#/', 'aria-hidden': 'true', tabindex: '-1' },
      h('strong', {}, 'Dolnobřežansko'), h('small', {}, `úřední desky · ${nObce} obcí`)),
    h('div', { class: 'sitenav__tabs' },
      NAV.map(([key, href, long, short, path]) => h('a', { class: 'sitenav__tab', href, 'aria-current': active === key ? 'page' : null },
        icon(path, 18), h('span', { class: 'sitenav__long' }, long), h('span', { class: 'sitenav__short', 'aria-hidden': 'true' }, short),
        fresh[key] ? h('span', { class: 'sitenav__cnt', title: 'Nové od vaší poslední návštěvy', 'aria-label': `${fresh[key]} nových` },
          String(fresh[key])) : null))),
    h('div', { class: 'sitenav__end' },
      h('a', { class: 'sitenav__help', href: HELP_HREF, title: 'Jak to funguje', 'aria-label': 'Jak to funguje',
        'aria-current': active === 'help' ? 'page' : null }, '?'),
      themeSwitch()));
}

const MONTH_RE = /(leden|únor|březen|duben|květen|červen(?:ec)?|srpen|září|říjen|listopad|prosinec)/i;

/** Krátký jednotný popis čísla: „č. 5/2026“, „č. 96 · září 2026“, „Říjen 2026“; celý název je v title odkazu. */
function issueLabel(i, name = '') {
  let t = String(i.t || '').trim();
  if (name && fold(t).startsWith(fold(name))) t = t.slice(name.length).trim();
  const series = /rozkvět/i.test(t) ? 'Rozkvět ' : '';
  let m = /^(?:č\.|číslo)?\s*(\d{1,3})\s*[/ ]\s*((?:19|20)\d\d)$/i.exec(t); // „5/2026“, „číslo 2 2026“
  if (m) return `č. ${m[1]}/${m[2]}`;
  m = /(?:č\.|číslo\.?)\s*(\d{1,4})\s*(?:\(([^)]+)\))?/i.exec(t);
  if (m) {
    const month = m[2] && MONTH_RE.test(m[2]) ? m[2] : null;
    return `${series}č. ${m[1]}${m[2] && !month ? ` (${m[2]})` : ''}${month ? ` · ${month}` : ''}`;
  }
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : i.t;
}

const NEW_ISSUE_DAYS = 31;
// vracející se návštěvník: vyšlo od poslední návštěvy; první návštěva: za posledních NEW_ISSUE_DAYS dní
const issueIsNew = (i) => (visit.last ? isFresh(i?.fs)
  : typeof i?.fs === 'string' && i.fs.length >= 10 && daysTo(i.fs.slice(0, 10)) >= -NEW_ISSUE_DAYS);

/** Čísla zpravodaje po ročnících (nejnovější první); číslo bez roku patří k sousednímu ročníku. */
function issueYears(issues) {
  const years = [];
  for (const i of issues) {
    const y = i.y || (years.length ? years[years.length - 1].y : 'bez roku');
    if (!years.length || years[years.length - 1].y !== y) years.push({ y, items: [] });
    years[years.length - 1].items.push(i);
  }
  return years;
}

/** Archiv čísel jednoho zpravodaje: přepínač ročníků a čísla ročníku jako dlaždice. */
function issueArchive(n, issues) {
  const years = issueYears(issues);
  const grid = h('div', { class: 'issues' });
  const yearRow = h('div', { class: 'topics years', role: 'group', 'aria-label': 'Ročník' });
  const show = (k) => {
    [...yearRow.children].forEach((b, i) => b.setAttribute('aria-pressed', String(i === k)));
    grid.replaceChildren(...years[k].items.map((i) => h('a', { class: 'issue', href: safeUrl(i.url), rel: 'noopener noreferrer', target: '_blank', title: i.t },
      h('span', { class: 'issue__t' }, issueLabel(i, n.name)), issueIsNew(i) ? newBadge() : null)));
  };
  years.forEach((g, k) => yearRow.append(h('button', { type: 'button', class: 'topic', onclick: () => show(k) },
    String(g.y), h('span', { class: 'topic__n' }, String(g.items.length)))));
  if (years.length) show(0);
  return h('div', { class: 'nl__archive' }, years.length > 1 ? yearRow : null, grid,
    h('a', { class: 'nl__ext', href: safeUrl(n.archive), rel: 'noopener noreferrer', target: '_blank' }, 'Archiv na webu obce ↗'));
}

function renderNewsletters(openSlug = '') {
  const all = state.data.newsletters || [];
  const withNews = all.filter((n) => n.name && safeUrl(n.archive));
  const none = all.filter((n) => !n.name && n.none).map((n) => n.obec);
  const missing = all.filter((n) => !n.name && !n.none).map((n) => n.obec);
  const latestOf = (n) => (n.issues || []).find((i) => safeUrl(i.url));
  // nahoře zpravodaje s novým číslem, pak podle ročníku nejnovějšího čísla
  const rank = (n) => [issueIsNew(latestOf(n)) ? 1 : 0, latestOf(n)?.y || 0, latestOf(n)?.fs || ''];
  const sorted = [...withNews].sort((a, b) => {
    const [ra, rb] = [rank(a), rank(b)];
    return rb[0] - ra[0] || rb[1] - ra[1] || rb[2].localeCompare(ra[2]) || a.obec.localeCompare(b.obec, 'cs');
  });
  const rowOf = (n) => {
    const issues = (n.issues || []).filter((i) => safeUrl(i.url));
    const latest = issues[0];
    const open = !!openSlug && obecOf(n.obec).slug === openSlug;
    const id = `nl-${obecOf(n.obec).slug || n.obec}`;
    let archive = open ? issueArchive(n, issues.slice(1)) : null;
    const slot = h('div', { class: 'nl__slot', id: `${id}-a` }, archive);
    const btn = issues.length > 1 ? h('button', {
      type: 'button', class: 'fbtn nl__more', 'aria-expanded': String(open), 'aria-controls': `${id}-a`,
      onclick: () => {
        const isOpen = btn.getAttribute('aria-expanded') === 'true';
        if (!archive) { archive = issueArchive(n, issues.slice(1)); slot.append(archive); }
        archive.hidden = isOpen;
        btn.setAttribute('aria-expanded', String(!isOpen));
      },
    }, `Starší čísla (${issues.length - 1})`, icon('m6 9 6 6 6-6', 14)) : null;
    return h('li', { class: 'nl', id, style: obecStyle(n.obec) },
      h('div', { class: 'nl__row' },
        h('a', { class: 'nl__obec', href: obecHref(n.obec) }, crest(n.obec, 28),
          h('span', {}, h('span', { class: 'nl__name' }, n.name), h('span', { class: 'nl__place' }, n.obec))),
        latest
          ? h('a', { class: 'nl__latest', href: safeUrl(latest.url), rel: 'noopener noreferrer', target: '_blank', title: latest.t },
            icon(ICON_DOC, 18), h('span', {}, h('span', { class: 'nl__label' }, 'Nejnovější číslo'),
              h('strong', {}, issueLabel(latest, n.name)), issueIsNew(latest) ? newBadge() : null))
          : h('span', { class: 'nl__empty' }, h('a', { href: safeUrl(n.archive), rel: 'noopener noreferrer', target: '_blank' }, 'Čísla jsou v archivu na webu obce ↗')),
        btn),
      slot);
  };
  mount('Zpravodaje',
    h('div', { class: 'page' },
      siteNav('zpravodaje'),
      h('header', { class: 'masthead' },
        h('h1', {}, 'Zpravodaje obcí'),
        h('span', { class: 'masthead__meta' }, 'Odkazy na čísla přímo na webech obcí')),
      h('div', { class: 'main' },
        h('p', { class: 'news__intro' }, 'Zpravodaje nezkracujeme – mají desítky stran a každého zajímá něco jiného. Otevři si číslo a listuj.'),
        sorted.length
          ? h('ul', { class: 'nls' }, sorted.map(rowOf))
          : h('p', { class: 'empty' }, 'Zatím tu nejsou žádné zpravodaje.'),
        none.length || missing.length ? h('section', { class: 'villages' },
          none.length ? h('p', {}, 'Zpravodaj nevydávají: ', obecTags(none)) : null,
          missing.length ? h('p', {}, 'Zpravodaj zatím nemáme: ', obecTags(missing)) : null) : null)));
  if (openSlug) requestAnimationFrame(() => document.getElementById(`nl-${openSlug}`)?.scrollIntoView({ block: 'start' }));
}

// ---------- samospráva: usnesení rad a zastupitelstev ----------

const BODY = { rada: 'Rada', zastupitelstvo: 'Zastupitelstvo' };
const POINTS_SHOWN = 4;

/** Jedno zasedání: datum vlevo, obec · orgán · číslo, hlavní věta a body usnesení (prvních pár, zbytek rozbalit). */
function meetingNode(mt, { compact = false } = {}) {
  const points = Array.isArray(mt.points) ? mt.points : [];
  const point = (p) => h('li', { class: `point${p.imp === 'vysoka' ? ' point--hot' : ''}` },
    h('span', { class: 'point__verb' }, p.v || ''),
    h('span', { class: 'point__text' }, withLinks(czDates(p.t), mt), p.amt ? h('span', { class: 'point__amt' }, ` · ${p.amt}`) : null));
  const shown = compact ? points.filter((p) => p.imp === 'vysoka').slice(0, 2) : points.slice(0, POINTS_SHOWN);
  const rest = compact ? [] : points.slice(POINTS_SHOWN);
  const doc = safeUrl(mt.url);
  return h('article', { class: 'meeting' },
    h('span', { class: 'entry__meta' }, obecTag(mt.o), ` · ${BODY[mt.body] || mt.body}`, mt.no ? ` · č. ${mt.no}` : '',
      mt.kind === 'zapis' ? ' · zápis' : '', meetingFresh(mt) ? [' ', freshBadge()] : null),
    h('h3', { class: 'meeting__title' }, mt.tldr ? withLinks(czDates(mt.tldr), mt) : (mt.t || 'Usnesení')),
    shown.length ? h('ul', { class: 'points' }, shown.map(point)) : null,
    rest.length ? h('details', { class: 'more-details' }, h('summary', {}, `Další body (${rest.length})`),
      h('ul', { class: 'points' }, rest.map(point))) : null,
    !compact && !points.length && !mt.tldr ? h('p', { class: 'muted meeting__none' }, 'Body usnesení zatím nemáme rozepsané – otevři dokument.') : null,
    doc ? h('a', { class: 'meeting__doc', href: doc, rel: 'noopener noreferrer', target: '_blank' },
      `${mt.kind === 'zapis' ? 'Zápis' : 'Usnesení'}${mt.x ? ` (${String(mt.x).toUpperCase()})` : ''} ↗`) : null);
}

function renderCouncil(params) {
  const c = state.data.council || { sources: [], meetings: [] };
  const all = (c.meetings || []).filter((m) => m.o);
  const bySlug = (s) => (state.data.members || []).find((m) => m.slug === s)?.name || '';
  const f = { obec: bySlug(params.get('obec') || ''), organ: BODY[params.get('organ')] ? params.get('organ') : '',
    jen: params.get('jen') === 'dulezite', nove: params.get('nove') === '1' && !!visit.last };
  const go = (patch) => {
    const n = { ...f, ...patch };
    const p = new URLSearchParams();
    if (n.obec) p.set('obec', obecOf(n.obec).slug || n.obec);
    if (n.organ) p.set('organ', n.organ);
    if (n.jen) p.set('jen', 'dulezite');
    if (n.nove) p.set('nove', '1');
    history.replaceState(null, '', p.toString() ? `#/samosprava?${p}` : '#/samosprava');
    renderCouncil(p);
  };
  const counts = new Map();
  for (const m of all) counts.set(m.o, (counts.get(m.o) || 0) + 1);
  const list = all.filter((m) => (!f.obec || m.o === f.obec) && (!f.organ || m.body === f.organ)
    && (!f.jen || (m.points || []).some((p) => p.imp === 'vysoka'))
    && (!f.nove || (meetingFresh(m) && inMine(m.o))))
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  const months = [];
  for (const m of list) {
    const key = (m.date || '').slice(0, 7) || 'bez data';
    if (!months.length || months[months.length - 1].key !== key) months.push({ key, items: [] });
    months[months.length - 1].items.push(m);
  }
  const monthName = (k) => (/^\d{4}-\d{2}$/.test(k) ? `${MONTHS[+k.slice(5, 7) - 1]} ${k.slice(0, 4)}` : 'Bez data');
  const names = [...counts.keys()].sort((a, b) => a.localeCompare(b, 'cs'));
  const sources = c.sources || [];
  const without = (state.data.members || []).map((m) => m.name).filter((n) => !sources.some((s) => s.obec === n));
  mount('Samospráva',
    h('div', { class: 'page' },
      siteNav('samosprava'),
      h('header', { class: 'masthead' },
        h('h1', {}, 'Co rozhodla rada a zastupitelstvo'),
        h('span', { class: 'masthead__meta' }, 'Usnesení a zápisy z webů obcí · letošní rok')),
      h('section', { class: 'filters', 'aria-label': 'Výběr' },
        h('div', { class: 'filters__row filters__row--view' },
          h('div', { class: 'seg', role: 'group', 'aria-label': 'Orgán' },
            [['', 'Vše'], ['rada', 'Rada'], ['zastupitelstvo', 'Zastupitelstvo']].map(([k, label]) => h('button', {
              type: 'button', class: 'seg__btn', 'aria-pressed': String(f.organ === k), onclick: () => go({ organ: k }),
            }, label))),
          h('button', { type: 'button', class: 'fbtn', 'aria-pressed': String(f.jen), onclick: () => go({ jen: !f.jen }) }, 'Jen důležité')),
        h('div', { class: 'topics topics--wrap', role: 'group', 'aria-label': 'Obec' },
          h('button', { type: 'button', class: 'topic', 'aria-pressed': String(!f.obec), onclick: () => go({ obec: '' }) }, 'Všechny obce'),
          names.map((n) => h('button', { type: 'button', class: 'topic topic--obec', 'aria-pressed': String(f.obec === n), onclick: () => go({ obec: f.obec === n ? '' : n }) },
            crest(n, 16), n, h('span', { class: 'topic__n' }, String(counts.get(n))))))),
      h('div', { class: 'summary' }, h('span', {}, h('strong', {}, String(list.length)), ` ${plural(list.length, 'zasedání', 'zasedání', 'zasedání')}`),
        f.nove ? freshFilterChip(() => go({ nove: false })) : null),
      h('div', { class: 'main' },
        h('div', { class: 'timeline' },
          months.length ? months.map((g) => [
            h('h2', { class: 'section-title' }, monthName(g.key).toUpperCase()),
            g.items.map((m) => h('section', { class: 'daygroup' },
              h('div', { class: 'daygroup__date' },
                h('span', { class: 'daygroup__wd' }, isIso(m.date) ? weekday(m.date) : ''),
                h('span', { class: 'daygroup__d' }, isIso(m.date) ? dm(m.date) : '')),
              h('div', { class: 'daygroup__items' }, meetingNode(m))))])
            : h('p', { class: 'empty' }, all.length ? 'V tomto výběru nic.' : 'Usnesení se zatím načítají – zkus to později.')),
        h('section', { class: 'villages' },
          h('h2', {}, 'ODKUD USNESENÍ BEREME'),
          h('ul', {}, sources.map((s) => h('li', {}, s.ok ? h('span', { class: 'ok' }, '✓') : h('span', { class: 'miss', title: 'Poslední načtení selhalo' }, '!'),
            h('a', { class: 'villages__obec', href: safeUrl(s.url), rel: 'noopener noreferrer', target: '_blank' }, crest(s.obec, 16), `${s.obec} – ${(BODY[s.body] || s.body).toLowerCase()}`)))),
          without.length ? h('p', {}, 'Usnesení na webu nezveřejňují: ', obecTags(without)) : null,
          h('p', {}, all.some((m) => m.src === 'claude')
            ? 'Body usnesení rozepisuje AI (Claude) z dokumentů obcí a může se splést. Platí znění v dokumentu obce.'
            : 'Platí znění v dokumentu obce.')))));
}

// ---------- obce ----------

/** Co je teď u obce: oznámení s termínem (sloučená jako na přehledu), nově vyvěšená, akce, zpravodaj. */
function villageData(name) {
  const items = state.data.items.filter((it) => it.o === name);
  const live = items.filter((it) => !it.r);
  const dated = live.map((it) => ({ it, w: when(it) })).filter((x) => x.w)
    .sort((a, b) => (a.w.type === 'running' ? a.w.until : a.w.date).localeCompare(b.w.type === 'running' ? b.w.until : b.w.date));
  const undated = live.filter((it) => !when(it) && !it.s?.foreign).sort((a, b) => (b.p || '').localeCompare(a.p || ''));
  const events = (state.data.events?.items || []).filter((e) => e.o === name && isIso(e.d) && (e.end || e.d) >= TODAY);
  const news = (state.data.newsletters || []).find((n) => n.obec === name && n.name);
  const evSource = (state.data.events?.sources || []).find((s) => s.obec === name);
  const council = (state.data.council?.meetings || []).filter((x) => x.o === name)
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  return { items, live, dated, undated, events, news, evSource, council };
}

function renderVillages() {
  const members = state.data.members || [];
  const horizon = addDays(TODAY, HORIZON);
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; } };
  const card = (m) => {
    const v = villageData(m.name);
    const deadlines = v.dated.filter((x) => x.w.type === 'deadline').length;
    // co je u obce nejblíž na řadě: lhůta nebo termín v příštích dnech (ne to, co už jen „platí“)
    const next = v.dated.filter((x) => x.w.type !== 'running' && x.w.date <= horizon)
      .sort((a, b) => a.w.date.localeCompare(b.w.date) || (a.w.kind === 'warn' ? -1 : 1))[0];
    const kind = /^město/i.test(m.publisher || '') ? 'město' : 'obec';
    const num = (n, label, warn) => h('div', { class: `ocard__num${warn && n ? ' ocard__num--warn' : ''}${n ? '' : ' ocard__num--zero'}` },
      h('dd', {}, String(n)), h('dt', {}, label));
    return h('li', {}, h('a', { class: 'ocard', href: obecHref(m.name), style: obecStyle(m.name) },
      h('div', { class: 'ocard__head' },
        crest(m.name, 46),
        h('div', { class: 'ocard__id' },
          h('span', { class: 'ocard__kind' }, [kind, host(m.web)].filter(Boolean).join(' · ')),
          h('h2', { class: 'ocard__name' }, m.name))),
      h('dl', { class: 'ocard__nums' },
        num(v.live.length, plural(v.live.length, 'oznámení', 'oznámení', 'oznámení')),
        num(deadlines, plural(deadlines, 'lhůta', 'lhůty', 'lhůt'), true),
        num(v.events.length, plural(v.events.length, 'akce', 'akce', 'akcí'))),
      next
        ? h('p', { class: `ocard__next ocard__next--${next.w.kind}` },
          h('span', { class: 'ocard__when' }, `${weekday(next.w.date)} ${dm(next.w.date)}`),
          h('span', { class: 'ocard__what' }, next.it.s?.tldr || next.it.t))
        : h('p', { class: 'ocard__next ocard__next--none' }, `V příštích ${HORIZON} dnech nic s termínem`)));
  };
  mount('Obce',
    h('div', { class: 'page' },
      siteNav('obce'),
      h('header', { class: 'masthead' },
        h('h1', {}, 'Obce mikroregionu'),
        h('span', { class: 'masthead__meta' }, `${members.length} ${plural(members.length, 'obec', 'obce', 'obcí')} · ${state.data.region?.name || 'Dolnobřežansko'}`)),
      h('ul', { class: 'ocards' }, members.map(card)),
      h('p', { class: 'credit' }, 'Znaky obcí: weby obcí a Wikimedia Commons.')));
}

/** Kontakt úřadu obce: adresa, telefon, e-mail, datová schránka, IČO, úřední hodiny (dnešní den zvýrazněný). */
function contactBox(m) {
  const c = m.contact;
  if (!c) return null;
  const tel = (p) => `tel:+420${String(p).replace(/\D/g, '').replace(/^420/, '')}`;
  const today = weekday(TODAY);
  const row = (label, ...value) => [h('dt', {}, label), h('dd', {}, ...value)];
  const list = (arr) => (Array.isArray(arr) ? arr : [arr]).filter(Boolean);
  return h('section', { class: 'contact', 'aria-labelledby': 'h-contact' },
    h('h2', { id: 'h-contact', class: 'section-title section-title--first' }, 'KONTAKT'),
    h('p', { class: 'contact__office' }, c.office),
    h('p', { class: 'contact__addr' }, c.address),
    h('dl', { class: 'contact__list' },
      list(c.phone).length ? row('Telefon', list(c.phone).map((p, i) => [i ? ', ' : '', h('a', { href: tel(p) }, p)])) : null,
      list(c.email).length ? row('E-mail', list(c.email).map((e, i) => [i ? ', ' : '', h('a', { href: `mailto:${e}` }, e)]))
        : safeUrl(c.form) ? row('E-mail', h('a', { href: safeUrl(c.form), rel: 'noopener noreferrer', target: '_blank' }, 'kontaktní formulář ↗')) : null,
      c.ds ? row('Datová schránka', h('span', { class: 'mono' }, c.ds)) : null,
      c.ico ? row('IČO', h('span', { class: 'mono' }, c.ico)) : null),
    Array.isArray(c.hours) && c.hours.length ? [
      h('h3', { class: 'contact__h' }, 'Úřední hodiny'),
      h('table', { class: 'contact__hours' }, h('tbody', {}, c.hours.map(([d, t]) => h('tr', { class: fold(d) === fold(today) ? 'is-today' : null },
        h('th', { scope: 'row' }, d), h('td', {}, t, fold(d) === fold(today) ? h('span', { class: 'contact__now' }, 'dnes') : null))))),
      c.hours_note ? h('p', { class: 'contact__note' }, c.hours_note) : null,
    ] : safeUrl(c.hours_url) ? h('p', {}, h('a', { href: safeUrl(c.hours_url), rel: 'noopener noreferrer', target: '_blank' }, 'Úřední hodiny na webu obce ↗')) : null,
    safeUrl(c.source) ? h('p', { class: 'contact__src' }, 'Podle ', h('a', { href: safeUrl(c.source), rel: 'noopener noreferrer', target: '_blank' }, 'webu obce'),
      isIso(c.checked) ? `, ověřeno ${dateCz(c.checked, { year: true })}` : '', '.') : null);
}

function renderVillage(slug) {
  const m = (state.data.members || []).find((x) => x.slug === slug || fold(x.name) === fold(slug));
  if (!m) {
    mount('Nenalezeno', h('div', { class: 'page' }, siteNav('obce'), h('p', { class: 'empty' }, 'Obec jsme nenašli. ', h('a', { href: '#/obce' }, 'Všechny obce'))));
    return;
  }
  const v = villageData(m.name);
  const web = safeUrl(m.web);
  const board = safeUrl(m.board_url);
  const horizon = addDays(TODAY, HORIZON);
  const now = v.dated.filter((x) => x.w.type === 'running' || x.w.date <= horizon);
  const later = v.dated.filter((x) => !now.includes(x));
  const latest = (v.news?.issues || []).find((i) => safeUrl(i.url));
  const ext = (href, text) => (href ? h('a', { class: 'band__btn', href, rel: 'noopener noreferrer', target: '_blank' }, text) : null);
  const evRow = (e) => h('li', {}, h('a', { class: 'row', href: safeUrl(e.url) || '#', rel: 'noopener noreferrer', target: '_blank' },
    h('span', { class: 'row__date' }, h('span', { class: 'row__pre' }, weekday(e.d < TODAY ? TODAY : e.d)), dm(e.d < TODAY ? TODAY : e.d)),
    h('span', { class: 'row__body' }, h('span', { class: 'row__title' }, e.t),
      h('span', { class: 'row__meta' }, [e.time, e.place].filter(Boolean).join(' · ')))));
  const others = (state.data.members || []).map((x) => x.name).filter((n) => n !== m.name);

  mount(m.name,
    h('div', { class: 'page' },
      siteNav('obce'),
      h('a', { class: 'back', href: '#/obce' }, icon(ICON_BACK, 20), 'Všechny obce'),
      h('header', { class: 'band band--page', style: obecStyle(m.name) },
        crest(m.name, 56),
        h('div', { class: 'band__text' },
          h('h1', {}, m.name),
          h('span', { class: 'band__sub' }, [m.publisher, `${v.live.length} ${plural(v.live.length, 'oznámení', 'oznámení', 'oznámení')} na desce`,
            v.events.length ? `${v.events.length} ${plural(v.events.length, 'akce', 'akce', 'akcí')}` : null].filter(Boolean).join(' · '))),
        h('div', { class: 'band__links' }, mineStar(m.name), ext(web, 'Web obce ↗'), ext(board, 'Úřední deska ↗'))),
      h('div', { class: 'village' },
        h('div', { class: 'village__main' },
          h('h2', { class: 'section-title section-title--first' }, `TEĎ ŘEŠIT · PŘÍŠTÍCH ${HORIZON} DNÍ`),
          now.length ? h('div', { class: 'daygroup__items village__entries' }, now.map(({ it, w }) => entry(it, w)))
            : h('p', { class: 'empty' }, `V příštích ${HORIZON} dnech žádné lhůty ani termíny.`),
          later.length ? h('h2', { class: 'section-title' }, 'POZDĚJI') : null,
          later.length ? h('ul', { class: 'rows' }, later.map(({ it, w }) => row(it, w.date, w.type === 'deadline' ? `${deadlineWord(it.s || {})} do ${dateCz(w.date)}` : null))) : null,
          v.undated.length ? h('h2', { class: 'section-title' }, 'NOVĚ VYVĚŠENO') : null,
          v.undated.length ? h('ul', { class: 'rows' }, v.undated.slice(0, 6).map((it) => row(it, it.p, 'vyvěšeno'))) : null,
          h('a', { class: 'more-link', href: `#/?obec=${encodeURIComponent(m.name)}` }, `Všechna oznámení obce (${v.live.length})`, icon('M9 5l7 7-7 7', 16)),
          v.council.length ? [
            h('h2', { class: 'section-title' }, 'CO ROZHODLA RADA A ZASTUPITELSTVO'),
            h('div', { class: 'meetings' }, v.council.slice(0, 3).map((mt) => h('div', { class: 'meetings__row' },
              h('span', { class: 'meetings__date' }, isIso(mt.date) ? dateCz(mt.date) : ''), meetingNode(mt, { compact: true })))),
            h('a', { class: 'more-link', href: `#/samosprava?obec=${encodeURIComponent(m.slug)}` }, `Všechna usnesení obce (${v.council.length})`, icon('M9 5l7 7-7 7', 16)),
          ] : null),
        h('aside', { class: 'village__side' },
          contactBox(m),
          h('h2', { class: `section-title${m.contact ? '' : ' section-title--first'}` }, 'AKCE'),
          v.events.length ? h('ul', { class: 'rows' }, v.events.slice(0, 6).map(evRow))
            : h('p', { class: 'empty' }, v.evSource ? 'V příštích dnech žádné akce.' : 'Kalendář akcí obce zatím nečteme.'),
          v.events.length ? h('a', { class: 'more-link', href: `#/akce?pohled=seznam&skryt=${encodeURIComponent(others.join(','))}` }, `Všechny akce obce (${v.events.length})`, icon('M9 5l7 7-7 7', 16)) : null,
          h('details', { class: 'subcal-more' }, h('summary', {}, icon(ICON_CAL_ADD, 16), 'Odebírat akce obce v kalendáři'), subscribeBox(m.slug)),
          h('h2', { class: 'section-title' }, 'ZPRAVODAJ'),
          v.news && latest ? h('a', { class: 'nl__latest nl__latest--side', href: safeUrl(latest.url), rel: 'noopener noreferrer', target: '_blank', title: latest.t },
            icon(ICON_DOC, 18), h('span', {}, h('span', { class: 'nl__label' }, v.news.name),
              h('strong', {}, issueLabel(latest, v.news.name)), issueIsNew(latest) ? newBadge() : null)) : null,
          v.news ? h('a', { class: 'more-link', href: `#/zpravodaje?obec=${encodeURIComponent(m.slug)}` }, `Starší čísla (${Math.max(0, (v.news.issues || []).length - 1)})`, icon('M9 5l7 7-7 7', 16))
            : h('p', { class: 'empty' }, 'Obec zpravodaj nevydává.')))));
}

// ---------- kalendář akcí ----------

const KDY = [['', 'Vše'], ['dnes', 'Dnes'], ['tyden', 'Tento týden'], ['vikend', 'Víkend']];

/** Rozsah dní pro volbu „kdy“: [od, do] včetně. */
function kdyRange(kdy) {
  const wd = new Date(utc(TODAY)).getUTCDay(); // 0 = neděle
  if (kdy === 'dnes') return [TODAY, TODAY];
  if (kdy === 'tyden') return [TODAY, addDays(TODAY, (7 - wd) % 7)];
  if (kdy === 'vikend') {
    const sat = wd === 0 ? addDays(TODAY, -1) : addDays(TODAY, (6 - wd + 7) % 7);
    return [sat < TODAY ? TODAY : sat, addDays(sat, 1)];
  }
  return [TODAY, '9999-12-31'];
}

const MONTHS = ['leden', 'únor', 'březen', 'duben', 'květen', 'červen', 'červenec', 'srpen', 'září', 'říjen', 'listopad', 'prosinec'];
// ---------- kalendář: přidat akci, odebírat kalendář ----------

const CAL_DIR = 'data/kalendar/';
const ICON_CAL_ADD = 'M4 5h16v15H4zM4 10h16M9 3v4M15 3v4M12 13v5M9.5 15.5h5';
const calAbs = (file) => new URL(CAL_DIR + file, location.href).href;
const pad2 = (n) => String(n).padStart(2, '0');

/** Posun pražského času o UTC (např. „+01:00“) – Outlook bez něj bere čas v zóně počítače. */
function pragueOffset(d, time) {
  try {
    const part = new Intl.DateTimeFormat('en-US', { timeZone: 'Europe/Prague', timeZoneName: 'longOffset' })
      .formatToParts(new Date(`${d}T${time}:00Z`)).find((p) => p.type === 'timeZoneName');
    return /[+-]\d{2}:\d{2}/.exec(part?.value || '')?.[0] || '+01:00';
  } catch { return '+01:00'; }
}

/**
 * Odkazy „přidat do kalendáře“ pro jednu akci {t, d, end?, time?, place?, desc?, cal?}.
 * Jako .ics na webu: s časem 2 h v pražském čase, bez času nebo vícedenní celodenně.
 */
function calLinks(o) {
  const multi = isIso(o.end) && o.end > o.d;
  const hm = !multi && /^(\d{1,2}):(\d{2})$/.exec(o.time || '');
  const q = (obj) => Object.entries(obj).filter(([, v]) => v != null && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  const desc = o.desc || '';
  let google; let outlook;
  if (hm) {
    const [y, mo, da] = o.d.split('-').map(Number);
    const b = new Date(Date.UTC(y, mo - 1, da, +hm[1], +hm[2]));
    const e = new Date(b.getTime() + 2 * 3600e3);
    const fmt = (x, sep) => `${x.getUTCFullYear()}${sep}${pad2(x.getUTCMonth() + 1)}${sep}${pad2(x.getUTCDate())}`;
    const tm = (x, sep) => `${pad2(x.getUTCHours())}${sep}${pad2(x.getUTCMinutes())}${sep}00`;
    google = { dates: `${fmt(b, '')}T${tm(b, '')}/${fmt(e, '')}T${tm(e, '')}`, ctz: 'Europe/Prague' };
    outlook = { startdt: `${fmt(b, '-')}T${tm(b, ':')}${pragueOffset(o.d, `${pad2(+hm[1])}:${hm[2]}`)}`,
      enddt: `${fmt(e, '-')}T${tm(e, ':')}${pragueOffset(fmt(e, '-'), tm(e, ':').slice(0, 5))}` };
  } else {
    const after = addDays(multi ? o.end : o.d, 1); // konec celodenní akce je výhradně den po ní
    google = { dates: `${o.d.replaceAll('-', '')}/${after.replaceAll('-', '')}` };
    outlook = { startdt: o.d, enddt: after, allday: 'true' };
  }
  const ms = (host) => `https://${host}/calendar/0/action/compose?${q({ rru: 'addevent', subject: o.t, ...outlook, location: o.place, body: desc })}`;
  return [
    o.cal ? ['Apple, iPhone (.ics)', `${CAL_DIR}u/${o.cal}.ics`, true] : null,
    ['Google Kalendář', `https://calendar.google.com/calendar/render?${q({ action: 'TEMPLATE', text: o.t, ...google, location: o.place, details: desc })}`],
    ['Outlook.com', ms('outlook.live.com')],
    ['Outlook – práce, škola (Microsoft 365)', ms('outlook.office.com')],
  ].filter(Boolean);
}

/** Tlačítko s nabídkou kalendářů; compact = jen ikona (u akce v seznamu). */
function addToCalendar(o, { compact = false } = {}) {
  return h('details', { class: `addcal${compact ? ' addcal--icon' : ''}` },
    h('summary', { class: 'addcal__btn', title: 'Přidat do kalendáře', 'aria-label': compact ? `Přidat do kalendáře: ${o.t}` : null },
      icon(ICON_CAL_ADD, compact ? 18 : 16), compact ? null : 'Přidat do kalendáře'),
    h('div', { class: 'addcal__menu' },
      calLinks(o).map(([label, href, file]) => h('a', file ? { href, download: '' }
        : { href, rel: 'noopener noreferrer', target: '_blank' }, label))));
}

// otevřená nabídka se zavře klikem jinam nebo otevřením jiné
document.addEventListener('click', (e) => {
  for (const d of document.querySelectorAll('details.addcal[open], details.subcal[open]')) if (!d.contains(e.target)) d.open = false;
});

/** Odběr kalendáře obce (slug) nebo celého regionu: aplikace kalendáře si ho sama obnovuje. */
function subscribeLinks(slug, name) {
  const https = calAbs(`${slug || 'vse'}.ics`);
  const webcal = https.replace(/^https?:/, 'webcal:');
  const title = `${name} – akce a termíny`;
  return [
    ['Apple, iPhone, Outlook v PC', webcal],
    ['Google Kalendář', `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(webcal)}`],
    ['Outlook.com', `https://outlook.live.com/calendar/0/addfromweb?url=${encodeURIComponent(https)}&name=${encodeURIComponent(title)}`],
  ].map(([label, href]) => ({ label, href, https }));
}

function subscribeBox(fixedSlug) {
  const members = (state.data.members || []).filter((m) => m.slug);
  const regionName = state.data.region?.name || 'Celý region';
  let slug = fixedSlug || '';
  const nameOf = (s) => members.find((m) => m.slug === s)?.name || regionName;
  const links = h('div', { class: 'subcal__links' });
  const status = h('span', { class: 'subcal__status', role: 'status' });
  const fill = () => {
    const list = subscribeLinks(slug, nameOf(slug));
    links.replaceChildren(
      ...list.map((x) => h('a', { class: 'subcal__link', href: x.href, rel: 'noopener noreferrer', target: x.href.startsWith('webcal:') ? null : '_blank' }, x.label)),
      h('button', { type: 'button', class: 'linkish', onclick: async () => {
        try { await navigator.clipboard.writeText(list[0].https); status.textContent = 'Adresa zkopírována.'; } catch { status.textContent = list[0].https; }
      } }, 'Kopírovat adresu'), status);
  };
  fill();
  return h('div', { class: 'subcal' },
    fixedSlug ? null : h('label', { class: 'subcal__pick' }, 'Kalendář: ',
      h('select', { onchange: (e) => { slug = e.target.value; status.textContent = ''; fill(); } },
        h('option', { value: '' }, `${regionName} (všechny obce)`),
        members.map((m) => h('option', { value: m.slug }, m.name)))),
    links,
    h('p', { class: 'subcal__hint' }, 'Nové akce a termíny z oznámení (zasedání, odstávky, uzavírky) se ti v kalendáři objeví samy. Google obnovuje odebírané kalendáře pomaleji, klidně až za den.'));
}

/** Akce z kalendáře obce → údaje pro „přidat do kalendáře“. */
const calOfEvent = (e) => ({ t: e.t, d: e.d, end: e.end, time: e.time, place: e.place, cal: e.cal,
  desc: [`Akce obce ${e.o}.`, safeUrl(e.url) ? `Podrobnosti: ${e.url}` : null].filter(Boolean).join('\n') });

const WD_HEAD = ['po', 'út', 'st', 'čt', 'pá', 'so', 'ne'];

/** Posun měsíce „YYYY-MM“ o n. */
function addMonths(ym, n) {
  const d = new Date(Date.UTC(+ym.slice(0, 4), +ym.slice(5, 7) - 1 + n, 1));
  return d.toISOString().slice(0, 7);
}

function renderEvents(params) {
  const ev = state.data.events || { sources: [], items: [] };
  // obce, jejichž akce jsou skryté (výchozí: vidět vše); v adrese ?skryt=Obec1,Obec2
  const hidden = new Set((params.get('skryt') || '').split(',').map((x) => x.trim()).filter(Boolean));
  const month = params.get('mesic') || '';
  const f = {
    pohled: params.get('pohled') === 'seznam' ? 'seznam' : 'mesic', // výchozí je měsíc jako v kalendáři
    kdy: params.get('kdy') || '',
    mesic: /^\d{4}-(?:0[1-9]|1[0-2])$/.test(month) ? month : TODAY.slice(0, 7),
    den: isIso(params.get('den')) ? params.get('den') : '',
    nove: params.get('nove') === '1' && !!visit.last, // z lišty novinek: jen akce nové od minulé návštěvy
  };
  const all = (ev.items || []).filter((e) => isIso(e.d) && (e.end || e.d) >= TODAY);
  const counts = new Map();
  for (const e of all) counts.set(e.o, (counts.get(e.o) || 0) + 1);
  const go = (patch) => {
    const n = { ...f, skryt: hidden, ...patch };
    const p = new URLSearchParams();
    if (n.pohled === 'seznam') p.set('pohled', 'seznam');
    if (n.skryt.size) p.set('skryt', [...n.skryt].join(','));
    if (n.pohled === 'seznam' && n.kdy) p.set('kdy', n.kdy);
    if (n.pohled === 'mesic' && n.mesic !== TODAY.slice(0, 7)) p.set('mesic', n.mesic);
    if (n.pohled === 'mesic' && n.den) p.set('den', n.den);
    if (n.nove) p.set('nove', '1');
    history.replaceState(null, '', p.toString() ? `#/akce?${p}` : '#/akce');
    renderEvents(p);
  };
  const toggle = (name) => {
    const next = new Set(hidden);
    if (next.has(name)) next.delete(name); else next.add(name);
    go({ skryt: next });
  };
  const only = (name) => go({ skryt: new Set([...counts.keys()].filter((n) => n !== name)) });
  const visible = all.filter((e) => !hidden.has(e.o) && (!f.nove || (isFresh(e.fs) && inMine(e.o))));

  // akce po dnech; vícedenní akce je v každém svém dni (nejvýš 31 dní)
  const byDay = new Map();
  for (const e of visible) {
    let day = e.d < TODAY ? TODAY : e.d;
    const last = e.end && e.end > e.d ? e.end : e.d;
    for (let i = 0; day <= last && i < 31; i += 1, day = addDays(day, 1)) {
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push(e);
    }
  }
  for (const list of byDay.values()) list.sort((a, b) => (a.time || '99').localeCompare(b.time || '99') || a.t.localeCompare(b.t, 'cs'));

  const eventNode = (e, day) => h('div', { class: 'ev-wrap' }, h('a', { class: `entry entry--event ev${isFresh(e.fs) ? ' is-fresh' : ''}`, href: safeUrl(e.url) || '#', rel: 'noopener noreferrer', target: '_blank' },
    h('span', { class: 'entry__meta' }, obecTag(e.o),
      [e.time, e.end && e.end !== e.d ? `${e.d < day ? 'probíhá' : 'až'} do ${dm(e.end)}` : null].filter(Boolean).map((x) => ` · ${x}`).join(''),
      isFresh(e.fs) ? [' ', freshBadge()] : null),
    h('h3', { class: 'entry__title' }, e.t),
    e.place || e.tags?.length ? h('span', { class: 'entry__sub' }, [e.place, ...(e.tags || [])].filter(Boolean).join(' · ')) : null),
    addToCalendar(calOfEvent(e), { compact: true }));
  const dayGroup = (iso, list) => {
    const n = daysTo(iso);
    return h('section', { class: 'daygroup', 'aria-label': `${weekdayLong(iso)} ${dateCz(iso)}` },
      h('div', { class: 'daygroup__date' },
        h('span', { class: 'daygroup__wd' }, weekday(iso)),
        h('span', { class: 'daygroup__d' }, dm(iso)),
        h('span', { class: `daygroup__rel${n <= 2 ? ' daygroup__rel--soon' : ''}` }, relDay(iso))),
      h('div', { class: 'daygroup__items' }, list.map((e) => eventNode(e, iso))));
  };

  let content;
  let shown;
  if (f.pohled === 'seznam') {
    const [from, to] = kdyRange(f.kdy);
    const keys = [...byDay.keys()].filter((d) => d >= from && d <= to).sort();
    // v seznamu je vícedenní akce jen jednou – v prvním dni výběru
    const seen = new Set();
    const groups = keys.map((d) => [d, byDay.get(d).filter((e) => !seen.has(e) && seen.add(e))]).filter(([, l]) => l.length);
    shown = seen.size;
    content = h('div', { class: 'timeline' }, groups.length ? groups.map(([d, l]) => dayGroup(d, l)) : h('p', { class: 'empty' }, 'V tomto výběru žádné akce.'));
  } else {
    const first = `${f.mesic}-01`;
    const offset = (new Date(utc(first)).getUTCDay() + 6) % 7; // pondělí = 0
    const start = addDays(first, -offset);
    const nextMonth = `${addMonths(f.mesic, 1)}-01`;
    const weeks = [];
    for (let wk = start; wk < nextMonth; wk = addDays(wk, 7)) weeks.push(wk);
    const inMonth = [...byDay.keys()].filter((d) => d.startsWith(f.mesic));
    shown = new Set(inMonth.flatMap((d) => byDay.get(d))).size;
    const sel = f.den && f.den.startsWith(f.mesic) ? f.den : (TODAY.startsWith(f.mesic) ? TODAY : inMonth.sort()[0] || '');
    const lastData = all.reduce((m, e) => (e.d > m ? e.d : m), TODAY);
    const cell = (iso) => {
      const list = byDay.get(iso) || [];
      const cls = ['cal__day', iso.startsWith(f.mesic) ? '' : 'cal__day--other', iso < TODAY ? 'cal__day--past' : '',
        iso === TODAY ? 'cal__day--today' : '', iso === sel ? 'cal__day--sel' : '', list.length ? 'cal__day--has' : ''].filter(Boolean).join(' ');
      return h('button', {
        type: 'button', class: cls, 'aria-pressed': String(iso === sel),
        'aria-label': `${weekdayLong(iso)} ${dateCz(iso)}: ${list.length ? `${list.length} ${plural(list.length, 'akce', 'akce', 'akcí')}` : 'nic'}`,
        onclick: () => go({ den: iso, mesic: iso.slice(0, 7) }),
      },
      h('span', { class: 'cal__num' }, String(+iso.slice(8, 10))),
      h('span', { class: 'cal__evs', 'aria-hidden': 'true' },
        list.slice(0, 3).map((e) => h('span', { class: 'cal__ev', style: obecStyle(e.o) }, e.time ? h('span', { class: 'cal__time' }, e.time) : null, e.t)),
        list.length > 3 ? h('span', { class: 'cal__more' }, `+${list.length - 3} další`) : null),
      h('span', { class: 'cal__dots', 'aria-hidden': 'true' },
        list.length ? h('span', { class: 'cal__count' }, String(list.length)) : null));
    };
    const selList = sel ? byDay.get(sel) || [] : [];
    content = h('div', { class: 'cal-wrap' },
      h('div', { class: 'cal__nav' },
        h('button', { type: 'button', class: 'fbtn cal__arrow', 'aria-label': 'Předchozí měsíc', onclick: () => go({ mesic: addMonths(f.mesic, -1), den: '' }) }, icon('M15 5l-7 7 7 7', 18)),
        h('h2', { class: 'cal__title' }, `${MONTHS[+f.mesic.slice(5, 7) - 1]} ${f.mesic.slice(0, 4)}`),
        h('button', { type: 'button', class: 'fbtn cal__arrow', 'aria-label': 'Další měsíc', onclick: () => go({ mesic: addMonths(f.mesic, 1), den: '' }) }, icon('M9 5l7 7-7 7', 18)),
        f.mesic !== TODAY.slice(0, 7) ? h('button', { type: 'button', class: 'linkish', onclick: () => go({ mesic: TODAY.slice(0, 7), den: '' }) }, 'Dnes') : null),
      h('div', { class: 'cal', role: 'grid', 'aria-label': 'Měsíc' },
        WD_HEAD.map((w) => h('span', { class: 'cal__head', role: 'columnheader' }, w)),
        weeks.map((wk) => Array.from({ length: 7 }, (_, i) => cell(addDays(wk, i))))),
      first > lastData ? h('p', { class: 'empty' }, 'Akce čteme na 60 dní dopředu – pro tento měsíc zatím nic nemáme.') : null,
      sel ? h('div', { class: 'timeline cal__detail' },
        selList.length ? dayGroup(sel, selList) : h('p', { class: 'empty' }, `${weekdayLong(sel)} ${dateCz(sel)}: žádné akce.`)) : null);
  }

  const villagesWith = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b, 'cs'));
  const sources = ev.sources || [];
  const linkOnly = sources.filter((s) => !s.read || !s.ok);
  const knownNames = new Set(sources.map((s) => s.obec));
  const noCal = (state.data.members || []).map((m) => m.name).filter((n) => !knownNames.has(n));
  mount('Kalendář akcí',
    h('div', { class: 'page' },
      siteNav('akce'),
      h('header', { class: 'masthead' },
        h('h1', {}, 'Kalendář akcí'),
        h('span', { class: 'masthead__meta' }, 'Z kalendářů na webech obcí · příštích 60 dní'),
        state.data.sample ? h('div', { class: 'sample' }, 'Ukázková data – nejde o skutečné akce.') : null),
      h('section', { class: 'filters', 'aria-label': 'Výběr' },
        h('div', { class: 'filters__row filters__row--view' },
          h('div', { class: 'seg', role: 'group', 'aria-label': 'Pohled' },
            [['mesic', 'Měsíc'], ['seznam', 'Seznam']].map(([k, label]) => h('button', {
              type: 'button', class: 'seg__btn', 'aria-pressed': String(f.pohled === k), onclick: () => go({ pohled: k }),
            }, label))),
          f.pohled === 'seznam' ? h('div', { class: 'topics', role: 'group', 'aria-label': 'Kdy' }, KDY.map(([k, label]) => h('button', {
            type: 'button', class: 'topic', 'aria-pressed': String(f.kdy === k), onclick: () => go({ kdy: k }),
          }, label))) : null),
        h('div', { class: 'places-filter' },
          h('div', { class: 'places-filter__head' },
            h('span', { class: 'places-filter__label' }, 'Obce'),
            h('span', { class: 'places-filter__hint' }, 'klikni pro skrytí / zobrazení'),
            hidden.size ? h('button', { type: 'button', class: 'linkish', onclick: () => go({ skryt: new Set() }) }, 'Zobrazit všechny') : null),
          h('div', { class: 'chips', role: 'group', 'aria-label': 'Obce v kalendáři' }, villagesWith.map((name) => h('span', { class: 'chipwrap' },
            h('button', {
              type: 'button', class: 'chip', 'aria-pressed': String(!hidden.has(name)), onclick: () => toggle(name),
              title: hidden.has(name) ? 'Zobrazit akce obce' : 'Skrýt akce obce',
            }, h('span', { class: 'chip__mark', 'aria-hidden': 'true' }, hidden.has(name) ? null : icon('M5 12l5 5 9-10', 12)), crest(name, 16), name, h('span', { class: 'topic__n' }, String(counts.get(name)))),
            h('button', { type: 'button', class: 'chip__only', onclick: () => only(name), 'aria-label': `Jen ${name}` }, 'jen')))))),
      h('div', { class: 'summary' }, h('span', {}, h('strong', {}, String(shown)), ` ${plural(shown, 'akce', 'akce', 'akcí')}`,
        f.pohled === 'mesic' ? ` v měsíci ${MONTHS[+f.mesic.slice(5, 7) - 1]}` : ''),
        f.nove ? freshFilterChip(() => go({ nove: false })) : null,
        h('button', { type: 'button', class: 'linkish summary__sub', onclick: () => document.getElementById('h-sub')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
          icon(ICON_CAL_ADD, 16), 'Odebírat v kalendáři')),
      h('div', { class: f.pohled === 'mesic' ? 'main main--wide' : 'main' },
        content,
        h('section', { class: 'villages', 'aria-labelledby': 'h-sub' },
          h('h2', { id: 'h-sub' }, 'ODEBÍRAT KALENDÁŘ'),
          subscribeBox()),
        h('section', { class: 'villages', 'aria-labelledby': 'h-cal' },
          h('h2', { id: 'h-cal' }, 'KALENDÁŘE NA WEBECH OBCÍ'),
          h('ul', {}, sources.map((s) => h('li', {}, h('a', { class: 'villages__obec', href: safeUrl(s.url), rel: 'noopener noreferrer', target: '_blank' }, crest(s.obec, 16), s.obec)))),
          linkOnly.length ? h('p', {}, `Akce z webu zatím nenačítáme: ${linkOnly.map((s) => s.obec).join(', ')} – otevři jejich kalendář.`) : null,
          noCal.length ? h('p', {}, 'Kalendář jsme nenašli: ', obecTags(noCal)) : null,
          h('p', {}, 'Akce přebíráme tak, jak je obce zveřejňují; podrobnosti a změny najdeš na odkazu u akce.')))));
}

// ---------- detail ----------

const docIcon = (k) => ({ mapa: ICON_MAP, sken: ICON_SCAN, obrazek: ICON_IMG }[k] || ICON_DOC);

function section(id, title, ...content) {
  return h('section', { 'aria-labelledby': id }, h('h2', { id, class: 'h2' }, title), ...content);
}

function renderDetail(id) {
  const back = h('a', { class: 'back', href: '#/' }, icon(ICON_BACK, 20), 'Zpět na přehled');
  back.addEventListener('click', (e) => {
    if (fromList) { e.preventDefault(); history.back(); } // zachová filtr i pozici v seznamu
  });
  const it = state.data.items.find((x) => x.id === id);
  if (!it) {
    mount('Nenalezeno', h('div', { class: 'page' }, siteNav('desky'), back, h('div', { class: 'detail' }, h('p', {}, 'Oznámení už na desce není nebo odkaz neplatí.'))));
    return;
  }
  const twins = twinsOf(it);
  const s = it.s || {};
  const w = when(it, { all: true });
  const docs = (it.docs || []).map((x) => ({ ...x, url: safeUrl(x.url) })).filter((x) => x.url);
  const original = safeUrl(it.url);
  const bullets = Array.isArray(s.bullets) ? s.bullets : [];
  const whenText = !w ? null
    : w.type === 'running' ? `probíhá do ${dateCz(w.until, { wd: true })}`
      : `${dateCz(w.date, { wd: true })}${s.time && w.type === 'event' ? ` v ${s.time}` : ''} · ${relDay(w.date)}`;

  // oranžová jen pro lhůtu nebo omezení, modrá pro jednání a akce, jinak neutrální
  const tone = !w || s.foreign ? 'todo todo--calm' : w.kind === 'warn' ? 'todo' : w.kind === 'event' ? 'todo todo--event' : 'todo todo--calm';

  const ai = aiInfo(it, s, docs.length, original);
  // budoucí nebo probíhající akce/jednání z oznámení → přidat do kalendáře
  const calBtn = it.cal && w && (w.type === 'event' || w.type === 'running') && isIso(s.event)
    ? h('div', { class: 'todo__cal' }, addToCalendar({ t: `${it.t} (${it.o})`, d: s.event, end: s.event_end, time: s.time, place: s.where, cal: it.cal,
      desc: [s.tldr, s.action, `Oznámení: ${new URL(detailHref(it), location.href).href}`].filter(Boolean).join('\n') })) : null;
  mount(s.tldr || it.t,
    h('div', { class: 'page' },
      siteNav('desky'),
      back,
    h('article', { class: 'detail' },
      h('div', {},
        h('div', { class: 'detail__meta' }, h('a', { class: 'obec obec--link', href: obecHref(it.o), style: obecStyle(it.o) }, crest(it.o, 18), it.o),
          ` · ${label(it)} · vyvěšeno ${dateCz(it.p, { year: true })}`, badges(it), ai.chip),
        ai.panel,
        h('h1', {}, withLinks(s.tldr || it.t, it))),
      s.foreign ? h('section', { class: 'foreign-note', 'aria-label': 'Vyvěšeno za jiný úřad' },
        h('p', {}, h('strong', {}, `Vydal jiný úřad: ${s.foreign}. `), `Obec ${it.o} oznámení jen vyvěšuje na své desce.`),
        s.foreign_why ? h('p', { class: 'muted' }, s.foreign_why) : null) : null,
      s.action
        ? h('section', { class: tone, 'aria-label': 'Co můžeš udělat' },
          h('p', { class: 'todo__main' }, withLinks(s.action, it)),
          whenText ? h('p', { class: 'todo__when' }, whenText) : null, calBtn)
        : h('section', { class: tone === 'todo' ? tone : `${tone} todo--plain`, 'aria-label': 'Co můžeš udělat' },
          h('p', {}, whenText || 'Jen pro informaci, nic dělat nemusíš.'), calBtn),
      twins.length ? h('section', { class: 'twins', 'aria-label': 'Stejné oznámení u dalších obcí' },
        h('span', {}, 'Totéž vyvěsily i: '),
        twins.map((x, i) => [i ? ', ' : '', h('a', { class: 'obec obec--link', href: detailHref(x), style: obecStyle(x.o) }, crest(x.o, 14), x.o)])) : null,
      s.impact || s.affects ? h('section', { class: 'meaning', 'aria-labelledby': 'h-mean' },
        h('h2', { id: 'h-mean', class: 'h2' }, 'CO TO ZNAMENÁ'),
        s.impact ? h('p', { class: 'meaning__lead' }, withLinks(czDates(s.impact), it)) : null,
        s.affects ? h('p', { class: 'muted' }, h('strong', {}, 'Týká se: '), withLinks(s.affects, it)) : null,
        s.importance === 'vysoka' && s.importance_why && !s.foreign ? h('p', { class: 'muted' }, h('strong', {}, 'Proč je to důležité: '), s.importance_why) : null) : null,
      bullets.length ? section('h-gist', s.src === 'claude' ? 'CO PŘESNĚ' : 'VE ZKRATCE',
        h('ul', { class: 'bullets' }, bullets.slice(0, 5).map((b) => h('li', {}, withLinks(czDates(b), it)))),
        bullets.length > 5 ? h('details', { class: 'more-details' },
          h('summary', {}, `Další podrobnosti (${bullets.length - 5})`),
          h('ul', { class: 'bullets' }, bullets.slice(5).map((b) => h('li', {}, withLinks(czDates(b), it))))) : null) : null,
      s.facts?.length ? section('h-facts', 'KLÍČOVÉ ÚDAJE',
        h('dl', { class: 'facts' }, s.facts.map((f) => [h('dt', {}, f.label), h('dd', {}, withLinks(czDates(f.value), it))]))) : null,
      s.dates?.length ? section('h-dates', 'DŮLEŽITÁ DATA',
        h('ul', { class: 'dates' }, s.dates.filter((x) => isIso(x.date)).map((x) => h('li', { class: daysTo(x.date) < 0 ? 'past' : null },
          h('span', { class: 'mono' }, dateCz(x.date, { wd: true })), h('span', {}, withLinks(x.what, it)))))) : null,
      s.where ? section('h-where', 'KDE', h('p', { style: 'margin:0' }, withLinks(s.where, it),
        // hledání v mapě jen u jednoho místa – výčet míst („…; …“) nebo dlouhý popis úseků by Google nenašel
        !s.where.includes(';') && s.where.length <= 140 ? [' ', h('a', { class: 'maplink', href: placeSearchUrl(s.where, it.o),
          rel: 'noopener noreferrer', target: '_blank', title: 'Vyhledat místo v Google Mapách' }, 'Ukázat na mapě')] : null)) : null,
      section('h-docs', docs.length ? `PŘÍLOHY (${docs.length})` : 'BEZ PŘÍLOH',
        h('div', { class: 'docs' }, docs.map((x) => {
          const title = x.w ? x.w.charAt(0).toUpperCase() + x.w.slice(1) : (x.n || 'Příloha');
          return h('a', { class: 'doc', href: x.url, rel: 'noopener noreferrer' },
            h('span', { class: 'muted' }, icon(docIcon(x.k), 24)),
            h('span', { class: 'doc__body' },
              h('span', { class: 'doc__name' }, title),
              x.sum ? h('span', { class: 'doc__sum' }, x.sum) : null,
              h('span', { class: 'doc__info' }, [
                x.w && x.n && fold(x.n) !== fold(x.w) ? x.n : null,
                x.x ? x.x.toUpperCase() : null,
                x.k === 'sken' ? (x.ocr ? 'sken, text rozpoznán' : 'sken') : x.k === 'mapa' ? 'mapa / výkres' : null,
                x.pg ? `${x.pg} ${plural(x.pg, 'strana', 'strany', 'stran')}` : null,
              ].filter(Boolean).join(' · '))));
        }))),
      s.contact ? section('h-contact', 'KAM SE OBRÁTIT', h('p', { style: 'margin:0' }, s.contact)) : null,
      h('section', { class: 'official' },
        h('div', {}, 'Úřední název: ', it.t),
        it.cj ? h('div', {}, `Č. j.: ${it.cj}`) : null,
        isIso(it.u) ? h('div', {}, `Visí do ${dateCz(it.u, { year: true })}`) : null,
        it.r ? h('div', {}, `Sejmuto ${dateCz(it.r.slice(0, 10), { year: true })}`) : null,
        original ? h('a', { href: original, rel: 'noopener noreferrer' }, `Původní znění na úřední desce (${it.o})`) : null,
        s.pending ? h('p', { class: 'warn-note' }, 'Úřad oznámení upravil (příloha nebo termín). Nové shrnutí se připravuje, tohle je k předchozí verzi.') : null,
        h('p', {}, s.src === 'claude'
          ? [`Shrnutí napsala AI (${modelName(s.model)}) z textu a příloh`,
            stampCz(s.summarized_at) || (isIso(s.summarized) ? ` ${dateCz(s.summarized, { year: true })}` : ''),
            stampCz(s.updated_at) ? `, aktualizováno po změně oznámení${stampCz(s.updated_at)}` : '',
            '. Může se splést – právně platí znění na úřední desce.']
          : 'Shrnutí vzniklo automaticky z textu oznámení. Právně platí znění na úřední desce.')))));
}

// ---------- nápověda: stránka „Jak to funguje“, lišta na úvodu, štítek „AI shrnutí“ v detailu ----------

const HELP_HREF = '#/jak-to-funguje';
const HELP_STRIP_KEY = 'help-strip-closed';
const REPO_URL = 'https://github.com/twarr58/dolnobrezansko';

/** Lišta nad filtrem úvodní stránky – jen dokud ji návštěvník nezavře (zapamatuje prohlížeč; bez úložiště se ukáže znovu). */
function helpStrip() {
  try { if (localStorage.getItem(HELP_STRIP_KEY)) return null; } catch { /* bez úložiště lištu ukázat */ }
  const n = state.data?.members?.length || 11;
  const strip = h('aside', { class: 'helpstrip', 'aria-label': 'O webu' },
    h('p', {}, h('strong', {}, `Úřední desky ${n} obcí na jednom místě.`),
      ' Oznámení každou hodinu shrnuje AI, platí vždy originál na desce obce. ',
      h('a', { href: HELP_HREF }, 'Jak to funguje →')),
    h('button', {
      type: 'button', class: 'helpstrip__x', 'aria-label': 'Zavřít a už neukazovat', title: 'Zavřít a už neukazovat',
      onclick: () => {
        try { localStorage.setItem(HELP_STRIP_KEY, '1'); } catch { /* jen pro tuto návštěvu */ }
        gc({ path: 'nápověda: lišta zavřena', title: 'Lišta na úvodu', event: true });
        strip.remove();
      },
    }, '×'));
  return strip;
}

/** Štítek „AI shrnutí“ v detailu oznámení: rozbalí, kdo text napsal, z čeho a co platí. Jen u shrnutí od Claude. */
function aiInfo(it, s, nDocs, original) {
  if (s.src !== 'claude') return { chip: null, panel: null };
  const linksNote = it.pl || it.ul
    ? h('li', {}, 'Podtržené ', it.ul ? h('span', { class: 'street' }, 'ulice') : null, it.ul && it.pl ? ' a ' : null,
      it.pl ? h('span', { class: 'parcel' }, 'parcely') : null,
      ` ${it.ul && it.pl ? 'vedou do mapy a katastru' : it.ul ? 'vedou do mapy' : 'vedou do katastru'} – doplnil je web podle registrů ČÚZK, ne AI.`)
    : null;
  const panel = h('div', { class: 'aiinfo', id: 'aiinfo', hidden: true },
    h('b', {}, `Tenhle text napsala AI (${modelName(s.model)})`),
    h('ul', {},
      h('li', {}, `Přečetla oznámení${nDocs ? ` a ${nDocs === 1 ? '1 přílohu' : nDocs < 5 ? `${nDocs} přílohy` : `${nDocs} příloh`}` : ''} z úřední desky obce ${it.o}.`),
      linksNote,
      h('li', {}, 'Může se splést. Platí ', original
        ? h('a', { href: original, rel: 'noopener noreferrer' }, 'původní znění na úřední desce') : 'původní znění na úřední desce', '.')),
    h('a', { href: HELP_HREF }, 'Jak to funguje →'));
  const chip = h('button', {
    type: 'button', class: 'aichip', 'aria-expanded': 'false', 'aria-controls': 'aiinfo', title: 'Kdo napsal tenhle text?',
    onclick: () => {
      panel.hidden = !panel.hidden;
      chip.setAttribute('aria-expanded', String(!panel.hidden));
      chip.lastChild.textContent = panel.hidden ? '▾' : '▴';
      if (!panel.hidden) gc({ path: 'nápověda: štítek AI shrnutí', title: it.o, event: true });
    },
  }, '✦ AI shrnutí ', h('span', { 'aria-hidden': 'true' }, '▾'));
  return { chip, panel };
}

/** Oddíl nápovědy: novinky od poslední návštěvy, výběr Mých obcí, zapomenutí nastavení. */
function helpSettings() {
  const names = (state.data?.members || []).map((m) => m.name).sort((x, y) => x.localeCompare(y, 'cs'));
  const chips = h('div', { class: 'mine-chips', role: 'group', 'aria-label': 'Moje obce' });
  const paint = () => {
    const mine = getMine();
    chips.replaceChildren(...names.map((name) => h('button', {
      type: 'button', class: 'mine-chip', 'aria-pressed': String(mine.has(name)),
      onclick: () => { toggleMine(name); paint(); },
    }, mine.has(name) ? `★ ${name}` : name)));
  };
  paint();
  const done = h('span', { class: 'muted', 'aria-live': 'polite' });
  return h('div', { style: 'display:grid;gap:12px' },
    h('p', { style: 'margin:0' }, 'Web si v tomto prohlížeči pamatuje, kdy jste ho naposledy otevřeli, a ukáže, co od té doby přibylo: ',
      'lištu na úvodu, počty u sekcí a štítek ', freshBadge(), '. Když vyberete Moje obce, novinky se počítají jen pro ně a na úvodu přibude přepínač.'),
    chips,
    h('p', { class: 'help__note' }, 'Ukládá se jen v tomto prohlížeči (localStorage), žádné cookies, nic se neodesílá. Na jiném zařízení ',
      'nebo v anonymním okně web novinky nezná a ukáže se jako při první návštěvě.'),
    h('div', {}, h('button', {
      type: 'button', class: 'fbtn',
      onclick: () => { forgetMe(); paint(); done.textContent = ' Zapomenuto – web se teď chová jako při první návštěvě.'; },
    }, 'Zapomenout moje nastavení'), done));
}

/** Stránka „Jak to funguje“: co web sbírá, co přidá AI, odkazy, značky, limity, soukromí. */
function renderHelp() {
  const d = state.data || {};
  const nObce = (d.members || []).length;
  const nDesky = (d.members || []).filter((m) => m.connected).length;
  const nItems = (d.items || []).filter((it) => !it.r).length;
  const nMeet = (d.council?.meetings || []).length;
  const row = (dt, ...dd) => h('div', { class: 'help__row' }, h('dt', {}, dt), h('dd', {}, ...dd));
  const sections = [
    ['help-zdroje', 'ODKUD JSOU DATA', 'Odkud jsou data', h('dl', { class: 'help__rows' },
      row('Úřední desky', `${nDesky} z ${nObce} obcí, každou hodinu přímo z webů obcí (u některých přes edesky.cz). Teď ${nItems} oznámení.`),
      row('Kalendář akcí', 'Akce z kalendářů na webech obcí na příštích 60 dní. Akci si přidáš do svého kalendáře (Apple, Google, Outlook), nebo kalendář obce rovnou odebíráš.'),
      row('Zpravodaje', 'Nová čísla obecních zpravodajů s odkazem na web obce.'),
      row('Samospráva', `Usnesení a zápisy rad a zastupitelstev z webů obcí, letošní rok${nMeet ? ` (${nMeet} zasedání)` : ''}.`),
      row('Obce', 'Kontakty a úřední hodiny úřadů, znaky obcí.'))],
    ['help-ai', 'CO DĚLÁ AI', 'Co dělá AI', h('dl', { class: 'help__rows' },
      row(h('span', {}, 'Shrnutí ', h('span', { class: 'badge badge--ai' }, 'Claude')), 'Hlavní věta, co máte udělat, co to znamená a koho se to týká.'),
      row('Lhůty a data', 'Vytáhne termíny – námitky, uzavírky, zasedání – a web je seřadí podle dní.'),
      row('Přílohy', 'Popíše každou přílohu. Naskenované dokumenty přečte jako obrázek.'),
      row('Usnesení', 'Rozloží usnesení rady a zastupitelstva na jednotlivé body a vyznačí důležité.'),
      row('Témata', 'Zařadí oznámení do tématu (doprava, územní plán, pozemky…) a určí, jestli je důležité.'))],
    ['help-odkazy', 'ODKAZY V TEXTU', 'Odkazy v textu', h('dl', { class: 'help__rows' },
      row(h('span', { class: 'parcel' }, '259/13'), 'Parcela – otevře Nahlížení do katastru nemovitostí.'),
      row(h('span', {}, h('span', { class: 'street' }, 'Nádražní'), ', ', h('span', { class: 'street' }, 'II/101')),
        'Ulice, dům (když je u ulice číslo) nebo silnice – bod v Google Mapách.'),
      row(h('span', { class: 'maplink' }, 'Ukázat na mapě'), 'U místa v oddílu KDE – hledání v Google Mapách podle textu.'),
      row('Původní znění', 'Na konci každého oznámení – odkaz na úřední desku obce.'),
      h('p', { class: 'help__note', style: 'margin-top:10px' }, 'Odkazy na parcely, ulice a silnice doplňuje web podle veřejných registrů ČÚZK (RÚIAN, ZABAGED), ne AI.'))],
    ['help-znacky', 'ZNAČKY A BARVY', 'Značky a barvy', h('dl', { class: 'help__rows' },
      row(h('span', { class: 'badge badge--hot' }, 'důležité'), 'AI oznámení vyhodnotila jako důležité – týká se hodně lidí nebo má lhůtu.'),
      row(h('span', { class: 'badge' }, 'nové'), `Vyvěšeno za poslední ${NEW_DAYS} dny.`),
      row(h('span', { class: 'badge badge--hot' }, 'změna'), 'Úřad oznámení upravil (příloha nebo termín).'),
      row('Oranžová / modrá', 'Oranžová je lhůta nebo omezení, modrá jednání a akce. Fialová vždy znamená věc od AI.'))],
    ['help-limity', 'CO AI NEUMÍ', 'Co AI neumí', h('p', { class: 'help__note' },
      'AI se může splést – hlavně u špatně čitelných skenů nebo dlouhých příloh. Shrnutí neplní funkci úřední desky: právně platí ',
      'jen znění vyvěšené obcí. Web je neoficiální a nekomerční, nejde o stránky obcí.')],
    ['help-nastaveni', 'NOVINKY A VAŠE NASTAVENÍ', 'Novinky a nastavení', helpSettings()],
    ['help-provoz', 'SOUKROMÍ A PROVOZ', 'Soukromí a provoz', h('dl', { class: 'help__rows' },
      row('Návštěvnost', 'Měří GoatCounter – bez cookies a bez osobních údajů, IP adresy se neukládají.'),
      row('Vaše nastavení', 'Poslední návštěva a Moje obce jen v tomto prohlížeči (localStorage), nikam se neposílají.'),
      row('Zdrojový kód', h('a', { href: REPO_URL, rel: 'noopener noreferrer', target: '_blank' }, 'github.com/twarr58/dolnobrezansko')),
      row('Podpora', h('a', { href: SPONSOR_URL, rel: 'noopener noreferrer', target: '_blank' }, 'Podpořit provoz'),
        ' – příspěvek jde na AI shrnutí a server v ČR.'))],
  ];
  const toc = h('nav', { class: 'help__toc', 'aria-label': 'Na této stránce' }, h('b', {}, 'NA TÉTO STRÁNCE'),
    sections.map(([id, , short]) => h('button', {
      type: 'button', onclick: () => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
    }, short)));
  mount('Jak to funguje',
    h('div', { class: 'page' },
      siteNav('help'),
      h('div', { class: 'help' },
        h('div', { class: 'help__main' },
          h('header', {},
            h('h1', {}, 'Jak to funguje'),
            h('p', { class: 'help__lede' }, `Co se děje v ${nObce} obcích mikroregionu ${String(d.region?.name || 'Dolnobřežansko').replace(/^mikroregion\s+/i, '')}, na jednom místě a srozumitelně. `,
              'Web je neoficiální a nekomerční, závazné je vždy znění na úřední desce obce.')),
          h('div', { class: 'help__steps' },
            h('div', { class: 'help__step' }, h('span', { class: 'help__k' }, '1 · sbíráme'),
              h('p', {}, 'Každou hodinu úřední desky, kalendáře akcí, zpravodaje a usnesení rad a zastupitelstev z webů obcí.')),
            h('div', { class: 'help__step help__step--ai' }, h('span', { class: 'help__k' }, '2 · přidá AI'),
              h('p', {}, 'Přečte oznámení i přílohy a napíše jednou větou, o co jde, co máte udělat a do kdy.')),
            h('div', { class: 'help__step' }, h('span', { class: 'help__k' }, '3 · dostanete'),
              h('p', {}, 'Přehled podle dní a lhůt, filtr obcí a témat, odkazy do mapy a katastru a vždy odkaz na originál.'))),
          sections.map(([id, title, , body]) => h('section', { class: 'help__sec', id, 'aria-labelledby': `${id}-h` },
            h('h2', { id: `${id}-h` }, title), body))),
        toc)));
}

// ---------- start ----------

async function start() {
  try {
    const res = await fetch(DATA, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!Array.isArray(data.items) || !Array.isArray(data.members)) throw new Error('neplatný formát dat');
    czTexts(data);
    state.data = data;
    OBCE = new Map(data.members.map((m) => [m.name, m]));
  } catch (err) {
    mount('Chyba', h('div', { class: 'col pad' }, h('h1', {}, 'Data se nepodařilo načíst'), h('p', {}, String(err.message || err))));
    return;
  }
  startVisit();
  window.addEventListener('hashchange', () => { touchVisit(); route(); countView(); });
  // po posunu stránky se hlavička (na počítači přichycená nahoře) zmenší i s logem
  const onScroll = () => document.documentElement.classList.toggle('is-scrolled', window.scrollY > 40);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  document.addEventListener('click', countOutbound, true);
  document.addEventListener('auxclick', countOutbound, true);
  route();
  countView();
  renderFooter();
}

start();
