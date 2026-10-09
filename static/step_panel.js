/* The step panel (window.FridayStepPanel): a short list of what Friday is doing, step by step, with a Stop button.
 *
 * The server shows a list when a workflow starts (`steps`) and updates one step at a time (`step_update`); the page
 * turns those commands into the window events friday:steps and friday:step_update (fridayRunActions). The panel
 * keeps the list in the pure reducer in friday_stage.js, shows each step's state in words and a glyph (never colour
 * alone), and stops the run three ways: the Stop button, Esc, or the spoken "stop" (a tool, not this file). Stopping
 * ends the step that is running after its step and starts no other; a step waiting at an approval card keeps its card.
 *
 * Brand tokens only; one change per step at a time; reduced motion has no animation to reduce. No second engine:
 * the states are the workflow's own task records.
 */
(function () {
  'use strict';
  if (window.FridayStepPanel) return;
  const FS = window.fridayStage;
  if (!FS || !FS.reduceSteps) { console.warn('[step_panel] friday_stage.js did not load first; the step panel is off'); return; }
  const h = React.createElement;
  const { useState, useEffect, useRef } = React;

  const WORD = { waiting: 'waiting', doing: 'doing', done: 'done', held: 'needs you', skipped: 'skipped', stopped: 'stopped', failed: 'failed' };
  const GLYPH = { waiting: '○', doing: '●', done: '✓', held: '⏸', skipped: '↷', stopped: '■', failed: '✕' };
  const COLOUR = { waiting: 'var(--fr-dim)', doing: 'var(--fr-cyan)', done: 'var(--fr-ok)', held: 'var(--fr-warn)', skipped: 'var(--fr-dim)', stopped: 'var(--fr-dim)', failed: 'var(--fr-error)' };
  const LINGER_MS = 9000;

  const api = (url, opts) => {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };

  function StepPanel() {
    const [list, setList] = useState(null);
    const [msg, setMsg] = useState('');
    const [hidden, setHidden] = useState(false);
    const listRef = useRef(null); listRef.current = list;
    const timer = useRef(null);

    useEffect(() => {
      const apply = evt => setList(cur => {
        const next = FS.reduceSteps(cur, evt);
        if (next && evt.type === 'steps') { setHidden(false); setMsg(''); }
        return next;
      });
      const onSteps = e => apply(Object.assign({ type: 'steps' }, e.detail || {}));
      const onUpdate = e => apply(Object.assign({ type: 'step_update' }, e.detail || {}));
      window.addEventListener('friday:steps', onSteps);
      window.addEventListener('friday:step_update', onUpdate);
      // a workflow already running when this page loaded
      api('/api/steps/active').then(r => r.json()).then(d => { if (d && d.list && !listRef.current) apply({ type: 'steps', id: d.list.id, title: d.list.title, steps: d.list.steps }); }).catch(() => {});
      return () => { window.removeEventListener('friday:steps', onSteps); window.removeEventListener('friday:step_update', onUpdate); };
    }, []);

    const running = !!(list && FS.stepsRunning(list));
    // a finished list lingers a few seconds, then goes
    useEffect(() => {
      if (timer.current) clearTimeout(timer.current);
      if (list && !running) timer.current = setTimeout(() => setList(null), LINGER_MS);
      return () => { if (timer.current) clearTimeout(timer.current); };
    }, [list, running]);

    const stop = () => {
      const cur = listRef.current;
      if (!cur) return Promise.resolve();
      return api('/api/steps/' + encodeURIComponent(cur.id) + '/stop', { method: 'POST' }).then(r => r.json()).then(d => setMsg(d.text || (d.ok ? 'Stopped.' : ''))).catch(() => setMsg('Could not reach the server to stop it.'));
    };
    // Esc stops it too, but only when nothing else is using Esc: not while typing, not over a dialog.
    useEffect(() => {
      if (!running) return undefined;
      const onKey = e => {
        if (e.key !== 'Escape' || e.defaultPrevented) return;
        const t = e.target || {};
        const tag = String(t.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable) return;
        // a dialog that is shut (aria-hidden, inert, hidden: the top bar's pull-out stays in the page) does not hold Esc
        if (Array.from(document.querySelectorAll('[role=dialog],[aria-modal=true]')).some(d => !d.closest('[aria-hidden=true],[inert],[hidden]'))) return;
        stop();
      };
      document.addEventListener('keydown', onKey);
      return () => document.removeEventListener('keydown', onKey);
    }, [running]);

    if (!list || hidden) return null;
    const live = (list.steps.find(s => s.state === 'doing') || {}).text || '';
    return h('div', { className: 'fr-steps', role: 'status', 'aria-live': 'polite', 'data-testid': 'step-panel', style: {
      position: 'fixed', top: 84, left: '50%', transform: 'translateX(-50%)', zIndex: 10040, minWidth: 280, maxWidth: 'min(520px, 92vw)',
      padding: '10px 12px', borderRadius: 12, background: 'var(--fr-glass)', backdropFilter: 'var(--fr-glass-blur)', WebkitBackdropFilter: 'var(--fr-glass-blur)',
      border: '1px solid var(--fr-glass-edge)', color: 'var(--fr-text)', fontSize: 'var(--fr-text-md)'
    } },
      h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 } },
        h('b', { style: { flex: 1, fontSize: 'var(--fr-text-base)' } }, list.title || 'Working'),
        running && h('button', { className: 'btn', onClick: stop, 'data-testid': 'step-stop', title: 'Stop after the step it is on (Esc)', style: { fontSize: 11, padding: '3px 10px' } }, 'Stop (Esc)'),
        !running && h('button', { className: 'btn', onClick: () => setHidden(true), 'aria-label': 'Close', style: { fontSize: 11, padding: '3px 8px' } }, '×')),
      h('ol', { style: { listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 3 } },
        list.steps.map(s => h('li', { key: s.n, 'data-state': s.state, style: { display: 'flex', gap: 8, alignItems: 'baseline', color: s.state === 'waiting' ? 'var(--fr-dim)' : 'var(--fr-text)' } },
          h('span', { 'aria-hidden': 'true', style: { width: 14, textAlign: 'center', color: COLOUR[s.state] } }, GLYPH[s.state] || '○'),
          h('span', { style: { flex: 1 } }, s.n + '. ' + s.text),
          h('span', { style: { color: COLOUR[s.state], fontSize: 'var(--fr-text-sm)' } }, WORD[s.state] || s.state)))),
      msg && h('div', { style: { marginTop: 6, color: 'var(--fr-dim)', fontSize: 'var(--fr-text-sm)' } }, msg),
      h('span', { className: 'sr-only', style: { position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' } }, live ? 'Now: ' + live : ''));
  }

  window.FridayStepPanel = StepPanel;
})();
