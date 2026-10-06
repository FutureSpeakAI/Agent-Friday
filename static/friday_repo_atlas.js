/* A source-linked repository map for the salon. The graph is data; repository
 * text is never executed here or pasted into a chat request. */
(function () {
  'use strict';
  if (window.FridayRepoAtlas) return;
  const h = React.createElement;
  const { useState, useEffect, useMemo, useRef } = React;
  const MODES = {
    explore: 'Explain how this repository works and trace its important connections.',
    learn: 'Teach me this repository through a short, source-linked walkthrough.',
    adapt: 'Help me plan an adaptation of this repository. Explain the relevant boundaries and ask what I want to make before changing files.'
  };

  function atlasMessage(mode, codebaseId, nodeId) {
    if (!Object.prototype.hasOwnProperty.call(MODES, mode)) throw new Error('Unknown atlas action.');
    return MODES[mode] + ' Use codebase_understand to ground your answer in the current repository atlas. References: '
      + JSON.stringify({ mode, codebase_id: codebaseId, node_id: nodeId || null }) + '.';
  }

  async function request(url, options) {
    const opts = Object.assign({}, options);
    opts.headers = Object.assign({}, opts.headers);
    if (window.__FRIDAY_API_TOKEN) opts.headers['X-Friday-Token'] = window.__FRIDAY_API_TOKEN;
    const response = await fetch(url, opts);
    const body = await response.json();
    if (!response.ok || body.error || body.status === 'error' || body.status === 'refused' || body.status === 'failed') {
      throw new Error(body.error || body.message || body.say || 'The request could not be completed.');
    }
    return body;
  }

  function neighborhood(atlas, selectedId) {
    const nodes = new Map((atlas.nodes || []).map(n => [n.id, n]));
    const selected = nodes.get(selectedId);
    if (!selected) return { nodes: [], edges: [], total: 0 };
    const edges = (atlas.edges || []).filter(e => (e.source === selectedId || e.target === selectedId)
      && nodes.has(e.source) && nodes.has(e.target));
    const ids = [...new Set(edges.map(e => e.source === selectedId ? e.target : e.source))].filter(id => id !== selectedId);
    const visible = new Set([selectedId].concat(ids.slice(0, 6)));
    return { nodes: [selected].concat(ids.slice(0, 6).map(id => nodes.get(id))),
      edges: edges.filter(e => visible.has(e.source) && visible.has(e.target)), total: ids.length };
  }

  function edgeArrow(edge, selectedId) {
    if (edge.direction === 'bidirectional') return '↔ ';
    const fromSelection = edge.source === selectedId;
    return (edge.direction === 'backward' ? !fromSelection : fromSelection) ? '→ ' : '← ';
  }

  const CSS = `
.fra-atlas{flex:1;min-height:0;overflow:auto;padding:14px;color:var(--fr-text);font:var(--fr-text-base)/1.55 var(--fr-font-body);container-type:inline-size}
.fra-atlas *{box-sizing:border-box}
.fra-atlas button,.fra-atlas input{font:inherit}
.fra-atlas button:focus-visible,.fra-atlas input:focus-visible,.fra-atlas summary:focus-visible,.fra-graph [role=button]:focus-visible{outline:2px solid var(--fr-cyan);outline-offset:3px}
.fra-atlas button{cursor:pointer}
.fra-atlas button:disabled{cursor:default;opacity:.5}
.fra-top,.fra-actions,.fra-stats,.fra-node-head,.fra-tour-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.fra-top{justify-content:space-between;align-items:flex-start}
.fra-eyebrow{font:var(--fr-text-2xs) var(--fr-font-display);letter-spacing:var(--fr-track-label);color:var(--fr-cyan);margin-bottom:5px}
.fra-title{font-size:var(--fr-text-xl);font-weight:600;line-height:1.3;margin:0}
.fra-subtitle{color:var(--fr-label);font-size:var(--fr-text-md);margin:7px 0 10px;overflow-wrap:anywhere}
.fra-btn{border:1px solid var(--fr-glass-edge);border-radius:7px;padding:5px 10px;color:var(--fr-label);background:var(--fr-glass);min-height:32px}
.fra-btn:hover{border-color:var(--fr-cyan);color:var(--fr-cyan)}
.fra-btn.fra-primary{border-color:var(--fr-cyan);color:var(--fr-cyan);background:var(--fr-cyan-soft)}
.fra-stats{gap:5px 12px;color:var(--fr-dim);font:var(--fr-text-xs)/1.5 var(--fr-font-mono);margin:9px 0}
.fra-actions{margin:12px 0}
.fra-actions .fra-btn{flex:1;min-width:70px}
.fra-note{padding:8px 10px;border-left:2px solid var(--fr-cyan);background:var(--fr-cyan-soft);font-size:var(--fr-text-md);margin:10px 0;overflow-wrap:anywhere}
.fra-note.fra-error{border-color:var(--fr-error);color:var(--fr-error);background:var(--fr-glass)}
.fra-note.fra-warning{border-color:var(--fr-warn);color:var(--fr-label);background:var(--fr-glass)}
.fra-search{width:100%;min-width:0;padding:9px 11px;border:1px solid var(--fr-glass-edge);border-radius:7px;background:var(--fr-surface);color:var(--fr-text)}
.fra-search::placeholder{color:var(--fr-dim)}
.fra-list{max-height:190px;overflow:auto;margin:8px 0;border:1px solid var(--fr-glass-edge);border-radius:8px}
.fra-result{display:flex;align-items:center;gap:9px;width:100%;border:0;border-bottom:1px solid var(--fr-glass-edge);background:transparent;color:var(--fr-label);text-align:left;padding:7px 9px;min-height:38px}
.fra-result:last-child{border-bottom:0}
.fra-result[aria-pressed=true]{background:var(--fr-cyan-soft);color:var(--fr-cyan)}
.fra-result:hover{background:var(--fr-cyan-soft)}
.fra-result-main{min-width:0;flex:1}
.fra-result-name{display:block;font-size:var(--fr-text-md);overflow-wrap:anywhere}
.fra-path{display:block;font:var(--fr-text-xs)/1.5 var(--fr-font-mono);color:var(--fr-dim);overflow-wrap:anywhere}
.fra-type{font:var(--fr-text-2xs) var(--fr-font-mono);color:var(--fr-violet-soft);flex-shrink:0}
.fra-count{font-size:var(--fr-text-xs);color:var(--fr-dim);margin:5px 0}
.fra-card{margin-top:12px;border:1px solid var(--fr-glass-edge);border-radius:10px;padding:12px;background:var(--fr-glass)}
.fra-card h3{margin:0;font-size:var(--fr-text-base);line-height:1.5;font-weight:600;overflow-wrap:anywhere}
.fra-node-head{justify-content:space-between;align-items:flex-start}
.fra-node-head>div{flex:1;min-width:0}
.fra-copy{font-size:var(--fr-text-md);color:var(--fr-label);margin:8px 0;overflow-wrap:anywhere;white-space:pre-line}
.fra-graph{display:block;width:100%;height:auto;max-height:270px;margin:4px 0}
.fra-graph line{stroke:var(--fr-violet-soft);stroke-width:1.2;opacity:.45}
.fra-graph circle{fill:var(--fr-surface);stroke:var(--fr-violet-soft);stroke-width:1.5}
.fra-graph [data-selected=true] circle{fill:var(--fr-cyan-soft);stroke:var(--fr-cyan);stroke-width:2}
.fra-graph [role=button]{cursor:pointer}
.fra-graph [role=button]:hover circle{stroke:var(--fr-cyan);stroke-width:2.5}
.fra-graph text{fill:var(--fr-label);font:12px var(--fr-font-mono);pointer-events:none}
.fra-graph [data-selected=true] text{fill:var(--fr-cyan)}
.fra-links{display:flex;flex-direction:column;gap:5px}
.fra-link{display:block;width:100%;border:0;background:transparent;color:var(--fr-cyan);text-align:left;padding:4px 0;font-size:var(--fr-text-md);overflow-wrap:anywhere}
.fra-relation{color:var(--fr-dim);font-size:var(--fr-text-xs);margin-right:7px}
.fra-section{margin-top:12px;border-top:1px solid var(--fr-glass-edge);padding-top:10px}
.fra-section>summary{cursor:pointer;color:var(--fr-label);font-size:var(--fr-text-md);padding:3px 0}
.fra-layer{padding:9px 0;border-bottom:1px solid var(--fr-glass-edge)}
.fra-layer:last-child{border-bottom:0}
.fra-layer strong{font-size:var(--fr-text-md);color:var(--fr-violet-soft)}
.fra-tour-controls{justify-content:space-between;margin-top:10px}
.fra-empty{padding:22px 4px;text-align:center;color:var(--fr-dim);font-size:var(--fr-text-md)}
@container (max-width:300px){.fra-node-head{display:block}.fra-node-head>.fra-btn{margin-top:8px}.fra-atlas .fra-btn{padding:5px 8px}.fra-actions{gap:5px}}
`;

  function Graph({ atlas, selectedId, onSelect }) {
    const graph = neighborhood(atlas, selectedId);
    if (graph.nodes.length < 2) return h('p', { className: 'fra-count' }, 'No mapped connections for this selection.');
    const positions = new Map([[selectedId, [210, 128]]]);
    graph.nodes.slice(1).forEach((node, i) => positions.set(node.id, [i % 2 ? 345 : 75, 35 + Math.floor(i / 2) * 88]));
    return h(React.Fragment, null,
      h('svg', { className: 'fra-graph', viewBox: '0 0 420 260', role: 'group', 'aria-label': 'Connections around the selected item' },
        graph.edges.map((edge, i) => { const a = positions.get(edge.source), b = positions.get(edge.target);
          return h('line', { key: i, x1: a[0], y1: a[1], x2: b[0], y2: b[1] }); }),
        graph.nodes.map(node => { const p = positions.get(node.id), name = String(node.name || node.id);
          return h('g', { key: node.id, transform: 'translate(' + p.join(',') + ')', role: 'button', tabIndex: 0,
            'aria-label': 'Select ' + name, 'aria-pressed': node.id === selectedId, 'data-selected': node.id === selectedId,
            onClick: () => onSelect(node.id), onKeyDown: event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node.id); } } },
            h('title', null, name), h('circle', { r: node.id === selectedId ? 19 : 12 }),
            h('text', { y: 32, textAnchor: 'middle' }, name.length > 18 ? name.slice(0, 16) + '…' : name)); })),
      h('p', { className: 'fra-count' }, graph.total > 6 ? 'Showing 6 of ' + graph.total + ' connected items.' : 'Select a connection to follow it.'));
  }

  function Atlas({ codebase, convId, refreshKey, onOpenFile, onAsk, chatBusy, onShowChat }) {
    const [record, setRecord] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [refresh, setRefresh] = useState(0);
    const [query, setQuery] = useState('');
    const [selection, setSelection] = useState(null);
    const [tourIndex, setTourIndex] = useState(0);
    const [action, setAction] = useState({ busy: false, text: '', error: false });
    const actionOwner = useRef(null);
    const owner = codebase.id + ':' + convId;
    const currentOwner = useRef(owner);
    currentOwner.current = owner;
    useEffect(() => {
      if (!document.getElementById('friday-repo-atlas-css')) {
        const style = document.createElement('style'); style.id = 'friday-repo-atlas-css'; style.textContent = CSS;
        document.head.appendChild(style);
      }
      return () => { currentOwner.current = null; actionOwner.current = null; };
    }, []);
    useEffect(() => { setSelection(null); setQuery(''); setTourIndex(0); setAction({ busy: false, text: '', error: false }); actionOwner.current = null; }, [owner]);
    useEffect(() => {
      const controller = new AbortController();
      let alive = true, timedOut = false;
      const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 20000);
      setLoading(true); setError('');
      request('/api/codebases/' + encodeURIComponent(codebase.id) + '/atlas' + (refresh ? '?refresh=1' : ''), { signal: controller.signal })
        .then(body => {
          if (!alive || controller.signal.aborted) return;
          if (!body.atlas || !Array.isArray(body.atlas.nodes)) throw new Error('The repository map is not available yet.');
          setRecord({ owner, atlas: body.atlas });
        }).catch(reason => {
          if (alive && timedOut) setError('Reading the map took too long. Refresh to try again.');
          else if (alive && reason.name !== 'AbortError') setError(reason.message || 'Could not read the repository map.');
        }).finally(() => { clearTimeout(timeout); if (alive) setLoading(false); });
      return () => { alive = false; clearTimeout(timeout); controller.abort(); };
    }, [owner, refreshKey, refresh]);
    const atlas = record && record.owner === owner ? record.atlas : null;
    const nodes = atlas ? atlas.nodes : [];
    const byId = useMemo(() => new Map(nodes.map(node => [node.id, node])), [nodes]);
    const tour = atlas && Array.isArray(atlas.tour) ? atlas.tour : [];
    const firstTourId = tour.length && (tour[0].nodeIds || []).find(id => byId.has(id));
    const selected = byId.get(selection) || byId.get(firstTourId) || nodes.find(n => n.type === 'file') || nodes[0];
    const selectedId = selected && selected.id;
    const filtered = useMemo(() => {
      const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
      return nodes.filter(node => {
        const text = [node.name, node.type, node.filePath, ...(node.tags || [])].join(' ').toLowerCase();
        return words.every(word => text.includes(word));
      });
    }, [nodes, query]);
    const connected = atlas && selected ? neighborhood(atlas, selected.id) : { edges: [] };
    const friday = atlas && atlas.friday || {};
    const coverage = friday.coverage || {};
    const warnings = [].concat(friday.warnings || [], coverage.limitations || []);
    const stepIndex = Math.min(tourIndex, Math.max(0, tour.length - 1));
    const tourStep = tour[stepIndex];
    const select = id => { if (byId.has(id)) setSelection(id); };
    const openSource = node => { if (node && node.filePath && onOpenFile) onOpenFile({ path: node.filePath, lineRange: node.lineRange }); };
    const followTour = index => { setTourIndex(index); const id = (tour[index].nodeIds || []).find(n => byId.has(n)); if (id) select(id); };
    const ask = async mode => {
      if (actionOwner.current || !atlas || !convId || chatBusy) return;
      if (typeof onAsk !== 'function') {
        setAction({ busy: false, text: 'Open this repository in its chat to ask Friday.', error: true });
        return;
      }
      const ticket = {}; actionOwner.current = ticket;
      setAction({ busy: true, text: 'Friday is responding in this chat…', error: false });
      try {
        // The owning chat renders its optimistic user message, stream, errors
        // and approval/forecast holds. A second transport would hide those.
        await onAsk(atlasMessage(mode, codebase.id, selectedId));
        if (currentOwner.current === owner && actionOwner.current === ticket) {
          setAction({ busy: false, text: 'Follow this request in the chat, including any response or pending decision.', error: false });
        }
      } catch (reason) {
        if (currentOwner.current === owner && actionOwner.current === ticket) setAction({ busy: false, text: reason.message || 'Could not send. Try again.', error: true });
      } finally { if (actionOwner.current === ticket) actionOwner.current = null; }
    };
    const nodeButton = (node, key) => h('button', { type: 'button', key: key || node.id, className: 'fra-link', onClick: () => select(node.id) }, node.name || node.id);

    return h('section', { className: 'fra-atlas', 'data-repo-atlas': codebase.id, 'aria-label': 'Repository understanding' },
      h('div', { className: 'fra-top' }, h('div', null, h('div', { className: 'fra-eyebrow' }, 'THE SALON · UNDERSTAND'), h('h2', { className: 'fra-title' }, 'Repository atlas')),
        h('button', { type: 'button', className: 'fra-btn', disabled: loading, onClick: () => setRefresh(n => n + 1) }, loading ? 'Mapping…' : 'Refresh')),
      error ? h('div', { className: 'fra-note fra-error', role: 'alert' }, error,
        atlas ? ' Showing the previous map; source checks did not complete.' : '') : null,
      loading ? h('div', { className: 'fra-note', role: 'status' }, atlas ? 'Checking the current sources…' : 'Reading the repository and tracing its structure…') : null,
      !atlas && !loading ? h('div', { className: 'fra-empty' }, 'Refresh to try mapping this repository again.') : null,
      atlas ? h(React.Fragment, null,
        h('p', { className: 'fra-subtitle' }, atlas.project && atlas.project.description || 'Find the starting points, follow the connections, then explore them with Friday.'),
        h('div', { className: 'fra-stats' },
          h('span', null, Number.isFinite(coverage.filesScanned) ? coverage.filesScanned + ' files scanned' : 'Imported map'),
          h('span', null, nodes.length + ' items'), h('span', null, (atlas.edges || []).length + ' connections'),
          (atlas.project && atlas.project.languages || []).length ? h('span', null, atlas.project.languages.join(' · ')) : null),
        codebase.source_snapshot ? h('p', { className: 'fra-count' }, 'A text snapshot in the salon. Your original stays untouched; dependencies and binary assets are omitted.') : null,
        coverage.truncated ? h('div', { className: 'fra-note fra-warning' }, 'Partial map · the repository exceeded the bounded scan limits. See “How this map was made” below.') : null,
        friday.stale || atlas.stale ? h('div', { className: 'fra-note fra-warning' }, friday.mode === 'imported'
          ? 'Saved analysis · explanations and line numbers may be older than the source. Verify them with Friday.'
          : 'Showing an earlier map while another scan is in progress.') : null,
        h('div', { className: 'fra-actions', 'aria-label': 'Discuss this repository' },
          [['explore', 'Explore'], ['learn', 'Learn'], ['adapt', 'Adapt']].map(([mode, label]) => h('button', {
            key: mode, type: 'button', className: 'fra-btn' + (mode === 'explore' ? ' fra-primary' : ''), disabled: action.busy || chatBusy || !convId || typeof onAsk !== 'function',
            onClick: () => ask(mode), title: MODES[mode]
          }, label))),
        action.text ? h('div', { className: 'fra-note' + (action.error ? ' fra-error' : ''), role: action.error ? 'alert' : 'status' }, action.text,
          typeof onShowChat === 'function' ? h('button', { type: 'button', className: 'fra-btn', style: { marginLeft: 8 }, onClick: onShowChat }, 'View chat') : null) : null,
        h('input', { className: 'fra-search', type: 'search', value: query, onChange: e => setQuery(e.target.value),
          placeholder: 'Find a file, function, or concept…', 'aria-label': 'Search repository map' }),
        h('div', { className: 'fra-count', role: 'status' }, filtered.length + ' matching items' + (filtered.length > 80 ? ' · showing the first 80; narrow your search' : '')),
        h('div', { className: 'fra-list', 'aria-label': 'Files and symbols' }, filtered.slice(0, 80).map(node => h('button', {
          key: node.id, type: 'button', className: 'fra-result', 'aria-pressed': node.id === selectedId, onClick: () => select(node.id)
        }, h('span', { className: 'fra-result-main' }, h('span', { className: 'fra-result-name' }, node.name || node.id),
          node.filePath ? h('span', { className: 'fra-path' }, node.filePath) : null), h('span', { className: 'fra-type' }, node.type))),
        filtered.length ? null : h('div', { className: 'fra-empty' }, 'No matches. Try a file name or a different term.')),
        selected ? h('div', { className: 'fra-card' },
          h('div', { className: 'fra-node-head' }, h('div', null, h('h3', null, selected.name || selected.id),
            h('span', { className: 'fra-path' }, selected.filePath || selected.type,
              Array.isArray(selected.lineRange) ? ' · lines ' + selected.lineRange.join('–') : '')),
          selected.filePath ? h('button', { type: 'button', className: 'fra-btn', onClick: () => openSource(selected) }, 'Open source') : null),
          selected.summary ? h('p', { className: 'fra-copy' }, selected.summary) : null,
          h(Graph, { atlas, selectedId, onSelect: select }),
          h('div', { className: 'fra-links', 'aria-label': 'Mapped relationships' }, connected.edges.slice(0, 12).map((edge, i) => {
            const outgoing = edge.source === selectedId, target = byId.get(outgoing ? edge.target : edge.source);
            return h('button', { key: i, type: 'button', className: 'fra-link', onClick: () => select(target.id) },
              h('span', { className: 'fra-relation' }, edgeArrow(edge, selectedId) + edge.type), target.name || target.id);
          }))) : h('div', { className: 'fra-empty' }, 'No supported source files were found in this repository.'),
        h('details', { className: 'fra-section', open: true }, h('summary', null, 'Architecture · ' + (atlas.layers || []).length + ' layers'),
          (atlas.layers || []).map(layer => h('div', { key: layer.id, className: 'fra-layer' }, h('strong', null, layer.name),
            h('p', { className: 'fra-copy' }, layer.description),
            (layer.nodeIds || []).slice(0, 5).map(id => byId.has(id) ? nodeButton(byId.get(id)) : null)))),
        tourStep ? h('details', { className: 'fra-section', open: true }, h('summary', null, 'Guided tour · ' + tour.length + ' stops'),
          h('div', { className: 'fra-layer' }, h('strong', null, (stepIndex + 1) + '. ' + tourStep.title),
            h('p', { className: 'fra-copy' }, tourStep.description),
            (tourStep.nodeIds || []).map(id => byId.has(id) ? nodeButton(byId.get(id)) : null),
            h('div', { className: 'fra-tour-controls' }, h('button', { type: 'button', className: 'fra-btn', disabled: stepIndex === 0, onClick: () => followTour(stepIndex - 1) }, 'Previous'),
              h('span', { className: 'fra-count' }, (stepIndex + 1) + ' / ' + tour.length),
              h('button', { type: 'button', className: 'fra-btn', disabled: stepIndex >= tour.length - 1, onClick: () => followTour(stepIndex + 1) }, 'Next')))) : null,
        h('details', { className: 'fra-section' }, h('summary', null, 'How this map was made'),
          h('p', { className: 'fra-copy' }, friday.mode === 'imported' ? 'Uses a saved Understand Anything map. Existing file paths are checked; its analysis and line numbers may be outdated.' : 'Built locally from source files. Connections describe what the scanner could verify; they are not a complete runtime trace.'),
          Number.isFinite(coverage.filesParsed) ? h('p', { className: 'fra-count' }, coverage.filesParsed + ' files parsed · ' + (coverage.filesSkipped || 0) + ' skipped') : null,
          codebase.source_snapshot ? h('div', null,
            h('p', { className: 'fra-count' }, (codebase.source_snapshot.copied_files || 0) + ' text files copied · '
              + (codebase.source_snapshot.skipped_entries || 0) + ' entries omitted (a skipped folder counts as one entry).'),
            (codebase.source_snapshot.limitations || []).map((limitation, i) => h('p', { key: 'intake-' + i, className: 'fra-copy' }, String(limitation)))) : null,
          warnings.map((warning, i) => h('p', { key: i, className: 'fra-copy' }, String(warning))),
          h('p', { className: 'fra-count' }, 'Inspired by Understand Anything. Explanations use Friday’s existing chat, model choice, and privacy controls.'))) : null
    );
  }
  window.FridayRepoAtlas = Atlas;
  window.FridayRepoAtlasData = { atlasMessage, neighborhood, edgeArrow };
})();
