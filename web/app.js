// Úřední desky – statický frontend bez buildu.
// Data generuje harvester: data/boards.json, data/recent.json, data/boards/<id>.json, data/meta.json.

const DATA = './data';
const STEP = 200;
const SOON_DAYS = 3;

const TOPICS = [
  { id: 'zastupitelstvo', label: 'zastupitelstvo', terms: ['zastupitelstv'] },
  { id: 'uzemni', label: 'územní plán', terms: ['uzemni plan', 'uzemniho planu', 'uzemni studi', 'uzemni rozhodnuti', 'uzemniho rizeni'] },
  { id: 'stavby', label: 'stavební řízení', terms: ['stavebni', 'stavby', 'kolaudac'] },
  { id: 'majetek', label: 'prodej a pronájem', terms: ['zamer', 'pronaj', 'prodej', 'pacht', 'vypujck'] },
  { id: 'drazby', label: 'dražby', terms: ['drazb'] },
  { id: 'rozpocet', label: 'rozpočet', terms: ['rozpoct', 'rozpocet', 'zaverecny ucet'] },
  { id: 'doprava', label: 'uzavírky', terms: ['uzavirk', 'objizd', 'omezeni provozu', 'dopravni opatreni'] },
  { id: 'nalez', label: 'ztráty a nálezy', terms: ['nalez', 'ztrat'] },
];

const state = { boards: new Map(), recent: [], meta: null, boardItems: new Map() };

// ---------- pomocné funkce ----------

/** Vytvoří element. Text se vkládá jen jako textový uzel – data z desek jsou nedůvěryhodná. */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);
const fold = (s) => (s || '').normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
const num = (n) => Number(n || 0).toLocaleString('cs-CZ');
const todayIso = () => new Date().toLocaleDateString('sv-SE');
const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);

function plural(n, one, few, many) {
  if (n === 1) return one;
  if (n >= 2 && n <= 4) return few;
  return many;
}

function dateCz(iso, withYear = true) {
  if (!iso) return '';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  const thisYear = new Date().getFullYear();
  return withYear || y !== thisYear ? `${d}. ${m}. ${y}` : `${d}. ${m}.`;
}

function dateTimeCz(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  return `${d.getDate()}. ${d.getMonth() + 1}. ${d.getFullYear()} v ${d.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' })}`;
}

function dayHeading(iso) {
  const diff = daysBetween(iso, todayIso());
  const long = new Date(`${iso}T12:00:00`).toLocaleDateString('cs-CZ', { weekday: 'long', day: 'numeric', month: 'long' });
  if (diff === 0) return ['Dnes', long];
  if (diff === 1) return ['Včera', long];
  return [long.charAt(0).toUpperCase() + long.slice(1), ''];
}

function endsIn(until) {
  if (!until) return null;
  const left = daysBetween(todayIso(), until);
  if (left < 0 || left > SOON_DAYS) return null;
  if (left === 0) return 'končí dnes';
  if (left === 1) return 'končí zítra';
  return `končí za ${left} ${plural(left, 'den', 'dny', 'dní')}`;
}

function attachments(n) {
  if (!n) return '';
  return `${n} ${plural(n, 'příloha', 'přílohy', 'příloh')}`;
}

function extOf(url) {
  const m = /\.([a-z0-9]{2,5})(?:$|[?#])/i.exec(url || '');
  return m ? m[1].toUpperCase() : '';
}

const boardName = (id) => state.boards.get(id)?.name || 'Neznámá deska';

async function loadJson(path) {
  const res = await fetch(`${DATA}/${path}`, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

async function boardItems(id) {
  if (!state.boardItems.has(id)) {
    const items = await loadJson(`boards/${encodeURIComponent(id)}.json`);
    for (const it of items) it._q = fold(`${it.title} ${it.cj || ''} ${it.sz || ''}`);
    state.boardItems.set(id, items);
  }
  return state.boardItems.get(id);
}

// ---------- směrování ----------

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, query = ''] = raw.split('?');
  return { parts: path.split('/').filter(Boolean).map(decodeURIComponent), params: new URLSearchParams(query) };
}

function setNav(key) {
  for (const a of document.querySelectorAll('[data-nav]')) {
    if (a.dataset.nav === key) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
}

function mount(title, ...nodes) {
  document.title = title ? `${title} – Úřední desky` : 'Úřední desky';
  document.getElementById('app').replaceChildren(...nodes.flat(Infinity).filter((n) => n != null && n !== false));
}

let routeSeq = 0;
async function route() {
  const seq = ++routeSeq;
  const { parts, params } = parseHash();
  const [section, a, b] = parts;
  window.scrollTo(0, 0);
  try {
    if (!section) return renderHome(params);
    if (section === 'deska' && a && b) return await renderItem(a, b, seq);
    if (section === 'deska' && a) return await renderBoard(a, params, seq);
    if (section === 'desky') return renderIndex();
    if (section === 'pokryti') return renderCoverage();
    if (section === 'o-projektu') return renderAbout();
    return renderNotFound();
  } catch (err) {
    if (seq === routeSeq) mount('Chyba', h('h1', {}, 'Něco se nepovedlo'), h('p', {}, String(err)));
  }
}

// ---------- přehled oznámení ----------

function readFilters(p) {
  return {
    q: p.get('q') || '',
    obec: p.get('obec') || '',
    tema: p.get('tema') || '',
    dni: ['7', '14', '30'].includes(p.get('dni')) ? p.get('dni') : '7',
    prilohy: p.get('prilohy') === '1',
    konci: p.get('konci') === '1',
    razeni: p.get('razeni') === 'konec' ? 'konec' : 'vyveseni',
  };
}

function writeFilters(f) {
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.obec) p.set('obec', f.obec);
  if (f.tema) p.set('tema', f.tema);
  if (f.dni !== '7') p.set('dni', f.dni);
  if (f.prilohy) p.set('prilohy', '1');
  if (f.konci) p.set('konci', '1');
  if (f.razeni !== 'vyveseni') p.set('razeni', f.razeni);
  const qs = p.toString();
  history.replaceState(null, '', qs ? `#/?${qs}` : '#/');
}

function filterRecent(f) {
  const terms = fold(f.q).split(/\s+/).filter(Boolean);
  const where = fold(f.obec).trim();
  const topic = TOPICS.find((t) => t.id === f.tema);
  const today = todayIso();
  const since = new Date(Date.parse(today) - Number(f.dni) * 86400000).toISOString().slice(0, 10);

  const out = state.recent.filter((it) => {
    if (it.p < since) return false;
    if (f.prilohy && !it.d) return false;
    if (f.konci && !endsIn(it.u)) return false;
    if (where && !it._b.includes(where)) return false;
    if (topic && !topic.terms.some((t) => it._q.includes(t))) return false;
    return terms.every((t) => it._q.includes(t) || it._b.includes(t));
  });
  if (f.razeni === 'konec') {
    out.sort((x, y) => (x.u || '9999').localeCompare(y.u || '9999') || y.p.localeCompare(x.p));
  }
  return out;
}

function noticeRow(it) {
  const soon = endsIn(it.u);
  const until = it.u ? `do ${dateCz(it.u, false)}` : '';
  // Na mobilu se termín zobrazuje v řádku s přílohami (.row__meta), na širokém displeji ve vlastním sloupci.
  const meta = [attachments(it.d), until].filter(Boolean).join(' · ');
  return h('li', { class: 'row' },
    h('div', { class: 'row__who' }, h('a', { href: `#/deska/${it.b}` }, boardName(it.b))),
    h('div', { class: 'row__what' },
      h('a', { href: `#/deska/${it.b}/${it.id}` }, it.t),
      it.d ? h('div', { class: 'row__meta wide' }, attachments(it.d)) : null,
      meta || soon ? h('div', { class: 'row__meta narrow' }, meta, soon ? h('span', { class: 'warn' }, `${meta ? ' · ' : ''}${soon}`) : null) : null),
    h('div', { class: 'row__until wide' },
      until,
      soon ? h('span', { class: 'warn' }, soon) : null));
}

function renderRegister(container, items, grouped, limit) {
  const shown = items.slice(0, limit);
  if (!shown.length) {
    container.replaceChildren(h('p', { class: 'empty' }, 'Nic nenalezeno. Zkuste jiný výraz nebo delší období.'));
    return;
  }
  if (!grouped) {
    container.replaceChildren(h('ol', { class: 'rows', style: 'border-top:2px solid var(--ink);margin-top:20px' }, shown.map(noticeRow)));
    return;
  }
  const counts = new Map();
  for (const it of items) counts.set(it.p, (counts.get(it.p) || 0) + 1);
  const days = [];
  let current = null;
  for (const it of shown) {
    if (!current || current.day !== it.p) {
      current = { day: it.p, rows: [] };
      days.push(current);
    }
    current.rows.push(it);
  }
  container.replaceChildren(...days.map((d) => {
    const [main, sub] = dayHeading(d.day);
    const total = counts.get(d.day);
    return h('section', { class: 'day' },
      h('div', { class: 'day__head' },
        h('h2', {}, main, sub ? h('span', {}, ` · ${sub}`) : null),
        h('span', { class: 'count' }, `${num(total)} ${plural(total, 'oznámení', 'oznámení', 'oznámení')}`)),
      h('ol', { class: 'rows' }, d.rows.map(noticeRow)));
  }));
}

function renderHome(params) {
  setNav('home');
  const f = readFilters(params);
  let limit = STEP;
  let results = [];

  const summary = h('p', { class: 'summary', 'aria-live': 'polite' });
  const register = h('div');
  const more = h('button', { type: 'button', class: 'more' }, 'Zobrazit další');

  const paint = () => {
    renderRegister(register, results, f.razeni === 'vyveseni', limit);
    const rest = results.length - limit;
    more.hidden = rest <= 0;
    more.textContent = `Zobrazit dalších ${num(Math.min(STEP, rest))} z ${num(rest)}`;
  };
  const update = () => {
    writeFilters(f);
    limit = STEP;
    results = filterRecent(f);
    const n = results.length;
    summary.replaceChildren(
      h('strong', {}, `${num(n)} ${plural(n, 'oznámení', 'oznámení', 'oznámení')}`),
      ` vyvěšených za posledních ${f.dni} dní na ${num(state.meta.boards_ok)} úředních deskách.`);
    paint();
  };
  more.addEventListener('click', () => { limit += STEP; paint(); });

  let timer;
  const onText = (key) => (e) => {
    f[key] = e.target.value;
    clearTimeout(timer);
    timer = setTimeout(update, 180);
  };

  const topicButtons = TOPICS.map((t) => h('button', {
    type: 'button',
    'aria-pressed': String(f.tema === t.id),
    onclick: () => {
      f.tema = f.tema === t.id ? '' : t.id;
      for (const b of topicButtons) b.setAttribute('aria-pressed', String(b.dataset.id === f.tema));
      update();
    },
    'data-id': t.id,
  }, t.label));

  const period = h('select', { id: 'dni', onchange: (e) => { f.dni = e.target.value; update(); } },
    h('option', { value: '7' }, 'posledních 7 dní'),
    h('option', { value: '14' }, 'posledních 14 dní'),
    h('option', { value: '30' }, 'posledních 30 dní'));
  period.value = f.dni;

  const order = h('select', { id: 'razeni', onchange: (e) => { f.razeni = e.target.value; update(); } },
    h('option', { value: 'vyveseni' }, 'podle data vyvěšení'),
    h('option', { value: 'konec' }, 'podle konce vyvěšení'));
  order.value = f.razeni;

  const names = h('datalist', { id: 'board-names' },
    [...state.boards.values()].map((b) => h('option', { value: b.name })));

  mount('',
    h('form', { class: 'search', role: 'search', onsubmit: (e) => { e.preventDefault(); clearTimeout(timer); update(); } },
      h('label', { class: 'vh', for: 'q' }, 'Co hledáte'),
      h('input', { id: 'q', type: 'search', value: f.q, placeholder: 'Co hledáte, např. pozemek, kanalizace, č. j.', oninput: onText('q') }),
      h('label', { class: 'vh', for: 'obec' }, 'Obec nebo úřad'),
      h('input', { id: 'obec', type: 'text', list: 'board-names', value: f.obec, placeholder: 'Obec nebo úřad', autocomplete: 'off', oninput: onText('obec') }),
      h('button', { type: 'submit' }, 'Hledat'),
      names),
    h('details', { class: 'filterbox', open: matchMedia('(min-width: 641px)').matches },
      h('summary', {}, 'Filtry'),
      h('div', { class: 'filters' },
      h('div', { class: 'topics' }, h('span', { class: 'muted' }, 'Téma:'), topicButtons),
      h('label', {}, h('span', { class: 'vh' }, 'Období'), period),
      h('label', {}, h('input', { type: 'checkbox', checked: f.prilohy, onchange: (e) => { f.prilohy = e.target.checked; update(); } }), 's přílohami'),
      h('label', {}, h('input', { type: 'checkbox', checked: f.konci, onchange: (e) => { f.konci = e.target.checked; update(); } }), `končí do ${SOON_DAYS} dnů`),
      h('label', {}, h('span', { class: 'vh' }, 'Řazení'), order))),
    summary, register, more);
  update();
}

// ---------- deska ----------

async function renderBoard(id, params, seq) {
  setNav('desky');
  const b = state.boards.get(id);
  if (!b) return renderNotFound();
  mount(b.name, h('p', { class: 'muted' }, 'Načítám desku…'));
  const items = await boardItems(id);
  if (seq !== routeSeq) return;

  const archive = params.has('sejmuto');
  const active = items.filter((i) => !i.removed);
  const removed = items.filter((i) => i.removed);
  const list = archive ? removed : active;
  list.sort((x, y) => (y.posted || '').localeCompare(x.posted || ''));

  const tbody = h('tbody');
  const paint = (q) => {
    const terms = fold(q).split(/\s+/).filter(Boolean);
    const rows = list.filter((it) => terms.every((t) => it._q.includes(t))).slice(0, 1000);
    tbody.replaceChildren(...rows.map((it) => {
      const soon = !archive && endsIn(it.until);
      return h('tr', {},
        h('td', { class: 'num' }, dateCz(it.posted)),
        h('td', {}, h('a', { href: `#/deska/${id}/${it.id}` }, it.title),
          it.cj ? h('div', { class: 'row__meta' }, `č. j. ${it.cj}`) : null),
        h('td', { class: 'num' }, archive ? dateCz(it.removed) : (it.until ? dateCz(it.until) : '–'),
          soon ? h('div', { class: 'warn' }, soon) : null),
        h('td', { class: 'num muted' }, it.docs?.length ? String(it.docs.length) : ''));
    }));
    if (!rows.length) tbody.append(h('tr', {}, h('td', { colspan: '4', class: 'muted' }, archive ? 'Archiv je zatím prázdný – sejmutá oznámení se začnou ukládat od prvních sběrů.' : 'Nic nenalezeno.')));
  };

  const source = safeUrl(b.access_url);
  const nkod = `https://data.gov.cz/datová-sada?iri=${encodeURIComponent(b.dataset_iri)}`;
  const status = b.ok
    ? h('span', {}, `Staženo ${dateTimeCz(b.fetched_at)}.`)
    : h('span', { class: 'bad' }, `Poslední stažení se nepovedlo: ${KINDS[b.kind]?.title || 'chyba'} (${b.error || 'bez podrobností'}). Zobrazuje se stav z dřívějška. `, h('a', { href: '#/pokryti' }, 'Co to znamená'), '.');

  mount(b.name,
    h('p', { class: 'crumbs' }, h('a', { href: '#/desky' }, 'Seznam desek'), ' / '),
    h('h1', {}, b.name),
    h('p', { class: 'lede' }, b.publisher && b.publisher !== b.name ? `${b.publisher} · ` : '', b.title),
    h('p', { class: 'facts' }, status, ' ',
      source ? h('a', { href: source, rel: 'noopener noreferrer' }, 'Zdrojová data') : null,
      ' · ', b.fmt && b.fmt !== 'ofn'
        ? h('span', { class: 'muted' }, b.fmt === 'imunis'
          ? 'Obec desku nezveřejňuje v katalogu otevřených dat, údaje jsou převzaty z její eDesky (iMunis).'
          : 'Obec desku nezveřejňuje v katalogu otevřených dat, údaje jsou převzaty z jejích webových stránek.')
        : h('a', { href: nkod, rel: 'noopener noreferrer' }, 'Záznam v katalogu')),
    h('nav', { class: 'tabs', 'aria-label': 'Výběr' },
      h('a', { href: `#/deska/${id}`, 'aria-current': archive ? null : 'page' }, `Vyvěšeno (${num(active.length)})`),
      h('a', { href: `#/deska/${id}?sejmuto`, 'aria-current': archive ? 'page' : null }, `Sejmuto (${num(removed.length)})`)),
    h('div', { class: 'boardfilter' },
      h('label', { class: 'vh', for: 'bq' }, 'Hledat na této desce'),
      h('input', { id: 'bq', type: 'search', placeholder: 'Hledat na této desce', oninput: (e) => paint(e.target.value) })),
    h('div', { class: 'scroll' }, h('table', { class: 'table' },
      h('thead', {}, h('tr', {},
        h('th', { scope: 'col' }, 'Vyvěšeno'),
        h('th', { scope: 'col' }, 'Oznámení'),
        h('th', { scope: 'col' }, archive ? 'Sejmuto' : 'Do'),
        h('th', { scope: 'col' }, 'Přílohy'))),
      tbody)),
    list.length > 1000 ? h('p', { class: 'muted' }, `Zobrazeno prvních 1 000 z ${num(list.length)}. Pro starší použijte hledání.`) : null);
  paint('');
}

// ---------- detail oznámení ----------

async function renderItem(boardId, itemId, seq) {
  setNav('');
  const b = state.boards.get(boardId);
  if (!b) return renderNotFound();
  const items = await boardItems(boardId);
  if (seq !== routeSeq) return;
  const it = items.find((x) => x.id === itemId);
  if (!it) return renderNotFound();

  const rows = [
    ['Vyvěšeno', dateCz(it.posted) || 'neuvedeno'],
    ['Relevantní do', it.until ? [dateCz(it.until), endsIn(it.until) ? h('span', { class: 'warn' }, ` (${endsIn(it.until)})`) : null] : 'neuvedeno'],
    it.cj && ['Číslo jednací', it.cj],
    it.sz && ['Spisová značka', it.sz],
    it.agendas?.length && ['Agenda', it.agendas.join(', ')],
    it.removed && ['Sejmuto', dateCz(it.removed)],
    it.first_seen && ['Poprvé zachyceno', dateTimeCz(it.first_seen)],
  ].filter(Boolean);

  const docs = (it.docs || []).map((d) => ({ ...d, url: safeUrl(d.url) })).filter((d) => d.url);
  const original = safeUrl(it.url);

  mount(it.title,
    h('article', { class: 'detail' },
      h('p', { class: 'crumbs' }, h('a', { href: `#/deska/${boardId}` }, b.name), ' / '),
      h('h1', {}, it.title),
      h('dl', { class: 'dl' }, rows.map(([k, v]) => [h('dt', {}, k), h('dd', {}, v)])),
      h('h2', { class: 'section' }, docs.length ? 'Dokumenty' : 'Bez dokumentů'),
      docs.length ? h('ol', { class: 'docs' }, docs.map((d) => h('li', {},
        h('a', { href: d.url, rel: 'noopener noreferrer' }, d.name || d.url),
        extOf(d.url) ? h('span', { class: 'ext' }, ` ${extOf(d.url)}`) : null))) : null,
      original ? h('p', { class: 'original' }, h('a', { href: original, rel: 'noopener noreferrer' }, `Zobrazit na úřední desce: ${b.name}`)) : null));
}

// ---------- seznam desek, pokrytí, o projektu ----------

function renderIndex() {
  setNav('desky');
  const all = [...state.boards.values()].sort((x, y) => x.name.localeCompare(y.name, 'cs'));
  const container = h('div', { class: 'az' });
  const paint = (q) => {
    const t = fold(q);
    const groups = new Map();
    for (const b of all) {
      if (t && !fold(`${b.name} ${b.publisher} ${b.title}`).includes(t)) continue;
      const letter = b.name.charAt(0).toLocaleUpperCase('cs');
      if (!groups.has(letter)) groups.set(letter, []);
      groups.get(letter).push(b);
    }
    container.replaceChildren(...[...groups].map(([letter, list]) => h('section', {},
      h('h2', {}, letter),
      h('ul', {}, list.map((b) => h('li', {},
        h('a', { href: `#/deska/${b.id}` }, b.name),
        h('span', { class: b.ok ? null : 'bad' }, b.ok ? num(b.count) : 'nedostupná')))))));
  };
  mount('Seznam desek',
    h('h1', {}, 'Seznam desek'),
    h('p', { class: 'lede' }, `${num(all.length)} úředních desek obcí a městských částí evidovaných v Národním katalogu otevřených dat. Číslo udává počet vyvěšených oznámení.`),
    h('div', { class: 'boardfilter' },
      h('label', { class: 'vh', for: 'iq' }, 'Hledat desku'),
      h('input', { id: 'iq', type: 'search', placeholder: 'Hledat obec nebo úřad', oninput: (e) => paint(e.target.value) })),
    container);
  paint('');
}

// Vysvětlení druhů chyb (pole `kind` z harvesteru). `who` říká, kdo může problém odstranit.
const KINDS = {
  timeout: { title: 'Server blokuje přístup ze zahraničí', who: 'my',
    text: 'Server úřadu nepřijímá spojení ze zahraničí. Sběr zatím běží na serverech GitHubu v USA a tyto weby (většinou u jednoho českého poskytovatele hostingu) přístup z nich blokují. Pracujeme na stahování přes server v Česku.' },
  connect: { title: 'Nelze se připojit', who: 'úřad',
    text: 'Spojení se serverem úřadu selhalo (neexistující adresa, odmítnuté spojení).' },
  tls: { title: 'Chyba zabezpečeného spojení', who: 'úřad',
    text: 'Certifikát HTTPS serveru je neplatný nebo neúplný a nepodařilo se ho automaticky doplnit.' },
  not_found: { title: 'Odkaz v katalogu nefunguje', who: 'úřad',
    text: 'Úřad má v Národním katalogu otevřených dat zapsanou adresu, na které data nejsou (HTTP 404). Opravit ji musí úřad v katalogu.' },
  server: { title: 'Chyba serveru úřadu', who: 'úřad',
    text: 'Server úřadu vrací chybu (HTTP 5xx nebo chybovou hlášku místo dat). Bývá to dočasné.' },
  http: { title: 'Server odmítl přístup', who: 'úřad',
    text: 'Server vrací jinou chybu HTTP, nejčastěji 403 (zakázaný přístup).' },
  html: { title: 'Místo dat webová stránka', who: 'úřad',
    text: 'Na zapsané adrese je běžná webová stránka, ne data podle normy. Úřad v katalogu uvedl špatný odkaz.' },
  empty: { title: 'Prázdná odpověď', who: 'úřad',
    text: 'Server vrací prázdný soubor.' },
  invalid: { title: 'Poškozená data', who: 'úřad',
    text: 'Soubor není platný JSON a nepodařilo se ho ani automaticky opravit.' },
  format: { title: 'Data neodpovídají normě', who: 'úřad',
    text: 'Soubor je čitelný, ale nemá strukturu podle OFN Úřední desky.' },
};

function renderCoverage() {
  setNav('pokryti');
  const m = state.meta;
  const boards = [...state.boards.values()];
  const failed = boards.filter((b) => !b.ok);
  const warned = boards.filter((b) => b.ok && b.warnings?.length);
  const groups = new Map();
  for (const b of failed) {
    const kind = KINDS[b.kind] ? b.kind : 'format';
    if (!groups.has(kind)) groups.set(kind, []);
    groups.get(kind).push(b);
  }
  const byName = (x, y) => x.name.localeCompare(y.name, 'cs');
  const share = m.boards_total ? Math.round((m.boards_ok / m.boards_total) * 100) : 0;

  const failedSection = ([kind, list]) => h('section', {},
    h('h2', { class: 'section' }, `${KINDS[kind].title} (${list.length})`),
    h('p', { class: 'note' }, KINDS[kind].text, KINDS[kind].who === 'úřad' ? ' Náprava je na straně úřadu.' : ''),
    h('ul', { class: 'docs' }, list.sort(byName).map((b) => {
      const url = safeUrl(b.used_url || b.access_url);
      return h('li', {}, h('a', { href: `#/deska/${b.id}` }, b.name),
        h('span', { class: 'ext' }, ` ${b.error || ''}`),
        url ? [' · ', h('a', { href: url, rel: 'noopener noreferrer', class: 'ext' }, 'adresa dat')] : null);
    })));

  mount('Pokrytí',
    h('h1', {}, 'Pokrytí'),
    h('div', { class: 'prose' },
      h('p', {}, `V katalogu je ${num(m.boards_total)} úředních desek obcí a městských částí. Při posledním stažení (${dateTimeCz(m.generated_at)}) se podařilo načíst ${num(m.boards_ok)} z nich (${share} %), dohromady ${num(m.items_active)} vyvěšených oznámení.`),
      h('p', {}, 'Desky, které se nepodařilo načíst, se zobrazují ve stavu z posledního úspěšného stažení. Obce, které úřední desku jako otevřená data nezveřejňují nebo ji neevidují v katalogu, tu chybí úplně.')),
    [...groups].sort((x, y) => y[1].length - x[1].length).map(failedSection),
    warned.length ? h('section', {},
      h('h2', { class: 'section' }, `Načteno s výhradami (${warned.length})`),
      h('p', { class: 'note' }, 'Tyto desky se načetly, ale jejich data nebo server neodpovídají normě. Harvester je opravil automaticky.'),
      h('ul', { class: 'docs' }, warned.sort(byName).map((b) => h('li', {},
        h('a', { href: `#/deska/${b.id}` }, b.name), h('span', { class: 'ext' }, ` ${b.warnings.join('; ')}`))))) : null);
}

function renderAbout() {
  setNav('about');
  mount('O projektu',
    h('h1', {}, 'O projektu'),
    h('div', { class: 'prose' },
      h('p', {}, 'Obce a městské části mohou (a podle zákona o svobodném přístupu k informacím by měly) zveřejňovat obsah své úřední desky také jako otevřená data a evidovat ji v Národním katalogu otevřených dat. Každá deska je ale samostatný soubor na serveru obce, takže je pracné sledovat víc než jednu.'),
      h('p', {}, 'Tento web každou hodinu stáhne všechny desky obcí a městských částí z katalogu, sjednotí je a umožní v nich hledat najednou. Desky krajů, ministerstev a soudů nesbírá. Oznámení, která z desky zmizí, zůstávají rok v záložce Sejmuto.'),
      h('p', {}, 'Hledání na úvodní stránce prochází oznámení vyvěšená za posledních 30 dní. Starší najdete na stránce konkrétní desky.'),
      h('p', {}, 'Některé obce, které desku do katalogu nezapsaly, se načítají přímo z jejich úřední desky na webu: služba iMunis eDeska (například Dolní Břežany) a weby na systému Galileo (například Semtěš a Bílé Podolí).'),
      h('p', {}, 'Zdrojový kód: ', h('a', { href: 'https://github.com/twarr58/uredni_desky' }, 'github.com/twarr58/uredni_desky'), '.')));
}

function renderNotFound() {
  mount('Nenalezeno', h('h1', {}, 'Stránka nenalezena'), h('p', {}, h('a', { href: '#/' }, 'Zpět na přehled oznámení')));
}

// ---------- start ----------

async function init() {
  try {
    const [boards, recent, meta] = await Promise.all([loadJson('boards.json'), loadJson('recent.json'), loadJson('meta.json')]);
    for (const b of boards) state.boards.set(b.id, b);
    for (const it of recent) {
      const b = state.boards.get(it.b);
      it._q = fold(it.t);
      it._b = fold(b ? `${b.name} ${b.publisher}` : '');
    }
    recent.sort((x, y) => y.p.localeCompare(x.p));
    state.recent = recent;
    state.meta = meta;
    document.getElementById('generated').textContent = dateTimeCz(meta.generated_at);
  } catch (err) {
    mount('Chyba', h('h1', {}, 'Data se nepodařilo načíst'), h('p', {}, String(err)),
      h('p', { class: 'muted' }, 'Lokálně: make sample && make serve'));
    return;
  }
  window.addEventListener('hashchange', route);
  route();
}

init();
