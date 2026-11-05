// Úřední deska Dolnobřežansko – statický frontend bez buildu.
// Data: data/region.json (generuje `python -m uredni_desky region`).

const DATA = './data/region.json';
const NOW_DAYS = 7; // „Teď řešit“: lhůta nebo akce do 7 dní
const NEW_DAYS = 7;
const STEP = 20;

const TONE = {
  stavba: 'green', uzemni: 'green', zastupitelstvo: 'purple', rozpocet: 'purple', vyhlasky: 'purple', volby: 'purple',
  doprava: 'yellow', energie: 'yellow', voda: 'blue', majetek: 'blue', dane: 'blue', priroda: 'green',
  drazby: 'red', pisemnost: 'gray', ostatni: 'gray',
};

// Ikony (stroke SVG, 24×24) podle tématu.
const ICON = {
  stavba: 'M3 21h18M5 21V10l7-5 7 5v11M9 21v-6h6v6',
  uzemni: 'M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15',
  zastupitelstvo: 'M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM17 11.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM3 20c0-3 3-5 6-5s6 2 6 5M15 15c3 0 6 1.5 6 4',
  doprava: 'M4 20L9 4M20 20L15 4M12 6v2M12 11v2M12 16v2',
  majetek: 'M4 20V9l8-5 8 5v11zM9 20v-5h6v5',
  rozpocet: 'M4 19h16M6 16V9M10 16V6M14 16v-4M18 16V8',
  vyhlasky: 'M6 2h9l5 5v15H6zM14 2v6h6M9 13h8M9 17h6',
  volby: 'M4 12l4 4 8-9M3 21h18',
  voda: 'M12 3s6 7 6 11a6 6 0 0 1-12 0c0-4 6-11 6-11z',
  priroda: 'M12 22V12M12 12c-4 0-7-3-7-7 4 0 7 3 7 7zM12 12c4 0 7-3 7-7-4 0-7 3-7 7z',
  energie: 'M13 2L4 14h7l-1 8 9-12h-7z',
  drazby: 'M14 4l6 6M11 7l6 6M4 20l8-8M15 3l6 6-3 3-6-6z',
  dane: 'M5 4h14v16H5zM9 8h6M9 12h6M9 16h3',
  pisemnost: 'M3 6h18v12H3zM3 6l9 7 9-7',
  ostatni: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v6M12 7.5v.5',
};
const DOC_ICON = {
  text: 'M6 2h9l5 5v15H6zM14 2v6h6M9 13h8M9 17h6',
  mapa: 'M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15',
  sken: 'M3 4h18v16H3zM7 9h10M7 13h6',
  obrazek: 'M3 4h18v16H3zM3 16l5-5 4 4 3-3 6 6',
  jine: 'M6 2h9l5 5v15H6zM14 2v6h6',
};
const CLOCK = 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2';
const CAL = 'M3 5h18v16H3zM3 10h18M8 3v4M16 3v4';
const BACK = 'M15 5l-7 7 7 7';

const state = { data: null, filter: { obec: '', q: '' }, limit: STEP };

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
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

function icon(path, size = 14, stroke = 'currentColor') {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  for (const [k, v] of Object.entries({ width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke, 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(k, v);
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', path);
  svg.append(p);
  return svg;
}

const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);
const fold = (s) => (s || '').normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
const todayIso = () => new Date().toLocaleDateString('sv-SE');
const daysTo = (iso) => Math.round((Date.parse(iso) - Date.parse(todayIso())) / 86400000);
const plural = (n, one, few, many) => (n === 1 ? one : n >= 2 && n <= 4 ? few : many);
const WEEKDAY = ['ne', 'po', 'út', 'st', 'čt', 'pá', 'so'];

function dateCz(iso, { year = false, weekday = false } = {}) {
  if (!iso) return '';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  const wd = weekday ? `${WEEKDAY[new Date(y, m - 1, d).getDay()]} ` : '';
  return `${wd}${d}. ${m}.${year || y !== new Date().getFullYear() ? ` ${y}` : ''}`;
}

function inDays(iso) {
  const n = daysTo(iso);
  if (n === 0) return 'dnes';
  if (n === 1) return 'zítra';
  if (n > 1) return `za ${n} ${plural(n, 'den', 'dny', 'dní')}`;
  return null;
}

/** Nejbližší termín, kvůli kterému je oznámení „teď řešit“: {kind, date, text}. */
function urgency(it) {
  const s = it.s || {};
  if (it.r) return null;
  const cands = [];
  if (s.deadline && daysTo(s.deadline) >= 0) cands.push({ kind: 'deadline', date: s.deadline });
  if (s.event && daysTo(s.event) >= 0) cands.push({ kind: 'event', date: s.event });
  else if (s.event_end && daysTo(s.event) < 0 && daysTo(s.event_end) >= 0) cands.push({ kind: 'running', date: s.event_end });
  cands.sort((a, b) => a.date.localeCompare(b.date));
  const c = cands[0];
  if (!c || daysTo(c.date) > NOW_DAYS) return null;
  return c;
}

function dueText(it, u) {
  const s = it.s;
  if (u.kind === 'deadline') {
    const what = /námitk/i.test(s.action || '') ? 'Námitky' : /připomín/i.test(s.action || '') ? 'Připomínky' : /nabídk/i.test(s.action || '') ? 'Nabídky' : 'Lhůta';
    return [CLOCK, `${what} do ${dateCz(u.date, { weekday: true })} · ${inDays(u.date)}`];
  }
  if (u.kind === 'running') return [CAL, `Platí ještě do ${dateCz(u.date, { weekday: true })}`];
  const end = s.event_end ? ` – ${dateCz(s.event_end)}` : '';
  return [CAL, `${dateCz(u.date, { weekday: true })}${s.time ? ` v ${s.time}` : ''}${end} · ${inDays(u.date)}`];
}

const DOC_KIND = { text: 'Text', mapa: 'Mapa', sken: 'Sken', obrazek: 'Obrázek', jine: 'Soubor' };

function docChip(d) {
  const kind = DOC_KIND[d.k] || (d.x ? d.x.toUpperCase() : 'Příloha');
  const pages = d.pg ? ` · ${d.pg} str.` : '';
  return `${kind}${pages}`;
}

function tag(it) {
  const s = it.s || {};
  return h('span', { class: `tag tag--${TONE[s.cat] || 'gray'}` }, icon(ICON[s.cat] || ICON.ostatni), s.label || 'Ostatní');
}

// ---------- směrování ----------

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query = ''] = raw.split('?');
  return { parts: path.split('/').filter(Boolean).map(decodeURIComponent), params: new URLSearchParams(query) };
}

function mount(title, ...nodes) {
  document.title = title ? `${title} – Úřední deska Dolnobřežansko` : 'Úřední deska Dolnobřežansko';
  document.getElementById('app').replaceChildren(...nodes.flat(Infinity).filter(Boolean));
}

function route() {
  const { parts, params } = parseHash();
  if (parts[0] === 'o' && parts[1]) return renderDetail(parts[1]);
  state.filter.obec = params.get('obec') || '';
  state.filter.q = params.get('q') || '';
  return renderHome();
}

// ---------- přehled ----------

function card(it, urgent) {
  const s = it.s || {};
  const u = urgent ? urgency(it) : null;
  const files = (it.docs || []).slice(0, 3);
  const line = !urgent && [
    s.event && daysTo(s.event) >= -1 ? `${dateCz(s.event, { weekday: true })}${s.time ? ` v ${s.time}` : ''}${s.event_end ? ` – ${dateCz(s.event_end)}` : ''}` : null,
    s.deadline && daysTo(s.deadline) >= 0 ? `lhůta do ${dateCz(s.deadline)}` : null,
    s.where,
    (it.docs || []).some((d) => d.k === 'mapa') ? 'mapa v příloze' : null,
  ].filter(Boolean).join(' · ');
  return h('a', { class: `card${urgent ? ' card--urgent' : ''}`, href: `#/o/${encodeURIComponent(it.id)}` },
    h('div', { class: 'card__meta' }, tag(it), h('span', {}, `${it.o} · vyvěšeno ${dateCz(it.p)}`)),
    h('h3', {}, s.tldr || it.t),
    u ? h('div', { class: 'due' }, icon(dueText(it, u)[0], 18), dueText(it, u)[1]) : null,
    line ? h('div', { class: 'card__line' }, line) : null,
    urgent && files.length ? h('div', { class: 'files' }, files.map((d) => h('span', {}, docChip(d)))) : null);
}

function filtered() {
  const { obec, q } = state.filter;
  const terms = fold(q).split(/\s+/).filter(Boolean);
  return state.data.items.filter((it) => {
    if (obec && it.o !== obec) return false;
    if (!terms.length) return true;
    const hay = it._q || (it._q = fold(`${it.t} ${it.s?.tldr || ''} ${it.s?.where || ''} ${it.cj || ''} ${it.o} ${it.s?.label || ''}`));
    return terms.every((t) => hay.includes(t));
  });
}

function writeFilter() {
  const p = new URLSearchParams();
  if (state.filter.obec) p.set('obec', state.filter.obec);
  if (state.filter.q) p.set('q', state.filter.q);
  const qs = p.toString();
  history.replaceState(null, '', qs ? `#/?${qs}` : '#/');
}

function renderHome() {
  const d = state.data;
  const updated = new Date(d.generated_at);
  const connected = d.members.filter((m) => m.connected);
  const counts = new Map();
  for (const it of d.items) if (!it.r) counts.set(it.o, (counts.get(it.o) || 0) + 1);

  const list = h('div', { class: 'content col' });
  const paint = () => {
    const items = filtered();
    const active = items.filter((it) => !it.r);
    const now = active.filter((it) => urgency(it))
      .sort((a, b) => urgency(a).date.localeCompare(urgency(b).date));
    const nowIds = new Set(now.map((it) => it.id));
    const since = new Date(Date.parse(todayIso()) - NEW_DAYS * 86400000).toISOString().slice(0, 10);
    const fresh = active.filter((it) => !nowIds.has(it.id) && (it.p || '') >= since);
    const freshIds = new Set(fresh.map((it) => it.id));
    const older = active.filter((it) => !nowIds.has(it.id) && !freshIds.has(it.id));
    const removed = items.filter((it) => it.r);

    const more = h('button', { type: 'button', class: 'btn' });
    const olderBox = h('div', { class: 'section' });
    const paintOlder = () => {
      olderBox.replaceChildren(...older.slice(0, state.limit).map((it) => card(it, false)));
      more.hidden = older.length <= state.limit;
      more.textContent = `Ukázat další (${older.length - state.limit})`;
    };
    more.addEventListener('click', () => { state.limit += STEP; paintOlder(); });
    paintOlder();

    list.replaceChildren(...[
      h('section', { class: 'section', 'aria-labelledby': 'h-now' },
        h('div', { class: 'section__head' }, h('h2', { id: 'h-now' }, 'Teď řešit'), h('span', {}, `lhůta nebo termín do ${NOW_DAYS} dní`)),
        now.length ? now.map((it) => card(it, true)) : h('p', { class: 'empty' }, 'Nic naléhavého. Klid.')),
      h('section', { class: 'section', 'aria-labelledby': 'h-new' },
        h('div', { class: 'section__head' }, h('h2', { id: 'h-new' }, 'Nové tento týden'), h('span', {}, `${fresh.length} ${plural(fresh.length, 'oznámení', 'oznámení', 'oznámení')}`)),
        fresh.length ? fresh.map((it) => card(it, false)) : h('p', { class: 'empty' }, 'Za posledních 7 dní nic nového.')),
      older.length ? h('section', { class: 'section', 'aria-labelledby': 'h-old' },
        h('div', { class: 'section__head' }, h('h2', { id: 'h-old' }, 'Dál visí na deskách'), h('span', {}, `${older.length}`)),
        olderBox, more) : null,
      removed.length ? h('details', { class: 'section' },
        h('summary', {}, `Sejmuté za posledních 30 dní (${removed.length})`),
        removed.map((it) => card(it, false))) : null,
      villages(d)].filter(Boolean));
  };

  const chips = h('nav', { class: 'chips', 'aria-label': 'Obce' });
  const paintChips = () => chips.replaceChildren(...['', ...connected.map((m) => m.name)]
    .sort((a, b) => (a === '' ? -1 : b === '' ? 1 : (counts.get(b) || 0) - (counts.get(a) || 0)))
    .map((name) => h('button', {
      type: 'button', class: 'chip', 'aria-pressed': String(state.filter.obec === name),
      onclick: () => { state.filter.obec = name; state.limit = STEP; writeFilter(); paintChips(); paint(); },
    }, name || 'Vše', name && counts.get(name) ? h('span', { class: 'vh' }, ` (${counts.get(name)})`) : null)));
  paintChips();

  let timer;
  const search = h('input', {
    type: 'search', id: 'q', value: state.filter.q, placeholder: 'Hledat (ulice, parcela, téma…)',
    oninput: (e) => { clearTimeout(timer); timer = setTimeout(() => { state.filter.q = e.target.value; state.limit = STEP; writeFilter(); paint(); }, 150); },
  });

  mount('',
    h('header', { class: 'top' }, h('div', { class: 'top__inner col' },
      h('div', { class: 'top__kicker' }, `${d.region.name} · ${d.members.length} obcí`),
      h('h1', {}, 'Co se vyvěsilo'),
      h('div', { class: 'top__meta' }, `Aktualizováno ${updated.toLocaleDateString('cs-CZ')} v ${updated.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' })}`),
      d.sample ? h('div', { class: 'sample' }, 'Ukázková data – nejde o skutečná oznámení.') : null)),
    h('div', { class: 'bar' }, h('div', { class: 'col' }, chips,
      h('div', { class: 'find' }, h('label', { class: 'vh', for: 'q' }, 'Hledat v oznámeních'), search))),
    list);
  paint();
}

function villages(d) {
  return h('section', { class: 'villages', 'aria-labelledby': 'h-vil' },
    h('h2', { id: 'h-vil' }, 'Které desky čteme'),
    h('ul', {}, d.members.map((m) => {
      const url = safeUrl(m.board_url) || safeUrl(m.web);
      const status = m.connected
        ? (m.ok ? h('span', { class: 'ok' }, 'připojeno') : h('span', { class: 'miss' }, 'poslední stažení selhalo'))
        : h('span', { class: 'miss' }, m.status || 'deska zatím nenalezena');
      return h('li', {}, url ? h('a', { href: url, rel: 'noopener noreferrer' }, m.name) : h('span', {}, m.name), status);
    })),
    h('p', {}, d.ai_model
      ? 'Shrnutí píše automaticky AI (Claude) z textu a příloh. Může se splést.'
      : 'Shrnutí vznikají automaticky z textu oznámení a příloh.',
    ' Právně platí znění na úřední desce obce.'));
}

// ---------- detail ----------

function renderDetail(id) {
  const it = state.data.items.find((x) => x.id === id);
  window.scrollTo(0, 0);
  const back = h('a', { class: 'back', href: '#/' }, icon(BACK, 20), 'Zpět na přehled');
  if (!it) {
    return mount('Nenalezeno', h('header', { class: 'top top--detail' }, h('div', { class: 'top__inner col' }, back)),
      h('div', { class: 'content col' }, h('p', {}, 'Oznámení už na desce není nebo odkaz neplatí.')));
  }
  const s = it.s || {};
  const u = urgency(it);
  const docs = (it.docs || []).map((d) => ({ ...d, url: safeUrl(d.url) })).filter((d) => d.url);
  const original = safeUrl(it.url);

  const todoBox = s.action
    ? h('section', { class: u ? 'todo' : 'calm', 'aria-labelledby': 'h-todo' },
      u ? h('h2', { id: 'h-todo' }, icon(CLOCK, 18), u.kind === 'deadline' ? 'Co můžeš udělat' : 'Kdy') : h('h2', { id: 'h-todo', class: 'vh' }, 'Co můžeš udělat'),
      h('p', { class: u ? 'todo__main' : '' }, s.action),
      u ? h('p', { class: 'muted' }, u.kind === 'running' ? `Platí ještě do ${dateCz(u.date)}.` : `${inDays(u.date).charAt(0).toUpperCase()}${inDays(u.date).slice(1)}.`) : null)
    : h('p', { class: 'calm' }, 'Jen pro informaci, nic dělat nemusíš.');

  mount(s.tldr || it.t,
    h('header', { class: 'top top--detail' }, h('div', { class: 'top__inner col' }, back)),
    h('article', { class: 'content col detail' },
      h('div', { class: 'section' },
        h('div', { class: 'card__meta' }, tag(it), h('span', {}, `${it.o} · vyvěšeno ${dateCz(it.p, { year: true })}`)),
        h('h1', {}, s.tldr || it.t)),
      todoBox,
      s.bullets?.length ? h('section', { class: 'section', 'aria-labelledby': 'h-gist' },
        h('h2', { id: 'h-gist', style: 'margin:0;font-size:18px' }, 'Ve zkratce'),
        h('ul', { class: 'bullets' }, s.bullets.map((b) => h('li', {}, b)))) : null,
      s.where ? h('section', { class: 'section', 'aria-labelledby': 'h-where' },
        h('h2', { id: 'h-where', style: 'margin:0;font-size:18px' }, 'Kde'),
        h('p', { style: 'margin:0' }, s.where)) : null,
      h('section', { class: 'section', 'aria-labelledby': 'h-docs' },
        h('h2', { id: 'h-docs', style: 'margin:0;font-size:18px' }, docs.length ? 'Přílohy' : 'Bez příloh'),
        docs.map((d) => h('a', { class: 'doc', href: d.url, rel: 'noopener noreferrer' },
          icon(DOC_ICON[d.k] || DOC_ICON.jine, 28, 'var(--link)'),
          h('span', { class: 'doc__body' },
            h('span', { class: 'doc__name' }, d.n || 'Příloha'),
            h('span', { class: 'doc__info' }, [
              d.x ? d.x.toUpperCase() : null,
              d.k === 'sken' ? (d.ocr ? 'sken, text rozpoznán (OCR)' : 'sken') : d.k === 'mapa' ? 'mapa / výkres' : null,
              d.pg ? `${d.pg} ${plural(d.pg, 'strana', 'strany', 'stran')}` : null,
              d.w,
            ].filter(Boolean).join(' · ')))))),
      h('section', { class: 'official' },
        h('div', {}, 'Úřední název: ', it.t),
        it.cj ? h('div', {}, `Č. j.: ${it.cj}`) : null,
        it.u ? h('div', {}, `Visí do ${dateCz(it.u, { year: true })}`) : null,
        it.r ? h('div', {}, `Sejmuto ${dateCz(it.r, { year: true })}`) : null,
        original ? h('a', { href: original, rel: 'noopener noreferrer' }, `Původní znění na úřední desce (${it.o})`) : null,
        h('p', {}, s.src === 'claude'
          ? 'Shrnutí napsala AI (Claude) z textu a příloh. Může se splést – právně platí znění na úřední desce.'
          : 'Shrnutí vzniklo automaticky z textu oznámení. Právně platí znění na úřední desce.'))));
}

// ---------- start ----------

async function start() {
  try {
    const res = await fetch(DATA, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
  } catch (err) {
    mount('Chyba', h('div', { class: 'content col' }, h('h1', {}, 'Data se nepodařilo načíst'), h('p', {}, String(err))));
    return;
  }
  window.addEventListener('hashchange', route);
  route();
}

start();
