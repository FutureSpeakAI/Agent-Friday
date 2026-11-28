/* Governance: what Friday did, which model served it, and where it ran.
 *
 * The workspace draws the PC's own A2UI tree for the builtin "governance" document
 * (GET /api/liquid/views/governance/rendered: services/a2ui_render bound with the broker's
 * receipt rows), through static/friday_a2ui.js, the same draw step as the standalone
 * /liquid/<ws> page. This file never validates, fetches data of its own or builds rows;
 * every string it shows has been escaped by FridayA2ui. --fr-* tokens only.
 *
 * Loaded by index.html after friday_a2ui.js; defines window.GovernanceWS.
 */
(function () {
  'use strict';
  if (window.GovernanceWS) return;
  const h = React.createElement;
  const { useEffect, useRef, useState } = React;
  const REFRESH_MS = 30000;
  const STYLE_ID = 'governance-ws-style';

  const CSS = [
    '[data-governance-ws]{padding:18px 22px;overflow:auto;color:var(--fr-text);font-family:var(--fr-font-body)}',
    '[data-governance-ws] h2{font-family:var(--fr-font-display);font-size:var(--fr-text-xl);letter-spacing:var(--fr-track-label);color:var(--fr-cyan);margin:0 0 6px}',
    '[data-governance-ws] p[data-style="body"]{color:var(--fr-dim);margin:0 0 12px}',
    '[data-governance-ws] section[data-a2ui="status_block"]{background:var(--fr-glass);border:1px solid var(--fr-glass-edge);border-radius:12px;padding:12px 14px;margin:12px 0;overflow-x:auto}',
    '[data-governance-ws] [data-a2ui-title]{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);color:var(--fr-label);text-transform:uppercase;margin:0 0 8px}',
    '[data-governance-ws] table{width:100%;min-width:520px;border-collapse:collapse;font-size:var(--fr-text-sm)}',
    '[data-governance-ws] th{text-align:left;color:var(--fr-dim);font-weight:500;text-transform:capitalize;padding:6px 8px;border-bottom:1px solid var(--fr-glass-edge)}',
    '[data-governance-ws] td{padding:6px 8px;border-bottom:1px solid var(--fr-glass-edge);vertical-align:top;overflow-wrap:break-word}',
    '[data-governance-ws] td:first-child{font-family:var(--fr-font-mono);color:var(--fr-dim);white-space:nowrap}',
    '[data-governance-ws] [data-a2ui-empty]{color:var(--fr-dim);margin:6px 0 0}',
    '[data-governance-ws] [data-governance-meta]{color:var(--fr-dim);font-size:var(--fr-text-2xs);margin-top:10px}',
  ].join('\n');

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function load() {
    const tok = window.__FRIDAY_API_TOKEN || '';
    return fetch('/api/liquid/views/governance/rendered', {
      cache: 'no-store', headers: tok ? { 'X-Friday-Token': tok } : {},
    }).then(r => r.json());
  }

  function GovernanceWS() {
    const view = useRef(null);
    const [asOf, setAsOf] = useState('');
    useEffect(() => {
      ensureStyle();
      let alive = true;
      const draw = () => load()
        .then(p => {
          if (!alive || !view.current) return;
          window.FridayA2ui.mount(view.current, p);
          setAsOf(p && p.ok && p.as_of ? p.as_of : '');
        })
        .catch(() => { if (alive && view.current) window.FridayA2ui.mount(view.current, null); });
      draw();
      const t = setInterval(draw, REFRESH_MS);
      return () => { alive = false; clearInterval(t); };
    }, []);
    return h('div', { className: 'ws-fill', 'data-governance-ws': '' },
      h('div', { ref: view, 'aria-live': 'polite' }),
      h('p', { 'data-governance-meta': '' }, asOf ? 'As of ' + asOf : ''));
  }

  window.GovernanceWS = GovernanceWS;
}());
