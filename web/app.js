// Úřední desky – statický frontend bez buildu.
// Čte data vygenerovaná harvesterem: data/boards.json, data/items.json, data/meta.json.

const DATA = './data';
const PAGE = 50;
const SOON_DAYS = 3;
const NEW_DAYS = 2;

const CHIPS = [
  { id: 'zastupitelstvo', label: 'Zasedání zastupitelstva', terms: ['zastupitelstv'] },
  { id: 'uzemni', label: 'Územní plánování', terms: ['uzemni plan', 'uzemniho planu', 'uzemni rozhodnuti', 'uzemniho rizeni'] },
  { id: 'zakazky', label: 'Veřejné zakázky', terms: ['zakazk', 'vyberove rizeni'] },
  { id: 'drazby', label: 'Dražby', terms: ['drazb'] },
  { id: 'majetek', label: 'Prodej a pronájem majetku', terms: ['pronaj', 'prodej', 'zamer', 'pachty', 'pacht'] },
  { id: 'volby', label: 'Volby', terms: ['volb', 'volic'] },
];

const state = { boards: new Map(), items: [], itemById: new Map(), meta: null, shown: PAGE };

// ---------- utils ----------

/** Vytvoří element; text se vkládá vždy jako textový uzel (data jsou nedůvěryhodná). */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
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
const todayIso = () => new Date().toLocaleDateString('sv-SE'); // YYYY-MM-DD v místním čase

function fmtDate(iso) {
  if (!iso) return 'neuvedeno';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return `${d}. ${m}. ${y}`;
}

function daysBetween(fromIso, toIso) {
  return Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 86400000);
}

function plural(n, one, few, many) {
  return n === 1 ? one : n >= 2 && n <= 4 ? few : many;
}

function docsLabel(n) {
  return n ? `${n} ${plural(n, 'příloha', 'přílohy', 'příloh')}` : 'bez příloh';
}

function boardName(b) {
  return b?.publisher || b?.title || 'Neznámá deska';
}

function itemFlags(it) {
  const today = todayIso();
  const left = it.until ? daysBetween(today, it.until) : null;
  return {
    active: !it.removed,
    isNew: !it.removed && it.first_seen && daysBetween(it.first_seen.slice(0, 10), today) <= NEW_DAYS,
    soon: !it.removed && left != null && left >= 0 && left <= SOON_DAYS,
    left,
  };
}

function soonLabel(left) {
  if (left === 0) return 'Končí dnes';
  if (left === 1) return 'Končí zítra';
  return `Končí za ${left} ${plural(left, 'den', 'dny', 'dní')}`;
}

function extOf(url) {
  const m = /\.([a-z0-9]{2,5})(?:$|[?#])/i.exec(url || '');
  return m ? m[1].toUpperCase() : 'URL';
}

// ---------- routing ----------

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

function mount(...nodes) {
  const app = document.getElementById('app');
  app.replaceChildren(...nodes);
  window.scrollTo(0, 0);
}

function route() {
  const { parts, params } = parseHash();
  const [section, id] = parts;
  if (!section) return renderHome(params);
  if (section === 'deska' && id) return renderBoard(id, params);
  if (section === 'oznameni' && id) return renderItem(id);
  if (section === 'obce') return renderBoards();
  if (section === 'pokryti') return renderCoverage();
  if (section === 'o-projektu') return renderAbout();
  return renderNotFound();
}

// ---------- home / search ----------

function readFilters(params) {
  return {
    q: params.get('q') || '',
    obec: params.get('obec') || '',
    chip: params.get('chip') || '',
    kdy: params.get('kdy') || '7',
    akt: params.get('akt') !== '0',
    pril: params.get('pril') === '1',
    soon: params.get('soon') === '1',
    sort: params.get('sort') || 'new',
  };
}

function writeFilters(f) {
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.obec) p.set('obec', f.obec);
  if (f.chip) p.set('chip', f.chip);
  if (f.kdy !== '7') p.set('kdy', f.kdy);
  if (!f.akt) p.set('akt', '0');
  if (f.pril) p.set('pril', '1');
  if (f.soon) p.set('soon', '1');
  if (f.sort !== 'new') p.set('sort', f.sort);
  const qs = p.toString();
  history.replaceState(null, '', qs ? `#/?${qs}` : '#/');
}

function filterItems(f) {
  const terms = fold(f.q).split(/\s+/).filter(Boolean);
  const obec = fold(f.obec).trim();
  const chip = CHIPS.find((c) => c.id === f.chip);
  const today = todayIso();
  const maxAge = { '1': 0, '7': 7, '30': 30 }[f.kdy];

  const out = state.items.filter((it) => {
    const fl = itemFlags(it);
    if (f.akt && !fl.active) return false;
    if (f.pril && !(it.docs && it.docs.length)) return false;
    if (f.soon && !fl.soon) return false;
    if (maxAge != null && (!it.posted || daysBetween(it.posted, today) > maxAge)) return false;
    if (obec && !it._board.includes(obec)) return false;
    if (chip && !chip.terms.some((t) => it._text.includes(t))) return false;
    return terms.every((t) => it._text.includes(t) || it._board.includes(t));
  });

  if (f.sort === 'soon') {
    out.sort((a, b) => (a.until || '9999').localeCompare(b.until || '9999'));
  } else if (f.sort === 'obec') {
    const name = (it) => boardName(state.boards.get(it.board));
    out.sort((a, b) => name(a).localeCompare(name(b), 'cs'));
  }
  return out;
}

function itemCard(it) {
  const b = state.boards.get(it.board);
  const fl = itemFlags(it);
  return h('li', { class: 'card' },
    h('div', { class: 'card__top' },
      h('a', { class: 'card__board', href: `#/deska/${it.board}` }, boardName(b)),
      h('div', { class: 'badges' },
        fl.isNew && h('span', { class: 'badge badge--new' }, 'Nové'),
        fl.soon && h('span', { class: 'badge badge--soon' }, soonLabel(fl.left)),
        it.removed && h('span', { class: 'badge' }, 'Sejmuto'),
      ),
    ),
    h('a', { class: 'card__title', href: `#/oznameni/${it.id}` }, it.title),
    h('div', { class: 'meta' },
      h('span', {}, 'Vyvěšeno ', h('span', { class: 'mono' }, fmtDate(it.posted))),
      h('span', {}, 'Do ', h('span', { class: 'mono' }, it.until ? fmtDate(it.until) : 'neuvedeno')),
      h('span', {}, docsLabel(it.docs?.length || 0)),
    ),
  );
}

function renderHome(params) {
  setNav('home');
  const f = readFilters(params);
  state.shown = PAGE;

  const resultsHead = h('h2', {});
  const list = h('ol', { class: 'cards' });
  const more = h('button', { type: 'button', class: 'btn btn--outline more' }, 'Načíst další');
  let current = [];

  const update = () => {
    writeFilters(f);
    current = filterItems(f);
    resultsHead.replaceChildren(
      `${current.length.toLocaleString('cs-CZ')} ${plural(current.length, 'oznámení', 'oznámení', 'oznámení')} `,
      h('span', {}, `z ${state.boards.size.toLocaleString('cs-CZ')} ${state.boards.size === 1 ? 'úřední desky' : 'úředních desek'}`),
    );
    paint();
  };
  const paint = () => {
    const slice = current.slice(0, state.shown);
    list.replaceChildren(...(slice.length ? slice.map(itemCard)
      : [h('li', { class: 'empty' }, 'Žádné oznámení neodpovídá filtrům.')]));
    more.hidden = current.length <= state.shown;
  };
  more.addEventListener('click', () => { state.shown += PAGE; paint(); });

  let t;
  const debounced = () => { clearTimeout(t); t = setTimeout(() => { state.shown = PAGE; update(); }, 150); };
  const bindText = (key) => (e) => { f[key] = e.target.value; debounced(); };
  const bindCheck = (key) => (e) => { f[key] = e.target.checked; state.shown = PAGE; update(); };

  const chipButtons = CHIPS.map((c) => h('button', {
    type: 'button', class: 'chip', 'aria-pressed': String(f.chip === c.id),
    onclick: (e) => {
      f.chip = f.chip === c.id ? '' : c.id;
      for (const btn of chipButtons) btn.setAttribute('aria-pressed', String(btn === e.currentTarget && !!f.chip));
      state.shown = PAGE; update();
    },
  }, c.label));

  const radio = (value, label) => h('label', { class: 'check' },
    h('input', { type: 'radio', name: 'kdy', value, checked: f.kdy === value, onchange: () => { f.kdy = value; state.shown = PAGE; update(); } }),
    label);

  const sortSel = h('select', { id: 'sort', onchange: (e) => { f.sort = e.target.value; update(); } },
    h('option', { value: 'new' }, 'Nejnověji vyvěšené'),
    h('option', { value: 'soon' }, 'Brzy končí'),
    h('option', { value: 'obec' }, 'Podle obce'));
  sortSel.value = f.sort;

  mount(
    h('section', { class: 'hero' }, h('div', { class: 'wrap' },
      h('h1', {}, 'Co je vyvěšeno na úředních deskách obcí'),
      h('p', {}, 'Oznámení obcí a městských částí, které zveřejňují úřední desku jako otevřená data, na jednom místě.'),
      h('form', { class: 'search', role: 'search', onsubmit: (e) => { e.preventDefault(); state.shown = PAGE; update(); } },
        h('div', { class: 'field' },
          h('label', { for: 'q' }, 'Hledat v oznámeních'),
          h('input', { id: 'q', type: 'search', value: f.q, placeholder: 'např. územní plán, dražba, pronájem pozemku', oninput: bindText('q') })),
        h('div', { class: 'field' },
          h('label', { for: 'obec' }, 'Obec nebo městská část'),
          h('input', { id: 'obec', type: 'text', value: f.obec, placeholder: 'např. Praha 7, Kolín, Brno-střed', oninput: bindText('obec') })),
        h('button', { type: 'submit', class: 'btn btn--primary' }, 'Hledat')),
      h('div', { class: 'chips' }, h('span', { class: 'chips__label' }, 'Časté:'), chipButtons),
    )),
    h('div', { class: 'wrap layout' },
      h('aside', { class: 'layout__side', 'aria-label': 'Filtry' }, h('details', { class: 'panel filters', open: matchMedia('(min-width: 861px)').matches },
        h('summary', {}, 'Filtry'),
        h('div', { class: 'filters__body' },
        h('fieldset', {}, h('legend', { class: 'legend' }, 'Vyvěšeno'),
          radio('1', 'Dnes'), radio('7', 'Posledních 7 dní'), radio('30', 'Posledních 30 dní'), radio('all', 'Kdykoli')),
        h('fieldset', {}, h('legend', { class: 'legend' }, 'Stav'),
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.akt, onchange: bindCheck('akt') }), 'Jen aktuálně vyvěšené'),
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.pril, onchange: bindCheck('pril') }), 'Jen s přílohami'),
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.soon, onchange: bindCheck('soon') }), `Končí do ${SOON_DAYS} dnů`)),
        h('a', { class: 'btn', href: '#/', onclick: (e) => { e.preventDefault(); history.replaceState(null, '', '#/'); route(); } }, 'Zrušit filtry'),
      ))),
      h('section', { class: 'layout__main', 'aria-label': 'Výsledky' },
        h('div', { class: 'results__head' }, resultsHead,
          h('div', { class: 'sort' }, h('label', { for: 'sort' }, 'Řadit'), sortSel)),
        list, more),
    ),
  );
  update();
}

// ---------- board ----------

function renderBoard(id, params) {
  setNav('obce');
  const b = state.boards.get(id);
  if (!b) return renderNotFound();
  const all = state.items.filter((it) => it.board === id);
  let tab = params.get('tab') === 'archiv' ? 'archiv' : 'aktualni';
  let q = '';

  const tbody = h('tbody');
  const tabs = [['aktualni', 'Aktuálně vyvěšené'], ['archiv', 'Archiv sejmutých']].map(([key, label]) =>
    h('button', { type: 'button', role: 'tab', class: 'tab', 'aria-selected': String(tab === key),
      onclick: () => {
        tab = key;
        for (const t of tabs) t.setAttribute('aria-selected', String(t.dataset.key === key));
        history.replaceState(null, '', key === 'archiv' ? `#/deska/${id}?tab=archiv` : `#/deska/${id}`);
        paint();
      }, 'data-key': key }, label));

  const paint = () => {
    const terms = fold(q).split(/\s+/).filter(Boolean);
    const rows = all
      .filter((it) => (tab === 'archiv' ? it.removed : !it.removed))
      .filter((it) => terms.every((t) => it._text.includes(t)));
    tbody.replaceChildren(...(rows.length ? rows.map((it) => {
      const fl = itemFlags(it);
      return h('tr', {},
        h('td', {}, h('a', { href: `#/oznameni/${it.id}` }, it.title),
          (it.cj || it.sz) && h('div', { class: 'sub' }, it.cj ? `č. j. ${it.cj}` : `sp. zn. ${it.sz}`)),
        h('td', { class: 'date' }, fmtDate(it.posted)),
        h('td', { class: `date${fl.soon ? ' soon' : ''}` }, it.until ? fmtDate(it.until) : 'neuvedeno'),
        h('td', {}, String(it.docs?.length || '—')));
    }) : [h('tr', {}, h('td', { colspan: '4', class: 'note' }, 'Žádné položky.'))]));
  };

  const original = safeUrl(b.access_url);
  mount(
    h('section', { class: 'pagehead' }, h('div', { class: 'wrap' },
      h('nav', { class: 'crumbs', 'aria-label': 'Drobečková navigace' }, h('a', { href: '#/obce' }, 'Obce'), h('span', { 'aria-hidden': 'true' }, '/'), h('span', {}, boardName(b))),
      h('div', { class: 'pagehead__row' },
        h('div', {}, h('h1', {}, boardName(b)), h('p', { class: 'note', style: 'margin:4px 0 0' }, b.title)),
        h('div', { class: 'pagehead__actions' },
          original && h('a', { class: 'btn btn--primary', href: original, target: '_blank', rel: 'noopener noreferrer' }, 'Data desky (JSON)'))),
      h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Zobrazení' }, tabs),
    )),
    h('div', { class: 'wrap layout' },
      h('section', { class: 'layout__main', 'aria-label': 'Oznámení' },
        h('div', { class: 'field' }, h('label', { for: 'bq', class: 'visually-hidden' }, 'Hledat na desce'),
          h('input', { id: 'bq', type: 'search', placeholder: `Hledat na desce: ${boardName(b)}`, oninput: (e) => { q = e.target.value; paint(); } })),
        h('div', { class: 'tablewrap' }, h('table', {},
          h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Oznámení'), h('th', { scope: 'col' }, 'Vyvěšeno'), h('th', { scope: 'col' }, 'Do'), h('th', { scope: 'col' }, 'Přílohy'))),
          tbody))),
      h('aside', { class: 'layout__side', 'aria-label': 'Zdroj dat' },
        h('div', { class: 'panel' }, h('h2', {}, 'Zdroj dat'),
          h('dl', { class: 'facts' },
            h('div', {}, h('dt', {}, 'Stav posledního sběru'), h('dd', {},
              b.ok ? h('span', { class: 'status status--ok' }, 'V pořádku') : h('span', { class: 'status status--err' }, b.error || 'Chyba'))),
            h('div', {}, h('dt', {}, 'Staženo'), h('dd', { class: 'mono' }, b.fetched_at ? new Date(b.fetched_at).toLocaleString('cs-CZ') : '–')),
            h('div', {}, h('dt', {}, 'Položek na desce'), h('dd', { class: 'mono' }, String(b.count ?? 0))),
            h('div', {}, h('dt', {}, 'Záznam v NKOD'), h('dd', {},
              h('a', { href: `https://data.gov.cz/datová-sada?iri=${encodeURIComponent(b.dataset_iri)}`, target: '_blank', rel: 'noopener noreferrer' }, 'Otevřít v katalogu'))))),
        h('div', { class: 'panel note' }, h('h2', {}, 'Právně závazná je originální deska'),
          'Tento přehled je pouze informativní. Lhůty a obsah vždy ověřte na úřední desce.'))),
  );
  paint();
}

// ---------- item detail ----------

function renderItem(id) {
  setNav('home');
  const it = state.itemById.get(id);
  if (!it) return renderNotFound();
  const b = state.boards.get(it.board);
  const fl = itemFlags(it);
  const url = safeUrl(it.url);
  const copy = h('button', { type: 'button', class: 'btn', onclick: async () => {
    try { await navigator.clipboard.writeText(location.href); copy.textContent = 'Odkaz zkopírován'; } catch { copy.textContent = 'Kopírování selhalo'; }
  } }, 'Kopírovat odkaz');
  const related = state.items.filter((x) => x.board === it.board && x.id !== it.id && !x.removed).slice(0, 4);

  mount(h('div', { class: 'wrap layout' },
    h('div', { style: 'flex-basis:100%' }, h('a', { class: 'back', href: `#/deska/${it.board}` }, `← Zpět na desku: ${boardName(b)}`)),
    h('article', { class: 'article layout__main' },
      h('div', { class: 'badges' },
        h('span', { class: `badge ${fl.active ? 'badge--new' : ''}` }, fl.active ? 'Aktuálně vyvěšeno' : 'Sejmuto'),
        fl.soon && h('span', { class: 'badge badge--soon' }, soonLabel(fl.left)),
        (it.agendas || []).map((a) => h('span', { class: 'badge' }, a))),
      h('h1', {}, it.title),
      h('p', { class: 'lead' }, boardName(b)),
      h('div', { class: 'factgrid' },
        h('div', {}, h('span', {}, 'Vyvěšeno'), h('span', { class: 'mono' }, fmtDate(it.posted))),
        h('div', {}, h('span', {}, 'Relevantní do'), h('span', { class: 'mono' }, it.until ? fmtDate(it.until) : 'neuvedeno')),
        it.cj && h('div', {}, h('span', {}, 'Číslo jednací'), h('span', { class: 'mono' }, it.cj)),
        it.sz && h('div', {}, h('span', {}, 'Spisová značka'), h('span', { class: 'mono' }, it.sz))),
      h('section', {},
        h('h2', { style: 'font-size:18px;margin:0 0 12px' }, 'Dokumenty'),
        it.docs?.length
          ? h('ul', { class: 'docs' }, it.docs.map((d) => {
            const u = safeUrl(d.url);
            return u && h('li', {}, h('a', { class: 'doc', href: u, target: '_blank', rel: 'noopener noreferrer' },
              h('span', { class: 'doc__ext' }, extOf(u)), h('span', { class: 'doc__name' }, d.name || u)));
          }))
          : h('p', { class: 'note' }, 'Bez příloh.')),
      h('div', { class: 'actions' },
        url && h('a', { class: 'btn btn--primary', href: url, target: '_blank', rel: 'noopener noreferrer' }, 'Zobrazit na originální desce ↗'),
        copy)),
    h('aside', { class: 'layout__side' },
      related.length > 0 && h('div', { class: 'panel' }, h('h2', {}, 'Další z této desky'),
        related.map((x) => h('a', { href: `#/oznameni/${x.id}`, style: 'color:var(--ink);text-decoration:none' }, x.title,
          h('div', { class: 'note' }, x.until ? `do ${fmtDate(x.until)}` : ''))),
        h('a', { href: `#/deska/${it.board}` }, 'Celá deska')),
      h('div', { class: 'panel note' }, 'Údaje převzaty z otevřených dat. Právně závazné je znění na originální úřední desce.')),
  ));
}

// ---------- boards list / coverage / about ----------

function renderBoards() {
  setNav('obce');
  const boards = [...state.boards.values()].sort((a, b) => boardName(a).localeCompare(boardName(b), 'cs'));
  const list = h('ul', { class: 'cards' });
  const paint = (q) => {
    const t = fold(q);
    list.replaceChildren(...boards.filter((b) => fold(`${boardName(b)} ${b.title}`).includes(t)).map((b) =>
      h('li', { class: 'card' },
        h('a', { class: 'card__title', href: `#/deska/${b.id}` }, boardName(b)),
        h('div', { class: 'meta' }, h('span', {}, b.title), h('span', {}, `${b.count ?? 0} položek`),
          !b.ok && h('span', { class: 'status status--err' }, 'chyba sběru')))));
  };
  mount(h('div', { class: 'wrap layout' }, h('section', { class: 'layout__main' },
    h('h1', { style: 'margin:0' }, 'Obce a úřady'),
    h('div', { class: 'field' }, h('label', { for: 'oq' }, 'Hledat obec nebo úřad'),
      h('input', { id: 'oq', type: 'search', oninput: (e) => paint(e.target.value) })),
    list)));
  paint('');
}

function renderCoverage() {
  setNav('pokryti');
  const m = state.meta || {};
  const failed = [...state.boards.values()].filter((b) => !b.ok).sort((a, b) => boardName(a).localeCompare(boardName(b), 'cs'));
  mount(h('div', { class: 'wrap layout' }, h('section', { class: 'layout__main' },
    h('h1', { style: 'margin:0' }, 'Pokrytí dat'),
    h('div', { class: 'factgrid' },
      h('div', {}, h('span', {}, 'Desek v NKOD'), h('span', { class: 'mono' }, String(m.boards_total ?? '–'))),
      h('div', {}, h('span', {}, 'Úspěšně staženo'), h('span', { class: 'mono' }, String(m.boards_ok ?? '–'))),
      h('div', {}, h('span', {}, 'Aktuálních oznámení'), h('span', { class: 'mono' }, String(m.items_active ?? '–'))),
      h('div', {}, h('span', {}, 'V archivu'), h('span', { class: 'mono' }, String(m.items_archived ?? '–')))),
    h('h2', { style: 'margin:8px 0 0;font-size:20px' }, `Desky s chybou (${failed.length})`),
    h('div', { class: 'tablewrap' }, h('table', {},
      h('thead', {}, h('tr', {}, h('th', {}, 'Deska'), h('th', {}, 'Chyba'), h('th', {}, 'URL'))),
      h('tbody', {}, failed.map((b) => h('tr', {},
        h('td', {}, h('a', { href: `#/deska/${b.id}` }, boardName(b))),
        h('td', {}, b.error || ''),
        h('td', { class: 'sub' }, b.access_url)))))))));
}

function renderAbout() {
  setNav('about');
  mount(h('div', { class: 'wrap prose' },
    h('h1', {}, 'O projektu'),
    h('p', {}, 'Obce a další úřady mají od 1. 2. 2022 povinnost zveřejňovat úřední desku jako otevřená data podle Otevřené formální normy Úřední desky a evidovat ji v Národním katalogu otevřených dat.'),
    h('p', {}, 'Tento web pravidelně (každou hodinu) stáhne všechny takto evidované desky, sjednotí je a zobrazí na jednom místě. Položky, které z desky zmizí, zůstávají v archivu.'),
    h('p', {}, 'Přehled je pouze informativní. Právně závazné je znění na originální úřední desce.')));
}

function renderNotFound() {
  mount(h('div', { class: 'wrap prose' }, h('h1', {}, 'Stránka nenalezena'), h('p', {}, h('a', { href: '#/' }, 'Zpět na přehled'))));
}

// ---------- bootstrap ----------

async function loadJson(name) {
  const res = await fetch(`${DATA}/${name}`, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
  return res.json();
}

async function init() {
  try {
    const [boards, items, meta] = await Promise.all([loadJson('boards.json'), loadJson('items.json'), loadJson('meta.json')]);
    for (const b of boards) state.boards.set(b.id, b);
    for (const it of items) {
      const b = state.boards.get(it.board);
      it._text = fold(`${it.title} ${it.cj || ''} ${it.sz || ''} ${(it.agendas || []).join(' ')}`);
      it._board = fold(`${boardName(b)} ${b?.title || ''}`);
      state.itemById.set(it.id, it);
    }
    state.items = items;
    state.meta = meta;
    document.getElementById('generated').textContent = new Date(meta.generated_at).toLocaleString('cs-CZ');
  } catch (err) {
    document.getElementById('app').replaceChildren(h('div', { class: 'wrap prose' },
      h('h1', {}, 'Data se nepodařilo načíst'),
      h('p', {}, String(err)),
      h('p', { class: 'note' }, 'Pro lokální vývoj spusťte: make sample && make serve')));
    return;
  }
  window.addEventListener('hashchange', route);
  route();
}

init();
