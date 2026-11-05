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

const state = { data: null, filter: { obec: '', tema: '', q: '' }, limit: STEP, listScroll: null };

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
function when(it) {
  const s = it.s || {};
  if (it.r) return null;
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
  if (w.type === 'deadline') return `poslední den: ${deadlineWord(s)} · ${it.o}`;
  if (w.type === 'running') return `probíhá do ${dm(w.until)} · ${it.o}`;
  return [s.time, it.o, w.until && w.until !== w.date ? `až ${dm(w.until)}` : null].filter(Boolean).join(' · ');
}

function badges(it) {
  const s = it.s || {};
  return [
    s.importance === 'vysoka' ? h('span', { class: 'badge badge--hot', title: s.importance_why || null }, 'důležité') : null,
    isIso(it.p) && daysTo(it.p) >= -NEW_DAYS && !it.r ? h('span', { class: 'badge' }, 'nové') : null,
    (isIso(s.updated) && daysTo(s.updated) >= -7) || s.pending
      ? h('span', { class: 'badge badge--hot', title: 'Úřad oznámení upravil (příloha nebo termín)' }, 'změna') : null,
  ];
}

const detailHref = (it) => `#/o/${encodeURIComponent(it.id)}`;

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
  if (parts[0] === 'zpravodaje') {
    onList = false;
    renderNewsletters();
    window.scrollTo(0, 0);
    return;
  }
  state.filter.obec = params.get('obec') || '';
  state.filter.tema = params.get('tema') || '';
  state.filter.q = params.get('q') || '';
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
  const { obec, tema, q } = state.filter;
  const terms = fold(q).split(/\s+/).filter(Boolean).map(stem);
  return state.data.items.filter((it) => {
    if (obec && it.o !== obec) return false;
    if (tema && it.s?.cat !== tema) return false;
    if (!terms.length) return true;
    if (!it._q) it._q = searchText(it);
    return terms.every((t) => it._q.includes(t));
  });
}

function entry(it, w) {
  const s = it.s || {};
  return h('a', { class: `entry entry--${w.kind}`, href: detailHref(it) },
    h('span', { class: `entry__meta${w.type === 'deadline' ? ' entry__meta--warn' : ''}` }, entryMeta(it, w), ` · ${label(it)}`, badges(it)),
    h('h3', { class: 'entry__title' }, s.tldr || it.t),
    s.action ? h('span', { class: 'entry__sub' }, s.action) : null);
}

function row(it, dateIso, dateNote, datePrefix) {
  const s = it.s || {};
  return h('li', {}, h('a', { class: 'row', href: detailHref(it) },
    h('span', { class: 'row__date' }, datePrefix ? h('span', { class: 'row__pre' }, datePrefix) : null, isIso(dateIso) ? dm(dateIso) : '–'),
    h('span', { class: 'row__body' },
      h('span', { class: 'row__title' }, s.tldr || it.t),
      h('span', { class: 'row__meta' }, [dateNote, it.o, label(it)].filter(Boolean).join(' · '), badges(it)))));
}

function weekStrip(groups, onPick) {
  return h('div', { class: 'week', role: 'list', 'aria-label': 'Příštích 7 dní' },
    Array.from({ length: 7 }, (_, i) => {
      const iso = addDays(TODAY, i);
      const items = groups.get(iso) || [];
      const kinds = new Set(items.map((x) => x.w.kind));
      const cls = ['day-btn', i === 0 ? 'day-btn--today' : '', items.length ? 'day-btn--has' : '',
        kinds.has('warn') ? 'day-btn--warn' : kinds.has('event') ? 'day-btn--event' : ''].filter(Boolean).join(' ');
      const name = `${i === 0 ? 'dnes, ' : ''}${weekdayLong(iso)} ${dateCz(iso)}`;
      return h('div', { role: 'listitem' }, h('button', {
        type: 'button', class: cls, disabled: !items.length,
        'aria-label': items.length ? `${name}: ${items.length} ${plural(items.length, 'termín', 'termíny', 'termínů')}` : `${name}: nic`,
        onclick: () => onPick(iso),
      },
      h('span', { class: 'day-btn__wd' }, i === 0 ? 'dnes' : weekday(iso)),
      h('span', { class: 'day-btn__d' }, String(+iso.slice(8, 10))),
      h('span', { class: 'day-btn__full' }, `${weekday(iso)} ${dateCz(iso)}`),
      h('span', { class: 'day-btn__rel' }, i < 2 ? relDay(iso) : ''),
      h('span', { class: 'day-btn__count' }, items.length ? `${items.length} ${plural(items.length, 'termín', 'termíny', 'termínů')}` : 'nic'),
      h('span', { class: 'dots', 'aria-hidden': 'true' }, ['warn', 'event', 'start'].filter((k) => kinds.has(k)).map((k) => h('span', { class: `dot dot--${k}` })))));
    }));
}

function renderHome() {
  const d = state.data;
  const updated = new Date(d.generated_at);
  const connected = d.members.filter((m) => m.connected);
  const counts = new Map();
  for (const it of d.items) if (!it.r) counts.set(it.o, (counts.get(it.o) || 0) + 1);

  const weekBox = h('div');
  const body = h('div', { class: 'timeline' });
  const status = h('p', { class: 'status', 'aria-live': 'polite' });

  const paint = () => {
    const items = filtered();
    const horizonEnd = addDays(TODAY, HORIZON);
    const groups = new Map();
    const later = [];
    const undated = [];
    const running = [];
    for (const it of items) {
      if (it.r) continue;
      const w = when(it);
      if (!w) undated.push(it);
      else if (w.type === 'running') running.push({ it, w }); // už platí (uzavírka): zvlášť, ne každý den „dnes“
      else if (w.date <= horizonEnd) {
        if (!groups.has(w.date)) groups.set(w.date, []);
        groups.get(w.date).push({ it, w });
      } else later.push({ it, w });
    }
    const order = { warn: 0, event: 1, start: 2 };
    for (const list of groups.values()) {
      list.sort((a, b) => order[a.w.kind] - order[b.w.kind] || (a.it.s?.time || '').localeCompare(b.it.s?.time || ''));
    }
    later.sort((a, b) => a.w.date.localeCompare(b.w.date));
    running.sort((a, b) => a.w.until.localeCompare(b.w.until));
    undated.sort((a, b) => (b.p || '').localeCompare(a.p || '') || (b.fs || '').localeCompare(a.fs || ''));
    const removed = items.filter((it) => it.r).sort((a, b) => (b.r || '').localeCompare(a.r || ''));

    weekBox.replaceChildren(weekStrip(groups, (iso) => document.getElementById(`den-${iso}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })));
    const nDated = [...groups.values()].reduce((n, l) => n + l.length, 0);
    status.textContent = items.length
      ? [`${nDated} ${plural(nDated, 'termín', 'termíny', 'termínů')} v příštích ${HORIZON} dnech`,
        running.length ? `${running.length} právě platí` : null,
        `${undated.length} bez termínu`].filter(Boolean).join(' · ')
      : '';

    const dayNodes = [...groups.keys()].sort().map((iso) => {
      const n = daysTo(iso);
      return h('section', { class: 'daygroup', id: `den-${iso}`, 'aria-label': `${weekdayLong(iso)} ${dateCz(iso)}` },
        h('div', { class: 'daygroup__date' },
          h('span', { class: 'daygroup__wd' }, weekday(iso)),
          h('span', { class: 'daygroup__d' }, dm(iso)),
          h('span', { class: `daygroup__rel${n <= 2 ? ' daygroup__rel--soon' : ''}` }, relDay(iso))),
        h('div', { class: 'daygroup__items' }, groups.get(iso).map(({ it, w }) => entry(it, w))));
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

    const filteredOut = state.filter.q || state.filter.obec || state.filter.tema;
    const nothing = !items.length
      ? h('p', { class: 'empty' }, filteredOut ? 'Nic nenalezeno. Zkus jiné slovo nebo všechny obce.' : 'Na deskách teď nic nevisí.')
      : !dayNodes.length ? h('p', { class: 'empty' }, `V příštích ${HORIZON} dnech žádné lhůty ani termíny.`) : null;

    body.replaceChildren(...[
      running.length ? h('h2', { class: 'section-title section-title--first' }, 'PRÁVĚ PLATÍ') : null,
      running.length ? h('ul', { class: 'rows' }, running.map(({ it, w }) => row(it, w.until, `platí do ${dateCz(w.until)}`, 'do'))) : null,
      running.length && dayNodes.length ? h('h2', { class: 'section-title' }, `PŘÍŠTÍCH ${HORIZON} DNÍ`) : null,
      ...dayNodes,
      nothing,
      later.length ? h('h2', { class: 'section-title' }, 'POZDĚJI') : null,
      later.length ? h('ul', { class: 'rows' }, later.map(({ it, w }) => row(it, w.date, w.type === 'deadline' ? `${deadlineWord(it.s || {})} do ${dateCz(w.date)}` : null))) : null,
      undated.length ? h('h2', { class: 'section-title' }, 'BEZ TERMÍNU · NEJNOVĚJŠÍ NAHOŘE') : null,
      undated.length ? undatedList : null,
      undated.length ? more : null,
      removed.length ? h('details', { class: 'removed' },
        h('summary', {}, `SEJMUTO ZA POSLEDNÍCH 30 DNÍ (${removed.length})`),
        h('ul', { class: 'rows' }, removed.map((it) => row(it, (it.r || '').slice(0, 10), 'sejmuto')))) : null,
      villages(d),
    ].filter(Boolean));
  };

  const chips = h('div', { class: 'places', role: 'group', 'aria-label': 'Obec' });
  const total = d.items.filter((it) => !it.r).length;
  const names = ['', ...connected.map((m) => m.name)
    .sort((a, b) => (counts.get(b) || 0) - (counts.get(a) || 0) || a.localeCompare(b, 'cs'))];
  const paintChips = () => chips.replaceChildren(...names.map((name) => h('button', {
    type: 'button', class: 'place', 'aria-pressed': String(state.filter.obec === name),
    onclick: () => { state.filter.obec = state.filter.obec === name ? '' : name; state.limit = STEP; writeFilter(); paintChips(); paint(); },
  }, h('span', { class: 'place__name' }, name || 'Všechny obce'), h('span', { class: 'place__n' }, String(name ? counts.get(name) || 0 : total)))));
  paintChips();

  // Témata (typy oznámení) – počty u aktivních oznámení, nejčastější nahoře
  const labels = d.categories || {};
  const catCounts = new Map();
  for (const it of d.items) if (!it.r && it.s?.cat) catCounts.set(it.s.cat, (catCounts.get(it.s.cat) || 0) + 1);
  const topics = h('div', { class: 'places', role: 'group', 'aria-label': 'Téma' });
  const topicIds = ['', ...[...catCounts.keys()].sort((a, b) => catCounts.get(b) - catCounts.get(a) || (labels[a] || a).localeCompare(labels[b] || b, 'cs'))];
  const paintTopics = () => topics.replaceChildren(...topicIds.map((id) => h('button', {
    type: 'button', class: 'place', 'aria-pressed': String(state.filter.tema === id),
    onclick: () => { state.filter.tema = state.filter.tema === id ? '' : id; state.limit = STEP; writeFilter(); paintTopics(); paint(); },
  }, h('span', { class: 'place__name' }, id ? (labels[id] || id) : 'Všechna témata'),
  h('span', { class: 'place__n' }, String(id ? catCounts.get(id) : total)))));
  paintTopics();

  let timer;
  const search = h('input', {
    type: 'search', id: 'q', class: 'search', value: state.filter.q, placeholder: 'např. Pražská, 208/1, kanalizace', autocomplete: 'off',
    oninput: (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.filter.q = e.target.value; state.limit = STEP; writeFilter(); paint(); }, 160);
    },
  });

  const stamp = Number.isNaN(updated.getTime()) ? ''
    : `aktualizováno ${updated.toLocaleDateString('cs-CZ')} ${updated.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' })}`;
  mount('',
    h('div', { class: 'page' },
      siteNav('desky'),
      h('header', { class: 'masthead' },
        h('h1', {}, 'Co se kdy děje'),
        h('span', { class: 'masthead__meta' }, [d.region?.name || 'Dolnobřežansko', stamp].filter(Boolean).join(' · ')),
        d.sample ? h('div', { class: 'sample' }, 'Ukázková data – nejde o skutečná oznámení.') : null),
      h('div', { class: 'layout' },
        h('aside', { class: 'panel', 'aria-label': 'Výběr' },
          h('div', { class: 'panel__block' },
            h('h2', { class: 'panel__title' }, 'NEJBLIŽŠÍCH 7 DNÍ'),
            weekBox,
            h('div', { class: 'legend', 'aria-hidden': 'true' },
              h('span', {}, h('span', { class: 'dot dot--warn' }), 'lhůta / omezení'),
              h('span', {}, h('span', { class: 'dot dot--event' }), 'jednání / akce'))),
          h('div', { class: 'panel__block' },
            h('label', { class: 'panel__title', for: 'q' }, 'HLEDAT'),
            search,
            h('p', { class: 'hint' }, 'Hledá v názvu, shrnutí, místě, č. j. i v popisu příloh. Bez ohledu na diakritiku; více slov = musí sedět všechna.')),
          h('div', { class: 'panel__block' },
            h('h2', { class: 'panel__title' }, 'OBEC'),
            chips),
          h('div', { class: 'panel__block' },
            h('h2', { class: 'panel__title' }, 'TÉMA'),
            topics),
          status),
        h('div', { class: 'main' }, body))));
  paint();
}

function villages(d) {
  return h('section', { class: 'villages', 'aria-labelledby': 'h-vil' },
    h('h2', { id: 'h-vil' }, 'KTERÉ DESKY ČTEME'),
    h('ul', {}, d.members.map((m) => {
      const url = safeUrl(m.board_url) || safeUrl(m.web);
      const mark = m.connected
        ? (m.ok ? h('span', { class: 'ok' }, '✓') : h('span', { class: 'miss', title: 'Poslední stažení selhalo' }, '!'))
        : h('span', { class: 'miss' }, '–');
      return h('li', { title: m.connected ? null : (m.status || 'deska zatím nenalezena') }, mark,
        url ? h('a', { href: url, rel: 'noopener noreferrer' }, m.name) : h('span', {}, m.name));
    })),
    h('p', {}, '✓ deska připojena · – zatím nečteme. ',
      d.ai_model ? 'Shrnutí píše AI (Claude) z textu a příloh a může se splést. ' : 'Shrnutí vznikají automaticky z textu oznámení a příloh. ',
      'Právně platí znění na úřední desce obce.'));
}

// ---------- navigace a zpravodaje ----------

/** Hlavní přepínač nahoře: úřední desky (výchozí) a zpravodaje obcí. */
function siteNav(active) {
  const tab = (key, href, label) => h('a', { class: 'sitenav__tab', href, 'aria-current': active === key ? 'page' : null }, label);
  return h('nav', { class: 'sitenav', 'aria-label': 'Sekce' },
    tab('desky', '#/', 'Úřední desky'),
    tab('zpravodaje', '#/zpravodaje', 'Zpravodaje'));
}

/** Krátký popisek staršího čísla do seznamu („č. 95“); celý název je v title odkazu. */
function issueLabel(i) {
  const m = /(?:č\.|číslo\.?)\s*(\d{1,4})/i.exec(i.t);
  if (!m) return i.t;
  const series = /rozkvět/i.test(i.t) ? 'Rozkvět ' : '';
  return `${series}č. ${m[1]}`;
}

function renderNewsletters() {
  const all = state.data.newsletters || [];
  const withNews = all.filter((n) => n.name && safeUrl(n.archive));
  const missing = all.filter((n) => !n.name).map((n) => n.obec);
  const card = (n) => {
    const issues = (n.issues || []).filter((i) => safeUrl(i.url));
    const [latest, ...older] = issues;
    const years = [];
    for (const i of older) {
      const y = i.y || (years.length ? years[years.length - 1].y : 'Bez roku'); // číslo bez roku k sousednímu ročníku
      if (!years.length || years[years.length - 1].y !== y) years.push({ y, items: [] });
      years[years.length - 1].items.push(i);
    }
    const ext = { href: safeUrl(n.archive), rel: 'noopener noreferrer', target: '_blank' };
    return h('article', { class: 'news' },
      h('p', { class: 'news__obec' }, n.obec),
      h('h2', { class: 'news__name' }, n.name),
      latest
        ? h('a', { class: 'news__latest', href: safeUrl(latest.url), rel: 'noopener noreferrer', target: '_blank' },
          icon(ICON_DOC, 20), h('span', {}, h('span', { class: 'news__label' }, 'Nejnovější číslo'), latest.t))
        : h('p', { class: 'news__empty' }, 'Seznam čísel se teď nepodařilo načíst – čísla najdeš v archivu na webu obce.'),
      years.length ? h('details', { class: 'news__older' },
        h('summary', {}, `Starší čísla (${older.length})`),
        years.map((g) => h('div', { class: 'news__year' },
          years.length > 1 || g.y !== 'Bez roku' ? h('h3', {}, String(g.y)) : null,
          h('ul', {}, g.items.map((i) => h('li', {},
            h('a', { href: safeUrl(i.url), rel: 'noopener noreferrer', target: '_blank', title: i.t }, issueLabel(i)))))))) : null,
      h('a', { class: 'news__archive', ...ext }, 'Archiv na webu obce ↗'));
  };
  mount('Zpravodaje',
    h('div', { class: 'page' },
      siteNav('zpravodaje'),
      h('header', { class: 'masthead' },
        h('h1', {}, 'Zpravodaje obcí'),
        h('span', { class: 'masthead__meta' }, 'Odkazy na čísla přímo na webech obcí')),
      h('p', { class: 'news__intro' }, 'Zpravodaje nezkracujeme – mají desítky stran a každého zajímá něco jiného. Otevři si číslo a listuj.'),
      withNews.length
        ? h('div', { class: 'news-grid' }, withNews.map(card))
        : h('p', { class: 'muted' }, 'Zatím tu nejsou žádné zpravodaje.'),
      missing.length ? h('p', { class: 'news__missing' }, `Zpravodaj na webu jsme zatím nenašli: ${missing.join(', ')}.`) : null));
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
  const header = h('header', { class: 'top top--detail' }, h('div', { class: 'col' }, back));
  const it = state.data.items.find((x) => x.id === id);
  if (!it) {
    mount('Nenalezeno', header, h('div', { class: 'col detail' }, h('p', {}, 'Oznámení už na desce není nebo odkaz neplatí.')));
    return;
  }
  const s = it.s || {};
  const w = when(it);
  const docs = (it.docs || []).map((x) => ({ ...x, url: safeUrl(x.url) })).filter((x) => x.url);
  const original = safeUrl(it.url);
  const bullets = Array.isArray(s.bullets) ? s.bullets : [];
  const whenText = !w ? null
    : w.type === 'running' ? `probíhá do ${dateCz(w.until, { wd: true })}`
      : `${dateCz(w.date, { wd: true })}${s.time && w.type === 'event' ? ` v ${s.time}` : ''} · ${relDay(w.date)}`;

  mount(s.tldr || it.t, header,
    h('article', { class: 'col detail' },
      h('div', {},
        h('div', { class: 'detail__meta' }, `${(it.o || '').toUpperCase()} · ${label(it)} · vyvěšeno ${dateCz(it.p, { year: true })}`, badges(it)),
        h('h1', {}, s.tldr || it.t)),
      s.action
        ? h('section', { class: w ? 'todo' : 'todo todo--calm', 'aria-label': 'Co můžeš udělat' },
          h('p', { class: 'todo__main' }, s.action),
          whenText ? h('p', { class: 'todo__when' }, whenText) : null)
        : h('section', { class: 'todo todo--calm', 'aria-label': 'Co můžeš udělat' },
          h('p', {}, whenText || 'Jen pro informaci, nic dělat nemusíš.')),
      s.impact || s.affects ? h('section', { class: 'meaning', 'aria-labelledby': 'h-mean' },
        h('h2', { id: 'h-mean', class: 'h2' }, 'CO TO ZNAMENÁ'),
        s.impact ? h('p', { class: 'meaning__lead' }, s.impact) : null,
        s.affects ? h('p', { class: 'muted' }, h('strong', {}, 'Týká se: '), s.affects) : null,
        s.importance === 'vysoka' && s.importance_why ? h('p', { class: 'muted' }, h('strong', {}, 'Proč je to důležité: '), s.importance_why) : null) : null,
      bullets.length ? section('h-gist', s.src === 'claude' ? 'CO PŘESNĚ' : 'VE ZKRATCE',
        h('ul', { class: 'bullets' }, bullets.slice(0, 5).map((b) => h('li', {}, b))),
        bullets.length > 5 ? h('details', { class: 'more-details' },
          h('summary', {}, `Další podrobnosti (${bullets.length - 5})`),
          h('ul', { class: 'bullets' }, bullets.slice(5).map((b) => h('li', {}, b)))) : null) : null,
      s.facts?.length ? section('h-facts', 'KLÍČOVÉ ÚDAJE',
        h('dl', { class: 'facts' }, s.facts.map((f) => [h('dt', {}, f.label), h('dd', {}, f.value)]))) : null,
      s.dates?.length ? section('h-dates', 'DŮLEŽITÁ DATA',
        h('ul', { class: 'dates' }, s.dates.filter((x) => isIso(x.date)).map((x) => h('li', { class: daysTo(x.date) < 0 ? 'past' : null },
          h('span', { class: 'mono' }, dateCz(x.date, { wd: true })), h('span', {}, x.what))))) : null,
      s.where ? section('h-where', 'KDE', h('p', { style: 'margin:0' }, s.where)) : null,
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
          : 'Shrnutí vzniklo automaticky z textu oznámení. Právně platí znění na úřední desce.'))));
}

// ---------- start ----------

async function start() {
  try {
    const res = await fetch(DATA, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!Array.isArray(data.items) || !Array.isArray(data.members)) throw new Error('neplatný formát dat');
    state.data = data;
  } catch (err) {
    mount('Chyba', h('div', { class: 'col pad' }, h('h1', {}, 'Data se nepodařilo načíst'), h('p', {}, String(err.message || err))));
    return;
  }
  window.addEventListener('hashchange', route);
  route();
}

start();
