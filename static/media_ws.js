/* Media: one home for everything Friday makes or helps make.
 *
 * One CARD per piece of work (a draft, an article, an episode, an image set, a
 * video, a page, a chart, a document, a deck, a post, a codebase), with its type,
 * status, provenance, privacy and where it has been published. Three views of the
 * same cards: the Library, the Pipeline board by status, and the Calendar of what
 * went out and what is due. A card opens into one editor frame with the right
 * tool for its medium and "turn this into…". The spec is
 * docs/design/active/media-workspace.md.
 *
 * Rules this file keeps:
 *   - the shell owns the frame: the root is .ws-fill, fills what it is given,
 *     never sets a width, a top offset or a viewport-height calc; a readable measure goes
 *     on text (80ch), never on the root;
 *   - --fr-* tokens only; amber only where something needs the owner; status is
 *     always a word; a selected segment is .btn.active with aria-pressed;
 *   - every move into Published goes through the server, which raises the one
 *     approval card; this file never marks a card published by itself;
 *   - everything has a key, and every key is in the help line.
 *
 * Loaded by index.html as a plain script; defines window.MediaWS.
 */
(function () {
  'use strict';
  if (window.MediaWS) return;
  const h = React.createElement;
  const { useState, useEffect, useRef, useMemo, useCallback } = React;

  function api(url, opts) {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  }
  function json(url, opts) {
    return api(url, opts).then(r => r.json().then(d => { d.__http = r.status; return d; }));
  }
  function post(url, body, method) {
    return json(url, { method: method || 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }
  const toast = msg => (window.fridayToast ? window.fridayToast(msg) : console.log('[media]', msg));

  // ── vocabulary ───────────────────────────────────────────────────────────
  // The five statuses. The board's columns, the card's strip, the filters: one list.
  const STATUSES = [
    ['idea', 'Idea'], ['draft', 'Draft'], ['review', 'In review'], ['scheduled', 'Scheduled'], ['published', 'Published']
  ];
  const STATUS_WORD = Object.fromEntries(STATUSES);
  const STATUS_PILL = { idea: 'md-pill-dim', draft: 'md-pill-working', review: 'md-pill-needs-you', scheduled: 'md-pill-neutral', published: 'md-pill-ok' };
  const KIND_WORD = {
    draft: 'Draft', article: 'Article', episode: 'Episode', imageset: 'Image set', image: 'Image', video: 'Video',
    page: 'Page', chart: 'Chart', doc: 'Document', deck: 'Deck', sheet: 'Sheet', post: 'Post', code: 'Codebase',
    music: 'Music', audio: 'Audio', model3d: '3D', timeline: 'Cut', file: 'File'
  };
  const KIND_GLYPH = {
    image: 'M4 6h24v20H4z M11 13m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0 M4 23l7-7 6 6 4-4 7 7',
    imageset: 'M4 6h24v20H4z M11 13m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0 M4 23l7-7 6 6 4-4 7 7',
    video: 'M4 7h18v18H4z M22 13l6-3v12l-6-3z', timeline: 'M4 7h18v18H4z M22 13l6-3v12l-6-3z',
    episode: 'M12 4h8v14h-8z M7 14a9 9 0 0 0 18 0 M16 23v5 M11 28h10',
    audio: 'M6 12v8h5l7 6V6l-7 6z M22 11a7 7 0 0 1 0 10 M25 8a11 11 0 0 1 0 16',
    music: 'M12 24V8l14-3v16 M9 24m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0 M23 21m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0',
    draft: 'M9 4h10l7 7v17H9z M19 4v7h7 M13 17h10 M13 22h10', article: 'M9 4h10l7 7v17H9z M19 4v7h7 M13 17h10 M13 22h10',
    doc: 'M9 4h10l7 7v17H9z M19 4v7h7 M13 17h10 M13 22h10', file: 'M9 4h10l7 7v17H9z M19 4v7h7',
    deck: 'M5 7h22v18H5z M5 13h22 M12 13v12 M19 13v12', sheet: 'M5 7h22v18H5z M5 13h22 M12 13v12 M19 13v12',
    chart: 'M5 27h22 M8 15h4v9H8z M15 9h4v15h-4z M22 12h4v12h-4z',
    model3d: 'M16 4l11 6v12l-11 6-11-6V10z M5 10l11 6 11-6 M16 16v12',
    page: 'M4 6h24v20H4z M4 12h24 M9 9h.01 M13 9h.01',
    code: 'M12 10l-6 6 6 6 M20 10l6 6-6 6 M18 7l-4 18',
    post: 'M6 8h20v12H13l-5 4v-4H6z M11 13h10 M11 16h6'
  };
  function glyph(kind, word) {
    const d = KIND_GLYPH[kind] || KIND_GLYPH.file;
    return h('span', { className: 'md-type' },
      h('svg', { viewBox: '0 0 32 32', 'aria-hidden': 'true' }, h('path', { d })),
      word == null ? (KIND_WORD[kind] || kind) : word);
  }
  function pill(cls, word) { return h('span', { className: 'md-pill ' + cls }, word); }
  function statusPill(c) {
    if (c.held) return pill('md-pill-needs-you', 'Held for you');
    const w = STATUS_WORD[c.status] || c.status;
    const badge = (c.badges || []).filter(b => b !== 'held')[0];
    return pill(STATUS_PILL[c.status] || 'md-pill-dim', badge ? w + ' · ' + badge : w);
  }
  function privacyPill(c) {
    if (c.privacy === 'published') return pill('md-pill-public', 'Published');
    if (c.privacy === 'shared') return pill('md-pill-public', 'Shared');
    return pill('md-pill-private', 'Private');
  }
  function fmtWhen(iso) {
    if (!iso) return '';
    const d = new Date(iso); if (isNaN(d)) return '';
    const now = new Date();
    const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate());
    const diff = Math.round((day(d) - day(now)) / 86400000);
    const hm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    if (diff === 0) return 'Today ' + hm;
    if (diff === 1) return 'Tomorrow ' + hm;
    if (diff === -1) return 'Yesterday';
    if (Math.abs(diff) < 7) return d.toLocaleDateString(undefined, { weekday: 'short' }) + (diff > 0 ? ' ' + hm : '');
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  // ── styles: injected once, tokens only ───────────────────────────────────
  const CSS = `
.md-root{display:flex;flex-direction:column;gap:10px;flex:1 1 auto;min-height:0;min-width:0;color:var(--fr-text);font-family:var(--fr-font-body);font-size:var(--fr-text-base)}
.md-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.md-head h2{margin:0;font-size:var(--fr-text-xl);font-weight:600}
.md-seg{display:inline-flex;gap:4px;flex-wrap:wrap}
.md-count{color:var(--fr-dim);font-size:var(--fr-text-md)}
.md-spacer{flex:1}
.md-label{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);text-transform:uppercase;color:var(--fr-dim)}
.md-pill{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:.12em;text-transform:uppercase;padding:2px 7px;border-radius:10px;border:1px solid;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;vertical-align:middle}
.md-pill::before{content:'';width:6px;height:6px;border-radius:3px;background:currentColor}
.md-pill-ok{color:var(--fr-ok);border-color:rgba(0,255,128,.45)}
.md-pill-working{color:var(--fr-violet-soft);border-color:rgba(167,139,250,.5)}
.md-pill-needs-you{color:var(--fr-warn);border-color:rgba(245,158,11,.5)}
.md-pill-neutral{color:var(--fr-neutral);border-color:rgba(122,134,153,.5)}
.md-pill-error{color:var(--fr-error);border-color:rgba(239,68,68,.5)}
.md-pill-dim{color:var(--fr-dim);border-color:var(--fr-glass-edge)}
.md-pill-private{color:var(--fr-violet-soft);border-color:rgba(167,139,250,.5)}
.md-pill-public{color:var(--fr-cat-blue);border-color:rgba(96,165,250,.5)}
.md-pill-dim::before,.md-pill-private::before,.md-pill-public::before{display:none}
.md-type{display:inline-flex;align-items:center;gap:5px;font-size:var(--fr-text-sm);color:var(--fr-label)}
.md-type svg{width:14px;height:14px;stroke:var(--fr-cyan);fill:none;stroke-width:1.3;stroke-linecap:round;stroke-linejoin:round}
.md-lib{display:grid;grid-template-columns:200px 1fr;gap:14px;flex:1 1 auto;min-height:0}
.md-rail{display:flex;flex-direction:column;gap:8px;overflow:auto;min-height:0}
.md-group{display:flex;flex-direction:column;gap:1px}
.md-group button{background:transparent;border:0;color:var(--fr-label);text-align:left;padding:5px 8px;border-radius:6px;display:flex;justify-content:space-between;font:inherit;font-size:var(--fr-text-md);cursor:pointer}
.md-group button:hover{background:var(--fr-cyan-soft)}
.md-group button[aria-current="true"]{background:var(--fr-cyan-soft);color:var(--fr-cyan)}
.md-group button .n{color:var(--fr-dim);font-family:var(--fr-font-mono);font-size:var(--fr-text-xs)}
.md-rail-note{font-size:var(--fr-text-sm);color:var(--fr-dim);padding:4px 8px}
.md-main{display:flex;flex-direction:column;gap:10px;min-width:0;min-height:0}
.md-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.md-toolbar input[type=search]{flex:1 1 240px;max-width:460px;font:inherit;color:var(--fr-text);background:rgba(0,0,0,.35);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:7px 10px}
.md-toolbar input[type=search]:focus{outline:none;border-color:var(--fr-cyan);box-shadow:0 0 0 3px rgba(0,212,255,.35)}
.md-toolbar select{font:inherit;color:var(--fr-text);background:rgba(0,0,0,.35);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:6px 8px}
.md-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,max(220px,calc((100% - 60px) / 6))),1fr));gap:12px;overflow:auto;min-height:0;align-content:start;padding:2px}
.md-grid.list{grid-template-columns:1fr}
.md-card{background:rgba(255,255,255,.035);border:1px solid var(--fr-glass-edge);border-radius:10px;overflow:hidden;display:flex;flex-direction:column;cursor:pointer;text-align:left;padding:0;color:inherit;font:inherit}
.md-card:hover,.md-card:focus-visible{border-color:rgba(0,212,255,.35);outline:none}
.md-card[aria-selected="true"]{border-color:var(--fr-cyan);box-shadow:0 0 0 1px var(--fr-cyan) inset}
.md-thumb{aspect-ratio:16/10;background:linear-gradient(135deg,rgba(0,212,255,.10),rgba(123,97,255,.10) 50%,rgba(255,0,255,.08));display:grid;place-items:center;color:var(--fr-dim);font-family:var(--fr-font-mono);font-size:var(--fr-text-xs);position:relative;overflow:hidden}
.md-thumb img{width:100%;height:100%;object-fit:cover}
.md-thumb .dur{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.6);padding:1px 5px;border-radius:4px;color:var(--fr-label)}
.md-body{padding:8px 10px 10px;display:flex;flex-direction:column;gap:4px;min-width:0}
.md-title{font-weight:600;font-size:var(--fr-text-md);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.md-meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap;color:var(--fr-dim);font-size:var(--fr-text-sm)}
.md-grid.list .md-card{flex-direction:row;align-items:center}
.md-grid.list .md-thumb{width:96px;flex:0 0 96px}
.md-grid.list .md-body{flex:1}
.md-empty{border:1px dashed var(--fr-glass-edge);border-radius:10px;padding:22px;text-align:center;color:var(--fr-dim)}
.md-empty b{color:var(--fr-label);font-weight:600}
.md-keys{font-size:var(--fr-text-sm);color:var(--fr-dim)}
.md-keys kbd{font-family:var(--fr-font-mono);font-size:var(--fr-text-xs);border:1px solid var(--fr-glass-edge);border-bottom-width:2px;border-radius:4px;padding:0 5px;color:var(--fr-dim)}
.md-detail{position:absolute;right:0;top:0;bottom:0;width:min(360px,100%);background:var(--fr-glass);backdrop-filter:var(--fr-glass-blur);border-left:1px solid var(--fr-glass-edge);padding:14px;display:flex;flex-direction:column;gap:10px;z-index:5;overflow:auto}
.md-detail .md-head-row{display:flex;justify-content:space-between;align-items:center;gap:10px}
.md-kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;font-size:var(--fr-text-md);margin:0}
.md-kv dt{color:var(--fr-dim)}.md-kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
.md-actions{display:flex;gap:8px;flex-wrap:wrap}
.md-actions .btn{flex:1 1 45%}
.md-stage-wrap{position:relative;flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.md-note{font-size:var(--fr-text-md);color:var(--fr-dim);border-left:2px solid rgba(0,212,255,.35);padding-left:10px;max-width:80ch}
@media (max-width:900px){.md-lib{grid-template-columns:1fr}.md-rail{flex-direction:row;flex-wrap:wrap}}
`;
  function ensureStyles() {
    if (document.getElementById('media-ws-styles')) return;
    const s = document.createElement('style'); s.id = 'media-ws-styles'; s.textContent = CSS; document.head.appendChild(s);
  }

  // ── data ─────────────────────────────────────────────────────────────────
  const DEFAULT_VIEWS = [['today', 'Today'], ['progress', 'In progress'], ['review', 'Needs you'], ['published', 'Published'], ['all', 'Everything']];
  const TYPE_GROUPS = [
    ['draft', 'Drafts'], ['article', 'Articles'], ['post', 'Posts'], ['episode', 'Episodes'], ['imageset', 'Images'],
    ['video', 'Video'], ['audio', 'Audio and music'], ['deck', 'Decks and documents'], ['chart', 'Charts and data'],
    ['page', 'Pages'], ['code', 'Codebases']
  ];
  function buildQuery(f) {
    const p = new URLSearchParams();
    if (f.view && f.view !== 'all') p.set('view', f.view);
    if (f.kind) p.set('kind', f.kind);
    if (f.project != null) p.set('project', f.project);
    if (f.privacy) p.set('privacy', f.privacy);
    if (f.unsigned) p.set('unsigned', '1');
    if (f.q) p.set('q', f.q);
    if (f.sort) p.set('sort', f.sort);
    if (f.status) p.set('status', f.status);
    return p.toString();
  }
  function useCards(filters) {
    const [state, setState] = useState({ cards: [], counts: {}, projects: [], loading: true, error: null });
    const key = buildQuery(filters);
    const reload = useCallback(() => {
      setState(s => Object.assign({}, s, { loading: true }));
      json('/api/media?' + key).then(d => {
        if (d.status !== 'ok') { setState({ cards: [], counts: {}, projects: [], loading: false, error: d.message || 'Media could not load.' }); return; }
        setState({ cards: d.cards || [], counts: d.counts || {}, projects: d.projects || [], loading: false, error: null });
      }).catch(() => setState({ cards: [], counts: {}, projects: [], loading: false, error: 'Media could not load.' }));
    }, [key]);
    useEffect(() => { reload(); }, [reload]);
    useEffect(() => {
      const on = () => reload();
      window.addEventListener('friday:media-changed', on);
      return () => window.removeEventListener('friday:media-changed', on);
    }, [reload]);
    return [state, reload];
  }
  function changed() { window.dispatchEvent(new Event('friday:media-changed')); }

  // ── one card ─────────────────────────────────────────────────────────────
  function Card({ c, selected, onOpen, onSelect, noThumb }) {
    const extra = c.duration ? c.duration : c.count ? c.count + ' takes' : c.pages ? c.pages + ' pages' : c.words ? c.words + ' words' : null;
    const where = c.published_at ? h('div', { className: 'md-meta' }, h('span', null, 'at'), h('span', null, c.published_at))
      : (c.targets && c.targets.length ? h('div', { className: 'md-meta' }, h('span', null, 'to'), h('span', null, c.targets.join(', '))) : null);
    return h('button', {
      className: 'md-card', role: 'option', 'aria-selected': selected ? 'true' : 'false', 'data-id': c.id,
      onClick: () => onSelect && onSelect(c), onDoubleClick: () => onOpen && onOpen(c),
      onKeyDown: e => { if (e.key === 'Enter') { e.preventDefault(); onOpen && onOpen(c); } }
    },
      noThumb ? null : h('div', { className: 'md-thumb' },
        c.thumb ? h('img', { src: c.thumb, alt: '' }) : (KIND_WORD[c.kind] || c.kind),
        extra ? h('span', { className: 'dur' }, extra) : null),
      h('div', { className: 'md-body' },
        h('div', { className: 'md-title', title: c.title }, c.title),
        h('div', { className: 'md-meta' }, glyph(c.kind), h('span', null, '·'), h('span', null, fmtWhen(c.when))),
        h('div', { className: 'md-meta' }, statusPill(c), privacyPill(c), c.signed ? null : pill('md-pill-neutral', 'Unsigned')),
        where));
  }

  // ── the details panel (Library) ──────────────────────────────────────────
  function Details({ c, onClose, onOpen, onAction }) {
    if (!c) return null;
    return h('aside', { className: 'md-detail', 'aria-label': 'Card details' },
      h('div', { className: 'md-head-row' }, h('strong', null, c.title), h('button', { className: 'btn', onClick: onClose, 'aria-label': 'Close details' }, 'Esc')),
      h('div', { className: 'md-meta' }, glyph(c.kind), h('span', null, '·'), h('span', null, fmtWhen(c.when))),
      h('dl', { className: 'md-kv' },
        h('dt', null, 'Status'), h('dd', null, statusPill(c), c.targets && c.targets.length ? ' → ' + c.targets.join(', ') : null),
        h('dt', null, 'Made by'), h('dd', null, c.maker || 'You'),
        h('dt', null, 'From'), h('dd', null, (c.sources || []).length ? c.sources.join(', ') : 'Nothing yet'),
        h('dt', null, 'Credentials'), h('dd', null, c.signed ? [pill('md-pill-ok', 'Signed'), ' content credentials'] : [pill('md-pill-neutral', 'Unsigned'), ' signed when it leaves']),
        h('dt', null, 'Privacy'), h('dd', null, privacyPill(c), ' ', c.published_at ? c.published_at : 'never left this computer'),
        h('dt', null, 'Project'), h('dd', null, c.project || '—')),
      h('div', { className: 'md-label' }, 'Actions'),
      h('div', { className: 'md-actions' },
        h('button', { className: 'btn active', onClick: () => onOpen(c) }, 'Open'),
        h('button', { className: 'btn', onClick: () => onAction('tab', c) }, 'Own tab'),
        h('button', { className: 'btn', onClick: () => onAction('turn', c) }, 'Turn this into…'),
        h('button', { className: 'btn', onClick: () => onAction('send', c) }, 'Send to…'),
        h('button', { className: 'btn', onClick: () => onAction('publish', c) }, c.status === 'published' ? 'Unpublish…' : 'Publish…'),
        h('button', { className: 'btn btn-magenta', onClick: () => onAction('delete', c) }, 'Delete')));
  }

  // ── the Library ──────────────────────────────────────────────────────────
  function Library({ filters, setFilters, sel, setSel, onOpen, onAction }) {
    const [state] = useCards(filters);
    const [layout, setLayout] = useState('grid');
    const searchRef = useRef(null);
    const cards = state.cards;
    const selCard = cards.find(c => c.id === sel) || null;
    const current = filters.kind ? 'kind:' + filters.kind : filters.project != null ? 'proj:' + filters.project : filters.privacy ? 'priv:' + filters.privacy : filters.unsigned ? 'unsigned' : 'view:' + (filters.view || 'today');
    const pick = useCallback(patch => { setFilters(Object.assign({ view: 'all', kind: null, project: null, privacy: null, unsigned: false, q: filters.q, sort: filters.sort }, patch)); setSel(null); }, [filters.q, filters.sort, setFilters, setSel]);
    useEffect(() => {
      const onKey = e => {
        const typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName) || (document.activeElement && document.activeElement.isContentEditable);
        if (e.key === '/' && !typing) { e.preventDefault(); searchRef.current && searchRef.current.focus(); return; }
        if (e.key === 'Escape') { setSel(null); return; }
        if (typing) return;
        const ids = cards.map(c => c.id); const i = ids.indexOf(sel);
        if (e.key === 'ArrowRight' && ids.length) { e.preventDefault(); setSel(ids[Math.min(ids.length - 1, i + 1)]); }
        if (e.key === 'ArrowLeft' && ids.length) { e.preventDefault(); setSel(ids[Math.max(0, i - 1)]); }
        if (e.key === 'Enter' && selCard) onOpen(selCard);
        if ((e.key === 'n' || e.key === 'N')) onAction('new');
        if ((e.key === 'o' || e.key === 'O') && selCard) onAction('tab', selCard);
        if ((e.key === 't' || e.key === 'T') && selCard) onAction('turn', selCard);
        if ((e.key === 's' || e.key === 'S') && selCard) onAction('send', selCard);
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [cards, sel, selCard, onOpen, onAction, setSel]);
    const counts = state.counts || {};
    const railBtn = (key, label, n, patch) => h('button', { key, 'aria-current': current === key ? 'true' : undefined, onClick: () => pick(patch) }, label, n != null ? h('span', { className: 'n' }, n) : null);
    return h('div', { className: 'md-lib' },
      h('aside', { className: 'md-rail', 'aria-label': 'Library' },
        h('div', { className: 'md-label' }, 'Default views'),
        h('div', { className: 'md-group' }, DEFAULT_VIEWS.map(v => railBtn('view:' + v[0], v[1], counts[v[0]], { view: v[0] }))),
        h('div', { className: 'md-label' }, 'Projects'),
        h('div', { className: 'md-group' },
          (state.projects || []).map(p => railBtn('proj:' + p.name, p.name, p.n, { project: p.name })),
          railBtn('proj:', 'No project', counts.no_project, { project: '' }),
          h('button', { key: 'newproj', onClick: () => onAction('project') }, '+ New project')),
        h('div', { className: 'md-label' }, 'Type'),
        h('div', { className: 'md-group' }, TYPE_GROUPS.map(t => railBtn('kind:' + t[0], t[1], (counts.kinds || {})[t[0]], { kind: t[0] }))),
        h('div', { className: 'md-label' }, 'Privacy'),
        h('div', { className: 'md-group' },
          railBtn('priv:private', 'Private to this PC', counts.private, { privacy: 'private' }),
          railBtn('priv:shared', 'Shared or published', counts.shared, { privacy: 'shared' }),
          railBtn('unsigned', 'Unsigned', counts.unsigned, { unsigned: true })),
        h('div', { className: 'md-rail-note' }, 'Not here: the News editions and their shows (in News); your wiki (in Knowledge). Search finds them and links across.')),
      h('section', { className: 'md-main', 'aria-label': 'Cards' },
        h('div', { className: 'md-toolbar' },
          h('input', { ref: searchRef, type: 'search', placeholder: 'Search titles, text, sources, transcripts…  /', 'aria-label': 'Search', value: filters.q || '', onChange: e => setFilters(Object.assign({}, filters, { q: e.target.value })) }),
          h('span', { className: 'md-spacer' }),
          h('select', { 'aria-label': 'Sort', value: filters.sort || 'next', onChange: e => setFilters(Object.assign({}, filters, { sort: e.target.value })) },
            h('option', { value: 'next' }, 'By what matters next'), h('option', { value: 'newest' }, 'Newest first'), h('option', { value: 'title' }, 'By title'), h('option', { value: 'status' }, 'By status')),
          h('div', { className: 'md-seg', role: 'group', 'aria-label': 'Layout' },
            h('button', { className: 'btn' + (layout === 'grid' ? ' active' : ''), 'aria-pressed': layout === 'grid', onClick: () => setLayout('grid') }, 'Grid'),
            h('button', { className: 'btn' + (layout === 'list' ? ' active' : ''), 'aria-pressed': layout === 'list', onClick: () => setLayout('list') }, 'List'))),
        h('div', { className: 'md-stage-wrap' },
          state.error ? h('div', { className: 'md-empty', role: 'alert' }, h('b', null, state.error), h('br'), 'Try again in a moment.') :
          !state.loading && cards.length === 0 ? h('div', { className: 'md-empty' }, h('b', null, 'Nothing here.'), h('br'), 'Pick another view on the left, or press ', h('kbd', null, 'N'), ' for a new card.') :
          h('div', { className: 'md-grid' + (layout === 'list' ? ' list' : ''), role: 'listbox', 'aria-label': 'Cards', 'aria-busy': state.loading ? 'true' : 'false' },
            cards.map(c => h(Card, { key: c.id, c, selected: c.id === sel, onSelect: x => setSel(x.id), onOpen }))),
          h(Details, { c: selCard, onClose: () => setSel(null), onOpen, onAction })),
        h('p', { className: 'md-keys' }, 'Keys: ', h('kbd', null, '/'), ' search · ', h('kbd', null, 'N'), ' new · ', h('kbd', null, '←'), ' ', h('kbd', null, '→'), ' move · ', h('kbd', null, 'Enter'), ' open · ', h('kbd', null, 'T'), ' turn this into… · ', h('kbd', null, 'S'), ' send to · ', h('kbd', null, 'O'), ' own tab · ', h('kbd', null, 'Esc'), ' close.')));
  }

  // ── the workspace ────────────────────────────────────────────────────────
  const VIEWS = [['library', 'Library'], ['board', 'Pipeline'], ['calendar', 'Calendar']];
  function MediaWS() {
    ensureStyles();
    const [view, setView] = useState('library');
    const [card, setCard] = useState(null);           // the open card id (editor), or null
    const [sel, setSel] = useState(null);             // the selected card id (details)
    const [filters, setFilters] = useState({ view: 'today', kind: null, project: null, privacy: null, unsigned: false, q: '', sort: 'next' });
    const [counts, setCounts] = useState({});
    useEffect(() => { json('/api/media?view=all&limit=0').then(d => { if (d.status === 'ok') setCounts(d.counts || {}); }).catch(() => {}); }, [view]);

    // Navigation in: {view, card, q, status, kind, project}, from a deep link, a tab
    // opened on an item, navigate_to or voice. The page stashes the target on
    // window.__fridayNavTarget and fires 'friday-nav' (the mail panel's pattern).
    useEffect(() => {
      const apply = t => {
        if (!t || (t.workspace && t.workspace !== 'media')) return;
        if (t.card) { setCard(t.card); return; }
        if (t.view && (t.view === 'insights' || VIEWS.some(v => v[0] === t.view))) { setCard(null); setView(t.view); }
        const patch = {};
        if (t.q != null) patch.q = String(t.q);
        if (t.kind) patch.kind = t.kind;
        if (t.project != null) patch.project = t.project;
        if (t.default_view) patch.view = t.default_view;
        if (t.status) patch.status = t.status;
        if (Object.keys(patch).length) { setFilters(f => Object.assign({}, f, patch)); setCard(null); setView('library'); }
      };
      apply(window.__fridayNavTarget);
      const f = e => apply(e.detail || {});
      window.addEventListener('friday-nav', f);
      return () => window.removeEventListener('friday-nav', f);
    }, []);
    // Navigation out: what a tab or a pop-out should carry (ids and a view, never text).
    const tabState = window.useTabState || window.fridayUseTabState;
    if (tabState) tabState('media', () => (card ? { view: 'card', card } : { view, q: filters.q || undefined, kind: filters.kind || undefined, default_view: filters.view }));

    const openCard = useCallback(c => { setCard(c.id); }, []);
    // A card opened from anywhere (a relation link, a "turn this into…" result, a notification).
    useEffect(() => { window.fridayMediaOpen = id => { setCard(id); setView('library'); }; return () => { if (window.fridayMediaOpen) delete window.fridayMediaOpen; }; }, []);
    const onAction = useCallback((what, c) => {
      if (what === 'new') { post('/api/media', { kind: 'draft', title: 'Untitled' }).then(d => { if (d.status === 'ok') { changed(); setCard(d.card.id); } else toast(d.message || 'Could not make a card.'); }); return; }
      if (what === 'project') { const name = prompt('Project name'); if (name) toast('Pick a card and set its project to "' + name + '" from its editor; a project exists once a card belongs to it.'); return; }
      if (!c) return;
      if (what === 'tab') { if (window.fridayOpenWorkspaceTab) window.fridayOpenWorkspaceTab('media', { view: 'card', card: c.id }); return; }
      if (what === 'send') { if (window.fridaySendTo) window.fridaySendTo({ text: c.title, title: c.title, card: c.id, path: c.path }); else toast('Send to… is not available here.'); return; }
      if (what === 'turn') { setCard(c.id); window.dispatchEvent(new CustomEvent('friday:media-turn', { detail: { id: c.id } })); return; }
      if (what === 'publish') {
        post('/api/media/' + encodeURIComponent(c.id) + '/' + (c.status === 'published' ? 'unpublish' : 'publish')).then(d => {
          if (d.status === 'pending') toast('A card is asking you first: nothing leaves this computer until you approve it.');
          else if (d.status === 'ok') { toast(d.message || 'Done.'); changed(); }
          else toast(d.message || 'Could not publish.');
        }); return;
      }
      if (what === 'delete') {
        if (!confirm('Delete "' + c.title + '"? The receipt stays.')) return;
        json('/api/media/' + encodeURIComponent(c.id), { method: 'DELETE' }).then(d => { if (d.status === 'ok') { toast('Deleted.'); setSel(null); setCard(null); changed(); } else if (d.status === 'pending') toast('Deleting that asks on a card first.'); else toast(d.message || 'Could not delete.'); });
      }
    }, []);

    const head = h('div', { className: 'md-head' },
      h('h2', null, 'Media'),
      h('div', { className: 'md-seg', role: 'tablist', 'aria-label': 'Views' },
        VIEWS.map(v => h('button', { key: v[0], role: 'tab', className: 'btn' + (view === v[0] && !card ? ' active' : ''), 'aria-selected': view === v[0] && !card, 'aria-pressed': view === v[0] && !card, onClick: () => { setCard(null); setView(v[0]); } }, v[1]))),
      h('span', { className: 'md-count' }, (counts.all != null ? counts.all + ' pieces of work' : '') + (counts.progress != null ? ' · ' + counts.progress + ' in progress' : '') + (counts.published != null ? ' · ' + counts.published + ' published' : '')),
      h('span', { className: 'md-spacer' }),
      h('button', { className: 'btn' + (view === 'insights' && !card ? ' active' : ''), 'aria-pressed': view === 'insights' && !card, title: 'How published cards did, and the best times to post', onClick: () => { setCard(null); setView('insights'); } }, 'Insights'),
      h('button', { className: 'btn', title: 'Open Media in its own tab', onClick: () => window.fridayOpenWorkspaceTab && window.fridayOpenWorkspaceTab('media', card ? { view: 'card', card } : { view }) }, '⧉ Own tab'),
      h('button', { className: 'btn active', onClick: () => onAction('new') }, '+ New'));

    let body;
    if (card) body = h(window.MediaCard || Placeholder, { id: card, onBack: () => setCard(null), onAction });
    else if (view === 'insights') body = h(window.MediaInsights || Placeholder, { onBack: () => setView('library') });
    else if (view === 'board') body = h(window.MediaBoard || Placeholder, { onOpen: openCard, onAction });
    else if (view === 'calendar') body = h(window.MediaCalendar || Placeholder, { onOpen: openCard, onAction });
    else body = h(Library, { filters, setFilters, sel, setSel, onOpen: openCard, onAction });
    return h('div', { className: 'md-root ws-fill' }, head, body);
  }
  function Placeholder() { return h('div', { className: 'md-empty' }, 'This view is on its way.'); }

  // ── the Pipeline board ───────────────────────────────────────────────────
  // Five columns, the status vocabulary. Drag or `]` advances. Into Scheduled
  // asks when; into Published always goes through the server, which raises the
  // one approval card and refuses a move that carries no approval.
  const LANE_NOTE = {
    idea: 'A line, a link, a thought. No file yet.',
    draft: 'Being made: by you, by Friday, or both.',
    review: 'Needs your eyes, or a held post waiting for a yes.',
    scheduled: 'Has a time and a place. Friday asks again before it goes.',
    published: 'Left this computer, or finished and kept. The receipt is on the card.'
  };
  const BOARD_CSS = `
.md-board{display:grid;grid-template-columns:repeat(5,minmax(200px,1fr));gap:10px;flex:1 1 auto;min-height:0;overflow:auto}
.md-col{display:flex;flex-direction:column;gap:8px;min-width:0;min-height:0}
.md-colhead{display:flex;align-items:center;gap:8px;padding:4px 2px;border-bottom:1px solid var(--fr-glass-edge);font-size:var(--fr-text-md);color:var(--fr-label);font-weight:600}
.md-colhead .n{color:var(--fr-dim);font-family:var(--fr-font-mono);font-size:var(--fr-text-xs);font-weight:400}
.md-colhead .gate{margin-left:auto}
.md-lane-note{font-size:var(--fr-text-sm);color:var(--fr-dim);padding:2px}
.md-drop{flex:1;display:flex;flex-direction:column;gap:8px;min-height:120px;border-radius:10px;padding:2px;overflow:auto}
.md-drop.over{outline:1px dashed var(--fr-cyan);background:var(--fr-cyan-soft)}
.md-board .md-card{cursor:grab}
.md-filters{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.md-filters select{font:inherit;color:var(--fr-text);background:rgba(0,0,0,.35);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:6px 8px}
@media (max-width:900px){.md-board{grid-template-columns:repeat(5,220px)}}
`;
  function nextStatus(s) { const o = STATUSES.map(x => x[0]); return o[Math.min(o.length - 1, o.indexOf(s) + 1)]; }
  function moveCard(c, to) {
    if (!c || c.status === to) return Promise.resolve();
    if (to === 'published') {
      return post('/api/media/' + encodeURIComponent(c.id) + '/publish').then(d => {
        if (d.status === 'pending') toast('A card is asking you first: "' + c.title + '" leaves this computer only when you approve it.');
        else if (d.status === 'ok') { toast(d.message || 'Published.'); changed(); }
        else toast(d.message || 'Could not publish.');
      });
    }
    if (to === 'scheduled') {
      const when = prompt('When should "' + c.title + '" go out? (date and time)', c.when ? c.when.slice(0, 16).replace('T', ' ') : '');
      if (!when) return Promise.resolve();
      return post('/api/media/' + encodeURIComponent(c.id), { status: 'scheduled', when }, 'PATCH').then(d => {
        if (d.status === 'ok') { toast('Scheduled. Friday asks again before it goes.'); changed(); } else toast(d.message || 'Could not schedule.');
      });
    }
    return post('/api/media/' + encodeURIComponent(c.id), { status: to }, 'PATCH').then(d => {
      if (d.status === 'ok') { toast('"' + c.title + '" is now ' + (STATUS_WORD[to] || to).toLowerCase() + '.'); changed(); }
      else if (d.status === 'pending') toast('That move asks you first.');
      else toast(d.message || 'Could not move the card.');
    });
  }
  function MediaBoard({ onOpen }) {
    useEffect(() => { if (!document.getElementById('media-board-styles')) { const s = document.createElement('style'); s.id = 'media-board-styles'; s.textContent = BOARD_CSS; document.head.appendChild(s); } }, []);
    const [proj, setProj] = useState('');
    const [kind, setKind] = useState('');
    const [state] = useCards({ view: 'all', project: proj || null, kind: kind || null, sort: 'next' });
    const [over, setOver] = useState(null);
    const [focus, setFocus] = useState(null);
    const cards = state.cards;
    useEffect(() => {
      const onKey = e => {
        const typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName);
        if (typing) return;
        const c = cards.find(x => x.id === focus);
        if (e.key === ']' && c) { e.preventDefault(); moveCard(c, nextStatus(c.status)); }
        if (e.key === 'Enter' && c) { e.preventDefault(); onOpen(c); }
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [cards, focus, onOpen]);
    return h(React.Fragment, null,
      h('div', { className: 'md-filters' },
        h('span', { className: 'md-label' }, 'Pipeline'),
        h('select', { 'aria-label': 'Project', value: proj, onChange: e => setProj(e.target.value) },
          h('option', { value: '' }, 'All projects'), (state.projects || []).map(p => h('option', { key: p.name, value: p.name }, p.name))),
        h('select', { 'aria-label': 'Type', value: kind, onChange: e => setKind(e.target.value) },
          h('option', { value: '' }, 'All types'), TYPE_GROUPS.map(t => h('option', { key: t[0], value: t[0] }, t[1]))),
        h('span', { className: 'md-count' }, 'Drag a card to the next stage. Into Scheduled asks when; into Published always asks you first, because it leaves this computer.')),
      h('div', { className: 'md-board', 'aria-label': 'Pipeline by status', 'aria-busy': state.loading ? 'true' : 'false' },
        STATUSES.map(s => {
          const col = cards.filter(c => c.status === s[0]);
          return h('section', { key: s[0], className: 'md-col', 'aria-label': s[1] },
            h('div', { className: 'md-colhead' }, s[1], ' ', h('span', { className: 'n' }, col.length),
              s[0] === 'published' ? h('span', { className: 'gate' }, pill('md-pill-needs-you', 'asks first')) : s[0] === 'scheduled' ? h('span', { className: 'gate' }, pill('md-pill-neutral', 'asks when')) : null),
            h('div', { className: 'md-lane-note' }, LANE_NOTE[s[0]]),
            h('div', {
              className: 'md-drop' + (over === s[0] ? ' over' : ''), 'data-status': s[0],
              onDragOver: e => { e.preventDefault(); setOver(s[0]); }, onDragLeave: () => setOver(null),
              onDrop: e => { e.preventDefault(); setOver(null); const id = e.dataTransfer.getData('text/plain'); moveCard(cards.find(x => x.id === id), s[0]); }
            }, col.map(c => h('div', { key: c.id, draggable: true, onDragStart: e => { e.dataTransfer.setData('text/plain', c.id); setFocus(c.id); }, onFocus: () => setFocus(c.id) },
              h(Card, { c, noThumb: true, selected: focus === c.id, onSelect: x => setFocus(x.id), onOpen })))));
        })),
      h('p', { className: 'md-keys' }, 'Keys: ', h('kbd', null, ']'), ' advance a stage · ', h('kbd', null, 'Enter'), ' open · drag between columns.'));
  }
  window.MediaBoard = MediaBoard;

  // ── the Calendar ─────────────────────────────────────────────────────────
  // Only cards with a time: what went out, what is due to go out, and what needs
  // the owner by when. Two weeks at a glance. Dragging a scheduled card to another
  // day reschedules it, and Friday asks again before it goes.
  const CAL_CSS = `
.md-calhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.md-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:var(--fr-text-sm);color:var(--fr-dim);margin-left:auto}
.md-legend i{display:inline-block;width:10px;height:10px;border-radius:3px;border:1px solid;margin-right:5px;vertical-align:middle}
.md-cal{display:grid;grid-template-columns:repeat(7,1fr);grid-auto-rows:auto;align-content:start;gap:6px;flex:1 1 auto;min-height:0;overflow:auto}
.md-cal .dh{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);text-transform:uppercase;color:var(--fr-dim);padding:2px 4px}
.md-day{border:1px solid var(--fr-glass-edge);border-radius:8px;padding:6px;min-height:110px;display:flex;flex-direction:column;gap:4px;background:rgba(255,255,255,.035)}
.md-day.today{border-color:rgba(0,212,255,.35)}
.md-day.over{outline:1px dashed var(--fr-cyan);background:var(--fr-cyan-soft)}
.md-day .dn{font-family:var(--fr-font-mono);font-size:var(--fr-text-xs);color:var(--fr-dim)}
.md-day.today .dn{color:var(--fr-cyan)}
.md-ev{border:1px solid var(--fr-glass-edge);border-radius:6px;padding:3px 6px;font-size:var(--fr-text-sm);display:flex;gap:5px;align-items:center;cursor:pointer;background:rgba(0,0,0,.25);text-align:left;color:inherit;font-family:inherit;width:100%}
.md-ev.published{border-color:rgba(0,255,128,.35)}.md-ev.scheduled{border-color:rgba(122,134,153,.5)}.md-ev.review{border-color:rgba(245,158,11,.45)}
.md-ev .t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.md-ev .tm{font-family:var(--fr-font-mono);color:var(--fr-dim);font-size:var(--fr-text-2xs)}
.md-ev .md-type svg{width:12px;height:12px}
`;
  function dayKey(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function MediaCalendar({ onOpen }) {
    useEffect(() => { if (!document.getElementById('media-cal-styles')) { const s = document.createElement('style'); s.id = 'media-cal-styles'; s.textContent = CAL_CSS; document.head.appendChild(s); } }, []);
    const [base, setBase] = useState(0);
    const [span, setSpan] = useState(14);
    const [cards, setCards] = useState([]);
    const [peek, setPeek] = useState(null);
    const [over, setOver] = useState(null);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const start = new Date(today); start.setDate(start.getDate() - ((start.getDay() + 6) % 7) + base);
    const end = new Date(start); end.setDate(end.getDate() + span);
    const load = useCallback(() => {
      json('/api/media/calendar?from=' + dayKey(start) + '&to=' + dayKey(end)).then(d => { if (d.status === 'ok') setCards(d.cards || []); }).catch(() => {});
    }, [dayKey(start), dayKey(end)]);
    useEffect(() => { load(); }, [load]);
    useEffect(() => { const on = () => load(); window.addEventListener('friday:media-changed', on); return () => window.removeEventListener('friday:media-changed', on); }, [load]);
    const days = []; for (let i = 0; i < span; i++) { const d = new Date(start); d.setDate(d.getDate() + i); days.push(d); }
    const byDay = {}; cards.forEach(c => { if (!c.when) return; const k = c.when.slice(0, 10); (byDay[k] = byDay[k] || []).push(c); });
    const reschedule = (c, d) => {
      if (!c || c.status === 'published') { toast('A published card cannot move; unpublish it first.'); return; }
      const t = c.when ? c.when.slice(11, 16) : '09:00';
      post('/api/media/' + encodeURIComponent(c.id), { status: 'scheduled', when: dayKey(d) + ' ' + t }, 'PATCH').then(r => {
        if (r.status === 'ok') { toast('Moved to ' + d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) + ' ' + t + '. Friday asks again before it goes.'); changed(); } else toast(r.message || 'Could not move it.');
      });
    };
    const fmt = d => d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
    return h(React.Fragment, null,
      h('div', { className: 'md-calhead' },
        h('button', { className: 'btn', 'aria-label': 'Earlier', onClick: () => setBase(b => b - 7) }, '←'),
        h('strong', null, fmt(days[0]) + ' – ' + fmt(days[days.length - 1])),
        h('button', { className: 'btn', 'aria-label': 'Later', onClick: () => setBase(b => b + 7) }, '→'),
        h('button', { className: 'btn', onClick: () => setBase(0) }, 'This week'),
        h('div', { className: 'md-seg', role: 'group', 'aria-label': 'Span' },
          h('button', { className: 'btn' + (span === 14 ? ' active' : ''), 'aria-pressed': span === 14, onClick: () => setSpan(14) }, '2 weeks'),
          h('button', { className: 'btn' + (span === 35 ? ' active' : ''), 'aria-pressed': span === 35, onClick: () => setSpan(35) }, 'Month')),
        h('div', { className: 'md-legend' },
          h('span', null, h('i', { style: { borderColor: 'rgba(0,255,128,.5)' } }), 'Published'),
          h('span', null, h('i', { style: { borderColor: 'rgba(122,134,153,.6)' } }), 'Scheduled'),
          h('span', null, h('i', { style: { borderColor: 'rgba(245,158,11,.5)' } }), 'Needs you by'))),
      h('div', { className: 'md-stage-wrap' },
        h('div', { className: 'md-cal', 'aria-label': 'Scheduled and published work' },
          ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(n => h('div', { key: n, className: 'dh' }, n)),
          days.map(d => {
            const k = dayKey(d); const isToday = k === dayKey(today);
            const evs = (byDay[k] || []).slice().sort((a, b) => (a.when < b.when ? -1 : 1));
            return h('div', {
              key: k, className: 'md-day' + (isToday ? ' today' : '') + (over === k ? ' over' : ''),
              onDragOver: e => { e.preventDefault(); setOver(k); }, onDragLeave: () => setOver(null),
              onDrop: e => { e.preventDefault(); setOver(null); reschedule(cards.find(x => x.id === e.dataTransfer.getData('text/plain')), d); }
            },
              h('div', { className: 'dn' }, d.getDate() + (isToday ? ' · today' : '')),
              evs.map(c => h('button', {
                key: c.id, className: 'md-ev ' + c.status, title: c.title, draggable: c.status !== 'published',
                onDragStart: e => e.dataTransfer.setData('text/plain', c.id), onClick: () => setPeek(c), onDoubleClick: () => onOpen(c)
              }, h('span', { className: 'tm' }, c.when.slice(11, 16)), glyph(c.kind, ''), h('span', { className: 't' }, c.title))));
          })),
        peek ? h('aside', { className: 'md-detail', 'aria-label': 'Card' },
          h('div', { className: 'md-head-row' }, h('strong', null, peek.title), h('button', { className: 'btn', onClick: () => setPeek(null), 'aria-label': 'Close' }, 'Esc')),
          h('div', { className: 'md-meta' }, glyph(peek.kind), h('span', null, '·'), h('span', null, fmtWhen(peek.when))),
          h('div', { className: 'md-meta' }, statusPill(peek), privacyPill(peek)),
          h('dl', { className: 'md-kv' },
            h('dt', null, peek.status === 'published' ? 'Went to' : peek.status === 'scheduled' ? 'Goes to' : 'Due'), h('dd', null, peek.published_at || (peek.targets && peek.targets.length ? peek.targets.join(', ') : 'your review')),
            h('dt', null, 'Made by'), h('dd', null, peek.maker || 'You'),
            h('dt', null, 'From'), h('dd', null, (peek.sources || []).join(', ') || '—')),
          h('div', { className: 'md-actions' },
            h('button', { className: 'btn active', onClick: () => onOpen(peek) }, 'Open'),
            peek.status !== 'published' ? h('button', { className: 'btn', onClick: () => moveCard(peek, 'published') }, 'Publish now…') : null)) : null),
      h('p', { className: 'md-keys' }, 'Only cards with a time are here. Drag a scheduled card to another day to move it; Friday asks again before it goes. The Calendar workspace shows these too, as one layer among your events.'));
  }
  window.MediaCalendar = MediaCalendar;

  // ── the card: one editor frame for every medium ──────────────────────────
  // The stage changes with the kind; the status strip, the rails and the verbs
  // do not. Text is edited in place and saved to the card's own body. A post
  // card's stage is the existing Compose surface for that post. "Turn this
  // into…" makes a new card, status Draft, related made_from.
  const CARD_CSS = `
.md-editor{display:grid;grid-template-columns:1fr 320px;gap:14px;flex:1 1 auto;min-height:0}
.md-emain{display:flex;flex-direction:column;gap:10px;min-width:0;min-height:0}
.md-ehead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.md-ehead input.title{font-size:var(--fr-text-xl);font-weight:600;background:transparent;border:0;border-bottom:1px solid transparent;padding:2px 4px;flex:1;min-width:200px;color:var(--fr-text);font-family:inherit}
.md-ehead input.title:focus{outline:none;border-bottom-color:var(--fr-cyan)}
.md-strip{display:flex;gap:4px;align-items:center;flex-wrap:wrap}
.md-stage{background:#000;border:1px solid var(--fr-glass-edge);border-radius:10px;flex:1 1 auto;min-height:320px;position:relative;overflow:auto;display:flex;flex-direction:column}
.md-text{padding:22px 28px;max-width:80ch;color:var(--fr-text);font-size:var(--fr-text-lg);line-height:1.65;outline:none;white-space:pre-wrap;font-family:var(--fr-font-body);flex:1;background:transparent;border:0;resize:none;min-height:300px}
.md-stage img,.md-stage video{max-width:100%;max-height:70vh;margin:auto;display:block}
.md-stage audio{width:80%;margin:auto}
.md-stage iframe{flex:1;border:0;background:#fff;min-height:420px}
.md-stage .md-center{margin:auto;color:var(--fr-dim);text-align:center;padding:20px}
.md-stage pre{font-family:var(--fr-font-mono);font-size:var(--fr-text-md);color:var(--fr-label);padding:20px 24px;margin:0;white-space:pre-wrap}
.md-ask{display:flex;gap:8px;align-items:center;border:1px solid rgba(0,212,255,.35);border-radius:10px;padding:8px 10px;background:rgba(0,212,255,.04)}
.md-ask input{flex:1;font:inherit;color:var(--fr-text);background:transparent;border:0;outline:none}
.md-side{display:flex;flex-direction:column;gap:10px;overflow:auto;min-height:0}
.md-side .card{background:rgba(255,255,255,.035);border:1px solid var(--fr-glass-edge);border-radius:10px;padding:12px 14px}
.md-side h3{font-size:var(--fr-text-md);color:var(--fr-dim);font-weight:500;margin:0 0 6px}
.md-turn{display:flex;flex-direction:column;gap:4px}
.md-turn .btn{text-align:left;display:flex;justify-content:space-between;gap:8px}
.md-turn .btn small{color:var(--fr-dim)}
.md-saved{font-size:var(--fr-text-sm);color:var(--fr-dim)}
@media (max-width:900px){.md-editor{grid-template-columns:1fr}}
`;
  const TURN_INTO = {
    text: [['episode', 'a podcast', 'two hosts read and cite it'], ['deck', 'slides', 'a deck from its headings'], ['post', 'a post', 'per platform, from its lede'], ['page', 'a page', 'a showcase page'], ['audio', 'read aloud', 'one voice, kept here']],
    episode: [['article', 'an article', 'from the transcript, cited by chapter'], ['post', 'a post', 'the best line per platform']],
    image: [['video', 'a video', 'a slow push on the keeper'], ['post', 'a post', 'with the image attached'], ['page', 'a page', 'a gallery page']],
    deck: [['page', 'a page', 'the deck as a scrolling page'], ['article', 'an article', 'from the notes'], ['episode', 'a podcast', 'the hosts walk the slides']],
    post: [['article', 'an article', 'expand it']],
    chart: [['episode', 'a data podcast', 'numbers computed first; the hosts only say computed numbers'], ['deck', 'slides', 'one chart per slide'], ['post', 'a post', 'the chart and one line']],
    code: [['page', 'a page', 'its README as a showcase page'], ['episode', 'a podcast', 'the brief, the README and the last run']],
    audio: [], music: [], video: [['post', 'a post', 'with the clip attached']]
  };
  function turnGroup(kind) {
    if (kind === 'draft' || kind === 'article' || kind === 'doc') return 'text';
    if (kind === 'imageset' || kind === 'image') return 'image';
    if (kind === 'deck' || kind === 'sheet') return 'deck';
    return kind;
  }
  function Stage({ c, body, setBody, saved }) {
    const kind = c.kind;
    const file = c.file_url;
    if (kind === 'draft' || kind === 'article' || kind === 'doc' || (c.editable_text && body != null)) {
      return h('textarea', { className: 'md-text', value: body || '', 'aria-label': 'Text', spellCheck: false, onChange: e => setBody(e.target.value), placeholder: 'Write here. Friday’s suggestions arrive as a diff you accept or not, never as a silent rewrite.' });
    }
    if (kind === 'post' && window.ContentComposeTab) {
      const prefill = Object.assign({}, (c.extra || {}).post || {}, { id: c.source_ref, title: c.title, body: body || '' });
      return h('div', { style: { padding: 10, overflow: 'auto', flex: 1 } }, h(window.ContentComposeTab, { platforms: window.__mediaPlatforms || [], prefillPost: prefill, slot: null, onNavAccounts: () => toast('Accounts live under Settings → Accounts & Keys.') }));
    }
    if ((kind === 'imageset' || kind === 'image') && file) return h('img', { src: file, alt: c.title });
    if ((kind === 'video' || kind === 'timeline') && file) return h('video', { src: file, controls: true });
    if ((kind === 'audio' || kind === 'music') && file) return h('audio', { src: file, controls: true });
    if (kind === 'episode') {
      return h('div', { className: 'md-center' },
        h('p', null, c.transcript_excerpt || 'An episode: chapters, a transcript and a source for every claim.'),
        h('button', { className: 'btn active', onClick: () => window.fridayPodcast && window.fridayPodcast('play', c.source_ref) }, '▶ Play in the mini player'),
        ' ', h('button', { className: 'btn', onClick: () => window.fridayPodcast && window.fridayPodcast('open', c.source_ref) }, 'Chapters and transcript'));
    }
    if (kind === 'page' && file) return h('iframe', { src: file, sandbox: 'allow-same-origin', title: c.title });
    if (kind === 'chart' && file) return h('img', { src: file, alt: c.title });
    if (kind === 'code') {
      return h('div', { className: 'md-center' }, h('pre', null, c.path || ''), h('p', null, 'Media does not edit code. '),
        h('button', { className: 'btn active', onClick: () => window.fridayRunActions ? window.fridayRunActions([{ type: 'navigate', workspace: 'code', path: c.path }]) : toast('Open the Code workspace on ' + c.path) }, 'Open in the Salon'));
    }
    if ((kind === 'deck' || kind === 'sheet') && c.renders && c.renders.length) return h('div', { style: { padding: 12, display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fill,minmax(220px,1fr))' } }, c.renders.map(r => h('img', { key: r, src: r, alt: '' })));
    if (file) return h('div', { className: 'md-center' }, h('a', { className: 'btn', href: file, target: '_blank', rel: 'noopener' }, 'Open the file'));
    return h('div', { className: 'md-center' }, 'Nothing to show yet.');
  }
  function MediaCard({ id, onBack, onAction }) {
    useEffect(() => { if (!document.getElementById('media-card-styles')) { const s = document.createElement('style'); s.id = 'media-card-styles'; s.textContent = CARD_CSS; document.head.appendChild(s); } }, []);
    const [data, setData] = useState(null);
    const [body, setBody] = useState(null);
    const [title, setTitle] = useState('');
    const [saved, setSaved] = useState('');
    const [ask, setAsk] = useState('');
    const dirty = useRef(false);
    const load = useCallback(() => json('/api/media/' + encodeURIComponent(id)).then(d => { if (d.status === 'ok') { setData(d); setBody(d.body == null ? null : d.body); setTitle(d.card.title); } else toast(d.message || 'That card is gone.'); }), [id]);
    useEffect(() => { load(); }, [load]);
    useEffect(() => { if (window.ContentComposeTab && !window.__mediaPlatforms) json('/api/content/platforms').then(d => { window.__mediaPlatforms = d.platforms || d.items || []; }).catch(() => {}); }, []);
    useEffect(() => {
      const on = e => { if (e.detail && e.detail.id === id) { const el = document.getElementById('md-turn'); if (el) el.scrollIntoView({ block: 'nearest' }); } };
      window.addEventListener('friday:media-turn', on); return () => window.removeEventListener('friday:media-turn', on);
    }, [id]);
    // Save the text a moment after typing stops; the title on blur.
    useEffect(() => {
      if (!data || body == null || body === data.body) return;
      dirty.current = true;
      const t = setTimeout(() => {
        post('/api/media/' + encodeURIComponent(id) + '/body', { text: body }, 'PUT').then(d => { if (d.status === 'ok') { dirty.current = false; setSaved('Saved ' + new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })); setData(x => Object.assign({}, x, { body })); changed(); } else toast(d.message || 'Could not save.'); });
      }, 800);
      return () => clearTimeout(t);
    }, [body, data, id]);
    if (!data) return h('div', { className: 'md-empty' }, 'Opening…');
    const c = data.card;
    const setStatus = s => {
      if (s === 'published' || s === 'scheduled') return moveCard(c, s);
      return post('/api/media/' + encodeURIComponent(id), { status: s }, 'PATCH').then(d => { if (d.status === 'ok') { toast('Now ' + STATUS_WORD[s].toLowerCase() + '.'); changed(); load(); } else toast(d.message || 'Could not move the card.'); });
    };
    const saveTitle = () => { if (title && title !== c.title) post('/api/media/' + encodeURIComponent(id), { title }, 'PATCH').then(d => { if (d.status === 'ok') { changed(); load(); } }); };
    const turn = kind => post('/api/media/' + encodeURIComponent(id) + '/turn-into', { kind }).then(d => {
      if (d.status === 'ok') { toast('A new card, "' + d.card.title + '", a draft made from this one.'); changed(); if (window.fridayMediaOpen) window.fridayMediaOpen(d.card.id); }
      else if (d.status === 'pending') toast('That asks you first: a card is waiting.');
      else toast(d.message || 'Could not make that.');
    });
    const askFriday = () => { if (!ask.trim()) return; const q = ask; setAsk(''); if (window.fridaySendChat) window.fridaySendChat('About "' + c.title + '" (Media card ' + c.id + '): ' + q); else toast('Ask Friday in the chat tray: ' + q); };
    const turns = TURN_INTO[turnGroup(c.kind)] || [];
    return h('div', { className: 'md-editor' },
      h('section', { className: 'md-emain', 'aria-label': 'Editor' },
        h('div', { className: 'md-ehead' },
          h('button', { className: 'btn', onClick: onBack, 'aria-label': 'Back to Media' }, '← Media'),
          glyph(c.kind),
          h('input', { className: 'title', 'aria-label': 'Title', value: title, onChange: e => setTitle(e.target.value), onBlur: saveTitle, onKeyDown: e => { if (e.key === 'Enter') e.target.blur(); } }),
          h('span', { className: 'md-count' }, fmtWhen(c.when)),
          h('span', { className: 'md-saved' }, saved)),
        h('div', { className: 'md-strip', role: 'group', 'aria-label': 'Status' },
          h('span', { className: 'md-label', style: { marginRight: 4 } }, 'Status'),
          STATUSES.map(s => h('button', { key: s[0], className: 'btn' + (c.status === s[0] ? ' active' : ''), 'aria-pressed': c.status === s[0], onClick: () => setStatus(s[0]) }, s[1])),
          c.held ? pill('md-pill-needs-you', 'Held for you') : null,
          c.targets && c.targets.length ? h('span', { className: 'md-count' }, '→ ' + c.targets.join(', ')) : null),
        h('div', { className: 'md-stage' }, h(Stage, { c, body, setBody, saved })),
        h('div', { className: 'md-ask' }, h('span', { className: 'md-label' }, 'Ask Friday'),
          h('input', { type: 'text', value: ask, placeholder: 'Tighten the second paragraph · add the quay number · make the hosts argue more…', 'aria-label': 'Ask Friday about this card', onChange: e => setAsk(e.target.value), onKeyDown: e => { if (e.key === 'Enter') askFriday(); } }),
          h('button', { className: 'btn', onClick: askFriday }, 'Go'))),
      h('aside', { className: 'md-side', 'aria-label': 'Rails' },
        h('div', { className: 'card', id: 'md-turn' }, h('h3', null, 'Turn this into…'),
          turns.length ? h('div', { className: 'md-turn' }, turns.map(t => h('button', { key: t[0], className: 'btn', onClick: () => turn(t[0]) }, h('span', null, t[1]), h('small', null, t[2])))) : h('div', { className: 'md-count' }, 'Not offered for this kind: an episode about an episode is noise. Edit it instead.')),
        h('div', { className: 'card' }, h('h3', null, 'Provenance'),
          h('dl', { className: 'md-kv' },
            h('dt', null, 'Made by'), h('dd', null, c.maker || 'You'),
            h('dt', null, 'From'), h('dd', null, (c.sources || []).join(', ') || 'Nothing yet'),
            h('dt', null, 'Credentials'), h('dd', null, c.signed ? [pill('md-pill-ok', 'Signed'), ' ', h('span', { className: 'md-count', style: { fontFamily: 'var(--fr-font-mono)' } }, (c.hash || '').slice(0, 10))] : [pill('md-pill-neutral', 'Unsigned'), ' signed when it leaves']),
            h('dt', null, 'Versions'), h('dd', null, (data.relations || []).filter(r => r.how === 'version_of').length ? (data.relations.filter(r => r.how === 'version_of').map(r => r.title).join(' · ')) : 'This one'))),
        h('div', { className: 'card' }, h('h3', null, 'Privacy and where it is'),
          h('dl', { className: 'md-kv' },
            h('dt', null, 'Now'), h('dd', null, privacyPill(c)),
            h('dt', null, 'Left this PC'), h('dd', null, c.published_at || 'Never'),
            h('dt', null, 'Project'), h('dd', null, h('input', { type: 'text', 'aria-label': 'Project', defaultValue: c.project || '', placeholder: 'No project', style: { font: 'inherit', background: 'transparent', border: 0, borderBottom: '1px solid var(--fr-glass-edge)', color: 'var(--fr-text)', width: '100%' }, onBlur: e => { if ((e.target.value || '') !== (c.project || '')) post('/api/media/' + encodeURIComponent(id), { project: e.target.value }, 'PATCH').then(() => { changed(); load(); }); } })))),
        (data.relations || []).filter(r => r.how !== 'version_of').length ? h('div', { className: 'card' }, h('h3', null, 'Related'),
          h('dl', { className: 'md-kv' }, (data.relations || []).filter(r => r.how !== 'version_of').map((r, i) => [h('dt', { key: 'k' + i }, r.how.replace('_', ' ')), h('dd', { key: 'v' + i }, h('a', { href: '#', onClick: e => { e.preventDefault(); if (window.fridayMediaOpen) window.fridayMediaOpen(r.id); } }, r.title || r.id))]))) : null,
        h('div', { className: 'card' }, h('h3', null, 'Actions'),
          h('div', { className: 'md-actions' },
            h('button', { className: 'btn', onClick: () => onAction('send', c) }, 'Send to…'),
            h('button', { className: 'btn', onClick: () => window.fridayOpenWorkspaceTab && window.fridayOpenWorkspaceTab('media', { view: 'card', card: c.id }) }, 'Own tab'),
            c.file_url ? h('a', { className: 'btn', href: c.file_url, download: '' }, 'Export') : null,
            h('button', { className: 'btn', onClick: () => setStatus('scheduled') }, 'Schedule…'),
            h('button', { className: 'btn', onClick: () => onAction('publish', c) }, c.status === 'published' ? 'Unpublish…' : 'Publish…'),
            h('button', { className: 'btn btn-magenta', onClick: () => onAction('delete', c) }, 'Delete')))));
  }
  window.MediaCard = MediaCard;

  // ── Channels and Insights: not pieces of work, so not cards ──────────────
  // Connected accounts live under Settings → Accounts & Keys (MediaChannelsSettings
  // re-houses the Content workspace's Accounts tab there). Analytics and best
  // times are a pane reached from Media's head row (MediaInsights).
  function usePlatforms() {
    const [plats, setPlats] = useState(window.__mediaPlatforms || []);
    const reload = useCallback(() => json('/api/content/platforms').then(d => { const p = d.platforms || d.items || []; window.__mediaPlatforms = p; setPlats(p); }).catch(() => {}), []);
    useEffect(() => { reload(); }, [reload]);
    return [plats, reload];
  }
  function MediaChannelsSettings() {
    const [plats, reload] = usePlatforms();
    if (!window.ContentAccountsTab) return null;
    return h('div', { style: { marginTop: 14 } },
      h('div', { className: 'md-label', style: { marginBottom: 6 } }, 'Publishing channels'),
      h('div', { className: 'md-count', style: { marginBottom: 8 } }, 'Where Media posts go. A post is a card in Media; the accounts it uses live here.'),
      h(window.ContentAccountsTab, { platforms: plats, reload }));
  }
  function MediaInsights({ onBack }) {
    if (!window.ContentAnalyticsTab) return h('div', { className: 'md-empty' }, 'Insights are not available here.');
    return h('div', { className: 'md-stage-wrap' },
      h('div', { className: 'md-ehead' }, h('button', { className: 'btn', onClick: onBack }, '← Media'), h('strong', null, 'Insights'), h('span', { className: 'md-count' }, 'How published cards did, and the best times to post.')),
      h('div', { style: { overflow: 'auto', flex: 1 } }, h(window.ContentAnalyticsTab, null)));
  }
  window.MediaChannelsSettings = MediaChannelsSettings;
  window.MediaInsights = MediaInsights;

  // Shared helpers for the other views (board, calendar, card), defined in this file's siblings.
  window.MediaWS = MediaWS;
  window.__media = { h, api, json, post, toast, STATUSES, STATUS_WORD, KIND_WORD, glyph, pill, statusPill, privacyPill, fmtWhen, Card, changed, useCards, ensureStyles };

  // Navigation: the views the dock, the palette and navigate_to may name.
  (window.__fridayNavDecls = window.__fridayNavDecls || []).push(['media', {
    key: 'view',
    keys: ['view', 'card', 'q', 'kind', 'project', 'default_view', 'status'],
    sections: [
      { id: 'library', label: 'Library', aliases: ['cards', 'everything', 'gallery', 'creations'] },
      { id: 'board', label: 'Pipeline', aliases: ['pipeline', 'board', 'kanban', 'stages', 'drafts', 'queue'] },
      { id: 'calendar', label: 'Calendar', aliases: ['schedule', 'scheduled', 'published'] },
      { id: 'card', label: 'Card', aliases: ['editor', 'open'] }
    ]
  }]);
})();
