/* The tested-change card (approval kind `instruction_trial`, services/trials).
 *
 * What the owner decides on, in full:
 * - the recommendation, as a label only ("Recommended: keep current" or
 *   "Recommended: use the new way"); neither button is pre-selected;
 * - for every section or file the change touches, the text in use now and
 *   the text it would become, side by side, and the line-by-line difference;
 * - the evidence: per practice task, how often each way passed, which way
 *   it favoured, missing runs, and any new safety slip;
 * - where to undo it afterwards.
 * Two buttons: Keep current (declines) and Use new (approves). Only a click
 * here approves; the server refuses voice, typed and in-process approvals.
 *
 * Defines window.FridayTrialCard; ApprovalCardBody reaches it in one line.
 */
(function () {
  'use strict';
  if (window.FridayTrialCard) return;
  const h = React.createElement;
  const { useState } = React;
  const ACCENT = '#00d4ff', AMBER = '#f59e0b', OK = '#00ff80', MONO = "'JetBrains Mono', monospace";

  const box = (text, label, testid) => h('div', { style: { flex: '1 1 240px', minWidth: 0 } },
    h('div', { style: { fontSize: 10, color: 'rgba(255,255,255,0.55)', marginBottom: 3, fontFamily: MONO } }, label),
    h('pre', { 'data-testid': testid, style: { fontFamily: MONO, fontSize: 10.5, color: '#dfe7ef', background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, padding: 6, margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 280, overflowY: 'auto' } }, text || '(nothing)'));

  function DiffLines({ diff }) {
    const lines = String(diff || '').split('\n');
    return h('pre', { 'data-testid': 'trial-diff', style: { fontFamily: MONO, fontSize: 10.5, background: 'rgba(0,0,0,0.25)', borderRadius: 4, padding: 6, margin: '6px 0 0', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 280, overflowY: 'auto' } },
      lines.map((l, i) => h('div', { key: i, style: { color: l.startsWith('+') && !l.startsWith('+++') ? OK : l.startsWith('-') && !l.startsWith('---') ? '#ff6b9a' : 'rgba(255,255,255,0.6)' } }, l || ' ')));
  }

  function Change({ c }) {
    const [diffView, setDiffView] = useState(false);
    const note = c.removed ? 'Removed by the new way' : c.added ? 'Added by the new way' : '';
    return h('div', { 'data-testid': 'trial-change', 'data-change': c.id, style: { marginTop: 8 } },
      h('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
        h('span', { style: { fontSize: 11.5, color: '#e8f0f7', fontWeight: 600, flex: 1 } }, c.title + (note ? ' · ' + note : '')),
        h('button', { className: 'btn', style: { fontSize: 10, padding: '1px 8px' }, onClick: () => setDiffView(!diffView) }, diffView ? 'Side by side' : 'Show the difference')),
      diffView ? h(DiffLines, { diff: c.diff }) :
        h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 } },
          box(c.before, 'Now', 'trial-before'), box(c.after, 'New way', 'trial-after')));
  }

  function Evidence({ p }) {
    const rows = Object.entries(p.per_task || {});
    const miss = p.missing || {};
    const safety = p.new_safety_failures || [];
    if (p.untested) return h('div', { style: { fontSize: 11, color: AMBER, marginTop: 6 } }, 'No practice task covers this change, so it is untested. Judge it by the difference above.');
    return h('div', { 'data-testid': 'trial-evidence', style: { marginTop: 8 } },
      rows.length ? h('table', { style: { fontSize: 10.5, color: 'rgba(255,255,255,0.75)', borderCollapse: 'collapse', width: '100%' } },
        h('thead', null, h('tr', null, ['Practice task', 'Now passed', 'New passed', 'Runs', 'Better'].map(t => h('th', { key: t, style: { textAlign: 'left', fontWeight: 500, color: 'rgba(255,255,255,0.5)', paddingRight: 8 } }, t)))),
        h('tbody', null, rows.map(([task, t]) => h('tr', { key: task },
          h('td', { style: { fontFamily: MONO, paddingRight: 8 } }, task), h('td', null, t.a_pass), h('td', null, t.b_pass), h('td', null, t.n),
          h('td', { style: { color: t.favours === 'b' ? OK : t.favours === 'a' ? AMBER : 'rgba(255,255,255,0.5)' } }, t.favours === 'b' ? 'new' : t.favours === 'a' ? 'now' : 'same'))))) : null,
      h('div', { style: { fontSize: 10.5, color: 'rgba(255,255,255,0.6)', marginTop: 4 } },
        'Missing runs (not counted against either way): now ' + (miss.a || 0) + ', new ' + (miss.b || 0) + '. ' +
        (safety.length ? 'Safety: the new way made ' + safety.length + ' mistake(s) the current way never made.' : 'No new safety mistakes.')),
      p.rule ? h('div', { style: { fontSize: 9.5, color: 'rgba(255,255,255,0.4)', fontFamily: MONO, marginTop: 3 } }, 'Rule: ' + p.rule) : null);
  }

  function FridayTrialCard({ a, busy, onDecide }) {
    const p = (a && a.payload) || {};
    const rec = p.recommended === 'candidate' ? 'Recommended: use the new way' : 'Recommended: keep current';
    const off = !!busy;
    const lines = String(a.description || '').split('\n').filter(Boolean);
    const btn = { fontSize: 11, padding: '3px 14px', color: '#e8f0f7', border: '1px solid rgba(255,255,255,0.25)', background: 'transparent' };
    return h('div', { className: 'card', style: { marginBottom: 6, padding: 10 }, 'data-testid': 'approval-card', 'data-approval-id': a.approval_id, 'data-trial-card': p.trial_id || '' },
      h('div', { style: { fontFamily: 'Orbitron, Inter, sans-serif', fontSize: 9, letterSpacing: '.22em', color: ACCENT } }, 'TESTED CHANGE'),
      h('div', { style: { fontSize: 13, color: '#f1f6fb', fontWeight: 600, marginTop: 4 } }, a.title),
      h('div', { 'data-testid': 'trial-recommendation', style: { fontSize: 11, color: p.recommended === 'candidate' ? OK : AMBER, marginTop: 4 } }, rec + (p.label ? ' (' + p.label + ')' : '')),
      h('div', { style: { fontSize: 11, color: 'rgba(255,255,255,0.72)', marginTop: 4, lineHeight: 1.5 } }, lines.map((l, i) => h('div', { key: i }, l))),
      (p.changes || []).map(c => h(Change, { key: c.id, c })),
      h(Evidence, { p }),
      p.undo ? h('div', { style: { fontSize: 10.5, color: 'rgba(255,255,255,0.55)', marginTop: 6 } }, 'To undo later: ' + p.undo + '.') : null,
      h('div', { style: { display: 'flex', gap: 8, marginTop: 8 } },
        h('button', { className: 'btn', 'data-testid': 'trial-keep', style: btn, disabled: off, onClick: () => onDecide(a.approval_id, 'deny') }, 'Keep current'),
        h('button', { className: 'btn', 'data-testid': 'trial-use-new', style: btn, disabled: off, onClick: () => onDecide(a.approval_id, 'approve') }, 'Use new')));
  }

  window.FridayTrialCard = FridayTrialCard;
})();
