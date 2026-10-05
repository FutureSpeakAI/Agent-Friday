/* The one 3D file browser (window.FridayFiles3D).
 *
 * Every workspace that shows files in 3D (Library, Media, Files in Studio, the
 * Code workspace's City) mounts this one component. It is the one engine
 * (studio_files3d.js) with one bar of lenses over it:
 *   - Library: the documents in the Library, on the Shelves layout
 *     (library_shelves.js), fed the Library's own records;
 *   - Media: what Friday made (the Creations folder);
 *   - Files: the owner's folders.
 * A lens is a filter on what the engine draws, never a second browser. A search
 * lights its path the same way in every lens, through the one pacer
 * (Friday3D.createPathLights): at most three lights a second, nothing lit while
 * nothing has been found, a cut instead of motion under reduced motion.
 *
 * Voice reaches it through the desktop bus: a `files3d` action ({lens, query})
 * becomes a `friday-files3d` window event (index.html), and the browser that is
 * open takes the lens and lights the search. Colours come from the --fr-* tokens.
 *
 * Loaded by index.html after studio_files3d.js and library_shelves.js.
 */
(function () {
  'use strict';
  if (window.FridayFiles3D) return;
  const h = React.createElement;
  const { useState, useEffect, useRef } = React;

  const LENSES = [
    { id: 'library', label: 'Library', hint: 'Your documents, on shelves' },
    { id: 'media', label: 'Media', hint: 'What Friday made' },
    { id: 'files', label: 'Files', hint: 'Your folders' }
  ];
  const LENS_IDS = LENSES.map(l => l.id);

  function ensureStyles() {
    if (document.getElementById('ff3-styles')) return;
    const st = document.createElement('style');
    st.id = 'ff3-styles';
    st.textContent = [
      '.ff3-root{display:flex;flex-direction:column;flex:1 1 auto;min-height:0}',
      '.ff3-bar{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:6px 8px;border-bottom:1px solid var(--fr-glass-edge)}',
      '.ff3-bar .btn[aria-pressed=true]{border-color:var(--fr-cyan);color:var(--fr-cyan)}',
      '.ff3-bar input{flex:1 1 180px;min-width:120px;font:inherit;color:var(--fr-text);background:var(--fr-surface);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:5px 9px}',
      '.ff3-body{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}',
      '.ff3-msg{padding:18px;color:var(--fr-dim)}'
    ].join('\n');
    document.head.appendChild(st);
  }

  const api = url => fetch(url, { headers: { 'X-Friday-Token': window.__FRIDAY_API_TOKEN || '' } }).then(r => r.json());

  // The Library lens's search: the Library's own stream, its decisions and
  // evidence handed to the Shelves as the Library workspace hands them.
  function streamLibrarySearch(text, signal) {
    return fetch('/api/library/search?q=' + encodeURIComponent(text), {
      headers: { 'X-Friday-Token': window.__FRIDAY_API_TOKEN || '' }, signal
    }).then(r => {
      if (!r.body) return;
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = '';
      const pump = () => reader.read().then(({ done, value }) => {
        if (done) return;
        buf += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, cut); buf = buf.slice(cut + 2);
          const line = chunk.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;
          let e; try { e = JSON.parse(line.slice(5)); } catch (_) { continue; }
          if (e.event === 'decision') window.dispatchEvent(new CustomEvent('friday-library-decision', { detail: e }));
          else if (e.event === 'evidence') window.dispatchEvent(new CustomEvent('friday-library-evidence', { detail: e }));
          else if (e.event === 'done' || e.event === 'failed') { try { reader.cancel(); } catch (_) {} return; }
        }
        return pump();
      });
      return pump();
    }).catch(() => {});
  }

  function FridayFiles3D(props) {
    props = props || {};
    ensureStyles();
    const lenses = props.lenses || LENS_IDS;
    const [lens, setLens] = useState(LENS_IDS.indexOf(props.lens) >= 0 ? props.lens : 'files');
    const [query, setQuery] = useState(props.query || '');
    const [nodes, setNodes] = useState(props.nodes || null);
    const [status, setStatus] = useState(props.status || null);
    useEffect(() => { if (props.nodes) setNodes(props.nodes); }, [props.nodes]);
    useEffect(() => { if (props.status) setStatus(props.status); }, [props.status]);
    useEffect(() => { if (props.lens && LENS_IDS.indexOf(props.lens) >= 0) setLens(props.lens); }, [props.lens]);

    // Voice: the open browser takes the lens and the search it was asked for.
    useEffect(() => {
      const on = e => {
        const d = (e && e.detail) || {};
        if (LENS_IDS.indexOf(d.lens) >= 0) setLens(d.lens);
        if (d.query != null) setQuery(String(d.query));
      };
      window.addEventListener('friday-files3d', on);
      const pending = window.__fridayFiles3dPending;
      if (pending) { window.__fridayFiles3dPending = null; on({ detail: pending }); }
      return () => window.removeEventListener('friday-files3d', on);
    }, []);

    // The Library lens reads its own records when the workspace did not hand them in.
    useEffect(() => {
      if (lens !== 'library' || props.nodes) return undefined;
      let live = true;
      api('/api/library/tree?depth=2').then(d => { if (live && d.status === 'ok') setNodes(d.nodes || []); }).catch(() => {});
      api('/api/library/status').then(d => { if (live && d.status === 'ok') setStatus(d); }).catch(() => {});
      return () => { live = false; };
    }, [lens]);

    // The Library lens's search lights the Shelves; nothing is lit for an empty box.
    const searchRef = useRef(null);
    useEffect(() => {
      if (lens !== 'library') return undefined;
      const text = query.trim();
      if (!text) return undefined;
      const ctl = new AbortController();
      const t = setTimeout(() => { searchRef.current = ctl; streamLibrarySearch(text, ctl.signal); }, 400);
      return () => { clearTimeout(t); ctl.abort(); };
    }, [query, lens]);

    const bar = h('div', { className: 'ff3-bar', role: 'toolbar', 'aria-label': 'What to show' },
      LENSES.filter(l => lenses.indexOf(l.id) >= 0).map(l => h('button', {
        key: l.id, type: 'button', className: 'btn', title: l.hint, 'aria-pressed': lens === l.id,
        onClick: () => setLens(l.id)
      }, l.label)),
      lens === 'library' && h('input', {
        value: query, onChange: e => setQuery(e.target.value),
        placeholder: 'Search your Library; its path lights up', 'aria-label': 'Search your Library'
      }));

    let body;
    if (lens === 'library') {
      body = !window.LibraryShelves3D ? h('div', { className: 'ff3-msg' }, 'The Library shelves did not load.')
        : (nodes && nodes.length) ? h(window.LibraryShelves3D, { nodes, status, onOpen: props.onOpen || (n => {
          if (window.fridayOpenWorkspace) window.fridayOpenWorkspace({ workspace: 'library', lib: { doc: Number(String(n.doc || n.id).replace(/\D/g, '')) } });
        }) })
        : h('div', { className: 'ff3-msg' }, nodes ? 'Nothing in the Library yet.' : 'Reading the Library…');
    } else if (!window.Files3DPanel) {
      body = h('div', { className: 'ff3-msg' }, 'The 3D file browser did not load.');
    } else {
      const panel = { key: lens + ':' + (props.root || ''), fill: true, view: props.view || 'wall', query: lens === 'library' ? undefined : query };
      if (lens === 'media') panel.root = 'creations';
      else if (props.root) { panel.root = props.root; panel.path = props.path || ''; }
      body = h(window.Files3DPanel, panel);
    }
    return h('div', { className: 'ff3-root', 'data-lens': lens }, bar, h('div', { className: 'ff3-body' }, body));
  }

  window.FridayFiles3D = FridayFiles3D;
  FridayFiles3D.LENSES = LENS_IDS;
})();
