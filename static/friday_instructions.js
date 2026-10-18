/* Friday's instructions: the Settings section where the owner sees, compares
 * and undoes changes to her core instructions (routes/instructions.py).
 *
 * - Every section is listed with who changed it last (shipped, you, or a
 *   tested change you approved) and any health row: an override that was
 *   ignored, or one paused because an update changed the shipped text.
 * - "Show" opens a section: the shipped text and the text in use side by
 *   side, the difference, a paused override if there is one, and the history.
 * - "Undo last change" puts back the text before the last change. It is a
 *   click on this page only (the server refuses anything else).
 * - Sections carrying safety rules are read-only: they change only in a
 *   software update.
 *
 * Defines window.FridayInstructionsSettings; index.html reaches it through one
 * line in Settings.
 */
(function () {
  'use strict';
  if (window.FridayInstructionsSettings) return;
  const h = React.createElement;
  const { useState, useEffect, useCallback } = React;

  const api = (url, opts) => {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };
  const getJ = url => api(url).then(r => r.json());
  const postJ = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
    .then(r => r.json().then(j => ({ ok: r.ok, status: r.status, j })));

  const NAME = () => (typeof window.fridayName === 'function' ? window.fridayName() : 'Friday');
  const ACCENT = '#00d4ff', AMBER = '#f59e0b', MONO = "'JetBrains Mono', monospace";
  const pre = (text, label, testid) => h('div', { style: { flex: '1 1 260px', minWidth: 0 } },
    h('div', { style: { fontSize: 10, color: 'rgba(255,255,255,0.55)', marginBottom: 3, fontFamily: MONO } }, label),
    h('pre', { 'data-testid': testid, style: { fontFamily: MONO, fontSize: 10.5, color: '#dfe7ef', background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, padding: 6, margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 260, overflowY: 'auto' } }, text || '(empty)'));

  function who(row) {
    if (!row.changed_by || row.changed_by === 'shipped') return 'as shipped';
    if (row.changed_by === 'owner:ui') return 'changed by you';
    if (String(row.changed_by).indexOf('card:') === 0) return 'a tested change you approved';
    return 'changed (' + row.changed_by + ')';
  }

  function SectionDetail({ sid, onChanged }) {
    const [d, setD] = useState(null);
    const [msg, setMsg] = useState('');
    const [busy, setBusy] = useState(false);
    const load = useCallback(() => getJ('/api/instructions/' + encodeURIComponent(sid)).then(r => setD(r.section || null)).catch(() => setD(null)), [sid]);
    useEffect(() => { load(); }, [load]);
    if (!d) return h('div', { style: { fontSize: 11, color: 'rgba(255,255,255,0.5)', marginTop: 6 } }, 'Loading…');
    const undo = () => {
      if (busy) return;
      setBusy(true);
      postJ('/api/instructions/' + encodeURIComponent(sid) + '/undo', { decided_by: 'owner:ui' }).then(res => {
        setBusy(false);
        setMsg(res.ok ? 'Undone: the text before the last change is back in use.' : ((res.j && res.j.error) || 'Could not undo.'));
        load(); onChanged && onChanged();
      });
    };
    return h('div', { 'data-testid': 'instruction-detail', 'data-section': sid, style: { marginTop: 8 } },
      h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap' } },
        pre(d.shipped, 'Shipped with ' + NAME(), 'instruction-shipped'),
        pre(d.in_use, 'In use now', 'instruction-in-use')),
      d.diff ? pre(d.diff, 'Difference (- shipped, + in use)', 'instruction-diff') : null,
      d.stored_override && d.stored_override !== d.in_use ? h('div', { style: { marginTop: 6 } },
        h('div', { style: { fontSize: 11, color: AMBER } }, d.problem || 'A stored version is not in use.'),
        pre(d.stored_override, 'Your stored version (not in use)', 'instruction-paused')) : null,
      (d.history || []).length ? h('ul', { 'data-testid': 'instruction-history', style: { fontSize: 10.5, color: 'rgba(255,255,255,0.6)', margin: '6px 0 0', paddingLeft: 16 } },
        d.history.slice(-8).reverse().map((r, i) => h('li', { key: i }, new Date((r.at || 0) * 1000).toLocaleString() + ' · ' + r.action + ' by ' + r.actor + (r.reason ? ' · ' + r.reason : '')))) : null,
      !d.locked && (d.history || []).length ? h('button', { className: 'btn', 'data-testid': 'instruction-undo', style: { fontSize: 11, padding: '2px 12px', marginTop: 6 }, disabled: busy, onClick: undo }, 'Undo last change') : null,
      d.locked ? h('div', { style: { fontSize: 11, color: 'rgba(255,255,255,0.55)', marginTop: 6 } }, 'This is part of ' + NAME() + '\'s safety rules and can only change in a software update.') : null,
      msg ? h('div', { style: { fontSize: 11, color: ACCENT, marginTop: 4 } }, msg) : null);
  }

  function FridayInstructionsSettings() {
    const [rows, setRows] = useState(null);
    const [open, setOpen] = useState('');
    const refresh = useCallback(() => getJ('/api/instructions').then(r => setRows(r.sections || [])).catch(() => setRows([])), []);
    useEffect(() => { refresh(); }, [refresh]);
    const Section = window.StSection || (({ title, children }) => h('section', null, h('h3', null, title), children));
    return h(Section, { title: NAME() + "'s instructions", desc: 'The text that tells ' + NAME() + ' how to work. A change you approve on a card shows here; you can compare it with the shipped text and undo it.' },
      h('div', { 'data-testid': 'instruction-sections' },
        rows === null ? h('div', { style: { fontSize: 11, color: 'rgba(255,255,255,0.5)' } }, 'Loading…') :
          rows.map(r => h('div', { key: r.id, 'data-instruction': r.id, style: { padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' } },
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
              h('span', { style: { fontSize: 12, color: 'var(--st-label, #ddd)', flex: 1 } }, r.title),
              r.locked ? h('span', { style: { fontSize: 9, fontFamily: MONO, color: 'rgba(255,255,255,0.5)' } }, 'SAFETY · LOCKED') :
                h('span', { style: { fontSize: 10, color: r.changed_by && r.changed_by !== 'shipped' ? ACCENT : 'rgba(255,255,255,0.45)' } }, who(r)),
              h('button', { className: 'btn', style: { fontSize: 10, padding: '1px 8px' }, onClick: () => setOpen(open === r.id ? '' : r.id) }, open === r.id ? 'Hide' : 'Show')),
            r.problem ? h('div', { 'data-testid': 'instruction-problem', style: { fontSize: 11, color: AMBER, marginTop: 3 } }, r.problem) : null,
            open === r.id ? h(SectionDetail, { sid: r.id, onChanged: refresh }) : null))));
  }

  window.FridayInstructionsSettings = FridayInstructionsSettings;
})();
