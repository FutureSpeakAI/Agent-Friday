/* The action ledger in Activity (services/action_ledger.py, routes/ledger.py).
 *
 * Activity reads one store: GET /api/ledger. Every row there is one entry of
 * the hash-chained ledger: activity rows (model calls, tool calls, spawns,
 * traces) and the receipts kept in the legacy receipt files
 * (checkpoint decisions, changes to the owner's things, goal receipts, dissent,
 * pre-approved sends, website trust, inbox cleanup, media diet).
 *
 * Defines:
 *   window.fridayLedgerEvents(data)  -> rows in the shape Activity draws
 *   window.FridayLedgerStrip         -> the state line at the top of Activity:
 *     "Ledger intact" with the count, or what changed on disk, a Review list
 *     and the owner's acknowledgement (a click on this page only).
 */
(function () {
  'use strict';
  if (window.FridayLedgerStrip) return;
  const h = React.createElement;
  const { useState } = React;
  const MONO = "'JetBrains Mono', monospace";

  function summaryOf(kind, r) {
    r = r || {};
    switch (kind) {
      case 'decision':
        return [r.tool, r.decision, r.surface].filter(Boolean).join(' · ');
      case 'action':
        return [r.tool, r.summary, r.status, r.undone ? 'undone' : ''].filter(Boolean).join(' · ');
      case 'goal':
        return [r.milestone_name || r.what_ran, r.kind].filter(Boolean).join(' · ');
      case 'dissent':
        return [r.category, r.severity, r.proceed === false ? 'did not proceed' : ''].filter(Boolean).join(' · ');
      case 'send_grant':
        return [r.event, r.grant_id].filter(Boolean).join(' · ');
      case 'web_site':
        return [r.event, r.site].filter(Boolean).join(' · ');
      case 'inbox_cleanup':
      case 'media_diet':
        return r.event || '';
      case 'migration':
        return (r.lines != null ? r.lines + ' lines from ' : '') + (r.from || '');
      case 'integrity_ack':
        return ((r.problems || []).length) + ' break(s) reviewed';
      default:
        return '';
    }
  }

  // Rows for Activity's list. Activity rows keep their own fields (Activity
  // draws them as before); every other entry carries its label and a summary.
  window.fridayLedgerEvents = function (d) {
    const rows = (d && d.entries) || [];
    return rows.map(function (e) {
      if (e.kind === 'activity') {
        return Object.assign({}, e.record || {}, { seq: e.seq });
      }
      return {
        kind: e.kind, ledger_kind: e.kind, label: e.label, seq: e.seq,
        ts: e.t, summary: summaryOf(e.kind, e.record), migrated: !!e.migrated,
        legacy_verified: e.migrated ? e.migrated.legacy_verified : null,
        task_id: (e.record || {}).task_id,
      };
    });
  };

  window.FridayLedgerStrip = function FridayLedgerStrip({ ledger }) {
    const [report, setReport] = useState(null);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState('');
    if (!ledger) return null;
    const ok = ledger.ok !== false;
    const review = () => {
      setBusy(true);
      fetch('/api/ledger/verify').then(r => r.json()).then(d => { setReport(d.report || null); setBusy(false); })
        .catch(() => { setBusy(false); setNote('The ledger could not be checked.'); });
    };
    const acknowledge = () => {
      setBusy(true);
      const shown = ((report && report.problems) || []).filter(p => !p.acknowledged);
      fetch('/api/ledger/acknowledge', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewed: shown }) })
        .then(r => r.json().then(d => ({ ok: r.ok, d })))
        .then(({ ok, d }) => {
          setBusy(false);
          if (!ok) { setNote(d.error || 'Not recorded.'); if (d.code === 'review_again') review(); return; }
          setReport(d.report || null);
          setNote('Recorded. Outward actions can go ahead again.');
        }).catch(() => { setBusy(false); setNote('Not recorded.'); });
    };
    const line = ok
      ? 'Ledger intact · ' + (ledger.records || 0) + ' records' + (ledger.archived_records ? ' · ' + ledger.archived_records + ' archived' : '') + (ledger.key_available === false ? ' · signatures not checked (key unavailable)' : '')
      : 'The ledger was changed on disk · outward actions are held';
    return h('div', { 'data-testid': 'ledger-strip', role: 'status',
      style: { padding: '5px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)', fontSize: 9, fontFamily: MONO,
        color: ok ? '#7fd8a8' : '#fbbf24', background: ok ? 'transparent' : 'rgba(251,191,36,0.07)' } },
      h('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
        h('span', { style: { flex: 1 } }, line),
        h('a', { href: '/ledger', target: '_blank', rel: 'noopener', 'data-testid': 'ledger-open',
          style: { color: '#00d4ff', fontSize: 9, fontFamily: MONO } }, 'Open the Ledger'),
        h('button', { onClick: review, disabled: busy, 'data-testid': 'ledger-review',
          style: { background: 'none', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 4, color: '#ccd', cursor: 'pointer', fontSize: 9, fontFamily: MONO, padding: '1px 6px' } },
          ok ? 'Check now' : 'Review')),
      report && h('div', { style: { marginTop: 4 } },
        (report.problems || []).length === 0
          ? h('div', null, 'Checked ' + (report.records || 0) + ' records: every one is as Friday wrote it.')
          : h('div', null,
              (report.problems || []).map((p, i) => h('div', { key: i, style: { color: p.acknowledged ? '#889' : '#fbbf24' } },
                (p.segment || '') + (p.seq != null ? ' · record ' + p.seq : '') + ' · ' + p.reason + (p.acknowledged ? ' (you reviewed this)' : ''))),
              report.open_problem_count > 0 && h('button', { onClick: acknowledge, disabled: busy, 'data-testid': 'ledger-acknowledge',
                style: { marginTop: 4, background: 'rgba(251,191,36,0.12)', border: '1px solid rgba(251,191,36,0.4)', borderRadius: 4, color: '#fbbf24', cursor: 'pointer', fontSize: 9, fontFamily: MONO, padding: '2px 8px' } },
                'I have looked at this: let Friday act again'))),
      note && h('div', { style: { marginTop: 3, color: '#aab' } }, note));
  };

  // Settings > Privacy & Data > Action record (amendment A22): how long entries
  // stay live before they move to the encrypted archive (kept, never deleted).
  window.FridayLedgerRetentionRow = function FridayLedgerRetentionRow() {
    const [a, setA] = useState(null);
    const [note, setNote] = useState('');
    const get = (window.apiFetch || fetch);
    const load = () => get('/api/ledger/retention').then(r => r.json()).then(d => setA(d.archive || null)).catch(() => setA(null));
    React.useEffect(() => { load(); }, []);
    const choose = v => {
      setNote('');
      get('/api/ledger/retention', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days: Number(v) }) })
        .then(r => r.json().then(d => ({ ok: r.ok, d })))
        .then(({ ok, d }) => { setNote(ok ? 'Saved.' + (d.result && d.result.archived ? ' ' + d.result.records + ' older entries moved to the archive.' : '') : (d.error || 'Not saved.')); load(); })
        .catch(() => setNote('Not saved.'));
    };
    if (!a) return null;
    const opts = [[365, '1 year'], [730, '2 years'], [1825, '5 years'], [3650, '10 years'], [0, 'always (never archive)']];
    if (!opts.some(o => o[0] === a.after_days)) opts.push([a.after_days, a.after_days + ' days']);
    return h('div', { 'data-testid': 'ledger-retention', style: { fontSize: 11 } },
      h('label', null, 'Keep entries live for ',
        h('select', { className: 'input', 'aria-label': 'How long action record entries stay live', value: String(a.after_days), onChange: e => choose(e.target.value) },
          opts.map(o => h('option', { key: o[0], value: String(o[0]) }, o[1])))),
      h('div', { style: { color: '#999', marginTop: 3 } },
        'Older entries move into an encrypted archive. They are never deleted, every Friday Bundle and backup carries them, and they are still checked as part of the chain. ' +
        (a.files ? a.records + ' entries archived so far, through ' + (a.through_ts || '') + '.' : 'Nothing is archived yet.')),
      h('a', { href: '/ledger', target: '_blank', rel: 'noopener', style: { color: '#00d4ff' } }, 'Open the Ledger'),
      note && h('div', { role: 'status', style: { marginTop: 3, color: '#aab' } }, note));
  };
})();
