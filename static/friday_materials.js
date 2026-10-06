/* Materials are references to real selections, never copies of document text.
 * This page keeps them per project in memory only. Reloading the page clears
 * them; no token, browser storage or network request is involved. Discuss
 * prepares a draft for the host; it never sends a turn.
 */
(function () {
  'use strict';
  if (window.FridayMaterials) return;
  const h = React.createElement;
  const { useState, useEffect, useRef } = React;
  const EVENT = 'friday:material-select', CHANGE = 'friday:materials-change';
  const LIMIT = 40;
  const text = (v, n) => typeof v === 'string' ? v.slice(0, n || 240) : '';
  const positive = v => Number.isSafeInteger(Number(v)) && Number(v) > 0 ? Number(v) : null;
  const METADATA = ['ext', 'size', 'mtime', 'pages', 'shelf', 'privacy', 'status', 'type', 'folder'];
  let selection = null;
  const memory = new Map();

  function normalize(value) {
    if (!value || typeof value !== 'object') return null;
    const kind = value.kind, input = value.ref || {}, ref = {}, meta = {};
    let key, origin;
    if (kind === 'library') {
      ref.doc = positive(input.doc);
      if (!ref.doc) return null;
      if (positive(input.section)) ref.section = positive(input.section);
      if (positive(input.block)) ref.block = positive(input.block);
      if (positive(input.page)) ref.page = positive(input.page);
      key = 'library:' + ref.doc + (ref.section ? ':section:' + ref.section : '') + (ref.block ? ':block:' + ref.block : '');
      origin = { workspace: 'library', view: value.origin && value.origin.view === 'list' ? 'list' : 'shelves', lib: Object.assign({}, ref) };
    } else if (kind === 'file') {
      ref.root = text(input.root, 100); ref.path = text(input.path, 2000);
      if (!/^[a-zA-Z0-9_-]+$/.test(ref.root) || !ref.path || ref.path.startsWith('/') || ref.path.indexOf(':') >= 0 || ref.path.indexOf(String.fromCharCode(92)) >= 0 || ref.path.split('/').some(p => !p || p === '..' || p === '.')) return null;
      ref.folder = !!input.folder;
      key = 'file:' + ref.root + ':' + ref.path;
      const split = ref.path.lastIndexOf('/');
      origin = { workspace: 'library', view: 'pc', files3d: {
        root: ref.root, path: ref.folder ? ref.path : (split < 0 ? '' : ref.path.slice(0, split)),
        file: ref.folder ? '' : ref.path.slice(split + 1),
        arrangement: text(value.origin && value.origin.files3d && value.origin.files3d.arrangement, 24)
      } };
    } else if (kind === 'media') {
      ref.id = text(String(input.id || ''), 180);
      if (!ref.id || /[\x00-\x1f]/.test(ref.id)) return null;
      key = 'media:' + ref.id;
      origin = { workspace: 'media', card: ref.id };
    } else return null;
    METADATA.forEach(k => {
      const v = value.meta && value.meta[k];
      if (typeof v === 'string') meta[k] = text(v, 160);
      else if (typeof v === 'number' && Number.isFinite(v) && v >= 0) meta[k] = v;
      else if (typeof v === 'boolean') meta[k] = v;
    });
    return { key, kind, title: text(value.title) || 'Untitled ' + kind, ref, meta, origin };
  }

  function fromFile(item, root, arrangement) {
    if (!item) return null;
    return normalize({ kind: 'file', title: item.name, ref: { root, path: item.rel, folder: !!item.dir },
      meta: { ext: item.ext, size: item.size, mtime: item.mtime, type: item.dir ? 'Folder' : 'File' },
      origin: { files3d: { arrangement } } });
  }
  function fromLibrary(node, view) {
    if (!node || (node.kind !== 'document' && node.kind !== 'section')) return null;
    const doc = Number(String(node.kind === 'document' ? node.id : node.doc).replace(/^d:/, ''));
    return normalize({ kind: 'library', title: node.title, ref: { doc, section: node.kind === 'section' ? Number(String(node.id).replace(/^s:/, '')) : undefined },
      meta: { ext: node.ext, pages: node.pages, size: node.size, shelf: node.shelf, type: node.kind === 'section' ? 'Section' : 'Document' }, origin: { view } });
  }
  function fromMedia(card) {
    return card ? normalize({ kind: 'media', title: card.title, ref: { id: card.id },
      meta: { type: card.kind, status: card.status, privacy: card.privacy } }) : null;
  }
  function select(value) {
    const item = normalize(value);
    if (item) window.dispatchEvent(new CustomEvent(EVENT, { detail: item }));
  }
  window.addEventListener(EVENT, e => { selection = normalize(e.detail); });

  function rows(value) {
    const item = normalize(value);
    if (!item) return [];
    const out = [['Source', item.kind === 'file' ? 'This PC' : item.kind === 'library' ? 'Library' : 'Media']];
    if (item.kind === 'file') out.push(['Location', item.ref.root + '/' + item.ref.path]);
    else out.push(['Reference', item.key]);
    const labels = { ext: 'Format', size: 'Bytes', mtime: 'Modified', pages: 'Pages', shelf: 'Shelf', privacy: 'Privacy', status: 'Status', type: 'Kind' };
    Object.keys(labels).forEach(k => {
      const v = item.meta[k];
      if (v == null || v === '') return;
      out.push([labels[k], k === 'mtime' ? new Date(v * 1000).toLocaleString() : String(v)]);
    });
    return out;
  }
  function discussionText(values) {
    const items = (values || []).map(normalize).filter(Boolean);
    if (!items.length) return '';
    return 'I would like to discuss these selected materials. These are references and metadata only; their contents have not been attached. Use the available source tools if needed and tell me if a source cannot be read.\n\n' +
      items.map((item, i) => (i + 1) + '. ' + item.title + '\n' + rows(item).map(r => r[0] + ': ' + r[1]).join('\n')).join('\n\n');
  }

  function navigate(origin) {
    if (!origin || !window.fridayOpenWorkspace) return false;
    if (origin.files3d) window.__files3dOpen = Object.assign({}, origin.files3d);
    window.fridayOpenWorkspace(origin);
    if (origin.files3d) window.dispatchEvent(new CustomEvent('friday-files3d-open'));
    return true;
  }

  function readItems(project) {
    const values = memory.get(project) || [];
    const seen = new Set();
    return (Array.isArray(values) ? values : []).map(normalize).filter(x => x && !seen.has(x.key) && seen.add(x.key)).slice(0, LIMIT);
  }
  function writeItems(project, items) {
    memory.set(project, items);
    window.dispatchEvent(new CustomEvent(CHANGE, { detail: { project, items } }));
  }
  function gatherReference(project, value) {
    const current = normalize(value), items = readItems(project);
    if (!current) return '';
    if (items.some(x => x.key === current.key)) return 'Already gathered.';
    if (items.length >= LIMIT) return 'Remove a material before gathering another. This tray holds 40 references.';
    writeItems(project, items.concat(current));
    return 'Gathered ' + current.title + '.';
  }

  function styles() {
    if (document.getElementById('friday-materials-style')) return;
    const node = document.createElement('style'); node.id = 'friday-materials-style';
    node.textContent = '.fm-tray{min-width:0;color:var(--fr-text);font-family:var(--fr-font-body);font-size:13px}.fm-head,.fm-actions,.fm-row{display:flex;align-items:center;gap:8px}.fm-head{justify-content:space-between}.fm-head h3{margin:0;font-size:15px}.fm-note{color:var(--fr-dim);font-size:12px;line-height:1.5;margin:8px 0}.fm-current,.fm-row,.fm-compare article{border:1px solid var(--fr-glass-edge);border-radius:10px;background:var(--fr-surface);padding:12px}.fm-current{margin:12px 0}.fm-current strong,.fm-row strong{display:block;overflow-wrap:anywhere}.fm-actions{flex-wrap:wrap;margin-top:10px}.fm-tray button{font:inherit;cursor:pointer;color:var(--fr-text);background:var(--fr-glass);border:1px solid var(--fr-glass-edge);border-radius:7px;padding:6px 9px;min-height:32px}.fm-tray button:hover{border-color:var(--fr-cyan)}.fm-tray button:focus-visible,.fm-tray input:focus-visible{outline:2px solid var(--fr-cyan);outline-offset:3px}.fm-tray button:disabled{opacity:.45;cursor:default}.fm-list{list-style:none;padding:0;display:flex;flex-direction:column;gap:7px}.fm-row>label{display:flex;align-items:center;gap:9px;flex:1;min-width:0}.fm-row input{accent-color:var(--fr-cyan)}.fm-row small{display:block;color:var(--fr-dim);margin-top:3px}.fm-compare{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px}.fm-compare h4{margin:0 0 10px;font-size:13px;overflow-wrap:anywhere}.fm-compare dl{margin:0}.fm-compare dt{font-size:11px;color:var(--fr-dim);margin-top:9px}.fm-compare dd{margin:2px 0 0;overflow-wrap:anywhere}.fm-status{color:var(--fr-dim);min-height:18px;font-size:12px;margin-top:9px}.fm-count{color:var(--fr-dim);font-variant-numeric:tabular-nums}.fm-copy{display:block;width:100%;box-sizing:border-box;min-height:130px;background:var(--fr-surface);color:var(--fr-text);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:10px;font:inherit;margin-top:10px}';
    node.textContent += '.fm-selection{display:flex;align-items:center;gap:12px;flex-wrap:wrap;min-width:0;padding:10px 14px;border:1px solid var(--fr-glass-edge);border-radius:12px;background:var(--fr-surface);color:var(--fr-text);font:13px var(--fr-font-body);box-shadow:0 8px 24px rgba(0,0,0,.14)}.fm-selection-copy{flex:1;min-width:140px}.fm-selection-copy strong{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.fm-selection-context{display:block;margin-top:3px;color:var(--fr-dim);font-size:11px;overflow-wrap:anywhere}.fm-selection-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.fm-selection button{font:inherit;cursor:pointer;color:var(--fr-text);background:var(--fr-glass);border:1px solid var(--fr-glass-edge);border-radius:7px;padding:6px 9px;min-height:32px}.fm-selection button:hover{border-color:var(--fr-cyan)}.fm-selection button:focus-visible{outline:2px solid var(--fr-cyan);outline-offset:3px}.fm-selection button:disabled{opacity:.55;cursor:default}.fm-selection .fm-gather{color:var(--fr-cyan);border-color:var(--fr-cyan)}.fm-selection-status{flex-basis:100%;color:var(--fr-dim);font-size:12px}.fm-selection-status:empty{display:none}';
    document.head.appendChild(node);
  }

  function SelectionBar({ projectId, projectName, onReview, className }) {
    styles();
    const project = String(projectId || 'session');
    const [state, setState] = useState(() => ({ project, current: selection, items: readItems(project) }));
    const [note, setNote] = useState('');
    useEffect(() => {
      setState({ project, current: selection, items: readItems(project) }); setNote('');
      const selected = e => { setState({ project, current: normalize(e.detail), items: readItems(project) }); setNote(''); };
      const changed = e => { if (e.detail && e.detail.project === project) setState(s => ({ project, current: s.current, items: readItems(project) })); };
      window.addEventListener(EVENT, selected); window.addEventListener(CHANGE, changed);
      return () => { window.removeEventListener(EVENT, selected); window.removeEventListener(CHANGE, changed); };
    }, [project]);
    const snapshot = state.project === project ? state : { current: selection, items: readItems(project) };
    const { current, items } = snapshot;
    if (!current) return null;
    const gathered = items.some(x => x.key === current.key);
    const target = project === 'session' ? 'This page' : text(projectName) || 'Selected project';
    return h('aside', { className: 'fm-selection' + (className ? ' ' + className : ''), 'aria-label': 'Gather selected material' },
      h('div', { className: 'fm-selection-copy' },
        h('strong', { title: current.title }, current.title),
        h('span', { className: 'fm-selection-context' }, target + ' · For this page · clears on reload')),
      h('div', { className: 'fm-selection-actions' },
        h('span', { className: 'fm-count', 'aria-label': items.length + ' of ' + LIMIT + ' materials gathered' }, items.length + ' / ' + LIMIT),
        h('button', { type: 'button', className: 'fm-gather', disabled: gathered, onClick: () => setNote(gatherReference(project, current)), 'aria-label': (gathered ? 'Gathered ' : 'Gather ') + current.title + ' in ' + target }, gathered ? 'Gathered' : 'Gather'),
        onReview ? h('button', { type: 'button', onClick: () => onReview() }, 'Review materials') : null),
      h('div', { className: 'fm-selection-status', role: 'status', 'aria-live': 'polite' }, state.project === project ? note : ''));
  }

  function Tray({ projectId, onNavigate, onDiscuss, className }) {
    styles();
    const project = String(projectId || 'session');
    const [current, setCurrent] = useState(selection);
    const [items, setItems] = useState([]), [checked, setChecked] = useState([]);
    const [compare, setCompare] = useState(false);
    const [note, setNote] = useState(''), [draft, setDraft] = useState('');
    const [discussing, setDiscussing] = useState(false);
    const discussion = useRef({ pending: false, active: true, project });
    discussion.current.project = project;
    useEffect(() => { discussion.current.active = true; return () => { discussion.current.active = false; }; }, []);
    useEffect(() => {
      setItems(readItems(project)); setCurrent(selection); setChecked([]); setCompare(false); setDraft(''); setNote('');
      const selected = e => setCurrent(normalize(e.detail));
      const changed = e => { if (e.detail && e.detail.project === project) setItems(e.detail.items); };
      window.addEventListener(EVENT, selected); window.addEventListener(CHANGE, changed);
      return () => { window.removeEventListener(EVENT, selected); window.removeEventListener(CHANGE, changed); };
    }, [project]);
    const commit = next => { setItems(next); writeItems(project, next); };
    const gather = () => setNote(gatherReference(project, current));
    const open = item => {
      if (onNavigate) {
        if (item.origin.files3d) window.__files3dOpen = Object.assign({}, item.origin.files3d);
        onNavigate(item.origin);
        if (item.origin.files3d) window.dispatchEvent(new CustomEvent('friday-files3d-open'));
      } else if (!navigate(item.origin)) setNote('Open the source from Library or Media; navigation is not available in this view.');
    };
    const chosen = items.filter(x => checked.includes(x.key));
    const toggle = key => { setCompare(false); setChecked(v => v.includes(key) ? v.filter(k => k !== key) : v.concat(key)); };
    const discuss = async values => {
      if (discussion.current.pending) return;
      const value = discussionText(values);
      if (!value) return;
      if (!onDiscuss) { setDraft(value); setNote('Copy these references into your conversation. Nothing has been sent.'); return; }
      discussion.current.pending = true; setDiscussing(true); setNote('Preparing a conversation draft…');
      try {
        await onDiscuss(value);
        if (discussion.current.active && discussion.current.project === project) { setDraft(''); setNote('Prepared references for the conversation. Nothing has been sent.'); }
      } catch (error) {
        if (discussion.current.active && discussion.current.project === project) { setDraft(value); setNote('Could not prepare the conversation. Copy the references below or try again.' + (error && error.message ? ' ' + text(error.message) : '')); }
      } finally {
        discussion.current.pending = false;
        if (discussion.current.active) setDiscussing(false);
      }
    };
    return h('section', { className: 'fm-tray' + (className ? ' ' + className : ''), 'aria-label': 'Gathered materials' },
      h('div', { className: 'fm-head' }, h('h3', null, 'Materials'), h('span', { className: 'fm-count' }, items.length + ' / ' + LIMIT)),
      h('p', { className: 'fm-note' }, 'For this page · clears on reload. References are kept separately for each project; they are not uploaded or added to project files.'),
      h('div', { className: 'fm-current' }, current ? [
        h('span', { key: 'label', className: 'fm-note' }, 'Last selected in ' + (current.kind === 'file' ? 'This PC' : current.kind === 'library' ? 'Library' : 'Media')),
        h('strong', { key: 'title' }, current.title),
        h('div', { key: 'actions', className: 'fm-actions' }, h('button', { type: 'button', disabled: items.some(x => x.key === current.key), onClick: gather }, items.some(x => x.key === current.key) ? 'Gathered' : 'Gather material'), h('button', { type: 'button', onClick: () => open(current) }, 'Open source'))
      ] : h('p', { className: 'fm-note' }, 'Select a file, a Library document or section, or a Media card. Gather it here to keep its place while you work.')),
      items.length ? h('ul', { className: 'fm-list' }, items.map(item => h('li', { key: item.key, className: 'fm-row' },
        h('label', null, h('input', { type: 'checkbox', checked: checked.includes(item.key), onChange: () => toggle(item.key), 'aria-label': 'Select ' + item.title + ' for comparison or discussion' }), h('span', null, h('strong', null, item.title), h('small', null, item.kind === 'file' ? 'This PC' : item.kind === 'library' ? 'Library' : 'Media'))),
        h('button', { type: 'button', onClick: () => open(item), 'aria-label': 'Open source: ' + item.title }, 'Open'),
        h('button', { type: 'button', onClick: () => { commit(items.filter(x => x.key !== item.key)); setChecked(v => v.filter(k => k !== item.key)); setCompare(false); setNote('Removed reference. The source is unchanged.'); }, 'aria-label': 'Remove ' + item.title + ' from materials' }, 'Remove')))) : null,
      items.length ? h('div', { className: 'fm-actions' }, h('button', { type: 'button', disabled: chosen.length !== 2, onClick: () => setCompare(v => !v) }, compare ? 'Close comparison' : 'Compare two'), h('button', { type: 'button', disabled: discussing, 'aria-busy': discussing, onClick: () => discuss(chosen.length ? chosen : items) }, discussing ? 'Preparing draft…' : chosen.length ? 'Discuss selected' : 'Discuss materials'), h('button', { type: 'button', onClick: () => { commit([]); setChecked([]); setCompare(false); setDraft(''); setNote('Gathered references cleared. The sources are unchanged.'); } }, 'Clear gathered')) : null,
      compare && chosen.length === 2 ? h('div', null, h('p', { className: 'fm-note' }, 'Metadata at the time of selection. Open each source to read its current contents.'), h('div', { className: 'fm-compare' }, chosen.map(item => h('article', { key: item.key }, h('h4', null, item.title), h('dl', null, rows(item).map(([label, value]) => h(React.Fragment, { key: label }, h('dt', null, label), h('dd', null, value)))), h('div', { className: 'fm-actions' }, h('button', { type: 'button', onClick: () => open(item) }, 'Open source')))))) : null,
      draft ? h('textarea', { className: 'fm-copy', readOnly: true, value: draft, 'aria-label': 'References to copy into a conversation', onFocus: e => e.target.select() }) : null,
      h('div', { className: 'fm-status', role: 'status', 'aria-live': 'polite' }, note));
  }
  window.FridayMaterials = { Tray, SelectionBar, normalize, keyFor: value => { const v = normalize(value); return v && v.key; }, fromFile, fromLibrary, fromMedia, select, rows, discussionText, navigate };
})();
