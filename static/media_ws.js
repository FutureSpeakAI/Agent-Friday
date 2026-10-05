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
  // The sixth word is not a lane: "kept" is a thing that was made and stays on
  // this PC, finished and never shown as published unless it actually went somewhere.
  const KEPT = ['kept', 'Kept'];
  const STATUS_WORD = Object.fromEntries(STATUSES.concat([KEPT]));
  const STATUS_PILL = { idea: 'md-pill-dim', draft: 'md-pill-working', review: 'md-pill-needs-you', scheduled: 'md-pill-neutral', published: 'md-pill-ok', kept: 'md-pill-neutral' };
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
  function fmtBytes(n) {
    if (n == null) return '';
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' KB';
    if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
    return (n / 1073741824).toFixed(2) + ' GB';
  }
  // One line of facts: the type, the size, and what measures it (dimensions, duration, pages, words).
  function facts(c) {
    const d = c.details || {};
    const out = [KIND_WORD[c.kind] || c.kind];
    if (d.bytes != null) out.push(fmtBytes(d.bytes));
    if (d.width && d.height) out.push(d.width + '×' + d.height);
    if (c.duration) out.push(c.duration);
    if (c.pages) out.push(c.pages + (c.pages === 1 ? ' page' : ' pages'));
    if (c.words && !c.pages) out.push(c.words + ' words');
    if (d.count > 1) out.push(d.count + ' images');
    return out.join(' · ');
  }
  const AV_KINDS = { audio: 1, music: 1, episode: 1, video: 1 };
  // Play in place: one <audio> for the whole workspace, so two cards never talk over each other.
  const player = { el: null, id: null, listeners: new Set() };
  function playing(id) { return player.id === id && player.el && !player.el.paused; }
  function togglePlay(c) {
    if (!player.el) { player.el = new Audio(); player.el.addEventListener('ended', () => { player.id = null; player.listeners.forEach(f => f()); }); }
    if (player.id === c.id) {
      if (player.el.paused) player.el.play().catch(() => {}); else player.el.pause();
    } else {
      player.el.pause(); player.el.src = c.file_url; player.id = c.id; player.el.play().catch(() => toast('Could not play that file.'));
    }
    player.listeners.forEach(f => f());
  }
  function usePlayerTick() {
    const [, set] = useState(0);
    useEffect(() => { const f = () => set(x => x + 1); player.listeners.add(f); return () => player.listeners.delete(f); }, []);
  }
  function Wave({ peaks, height }) {
    if (!peaks || !peaks.length) return null;
    return h('div', { className: 'md-wave', style: height ? { height } : null, 'aria-hidden': 'true' },
      peaks.map((v, i) => h('i', { key: i, style: { height: Math.max(2, Math.round(v * 100)) + '%' } })));
  }
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
.md-thumb .scrub{position:absolute;inset:0;background-repeat:no-repeat;background-size:800% 100%;opacity:0;transition:opacity .12s}
.md-thumb:hover .scrub{opacity:1}
.md-thumb .scrubbar{position:absolute;left:0;bottom:0;height:2px;background:var(--fr-cyan);width:0}
.md-thumb .play{position:absolute;left:8px;bottom:6px;width:28px;height:28px;border-radius:50%;border:1px solid var(--fr-glass-edge);background:rgba(0,0,0,.6);color:var(--fr-text);display:grid;place-items:center;cursor:pointer;font-size:12px}
.md-thumb .play:hover{border-color:var(--fr-cyan)}
.md-thumb .ico{display:flex;flex-direction:column;align-items:center;gap:4px;color:var(--fr-dim)}
.md-thumb .ico svg{width:28px;height:28px;fill:none;stroke:currentColor;stroke-width:1.6;opacity:.8}
.md-thumb .ico b{font-weight:600;color:var(--fr-label);font-size:var(--fr-text-xs)}
.md-facts{color:var(--fr-dim);font-size:var(--fr-text-xs);font-family:var(--fr-font-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.md-dthumb{width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:8px;border:1px solid var(--fr-glass-edge);background:#000}
.md-detail audio,.md-detail video{width:100%;margin-top:6px}
.md-prompt{font-size:var(--fr-text-sm);color:var(--fr-text);background:rgba(0,0,0,.25);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:6px 8px;max-height:120px;overflow:auto;white-space:pre-wrap}
.md-ql{position:fixed;inset:0;z-index:60;background:rgba(4,6,12,.86);backdrop-filter:blur(6px);display:grid;grid-template-rows:auto 1fr auto;gap:10px;padding:16px}
.md-ql-head{display:flex;align-items:center;gap:10px;color:var(--fr-label)}
.md-ql-head strong{font-size:var(--fr-text-lg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.md-ql-stage{display:grid;place-items:center;min-height:0;overflow:hidden}
.md-ql-stage img,.md-ql-stage video{max-width:100%;max-height:100%;object-fit:contain;border-radius:8px;background:#000}
.md-ql-stage iframe{width:min(1280px,100%);height:100%;border:1px solid var(--fr-glass-edge);border-radius:8px;background:#fff}
.md-ql-stage .text{width:min(72ch,100%);max-height:100%;overflow:auto;white-space:pre-wrap;font-size:var(--fr-text-md);line-height:1.5;color:var(--fr-text);background:rgba(255,255,255,.035);border:1px solid var(--fr-glass-edge);border-radius:10px;padding:18px 22px}
.md-ql-stage .audio{width:min(720px,100%);display:flex;flex-direction:column;gap:10px;align-items:stretch}
.md-ql-foot{display:flex;gap:8px;align-items:center;flex-wrap:wrap;color:var(--fr-dim);font-size:var(--fr-text-sm)}
.md-ql-nav{position:fixed;top:50%;transform:translateY(-50%);width:40px;height:40px;border-radius:50%;border:1px solid var(--fr-glass-edge);background:rgba(0,0,0,.5);color:var(--fr-text);font-size:18px;cursor:pointer}
.md-thumb .fav{position:absolute;right:6px;top:6px;width:26px;height:26px;border-radius:50%;border:1px solid var(--fr-glass-edge);background:rgba(0,0,0,.55);color:var(--fr-dim);display:grid;place-items:center;cursor:pointer;font-size:13px;opacity:0;transition:opacity .12s}
.md-thumb:hover .fav,.md-thumb .fav.on{opacity:1}
.md-thumb .fav.on{color:var(--fr-cyan);border-color:var(--fr-cyan)}
.md-card.multi{border-color:var(--fr-cyan);box-shadow:0 0 0 2px rgba(0,229,255,.35) inset}
.md-tags{display:flex;gap:4px;flex-wrap:wrap}
.md-tag{font-size:var(--fr-text-2xs);padding:1px 6px;border-radius:999px;border:1px solid var(--fr-glass-edge);color:var(--fr-dim);background:rgba(255,255,255,.04)}
.md-grouphead{grid-column:1/-1;font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);text-transform:uppercase;color:var(--fr-dim);padding:10px 2px 2px;border-bottom:1px solid var(--fr-glass-edge);display:flex;gap:8px;align-items:center}
.md-grouphead .n{color:var(--fr-label)}
.md-multibar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:6px 10px;border:1px solid var(--fr-cyan);border-radius:10px;background:rgba(0,229,255,.06);font-size:var(--fr-text-sm)}
.md-multibar input{font:inherit;color:var(--fr-text);background:rgba(0,0,0,.35);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:4px 8px;width:160px}
.md-detail input.md-in{font:inherit;color:var(--fr-text);background:rgba(0,0,0,.35);border:1px solid var(--fr-glass-edge);border-radius:8px;padding:5px 8px;width:100%}
.md-tidy{display:flex;flex-direction:column;gap:10px;overflow:auto;min-height:0;padding:2px}
.md-tidy .grp{border:1px solid var(--fr-glass-edge);border-radius:10px;padding:8px 10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.md-tidy .grp img{width:72px;height:45px;object-fit:cover;border-radius:6px;border:1px solid var(--fr-glass-edge)}
.md-tidy .keep{outline:2px solid var(--fr-cyan);outline-offset:1px}
.md-tidy .why{color:var(--fr-dim);font-size:var(--fr-text-sm);width:100%}
.md-hit{font-size:var(--fr-text-xs);color:var(--fr-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.md-hit mark{background:rgba(0,229,255,.18);color:var(--fr-text);border-radius:3px;padding:0 2px}
.md-tx{width:min(72ch,100%);max-height:38vh;overflow:auto;display:flex;flex-direction:column;gap:2px;font-size:var(--fr-text-sm);line-height:1.45}
.md-tx button{text-align:left;background:none;border:0;color:var(--fr-text);padding:2px 6px;border-radius:6px;cursor:pointer;font:inherit}
.md-tx button:hover{background:rgba(255,255,255,.06)}
.md-tx button b{color:var(--fr-cyan);font-weight:500;font-family:var(--fr-font-mono);font-size:var(--fr-text-2xs);margin-right:6px}
.md-wave{display:flex;align-items:center;gap:1px;height:56px;width:100%}
.md-wave i{flex:1 1 0;background:var(--fr-cyan);opacity:.75;border-radius:1px;min-height:2px}
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
  const DEFAULT_VIEWS = [['today', 'Today'], ['progress', 'In progress'], ['review', 'Needs you'], ['published', 'Published'], ['kept', 'Kept here'], ['all', 'Everything']];
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
    if (f.favorite) p.set('favorite', '1');
    if (f.tag) p.set('tag', f.tag);
    if (f.when) p.set('when', f.when);
    return p.toString();
  }
  function useCards(filters) {
    const [state, setState] = useState({ cards: [], counts: {}, projects: [], collections: [], loading: true, error: null, indexing: null, previews: null });
    const key = buildQuery(filters);
    const reload = useCallback(() => {
      setState(s => Object.assign({}, s, { loading: true }));
      json('/api/media?' + key).then(d => {
        if (d.status !== 'ok') { setState({ cards: [], counts: {}, projects: [], loading: false, error: d.message || 'Media could not load.', indexing: null }); return; }
        if (d.turns) window.__mediaTurns = d.turns;
        setState({ cards: d.cards || [], counts: d.counts || {}, projects: d.projects || [], collections: d.collections || [], loading: false, error: null, indexing: d.indexing || null, previews: d.previews || null });
      }).catch(() => setState({ cards: [], counts: {}, projects: [], loading: false, error: 'Media could not load.', indexing: null }));
    }, [key]);
    useEffect(() => { reload(); }, [reload]);
    // While the library is being indexed the list is asked again every little
    // while, so the count grows on screen and the grid fills when it is done.
    const building = !!(state.indexing && state.indexing.state === 'indexing');
    const soFar = state.indexing ? state.indexing.indexed : 0;
    useEffect(() => {
      if (!building) return undefined;
      const t = setTimeout(reload, 1500);
      return () => clearTimeout(t);
    }, [building, soFar, reload]);
    // and while the preview pass is still making thumbnails, every few seconds, so they appear as they land
    const pending = state.previews ? state.previews.pending : 0;
    const done = state.previews ? state.previews.done : 0;
    useEffect(() => {
      if (!pending) return undefined;
      const t = setTimeout(reload, 3000);
      return () => clearTimeout(t);
    }, [pending, done, reload]);
    useEffect(() => {
      const on = () => reload();
      window.addEventListener('friday:media-changed', on);
      return () => window.removeEventListener('friday:media-changed', on);
    }, [reload]);
    return [state, reload];
  }
  function changed() { window.dispatchEvent(new Event('friday:media-changed')); }

  // ── one card ─────────────────────────────────────────────────────────────
  // The thumbnail: the preview when the pass has made one; a video scrubs on
  // hover through its frame strip; audio plays in place; a type the pass has no
  // image for shows its icon and size. Clicking the picture opens the quick look.
  function favToggle(c) {
    return api('/api/media/' + encodeURIComponent(c.id), { method: 'PATCH', body: JSON.stringify({ favorite: !c.favorite }) }).then(() => changed());
  }
  function Thumb({ c, onQuick }) {
    usePlayerTick();
    const [frac, setFrac] = useState(-1);
    const d = c.details || {};
    const extra = c.duration ? c.duration : c.pages ? c.pages + ' pages' : c.words ? c.words + ' words' : null;
    const frames = d.strip || 8;
    const onMove = c.strip ? e => { const r = e.currentTarget.getBoundingClientRect(); setFrac(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width))); } : null;
    const scrub = c.strip && frac >= 0 ? h('div', { className: 'scrub', style: { backgroundImage: 'url(' + c.strip + ')', backgroundSize: (frames * 100) + '% 100%', backgroundPosition: (Math.min(frames - 1, Math.floor(frac * frames)) * 100 / (frames - 1)) + '% 0' } }) : null;
    const bar = c.strip && frac >= 0 ? h('div', { className: 'scrubbar', style: { width: (frac * 100) + '%' } }) : null;
    const play = AV_KINDS[c.kind] && c.file_url && c.kind !== 'video' ? h('span', { role: 'button', tabIndex: 0, className: 'play', 'aria-label': playing(c.id) ? 'Pause' : 'Play', title: playing(c.id) ? 'Pause' : 'Play here',
      onClick: e => { e.stopPropagation(); togglePlay(c); }, onKeyDown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); togglePlay(c); } } }, playing(c.id) ? '❚❚' : '▶') : null;
    return h('div', { className: 'md-thumb', onMouseMove: onMove, onMouseLeave: () => setFrac(-1), onClick: e => { if (onQuick) { e.stopPropagation(); onQuick(c); } }, title: onQuick ? 'Quick look (Space)' : undefined },
      c.thumb ? h('img', { src: c.thumb, alt: '', loading: 'lazy' }) : h('span', { className: 'ico' }, glyph(c.kind, ''), h('b', null, (d.suffix ? d.suffix.replace('.', '').toUpperCase() + ' ' : '') + (d.bytes != null ? fmtBytes(d.bytes) : (KIND_WORD[c.kind] || c.kind)))),
      scrub, bar, play,
      h('span', { role: 'button', tabIndex: 0, className: 'fav' + (c.favorite ? ' on' : ''), 'aria-label': c.favorite ? 'Remove from favourites' : 'Add to favourites', 'aria-pressed': c.favorite ? 'true' : 'false', title: c.favorite ? 'Favourite' : 'Make a favourite',
        onClick: e => { e.stopPropagation(); favToggle(c); }, onKeyDown: e => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); favToggle(c); } } }, c.favorite ? '\u2605' : '\u2606'),
      extra ? h('span', { className: 'dur' }, extra) : null);
  }
  // A search hit: the line where the words were found, the words marked.
  function Hit({ text }) {
    if (!text) return null;
    const parts = String(text).replace(/\s+/g, ' ').split(/(\[[^\]]*\])/g).filter(Boolean);
    return h('div', { className: 'md-hit', title: String(text).replace(/[\[\]]/g, '') },
      parts.map((x, i) => x[0] === '[' && x[x.length - 1] === ']' ? h('mark', { key: i }, x.slice(1, -1)) : x));
  }
  function fmtT(t) { t = Math.max(0, Math.round(t || 0)); return Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0'); }
  // The transcript, each line a button that seeks the player to when it was said.
  function Transcript({ tx, mediaRef }) {
    if (!tx || !(tx.segments || []).length) return null;
    return h('div', { className: 'md-tx', 'aria-label': 'Transcript' },
      tx.segments.map((sg, i) => h('button', { key: i, onClick: () => { const m = mediaRef.current; if (m) { m.currentTime = sg.start || 0; m.play && m.play().catch(() => {}); } } }, h('b', null, fmtT(sg.start)), sg.text)));
  }
  function Tags({ tags }) { return tags && tags.length ? h('div', { className: 'md-tags' }, tags.map(t => h('span', { key: t, className: 'md-tag' }, t))) : null; }
  function Card({ c, selected, onOpen, onSelect, noThumb, onQuick, multi, onMulti }) {
    const where = c.published_at ? h('div', { className: 'md-meta' }, h('span', null, 'at'), h('span', null, c.published_at))
      : (c.targets && c.targets.length ? h('div', { className: 'md-meta' }, h('span', null, 'to'), h('span', null, c.targets.join(', '))) : null);
    const d = c.details || {};
    const made = d.model ? d.model : (c.maker || '').replace(' · this PC', '');
    return h('button', {
      className: 'md-card' + (multi ? ' multi' : ''), role: 'option', 'aria-selected': selected ? 'true' : 'false', 'data-id': c.id,
      onClick: e => { if ((e.ctrlKey || e.metaKey || e.shiftKey) && onMulti) { e.preventDefault(); onMulti(c, e.shiftKey); return; } onSelect && onSelect(c); }, onDoubleClick: () => onOpen && onOpen(c),
      onKeyDown: e => { if (e.key === 'Enter') { e.preventDefault(); onOpen && onOpen(c); } if (e.key === 'x' && onMulti) { e.preventDefault(); onMulti(c, false); } }
    },
      noThumb ? null : h(Thumb, { c, onQuick }),
      h('div', { className: 'md-body' },
        h('div', { className: 'md-title', title: c.title }, c.title),
        c.hit ? h(Hit, { text: c.hit }) : null,
        h('div', { className: 'md-facts', title: facts(c) + (made ? ' · ' + made : '') }, facts(c) + (made ? ' · ' + made : '')),
        h('div', { className: 'md-meta' }, glyph(c.kind), h('span', null, '·'), h('span', null, fmtWhen(c.when))),
        h('div', { className: 'md-meta' }, statusPill(c), privacyPill(c), c.signed ? null : pill('md-pill-neutral', 'Unsigned')),
        h(Tags, { tags: c.tags }),
        where));
  }

  // ── the details panel (Library) ──────────────────────────────────────────
  // Favourite, tags and project, by hand, on one card.
  function Organize({ c }) {
    const [tags, setTags] = useState((c.tags || []).join(', '));
    const [proj, setProj] = useState(c.project || '');
    useEffect(() => { setTags((c.tags || []).join(', ')); setProj(c.project || ''); }, [c.id, (c.tags || []).join(','), c.project]);
    const save = body => api('/api/media/' + encodeURIComponent(c.id), { method: 'PATCH', body: JSON.stringify(body) }).then(d => { if (d.status !== 'ok') toast(d.message || 'Could not save that.'); changed(); });
    return h('div', { className: 'md-actions', style: { flexDirection: 'column', alignItems: 'stretch', gap: 6 } },
      h('button', { className: 'btn' + (c.favorite ? ' active' : ''), 'aria-pressed': c.favorite ? 'true' : 'false', onClick: () => favToggle(c) }, c.favorite ? '\u2605 Favourite' : '\u2606 Make a favourite'),
      h('input', { className: 'md-in', 'aria-label': 'Tags', placeholder: 'Tags, comma separated', value: tags, onChange: e => setTags(e.target.value), onBlur: () => save({ tags: tags.split(',').map(x => x.trim()).filter(Boolean) }), onKeyDown: e => { if (e.key === 'Enter') e.target.blur(); } }),
      h('input', { className: 'md-in', 'aria-label': 'Project', placeholder: 'Project (move to\u2026)', value: proj, onChange: e => setProj(e.target.value), onBlur: () => { if (proj !== (c.project || '')) save({ project: proj }); }, onKeyDown: e => { if (e.key === 'Enter') e.target.blur(); } }));
  }
  function fmtStamp(iso) { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }); }
  function Details({ c, onClose, onOpen, onAction, onQuick }) {
    if (!c) return null;
    const d = c.details || {};
    const measure = d.width && d.height ? [['Dimensions', d.width + ' × ' + d.height]] : [];
    if (c.duration) measure.push(['Duration', c.duration]);
    if (c.pages) measure.push(['Pages', String(c.pages)]);
    if (c.words && !c.pages) measure.push(['Words', String(c.words)]);
    if (d.count > 1) measure.push(['Images', String(d.count)]);
    const made = d.model || (c.maker || 'You');
    return h('aside', { className: 'md-detail', 'aria-label': 'Card details' },
      h('div', { className: 'md-head-row' }, h('strong', null, c.title), h('button', { className: 'btn', onClick: onClose, 'aria-label': 'Close details' }, 'Esc')),
      c.thumb ? h('img', { className: 'md-dthumb', src: c.thumb, alt: '', onClick: () => onQuick && onQuick(c), style: { cursor: onQuick ? 'zoom-in' : 'default' } }) : null,
      c.kind === 'video' && c.file_url ? h('video', { src: c.file_url, controls: true, preload: 'metadata', poster: c.thumb || undefined }) : null,
      AV_KINDS[c.kind] && c.kind !== 'video' && c.file_url ? h('audio', { src: c.file_url, controls: true, preload: 'metadata' }) : null,
      h('div', { className: 'md-meta' }, glyph(c.kind), h('span', null, '·'), h('span', null, fmtWhen(c.when))),
      h('dl', { className: 'md-kv', 'data-facts': 'true' },
        h('dt', null, 'Type'), h('dd', null, (KIND_WORD[c.kind] || c.kind) + (d.suffix ? ' · ' + d.suffix.replace('.', '').toUpperCase() : ''), c.filename ? h('div', { className: 'md-facts', title: c.filename }, c.filename) : null),
        h('dt', null, 'Size'), h('dd', null, d.bytes != null ? fmtBytes(d.bytes) : '—'),
        measure.map(m => [h('dt', { key: m[0] + 't' }, m[0]), h('dd', { key: m[0] + 'd' }, m[1])]),
        h('dt', null, 'Created'), h('dd', null, fmtStamp(c.created)),
        h('dt', null, 'Modified'), h('dd', null, fmtStamp(c.modified)),
        h('dt', null, 'Status'), h('dd', null, statusPill(c), c.targets && c.targets.length ? ' → ' + c.targets.join(', ') : null)),
      h('div', { className: 'md-label' }, 'Provenance'),
      h('dl', { className: 'md-kv', 'data-provenance': 'true' },
        h('dt', null, 'Made with'), h('dd', null, made),
        d.prompt ? [h('dt', { key: 'pt' }, 'Prompt'), h('dd', { key: 'pd' }, h('div', { className: 'md-prompt' }, d.prompt))] : null,
        h('dt', null, 'From'), h('dd', null, (d.sources && d.sources.length ? d.sources : (c.sources || [])).length ? (d.sources && d.sources.length ? d.sources : c.sources).join(', ') : 'Nothing recorded'),
        h('dt', null, 'Credentials'), h('dd', null, c.signed ? [pill('md-pill-ok', 'Signed'), ' content credentials'] : [pill('md-pill-neutral', 'Unsigned'), ' signed when it leaves']),
        h('dt', null, 'Privacy'), h('dd', null, privacyPill(c), ' ', c.published_at ? 'published at ' + c.published_at : 'never left this computer'),
        h('dt', null, 'Project'), h('dd', null, c.project || '—')),
      d.snippet ? h('div', { className: 'md-prompt', title: 'The first lines' }, d.snippet) : null,
      h('div', { className: 'md-label' }, 'Organize'),
      h(Organize, { c }),
      h('div', { className: 'md-label' }, 'Actions'),
      h('div', { className: 'md-actions' },
        h('button', { className: 'btn active', onClick: () => onOpen(c) }, 'Open'),
        onQuick ? h('button', { className: 'btn', onClick: () => onQuick(c) }, 'Quick look') : null,
        h('button', { className: 'btn', onClick: () => onAction('tab', c) }, 'Own tab'),
        h('button', { className: 'btn', onClick: () => onAction('turn', c) }, 'Turn this into…'),
        h('button', { className: 'btn', onClick: () => onAction('send', c) }, 'Send to…'),
        h('button', { className: 'btn', onClick: () => onAction('publish', c) }, c.status === 'published' ? 'Unpublish…' : 'Publish…'),
        h('button', { className: 'btn btn-magenta', onClick: () => onAction('delete', c) }, 'Delete')));
  }

  // Groups for the grid: by project, by date (today, yesterday, this week, this month, then months), or by type.
  function grouped(cards, by) {
    if (!by || by === 'none') return [{ label: null, items: cards }];
    const key = c => {
      if (by === 'project') return c.project || 'No project';
      if (by === 'type') return KIND_WORD[c.kind] || c.kind;
      const t = c.when_ts ? new Date(c.when_ts * 1000) : null;
      if (!t) return 'Undated';
      const now = new Date(); const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate());
      const diff = Math.round((day(now) - day(t)) / 86400000);
      if (diff <= 0) return 'Today'; if (diff === 1) return 'Yesterday'; if (diff < 7) return 'This week';
      if (t.getFullYear() === now.getFullYear() && t.getMonth() === now.getMonth()) return 'This month';
      return t.toLocaleString([], { month: 'long', year: 'numeric' });
    };
    const out = []; const idx = {};
    cards.forEach(c => { const k = key(c); if (!(k in idx)) { idx[k] = out.length; out.push({ label: k, items: [] }); } out[idx[k]].items.push(c); });
    return out;
  }

  // ── clean-up help: Friday offers, the owner decides, the trash restores ──
  function TidyPanel({ onClose }) {
    const [rep, setRep] = useState(null);
    const [busy, setBusy] = useState(false);
    useEffect(() => { json('/api/media/tidy').then(setRep).catch(() => setRep({ status: 'error' })); }, []);
    if (!rep) return h('div', { className: 'md-empty' }, 'Looking for near-duplicates and stale drafts\u2026');
    const n = rep.count || 0;
    const offer = () => { setBusy(true); post('/api/media/tidy', {}).then(d => { setBusy(false); toast(d.status === 'pending' ? 'One card is asking you first: ' + (d.count || n) + ' items would move to the trash.' : d.status === 'nothing' ? 'Nothing to tidy.' : d.status === 'ok' ? 'Tidied.' : (d.message || 'That did not work.')); if (d.status === 'ok') changed(); }); };
    return h('div', { className: 'md-tidy', 'aria-label': 'Tidy up' },
      h('div', { className: 'md-head-row' }, h('strong', null, n ? n + ' item' + (n === 1 ? '' : 's') + ' Friday would move to the trash (' + fmtBytes(rep.bytes || 0) + ')' : 'Nothing to tidy'), h('span', { className: 'md-spacer' }),
        n ? h('button', { className: 'btn active', disabled: busy, onClick: offer }, 'Ask me with one card') : null,
        h('button', { className: 'btn', onClick: onClose }, 'Close')),
      h('div', { className: 'md-count' }, 'Nothing moves until you approve the card. Whatever moves can be restored from the Trash; Friday never deletes for good.'),
      (rep.groups || []).map((g, i) => h('div', { key: 'g' + i, className: 'grp' },
        h('div', { className: 'why' }, g.why + ': keep ' + g.keep.title + (g.keep.favorite ? ' (your favourite)' : '') + ', move ' + g.remove.length),
        g.keep.thumb ? h('img', { className: 'keep', src: g.keep.thumb, alt: '', title: 'Kept: ' + g.keep.title }) : h('span', { className: 'md-tag keep' }, g.keep.title),
        g.remove.map(r => r.thumb ? h('img', { key: r.id, src: r.thumb, alt: '', title: 'Moves: ' + r.title }) : h('span', { key: r.id, className: 'md-tag' }, r.title)))),
      (rep.stale || []).length ? h('div', { className: 'grp' }, h('div', { className: 'why' }, 'Stale drafts, untouched for a month with little in them:'), rep.stale.map(s => h('span', { key: s.id, className: 'md-tag', title: s.when || '' }, s.title))) : null);
  }
  function TrashPanel({ onClose }) {
    const [d, setD] = useState(null);
    const load = () => json('/api/media/trash').then(setD).catch(() => setD({ status: 'error', entries: [] }));
    useEffect(() => { load(); }, []);
    if (!d) return h('div', { className: 'md-empty' }, 'Opening the trash\u2026');
    const entries = d.entries || [];
    return h('div', { className: 'md-tidy', 'aria-label': 'Trash' },
      h('div', { className: 'md-head-row' }, h('strong', null, entries.length ? entries.length + ' in the trash' : 'The trash is empty'), h('span', { className: 'md-spacer' }), h('button', { className: 'btn', onClick: onClose }, 'Close')),
      h('div', { className: 'md-count' }, 'Everything here can be put back. Friday never empties this folder; it is yours: ' + (d.folder || '')),
      entries.map(e => h('div', { key: e.entry, className: 'grp' },
        h('span', null, glyph((e.card || {}).kind), ' ', h('b', null, (e.card || {}).title || e.entry)),
        h('span', { className: 'md-count' }, (e.files || []).length + ' file' + ((e.files || []).length === 1 ? '' : 's') + ' \u00b7 ' + fmtBytes(e.bytes || 0) + ' \u00b7 ' + (e.reason || '')),
        h('span', { className: 'md-spacer' }),
        h('button', { className: 'btn', onClick: () => post('/api/media/trash/' + encodeURIComponent(e.entry) + '/restore', {}).then(r => { toast(r.status === 'ok' ? 'Restored.' : (r.message || 'Could not restore it.')); load(); changed(); }) }, 'Restore'))));
  }

  // ── the quick look ───────────────────────────────────────────────────────
  // Space or a click on the picture: the whole thing, full size, with inline
  // playback, a page in a sandboxed frame, a document's rendered pages, and
  // the ways out of it: the editor, the app that opens it, its folder.
  function QuickLook({ c, cards, setSel, onClose, onOpen, q }) {
    const [full, setFull] = useState(null);
    const [page, setPage] = useState(0);
    const mediaRef = useRef(null);
    useEffect(() => { setFull(null); setPage(0); if (c && (c.kind === 'draft' || c.kind === 'article' || c.kind === 'doc' || AV_KINDS[c.kind])) json('/api/media/' + encodeURIComponent(c.id) + (q ? '?q=' + encodeURIComponent(q) : '')).then(d => { if (d.status === 'ok') { setFull(d); const at = c.at != null ? c.at : d.hit_t; if (at != null && mediaRef.current) { mediaRef.current.currentTime = at; mediaRef.current.play && mediaRef.current.play().catch(() => {}); } } }).catch(() => {}); }, [c && c.id, q]);
    useEffect(() => {
      const onKey = e => {
        if (!c) return;
        const ids = cards.map(x => x.id); const i = ids.indexOf(c.id);
        if (e.key === 'Escape' || e.key === ' ') { e.preventDefault(); onClose(); }
        if (e.key === 'ArrowRight' && i < ids.length - 1) { e.preventDefault(); setSel(ids[i + 1]); }
        if (e.key === 'ArrowLeft' && i > 0) { e.preventDefault(); setSel(ids[i - 1]); }
        if (e.key === 'Enter') { e.preventDefault(); onOpen(c); }
      };
      window.addEventListener('keydown', onKey, true);
      return () => window.removeEventListener('keydown', onKey, true);
    }, [c, cards, setSel, onClose, onOpen]);
    if (!c) return null;
    const ids = cards.map(x => x.id); const i = ids.indexOf(c.id);
    const d = c.details || {};
    let stage;
    const tx = full && full.transcript;
    if (c.kind === 'video' && c.file_url) stage = h('div', { className: 'audio', style: { width: 'min(1100px,100%)', height: '100%' } }, h('video', { key: c.id, ref: mediaRef, src: c.file_url, controls: true, autoPlay: true, poster: c.thumb || undefined, style: { maxHeight: tx ? '55vh' : '100%' } }), h(Transcript, { tx, mediaRef }));
    else if (AV_KINDS[c.kind] && c.file_url) stage = h('div', { className: 'audio' }, c.thumb ? h('img', { src: c.thumb, alt: '', style: { maxHeight: 160 } }) : null, h(Wave, { peaks: full && full.peaks, height: 72 }), h('audio', { key: c.id, ref: mediaRef, src: c.file_url, controls: true, autoPlay: true }), h(Transcript, { tx, mediaRef }));
    else if (c.kind === 'page' && c.file_url) stage = h('iframe', { key: c.id, src: c.file_url, sandbox: '', title: c.title });
    else if (c.renders && c.renders.length) stage = h('img', { key: c.id + page, src: c.renders[Math.min(page, c.renders.length - 1)], alt: '' });
    else if ((c.kind === 'image' || c.kind === 'imageset' || c.kind === 'chart') && c.file_url) stage = h('img', { key: c.id, src: c.file_url, alt: c.title });
    else if (full && full.body) stage = h('div', { className: 'text' }, full.body);
    else if (c.thumb) stage = h('img', { key: c.id, src: c.thumb, alt: c.title });
    else stage = h('div', { className: 'text' }, h('b', null, c.title), '\n\n', facts(c), d.snippet ? '\n\n' + d.snippet : '', '\n\nNo preview for this type; open it in its app.');
    const act = what => post('/api/media/' + encodeURIComponent(c.id) + '/' + what, {}).then(r => { if (r.status !== 'ok') toast(r.message || 'That did not work.'); });
    return h('div', { className: 'md-ql', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Quick look: ' + c.title, onClick: e => { if (e.target === e.currentTarget) onClose(); } },
      h('div', { className: 'md-ql-head' }, glyph(c.kind), h('strong', null, c.title), h('span', { className: 'md-count' }, (i + 1) + ' of ' + ids.length), h('span', { className: 'md-spacer' }),
        c.renders && c.renders.length > 1 ? h('span', { className: 'md-seg' }, h('button', { className: 'btn', onClick: () => setPage(p => Math.max(0, p - 1)) }, '‹'), h('span', { className: 'md-count' }, 'page ' + (page + 1) + ' / ' + c.renders.length), h('button', { className: 'btn', onClick: () => setPage(p => Math.min(c.renders.length - 1, p + 1)) }, '›')) : null,
        h('button', { className: 'btn', onClick: onClose, 'aria-label': 'Close quick look' }, 'Esc')),
      h('div', { className: 'md-ql-stage' }, stage),
      h('div', { className: 'md-ql-foot' },
        h('span', null, facts(c)), h('span', null, '·'), h('span', null, d.model || c.maker || ''), d.prompt ? h('span', { title: d.prompt }, '· “' + d.prompt.slice(0, 80) + (d.prompt.length > 80 ? '…' : '') + '”') : null,
        h('span', { className: 'md-spacer' }),
        h('button', { className: 'btn active', onClick: () => onOpen(c) }, 'Open card'),
        c.file_url ? h('button', { className: 'btn', onClick: () => act('open') }, 'Open in app') : null,
        c.file_url ? h('button', { className: 'btn', onClick: () => act('reveal') }, 'Show in folder') : null,
        h('span', { className: 'md-count' }, '← → move · Space or Esc close · Enter open')),
      i > 0 ? h('button', { className: 'md-ql-nav', style: { left: 12 }, 'aria-label': 'Previous', onClick: () => setSel(ids[i - 1]) }, '←') : null,
      i < ids.length - 1 ? h('button', { className: 'md-ql-nav', style: { right: 12 }, 'aria-label': 'Next', onClick: () => setSel(ids[i + 1]) }, '→') : null);
  }

  // ── the Library ──────────────────────────────────────────────────────────
  function Library({ filters, setFilters, sel, setSel, onOpen, onAction }) {
    const [state] = useCards(filters);
    // Declared before every hook that lists it: a dependency array is read
    // during render, so a later `const cards` throws before it is set.
    const cards = state.cards;
    const [ql, setQl] = useState(false);
    const [panel, setPanel] = useState(null);     // 'tidy' | 'trash' | null
    const [groupBy, setGroupBy] = useState('none');
    const [multi, setMulti] = useState([]);       // ids picked with Ctrl/Shift-click or X, for one change on many
    const [bulkProj, setBulkProj] = useState('');
    const [bulkTag, setBulkTag] = useState('');
    const onMulti = useCallback((c, range) => setMulti(m => {
      if (range && m.length) { const ids = cards.map(x => x.id); const a = ids.indexOf(m[m.length - 1]), b = ids.indexOf(c.id); const lo = Math.min(a, b), hi = Math.max(a, b); return Array.from(new Set(m.concat(ids.slice(lo, hi + 1)))); }
      return m.includes(c.id) ? m.filter(x => x !== c.id) : m.concat([c.id]);
    }), [cards]);
    const bulk = body => post('/api/media/bulk', Object.assign({ ids: multi }, body)).then(d => { toast(d.status === 'ok' ? d.done + ' card' + (d.done === 1 ? '' : 's') + ' changed.' : (d.message || 'That did not work.')); changed(); });
    const saveCollection = () => {
      const name = window.prompt('Save this view as a collection called\u2026');
      if (!name) return;
      post('/api/media/collections', { name, filters: { view: filters.view, kind: filters.kind, project: filters.project, q: filters.q, privacy: filters.privacy, status: filters.status, tag: filters.tag, favorite: filters.favorite, unsigned: filters.unsigned, when: filters.when, sort: filters.sort } })
        .then(d => { toast(d.status === 'ok' ? 'Saved \u201c' + name + '\u201d.' : (d.message || 'Could not save it.')); changed(); });
    };
    const [qlCard, setQlCard] = useState(null);   // a card asked for by id that the current list may not hold
    const onQuick = useCallback(c => { setSel(c.id); setQl(true); }, [setSel]);
    useEffect(() => {
      const on = e => {
        const d = e.detail || {};
        if (!d.id) return;
        json('/api/media/' + encodeURIComponent(d.id) + (d.q ? '?q=' + encodeURIComponent(d.q) : '')).then(r => {
          if (r.status !== 'ok') return;
          const c = Object.assign({}, r.card, { play: true, at: d.at != null ? d.at : r.hit_t });
          setQlCard(c); setSel(c.id); setQl(true);
        }).catch(() => {});
      };
      window.addEventListener('friday:media-quicklook', on);
      return () => window.removeEventListener('friday:media-quicklook', on);
    }, [setSel]);
    const [layout, setLayout] = useState('grid');
    const searchRef = useRef(null);
    const selCard = cards.find(c => c.id === sel) || null;
    const current = filters.collection ? 'col:' + filters.collection : filters.favorite ? 'fav' : filters.tag ? 'tag:' + filters.tag : filters.kind ? 'kind:' + filters.kind : filters.project != null ? 'proj:' + filters.project : filters.privacy ? 'priv:' + filters.privacy : filters.unsigned ? 'unsigned' : 'view:' + (filters.view || 'today');
    const pick = useCallback(patch => { setFilters(Object.assign({ view: 'all', kind: null, project: null, privacy: null, unsigned: false, favorite: false, tag: null, when: null, collection: null, q: filters.q, sort: filters.sort }, patch)); setSel(null); setMulti([]); }, [filters.q, filters.sort, setFilters, setSel]);
    const openCollection = col => pick(Object.assign({ view: 'all', q: '' }, col.filters || {}, { collection: col.id }));
    useEffect(() => {
      const onKey = e => {
        const typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName) || (document.activeElement && document.activeElement.isContentEditable);
        if (e.key === '/' && !typing) { e.preventDefault(); searchRef.current && searchRef.current.focus(); return; }
        if (e.key === 'Escape') { if (ql) setQl(false); else setSel(null); return; }
        if (typing || ql) return;
        if (e.key === ' ' && selCard) { e.preventDefault(); setQl(true); return; }
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
    }, [cards, sel, selCard, onOpen, onAction, setSel, ql]);
    const counts = state.counts || {};
    const railBtn = (key, label, n, patch) => h('button', { key, 'aria-current': current === key ? 'true' : undefined, onClick: () => pick(patch) }, label, n != null ? h('span', { className: 'n' }, n) : null);
    return h('div', { className: 'md-lib' },
      h('aside', { className: 'md-rail', 'aria-label': 'Library' },
        h('div', { className: 'md-label' }, 'Default views'),
        h('div', { className: 'md-group' }, DEFAULT_VIEWS.map(v => railBtn('view:' + v[0], v[1], counts[v[0]], { view: v[0] }))),
        h('div', { className: 'md-group' }, railBtn('fav', '\u2605 Favourites', counts.favorites, { favorite: true })),
        h('div', { className: 'md-label' }, 'Collections'),
        h('div', { className: 'md-group', 'data-collections': 'true' },
          (state.collections || []).map(col => h('button', { key: 'col:' + col.id, 'aria-current': current === 'col:' + col.id ? 'true' : undefined, onClick: () => openCollection(col), title: 'A saved filter, evaluated now' }, col.name)),
          h('button', { className: 'dim', onClick: saveCollection, title: 'Save the current filters as a collection' }, '+ Save this view\u2026')),
        Object.keys(counts.tags || {}).length ? h('div', { className: 'md-label' }, 'Tags') : null,
        Object.keys(counts.tags || {}).length ? h('div', { className: 'md-group' }, Object.keys(counts.tags).sort().map(t => railBtn('tag:' + t, '# ' + t, counts.tags[t], { tag: t }))) : null,
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
        h('div', { className: 'md-label' }, 'Housekeeping'),
        h('div', { className: 'md-group' },
          h('button', { 'aria-current': panel === 'tidy' ? 'true' : undefined, onClick: () => setPanel(panel === 'tidy' ? null : 'tidy'), title: 'Near-duplicate renders and stale drafts, offered as one card' }, 'Tidy up\u2026'),
          h('button', { 'aria-current': panel === 'trash' ? 'true' : undefined, onClick: () => setPanel(panel === 'trash' ? null : 'trash'), title: 'What was removed; everything here can be restored' }, 'Trash')),
        h('div', { className: 'md-rail-note' }, 'Not here: the News editions and their shows (in News); your wiki (in Knowledge). Search finds them and links across.')),
      h('section', { className: 'md-main', 'aria-label': 'Cards' },
        h('div', { className: 'md-toolbar' },
          h('input', { ref: searchRef, type: 'search', placeholder: 'Search titles, text, sources, transcripts…  /', 'aria-label': 'Search', value: filters.q || '', onChange: e => setFilters(Object.assign({}, filters, { q: e.target.value })) }),
          h('span', { className: 'md-spacer' }),
          h('select', { 'aria-label': 'Sort', value: filters.sort || 'next', onChange: e => setFilters(Object.assign({}, filters, { sort: e.target.value })) },
            h('option', { value: 'next' }, 'By what matters next'), h('option', { value: 'newest' }, 'Newest first'), h('option', { value: 'title' }, 'By title'), h('option', { value: 'status' }, 'By status')),
          h('select', { 'aria-label': 'Group by', value: groupBy, onChange: e => setGroupBy(e.target.value) },
            h('option', { value: 'none' }, 'No groups'), h('option', { value: 'project' }, 'Group by project'), h('option', { value: 'date' }, 'Group by date'), h('option', { value: 'type' }, 'Group by type')),
          h('div', { className: 'md-seg', role: 'group', 'aria-label': 'Layout' },
            h('button', { className: 'btn' + (layout === 'grid' ? ' active' : ''), 'aria-pressed': layout === 'grid', onClick: () => setLayout('grid') }, 'Grid'),
            h('button', { className: 'btn' + (layout === 'list' ? ' active' : ''), 'aria-pressed': layout === 'list', onClick: () => setLayout('list') }, 'List'),
            h('button', { className: 'btn' + (layout === '3d' ? ' active' : ''), 'aria-pressed': layout === '3d', title: 'The creations folder in the 3D file browser', onClick: () => setLayout('3d') }, '3D'))),
        panel === 'tidy' ? h(TidyPanel, { onClose: () => setPanel(null) }) :
        panel === 'trash' ? h(TrashPanel, { onClose: () => setPanel(null) }) :
        multi.length ? h('div', { className: 'md-multibar', role: 'toolbar', 'aria-label': 'Selected cards' },
          h('b', null, multi.length + ' selected'),
          h('input', { 'aria-label': 'Move to project', placeholder: 'Move to project\u2026', value: bulkProj, onChange: e => setBulkProj(e.target.value), onKeyDown: e => { if (e.key === 'Enter' && bulkProj.trim()) bulk({ project: bulkProj.trim() }); } }),
          h('input', { 'aria-label': 'Add a tag', placeholder: 'Add a tag\u2026', value: bulkTag, onChange: e => setBulkTag(e.target.value), onKeyDown: e => { if (e.key === 'Enter' && bulkTag.trim()) { bulk({ add_tags: [bulkTag.trim()] }); setBulkTag(''); } } }),
          h('button', { className: 'btn', onClick: () => bulk({ favorite: true }) }, '\u2605 Favourite'),
          h('button', { className: 'btn', onClick: () => bulk({ favorite: false }) }, 'Unfavourite'),
          h('span', { className: 'md-spacer' }),
          h('span', { className: 'md-count' }, 'Ctrl-click or X picks; Shift-click picks a run'),
          h('button', { className: 'btn', onClick: () => setMulti([]) }, 'Clear')) : null,
        layout === '3d' ? h(window.MediaFiles3D || Placeholder, null) :
        h('div', { className: 'md-stage-wrap' },
          state.error ? h('div', { className: 'md-empty', role: 'alert' }, h('b', null, state.error), h('br'), 'Try again in a moment.') :
          cards.length === 0 && state.indexing && state.indexing.state === 'indexing' ? h('div', { className: 'md-empty', role: 'status', 'aria-live': 'polite' }, h('b', null, 'Indexing your library…'), h('br'), (state.indexing.indexed || 0) + ' so far. Everything Friday has made on this PC is being listed; this only takes a moment.') :
          !state.loading && cards.length === 0 ? h('div', { className: 'md-empty' }, h('b', null, 'Nothing here.'), h('br'), 'Pick another view on the left, or press ', h('kbd', null, 'N'), ' for a new card.') :
          h('div', { className: 'md-grid' + (layout === 'list' ? ' list' : ''), role: 'listbox', 'aria-label': 'Cards', 'aria-busy': state.loading ? 'true' : 'false', 'aria-multiselectable': 'true' },
            grouped(cards, groupBy).map(g => [
              g.label != null ? h('div', { key: 'g:' + g.label, className: 'md-grouphead', role: 'presentation' }, g.label, h('span', { className: 'n' }, g.items.length)) : null,
              g.items.map(c => h(Card, { key: c.id, c, selected: c.id === sel, onSelect: x => setSel(x.id), onOpen, onQuick, multi: multi.includes(c.id), onMulti }))
            ])),
          h(Details, { c: selCard, onClose: () => setSel(null), onOpen, onAction, onQuick }),
          ql && (selCard || qlCard) ? h(QuickLook, { c: selCard || qlCard, cards, setSel, onClose: () => { setQl(false); setQlCard(null); }, onOpen, q: filters.q }) : null),
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
        if (t.card && t.play) {
          // "play the last podcast about X": the Library's quick look, started where the words were said
          setCard(null); setView('library');
          if (t.q != null) setFilters(f => Object.assign({}, f, { q: String(t.q), view: 'all' }));
          window.dispatchEvent(new CustomEvent('friday:media-quicklook', { detail: { id: t.card, q: t.q, at: t.at } }));
          return;
        }
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

    // A routine show's episode is listed so nothing is missing, but it is News's:
    // opening it goes to the show's tab in News, never into Media's editor.
    const NEWS_TAB = { front_page: 'frontpage', briefing: 'briefings', weekly: 'weekly', editorial: 'editorial' };
    const openCard = useCallback(c => {
      const routine = c.origin === 'routine' && c.extra && c.extra.routine;
      if (routine && window.fridayNavigate) { window.fridayNavigate({ workspace: 'news', tab: NEWS_TAB[routine] || 'frontpage' }); return; }
      setCard(c.id);
    }, []);
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
        if (!confirm('Move "' + c.title + '" to Friday\u2019s trash? You can restore it from the Trash in Media.')) return;
        json('/api/media/' + encodeURIComponent(c.id), { method: 'DELETE' }).then(d => { if (d.status === 'ok') { toast('Moved to the trash. Restore it from Housekeeping \u203a Trash.'); setSel(null); setCard(null); changed(); } else if (d.status === 'pending') toast('Deleting that asks on a card first.'); else toast(d.message || 'Could not move it.'); });
      }
    }, []);

    const head = h('div', { className: 'md-head' },
      h('h2', null, 'Media'),
      h('div', { className: 'md-seg', role: 'tablist', 'aria-label': 'Views' },
        VIEWS.map(v => h('button', { key: v[0], role: 'tab', className: 'btn' + (view === v[0] && !card ? ' active' : ''), 'aria-selected': view === v[0] && !card, 'aria-pressed': view === v[0] && !card, onClick: () => { setCard(null); setView(v[0]); } }, v[1]))),
      h('span', { className: 'md-count' }, (counts.all != null ? counts.all + ' pieces of work' : '') + (counts.progress != null ? ' · ' + counts.progress + ' in progress' : '') + (counts.published != null ? ' · ' + counts.published + ' published' : '') + (counts.kept ? ' · ' + counts.kept + ' kept here' : '')),
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
.md-drop{flex:1;display:flex;flex-direction:column;gap:8px;min-height:120px;border-radius:10px;padding:2px;overflow-y:auto;overflow-x:hidden}
.md-drop>div{min-width:0}
.md-drop .md-card{min-width:0;width:100%}
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
        h('span', { className: 'md-count' }, 'Drag a card to the next stage. Into Scheduled asks when; into Published always asks you first, because it leaves this computer.' + (state.counts && state.counts.kept ? ' ' + state.counts.kept + ' finished pieces are kept in the Library, outside the pipeline.' : ''))),
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
    audio: [['transcript', 'a transcript', 'the words, with the time they were said'], ['captions', 'captions', 'an .srt and a .vtt file'], ['wavevideo', 'a video', 'a waveform, the captions burned in']],
    music: [['transcript', 'a transcript', 'only for Friday\u2019s own music'], ['captions', 'captions', 'only for Friday\u2019s own music'], ['wavevideo', 'a video', 'a waveform, the captions burned in']],
    video: [['transcript', 'a transcript', 'the words, with the time they were said'], ['captions', 'captions', 'an .srt and a .vtt file'], ['soundtrack', 'the sound track', 'the audio on its own'], ['still', 'a still', 'one frame as a picture'], ['post', 'a post', 'with the clip attached']]
  };
  TURN_INTO.deck = TURN_INTO.deck.concat([['narration', 'narration', 'the local voice reads each slide'], ['deckvideo', 'a narrated video', 'each slide held while the voice reads it']]);
  TURN_INTO.image = TURN_INTO.image.concat([['ocr', 'the words in it', 'read by local OCR; not a description']]);
  TURN_INTO.document = [['article', 'an article', 'from the document\u2019s text'], ['audio', 'read aloud', 'one voice, kept here'], ['deck', 'slides', 'a deck from its outline']];
  TURN_INTO.episode = TURN_INTO.episode.concat([['captions', 'captions', 'an .srt and a .vtt file'], ['wavevideo', 'a video', 'a waveform, the captions burned in']]);
  function turnGroup(kind, c) {
    if (kind === 'doc' && c && c.source_kind !== 'media' && !c.editable_text) return 'document';
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
      // Compose takes the post's id and fetches the post itself; a post card is that post.
      return h('div', { style: { padding: 10, overflow: 'auto', flex: 1 } }, h(window.ContentComposeTab, { platforms: window.__mediaPlatforms || [], prefillPost: c.source_ref, slot: null, onNavAccounts: () => toast('Accounts live under Settings → Accounts & Keys.') }));
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
    useEffect(() => { if (!window.__mediaTurns) json('/api/media/turns').then(d => { if (d.status === 'ok') window.__mediaTurns = d.turns; }).catch(() => {}); }, []);
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
    // Only what this PC can make is offered; what it cannot is named with the reason, never a dead button.
    const caps = (window.__mediaTurns && window.__mediaTurns.by_group && window.__mediaTurns.by_group[turnGroup(c.kind, c)]) || {};
    const allTurns = TURN_INTO[turnGroup(c.kind, c)] || [];
    const turns = allTurns.filter(t => !caps[t[0]] || caps[t[0]].available);
    const missing = allTurns.filter(t => caps[t[0]] && !caps[t[0]].available);
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
          c.status === 'kept' ? pill('md-pill-neutral', 'Kept on this PC') : null,
          c.held ? pill('md-pill-needs-you', 'Held for you') : null,
          c.targets && c.targets.length ? h('span', { className: 'md-count' }, '→ ' + c.targets.join(', ')) : null),
        h('div', { className: 'md-stage' }, h(Stage, { c, body, setBody, saved })),
        h('div', { className: 'md-ask' }, h('span', { className: 'md-label' }, 'Ask Friday'),
          h('input', { type: 'text', value: ask, placeholder: 'Tighten the second paragraph · add the quay number · make the hosts argue more…', 'aria-label': 'Ask Friday about this card', onChange: e => setAsk(e.target.value), onKeyDown: e => { if (e.key === 'Enter') askFriday(); } }),
          h('button', { className: 'btn', onClick: askFriday }, 'Go'))),
      h('aside', { className: 'md-side', 'aria-label': 'Rails' },
        h('div', { className: 'card', id: 'md-turn' }, h('h3', null, 'Turn this into…'),
          turns.length ? h('div', { className: 'md-turn' }, turns.map(t => h('button', { key: t[0], className: 'btn', onClick: () => turn(t[0]) }, h('span', null, t[1]), h('small', null, t[2])))) : (allTurns.length ? null : h('div', { className: 'md-count' }, 'Not offered for this kind: an episode about an episode is noise. Edit it instead.')),
          missing.length ? h('div', { className: 'md-count', 'data-missing-turns': missing.map(t => t[0]).join(' ') }, missing.map(t => h('div', { key: t[0] }, 'Not ' + t[1] + ' here: ' + (caps[t[0]].reason || 'no backend on this PC.')))) : null),
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

  // ── cards in 3D: the records view's source for Media ─────────────────────
  // The records-3D script (static/friday3d_records.js) loads after this one
  // and registers its sources on window.__friday3dRecords; Media adds its own
  // once that exists, so the "View in 3D" bar shows the cards as a cluster.
  function registerSource() {
    const R = window.__friday3dRecords;
    if (!R || !R.SOURCES || R.SOURCES.media) return !!(R && R.SOURCES && R.SOURCES.media);
    const word = c => KIND_WORD[c.kind] || c.kind;
    const by = f => (a, b) => { const x = f(a), y = f(b); return x < y ? -1 : x > y ? 1 : 0; };
    R.SOURCES.media = {
      label: 'Media', openLabel: 'Open the card', views: ['cluster', 'wall', 'ring', 'time'],
      empty: 'No cards yet. Make something with + New, or ask Friday in chat.', noun: 'cards', intro: 'center',
      blurb: 'Every piece of work as a card: by status, by kind, by project.',
      load: () => json('/api/media?view=all&limit=600').then(d => d.cards || []),
      toItem: c => ({ id: c.id, title: c.title, sub: word(c) + ' · ' + (STATUS_WORD[c.status] || c.status) + (c.held ? ' · held for you' : ''),
        badge: c.privacy === 'private' ? 'private' : c.privacy, strip: c.title, weight: 1, time: c.when_ts || 0 }),
      sorts: {
        newest: { label: 'Newest', cmp: (a, b) => (b.when_ts || 0) - (a.when_ts || 0) },
        title: { label: 'Title', cmp: by(c => String(c.title || '').toLowerCase()) },
        status: { label: 'Status', cmp: by(c => ['review', 'draft', 'idea', 'scheduled', 'published', 'kept'].indexOf(c.status)) }
      },
      filters: [
        { id: 'progress', label: 'In progress', test: c => c.status === 'draft' || c.status === 'review' },
        { id: 'needs', label: 'Needs you', test: c => c.status === 'review' || !!c.held },
        { id: 'published', label: 'Published', test: c => c.status === 'published' },
        { id: 'kept', label: 'Kept here', test: c => c.status === 'kept' },
        { id: 'unsigned', label: 'Unsigned', test: c => !c.signed }
      ],
      groupings: {
        status: { label: 'status', key: c => STATUS_WORD[c.status] || c.status },
        kind: { label: 'kind', key: c => word(c) },
        project: { label: 'project', key: c => c.project || 'No project' }
      },
      detail: c => [['Kind', word(c)], ['Status', STATUS_WORD[c.status] || c.status], ['Made by', c.maker], ['From', (c.sources || []).join(', ') || null],
        ['Privacy', c.privacy === 'private' ? 'private to this PC' : c.privacy], ['Credentials', c.signed ? 'signed' : 'unsigned'], ['Where', c.published_at], ['Project', c.project]],
      open: c => { if (window.fridayMediaOpen) window.fridayMediaOpen(c.id); else if (window.fridayOpenWorkspace) window.fridayOpenWorkspace({ workspace: 'media', card: c.id, view3d: false }); }
    };
    return true;
  }
  document.addEventListener('DOMContentLoaded', registerSource);
  window.__mediaRegister3D = registerSource;

  // ── Files 3D as a layout of the Library: the creations folder, in the file browser ─
  function MediaFiles3D() {
    if (!window.Files3DPanel) return h('div', { className: 'md-empty' }, 'The 3D file browser did not load.');
    return h('div', { className: 'md-stage-wrap' },
      h('div', { className: 'md-count', style: { marginBottom: 6 } }, 'Your creations folder in the 3D file browser. Documents and episodes stay in Friday’s home, which this browser never lists; find them in the grid. ', h('button', { className: 'btn', onClick: () => { if (window.fridayOpenWorkspace) window.fridayOpenWorkspace({ workspace: 'library', view: 'pc' }); } }, 'Browse this PC in the Library')),
      h('div', { className: 'f3-host on', style: { flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' } },
        window.FridayFiles3D ? h(window.FridayFiles3D, { lens: 'media', view: 'wall' })
          : h(window.Files3DPanel, { root: 'creations', path: '', view: 'wall', fill: true })));
  }
  window.MediaFiles3D = MediaFiles3D;

  // Shared helpers for the other views (board, calendar, card), defined in this file's siblings.
  window.MediaWS = MediaWS;
  window.__media = { h, api, json, post, toast, STATUSES, KEPT, STATUS_WORD, QuickLook, Thumb, Hit, Transcript, Organize, TidyPanel, TrashPanel, grouped, fmtBytes, facts, KIND_WORD, glyph, pill, statusPill, privacyPill, fmtWhen, Card, changed, useCards, ensureStyles };

  // Navigation: the views the dock, the palette and navigate_to may name.
  (window.__fridayNavDecls = window.__fridayNavDecls || []).push(['media', {
    key: 'view',
    keys: ['view', 'card', 'q', 'kind', 'project', 'default_view', 'status'],
    sections: [
      { id: 'library', label: 'Library', aliases: ['cards', 'everything', 'gallery', 'creations', 'kept', 'library'] },
      { id: 'board', label: 'Pipeline', aliases: ['pipeline', 'board', 'kanban', 'stages', 'drafts', 'queue'] },
      { id: 'calendar', label: 'Calendar', aliases: ['schedule', 'scheduled', 'published'] },
      { id: 'card', label: 'Card', aliases: ['editor', 'open'] }
    ]
  }]);
})();
