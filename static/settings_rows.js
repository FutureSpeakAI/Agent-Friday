/* Settings rows by sentence (window.FridaySettingRows): the line under a row that Friday changed, and the outline
 * a row gets when the owner is taken to it.
 *
 * A row that has a stable path (`settings.<page>.<key>`, HIG 6.4) carries it as data-st-key. Two things use it:
 *   - Provenance: who last changed the row and when ("Friday, by a proposal you accepted"), with an Undo that
 *     holds for thirty days (GET /api/settings/changes, POST /api/settings/changes/<id>/undo).
 *   - highlight(key): scrolls the row into view and outlines it (the reticle's Locked look: a 2px --fr-cyan
 *     ring, set once, no pulse); it goes on the owner's next input or after 12 s.
 * Brand tokens only. Nothing here changes a setting: a change is the owner's own control or a card's Yes.
 */
(function () {
  'use strict';
  if (window.FridaySettingRows) return;
  const h = React.createElement;
  const { useState, useEffect } = React;
  const HIT_MS = 12000;

  const api = (url, opts) => {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };

  const css = document.createElement('style');
  css.textContent = '.fr-st-hit{outline:2px solid var(--fr-cyan);outline-offset:4px;border-radius:6px;transition:outline-color 150ms ease-out}' +
    '@media (prefers-reduced-motion:reduce){.fr-st-hit{transition-duration:80ms}}';
  document.head.appendChild(css);

  let lit = null, litTimer = null, offInput = null;
  function clearHit() {
    if (lit) lit.classList.remove('fr-st-hit');
    lit = null;
    if (litTimer) { clearTimeout(litTimer); litTimer = null; }
    if (offInput) { offInput(); offInput = null; }
  }
  // highlight(key) -> true when a row with that key is on screen.
  function highlight(key) {
    const el = key ? document.querySelector('[data-st-key="' + String(key).replace(/"/g, '') + '"]') : null;
    if (!el) return false;
    if (lit !== el) {
      clearHit();
      lit = el;
      try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) { el.scrollIntoView(); }
      el.classList.add('fr-st-hit');
      const evs = ['wheel', 'pointerdown', 'keydown', 'touchstart'];
      const stop = () => clearHit();
      // the scroll we just caused must not clear it: arm on the next tick
      const arm = setTimeout(() => evs.forEach(ev => window.addEventListener(ev, stop, true)), 600);
      offInput = () => { clearTimeout(arm); evs.forEach(ev => window.removeEventListener(ev, stop, true)); };
      litTimer = setTimeout(clearHit, HIT_MS);
    }
    return true;
  }

  const when = ts => {
    try { return new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; }
  };

  // The provenance line for one row path, with its Undo.
  function Provenance(props) {
    const path = props && props.path;
    const [last, setLast] = useState(null);
    const [note, setNote] = useState('');
    const load = () => {
      if (!path) return;
      api('/api/settings/changes?path=' + encodeURIComponent(path) + '&limit=1').then(r => r.json())
        .then(d => setLast((d && d.changes && d.changes[0]) || null)).catch(() => {});
    };
    useEffect(() => {
      load();
      window.addEventListener('friday:settings-changed', load);
      return () => window.removeEventListener('friday:settings-changed', load);
    }, [path]);
    if (!last) return null;
    const undo = () => api('/api/settings/changes/' + encodeURIComponent(last.id) + '/undo', { method: 'POST' })
      .then(r => r.json()).then(d => {
        setNote(d.text || '');
        try { window.dispatchEvent(new CustomEvent('friday:settings-changed', { detail: { path } })); } catch (e) {}
      }).catch(() => setNote('Could not reach the server.'));
    return h('div', { 'data-testid': 'st-provenance', style: { fontSize: 'var(--fr-text-sm)', color: 'var(--fr-faint)', margin: '2px 0 8px' } },
      last.undone ? 'Changed by ' + last.by + ' · ' : last.by + ' · ',
      h('span', { style: { fontFamily: 'var(--fr-font-mono)' } }, when(last.at)),
      last.undone ? ' · undone' : '',
      last.undoable ? [' · ', h('button', { key: 'u', type: 'button', onClick: undo, 'data-testid': 'st-undo',
        style: { background: 'none', border: 0, padding: 0, color: 'var(--fr-cyan)', cursor: 'pointer', font: 'inherit', textDecoration: 'underline' } }, 'Undo')] : null,
      note ? h('span', { style: { marginLeft: 8, color: 'var(--fr-dim)' } }, note) : null);
  }

  window.FridaySettingRows = { Provenance: Provenance, highlight: highlight };
})();
