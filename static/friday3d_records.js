/* Friday 3D for other workspaces: News, Contacts, Tasks, Calendar,
 * Messages, the model library, and Code (Projects as a city).
 *
 * Each workspace keeps its normal view. A slim bar on top ("🧊 View in 3D")
 * swaps it for the same 3D engine the Studio file browser uses
 * (studio_files3d.js), fed by the workspace's own read endpoint. The 3D view
 * is read-only: selecting a card shows its details, and "Open" hands it back
 * to the workspace's own view, where every real action already lives.
 *
 * Sources are plain objects in SOURCES below: load() returns records,
 * toItem() maps one record to a card, detail() lists what the side panel
 * shows, open() deep-links back into the workspace.
 *
 * Loaded after studio_files3d.js; defines window.Friday3DToggle.
 */
(function () {
  'use strict';
  if (window.Friday3DToggle) return;
  const F = window.Friday3D;
  if (!F) return;
  const h = React.createElement;
  const { useState, useEffect, useRef } = React;
  const { api, fmtDate, hex, BRAND } = F;

  const secs = v => {
    if (v == null || v === '') return 0;
    if (typeof v === 'number') return v > 1e12 ? Math.floor(v / 1000) : Math.floor(v);
    const t = Date.parse(v);
    return isNaN(t) ? 0 : Math.floor(t / 1000);
  };
  const clip = (s, n) => { s = String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const PALETTE = [0x00d4ff, 0xff0080, 0x00ff80, 0x7b61ff, 0xf59e0b, 0x5fa8ff, 0xff8bcb, 0x4ecdc4, 0xfeca57, 0xff6b6b, 0x7de1ff, 0xb0ffc8];
  const colorFor = key => { let x = 0; for (const c of String(key)) x = (x * 31 + c.charCodeAt(0)) >>> 0; return PALETTE[x % PALETTE.length]; };
  const json = url => api(url).then(r => r.json());
  const nav = (workspace, extra) => window.fridayRunActions && window.fridayRunActions([Object.assign({ type: 'navigate', workspace }, extra || {})]);

  const V = {
    wall: { id: 'wall', label: 'Wall', ico: '▦' }, ring: { id: 'ring', label: 'Carousel', ico: '◎' },
    time: { id: 'time', label: 'Timeline', ico: '⌛' }, cluster: { id: 'cluster', label: 'Clusters', ico: '⁂' },
    week: { id: 'week', label: 'Week', ico: '▤' }, orbit: { id: 'orbit', label: 'Orbit', ico: '◌' },
    stack: { id: 'stack', label: 'Stacks', ico: '☰' }, city: { id: 'city', label: 'City', ico: '▥' }
  };

  // ── sources ────────────────────────────────────────────────────────────
  // group(rec) -> key; groups registers colours and labels for those keys.
  const SOURCES = {};
  window.__friday3dSources = SOURCES;   // read-only, for tests

  const PRIO = { high: 3, medium: 2, low: 1 };

  // Model library: every model Friday can route to, from /api/models/search.
  SOURCES.models = {
    label: 'Model library', openLabel: 'Show in Model Browser', views: ['ring', 'cluster', 'wall', 'orbit'],
    empty: 'No models in the catalog yet.',
    load: () => json('/api/models/search?q=&limit=500').then(d => d.models || []),
    toItem: m => ({ id: m.provider + ':' + m.id, title: m.label || m.id,
      sub: (m.provider_label || m.provider) + (m.context_window ? ' · ' + Math.round(m.context_window / 1000) + 'k context' : ''),
      badge: (m.local ? 'local' : 'cloud') + (m.available ? '' : ' · no key'), strip: m.label || m.id,
      weight: m.context_window || 0, time: 0 }),
    groupings: {
      provider: { label: 'provider', key: m => m.provider_label || m.provider },
      where: { label: 'local / cloud', key: m => m.local ? 'On this PC' : m.available ? 'Cloud, ready' : 'Cloud, needs a key',
        style: k => ({ color: k === 'On this PC' ? BRAND.cyan : k === 'Cloud, ready' ? 0x7b61ff : 0x66758c, rank: k === 'On this PC' ? 0 : k === 'Cloud, ready' ? 1 : 2 }) },
      modality: { label: 'kind', key: m => (m.modalities || ['text']).join(' + ') }
    },
    detail: m => [['Model', m.id], ['Provider', m.provider_label || m.provider], ['Runs', m.local ? 'on this PC' : 'in the cloud'],
      ['Ready', m.available ? 'yes' : 'needs an API key'], ['Context', m.context_window ? m.context_window.toLocaleString() + ' tokens' : null],
      ['Price', m.price_in != null ? '$' + m.price_in + ' in / $' + m.price_out + ' out per million tokens' : m.free ? 'free' : null],
      ['Tools', m.supports_tools == null ? null : m.supports_tools ? 'yes' : 'no'], ['Kinds', (m.modalities || []).join(', ')], ['Note', m.note], ['Licence', m.licence_note || m.licence]],
    open: () => {
      window.__fridaySettingsTab = 'providers';
      try { window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: 'providers' })); } catch (_) {}
      nav('settings', { view3d: false });
    }
  };

  // News: the archive the News workspace's Feed tab reads.
  SOURCES.news = {
    label: 'News', openLabel: 'Open article', views: ['time', 'cluster', 'wall', 'ring'],
    empty: 'No archived stories yet. They appear after the news archiver has run.',
    // the archive serves 200 a page; read up to 1,000 of the newest
    load: () => {
      const page = (off, acc) => json('/api/news/archive?offset=' + off + '&limit=200&sort=newest').then(d => {
        const got = acc.concat(d.items || []);
        return d.has_more && got.length < 1000 && (d.items || []).length ? page(off + 200, got) : got;
      });
      return page(0, []);
    },
    toItem: n => ({ id: n.id || n.url, title: n.title, sub: n.snippet, badge: (n.source || n.domain || '') + (n.category ? ' · ' + n.category : ''),
      strip: n.source || n.domain || n.title, weight: +n.relevance_score || 0, time: secs(n.published_at || n.fetched_at) }),
    groupings: {
      category: { label: 'topic', key: n => n.category || 'Other' },
      source: { label: 'source', key: n => n.source || n.domain || 'Other' },
      sentiment: { label: 'tone', key: n => n.sentiment || 'neutral',
        style: k => ({ color: k === 'positive' ? 0x00ff80 : k === 'negative' ? 0xff6b6b : 0x5fa8ff }) }
    },
    detail: n => [['Source', n.source || n.domain], ['Topic', n.category], ['Published', n.published_at ? fmtDate(secs(n.published_at)) : null],
      ['Tone', n.sentiment], ['Relevance', n.relevance_score != null ? (+n.relevance_score).toFixed(2) : null],
      ['Source trust', n.trust_score != null ? Math.round(n.trust_score * 100) + '%' : null], ['Summary', n.snippet]],
    open: n => { if (n.url) window.open(n.url, '_blank', 'noopener'); }
  };

  // People: the trust graph behind Contacts and Trust.
  const RING = ['Inner circle', 'Trusted', 'Known', 'Distant'];
  const ringOf = c => { const o = +c.overall || 0; return o >= 0.75 ? RING[0] : o >= 0.5 ? RING[1] : o >= 0.25 ? RING[2] : RING[3]; };
  SOURCES.people = {
    label: 'People', openLabel: 'Open contact', views: ['orbit', 'cluster', 'wall', 'ring'],
    empty: 'No people tracked yet.',
    load: () => json('/api/contacts').then(d => d.contacts || []),
    toItem: c => ({ id: c.name, title: c.name, sub: (c.aliases || []).slice(0, 3).join(', '),
      badge: Math.round((+c.overall || 0) * 100) + '% trust · ' + (c.evidence_count || 0) + ' notes',
      weight: +c.overall || 0, time: secs(c.last_interaction) }),
    groupings: {
      closeness: { label: 'trust', key: ringOf,
        style: k => ({ rank: RING.indexOf(k), color: [BRAND.cyan, 0x7b61ff, 0x5fa8ff, 0x66758c][RING.indexOf(k)] }) },
      domain: { label: 'area', key: c => (c.domains && c.domains[0]) || 'general' }
    },
    detail: c => [['Trust', Math.round((+c.overall || 0) * 100) + '%'], ['Also known as', (c.aliases || []).join(', ')],
      ['Areas', (c.domains || []).join(', ')], ['Evidence', c.evidence_count], ['Last contact', c.last_interaction ? fmtDate(secs(c.last_interaction)) : null]],
    open: c => nav('contacts', { view3d: false, name: c.name })
  };

  // Tasks: Friday's to-dos (Home) and goals, orbiting what matters now.
  const URGENCY = ['Due now', 'High priority', 'Waiting for you', 'Later', 'Done'];
  const urgencyOf = t => {
    if (t.status === 'completed' || t.status === 'rejected' || t.status === 'cancelled') return 'Done';
    const due = secs(t.deadline), now = Date.now() / 1000;
    if (due && due < now + 86400 * 2) return 'Due now';
    if (t.priority === 'high') return 'High priority';
    if (t.status === 'proposed') return 'Waiting for you';
    return 'Later';
  };
  SOURCES.tasks = {
    label: 'Tasks', openLabel: 'Show on Home', views: ['orbit', 'cluster', 'time', 'wall'],
    empty: 'No to-dos or goals yet.',
    load: () => Promise.all([
      json('/api/todos').then(d => (d.todos || []).map(t => Object.assign({ kind: 'todo' }, t))).catch(() => []),
      json('/api/goals').then(d => (d.goals || []).map(g => Object.assign({ kind: 'goal', id: g.goal_id }, g))).catch(() => [])
    ]).then(([a, b]) => a.concat(b)),
    toItem: t => ({ id: t.kind + ':' + t.id, title: t.title, sub: t.description,
      badge: (t.kind === 'goal' ? 'goal · ' : '') + (t.priority || t.status || '') + (t.deadline ? ' · due ' + String(t.deadline).slice(0, 10) : ''),
      weight: PRIO[t.priority] || (t.kind === 'goal' ? 2 : 1), time: secs(t.deadline || t.updated || t.updated_at || t.created || t.created_at) }),
    groupings: {
      urgency: { label: 'urgency', key: urgencyOf,
        style: k => ({ rank: URGENCY.indexOf(k), color: [0xef4444, 0xff0080, 0xf59e0b, 0x5fa8ff, 0x66758c][URGENCY.indexOf(k)] }) },
      status: { label: 'status', key: t => t.status || 'open' },
      category: { label: 'area', key: t => t.kind === 'goal' ? 'Goals' : (t.category || 'general') }
    },
    detail: t => [['Kind', t.kind === 'goal' ? 'Goal' : 'To-do'], ['Status', t.status], ['Priority', t.priority], ['Due', t.deadline],
      ['Area', t.category], ['From', t.source], ['Details', t.description]],
    open: () => nav('home', { view3d: false })
  };

  // Calendar: this week, day by day.
  const isoDay = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  const hhmm = t => t ? new Date(t * 1000).toTimeString().slice(0, 5) : '';
  SOURCES.calendar = {
    label: 'Calendar', openLabel: 'Open that day', views: ['week', 'time', 'cluster', 'wall'],
    empty: 'Nothing on the calendar this week.',
    load: () => {
      const now = new Date(), start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
      const days = Array.from({ length: 7 }, (_, k) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + k));
      return Promise.all(days.map(d => json('/api/calendar/day/' + isoDay(d)).then(r => (r.events || []).map(e => Object.assign({ day: isoDay(d) }, e))).catch(() => [])))
        .then(parts => [].concat.apply([], parts));
    },
    toItem: e => {
      const t0 = secs(e.start_time), t1 = secs(e.end_time);
      return { id: e.id || (e.title + t0), title: e.title, sub: [e.location, (e.attendees || []).slice(0, 3).join(', ')].filter(Boolean).join(' · '),
        badge: e.all_day ? 'all day' : hhmm(t0) + (t1 ? '–' + hhmm(t1) : ''),
        weight: (e.attendees || []).length, time: e.all_day ? secs(e.day + 'T07:00:00') : t0,
        dur: e.all_day ? 3600 : Math.max(900, (t1 - t0) || 3600) };
    },
    groupings: {
      type: { label: 'kind', key: e => e.conflict ? 'Clashes' : e.type === 'career' ? 'Career' : 'Everyday',
        style: k => ({ color: { Clashes: 0xef4444, Career: 0x7b61ff, Everyday: BRAND.cyan }[k] }) },
      source: { label: 'calendar', key: e => e.source === 'google' ? 'Google' : 'Friday' }
    },
    detail: e => [['When', e.all_day ? e.day + ' (all day)' : fmtDate(secs(e.start_time)) + (e.end_time ? ' – ' + hhmm(secs(e.end_time)) : '')],
      ['Where', e.location], ['With', (e.attendees || []).join(', ')], ['Clashes', e.conflict ? 'overlaps another event' : null],
      ['Calendar', e.source], ['Notes', e.description]],
    open: e => nav('calendar', { view3d: false, date: e.day })
  };

  // Messages: the triaged inbox, stacked by lane, sender, state or account.
  SOURCES.messages = {
    label: 'Messages', openLabel: 'Open thread', views: ['stack', 'time', 'cluster', 'wall'],
    empty: 'No messages to show.',
    load: () => json('/api/messages?lane=all').then(d => d.messages || []),
    toItem: m => ({ id: m.id || m.thread_id, title: m.subject || '(no subject)', sub: m.snippet || '',
      badge: (m.unread ? '● ' : '') + (m.sender || m.sender_email || ''), strip: m.sender || m.subject,
      weight: (m.unread ? 2 : 0) + (m.flagged ? 2 : 0) + (m.has_attachment ? 1 : 0), time: secs(m.timestamp) }),
    groupings: {
      lane: { label: 'lane', key: m => m.lane || 'other' },
      sender: { label: 'sender', key: m => m.sender || m.sender_email || 'unknown' },
      state: { label: 'state', key: m => m.flagged ? 'Flagged' : m.unread ? 'Unread' : 'Read',
        style: k => ({ color: { Flagged: 0xf59e0b, Unread: BRAND.cyan, Read: 0x66758c }[k] }) },
      account: { label: 'account', key: m => m.account_label || 'inbox' }
    },
    detail: m => [['From', m.sender_email ? (m.sender || '') + ' <' + m.sender_email + '>' : m.sender], ['When', m.timestamp ? fmtDate(secs(m.timestamp)) : null],
      ['Lane', m.lane], ['Account', m.account_label], ['State', [m.unread && 'unread', m.flagged && 'flagged', m.has_attachment && 'attachment'].filter(Boolean).join(', ')],
      ['Preview', m.snippet]],
    open: m => nav('messages', { view3d: false, lane: 'all', thread_id: m.thread_id || m.id })
  };

  function Records3DPanel({ source, onClose }) {
    const src = SOURCES[source];
    const mountRef = useRef(null), engRef = useRef(null), boxRef = useRef(null);
    const [recs, setRecs] = useState(null);
    const [err, setErr] = useState('');
    const [view, setView] = useState(src.views[0]);
    const [grouping, setGrouping] = useState(Object.keys(src.groupings)[0]);
    const [query, setQuery] = useState('');
    const [sel, setSel] = useState(null);
    const [hover, setHover] = useState(null);
    const [dazzle, setDazzle] = useState('full');
    const [stats, setStats] = useState(null);
    const itemsRef = useRef([]);

    useEffect(() => {
      let eng;
      try {
        eng = F.createEngine(mountRef.current, {
          onHover: (it, p) => setHover(it && p ? { it, x: p.x, y: p.y } : null),
          onPick: (i, activate) => {
            const it = itemsRef.current[i];
            if (!it) { setSel(null); eng.select(-1); return; }
            setSel(it); eng.select(i, true);
            if (activate) src.open(it.rec);
          },
          onStep: d => { const cur = itemsRef.current.indexOf(selRef.current); const j = eng.step(cur, d); if (j >= 0) { setSel(itemsRef.current[j]); eng.select(j, true); } },
          onInteract: () => boxRef.current && boxRef.current.focus({ preventScroll: true })
        });
      } catch (e) { setErr('3D is unavailable in this browser.'); return; }
      engRef.current = eng;
      window.__friday3d = eng;
      const iv = setInterval(() => setStats(eng.stats()), 1000);
      api('/api/settings').then(r => r.json()).then(d => { const v = ((d && (d.settings || d)) || {}).studio_dazzle; if (v) setDazzle(v); }).catch(() => {});
      const onDz = e => e && e.detail && setDazzle(e.detail);
      window.addEventListener('friday-dazzle', onDz);
      return () => { clearInterval(iv); window.removeEventListener('friday-dazzle', onDz); eng.dispose(); if (window.__friday3d === eng) window.__friday3d = null; };
    }, []);
    const selRef = useRef(null); selRef.current = sel;
    useEffect(() => { engRef.current && engRef.current.setDazzle(dazzle); }, [dazzle]);

    const load = () => {
      setErr('');
      src.load().then(list => setRecs(list || []), e => { setErr(String((e && e.message) || e || 'Could not load')); setRecs([]); });
    };
    useEffect(load, [source]);

    // records -> items for the current grouping; the same ids keep their
    // place, so regrouping glides rather than rebuilding
    useEffect(() => {
      const eng = engRef.current;
      if (!eng || !recs) return;
      const g = src.groupings[grouping];
      const cats = {};
      const list = recs.map((rec, i) => {
        const base = src.toItem(rec);
        const key = String(g.key(rec) || 'other');
        if (!cats[key]) cats[key] = Object.assign({ color: colorFor(key), label: key, ico: '•' }, g.style ? g.style(key, rec) : {});
        return Object.assign({ i, rel: String(base.id != null ? base.id : i), name: base.title || '(untitled)', dir: false,
          size: base.weight || 0, mtime: base.time || 0, dur: base.dur || 0, ext: '', cat: 'rec:' + source + ':' + key,
          parent: -1, depth: 1, kids: [], card: { title: base.title || '(untitled)', sub: base.sub || '', badge: base.badge || '' },
          strip: base.strip || base.title, img: base.img || null, rec }, {});
      });
      const reg = {};
      Object.keys(cats).forEach(k => { reg['rec:' + source + ':' + k] = cats[k]; });
      F.registerCats(reg);
      itemsRef.current = list;
      eng.setData(list, 'rec:' + source, '');
      eng.setGroupBy('type');
      setSel(null);
    }, [recs, grouping]);
    useEffect(() => { engRef.current && engRef.current.setView(view); }, [view]);

    // search dims nothing: it narrows the arrangement to what matches
    useEffect(() => {
      const eng = engRef.current, list = itemsRef.current;
      if (!eng || !list.length) return;
      const q = query.trim().toLowerCase();
      if (!q) { eng.setFilter(null); return; }
      const t = setTimeout(() => {
        const mask = new Uint8Array(list.length);
        list.forEach((it, i) => { const hay = (it.card.title + ' ' + it.card.sub + ' ' + it.card.badge).toLowerCase(); if (q.split(/\s+/).every(w => hay.includes(w))) mask[i] = 1; });
        eng.setFilter(mask);
      }, 150);
      return () => clearTimeout(t);
    }, [query, recs, grouping]);

    const onKey = e => {
      const eng = engRef.current;
      if (!eng || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      const cur = itemsRef.current.indexOf(sel);
      const dir = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[e.key];
      const vi = '123456789'.indexOf(e.key);
      if (dir) { const j = eng.nav(cur, dir); if (j >= 0) { setSel(itemsRef.current[j]); eng.select(j, true); } }
      else if (vi >= 0 && vi < src.views.length) setView(src.views[vi]);
      else if (e.key === 'Enter' && sel) src.open(sel.rec);
      else if (e.key === 'Escape') { if (sel) { setSel(null); eng.select(-1); } else onClose(); }
      else if (e.key === 'r' || e.key === 'R') eng.resetCamera();
      else return;
      e.preventDefault(); e.stopPropagation();
    };

    const BTN = { fontSize: 11, padding: '5px 9px', minHeight: 30 };
    const PANEL = 'rgba(6,10,18,0.9)';
    const shown = stats ? stats.visible : (recs ? recs.length : 0);
    return h('div', { ref: boxRef, tabIndex: 0, onKeyDown: onKey, style: { outline: 'none' } },
      h('div', { style: { display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center', marginBottom: 6 } },
        src.views.map((v, i) => h('button', { key: v, className: 'btn' + (view === v ? '' : ' btn-magenta'), style: Object.assign({}, BTN, { fontWeight: view === v ? 700 : 400 }), 'aria-pressed': view === v, onClick: () => setView(v), title: V[v].label + ' (' + (i + 1) + ')' }, V[v].ico + ' ' + V[v].label)),
        Object.keys(src.groupings).length > 1 && h('span', { style: { display: 'flex', gap: 3, marginLeft: 6, alignItems: 'center', fontSize: 11, color: '#8fb2dd' } }, 'group by',
          Object.keys(src.groupings).map(k => h('button', { key: k, className: 'btn' + (grouping === k ? '' : ' btn-magenta'), style: BTN, onClick: () => setGrouping(k) }, src.groupings[k].label))),
        h('input', { value: query, onChange: e => setQuery(e.target.value), placeholder: 'Search…', 'aria-label': 'Search in 3D', style: { flex: '1 1 140px', minWidth: 120, background: '#0b1220', color: '#e6f0ff', border: '1px solid #24406a', borderRadius: 6, padding: '6px 8px', fontSize: 12, minHeight: 30 } }),
        h('button', { className: 'btn btn-magenta', style: BTN, onClick: load, title: 'Reload' }, '↻')),
      h('div', { style: { position: 'relative', height: 'calc(100vh - 290px)', minHeight: 380, borderRadius: 10, overflow: 'hidden', border: '1px solid rgba(80,140,220,0.25)', background: '#000103' } },
        h('div', { ref: mountRef, style: { position: 'absolute', inset: 0 } }),
        (err || recs === null) && h('div', { style: { position: 'absolute', top: 12, left: 12, padding: '6px 10px', borderRadius: 6, background: PANEL, color: err ? '#ff8a8a' : '#9fd0ff', fontSize: 12 } }, err || 'Loading…'),
        recs && !recs.length && !err && h('div', { style: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#7f93ad', fontSize: 13 } }, src.empty),
        hover && hover.it && hover.it !== sel && h('div', { style: { position: 'fixed', left: hover.x + 14, top: hover.y + 12, pointerEvents: 'none', padding: '5px 8px', borderRadius: 6, background: PANEL, border: '1px solid #2e5a8f', color: '#e6f0ff', fontSize: 11, zIndex: 50, maxWidth: 300 } },
          h('div', { style: { fontWeight: 600 } }, clip(hover.it.card.title, 90)), hover.it.card.sub && h('div', { style: { color: '#8fa6c4' } }, clip(hover.it.card.sub, 120))),
        h('div', { style: { position: 'absolute', left: 10, bottom: 8, fontSize: 10, color: '#6f86a6', pointerEvents: 'none' } },
          (recs ? shown + ' of ' + recs.length + ' shown' : '') + (stats ? ' · ' + stats.fps + ' fps' : '') + ' · drag orbit · scroll zoom · 1–' + src.views.length + ' views · Enter opens · Esc closes'),
        sel && h('div', { style: { position: 'absolute', top: 10, right: 10, bottom: 10, width: 'min(360px, 44%)', display: 'flex', flexDirection: 'column', gap: 8, padding: 12, borderRadius: 10, background: PANEL, border: '1px solid rgba(0,212,255,0.35)', color: '#e6f0ff', fontSize: 12, overflow: 'auto' } },
          h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8 } },
            h('div', { style: { fontWeight: 700, fontSize: 13 } }, sel.card.title),
            h('button', { className: 'btn', style: BTN, onClick: () => { setSel(null); engRef.current && engRef.current.select(-1); }, 'aria-label': 'Close details' }, '✕')),
          sel.img && h('img', { src: sel.img, alt: '', style: { maxWidth: '100%', maxHeight: 160, objectFit: 'contain', borderRadius: 6 } }),
          h('div', { style: { display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 10px', color: '#b8c7dc' } },
            src.detail(sel.rec).filter(r => r && r[1] != null && r[1] !== '').map(([k, v], j) => [h('div', { key: 'k' + j, style: { color: '#6f86a6' } }, k), h('div', { key: 'v' + j, style: { wordBreak: 'break-word' } }, String(v))])),
          h('div', { style: { display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 'auto' } },
            h('button', { className: 'btn', style: BTN, onClick: () => src.open(sel.rec) }, '↗ ' + src.openLabel)))));
  }

  // The bar every wrapped workspace gets. The workspace itself stays mounted
  // underneath (hidden) so switching back is instant and loses nothing.
  function Friday3DToggle({ ws, source, children }) {
    ws = ws || source;
    const [on, setOn] = useState(() => { try { return localStorage.getItem('friday_3d_on_' + ws) === '1'; } catch (_) { return false; } });
    const set = v => { setOn(v); try { localStorage.setItem('friday_3d_on_' + ws, v ? '1' : '0'); } catch (_) {} };
    useEffect(() => {
      // a link that opened this workspace before the bar mounted
      const t = window.__fridayNavTarget;
      if (t && t.workspace === ws && t.view3d != null) set(!!t.view3d);
      const f = e => { const d = e && e.detail; if (d && d.workspace === ws && d.view3d != null) set(!!d.view3d); };
      window.addEventListener('friday-nav', f);
      return () => window.removeEventListener('friday-nav', f);
    }, [ws]);
    const src = source === 'code' ? { label: 'Projects' } : SOURCES[source];
    if (!src || typeof THREE === 'undefined') return children;
    return h(React.Fragment, null,
      h('div', { style: { display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 6, marginBottom: on ? 6 : 4 } },
        on && h('span', { style: { fontFamily: 'Orbitron, Inter, sans-serif', fontSize: 10, letterSpacing: '.12em', color: '#00d4ff', marginRight: 'auto' } }, '🧊 ' + String(src.label).toUpperCase() + ' IN 3D'),
        h('button', { className: 'btn' + (on ? '' : ' btn-magenta'), style: { fontSize: 11, padding: '4px 9px' }, onClick: () => set(!on), title: on ? 'Back to the normal view' : 'See this workspace in 3D' }, on ? '✕ Close 3D' : '🧊 View in 3D')),
      on && (source === 'code' ? h(window.Files3DPanel, { root: 'projects', path: '', view: 'city' }) : h(Records3DPanel, { source, onClose: () => set(false) })),
      h('div', { style: on ? { display: 'none' } : null }, children));
  }

  window.Friday3DToggle = Friday3DToggle;
  window.__friday3dRecords = { SOURCES, secs, clip, colorFor };
})();
