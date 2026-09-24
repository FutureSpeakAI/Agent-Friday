/* Friday 3D for other workspaces: News, Contacts, Tasks, Calendar,
 * Messages, the model library, and Code (Projects as a city).
 *
 * Each workspace keeps its normal view. A slim bar on top ("🧊 View in 3D")
 * swaps it for the same 3D engine the Studio file browser uses
 * (studio_files3d.js), fed by the workspace's own read endpoint. The 3D view
 * is read-only: selecting a card shows its details, and "Open" hands it back
 * to the workspace's own view, where every real action already lives.
 * The exception is a source with zones (Messages): its cards can be dragged
 * onto a zone, or selected several at a time, for actions that change only
 * Friday's own state and can each be undone. Nothing there sends or deletes.
 *
 * Sources are plain objects in SOURCES below: load() returns records,
 * toItem() maps one record to a card, detail() lists what the side panel
 * shows, open() deep-links back into the workspace. Each also says what
 * makes it its own: intro (how its cards arrive), sorts, filters, tools,
 * and, where it acts on cards, zones with act() and undo().
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
  const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(r => r.json().then(j => ({ ok: r.ok && j.status !== 'error', j }), () => ({ ok: false, j: { message: 'Friday answered ' + r.status } })));
  const nav = (workspace, extra) => window.fridayRunActions && window.fridayRunActions([Object.assign({ type: 'navigate', workspace }, extra || {})]);

  const V = {
    wall: { id: 'wall', label: 'Wall', ico: '▦', tip: 'A curved wall, one section per group' },
    ring: { id: 'ring', label: 'Carousel', ico: '◎', tip: 'Cover-flow ring; scroll to step through' },
    time: { id: 'time', label: 'Timeline', ico: '⌛', tip: 'A tunnel back through time, newest nearest' },
    cluster: { id: 'cluster', label: 'Clusters', ico: '⁂', tip: 'One ball per group, sized by how many' },
    week: { id: 'week', label: 'Week', ico: '▤', tip: 'Day columns, hour by hour' },
    orbit: { id: 'orbit', label: 'Orbit', ico: '◌', tip: 'Rings around now, the closest innermost' },
    stack: { id: 'stack', label: 'Stacks', ico: '☰', tip: 'One pile per group' },
    city: { id: 'city', label: 'City', ico: '▥', tip: 'Towers by size' }
  };
  const by = f => (a, b) => { const x = f(a), y = f(b); return x < y ? -1 : x > y ? 1 : 0; };
  const desc = f => (a, b) => by(f)(b, a);
  const lc = v => String(v || '').toLowerCase();

  // ── sources ────────────────────────────────────────────────────────────
  // group(rec) -> key; groups registers colours and labels for those keys.
  const SOURCES = {};
  window.__friday3dSources = SOURCES;   // read-only, for tests

  const PRIO = { high: 3, medium: 2, low: 1 };

  // Model library: every model Friday can route to, from /api/models/search.
  SOURCES.models = {
    label: 'Model library', openLabel: 'Show in Model Browser', views: ['ring', 'cluster', 'wall', 'orbit'],
    empty: 'No models in the catalog yet.', noun: 'models', intro: 'center',
    blurb: 'Every model Friday can route to. Filter to the ones that are ready.',
    sorts: {
      context: { label: 'Longest context', cmp: desc(m => +m.context_window || 0) },
      price: { label: 'Cheapest', cmp: by(m => m.free ? 0 : m.price_in != null ? +m.price_in : 1e9) },
      name: { label: 'Name', cmp: by(m => lc(m.label || m.id)) }
    },
    filters: [
      { id: 'ready', label: 'Ready to use', test: m => !!(m.local || m.available) },
      { id: 'local', label: 'On this PC', test: m => !!m.local },
      { id: 'tools', label: 'Uses tools', test: m => !!m.supports_tools }
    ],
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
    noun: 'stories', intro: 'rain', carryIco: '📰',
    blurb: 'The archive as a feed falling into place. Drop a story on Read later to keep it; Undo takes it back.',
    // The archive serves 200 a page; up to 1,000 of the newest are read.
    // The first page is shown as soon as it lands; the rest follow.
    load: (params, partial) => {
      const saved = json('/api/news/read-later').then(d => new Set((d.items || []).map(a => a.url)), () => new Set());
      const page = (off, acc) => json('/api/news/archive?offset=' + off + '&limit=200&sort=newest').then(d => {
        const got = acc.concat(d.items || []);
        if (off === 0 && partial) saved.then(sv => partial(got.map(n => Object.assign({}, n, { saved: sv.has(n.url) }))));
        return d.has_more && got.length < 1000 && (d.items || []).length ? page(off + 200, got) : got;
      });
      return Promise.all([page(0, []), saved]).then(([all, sv]) => all.map(n => Object.assign({}, n, { saved: sv.has(n.url) })));
    },
    sorts: {
      newest: { label: 'Newest', cmp: desc(n => secs(n.published_at || n.fetched_at)) },
      relevance: { label: 'Most relevant', cmp: desc(n => +n.relevance_score || 0) },
      trust: { label: 'Most trusted source', cmp: desc(n => +n.trust_score || 0) }
    },
    filters: [
      { id: 'day', label: 'Last 24 h', test: n => secs(n.published_at || n.fetched_at) > Date.now() / 1000 - 86400 },
      { id: 'saved', label: 'Read later', test: n => !!n.saved },
      { id: 'trusted', label: 'Trusted sources', tip: 'Source trust of 70% or more', test: n => (+n.trust_score || 0) >= 0.7 }
    ],
    zones: [
      { id: 'save', ico: '🔖', label: 'Read later', done: 'Saved for later', patch: { saved: true }, fx: 'share', inDetail: true,
        when: n => !n.saved && !!n.url, whenNot: 'Already saved for later.', tip: 'Keep it in Read later (Undo takes it back out)' }
    ],
    keys: { s: 'save' },
    act: (recs, z) => {
      const ids = recs.map(r => r.id || r.url);
      return Promise.all(recs.map(n => post('/api/news/read-later', { url: n.url, title: n.title, source: n.source || n.domain, snippet: n.snippet, category: n.category })
        .catch(e => ({ ok: false, j: { message: String(e) } })))).then(rs => ({
        okIds: ids.filter((_, k) => rs[k].ok), before: { urls: recs.filter((_, k) => rs[k].ok).map(n => n.url) },
        error: (rs.find(r => !r.ok) || { j: {} }).j.message || '' }));
    },
    // only what this view saved is taken back out
    undo: before => Promise.all((before.urls || []).map(u => api('/api/news/read-later?url=' + encodeURIComponent(u), { method: 'DELETE' }).then(r => r.ok, () => false)))
      .then(oks => oks.every(Boolean)),
    toItem: n => ({ id: n.id || n.url, title: n.title, sub: n.snippet, badge: (n.saved ? '🔖 ' : '') + (n.source || n.domain || '') + (n.category ? ' · ' + n.category : ''),
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
    empty: 'No people tracked yet.', noun: 'people', intro: 'spiral',
    blurb: 'Your circles opening out from you: the closest ring is the people you trust most.',
    sorts: {
      trust: { label: 'Most trusted', cmp: desc(c => +c.overall || 0) },
      recent: { label: 'Recently in touch', cmp: desc(c => secs(c.last_interaction)) },
      evidence: { label: 'Most notes', cmp: desc(c => +c.evidence_count || 0) },
      name: { label: 'Name', cmp: by(c => lc(c.name)) }
    },
    filters: [
      { id: 'month', label: 'In touch this month', test: c => secs(c.last_interaction) > Date.now() / 1000 - 30 * 86400 }
    ],
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
    empty: 'No to-dos or goals yet.', noun: 'tasks', intro: 'rise', carryIco: '☑',
    blurb: 'What needs you, innermost first. Drop a to-do on Done to finish it; Undo reopens it.',
    sorts: {
      due: { label: 'Due soonest', cmp: by(t => secs(t.deadline) || 9e12) },
      priority: { label: 'Priority', cmp: desc(t => PRIO[t.priority] || 0) },
      updated: { label: 'Recently changed', cmp: desc(t => secs(t.updated || t.updated_at || t.created || t.created_at)) }
    },
    filters: [
      { id: 'open', label: 'Hide done', on: true, test: t => urgencyOf(t) !== 'Done' },
      { id: 'todo', label: 'To-dos only', test: t => t.kind === 'todo' }
    ],
    zones: [
      { id: 'done', ico: '✓', label: 'Done', done: 'Marked done', patch: { status: 'completed' }, fx: 'rename', inDetail: true,
        when: t => t.kind === 'todo' && urgencyOf(t) !== 'Done', whenNot: 'Only open to-dos can be marked done here (goals have their own steps).', tip: 'Mark the to-do done (Undo reopens it)' }
    ],
    keys: { d: 'done' },
    act: (recs, z) => Promise.all(recs.map(t => post('/api/todos/' + encodeURIComponent(t.id) + '/complete', {}).catch(e => ({ ok: false, j: { message: String(e) } }))))
      .then(rs => {
        const before = {};
        recs.forEach((t, k) => { if (rs[k].ok) before[t.id] = t.status || 'proposed'; });
        return { okIds: recs.filter((_, k) => rs[k].ok).map(t => t.id), before, error: (rs.find(r => !r.ok) || { j: {} }).j.message || '' };
      }),
    undo: before => Promise.all(Object.keys(before).map(id => post('/api/todos/' + encodeURIComponent(id) + '/restore', { status: before[id] }).then(r => r.ok, () => false)))
      .then(oks => oks.every(Boolean)),
    load: () => Promise.all([
      json('/api/todos').then(d => (d.todos || []).map(t => Object.assign({ kind: 'todo' }, t))),
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
    empty: 'Nothing on the calendar that week.', noun: 'events', intro: 'sweep',
    blurb: 'A week as it unrolls, Monday to Sunday. Step to other weeks with the arrows.',
    params: { week: 0 },
    tools: [
      { id: 'prev', label: '◀ Week', tip: 'The week before', run: c => c.setParams(p => Object.assign({}, p, { week: p.week - 1 })) },
      { id: 'now', label: 'This week', run: c => c.setParams(p => Object.assign({}, p, { week: 0 })), disabled: c => c.params.week === 0 },
      { id: 'next', label: 'Week ▶', tip: 'The week after', run: c => c.setParams(p => Object.assign({}, p, { week: p.week + 1 })) }
    ],
    sorts: {
      start: { label: 'Start time', cmp: by(e => secs(e.start_time)) },
      long: { label: 'Longest', cmp: desc(e => secs(e.end_time) - secs(e.start_time)) },
      people: { label: 'Most people', cmp: desc(e => (e.attendees || []).length) }
    },
    filters: [
      { id: 'clash', label: 'Clashes', test: e => !!e.conflict },
      { id: 'timed', label: 'Hide all-day', test: e => !e.all_day }
    ],
    load: params => {
      const now = new Date(), wk = (params && params.week) || 0;
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7) + wk * 7);
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
  const LANE_ORDER = ['career', 'finance', 'futurespeak', 'family', 'subscriptions', 'noise'];
  const LANE_NAME = { career: 'Career', finance: 'Finance', futurespeak: 'Projects', family: 'Family', subscriptions: 'Subscriptions', noise: 'Noise', other: 'Other' };
  SOURCES.messages = {
    label: 'Messages', openLabel: 'Open thread', views: ['stack', 'time', 'cluster', 'wall'],
    empty: 'No messages to show.', noun: 'messages', intro: 'deal', carryIco: '✉',
    zoneNote: 'in Friday only',
    blurb: 'Your inbox dealt into piles by lane. Drag cards onto a zone; every change is Friday-only and can be undone.',
    sorts: {
      newest: { label: 'Newest', cmp: desc(m => secs(m.timestamp)) },
      unread: { label: 'Unread first', cmp: (a, b) => (b.unread ? 1 : 0) - (a.unread ? 1 : 0) || secs(b.timestamp) - secs(a.timestamp) },
      sender: { label: 'Sender', cmp: by(m => lc(m.sender || m.sender_email)) }
    },
    filters: [
      { id: 'unread', label: 'Unread', test: m => !!m.unread },
      { id: 'flag', label: 'Flagged', test: m => !!m.flagged },
      { id: 'att', label: 'Attachments', test: m => !!m.has_attachment }
    ],
    keys: { e: 'archive', s: 'flag' },
    // a failed read is an error on screen, never an empty inbox
    load: () => json('/api/messages?lane=all').then(d => { if (d.status === 'error') throw new Error(d.error || 'Could not read mail.'); return d.messages || []; }),
    toItem: m => ({ id: m.id || m.thread_id, title: m.subject || '(no subject)', sub: m.snippet || '',
      badge: (m.unread ? '● ' : '') + (m.sender || m.sender_email || ''), strip: m.sender || m.subject,
      weight: (m.unread ? 2 : 0) + (m.flagged ? 2 : 0) + (m.has_attachment ? 1 : 0), time: secs(m.timestamp) }),
    groupings: {
      lane: { label: 'lane', key: m => m.lane || 'other',
        style: k => { const r = LANE_ORDER.indexOf(k); return { rank: r < 0 ? 9 : r, label: LANE_NAME[k] || k }; } },
      sender: { label: 'sender', key: m => m.sender || m.sender_email || 'unknown' },
      state: { label: 'state', key: m => m.flagged ? 'Flagged' : m.unread ? 'Unread' : 'Read',
        style: k => ({ color: { Flagged: 0xf59e0b, Unread: BRAND.cyan, Read: 0x66758c }[k] }) },
      account: { label: 'account', key: m => m.account_label || 'inbox' }
    },
    detail: m => [['From', m.sender_email ? (m.sender || '') + ' <' + m.sender_email + '>' : m.sender], ['When', m.timestamp ? fmtDate(secs(m.timestamp)) : null],
      ['Lane', m.lane], ['Account', m.account_label], ['State', [m.unread && 'unread', m.flagged && 'flagged', m.has_attachment && 'attachment'].filter(Boolean).join(', ')],
      ['Preview', m.snippet]],
    open: m => nav('messages', { view3d: false, lane: 'all', thread_id: m.thread_id || m.id }),
    // Drop zones. Every one changes Friday's view only (Gmail is not
    // touched) and every one can be undone; there is no delete zone.
    zones: [
      { id: 'archive', ico: '🗄', label: 'Archive', action: 'archive', away: true, done: 'Archived', inDetail: true, tip: 'Archive (in Friday only)' },
      { id: 'snooze', ico: '😴', label: 'Snooze 4h', action: 'snooze', away: true, done: 'Snoozed for 4 hours', inDetail: true, tip: 'Snooze for 4 hours (in Friday only)' },
      { id: 'flag', ico: '🚩', label: 'Flag', action: 'flag', patch: { flagged: true }, done: 'Flagged', inDetail: true, when: m => !m.flagged, whenNot: 'Already flagged.' },
      { id: 'read', ico: '✓', label: 'Read', action: 'read', patch: { unread: false }, done: 'Marked read', when: m => !!m.unread, whenNot: 'Already read.' },
      { id: 'unread', ico: '●', label: 'Unread', action: 'unread', patch: { unread: true }, done: 'Marked unread', inDetail: true, when: m => !m.unread, whenNot: 'Already unread.' }
    ].concat([['career', '💼 Career'], ['finance', '💰 Finance'], ['futurespeak', '🚀 Projects'], ['family', '👪 Family'], ['subscriptions', '📰 Subscriptions'], ['noise', '🔇 Noise']]
      .map(([k, l]) => ({ id: 'lane:' + k, ico: l.split(' ')[0], label: l.split(' ').slice(1).join(' '), lane: k, patch: { lane: k }, done: 'Moved to ' + l.split(' ').slice(1).join(' '), kind: 'lane',
        when: m => m.lane !== k, whenNot: 'Already in that lane.', tip: 'Move to the ' + l.split(' ').slice(1).join(' ') + ' lane (Friday learns from it)' }))),
    // -> { okIds, before, error }: which messages the server actually changed
    act: (recs, z) => {
      const ids = recs.map(r => r.id);
      if (z.lane) {
        return Promise.all(ids.map(id => post('/api/messages/classify', { id, lane: z.lane }).catch(e => ({ ok: false, j: { message: String(e) } })))).then(rs => {
          const before = {};
          rs.forEach(r => r.ok && Object.assign(before, r.j.before || {}));
          const bad = rs.find(r => !r.ok);
          return { okIds: ids.filter((_, k) => rs[k].ok), before, error: bad ? (bad.j.message || 'Friday could not move that.') : '' };
        });
      }
      // Gmail itself changes too, for accounts reconnected with sending
      const gmail = recs.map(m => ({ id: m.id, account_id: m.account_id, thread_id: m.thread_id || m.gmail_id }));
      return post('/api/messages/action', { ids, action: z.action, gmail }).then(r => {
        const st = Object.values((r.j && r.j.gmail_status) || {});
        const note = z.action === 'snooze' ? 'in Friday only' : st.includes('synced') && !st.includes('not_permitted') ? 'also in Gmail'
          : st.includes('synced') ? 'in Gmail where allowed' : 'in Friday only';
        const bad = Object.values((r.j && r.j.not_changed) || {});
        return { okIds: r.ok ? (r.j.ids || ids) : [], note,
          before: r.ok ? { states: r.j.before || {}, gmail: r.j.gmail_changes || {} } : {},
          error: !r.ok ? (r.j.message || 'That did not work.') : bad.length ? 'Gmail refused: ' + bad[0] : '' };
      }, e => ({ okIds: [], before: {}, error: "Couldn't reach Friday: " + e }));
    },
    undo: before => post('/api/messages/restore', before && before.states ? { states: before.states, gmail_changes: before.gmail || {} } : { states: before }).then(r => r.ok, () => false)
  };

  // ── the panel ──────────────────────────────────────────────────────────
  // One layout for every workspace, driven by its source:
  //   toolbar  views · group · sort · the workspace's filters · search · its tools
  //   scene    legend of groups (click one to show only it) · status
  //   zones    where cards can be dropped, for workspaces that act on them
  //   details  the picked card, with that workspace's actions
  // Records last read are kept for the session, so reopening a view shows
  // them at once and says it is refreshing until the new read lands.
  const CACHE = new Map(), PENDING = new Map();
  const FRESH_MS = 20000;
  // One read per source+params at a time: a hover that already started the
  // read and the view that opens a moment later share it.
  function fetchRecs(source, params, partial) {
    const key = source + ':' + JSON.stringify(params);
    if (PENDING.has(key)) return PENDING.get(key);
    const p = Promise.resolve().then(() => SOURCES[source].load(params, partial)).then(list => {
      PENDING.delete(key); CACHE.set(key, { recs: list || [], at: Date.now() }); return list || [];
    }, e => { PENDING.delete(key); throw e; });
    PENDING.set(key, p);
    return p;
  }
  function prefetch(source) {
    const src = SOURCES[source];
    if (!src) return;
    const params = Object.assign({}, src.params || {}), key = source + ':' + JSON.stringify(params), c = CACHE.get(key);
    if (PENDING.has(key) || (c && Date.now() - c.at < FRESH_MS)) return;
    fetchRecs(source, params).catch(() => {});
  }
  const ago = t => { const s = Math.round((Date.now() - t) / 1000); return s < 45 ? 'just now' : s < 3600 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago'; };

  if (typeof document !== 'undefined' && !document.getElementById('f3-style')) {
    const st = document.createElement('style');
    st.id = 'f3-style';
    st.textContent = `
      .f3 { outline:none; font-family:Inter,sans-serif; }
      .f3-bar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:8px; }
      .f3-seg { display:inline-flex; border:1px solid rgba(0,212,255,0.28); border-radius:9px; overflow:hidden; }
      .f3-seg button { background:transparent; border:0; border-right:1px solid rgba(0,212,255,0.14); color:#9fb6d6; font-size:11px; padding:6px 10px; cursor:pointer; min-height:30px; }
      .f3-seg button:last-child { border-right:0; }
      .f3-seg button:hover { color:#e6f6ff; background:rgba(0,212,255,0.06); }
      .f3-seg button.on { color:#e8fbff; background:rgba(0,212,255,0.16); font-weight:700; box-shadow:inset 0 -2px 0 #00d4ff; }
      .f3-field { display:inline-flex; align-items:center; gap:5px; font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:#6f86a6; }
      .f3-field select { background:#0b1220; color:#dbe8fa; border:1px solid #24406a; border-radius:7px; font-size:11.5px; padding:5px 6px; min-height:30px; text-transform:none; letter-spacing:0; }
      .f3-chip { display:inline-flex; align-items:center; gap:5px; font-size:11px; padding:5px 9px; border-radius:999px; border:1px solid rgba(255,255,255,0.14); color:#a9bbd4; background:rgba(255,255,255,0.03); cursor:pointer; min-height:28px; }
      .f3-chip.on { border-color:#00d4ff; color:#e6f9ff; background:rgba(0,212,255,0.13); }
      .f3-search { flex:1 1 150px; min-width:120px; background:#0b1220; color:#e6f0ff; border:1px solid #24406a; border-radius:7px; padding:6px 9px; font-size:12px; min-height:30px; }
      .f3-search:focus { outline:none; border-color:#00d4ff; }
      .f3-tool { font-size:11px; padding:5px 10px; min-height:30px; }
      .f3-stage { position:relative; height:calc(100vh - 322px); min-height:380px; border-radius:12px; overflow:hidden; border:1px solid rgba(80,140,220,0.25); background:#000103; }
      .f3-glass { position:absolute; background:rgba(4,8,16,0.82); border:1px solid rgba(80,140,220,0.25); border-radius:9px; color:#dbe8fa; font-size:11px; }
      /* The legend sits above the scene, never over it, so it cannot hide a
         group's heading in any view. */
      .f3-legend { display:flex; gap:4px; align-items:center; overflow-x:auto; margin:-2px 0 6px; padding:2px 0; scrollbar-width:thin; }
      .f3-legend button { display:inline-flex; align-items:center; gap:6px; flex:none; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); color:#c6d6ea; font-size:11px; padding:3px 9px; border-radius:999px; cursor:pointer; white-space:nowrap; }
      .f3-legend button:hover { background:rgba(255,255,255,0.07); }
      .f3-legend button.off { opacity:.35; }
      .f3-legend .sw { width:9px; height:9px; border-radius:3px; flex:none; }
      .f3-legend .n { font-family:'JetBrains Mono',monospace; color:#7f93ad; font-size:10px; }
      .f3-status { left:10px; bottom:8px; padding:3px 8px; font-size:10px; color:#8fa3bf; pointer-events:none; }
      .f3-status.warn { color:#ffd699; border-color:rgba(245,158,11,0.5); }
      .f3-help { right:10px; top:10px; padding:12px 14px; width:min(300px, 60%); z-index:4; line-height:1.7; }
      .f3-help kbd { font-family:'JetBrains Mono',monospace; background:#16213a; border:1px solid #2b4470; border-radius:4px; padding:0 5px; color:#fff; margin-right:6px; font-size:10px; }
      .f3-detail { top:10px; right:10px; bottom:10px; width:min(360px, 44%); display:flex; flex-direction:column; gap:10px; padding:14px; border-color:rgba(0,212,255,0.35); font-size:12px; overflow:auto; }
      .f3-detail h3 { margin:0; font-size:14px; line-height:1.35; color:#fff; }
      .f3-kv { display:grid; grid-template-columns:auto 1fr; gap:5px 12px; color:#b8c7dc; }
      .f3-kv .k { color:#6f86a6; }
      .f3-acts { display:flex; gap:6px; flex-wrap:wrap; margin-top:auto; padding-top:8px; border-top:1px solid rgba(255,255,255,0.07); }
      .f3-zones { left:10px; bottom:30px; width:fit-content; max-width:calc(100% - 20px); margin:0 auto; display:flex; gap:5px; flex-wrap:wrap; justify-content:center; align-items:center; padding:6px 8px; border-radius:12px; transition:border-color .15s, box-shadow .15s; }
      .f3-zones.live { border-color:rgba(0,212,255,0.6); box-shadow:0 0 26px rgba(0,212,255,0.22); }
      .f3-zones button { transition:transform .12s, background .12s, box-shadow .12s; }
      .f3-zones button.hot { transform:scale(1.12); background:rgba(0,212,255,0.25); border-color:#00d4ff; box-shadow:0 0 16px rgba(0,212,255,0.55); }
      .f3-toast { top:10px; left:50%; transform:translateX(-50%); display:flex; gap:10px; align-items:center; padding:7px 12px; font-size:12px; max-width:70%; z-index:3; }
      .f3-toast.err { color:#ffb4b4; border-color:rgba(239,68,68,0.6); }
      .f3-marks { top:10px; left:50%; transform:translateX(-50%); display:flex; gap:8px; align-items:center; padding:5px 10px; font-size:12px; color:#ffd1ea; border-color:rgba(255,0,128,0.55); }
    `;
    document.head.appendChild(st);
  }

  function Records3DPanel({ source, onClose }) {
    const src = SOURCES[source];
    const mountRef = useRef(null), engRef = useRef(null), boxRef = useRef(null);
    const [params, setParams] = useState(() => Object.assign({}, src.params || {}));
    const ckey = source + ':' + JSON.stringify(params);
    const cached = CACHE.get(ckey);
    const [recs, setRecs] = useState(cached ? cached.recs : null);
    const [readAt, setReadAt] = useState(cached ? cached.at : 0);
    const [busy, setBusy] = useState(true);
    const [err, setErr] = useState('');
    const [view, setView] = useState(src.views[0]);
    const [grouping, setGrouping] = useState(Object.keys(src.groupings)[0]);
    const [sortKey, setSortKey] = useState(src.sorts ? Object.keys(src.sorts)[0] : '');
    const [filters, setFilters] = useState(() => { const o = {}; (src.filters || []).forEach(f => { o[f.id] = !!f.on; }); return o; });
    const [solo, setSolo] = useState(null);
    const [query, setQuery] = useState('');
    const [sel, setSel] = useState(null);
    const [hover, setHover] = useState(null);
    const [dazzle, setDazzle] = useState('full');
    const [stats, setStats] = useState(null);
    const [help, setHelp] = useState(false);
    const [legend, setLegend] = useState([]);
    const itemsRef = useRef([]), groupsRef = useRef([]);
    const zones = src.zones || null;
    const [marks, setMarks] = useState(() => new Set());
    const marksRef = useRef(marks); marksRef.current = marks;
    const [carry, setCarry] = useState(null);
    const carryRef = useRef(null); carryRef.current = carry;
    const [toast, setToast] = useState(null);
    const anchorRef = useRef(-1), maskRef = useRef(null), undoRef = useRef([]), retileRef = useRef(new Set()), fnRef = useRef({});
    const selRef = useRef(null); selRef.current = sel;

    // Read first, before the engine is built: the request is in flight while
    // the GPU sets up, not queued behind it. A source may hand over a first
    // part early (partial), shown at once. A failed refresh over a remembered
    // copy says so and keeps the copy, labelled with its age.
    const seq = useRef(0);
    const load = force => {
      const n = ++seq.current;
      setErr(''); setBusy(true);
      if (force) PENDING.delete(ckey);
      const partial = list => { if (n === seq.current && list && list.length) setRecs(list); };
      fetchRecs(source, params, partial).then(list => {
        if (n !== seq.current) return;
        const c = CACHE.get(ckey);
        setRecs(list); setReadAt(c ? c.at : Date.now()); setBusy(false);
      }, e => {
        if (n !== seq.current) return;
        setErr(String((e && e.message) || e || 'Could not load')); setBusy(false);
        setRecs(r => r || []);
      });
    };
    useEffect(() => {
      const c = CACHE.get(ckey);
      if (c) { setRecs(c.recs); setReadAt(c.at); } else { setRecs(null); setReadAt(0); }
      // a copy read moments ago (the hover that led here) is not read again
      if (c && Date.now() - c.at < FRESH_MS && !PENDING.has(ckey)) { setBusy(false); return; }
      load();
    }, [ckey]);

    useEffect(() => {
      try { localStorage.setItem('friday_3d_used', '1'); } catch (_) {}
      let eng;
      try {
        eng = F.createEngine(mountRef.current, {
          onHover: (it, p) => setHover(it && p ? { it, x: p.x, y: p.y } : null),
          onPick: (i, activate, mods) => {
            const it = itemsRef.current[i];
            if (zones && it && mods && (mods.add || mods.range)) { fnRef.current.mark(i, mods.range); return; }
            if (marksRef.current.size) setMarks(new Set());
            if (it) anchorRef.current = i;
            if (!it) { setSel(null); eng.select(-1); return; }
            setSel(it); eng.select(i, true);
            if (activate) src.open(it.rec);
          },
          onStep: d => { const cur = itemsRef.current.indexOf(selRef.current); const j = eng.step(cur, d); if (j >= 0) { setSel(itemsRef.current[j]); eng.select(j, true); } },
          onInteract: () => boxRef.current && boxRef.current.focus({ preventScroll: true }),
          onItemDrag: zones ? (phase, i, x, y) => fnRef.current.carry(phase, i, x, y) : undefined
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
    useEffect(() => { engRef.current && engRef.current.setDazzle(dazzle); }, [dazzle]);


    // records -> cards. Cards arrive group by group in the workspace's own
    // order (item.ord), so every view is laid out the way the workspace
    // sorts; the same ids keep their place, so a regroup or re-sort glides.
    useEffect(() => {
      const eng = engRef.current;
      if (!eng || !recs) return;
      const g = src.groupings[grouping];
      const cats = {}, counts = {};
      const keyOf = rec => String(g.key(rec) || 'other');
      recs.forEach(rec => { const k = keyOf(rec); counts[k] = (counts[k] || 0) + 1; if (!cats[k]) cats[k] = Object.assign({ color: colorFor(k), label: k, ico: '•' }, g.style ? g.style(k, rec) : {}); });
      const rank = k => (cats[k].rank != null ? cats[k].rank : 1000 - Math.min(999, counts[k]));
      const cmp = src.sorts && src.sorts[sortKey] ? src.sorts[sortKey].cmp : () => 0;
      const order = recs.map((rec, k) => k).sort((a, b) => {
        const ka = keyOf(recs[a]), kb = keyOf(recs[b]);
        return rank(ka) - rank(kb) || (ka < kb ? -1 : ka > kb ? 1 : 0) || cmp(recs[a], recs[b]) || a - b;
      });
      const ordOf = new Int32Array(recs.length);
      order.forEach((k, pos) => { ordOf[k] = pos; });
      const list = recs.map((rec, i) => {
        const base = src.toItem(rec);
        const key = keyOf(rec);
        return { i, rel: String(base.id != null ? base.id : i), name: base.title || '(untitled)', dir: false,
          size: base.weight || 0, mtime: base.time || 0, dur: base.dur || 0, ext: '', cat: 'rec:' + source + ':' + key, ord: ordOf[i],
          parent: -1, depth: 1, kids: [], card: { title: base.title || '(untitled)', sub: base.sub || '', badge: base.badge || '' },
          strip: base.strip || base.title, img: base.img || null, rec, key };
      });
      const reg = {};
      Object.keys(cats).forEach(k => { reg['rec:' + source + ':' + k] = Object.assign({}, cats[k], { rank: rank(k) }); });
      F.registerCats(reg);
      itemsRef.current = list;
      if (eng.setIntro) eng.setIntro(src.intro || 'center');
      eng.setData(list, 'rec:' + source + ':' + JSON.stringify(params), '');
      eng.setGroupBy('type');
      if (retileRef.current.size) {
        list.forEach((it, k) => { if (retileRef.current.has(it.rel)) eng.retile(k, it.rel, it.name); });
        retileRef.current.clear();
      }
      groupsRef.current = Object.keys(cats).sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : 1)).map(k => ({ key: k, label: cats[k].label || k, color: cats[k].color }));
      setSel(null);
    }, [recs, grouping, sortKey]);
    useEffect(() => {
      const eng = engRef.current;
      if (!eng || !eng.setMarked) return;
      eng.setMarked(itemsRef.current.map((it, k) => marks.has(it.rel) ? k : -1).filter(k => k >= 0));
    }, [marks, recs, grouping, sortKey]);
    useEffect(() => { engRef.current && engRef.current.setView(view); }, [view]);

    // What is shown: the workspace's filters, the legend's one group, and
    // the search, together. Hidden cards shrink away; nothing is re-read.
    const shownCount = useRef(0);
    useEffect(() => {
      const eng = engRef.current, list = itemsRef.current;
      if (!eng) return;
      if (!list.length) { setLegend([]); return; }
      const q = query.trim().toLowerCase(), words = q ? q.split(/\s+/) : [];
      const active = (src.filters || []).filter(f => filters[f.id]);
      const counts = {};
      list.forEach(it => { if (!active.some(f => !f.test(it.rec))) counts[it.key] = (counts[it.key] || 0) + 1; });
      setLegend(groupsRef.current.filter(g => counts[g.key]).map(g => Object.assign({ n: counts[g.key] }, g)));
      if (solo && !counts[solo]) { setSolo(null); return; }
      const t = setTimeout(() => {
        if (!words.length && !active.length && !solo) { maskRef.current = null; shownCount.current = list.length; eng.setFilter(null); return; }
        const mask = new Uint8Array(list.length);
        let c = 0;
        list.forEach((it, i) => {
          if (solo && it.key !== solo) return;
          if (active.some(f => !f.test(it.rec))) return;
          if (words.length) { const hay = (it.card.title + ' ' + it.card.sub + ' ' + it.card.badge).toLowerCase(); if (!words.every(w => hay.includes(w))) return; }
          mask[i] = 1; c++;
        });
        maskRef.current = mask; shownCount.current = c;
        eng.setFilter(mask);
      }, words.length ? 150 : 0);
      return () => clearTimeout(t);
    }, [query, recs, grouping, sortKey, filters, solo]);

    // ── acting on cards (sources with zones) ──
    const relIndex = rel => itemsRef.current.findIndex(x => x.rel === rel);
    const targets = () => {
      const list = itemsRef.current, m = marksRef.current;
      if (m.size) return list.filter(x => m.has(x.rel));
      return selRef.current ? [selRef.current] : [];
    };
    fnRef.current.mark = (i, range) => {
      const list = itemsRef.current, mask = maskRef.current;
      setMarks(prev => {
        const next = new Set(prev);
        if (!next.size && selRef.current) next.add(selRef.current.rel);   // the open card counts as picked
        if (range && anchorRef.current >= 0) {
          const a = Math.min(list[anchorRef.current] ? list[anchorRef.current].ord : 0, list[i].ord), b = Math.max(list[anchorRef.current] ? list[anchorRef.current].ord : 0, list[i].ord);
          list.forEach((it, k) => { if (it.ord >= a && it.ord <= b && (!mask || mask[k])) next.add(it.rel); });
        } else if (list[i]) { const r = list[i].rel; if (next.has(r)) next.delete(r); else next.add(r); }
        return next;
      });
      anchorRef.current = i;
    };
    const zoneAt = (x, y) => {
      const el = document.elementsFromPoint(x, y).find(e => e.dataset && e.dataset.zone);
      return el ? zones.find(z => z.id === el.dataset.zone) : null;
    };
    fnRef.current.carry = (phase, i, x, y) => {
      const list = itemsRef.current, it = list[i];
      if (phase === 'start' && it) {
        const m = marksRef.current;
        setCarry({ group: m.has(it.rel) ? list.filter(g => m.has(g.rel)) : [it], x, y, hot: zoneAt(x, y) });
      } else if (phase === 'move') {
        const hot = zoneAt(x, y);
        setCarry(c => c && Object.assign({}, c, { x, y, hot }));
      } else if (phase === 'end') {
        const z = zoneAt(x, y), c = carryRef.current;
        setCarry(null);
        if (c && z) perform(c.group, z);
      } else setCarry(null);
    };
    // Nothing animates as done until the server says it is done: the cards
    // hover amber while asked, then fly off / spin / settle into their new
    // place, or shake if the change was refused. Every zone can be undone.
    const busyRef = useRef(false);
    const perform = (group, z) => {
      const eng = engRef.current;
      if (!eng || !group.length || busyRef.current) return;
      const fit = z.when ? group.filter(g => z.when(g.rec)) : group;
      if (!fit.length) { setToast({ text: z.whenNot || 'That does not apply here.', err: true }); return; }
      group = fit;
      busyRef.current = true;
      group.forEach(g => eng.setHeld(relIndex(g.rel), true));
      src.act(group.map(g => g.rec), z).then(res => {
        const ok = new Set((res.okIds || []).map(String));
        const anims = group.map(g => {
          const k = relIndex(g.rel);
          eng.setHeld(k, false);
          if (!ok.has(String(g.rec.id))) return eng.fx('fail', k);
          return z.away ? eng.fx('move', k, { dest: -1 }) : eng.fx(z.fx || 'rename', k);
        });
        return Promise.all(anims).then(() => {
          busyRef.current = false;
          if (!ok.size) { setToast({ text: 'Nothing changed: ' + (res.error || 'the change was refused.'), err: true }); return; }
          if (!z.away) group.forEach(g => ok.has(String(g.rec.id)) && retileRef.current.add(g.rel));
          setRecs(rs => { const next = z.away ? rs.filter(r => !ok.has(String(r.id))) : rs.map(r => ok.has(String(r.id)) ? Object.assign({}, r, z.patch) : r); const c = CACHE.get(ckey); if (c) CACHE.set(ckey, { recs: next, at: c.at }); return next; });
          setMarks(new Set());
          const u = { before: res.before, rels: group.map(g => g.rel) };
          undoRef.current.push(u);
          const failed = group.length - ok.size;
          const noun = ok.size > 1 ? ' · ' + ok.size + ' ' + (src.noun || 'items') : '';
          setToast({ text: z.done + noun + ((res.note || src.zoneNote) ? ' (' + (res.note || src.zoneNote) + ')' : '') + (failed ? ' · ' + failed + ' not changed: ' + res.error : ''), undo: u, err: !!failed });
        });
      }).catch(() => { busyRef.current = false; group.forEach(g => eng.setHeld(relIndex(g.rel), false)); setToast({ text: 'Nothing changed: Friday could not be reached.', err: true }); });
    };
    const undo = u => {
      u = u || undoRef.current.pop();
      if (!u) { setToast({ text: 'Nothing to undo.' }); return; }
      undoRef.current = undoRef.current.filter(x => x !== u);
      src.undo(u.before).then(ok => {
        if (!ok) { setToast({ text: 'Undo failed: nothing was changed back.', err: true }); return; }
        u.rels.forEach(r => retileRef.current.add(r));
        setToast({ text: 'Undone.' });
        load(true);
      });
    };
    useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), toast.undo ? 9000 : 4000); return () => clearTimeout(t); }, [toast]);

    const onKey = e => {
      const eng = engRef.current;
      if (!eng || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      const cur = itemsRef.current.indexOf(sel);
      const dir = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[e.key];
      const vi = '123456789'.indexOf(e.key);
      if (dir) { const j = eng.nav(cur, dir); if (j >= 0) { setSel(itemsRef.current[j]); eng.select(j, true); } }
      else if (vi >= 0 && vi < src.views.length) setView(src.views[vi]);
      else if (e.key === 'Enter' && sel) src.open(sel.rec);
      else if (zones && (e.key === 'z' || e.key === 'Z') && !e.ctrlKey) undo();
      else if (zones && e.key === 'x' && sel) fnRef.current.mark(itemsRef.current.indexOf(sel), false);
      else if (zones && src.keys && src.keys[e.key] && targets().length) perform(targets(), zones.find(z => z.id === src.keys[e.key]));
      else if (e.key === '?') setHelp(v => !v);
      else if (e.key === 'Escape') { if (help) setHelp(false); else if (marks.size) setMarks(new Set()); else if (solo) setSolo(null); else if (sel) { setSel(null); eng.select(-1); } else onClose(); }
      else if (e.key === 'r' || e.key === 'R') eng.resetCamera();
      else return;
      e.preventDefault(); e.stopPropagation();
    };

    const total = recs ? recs.length : 0;
    const shown = stats ? stats.visible : total;
    const ctx = { params, setParams, reload: load };
    const status = err && recs && recs.length ? { warn: true, text: 'Could not refresh: ' + err + ' · showing the copy from ' + ago(readAt) }
      : busy && recs && readAt ? { text: 'Showing the copy from ' + ago(readAt) + ' · refreshing…' }
        : busy && recs ? { text: 'Loading more…' } : null;
    const noun = src.noun || 'items';
    return h('div', { ref: boxRef, tabIndex: 0, onKeyDown: onKey, className: 'f3' },
      h('div', { className: 'f3-bar' },
        h('div', { className: 'f3-seg', role: 'group', 'aria-label': 'Views' },
          src.views.map((v, i) => h('button', { key: v, className: view === v ? 'on' : '', 'aria-pressed': view === v, onClick: () => setView(v), title: (V[v].tip || V[v].label) + ' (' + (i + 1) + ')' }, V[v].ico + ' ' + V[v].label))),
        Object.keys(src.groupings).length > 1 && h('label', { className: 'f3-field' }, 'Group',
          h('select', { value: grouping, onChange: e => setGrouping(e.target.value) }, Object.keys(src.groupings).map(k => h('option', { key: k, value: k }, src.groupings[k].label)))),
        src.sorts && h('label', { className: 'f3-field' }, 'Sort',
          h('select', { value: sortKey, onChange: e => setSortKey(e.target.value) }, Object.keys(src.sorts).map(k => h('option', { key: k, value: k }, src.sorts[k].label)))),
        (src.filters || []).map(f => h('span', { key: f.id, className: 'f3-chip' + (filters[f.id] ? ' on' : ''), role: 'switch', 'aria-checked': !!filters[f.id], tabIndex: 0,
          onClick: () => setFilters(o => Object.assign({}, o, { [f.id]: !o[f.id] })), title: f.tip || f.label }, (filters[f.id] ? '✓ ' : '') + f.label)),
        h('input', { className: 'f3-search', value: query, onChange: e => setQuery(e.target.value), placeholder: 'Search ' + noun + '…', 'aria-label': 'Search in 3D' }),
        (src.tools || []).map(t => h('button', { key: t.id, className: 'btn btn-magenta f3-tool', onClick: () => t.run(ctx), title: t.tip || t.label, disabled: t.disabled ? t.disabled(ctx) : false }, t.label)),
        h('button', { className: 'btn btn-magenta f3-tool', onClick: () => load(true), title: 'Read again' }, '↻'),
        h('button', { className: 'btn btn-magenta f3-tool', onClick: () => setHelp(v => !v), title: 'Mouse and keys (?)', 'aria-pressed': help }, '?')),
      legend.length > 1 && h('div', { className: 'f3-legend', role: 'group', 'aria-label': 'Groups' },
        legend.map(g => h('button', { key: g.key, className: solo && solo !== g.key ? 'off' : '', 'aria-pressed': solo === g.key, onClick: () => setSolo(s => s === g.key ? null : g.key), title: solo === g.key ? 'Show every group' : 'Show only ' + g.label },
          h('span', { className: 'sw', style: { background: hex(g.color) } }), clip(g.label, 28), h('span', { className: 'n' }, g.n)))),
      h('div', { className: 'f3-stage' },
        h('div', { ref: mountRef, style: { position: 'absolute', inset: 0 } }),
        (err && !(recs && recs.length)) && h('div', { className: 'f3-glass', role: 'alert', style: { top: 12, left: 12, padding: '7px 11px', color: '#ff9a9a', fontSize: 12, borderColor: 'rgba(239,68,68,0.5)' } }, '⚠ ' + err + ' This is not an empty ' + (src.label || '').toLowerCase() + ' view: the read failed.'),
        recs === null && !err && h('div', { className: 'f3-glass', style: { top: 12, left: 12, padding: '6px 10px', color: '#9fd0ff', fontSize: 12 } }, 'Reading ' + (src.label || '').toLowerCase() + '…'),
        recs && !recs.length && !err && !busy && h('div', { style: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#7f93ad', fontSize: 13 } }, src.empty),
        carry && h('div', { style: { position: 'fixed', left: carry.x + 14, top: carry.y + 10, pointerEvents: 'none', zIndex: 60, padding: '6px 10px', borderRadius: 8, background: 'rgba(6,10,18,0.94)', border: '1px solid ' + (carry.hot ? '#00d4ff' : '#ff0080'), color: '#e6f0ff', fontSize: 12, boxShadow: '0 6px 24px rgba(0,0,0,0.5)', maxWidth: 280 } },
          (src.carryIco || '▣') + ' ' + (carry.group.length > 1 ? carry.group.length + ' ' + noun : clip(carry.group[0].card.title, 60)) + (carry.hot ? ' → ' + carry.hot.label : '')),
        !carry && hover && hover.it && hover.it !== sel && h('div', { style: { position: 'fixed', left: hover.x + 14, top: hover.y + 12, pointerEvents: 'none', padding: '5px 8px', borderRadius: 6, background: 'rgba(6,10,18,0.92)', border: '1px solid #2e5a8f', color: '#e6f0ff', fontSize: 11, zIndex: 50, maxWidth: 300 } },
          h('div', { style: { fontWeight: 600 } }, clip(hover.it.card.title, 90)), hover.it.card.sub && h('div', { style: { color: '#8fa6c4' } }, clip(hover.it.card.sub, 120))),
        h('div', { className: 'f3-glass f3-status' + (status && status.warn ? ' warn' : '') },
          (recs ? (shown < total ? shown + ' of ' + total + ' ' + noun : total + ' ' + noun) : '') + (status ? ' · ' + status.text : readAt ? ' · read ' + ago(readAt) : '') + (stats ? ' · ' + stats.fps + ' fps' : '')),
        zones && marks.size > 0 && h('div', { className: 'f3-glass f3-marks' },
          marks.size + ' selected · drag them onto a zone or click one',
          h('button', { className: 'btn f3-tool', onClick: () => setMarks(new Set()) }, 'Clear')),
        zones && h('div', { role: 'toolbar', 'aria-label': 'Drop zones', className: 'f3-glass f3-zones' + (carry ? ' live' : ''), style: { right: sel ? 'calc(min(360px, 44%) + 20px)' : 10 } },
          zones.map((z, k) => {
            const hot = carry && carry.hot && carry.hot.id === z.id;
            const divider = z.kind === 'lane' && (k === 0 || zones[k - 1].kind !== 'lane');
            return [divider && h('span', { key: 'd' + k, style: { width: 1, alignSelf: 'stretch', background: 'rgba(120,160,220,0.3)', margin: '0 4px' } }),
              h('button', { key: z.id, 'data-zone': z.id, className: 'btn f3-tool' + (z.kind === 'lane' ? ' btn-magenta' : '') + (hot ? ' hot' : ''), title: z.tip || z.label,
                onClick: () => { const t = targets(); if (t.length) perform(t, z); else setToast({ text: 'Pick a card first, or drag one here.' }); } }, z.ico + ' ' + z.label)];
          })),
        toast && h('div', { role: 'status', className: 'f3-glass f3-toast' + (toast.err ? ' err' : '') },
          toast.text, toast.undo && h('button', { className: 'btn f3-tool', onClick: () => undo(toast.undo) }, 'Undo (Z)')),
        help && h('div', { className: 'f3-glass f3-help', role: 'dialog', 'aria-label': 'Mouse and keys' },
          h('div', { style: { fontWeight: 700, marginBottom: 4, color: '#9fe6ff' } }, (src.label || '') + ' in 3D'),
          src.blurb && h('div', { style: { color: '#9fb0c8', marginBottom: 6 } }, src.blurb),
          [[zones ? 'drag a card' : 'drag', zones ? 'carry it to a zone' : 'turn the view'], zones && ['drag space', 'turn the view'], ['right-drag', 'slide'], ['scroll', 'zoom'],
            zones && ['Ctrl/Shift-click', 'pick several'], ['1–' + src.views.length, 'switch view'], ['arrows', 'next card'], ['Enter', src.openLabel.toLowerCase()],
            zones && ['Z', 'undo'], ['R', 'recentre'], ['Esc', 'back / close']].filter(Boolean)
            .map(([k, d]) => h('div', { key: k }, h('kbd', null, k), d))),
        sel && h('div', { className: 'f3-glass f3-detail' },
          h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8 } },
            h('h3', null, sel.card.title),
            h('button', { className: 'btn f3-tool', onClick: () => { setSel(null); engRef.current && engRef.current.select(-1); }, 'aria-label': 'Close details' }, '✕')),
          sel.img && h('img', { src: sel.img, alt: '', style: { maxWidth: '100%', maxHeight: 160, objectFit: 'contain', borderRadius: 6 } }),
          h('div', { className: 'f3-kv' },
            src.detail(sel.rec).filter(r => r && r[1] != null && r[1] !== '').map(([k, v], j) => [h('div', { key: 'k' + j, className: 'k' }, k), h('div', { key: 'v' + j, style: { wordBreak: 'break-word' } }, String(v))])),
          h('div', { className: 'f3-acts' },
            h('button', { className: 'btn f3-tool', onClick: () => src.open(sel.rec) }, '↗ ' + src.openLabel),
            (zones || []).filter(z => z.inDetail && (!z.when || z.when(sel.rec))).map(z => h('button', { key: z.id, className: 'btn btn-magenta f3-tool', onClick: () => perform([sel], z) }, z.ico + ' ' + z.label))))));
  }

  // The bar every wrapped workspace gets. The workspace itself stays mounted
  // underneath (hidden) so switching back is instant and loses nothing.
  function Friday3DToggle({ ws, source, children }) {
    ws = ws || source;
    // A link that says view3d (e.g. /w/news?view3d=1) is read here, while the
    // bar is created: the workspace inside reads the same nav target in its
    // own effect, which runs first and clears it.
    const [on, setOn] = useState(() => {
      const t = window.__fridayNavTarget;
      if (t && t.workspace === ws && t.view3d != null) return !!t.view3d;
      try { return localStorage.getItem('friday_3d_on_' + ws) === '1'; } catch (_) { return false; }
    });
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
    // The engine's GPU set-up starts while the pointer is still on the way
    // to the button, not after the click.
    const warm = () => { if (source !== 'code') prefetch(source); if (F.prewarm) setTimeout(F.prewarm, 0); };
    return h(React.Fragment, null,
      h('div', { style: { display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 6, marginBottom: on ? 6 : 4 } },
        on && h('span', { style: { fontFamily: 'Orbitron, Inter, sans-serif', fontSize: 10, letterSpacing: '.12em', color: '#00d4ff', marginRight: 'auto' } }, '🧊 ' + String(src.label).toUpperCase() + ' IN 3D'),
        h('button', { className: 'btn' + (on ? '' : ' btn-magenta'), style: { fontSize: 11, padding: '4px 9px' }, onPointerEnter: on ? undefined : warm, onFocus: on ? undefined : warm, onClick: () => set(!on), title: on ? 'Back to the normal view' : 'See this workspace in 3D' }, on ? '✕ Close 3D' : '🧊 View in 3D')),
      on && (source === 'code' ? h(window.Files3DPanel, { root: 'projects', path: '', view: 'city' }) : h(Records3DPanel, { source, onClose: () => set(false) })),
      h('div', { style: on ? { display: 'none' } : null }, children));
  }

  // Someone who uses 3D gets the engine built while the app is idle, so even
  // the first view of the session opens without the GPU set-up pause.
  try {
    const used = localStorage.getItem('friday_3d_used') === '1' || Object.keys(localStorage).some(k => /^friday_3d_on_/.test(k) && localStorage.getItem(k) === '1');
    if (used && F.prewarm) {
      const go = () => (window.requestIdleCallback ? requestIdleCallback(() => F.prewarm(), { timeout: 4000 }) : F.prewarm());
      setTimeout(go, 6000);
    }
  } catch (_) { /* storage blocked: build on first use */ }

  window.Friday3DToggle = Friday3DToggle;
  window.__friday3dRecords = { SOURCES, secs, clip, colorFor };
})();
