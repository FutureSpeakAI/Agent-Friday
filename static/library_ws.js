/* The Library: the documents you gave Friday to read, answered with footnotes.
 *
 * You choose folders and files; Friday reads them on this PC, keeps an index of
 * their structure, and answers questions with footnotes that open the exact page
 * and paragraph (or the exact second of a recording). Nothing is sent anywhere.
 *
 * Rules this file keeps:
 *   - the shell owns the frame: the root is .ws-fill; --fr-* tokens only; Orbitron
 *     for small labels only, Inter for reading text, mono for page numbers;
 *   - a search lights its route only as real decisions arrive, never while
 *     waiting; confidence is a word and a line weight, never a new colour;
 *   - nothing here shows a file path as a label; documents are named by title;
 *   - every action has a key; the 3D view has a text twin (a tree).
 *
 * Loaded by index.html after library_reader.js; defines window.LibraryWS.
 */
(function () {
  'use strict';
  if (window.LibraryWS) return;
  const h = React.createElement;
  const { useState, useEffect, useRef, useMemo, useCallback } = React;

  function api(url, opts) {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  }
  const json = (url, opts) => api(url, opts).then(r => r.json().then(d => { d.__http = r.status; return d; }));
  const post = (url, body) => json(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const toast = msg => (window.fridayToast ? window.fridayToast(msg) : console.log('[library]', msg));
  const clock = t => (window.__libraryReaderClock ? window.__libraryReaderClock(t) : String(Math.floor(t || 0)));

  const KIND_WORD = { pdf: 'PDF', docx: 'Word', markdown: 'Notes', text: 'Text', code: 'Code', html: 'Web page', csv: 'Table', xlsx: 'Spreadsheet', transcript: 'Transcript', media: 'Recording' };
  const CONF = { sure: 'sure', 'fairly sure': 'fairly sure', 'a guess': 'a guess' };

  const CSS = `
.lb-root{display:flex;flex-direction:column;gap:10px;flex:1 1 auto;min-height:0;min-width:0;color:var(--fr-text);font-family:var(--fr-font-body);font-size:var(--fr-text-base)}
.lb-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.lb-head h2{margin:0;font-size:var(--fr-text-xl);font-weight:600}
.lb-count{color:var(--fr-dim);font-size:var(--fr-text-md)}
.lb-spacer{flex:1}
.lb-label{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);text-transform:uppercase;color:var(--fr-dim)}
.lb-seg{display:inline-flex;gap:4px}
.lb-body{display:grid;gap:14px;flex:1 1 auto;min-height:0}
.lb-body.insp{grid-template-columns:210px minmax(0,1fr) 290px}
.lb-body.noinsp{grid-template-columns:210px minmax(0,1fr)}
.lb-side{display:flex;flex-direction:column;gap:2px;overflow:auto;min-height:0}
.lb-side button{background:transparent;border:0;color:var(--fr-label);text-align:left;padding:6px 8px;border-radius:6px;display:flex;justify-content:space-between;gap:8px;font:inherit;font-size:var(--fr-text-md);cursor:pointer}
.lb-side button:hover{background:var(--fr-cyan-soft)}
.lb-side button[aria-current="true"]{background:var(--fr-cyan-soft);color:var(--fr-cyan)}
.lb-side .n{color:var(--fr-dim);font-family:var(--fr-font-mono);font-size:var(--fr-text-xs)}
.lb-sep{height:1px;background:var(--fr-glass-edge);margin:6px 0}
.lb-foot{font-size:var(--fr-text-sm);color:var(--fr-faint);padding:6px 8px;margin-top:auto}
.lb-main{display:flex;flex-direction:column;gap:10px;min-width:0;min-height:0;overflow:auto}
.lb-ask{display:flex;gap:8px;flex-wrap:wrap}
.lb-ask input{flex:1 1 280px;max-width:640px;font:inherit;color:var(--fr-text);background:rgba(0,0,0,.35);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:8px 11px}
.lb-ask input:focus{outline:2px solid var(--fr-cyan);outline-offset:1px}
.lb-steps{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:3px;font-size:var(--fr-text-sm);color:var(--fr-dim)}
.lb-steps li{display:flex;gap:8px;align-items:baseline}
.lb-steps .bar{display:inline-block;height:2px;background:var(--fr-cyan);border-radius:1px;min-width:6px}
.lb-card{border:1px solid var(--fr-glass-edge);background:var(--fr-glass);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:6px;max-width:80ch}
.lb-card.cur{border-color:var(--fr-cyan)}
.lb-card .meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:var(--fr-text-sm);color:var(--fr-dim)}
.lb-card .meta b{color:var(--fr-label);font-weight:600}
.lb-card .where{font-family:var(--fr-font-mono)}
.lb-card .quote{font-size:var(--fr-text-base);line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere;user-select:text}
.lb-card .acts{display:flex;gap:6px;flex-wrap:wrap}
.lb-answer{max-width:80ch;border-left:2px solid var(--fr-cyan);padding-left:12px}
.lb-note{color:var(--fr-dim);font-size:var(--fr-text-md);max-width:80ch}
.lb-empty{border:1px dashed var(--fr-glass-edge);border-radius:10px;padding:26px;display:flex;flex-direction:column;gap:8px;align-items:flex-start;max-width:60ch}
.lb-empty h3{margin:0;font-size:var(--fr-text-xl);font-weight:600}
.lb-table{width:100%;border-collapse:collapse;font-size:var(--fr-text-md)}
.lb-table th{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);text-transform:uppercase;color:var(--fr-dim);text-align:left;padding:4px 8px;font-weight:400}
.lb-table td{padding:6px 8px;border-top:1px solid var(--fr-glass-edge)}
.lb-table tr[aria-selected="true"]{background:var(--fr-cyan-soft)}
.lb-table tbody tr{cursor:pointer}
.lb-num{font-family:var(--fr-font-mono);color:var(--fr-dim)}
.lb-pill{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:.12em;text-transform:uppercase;padding:2px 7px;border-radius:10px;border:1px solid var(--fr-glass-edge);color:var(--fr-dim)}
.lb-pill.vault{color:var(--fr-violet-soft);border-color:rgba(167,139,250,.5)}
.lb-insp{display:flex;flex-direction:column;gap:10px;overflow:auto;min-height:0;border:1px solid var(--fr-glass-edge);border-radius:10px;padding:12px;background:var(--fr-glass)}
.lb-insp dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:4px 10px;font-size:var(--fr-text-md)}
.lb-insp dt{color:var(--fr-dim)}
.lb-insp dd{margin:0;color:var(--fr-text)}
.lb-danger{color:var(--fr-error);border-color:rgba(239,68,68,.5)}
.lb-pop{position:absolute;z-index:20;background:var(--fr-surface);border:1px solid var(--fr-glass-edge);border-radius:10px;padding:10px;display:flex;flex-direction:column;gap:8px;min-width:300px;box-shadow:0 10px 30px rgba(0,0,0,.5)}
.lb-pop input{font:inherit;color:var(--fr-text);background:rgba(0,0,0,.35);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:7px 10px}
.lb-stage{position:relative;flex:1 1 auto;min-height:340px;display:flex;flex-direction:column}
.lb-twin{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.lb-twin.shown{position:static;width:auto;height:auto;clip:auto;overflow:auto;white-space:normal}
.lb-twin ul{list-style:none;margin:0;padding-left:16px}
.lb-twin li[role=treeitem]{padding:2px 0}
.lb-twin button{background:transparent;border:0;color:var(--fr-label);font:inherit;cursor:pointer;text-align:left}
@media (max-width:900px){.lb-body.insp,.lb-body.noinsp{grid-template-columns:1fr}.lb-side{flex-direction:row;flex-wrap:wrap}}
@media (prefers-reduced-motion:reduce){.lb-root *{transition:none!important;animation:none!important}}
`;
  function ensureStyles() {
    if (document.getElementById('library-ws-styles')) return;
    const s = document.createElement('style'); s.id = 'library-ws-styles'; s.textContent = CSS; document.head.appendChild(s);
  }

  // ── the twin: the same tree as an accessible list, beside the 3D canvas ──
  function Twin({ nodes, shown, onOpen, label }) {
    const byParent = useMemo(() => {
      const m = {};
      (nodes || []).forEach(n => { (m[n.parent || ''] = m[n.parent || ''] || []).push(n); });
      return m;
    }, [nodes]);
    const render = parent => (byParent[parent] || []).map(n => h('li', { key: n.id, role: 'treeitem', 'aria-expanded': byParent[n.id] ? 'true' : undefined },
      h('button', { onClick: () => onOpen(n) }, n.title + (n.kind === 'document' && n.pages ? ', ' + n.pages + ' pages' : '')),
      byParent[n.id] ? h('ul', { role: 'group' }, render(n.id)) : null));
    return h('div', { className: 'lb-twin' + (shown ? ' shown' : ''), role: 'tree', 'aria-label': label || 'Your Library as a list' }, h('ul', null, render('')));
  }

  function Pop({ children, onClose, label }) {
    const ref = useRef(null);
    useEffect(() => {
      const k = e => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
      document.addEventListener('keydown', k, true);
      return () => document.removeEventListener('keydown', k, true);
    }, [onClose]);
    return h('div', { className: 'lb-pop', role: 'dialog', 'aria-label': label, ref }, children);
  }

  function AddPopup({ onDone, onClose }) {
    const [roots, setRoots] = useState([]);
    const [path, setPath] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => { json('/api/studio-files/roots').then(d => setRoots((d.roots || []).filter(r => r.available))).catch(() => {}); }, []);
    const add = body => {
      setBusy(true);
      post('/api/library/add', body).then(d => {
        setBusy(false);
        if (d.status === 'ok') { toast('Added. Friday is reading it now.'); onDone(d.scope); }
        else toast(d.error || 'Couldn’t add that.');
      }).catch(() => { setBusy(false); toast('Couldn’t reach Friday.'); });
    };
    return h(Pop, { onClose, label: 'Add to your Library' },
      h('div', { className: 'lb-label' }, 'Add a folder'),
      h('div', { className: 'lb-count' }, 'Friday reads it here on this PC. Nothing is sent anywhere.'),
      roots.map(r => h('button', { key: r.id, className: 'btn', disabled: busy, onClick: () => add({ root: r.id }) }, r.label)),
      h('div', { className: 'lb-label' }, 'Or paste a folder or file location'),
      h('input', { value: path, placeholder: 'A folder or file on this PC', 'aria-label': 'Folder or file location', onChange: e => setPath(e.target.value), onKeyDown: e => { if (e.key === 'Enter' && path.trim()) add({ path: path.trim() }); } }),
      h('div', { style: { display: 'flex', gap: 8 } },
        h('button', { className: 'btn primary', disabled: busy || !path.trim(), onClick: () => add({ path: path.trim() }) }, 'Add to Library'),
        h('button', { className: 'btn', onClick: onClose }, 'Cancel')));
  }

  function Evidence({ ev, current, onOpen, on3D }) {
    const where = ev.t_start != null ? clock(ev.t_start) : [ev.page ? 'p. ' + ev.page : null, ev.para ? '¶' + ev.para : null].filter(Boolean).join(' ');
    return h('div', { className: 'lb-card' + (current ? ' cur' : ''), role: 'listitem' },
      h('div', { className: 'meta' },
        h('b', null, ev.doc), h('span', { className: 'where' }, where),
        h('span', null, CONF[ev.sure] || ev.sure)),
      h('div', { className: 'quote' }, ev.text),
      h('div', { className: 'acts' },
        h('button', { className: 'btn', onClick: () => onOpen(ev), 'aria-label': 'Open ' + ev.doc + ' at this passage' }, 'Open'),
        h('button', { className: 'btn', onClick: () => on3D(ev) }, 'Show in 3D')));
  }

  function LibraryWS() {
    ensureStyles();
    const [status, setStatus] = useState(null);
    const [view, setView] = useState('ask');            // ask | docs | failures | vault | pc | shelves | reader
    const [folder, setFolder] = useState(null);          // folder id string "f:3" or null = all
    const [nodes, setNodes] = useState([]);
    const [sel, setSel] = useState(null);                // selected document node
    const [detail, setDetail] = useState(null);
    const [inspector, setInspector] = useState(true);
    const [adding, setAdding] = useState(false);
    const [q, setQ] = useState('');
    const [run, setRun] = useState(null);                // {busy, steps, evidence, searched, notes, answer, receipt, error}
    const [cursor, setCursor] = useState(0);
    const [reader, setReader] = useState(null);          // {doc, block, page}
    const [stage, setStage] = useState('list');          // list | shelves  (the 2D/3D toggle)
    const inputRef = useRef(null);
    const esRef = useRef(null);

    const refresh = useCallback(() => {
      json('/api/library/status').then(d => { if (d.status === 'ok') setStatus(d); }).catch(() => {});
      json('/api/library/tree?depth=2').then(d => { if (d.status === 'ok') setNodes(d.nodes || []); }).catch(() => {});
    }, []);
    useEffect(() => { refresh(); }, [refresh]);
    useEffect(() => {
      const busy = status && (status.reading > 0 || status.counts.queued > 0);
      if (!busy) return undefined;
      const t = setInterval(refresh, 3000);
      return () => clearInterval(t);
    }, [status, refresh]);
    useEffect(() => () => { if (esRef.current) esRef.current.close(); }, []);

    useEffect(() => {
      if (!sel) { setDetail(null); return; }
      json('/api/library/document/' + sel.id.slice(2)).then(d => setDetail(d.status === 'ok' ? d.document : null)).catch(() => setDetail(null));
    }, [sel]);

    // Navigation in: {lib:{doc,block,page}, view, q}, from a footnote, navigate_to or voice.
    useEffect(() => {
      const apply = t => {
        if (!t || (t.workspace && t.workspace !== 'library')) return;
        if (t.view === 'shelves' || t.view === '3d' || t.view3d) { setStage('shelves'); setView('shelves'); }
        else if (t.view === 'list') { setStage('list'); setView('docs'); }
        if (t.q) { setQ(String(t.q)); setView('ask'); }
        const lib = t.lib || (t.libblock ? { doc: Number(t.libdoc), block: Number(t.libblock) } : null);
        if (lib && (lib.block || lib.doc)) {
          t = Object.assign({}, t, { lib });
          setReader({ doc: t.lib.doc, block: t.lib.block, page: t.lib.page });
          if (!(t.view3d || t.view === 'shelves' || t.view === '3d')) setView('reader');
          window.__libraryFocus = t.lib;
          window.dispatchEvent(new CustomEvent('friday-library-focus', { detail: t.lib }));
        }
      };
      apply(window.__fridayNavTarget);
      const f = e => apply(e.detail || {});
      window.addEventListener('friday-nav', f);
      return () => window.removeEventListener('friday-nav', f);
    }, []);
    const tabState = window.useTabState || window.fridayUseTabState;
    if (tabState) tabState('library', () => ({ view: view === 'shelves' ? 'shelves' : 'list', doc: reader && reader.doc ? reader.doc : undefined }));

    // ── asking ───────────────────────────────────────────────────────────
    const ask = useCallback((question, wantAnswer) => {
      const text = (question || '').trim();
      if (!text) return;
      if (esRef.current) esRef.current.close();
      setView('ask'); setCursor(0);
      setRun({ busy: true, steps: [], evidence: null, searched: null, notes: [], answer: null, receipt: null, error: null, q: text });
      const url = '/api/library/search?q=' + encodeURIComponent(text) + (wantAnswer ? '&answer=1' : '');
      const es = new EventSource(url);
      esRef.current = es;
      es.onmessage = m => {
        let e; try { e = JSON.parse(m.data); } catch (_) { return; }
        setRun(r => {
          if (!r) return r;
          if (e.event === 'decision') {
            window.dispatchEvent(new CustomEvent('friday-library-decision', { detail: e }));
            return Object.assign({}, r, { steps: r.steps.concat([e]) });
          }
          if (e.event === 'evidence') {
            window.dispatchEvent(new CustomEvent('friday-library-evidence', { detail: e }));
            return Object.assign({}, r, { evidence: e.evidence, searched: e.searched, notes: e.notes || [], stats: e.stats || null });
          }
          if (e.event === 'answer') return Object.assign({}, r, { answer: e.text });
          if (e.event === 'answer_unavailable') return Object.assign({}, r, { answerNote: e.reason });
          if (e.event === 'failed') return Object.assign({}, r, { error: e.error, busy: false });
          if (e.event === 'done') { es.close(); return Object.assign({}, r, { busy: false, receipt: e.receipt }); }
          return r;
        });
      };
      es.onerror = () => { es.close(); setRun(r => (r && r.busy ? Object.assign({}, r, { busy: false, error: 'The search stopped before it finished.' }) : r)); };
    }, []);

    const openEvidence = ev => { setReader({ doc: ev.doc_id, block: ev.block_id, page: ev.page }); setView('reader'); };
    const show3D = ev => {
      setReader({ doc: ev.doc_id, block: ev.block_id, page: ev.page });
      setStage('shelves'); setView('shelves');
      window.__libraryFocus = { doc: ev.doc_id, block: ev.block_id, page: ev.page, section: ev.section_id };
      window.dispatchEvent(new CustomEvent('friday-library-focus', { detail: window.__libraryFocus }));
    };
    const stepEvidence = d => {
      const evs = (run && run.evidence) || [];
      if (!evs.length) return;
      const i = (cursor + d + evs.length) % evs.length;
      setCursor(i);
      if (view === 'reader') openEvidence(evs[i]);
    };

    // ── keys ──────────────────────────────────────────────────────────────
    useEffect(() => {
      const k = e => {
        const tag = (e.target && e.target.tagName) || '';
        if (tag === 'INPUT' || tag === 'TEXTAREA') return;
        if (e.key === '/') { e.preventDefault(); setView('ask'); setTimeout(() => inputRef.current && inputRef.current.focus(), 0); }
        else if (e.key === 'Escape' && view === 'reader') { setView(stage === 'shelves' ? 'shelves' : 'ask'); }
      };
      window.addEventListener('keydown', k);
      return () => window.removeEventListener('keydown', k);
    }, [view, stage]);

    // ── derived ───────────────────────────────────────────────────────────
    const docs = useMemo(() => (nodes || []).filter(n => n.kind === 'document'), [nodes]);
    const folders = useMemo(() => (nodes || []).filter(n => n.kind === 'folder'), [nodes]);
    const shown = useMemo(() => (folder ? docs.filter(d => d.parent === folder) : docs), [docs, folder]);
    const counts = (status && status.counts) || { indexed: 0, queued: 0, failed: 0, total: 0 };
    const empty = status && status.empty;
    const line = !status ? 'Loading…'
      : empty ? 'Nothing added yet'
        : [counts.indexed + ' documents',
          (status.reading || counts.queued) ? (status.reading || counts.queued) + ' being read' : null,
          counts.failed ? counts.failed + ' couldn’t be read' : null].filter(Boolean).join(' · ');

    const onForget = () => {
      if (!sel) return;
      if (!window.confirm('Forget “' + sel.title + '” completely?\n\nIts index is deleted, and in your saved chats every footnote that cited it becomes “[forgotten source]” and its quotations are removed. The file itself is not touched. This cannot be undone.')) return;
      post('/api/library/forget', { doc_id: Number(sel.id.slice(2)), confirm: true }).then(d => {
        toast(d.ok ? 'Forgotten.' : (d.error || 'Couldn’t forget it.')); setSel(null); refresh();
      });
    };
    const onRemove = () => {
      if (!sel) return;
      post('/api/library/remove', { doc_id: Number(sel.id.slice(2)) }).then(() => { toast('Taken out of the Library. The file is untouched.'); setSel(null); refresh(); });
    };
    const onShelf = shelf => {
      post('/api/library/shelf', { doc_id: Number(sel.id.slice(2)), shelf }).then(d => { toast(d.status === 'ok' ? 'Moved. Friday is re-reading it.' : (d.error || 'Couldn’t move it.')); refresh(); });
    };
    const onRetry = () => post('/api/library/reindex', {}).then(() => { toast('Trying again.'); refresh(); });

    // ── views ─────────────────────────────────────────────────────────────
    const emptyState = h('div', { className: 'lb-empty' },
      h('h3', null, 'Your Library is empty.'),
      h('div', { className: 'lb-count' }, 'Add a folder and Friday will read it here, on this PC.'),
      h('button', { className: 'btn primary', onClick: () => setAdding(true) }, 'Add a folder'));

    const chooseKg = v => post('/api/settings', { settings: { library_kg_learn: v } }).then(() => { toast(v === 'on' ? 'The graph will learn from your documents.' : 'The graph will not read your documents.'); refresh(); });
    // The one question asked after the first folder: neither answer is chosen for the owner.
    const kgCard = status && !status.empty && !status.kg_learn && h('div', { className: 'lb-card', role: 'group', 'aria-label': 'Knowledge graph' },
      h('div', { className: 'quote' }, 'Should Friday’s knowledge graph learn the people and places in these documents?'),
      h('div', { className: 'meta' }, 'They would appear in the Galaxy with a link back to where they are mentioned. Reading happens on this PC. You can change this in Settings.'),
      h('div', { className: 'acts' },
        h('button', { className: 'btn', onClick: () => chooseKg('on') }, 'Yes, let it learn (Recommended)'),
        h('button', { className: 'btn', onClick: () => chooseKg('off') }, 'No, keep them separate')));

    const askView = h('div', { className: 'lb-main' },
      kgCard,
      h('form', { className: 'lb-ask', onSubmit: e => { e.preventDefault(); ask(q, false); } },
        h('input', { ref: inputRef, value: q, onChange: e => setQ(e.target.value), placeholder: 'Ask your documents a question', 'aria-label': 'Ask your Library' }),
        h('button', { className: 'btn primary', type: 'submit', disabled: !q.trim() || (run && run.busy) }, 'Ask')),
      !run && (empty ? emptyState : h('div', { className: 'lb-note' }, 'Ask about anything in your documents. Friday finds the passages first, then you can have her write an answer. Every statement carries a footnote that opens the exact page.')),
      run && run.busy && !run.evidence && h('div', { className: 'lb-count', role: 'status' }, 'Looking…'),
      run && run.error && h('div', { className: 'lb-empty' }, h('div', null, run.error), h('button', { className: 'btn', onClick: () => ask(run.q, false) }, 'Try again')),
      run && (run.notes || []).map((n, i) => h('div', { key: i, className: 'lb-note', role: 'status' }, n)),
      run && run.stats && h('div', { className: 'lb-card' }, h('div', { className: 'quote' }, run.stats.answer), h('div', { className: 'meta' }, run.stats.method)),
      run && run.answer && h('div', { className: 'lb-answer', dangerouslySetInnerHTML: { __html: window.renderFridayMarkdown ? window.renderFridayMarkdown(run.answer) : '' } }),
      run && run.evidence && run.evidence.length > 0 && !run.answer && h('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
        h('button', { className: 'btn', onClick: () => ask(run.q, true) }, 'Write an answer'),
        h('span', { className: 'lb-count' }, 'Written here by your local model; the passages are below.')),
      run && run.answerNote && h('div', { className: 'lb-note' }, 'No answer was written: ' + run.answerNote + '.'),
      run && run.evidence && run.evidence.length === 0 && !run.stats && h('div', { className: 'lb-note' }, 'Nothing in your Library matches that. Try a narrower question, or add the document it is in.'),
      run && run.steps.length > 0 && h('details', null,
        h('summary', { className: 'lb-label', style: { cursor: 'pointer' } }, 'How I looked'),
        h('ul', { className: 'lb-steps' }, run.steps.map((s, i) => h('li', { key: i },
          h('span', { className: 'bar', style: { width: 6 + Math.round(s.p * 60) } }),
          h('span', null, s.title), h('span', { className: 'lb-num' }, s.p >= 0.8 ? 'sure' : s.p >= 0.5 ? 'fairly sure' : 'a guess'))))),
      run && run.evidence && run.evidence.length > 0 && h('div', { role: 'list', 'aria-label': 'Passages found', style: { display: 'flex', flexDirection: 'column', gap: 8 } },
        run.evidence.map((ev, i) => h(Evidence, { key: ev.label, ev, current: i === cursor, onOpen: openEvidence, on3D: show3D }))));

    const docsView = h('div', { className: 'lb-main' },
      empty ? emptyState : h('table', { className: 'lb-table' },
        h('thead', null, h('tr', null, ['Document', 'Kind', 'Pages', 'Shelf'].map(c => h('th', { key: c, scope: 'col' }, c)))),
        h('tbody', null, shown.map(d => h('tr', { key: d.id, 'aria-selected': sel && sel.id === d.id ? 'true' : 'false', tabIndex: 0,
          onClick: () => setSel(d), onDoubleClick: () => { setReader({ doc: Number(d.id.slice(2)) }); setView('reader'); },
          onKeyDown: e => { if (e.key === 'Enter') { setReader({ doc: Number(d.id.slice(2)) }); setView('reader'); } else if (e.key === ' ') { e.preventDefault(); setSel(d); } } },
          h('td', null, d.title), h('td', null, KIND_WORD[d.ext] || d.ext), h('td', { className: 'lb-num' }, d.pages || '–'),
          h('td', null, h('span', { className: 'lb-pill' + (d.shelf === 'vault' ? ' vault' : '') }, d.shelf === 'vault' ? 'Vault' : 'Open')))))),
      h(Twin, { nodes, shown: false, onOpen: n => { if (n.kind === 'document') setSel(n); } }));

    const failView = h('div', { className: 'lb-main' },
      status && (status.failures.length + status.skipped.length) === 0 && h('div', { className: 'lb-note' }, 'Everything added was read.'),
      status && status.failures.concat(status.skipped).map(f => h('div', { key: f.kind + f.id, className: 'lb-card' },
        h('div', { className: 'quote' }, 'Couldn’t read ' + f.title + '.'),
        h('div', { className: 'meta' }, f.reason || 'It was skipped.'),
        h('div', { className: 'acts' }, h('button', { className: 'btn', onClick: onRetry }, 'Try again')))));

    const vaultView = h('div', { className: 'lb-main' },
      h('div', { className: 'lb-note' }, status && status.vault.unlocked
        ? 'Documents Friday judged private are kept here, encrypted with your vault key, and are searched only while the vault is open.'
        : 'The vault is locked, so documents on this shelf cannot be searched.'),
      h('div', { className: 'lb-count' }, status ? status.vault.documents + ' documents on this shelf' : ''),
      h('table', { className: 'lb-table' }, h('tbody', null, docs.filter(d => d.shelf === 'vault').map(d => h('tr', { key: d.id, onClick: () => setSel(d) }, h('td', null, d.title))))));

    const pcView = h('div', { className: 'lb-stage' },
      h('div', { className: 'lb-count', style: { marginBottom: 6 } }, 'Browse this PC. These files are not in your Library until you add them.'),
      window.Files3DPanel ? h('div', { className: 'f3-host on', style: { flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' } },
        h(window.Files3DPanel, { root: 'documents', path: '', view: 'wall', fill: true }))
        : h('div', { className: 'lb-note' }, 'The 3D file browser did not load.'));

    const shelvesView = h('div', { className: 'lb-stage' },
      empty ? emptyState : (window.LibraryShelves3D
        ? h(window.LibraryShelves3D, { nodes, onOpen: n => { setReader({ doc: Number(String(n.doc || n.id).replace(/\D/g, '')) }); setView('reader'); }, status })
        : h('div', { className: 'lb-note' }, 'The 3D view did not load. Your documents are listed on the left.')),
      h(Twin, { nodes, shown: false, label: 'Your Library as a list', onOpen: n => { if (n.kind === 'document') setSel(n); } }));

    const readerView = h(window.LibraryReader || 'div', { block: reader && reader.block, doc: reader && reader.doc, page: reader && reader.page,
      onClose: () => setView(stage === 'shelves' ? 'shelves' : 'ask'), onStep: run && run.evidence ? stepEvidence : null });

    const main = view === 'reader' ? readerView : view === 'docs' ? docsView : view === 'failures' ? failView
      : view === 'vault' ? vaultView : view === 'pc' ? pcView : view === 'shelves' ? shelvesView : askView;

    const nav = (id, label, n, extra) => h('button', { key: id, 'aria-current': view === id && !folder ? 'true' : undefined,
      onClick: () => { setFolder(null); setView(id); if (id === 'shelves') setStage('shelves'); if (id === 'docs') setStage('list'); } },
      h('span', null, label), n != null ? h('span', { className: 'n' }, n) : null);

    return h('div', { className: 'lb-root ws-fill', onKeyDown: undefined },
      h('div', { className: 'lb-head' },
        h('h2', null, 'Library'), h('span', { className: 'lb-count', role: 'status' }, line),
        status && status.waiting_because && h('span', { className: 'lb-count' }, 'Reading paused: ' + status.waiting_because),
        status && status.paused && h('span', { className: 'lb-count lb-danger' }, 'Paused: the permissions record could not be verified.'),
        h('span', { className: 'lb-spacer' }),
        h('div', { className: 'lb-seg', role: 'group', 'aria-label': 'View' },
          h('button', { className: 'btn' + (stage === 'list' && view !== 'ask' ? ' active' : ''), 'aria-pressed': stage === 'list', onClick: () => { setStage('list'); setView('docs'); } }, 'List'),
          h('button', { className: 'btn' + (stage === 'shelves' ? ' active' : ''), 'aria-pressed': stage === 'shelves', onClick: () => { setStage('shelves'); setView('shelves'); } }, 'Shelves')),
        h('button', { className: 'btn primary', onClick: () => { setView('ask'); setTimeout(() => inputRef.current && inputRef.current.focus(), 0); } }, 'Ask'),
        h('div', { style: { position: 'relative' } },
          h('button', { className: 'btn', onClick: () => setAdding(a => !a), 'aria-haspopup': 'dialog', 'aria-expanded': adding }, 'Add…'),
          adding && h(AddPopup, { onClose: () => setAdding(false), onDone: () => { setAdding(false); refresh(); setView('docs'); } })),
        h('button', { className: 'btn', 'aria-pressed': inspector, onClick: () => setInspector(i => !i) }, 'Inspector')),
      h('div', { className: 'lb-body ' + (inspector ? 'insp' : 'noinsp') },
        h('nav', { className: 'lb-side', 'aria-label': 'Library' },
          nav('ask', 'Ask'),
          nav('docs', 'All documents', counts.indexed),
          folders.map(f => h('button', { key: f.id, 'aria-current': folder === f.id ? 'true' : undefined, onClick: () => { setFolder(f.id); setView('docs'); setStage('list'); } },
            h('span', null, f.title), h('span', { className: 'n' }, f.documents))),
          h('div', { className: 'lb-sep' }),
          nav('failures', 'Couldn’t read', counts.failed + ((status && status.skipped.length) || 0)),
          nav('vault', 'Vault shelf', status ? (status.vault.unlocked ? status.vault.documents : 'locked') : null),
          nav('pc', 'Browse this PC'),
          h('div', { className: 'lb-foot' }, 'Indexed on this PC · nothing sent. The index sits on this PC, protected by your Windows account; private documents are encrypted with your vault key.')),
        main,
        inspector && h('aside', { className: 'lb-insp', 'aria-label': 'Details' },
          sel && detail ? h(React.Fragment, null,
            h('div', { className: 'lb-label' }, 'Document'),
            h('div', { style: { fontWeight: 600 } }, detail.title),
            h('dl', null,
              h('dt', null, 'Kind'), h('dd', null, KIND_WORD[detail.ext] || detail.ext),
              detail.pages ? h(React.Fragment, null, h('dt', null, 'Pages'), h('dd', { className: 'lb-num' }, detail.pages)) : null,
              h('dt', null, 'Shelf'), h('dd', null, detail.shelf === 'vault' ? 'Vault' : 'Open'),
              h('dt', null, 'Cloud'), h('dd', null, detail.cloud_grant ? 'Allowed' : 'Not allowed'),
              h('dt', null, 'Cited in'), h('dd', null, detail.cited_in + (detail.cited_in === 1 ? ' chat' : ' chats'))),
            h('div', { className: 'acts', style: { display: 'flex', gap: 6, flexWrap: 'wrap' } },
              h('button', { className: 'btn', onClick: () => { setReader({ doc: Number(sel.id.slice(2)) }); setView('reader'); } }, 'Open'),
              h('button', { className: 'btn', onClick: () => onShelf(detail.shelf === 'vault' ? 'open' : 'vault') }, detail.shelf === 'vault' ? 'Move to open shelf' : 'Move to vault'),
              h('button', { className: 'btn', onClick: onRemove }, 'Remove from Library'),
              h('button', { className: 'btn lb-danger', onClick: onForget }, 'Forget everywhere')))
            : h('div', { className: 'lb-count' }, 'Select a document to see its details.'),
          run && run.receipt && h('div', { className: 'lb-count' }, 'Last search: ' + run.steps.length + ' choices, ' + ((run.searched && run.searched.fallback) || 'none') + ' fallback.'))));
  }

  window.LibraryWS = LibraryWS;

  // Footnote chips in chat name their source: the document's title and the page
  // (or the second). Set as text, never markup; a dead source says so.
  const chipCache = {};
  function enrichChip(el) {
    if (el.__libDone) return;
    el.__libDone = true;
    const id = el.getAttribute('data-lib-block');
    if (!chipCache[id]) chipCache[id] = json('/api/library/block/' + id);
    chipCache[id].then(d => {
      if (d.status === 'ok') {
        const b = d.block;
        const where = b.t_start != null ? clock(b.t_start) : (b.page ? 'p. ' + b.page + (b.para ? ' ¶' + b.para : '') : '');
        el.textContent = b.title + (where ? ' ' + where : '');
      } else {
        el.textContent = 'source no longer in your Library';
        el.style.textDecoration = 'line-through';
        el.removeAttribute('data-lib-block');
        el.removeAttribute('role');
        el.removeAttribute('tabindex');
      }
    }).catch(() => {});
  }
  const CHIP = '.friday-cite[data-lib-block]';
  function scan(root) {
    if (root.nodeType !== 1) return;
    if (root.matches && root.matches(CHIP)) enrichChip(root);
    if (root.querySelectorAll) root.querySelectorAll(CHIP).forEach(enrichChip);
  }
  if (typeof MutationObserver !== 'undefined' && document.body) {
    new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(scan))).observe(document.body, { childList: true, subtree: true });
    scan(document.body);
  }
  (window.__fridayNavDecls = window.__fridayNavDecls || []).push(['library', {
    key: 'view',
    keys: ['view', 'lib', 'q', 'libdoc', 'libblock'],
    sections: [
      { id: 'list', label: 'Documents', aliases: ['documents', 'list', 'files'] },
      { id: 'shelves', label: 'Shelves', aliases: ['3d', 'shelf', 'spatial'] }
    ]
  }]);
})();
