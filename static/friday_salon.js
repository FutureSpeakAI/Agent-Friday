/* The Salon section of Settings → Connections (docs/design/active/vibe-coding-salon.md §4.7, §6.1 table;
 * Settings keeps its eight task-shaped tabs, tests/unit/test_settings_structure.py).
 *
 * Loaded after friday_bundles.js. Defines window.FridaySalonSettings: one
 * section per codebase with its header line, its seats (small edits, big
 * edits), whose key pays, its guest keys (add, remove), and what it runs in.
 *
 * A guest key is typed into a password field, posted once, and the field is
 * cleared; the page never receives a key back from the server, only the
 * label, provider, cap and date. Removal deletes the key and the line says so.
 * The default posture and the box backend are shown as what they are: they
 * are decisions recorded in the spec, not switches here.
 */
(function () {
  if (window.FridaySalonSettings) return;
  const h = React.createElement;
  const { useState, useEffect, useCallback } = React;
  const ACCENT = '#00d4ff', AMBER = '#f59e0b', DENY = '#ff0080';
  const MONO = 'JetBrains Mono, Consolas, monospace';
  const dim = 'rgba(255,255,255,0.5)';

  const api = (url, opts) => {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };
  const getJ = url => api(url).then(r => r.json());
  const send = (method, url, body) => api(url, { method, headers: { 'Content-Type': 'application/json' }, body: body == null ? undefined : JSON.stringify(body) })
    .then(r => r.json().then(j => ({ ok: r.ok, j })));

  const label = (text) => h('div', { style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', color: dim, margin: '10px 0 4px' } }, text);
  const btn = (text, onClick, color, extra) => h('button', Object.assign({ className: 'btn', style: { fontSize: 11, padding: '2px 10px', color: color || undefined }, onClick }, extra || {}), text);
  const field = (props) => h('input', Object.assign({ style: { fontFamily: MONO, fontSize: 11, padding: '4px 6px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(0,212,255,0.25)', borderRadius: 4, color: '#e6eef8', minWidth: 0 } }, props));

  function Codebase({ cb }) {
    const base = '/api/codebases/' + encodeURIComponent(cb.id);
    const [hdr, setHdr] = useState(null);
    const [keys, setKeys] = useState(cb_keys(cb));
    const [heavy, setHeavy] = useState('');
    const [small, setSmall] = useState('local');
    const [engine, setEngineState] = useState((cb.seats && cb.seats.engine) || 'friday');
    const setEngine = (eng) => send('POST', base + '/engine', { engine: eng }).then(({ ok, j }) => {
      if (!ok) { setNote(j.error || 'That did not work.'); return; }
      setEngineState(j.engine); setNote(j.engine === 'claude_agent'
        ? "Claude's agent edits this codebase now. It runs as a process on this PC and can read this PC's files while it works; the salon proxy injects the key."
        : 'Friday edits this codebase again.'); load();
    });
    const [form, setForm] = useState({ label: '', provider: 'anthropic', key: '', cap: '' });
    const [note, setNote] = useState('');
    const load = useCallback(() => {
      getJ(base + '/header').then(hd => { setHdr(hd); setHeavy(hd.heavy || ''); setSmall(hd.small || 'local'); }).catch(() => {});
      getJ(base + '/keys').then(d => setKeys(d.keys || [])).catch(() => {});
    }, [base]);
    useEffect(() => { load(); }, [load]);
    const setSeat = (which, model) => send('POST', base + '/seats', { which, model }).then(({ ok, j }) => { setNote(ok ? '' : (j.error || 'That did not work.')); load(); });
    const setKey = (profile) => send('POST', base + '/key', { profile }).then(({ ok, j }) => { setNote(ok ? '' : (j.error || 'That did not work.')); load(); });
    const addKey = () => {
      if (!form.label.trim() || !form.key.trim()) { setNote('A guest key needs a name for whose it is, and the key itself.'); return; }
      send('POST', base + '/keys', { label: form.label.trim(), provider: form.provider, key: form.key, cap_usd: form.cap ? Number(form.cap) : null })
        .then(({ ok, j }) => {
          setForm(f => Object.assign({}, f, { key: '' }));          // the key never stays in the page
          if (!ok) { setNote(j.error || 'Not added.'); return; }
          setNote(form.label.trim() + "'s key is stored for this codebase only. Say \"use " + form.label.trim() + "'s key\" or pick it below.");
          setForm({ label: '', provider: 'anthropic', key: '', cap: '' });
          load();
        });
    };
    const removeKey = (lbl) => send('DELETE', base + '/keys/' + encodeURIComponent(lbl)).then(({ ok, j }) => {
      setNote(ok ? lbl + "'s key was removed and deleted." : (j.error || 'Not removed.')); load();
    });
    return h('div', { className: 'card', 'data-salon-codebase': cb.id, style: { padding: 12, marginBottom: 10 } },
      h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' } },
        h('span', { style: { fontSize: 13, fontWeight: 600, color: '#f1f6fb' } }, cb.title),
        h('span', { style: { fontFamily: MONO, fontSize: 9, color: dim } }, (cb.template || 'folder') + ' · ' + (cb.tier || 'B0') + ' · the browser frame')),
      hdr ? h('div', { 'data-salon-header': cb.id, style: { fontFamily: MONO, fontSize: 10, color: hdr.red ? '#ff6b9d' : 'rgba(255,255,255,0.72)', marginTop: 4, lineHeight: 1.5 } }, hdr.text, hdr.red && hdr.note ? h('div', null, hdr.note) : null) : null,
      label('SEATS'),
      h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', fontSize: 11 } },
        h('span', null, 'Small edits:'),
        field({ value: small, onChange: e => setSmall(e.target.value), placeholder: "local, or a model as you say it", 'aria-label': 'Small-edit seat', style: { width: 160 } }),
        btn('Set', () => setSeat('small', small)),
        h('span', { style: { marginLeft: 8 } }, 'Big edits:'),
        field({ value: heavy, onChange: e => setHeavy(e.target.value), placeholder: "none yet, e.g. Opus 5.5", 'aria-label': 'Heavy seat', style: { width: 160 } }),
        btn('Set', () => setSeat('heavy', heavy)),
        heavy ? btn('Clear', () => setSeat('heavy', ''), dim) : null),
      label('ENGINE'),
      h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', fontSize: 11 } },
        btn('Friday', () => setEngine('friday'), engine === 'friday' ? ACCENT : undefined, { 'data-engine': 'friday', title: "Friday's own loop edits this codebase" }),
        btn("Claude's agent", () => setEngine('claude_agent'), engine === 'claude_agent' ? ACCENT : undefined, { 'data-engine': 'claude_agent', title: "Your Claude Code runs as a process on this PC, behind the salon proxy" }),
        h('span', { style: { fontFamily: MONO, fontSize: 9, color: dim } }, engine === 'claude_agent'
          ? "runs as a process on this PC and can read this PC's files; the key never enters its environment, the proxy injects it"
          : "Friday edits in her own loop; nothing runs outside her")),
      label('WHOSE KEY PAYS'),
      h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', fontSize: 11 } },
        btn('your key', () => setKey('mine'), hdr && hdr.key === 'mine' ? ACCENT : undefined, { 'data-key-profile': 'mine' }),
        keys.map(k => h('span', { key: k.label, style: { display: 'inline-flex', gap: 4, alignItems: 'center' } },
          btn(k.label + "'s key", () => setKey(k.label), hdr && hdr.key === k.label ? ACCENT : undefined, { 'data-key-profile': k.label }),
          h('span', { style: { fontFamily: MONO, fontSize: 9, color: dim } }, k.provider + (k.cap_usd ? ' · cap $' + Number(k.cap_usd).toFixed(2) : '')),
          btn('Remove', () => removeKey(k.label), DENY, { 'data-remove-key': k.label, title: 'Deletes the key from this PC' })))),
      label('ADD A GUEST KEY (THIS CODEBASE ONLY)'),
      h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' } },
        field({ value: form.label, onChange: e => setForm(f => Object.assign({}, f, { label: e.target.value })), placeholder: 'whose key (a name)', 'aria-label': 'Whose key', style: { width: 130 } }),
        h('select', { value: form.provider, onChange: e => setForm(f => Object.assign({}, f, { provider: e.target.value })), 'aria-label': 'Provider', style: { fontFamily: MONO, fontSize: 11 } },
          h('option', { value: 'anthropic' }, 'Anthropic')),
        field({ value: form.key, type: 'password', autoComplete: 'off', onChange: e => setForm(f => Object.assign({}, f, { key: e.target.value })), placeholder: 'the key', 'aria-label': 'The key', style: { width: 200 } }),
        field({ value: form.cap, onChange: e => setForm(f => Object.assign({}, f, { cap: e.target.value })), placeholder: 'cap $ (theirs, optional)', 'aria-label': 'Cap in dollars', style: { width: 120 } }),
        btn('Add', addKey, AMBER, { 'data-add-key': cb.id })),
      note ? h('div', { role: 'status', style: { fontSize: 11, color: '#ffd28a', marginTop: 6 } }, note) : null);
  }
  function cb_keys() { return []; }

  function FridaySalonSettings() {
    const [cbs, setCbs] = useState(null);
    useEffect(() => { getJ('/api/codebases').then(d => setCbs(d.codebases || [])).catch(() => setCbs([])); }, []);
    return h('div', { 'data-salon-settings': '1' },
      h('div', { className: 'card', style: { padding: 12, marginBottom: 10, fontSize: 11, lineHeight: 1.6, color: 'rgba(255,255,255,0.7)' } },
        h('div', { style: { fontFamily: 'Orbitron, Inter, sans-serif', fontSize: 9, letterSpacing: '.22em', color: ACCENT, marginBottom: 6 } }, 'THE SALON'),
        h('div', null, 'Default posture: ', h('b', null, 'announce'), '. Reads are announced in the chat and go ahead; anything that changes something outside asks first.'),
        h('div', null, 'Box: ', h('b', null, 'B0 · the browser frame'), '. A codebase runs in a sandboxed frame with no process and no install; other tiers arrive with later phases.'),
        h('div', null, 'Small edits go to the local model on this PC when one is resident; big ones to the seat you pick per codebase. Whose key pays is per codebase too.')),
      cbs == null ? h('div', { style: { fontSize: 11, color: dim } }, 'Reading your codebases…')
        : !cbs.length ? h('div', { style: { fontSize: 11, color: dim } }, 'No codebases yet. Start one with "+ Codebase" beside the chats.')
        : cbs.map(cb => h(Codebase, { key: cb.id, cb })));
  }
  window.FridaySalonSettings = FridaySalonSettings;
})();
