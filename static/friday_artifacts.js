/* The artifact panel beside every chat.
 *
 * Spec: docs/design/active/vibe-coding-salon.md §4.2–§4.3 (Phase 1).
 *
 * What it does, and what it deliberately does not:
 * - Shows what a model made that is better seen than read: markdown drafts
 *   (editable in place), tables (sortable, cells editable, CSV export),
 *   charts (drawn here in SVG from rows plus a spec), `html` apps in a
 *   sandboxed frame, diffs, images and svg. Every version is kept; the
 *   timeline scrubs through them and "restore" is itself a new version. A
 *   hand edit is a version authored by "you", and Friday is shown it as a
 *   diff on her next turn (services/artifacts.context_block).
 * - The frame is the browser as the first box (§4.3): `sandbox="allow-scripts"`
 *   with NO allow-same-origin gives the app an opaque origin, and a CSP inside
 *   the document lets it load nothing but pinned packages from one host
 *   (esm.sh, the host spike S1 settled on). It cannot reach Friday's API, its
 *   cookies, its storage or its DOM. Nothing crosses except postMessage.
 * - It opens by itself when an `artifact_put` lands in this conversation
 *   (the server broadcasts on /api/desktop/events, kind=chat), sits as a
 *   column beside the chat where there is room and as a tab over the chat
 *   where there is not, and remembers its width and whether it was open.
 * - It is one column ADDED next to the chat through FridayChatShell in
 *   index.html; it changes nothing inside ChatSurface. Without this script
 *   the shell is a Fragment and the chat is exactly as it was.
 *
 * Loaded after friday_chart.js (the chart renderer it shares with published
 * pages); defines window.FridayArtifactHost,
 * window.FridayArtifactPanel and window.fridayArtifactFrameDoc.
 */
(function () {
  'use strict';
  if (window.FridayArtifactHost) return;
  const h = React.createElement;
  const { useState, useEffect, useRef, useCallback, useMemo } = React;

  const api = (url, opts) => {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  };
  const getJ = url => api(url).then(r => r.json());
  const postJ = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(r => r.json().then(j => ({ ok: r.ok, status: r.status, j })));

  // ── The frame: the browser as the first box (§4.3) ─────────────────────
  // allow-scripts WITHOUT allow-same-origin: an opaque origin. Never widened.
  const SANDBOX = 'allow-scripts';
  // svg is framed with every restriction on: no scripts at all.
  const SVG_SANDBOX = '';
  // The one network hole an html artifact has is the pinned package host.
  // connect-src never names Friday: the frame talks to Friday only by
  // postMessage to the broker (Phase 2), never by fetch.
  const PACKAGE_HOST = 'https://esm.sh';
  const FRAME_CSP = [
    "default-src 'none'",
    "script-src 'unsafe-inline' 'wasm-unsafe-eval' " + PACKAGE_HOST,
    "style-src 'unsafe-inline' " + PACKAGE_HOST,
    "connect-src " + PACKAGE_HOST,
    "img-src data: blob: " + PACKAGE_HOST,
    "font-src data: " + PACKAGE_HOST,
    "media-src data: blob:",
    "worker-src blob:",
    "form-action 'none'",
    "base-uri 'none'"
  ].join('; ');
  const SVG_CSP = "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'";

  const FRAME_BASE_CSS = 'html,body{margin:0;background:#0b0e14;color:#e6e6e6;font-family:Inter,system-ui,sans-serif}';

  function frameDoc(html, csp, baseCss) {
    const s = String(html == null ? '' : html);
    const meta = '<meta http-equiv="Content-Security-Policy" content="' + csp.replace(/"/g, '&quot;') + '">';
    const base = baseCss ? '<style>' + baseCss + '</style>' : '';
    let doc;
    const headOpen = /<head[^>]*>/i.exec(s);
    if (headOpen) {
      // The CSP must be the first thing in <head>, before any script or style.
      doc = s.slice(0, headOpen.index + headOpen[0].length) + meta + base + s.slice(headOpen.index + headOpen[0].length);
    } else if (/<html[^>]*>/i.test(s)) {
      const m = /<html[^>]*>/i.exec(s);
      doc = s.slice(0, m.index + m[0].length) + '<head><meta charset="utf-8">' + meta + base + '</head>' + s.slice(m.index + m[0].length);
    } else {
      // A fragment: wrap it into a document.
      doc = '<!doctype html><html><head><meta charset="utf-8">' + meta + base + '</head><body>' + s + '</body></html>';
    }
    return (window.fridayFrameScrollbars || (x => x))(doc);
  }
  window.fridayArtifactFrameDoc = html => frameDoc(html, FRAME_CSP, FRAME_BASE_CSS);

  // Point-and-say: the picker that runs INSIDE the preview frame in point
  // mode. Inline, so it needs no network under the frame's CSP. A hover
  // outlines; a click is swallowed, the element is outlined solid, and one
  // message crosses to the parent: a selector that resolves to that element
  // alone, the tag, its text and a short snippet. Nothing else crosses.
  const PICKER_JS = "(function(){var hov=null,sel=null;var S=document.createElement('style');S.textContent='[data-fp-hover]{outline:2px dashed #00d4ff!important;outline-offset:-2px!important;cursor:crosshair!important}[data-fp-sel]{outline:2px solid #00d4ff!important;outline-offset:2px!important}';document.documentElement.appendChild(S);" +
    "function selector(el){if(el.id&&/^[A-Za-z][\\w-]*$/.test(el.id)&&document.querySelectorAll('#'+el.id).length===1)return '#'+el.id;var parts=[],cur=el,depth=0;while(cur&&cur.nodeType===1&&cur!==document.documentElement&&depth<6){var part=cur.tagName.toLowerCase();var cls=(cur.getAttribute('class')||'').trim().split(/\\s+/)[0];if(cls&&/^[A-Za-z_][\\w-]*$/.test(cls))part+='.'+cls;var sib=cur.parentElement?Array.prototype.filter.call(cur.parentElement.children,function(c){return c.tagName===cur.tagName}):[];if(sib.length>1)part+=':nth-of-type('+(sib.indexOf(cur)+1)+')';parts.unshift(part);var cand=parts.join(' > ');try{if(document.querySelectorAll(cand).length===1)return cand;}catch(e){}cur=cur.parentElement;depth++;}return parts.join(' > ');}" +
    "document.addEventListener('mouseover',function(e){var t=e.target;if(!(t instanceof Element)||t===document.documentElement||t===document.body)return;if(hov&&hov!==t)hov.removeAttribute('data-fp-hover');hov=t;t.setAttribute('data-fp-hover','');},true);" +
    "document.addEventListener('mouseout',function(e){if(e.target instanceof Element)e.target.removeAttribute('data-fp-hover');},true);" +
    "document.addEventListener('click',function(e){var t=e.target;if(!(t instanceof Element))return;e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();if(sel)sel.removeAttribute('data-fp-sel');sel=t;if(hov){hov.removeAttribute('data-fp-hover');hov=null;}t.removeAttribute('data-fp-hover');t.setAttribute('data-fp-sel','');var r=t.getBoundingClientRect();var html=t.outerHTML.replace(/ data-fp-(hover|sel)=\"\"/g,'');var fs=parseFloat(getComputedStyle(t).fontSize)||null;" +
    "parent.postMessage({__friday:'pick',selector:selector(t),tag:t.tagName.toLowerCase(),text:(t.textContent||'').trim().replace(/\\s+/g,' ').slice(0,200),snippet:html.slice(0,400),rect:{x:r.x,y:r.y,w:r.width,h:r.height},font_px:fs},'*');},true);" +
    "document.addEventListener('keydown',function(e){if(e.key==='Escape'){if(sel)sel.removeAttribute('data-fp-sel');sel=null;}},true);" +
    "document.documentElement.addEventListener('mouseleave',function(){if(hov){hov.removeAttribute('data-fp-hover');hov=null;}});})();";
  window.fridayPickerDoc = html => {
    const s = String(html == null ? '' : html);
    const tag = '<script>' + PICKER_JS + '</script>';
    const i = s.toLowerCase().lastIndexOf('</body>');
    return i >= 0 ? s.slice(0, i) + tag + s.slice(i) : s + tag;
  };
  const svgDoc = svg => frameDoc('<!doctype html><html><head></head><body style="margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#0b0e14">' + String(svg || '') + '</body></html>', SVG_CSP, 'svg{max-width:100%;max-height:100vh}');

  // ── One event stream for the whole page ───────────────────────────────
  // A browser allows about six connections to one host, and Friday's page
  // already holds several open (desktop events, approvals, the chat's own).
  // One more per chat surface would queue ordinary requests behind them, so
  // every host on the page shares this single EventSource.
  const bus = { es: null, subs: new Set(), timer: null };
  function busPoll() {
    if (!bus.timer) bus.timer = setInterval(() => bus.subs.forEach(f => { try { f(null); } catch (_) {} }), 20000);
  }
  function busSubscribe(fn) {
    bus.subs.add(fn);
    if (!bus.es && !bus.timer) {
      const client = 'art-' + Math.random().toString(36).slice(2, 10);
      try { bus.es = new EventSource('/api/desktop/events?client=' + client + '&kind=chat'); } catch (e) { bus.es = null; }
      if (bus.es) {
        bus.es.onmessage = e => {
          let m = null; try { m = JSON.parse(e.data); } catch (_) { return; }
          if (m && (m.type === 'artifact_put' || m.type === 'codebase_step' || m.type === 'workspace_bundle_changed' || m.type === 'open_conversation')) bus.subs.forEach(f => { try { f(m); } catch (_) {} });
        };
        // The stream reconnects by itself; while it is down, a slow poll keeps
        // the panel honest.
        bus.es.onerror = busPoll;
        bus.es.onopen = () => { if (bus.timer) { clearInterval(bus.timer); bus.timer = null; } };
      } else {
        busPoll();
      }
    }
    return () => { bus.subs.delete(fn); };
  }
  window.fridayBusSubscribe = busSubscribe;
  // A tool that opens a chat (improve_workspace by voice or in another chat)
  // says so on the bus once; this page opens the window. Each event opens once.
  const openedConvs = new Set();
  busSubscribe(m => {
    if (!m || m.type !== 'open_conversation' || !m.conversation_id) return;
    const key = m.conversation_id + ':' + (m.ts || m.reason || '');
    if (openedConvs.has(key)) return;
    openedConvs.add(key);
    if (window.fridayOpenChatWindow) window.fridayOpenChatWindow(m.conversation_id, m.title || 'Chat');
  });

  // ── Look ──────────────────────────────────────────────────────────────
  const ACCENT = '#00d4ff';
  const AMBER = '#f59e0b';
  const MONO = "'JetBrains Mono', monospace";
  const PALETTE = ['#00d4ff', '#b794f6', '#00ff80', '#f59e0b', '#ff6dd9', '#4f8cff', '#ffd166', '#9be7ff'];
  const KIND_LABEL = { markdown: 'Draft', table: 'Table', chart: 'Chart', html: 'App', diff: 'Changes', image: 'Image', svg: 'Drawing' };
  const KIND_GLYPH = { markdown: '¶', table: '▦', chart: '◔', html: '▣', diff: '±', image: '▧', svg: '◇' };

  const CSS = `
.fa-host{display:flex;flex:1;min-height:0;min-width:0;position:relative}
.fa-chat{display:flex;flex-direction:column;flex:1;min-width:0;min-height:0}
.fa-panel{display:flex;flex-direction:column;min-height:0;min-width:0;position:relative;
  background:linear-gradient(180deg,rgba(10,14,26,0.92),rgba(10,14,26,0.80));
  border-left:1px solid rgba(0,212,255,0.16);font-family:Inter,system-ui,sans-serif;color:#dfe7f2}
.fa-panel.fa-tab{border-left:none;flex:1}
.fa-panel::before{content:'';position:absolute;left:0;right:0;top:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(0,212,255,0.6),transparent);pointer-events:none}
.fa-head{padding:8px 10px 6px;border-bottom:1px solid rgba(0,212,255,0.10);flex-shrink:0}
.fa-eyebrow{display:flex;align-items:center;justify-content:space-between;gap:8px}
.fa-brand{font-family:Orbitron,Inter,sans-serif;font-size:9px;letter-spacing:.22em;color:${ACCENT};opacity:.9;white-space:nowrap}
.fa-brand b{font-weight:400;opacity:.55}
.fa-title-row{display:flex;align-items:center;gap:8px;margin-top:6px;min-width:0}
.fa-title{font-size:13px;font-weight:600;color:#f1f6fb;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fa-select{flex:1;min-width:0;background:rgba(0,212,255,0.05);border:1px solid rgba(0,212,255,0.18);border-radius:6px;color:#f1f6fb;font:600 13px Inter,system-ui,sans-serif;padding:3px 6px;cursor:pointer}
.fa-kind{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.06em;color:${ACCENT};
  border:1px solid rgba(0,212,255,0.28);background:rgba(0,212,255,0.07);border-radius:4px;padding:1px 6px;white-space:nowrap}
.fa-by{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.06em;border-radius:4px;padding:1px 6px;white-space:nowrap;
  color:${AMBER};border:1px solid rgba(245,158,11,0.35);background:rgba(245,158,11,0.08)}
.fa-tools{display:flex;align-items:center;gap:6px;margin-top:7px;flex-wrap:wrap}
.fa-btn{background:rgba(0,212,255,0.06);border:1px solid rgba(0,212,255,0.20);color:${ACCENT};cursor:pointer;
  font:11px Inter,system-ui,sans-serif;padding:3px 9px;border-radius:5px;line-height:1.4;white-space:nowrap}
.fa-btn:hover{background:rgba(0,212,255,0.14)}
.fa-btn:disabled{opacity:.4;cursor:default}
.fa-btn.fa-primary{background:rgba(0,212,255,0.18);border-color:rgba(0,212,255,0.5);color:#eafcff}
.fa-btn.fa-quiet{background:transparent;border-color:rgba(255,255,255,0.12);color:rgba(255,255,255,0.7)}
.fa-btn.fa-amber{border-color:rgba(245,158,11,0.45);color:${AMBER};background:rgba(245,158,11,0.08)}
.fa-icon{background:transparent;border:none;color:rgba(255,255,255,0.55);cursor:pointer;font-size:14px;line-height:1;padding:2px 5px;border-radius:4px}
.fa-icon:hover{color:${ACCENT};background:rgba(0,212,255,0.08)}
.fa-timeline{display:flex;align-items:center;gap:8px;margin-top:7px;font-family:'JetBrains Mono',monospace;font-size:10px;color:rgba(255,255,255,0.6)}
.fa-timeline input[type=range]{flex:1;min-width:60px;accent-color:${ACCENT};height:14px}
.fa-vlabel{color:#eafcff;min-width:56px;text-align:center}
.fa-body{flex:1;min-height:0;overflow:auto;position:relative;padding:10px}
.fa-body.fa-flush{padding:0;overflow:hidden}
.fa-frame{width:100%;height:100%;border:none;background:#0b0e14;display:block}
.fa-md{font-size:13px;line-height:1.6;color:#dfe7f2}
.fa-md h1,.fa-md h2,.fa-md h3{color:#f1f6fb;margin:.6em 0 .3em}
.fa-md a{color:${ACCENT}}
.fa-md pre{background:rgba(0,0,0,0.35);border-radius:6px;padding:8px 10px;overflow:auto}
.fa-editor{width:100%;height:100%;min-height:200px;resize:none;background:rgba(0,0,0,0.30);color:#eef4fa;border:1px solid rgba(0,212,255,0.25);border-radius:6px;
  padding:10px;font:12.5px/1.55 'JetBrains Mono',monospace;outline:none;box-sizing:border-box}
.fa-editor:focus{border-color:rgba(0,212,255,0.55);box-shadow:0 0 0 2px rgba(0,212,255,0.12)}
.fa-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px}
.fa-table th{position:sticky;top:0;background:rgba(10,14,26,0.96);color:${ACCENT};font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.06em;
  text-align:left;padding:6px 8px;border-bottom:1px solid rgba(0,212,255,0.25);cursor:pointer;user-select:none;white-space:nowrap}
.fa-table th .fa-sort{opacity:.5;margin-left:4px}
.fa-table td{padding:5px 8px;border-bottom:1px solid rgba(255,255,255,0.05);color:#dfe7f2;vertical-align:top}
.fa-table td.fa-num{text-align:right;font-family:'JetBrains Mono',monospace}
.fa-table tr:hover td{background:rgba(0,212,255,0.035)}
.fa-table td[contenteditable=true]{outline:1px dashed rgba(245,158,11,0.45);outline-offset:-1px;background:rgba(245,158,11,0.04)}
.fa-table td[contenteditable=true]:focus{outline:1px solid ${AMBER}}
.fa-foot{flex-shrink:0;padding:5px 10px;border-top:1px solid rgba(255,255,255,0.05);font-family:'JetBrains Mono',monospace;font-size:9.5px;color:rgba(255,255,255,0.42);
  display:flex;justify-content:space-between;gap:8px;white-space:nowrap;overflow:hidden}
.fa-foot span{overflow:hidden;text-overflow:ellipsis}
.fa-note{padding:6px 10px;font-size:11px;background:rgba(245,158,11,0.08);border-bottom:1px solid rgba(245,158,11,0.25);color:#ffd28a;flex-shrink:0}
.fa-note.fa-ok{background:rgba(0,255,128,0.06);border-color:rgba(0,255,128,0.25);color:#8ff5c0}
.fa-divider{width:6px;cursor:col-resize;flex-shrink:0;background:transparent;position:relative}
.fa-divider::after{content:'';position:absolute;top:0;bottom:0;left:2px;width:2px;background:rgba(0,212,255,0.0);transition:background .15s}
.fa-divider:hover::after,.fa-divider.fa-dragging::after{background:rgba(0,212,255,0.45)}
.fa-rail{width:34px;flex-shrink:0;border-left:1px solid rgba(0,212,255,0.14);background:rgba(10,14,26,0.6);display:flex;flex-direction:column;align-items:center;padding-top:8px;gap:10px;cursor:pointer}
.fa-rail:hover{background:rgba(0,212,255,0.05)}
.fa-rail-label{writing-mode:vertical-rl;transform:rotate(180deg);font-family:Orbitron,Inter,sans-serif;font-size:9px;letter-spacing:.22em;color:${ACCENT};opacity:.85;white-space:nowrap}
.fa-rail-count{font-family:'JetBrains Mono',monospace;font-size:10px;color:#eafcff;background:rgba(0,212,255,0.15);border:1px solid rgba(0,212,255,0.35);border-radius:10px;padding:0 6px}
.fa-strip{display:flex;align-items:stretch;flex-shrink:0;border-bottom:1px solid rgba(0,212,255,0.12);background:rgba(10,14,26,0.55)}
.fa-strip button{flex:1;background:transparent;border:none;border-bottom:2px solid transparent;color:rgba(255,255,255,0.6);cursor:pointer;
  font:11px Inter,system-ui,sans-serif;padding:6px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fa-strip button.fa-on{color:${ACCENT};border-bottom-color:${ACCENT};background:rgba(0,212,255,0.05)}
.fa-strip .fa-count{font-family:'JetBrains Mono',monospace;font-size:9px;margin-left:6px;color:#eafcff;background:rgba(0,212,255,0.15);border-radius:8px;padding:0 5px}
.fa-empty{display:flex;align-items:center;justify-content:center;height:100%;color:rgba(255,255,255,0.4);font-size:12px;text-align:center;padding:20px}
.fa-chart{width:100%;height:100%;display:block}
.fa-legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;color:rgba(255,255,255,0.75);padding:4px 2px 0}
.fa-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:-1px}
@media (max-width:390px){.fa-brand{letter-spacing:.14em}.fa-tools .fa-btn{padding:3px 7px;font-size:10.5px}}
`;
  function ensureCss() {
    if (document.getElementById('friday-artifacts-css')) return;
    const st = document.createElement('style');
    st.id = 'friday-artifacts-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  const ls = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, String(v)); } catch (e) {} }
  };
  const fmtTime = iso => { try { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; } };
  const download = (name, text, type) => {
    try {
      const blob = new Blob([text], { type: type || 'text/plain' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    } catch (e) {}
  };
  const slug = s => String(s || 'artifact').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48) || 'artifact';
  const csvOf = (columns, rows) => {
    const cell = v => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    return [columns.map(cell).join(',')].concat((rows || []).map(r => r.map(cell).join(','))).join('\n');
  };
  const isNum = v => typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v)));

  // ── Renderers ─────────────────────────────────────────────────────────
  function Markdown({ text, editing, draft, setDraft }) {
    if (editing) return h('textarea', { className: 'fa-editor', value: draft, onChange: e => setDraft(e.target.value), spellCheck: true, 'aria-label': 'Edit the draft' });
    const html = window.renderFridayMarkdown ? window.renderFridayMarkdown(text || '', {}) : null;
    if (html != null) return h('div', { className: 'fa-md', dangerouslySetInnerHTML: { __html: html } });
    return h('pre', { className: 'fa-md', style: { whiteSpace: 'pre-wrap' } }, text || '');
  }

  function Table({ content, editing, draft, setDraft }) {
    const [sort, setSort] = useState(null);   // {col, dir}
    const src = editing ? draft : content;
    const columns = (src && src.columns) || [];
    const rows = (src && src.rows) || [];
    const order = useMemo(() => {
      const idx = rows.map((_, i) => i);
      if (!sort || editing) return idx;
      const c = sort.col;
      idx.sort((a, b) => {
        const x = rows[a][c], y = rows[b][c];
        const nx = isNum(x), ny = isNum(y);
        let r = nx && ny ? Number(x) - Number(y) : String(x == null ? '' : x).localeCompare(String(y == null ? '' : y));
        return sort.dir === 'desc' ? -r : r;
      });
      return idx;
    }, [rows, sort, editing]);
    const numeric = columns.map((_, c) => rows.length > 0 && rows.every(r => r[c] == null || r[c] === '' || isNum(r[c])));
    const onCell = (ri, ci, e) => {
      const v = e.currentTarget.textContent;
      setDraft(d => { const rows2 = d.rows.map(r => r.slice()); rows2[ri][ci] = isNum(v) && numeric[ci] ? Number(v) : v; return Object.assign({}, d, { rows: rows2 }); });
    };
    if (!columns.length) return h('div', { className: 'fa-empty' }, 'An empty table.');
    return h('table', { className: 'fa-table' },
      h('thead', null, h('tr', null, columns.map((c, i) => h('th', {
        key: i, onClick: () => !editing && setSort(s => s && s.col === i ? (s.dir === 'asc' ? { col: i, dir: 'desc' } : null) : { col: i, dir: 'asc' }),
        title: editing ? '' : 'Sort'
      }, String(c), !editing && h('span', { className: 'fa-sort' }, sort && sort.col === i ? (sort.dir === 'asc' ? '▲' : '▼') : '⇅'))))),
      h('tbody', null, order.map(ri => h('tr', { key: ri }, columns.map((_, ci) => h('td', {
        key: ci, className: numeric[ci] ? 'fa-num' : '',
        contentEditable: editing ? true : undefined, suppressContentEditableWarning: true,
        onBlur: editing ? e => onCell(ri, ci, e) : undefined
      }, rows[ri][ci] == null ? '' : String(rows[ri][ci])))))));
  }

  // Charts are drawn by the shared renderer (static/friday_chart.js): the same
  // file a published chart page carries, so the panel and the page never drift.
  const chartSeries = spec => (window.FridayChart ? window.FridayChart.series(spec) : { x: null, series: [] });
  function Chart({ content, size }) {
    const spec = content || {};
    const W = Math.max(240, size.w || 480), H = Math.max(160, (size.h || 300) - 28);
    const r = useMemo(() => window.FridayChart ? window.FridayChart.renderSVG(spec, W, H) : { svg: '', legend: [], empty: true }, [content, W, H]);
    if (!window.FridayChart) return h('div', { className: 'fa-empty' }, 'The chart renderer did not load.');
    if (r.empty) return h('div', { className: 'fa-empty' }, 'Nothing to chart yet.');
    return h('div', { style: { height: '100%', display: 'flex', flexDirection: 'column' } },
      h('div', { className: 'fa-chart', style: { flex: 1, minHeight: 0 }, dangerouslySetInnerHTML: { __html: r.svg } }),
      r.legend.length ? h('div', { className: 'fa-legend' }, r.legend.map((l, i) => h('span', { key: i }, h('i', { style: { background: l.color } }), l.name))) : null);
  }

  function Diff({ diff }) {
    if (window.DevDiff) return h(window.DevDiff, { diff });
    return h('pre', { style: { margin: 0, fontFamily: 'JetBrains Mono', fontSize: 11, lineHeight: 1.5, whiteSpace: 'pre-wrap' } },
      String(diff || '').split('\n').map((ln, i) => h('div', { key: i, style: { color: /^\+/.test(ln) ? '#00ff80' : /^-/.test(ln) ? '#ff5a7a' : /^@@/.test(ln) ? '#ff6dd9' : '#9aa' } }, ln || ' ')));
  }

  function HtmlFrame({ html, reloadKey }) {
    const doc = useMemo(() => window.fridayArtifactFrameDoc(html), [html, reloadKey]);
    return h('iframe', { key: reloadKey, className: 'fa-frame', sandbox: SANDBOX, srcDoc: doc, title: 'Artifact app (sandboxed)', referrerPolicy: 'no-referrer' });
  }
  function SvgFrame({ svg }) {
    return h('iframe', { className: 'fa-frame', sandbox: SVG_SANDBOX, srcDoc: svgDoc(svg), title: 'Drawing (sandboxed, no scripts)', referrerPolicy: 'no-referrer' });
  }
  function Image({ content }) {
    const src = content && content.src, alt = (content && content.alt) || '';
    if (!src || !/^(data:image\/|blob:|https?:\/\/)/i.test(src)) return h('div', { className: 'fa-empty' }, 'The image has no usable source.');
    return h('div', { style: { height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' } },
      h('img', { src, alt, style: { maxWidth: '100%', maxHeight: '100%', borderRadius: 6 }, referrerPolicy: 'no-referrer' }));
  }

  function Body({ rec, editing, draft, setDraft, reloadKey, size }) {
    const kind = rec.kind;
    switch (kind) {
      case 'markdown': return h(Markdown, { text: rec.content, editing, draft, setDraft });
      case 'table': return h(Table, { content: rec.content, editing, draft, setDraft });
      case 'chart': return h(Chart, { content: rec.content, size });
      case 'html': return h(HtmlFrame, { html: rec.content, reloadKey });
      case 'diff': return h(Diff, { diff: rec.content });
      case 'image': return h(Image, { content: rec.content });
      case 'svg': return h(SvgFrame, { svg: rec.content });
      default: return h('pre', null, JSON.stringify(rec.content, null, 1));
    }
  }

  // ── A plan on a markdown artifact (services/plans) ─────────────────────
  // Milestones with their status; "Build this plan" is the user's approval.
  const STATUS_COLOR = { todo: 'rgba(255,255,255,0.5)', doing: ACCENT, done: '#00ff80', blocked: AMBER };
  function PlanStrip({ convId, rec, busy, setBusy, setNote, onChanged }) {
    const plan = rec.meta.plan;
    const approve = () => {
      if (busy) return;
      setBusy(true);
      postJ('/api/artifacts/' + encodeURIComponent(convId) + '/' + encodeURIComponent(rec.id) + '/plan/approve', {})
        .then(({ ok, j }) => {
          if (!ok) { setNote({ text: 'Not approved: ' + ((j && j.error) || 'unknown error') }); return; }
          setNote({ text: 'Plan approved. Friday builds it from her next turn, one milestone at a time.', ok: true });
          onChanged && onChanged(j.artifact);
        }).catch(e => setNote({ text: 'Not approved: ' + e })).then(() => setBusy(false));
    };
    return h('div', { className: 'fa-plan', 'data-plan': plan.approved ? 'approved' : 'awaiting', style: { padding: '8px 10px', borderBottom: '1px solid rgba(0,212,255,0.10)', background: 'rgba(0,212,255,0.03)' } },
      h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 } },
        h('span', { style: { fontFamily: 'Orbitron, Inter, sans-serif', fontSize: 9, letterSpacing: '.2em', color: ACCENT } }, 'PLAN'),
        h('span', { style: { fontSize: 11, color: plan.approved ? '#8ff5c0' : '#ffd28a', flex: 1 } },
          plan.approved ? 'Approved' + (plan.approved_by ? ' by ' + plan.approved_by : '') : 'Awaiting your approval. Edit the text above if you want changes; Friday sees them.'),
        !plan.approved ? h('button', { className: 'fa-btn fa-primary', onClick: approve, disabled: busy }, 'Build this plan') : null),
      h('ol', { style: { margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.6 } }, plan.milestones.map(m => h('li', { key: m.n, 'data-milestone': m.status },
        h('span', { style: { color: m.status === 'done' ? 'rgba(255,255,255,0.55)' : '#eef4fa', textDecoration: m.status === 'done' ? 'line-through' : 'none' } }, m.title),
        ' ', h('span', { className: 'fa-kind', style: { color: STATUS_COLOR[m.status] || '#fff', borderColor: (STATUS_COLOR[m.status] || '#fff') + '55', background: (STATUS_COLOR[m.status] || '#fff') + '14' } }, m.status + (m.blocker ? ' · ' + m.blocker : '')),
        m.note ? h('span', { style: { color: 'rgba(255,255,255,0.6)', fontSize: 11 } }, ' — ' + m.note) : null,
        m.step ? h('span', { style: { fontFamily: MONO, fontSize: 9.5, color: 'rgba(255,255,255,0.4)' } }, ' ' + String(m.step).slice(0, 7)) : null))));
  }

  // ── The panel ─────────────────────────────────────────────────────────
  function FridayArtifactPanel({ convId, items, selectedId, onSelect, onCollapse, tab, width, onChanged }) {
    const cur = items.find(i => i.id === selectedId) || items[items.length - 1];
    const [versions, setVersions] = useState([]);
    const [viewV, setViewV] = useState(null);       // null = current
    const [rec, setRec] = useState(null);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(null);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState(null);         // {text, ok}
    const [reloadKey, setReloadKey] = useState(0);
    const bodyRef = useRef(null);
    const [size, setSize] = useState({ w: 0, h: 0 });
    // Versions already read, keyed id@version: switching back is instant and
    // never shows another artifact's body while a request is in flight.
    const cache = useRef({});

    useEffect(() => {
      if (!bodyRef.current || typeof ResizeObserver === 'undefined') return;
      const ro = new ResizeObserver(es => { const r = es[0].contentRect; setSize({ w: r.width - 20, h: r.height - 20 }); });
      ro.observe(bodyRef.current);
      return () => ro.disconnect();
    }, [cur && cur.id]);

    // A new current version (Friday's, or ours) shows the latest again.
    useEffect(() => { setViewV(null); setEditing(false); setDraft(null); }, [cur && cur.id, cur && cur.version]);
    // Another artifact: never leave the previous one's body on screen.
    useEffect(() => { setRec(cur && cache.current[cur.id + '@' + cur.version] || null); setNote(null); }, [cur && cur.id]);

    useEffect(() => {
      if (!cur) return;
      let dead = false;
      const q = '?include=versions' + (viewV ? '&version=' + viewV : '');
      const key = cur.id + '@' + (viewV || cur.version);
      if (cache.current[key]) setRec(cache.current[key]);
      getJ('/api/artifacts/' + encodeURIComponent(convId) + '/' + encodeURIComponent(cur.id) + q)
        .then(d => {
          if (d.artifact) { cache.current[cur.id + '@' + d.artifact.version] = d.artifact; if (!dead) setRec(d.artifact); }
          if (!dead && d.versions) setVersions(d.versions);
        }).catch(() => {});
      return () => { dead = true; };
    }, [convId, cur && cur.id, cur && cur.version, viewV]);

    if (!cur) return null;
    const total = cur.version;
    const shown = viewV || total;
    const editable = rec && (rec.kind === 'markdown' || rec.kind === 'table' || rec.kind === 'html' || rec.kind === 'svg') && !viewV;
    const flush = rec && (rec.kind === 'html' || rec.kind === 'svg') && !editing;

    const startEdit = () => { if (!rec) return; setDraft(rec.kind === 'table' ? { columns: rec.content.columns.slice(), rows: rec.content.rows.map(r => r.slice()) } : rec.content); setEditing(true); setNote(null); };
    const save = () => {
      if (!rec || busy) return;
      setBusy(true);
      postJ('/api/artifacts/' + encodeURIComponent(convId) + '/' + encodeURIComponent(cur.id), { content: draft })
        .then(({ ok, j }) => {
          if (!ok) { setNote({ text: 'Not saved: ' + ((j && j.error) || 'unknown error') }); return; }
          setEditing(false); setDraft(null);
          setNote({ text: 'Saved as v' + j.artifact.version + ' · by you · Friday sees this change on her next turn', ok: true });
          onChanged && onChanged(j.artifact);
        }).catch(e => setNote({ text: 'Not saved: ' + e })).then(() => setBusy(false));
    };
    const restore = () => {
      if (!viewV || busy) return;
      setBusy(true);
      postJ('/api/artifacts/' + encodeURIComponent(convId) + '/' + encodeURIComponent(cur.id) + '/restore', { version: viewV })
        .then(({ ok, j }) => {
          if (!ok) { setNote({ text: 'Not restored: ' + ((j && j.error) || 'unknown error') }); return; }
          setNote({ text: 'Restored v' + viewV + ' as v' + j.artifact.version + ' · every version is still here', ok: true });
          onChanged && onChanged(j.artifact);
        }).catch(e => setNote({ text: 'Not restored: ' + e })).then(() => setBusy(false));
    };
    const publish = () => {
      if (!rec || busy) return;
      setBusy(true);
      postJ('/api/publish/request', { conversation_id: convId, artifact_id: cur.id, requested_by: 'panel' })
        .then(({ ok, j }) => {
          if (!ok) { setNote({ text: 'Could not ask to publish: ' + ((j && j.error) || 'unknown error') }); return; }
          if (j.status === 'refused') { setNote({ text: 'Not publishable as it is: ' + (j.refused || []).join('; ') }); return; }
          const a = j.approval || {};
          setNote({ text: a.status === 'pending'
            ? 'A publish card is waiting for your approval (' + ((a.payload || {}).size || '') + ', ' + ((a.payload || {}).adapter_label || '') + '). Nothing is public until you approve it.'
            : 'Publish card: ' + a.status, ok: a.status === 'pending' });
          try { window.fridayRunActions && a.status === 'pending' && window.fridayRunActions([{ type: 'navigate', workspace: 'system', tab: 'approvals' }]); } catch (_) {}
        }).catch(e => setNote({ text: 'Could not ask to publish: ' + e })).then(() => setBusy(false));
    };
    const exportIt = () => {
      if (!rec) return;
      const base = slug(rec.title) + '-v' + rec.version;
      if (rec.kind === 'table') return download(base + '.csv', csvOf(rec.content.columns, rec.content.rows), 'text/csv');
      if (rec.kind === 'chart') { const s = chartSeries(rec.content); return download(base + '.csv', csvOf([s.x || 'x'].concat(s.series.map(q => q.name)), (s.series[0] ? s.series[0].points : []).map((p, i) => [p.x].concat(s.series.map(q => q.points[i] ? q.points[i].y : '')))), 'text/csv'); }
      if (rec.kind === 'markdown') return download(base + '.md', rec.content, 'text/markdown');
      if (rec.kind === 'html') return download(base + '.html', rec.content, 'text/html');
      if (rec.kind === 'svg') return download(base + '.svg', rec.content, 'image/svg+xml');
      if (rec.kind === 'diff') return download(base + '.diff', rec.content, 'text/plain');
      if (rec.kind === 'image' && rec.content && rec.content.src) { const a = document.createElement('a'); a.href = rec.content.src; a.download = base; document.body.appendChild(a); a.click(); a.remove(); }
    };
    const vAt = versions.find(v => v.version === shown) || rec;
    const byYou = vAt && vAt.author === 'you';

    return h('div', { className: 'fa-panel' + (tab ? ' fa-tab' : ''), style: tab ? undefined : { width, flexShrink: 0 }, 'data-artifact-panel': cur.id,
      'data-artifact-shown': rec ? rec.id + '@' + rec.version : '', role: 'complementary', 'aria-label': 'Artifact panel' },
      h('div', { className: 'fa-head' },
        h('div', { className: 'fa-eyebrow' },
          h('span', { className: 'fa-brand' }, 'FRIDAY ', h('b', null, '· PANEL')),
          h('span', { style: { display: 'flex', gap: 2 } },
            h('button', { className: 'fa-icon', title: 'Publish to the web (asks first)', 'aria-label': 'Publish to the web', onClick: publish, disabled: busy || !!viewV }, '⤒'),
            h('button', { className: 'fa-icon', title: 'Export', 'aria-label': 'Export', onClick: exportIt }, '⤓'),
            rec && rec.kind === 'html' ? h('button', { className: 'fa-icon', title: 'Reload the app', 'aria-label': 'Reload', onClick: () => setReloadKey(k => k + 1) }, '↻') : null,
            h('button', { className: 'fa-icon', title: tab ? 'Back to the chat' : 'Collapse the panel', 'aria-label': 'Collapse', onClick: onCollapse }, tab ? '✕' : '⟩'))),
        h('div', { className: 'fa-title-row' },
          items.length > 1
            ? h('select', { className: 'fa-select', value: cur.id, onChange: e => onSelect(e.target.value), 'aria-label': 'Which artifact' },
                items.map(it => h('option', { key: it.id, value: it.id }, (KIND_GLYPH[it.kind] || '') + ' ' + it.title)))
            : h('span', { className: 'fa-title', title: cur.title }, cur.title),
          h('span', { className: 'fa-kind' }, KIND_LABEL[cur.kind] || cur.kind),
          byYou ? h('span', { className: 'fa-by', title: 'This version was edited by you' }, 'BY YOU') : null),
        h('div', { className: 'fa-timeline' },
          h('button', { className: 'fa-icon', disabled: shown <= 1, onClick: () => setViewV(Math.max(1, shown - 1)), 'aria-label': 'Earlier version' }, '◀'),
          h('input', { type: 'range', min: 1, max: total, value: shown, onChange: e => { const v = +e.target.value; setViewV(v >= total ? null : v); }, 'aria-label': 'Version' }),
          h('button', { className: 'fa-icon', disabled: shown >= total, onClick: () => { const v = shown + 1; setViewV(v >= total ? null : v); }, 'aria-label': 'Later version' }, '▶'),
          h('span', { className: 'fa-vlabel' }, 'v' + shown + ' / ' + total)),
        h('div', { className: 'fa-tools' },
          viewV ? h('button', { className: 'fa-btn fa-amber', onClick: restore, disabled: busy }, 'Restore v' + viewV + ' as new') : null,
          viewV ? h('button', { className: 'fa-btn fa-quiet', onClick: () => setViewV(null) }, 'Latest') : null,
          !viewV && editable && !editing ? h('button', { className: 'fa-btn', onClick: startEdit }, rec.kind === 'table' ? 'Edit cells' : 'Edit') : null,
          editing ? h('button', { className: 'fa-btn fa-primary', onClick: save, disabled: busy }, busy ? 'Saving…' : 'Save as new version') : null,
          editing ? h('button', { className: 'fa-btn fa-quiet', onClick: () => { setEditing(false); setDraft(null); } }, 'Cancel') : null,
          vAt && vAt.note && !editing ? h('span', { style: { fontSize: 10.5, color: 'rgba(255,255,255,0.5)', marginLeft: 'auto', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: vAt.note }, vAt.note) : null)),
      note ? h('div', { className: 'fa-note' + (note.ok ? ' fa-ok' : ''), role: 'status' }, note.text) : null,
      viewV ? h('div', { className: 'fa-note', role: 'status' }, 'Viewing v' + viewV + ' of ' + total + ' · nothing is changed until you restore it') : null,
      rec && rec.meta && rec.meta.plan ? h(PlanStrip, { convId, rec, busy, setBusy, setNote, onChanged }) : null,
      h('div', { className: 'fa-body' + (flush ? ' fa-flush' : ''), ref: bodyRef },
        rec ? h(Body, { rec, editing, draft, setDraft, reloadKey, size }) : h('div', { className: 'fa-empty' }, 'Loading…')),
      h('div', { className: 'fa-foot' },
        h('span', null, vAt ? (vAt.author === 'you' ? 'you' : 'Friday') + ' · ' + fmtTime(vAt.created_at) : ''),
        h('span', { title: rec ? rec.sha256 : '' }, rec ? 'sha ' + String(rec.sha256 || '').slice(0, 10) : '')));
  }
  window.FridayArtifactPanel = FridayArtifactPanel;

  // ── The codebase panel: Preview, Files, Changes ────────────────────────
  // A chat bound to a codebase (services/codebases) gets these three tabs.
  // Preview runs the one-document preview in the same sandboxed frame an
  // html artifact uses; Files lists and edits (a hand edit is a step by
  // "you"); Changes is the step list with undo and a diff per step.
  const TIER_LABEL = { B0: 'frame', B1: 'this PC', B2: 'box' };
  const WIDTHS = [['phone', 390], ['tablet', 768], ['desktop', 0]];

  const stepKeyOf = cb => (cb && cb.updated_at) || '';
  function CodebasePanel({ convId, codebase, artifactsTab, onCollapse, tab, width, refreshKey }) {
    const [view, setView] = useState('preview');
    const [html, setHtml] = useState(null);
    const [files, setFiles] = useState([]);
    const [file, setFile] = useState(null);           // {path, content}
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState('');
    const [steps, setSteps] = useState([]);
    const [diff, setDiff] = useState(null);           // {sha, text}
    const [frameW, setFrameW] = useState(0);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState(null);
    const [reload, setReload] = useState(0);
    const [pointing, setPointing] = useState(false);
    const [pick, setPick] = useState(codebase.pick || null);
    // A codebase that improves a bundle workspace (spec §4.9.1): Compare shows
    // the live version beside the improved one; Swap in raises the ONE card.
    const wsId = codebase.workspace_id || null;
    const isBundle = codebase.template === 'bundle';
    const [compare, setCompare] = useState(false);
    const [liveDoc, setLiveDoc] = useState(null);
    useEffect(() => {
      if (!compare || !wsId) return;
      api('/api/workspaces/' + encodeURIComponent(wsId) + '/bundle').then(r => r.text())
        .then(t => setLiveDoc(window.fridayArtifactFrameDoc(t))).catch(() => setLiveDoc(window.fridayArtifactFrameDoc('<p>The live version could not be read.</p>')));
    }, [compare, wsId, stepKeyOf(codebase)]);
    const swapIn = () => {
      if (busy) return;
      setBusy(true);
      postJ('/api/workspaces/swap', { codebase_id: codebase.id }).then(({ ok, j }) => {
        if (!ok) { setNote({ text: (j && j.error) || 'Refused.' }); return; }
        const p = (j.approval && j.approval.payload) || {};
        setNote({ text: (p.is_new ? 'Card raised: install it as a workspace. ' : 'Card raised: swap it in. ') + (p.spoken || 'Decide on the card.'), ok: true });
      }).catch(e => setNote({ text: 'Could not raise the card: ' + e })).then(() => setBusy(false));
    };
    const frameRef = useRef(null);
    const base = '/api/codebases/' + encodeURIComponent(codebase.id);
    // The picker's one message, from this panel's own frame only.
    useEffect(() => {
      const onMsg = e => {
        if (!frameRef.current || e.source !== frameRef.current.contentWindow) return;
        const d = e.data;
        if (!d || d.__friday !== 'pick' || typeof d.selector !== 'string') return;
        const body = { selector: d.selector, tag: d.tag, text: d.text, snippet: d.snippet, rect: d.rect, font_px: d.font_px };
        postJ(base + '/pick', body).then(({ ok, j }) => { if (ok) setPick(j.pick); else setNote({ text: 'Could not keep that selection: ' + ((j && j.error) || '') }); }).catch(() => {});
      };
      window.addEventListener('message', onMsg);
      return () => window.removeEventListener('message', onMsg);
    }, [base]);
    const quick = action => {
      if (!pick || busy) return;
      setBusy(true);
      postJ(base + '/quick-style', { selector: pick.selector, action, font_px: pick.font_px }).then(({ ok, j }) => {
        if (!ok) { setNote({ text: (j && j.error) || 'That edit did not apply.' }); return; }
        setNote({ text: j.step.summary + ' (a step you can undo)', ok: true });
        loadSteps(); loadPreview();
      }).catch(e => setNote({ text: 'That edit did not apply: ' + e })).then(() => setBusy(false));
    };
    const clearPick = () => { postJ(base + '/pick/clear', {}).then(() => setPick(null)).catch(() => {}); };

    const loadPreview = useCallback(() => getJ(base + '/preview').then(d => setHtml(d.html || '')).catch(() => setHtml('')), [base]);
    const loadFiles = useCallback(() => getJ(base + '/files').then(d => setFiles(d.files || [])).catch(() => {}), [base]);
    const loadSteps = useCallback(() => getJ(base + '/steps').then(d => setSteps(d.steps || [])).catch(() => {}), [base]);
    useEffect(() => { loadPreview(); loadFiles(); loadSteps(); setDiff(null); }, [refreshKey, loadPreview, loadFiles, loadSteps]);
    useEffect(() => { if (file && !editing) getJ(base + '/file?path=' + encodeURIComponent(file.path)).then(d => d.content != null && setFile({ path: file.path, content: d.content })).catch(() => {}); }, [refreshKey]);  // eslint-disable-line

    const openFile = f => { setEditing(false); getJ(base + '/file?path=' + encodeURIComponent(f.path)).then(d => setFile({ path: f.path, content: d.content || '' })).catch(() => setNote({ text: 'Could not read ' + f.path })); };
    const save = () => {
      if (!file || busy) return;
      setBusy(true);
      postJ(base + '/file', { path: file.path, content: draft }).then(({ ok, j }) => {
        if (!ok) { setNote({ text: 'Not saved: ' + ((j && j.error) || 'unknown error') }); return; }
        setEditing(false); setFile({ path: file.path, content: draft });
        setNote({ text: j.step ? 'Saved as a step: ' + j.step.summary : 'Nothing changed.', ok: true });
        loadSteps(); loadPreview();
      }).catch(e => setNote({ text: 'Not saved: ' + e })).then(() => setBusy(false));
    };
    const undo = () => {
      if (busy) return;
      setBusy(true);
      postJ(base + '/undo', {}).then(({ ok, j }) => {
        if (!ok) { setNote({ text: (j && j.error) || 'Nothing to undo.' }); return; }
        setNote({ text: j.step.summary, ok: true });
        loadSteps(); loadPreview(); loadFiles();
      }).catch(e => setNote({ text: 'Undo failed: ' + e })).then(() => setBusy(false));
    };
    const showDiff = st => { if (diff && diff.sha === st.sha) { setDiff(null); return; } getJ(base + '/diff/' + st.sha).then(d => setDiff({ sha: st.sha, text: d.diff || '' })).catch(() => {}); };
    const kindChip = k => chipEl(k === 'undo' ? 'UNDO' : k === 'start' ? 'START' : 'STEP', k === 'undo' ? AMBER : k === 'start' ? 'rgba(255,255,255,0.5)' : ACCENT);
    const tabBtn = (id, label) => h('button', { className: 'fa-btn' + (view === id ? ' fa-primary' : ' fa-quiet'), onClick: () => setView(id), role: 'tab', 'aria-selected': view === id }, label);
    const doc = useMemo(() => html == null ? null : window.fridayArtifactFrameDoc(pointing ? window.fridayPickerDoc(html) : html), [html, reload, pointing]);

    return h('div', { className: 'fa-panel' + (tab ? ' fa-tab' : ''), style: tab ? undefined : { width, flexShrink: 0 }, 'data-codebase-panel': codebase.id, 'data-codebase-view': view, role: 'complementary', 'aria-label': 'Codebase panel' },
      h('div', { className: 'fa-head' },
        h('div', { className: 'fa-eyebrow' },
          h('span', { className: 'fa-brand' }, 'FRIDAY ', h('b', null, '· CODEBASE')),
          h('span', { style: { display: 'flex', gap: 2 } },
            view === 'preview' ? h('button', { className: 'fa-icon', title: 'Reload the preview', 'aria-label': 'Reload', onClick: () => { setReload(k => k + 1); loadPreview(); } }, '↻') : null,
            h('button', { className: 'fa-icon', title: 'Export as a plain project (zip, nothing of Friday\'s inside)', 'aria-label': 'Export', onClick: () => exportZip(base, codebase.slug) }, '⤓'),
            wsId ? h('span', { className: 'fa-chip', 'data-improves': wsId, title: 'This codebase improves a workspace in your dock; nothing goes live until you approve a swap' }, 'improves ' + wsId) : null,
            h('button', { className: 'fa-icon', title: tab ? 'Back to the chat' : 'Collapse the panel', 'aria-label': 'Collapse', onClick: onCollapse }, tab ? '✕' : '⟩'))),
        h('div', { className: 'fa-title-row' },
          h('span', { className: 'fa-title', title: codebase.title }, codebase.title),
          chipEl(codebase.template || 'folder', 'rgba(255,255,255,0.6)'),
          chipEl((codebase.tier || 'B0') + ' · ' + (TIER_LABEL[codebase.tier || 'B0'] || ''), ACCENT, 'Where the code runs: B0 is the browser frame, no process, no install')),
        h('div', { className: 'fa-tools', role: 'tablist' },
          tabBtn('preview', 'Preview'), tabBtn('files', 'Files'), tabBtn('changes', 'Changes' + (steps.length > 1 ? ' · ' + (steps.length - 1) : '')),
          artifactsTab ? tabBtn('artifacts', 'Artifacts') : null,
          view === 'preview' ? h('button', { className: 'fa-btn' + (pointing ? ' fa-primary' : ' fa-quiet'), 'data-point-mode': pointing ? 'on' : 'off', onClick: () => setPointing(v => !v), title: 'Point at something in the preview, then say what to change' }, '\u2316 Point') : null,
          view === 'preview' && wsId ? h('button', { className: 'fa-btn' + (compare ? ' fa-primary' : ' fa-quiet'), 'data-compare': compare ? 'on' : 'off', onClick: () => setCompare(v => !v), title: 'The version in your dock beside the improved one' }, 'Compare') : null,
          view === 'preview' && isBundle ? h('button', { className: 'fa-btn fa-amber', 'data-swap': wsId ? 'swap' : 'install', disabled: busy, onClick: swapIn, title: wsId ? 'Raise the one card that swaps this in as the live version' : 'Raise the one card that installs this as a new workspace in your dock' }, wsId ? 'Swap in' : 'Install as workspace') : null,
          view === 'preview' ? h('span', { style: { marginLeft: 'auto', display: 'flex', gap: 4 } }, WIDTHS.map(([name, w]) => h('button', { key: name, className: 'fa-btn fa-quiet', style: frameW === w ? { color: ACCENT, borderColor: ACCENT } : undefined, onClick: () => setFrameW(w), title: name }, name))) : null,
          view === 'changes' ? h('button', { className: 'fa-btn fa-amber', style: { marginLeft: 'auto' }, onClick: undo, disabled: busy || steps.filter(s => s.kind === 'step').length === 0 }, 'Undo last step') : null)),
      note ? h('div', { className: 'fa-note' + (note.ok ? ' fa-ok' : ''), role: 'status' }, note.text) : null,
      view === 'preview' && (pointing || pick) ? h('div', { className: 'fa-note', 'data-pick': pick ? pick.selector : '', role: 'status', style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' } },
        pick ? h(React.Fragment, null,
          h('span', { style: { flexBasis: '100%', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: pick.selector },
            h('span', { style: { fontFamily: MONO, fontSize: 10, marginRight: 6 } }, '<' + (pick.tag || 'element') + '>'),
            (pick.text ? '\u201c' + pick.text.slice(0, 60) + '\u201d' : pick.selector) + ' \u2014 say what to change, here or by voice, or:'),
          ['bigger', 'smaller', 'bolder', 'center', 'hide'].map(a => h('button', { key: a, className: 'fa-btn fa-amber', onClick: () => quick(a), disabled: busy }, a)),
          h('button', { className: 'fa-btn fa-quiet', onClick: clearPick, title: 'Forget the selection' }, 'clear'))
          : h(React.Fragment, null,
            h('span', { style: { flexBasis: '100%' } }, 'Point mode: click anything in the preview to select it, then say what to change, here or by voice, or:'),
            ['bigger', 'smaller', 'bolder', 'center', 'hide'].map(a => h('button', { key: a, className: 'fa-btn fa-amber', disabled: true }, a)),
            h('button', { className: 'fa-btn fa-quiet', onClick: () => setPointing(false), title: 'Leave point mode' }, 'done'))) : null,
      view === 'preview' && compare && wsId ? h('div', { className: 'fa-body fa-flush', 'data-compare-view': '1', style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, background: 'rgba(0,0,0,0.35)' } },
        h('div', { style: { display: 'flex', flexDirection: 'column', minWidth: 0 } },
          h('div', { style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', color: 'rgba(255,255,255,0.5)', padding: '3px 8px' } }, 'IN YOUR DOCK NOW'),
          liveDoc == null ? h('div', { className: 'fa-empty' }, 'Loading the live version…')
            : h('iframe', { className: 'fa-frame', sandbox: SANDBOX, srcDoc: liveDoc, referrerPolicy: 'no-referrer', title: 'The live workspace (sandboxed)' })),
        h('div', { style: { display: 'flex', flexDirection: 'column', minWidth: 0, borderLeft: '1px solid rgba(0,212,255,0.2)' } },
          h('div', { style: { fontFamily: MONO, fontSize: 9, letterSpacing: '.08em', color: ACCENT, padding: '3px 8px' } }, 'IMPROVED (THIS CHAT)'),
          doc == null ? h('div', { className: 'fa-empty' }, 'Loading the preview…')
            : h('iframe', { key: reload + ':cmp:' + codebase.id, className: 'fa-frame', sandbox: SANDBOX, srcDoc: doc, referrerPolicy: 'no-referrer', title: 'The improved workspace (sandboxed)' })))
      : view === 'preview' ? h('div', { className: 'fa-body fa-flush', style: { display: 'flex', justifyContent: 'center', background: frameW ? 'rgba(0,0,0,0.35)' : undefined } },
        doc == null ? h('div', { className: 'fa-empty' }, 'Loading the preview…')
          : h('iframe', { key: reload + ':' + codebase.id + ':' + (pointing ? 'p' : 'v'), ref: frameRef, className: 'fa-frame', sandbox: SANDBOX, srcDoc: doc, referrerPolicy: 'no-referrer', title: 'Preview (sandboxed)', style: frameW ? { width: frameW, maxWidth: '100%', borderLeft: '1px solid rgba(0,212,255,0.15)', borderRight: '1px solid rgba(0,212,255,0.15)' } : undefined })) : null,
      view === 'files' ? h('div', { className: 'fa-body', style: { display: 'flex', gap: 10, padding: 0 } },
        h('div', { style: { width: 180, flexShrink: 0, borderRight: '1px solid rgba(0,212,255,0.10)', overflow: 'auto', padding: '8px 6px', fontFamily: MONO, fontSize: 11 } },
          files.map(f => h('div', { key: f.path, onClick: () => openFile(f), title: f.bytes + ' bytes', style: { padding: '3px 6px', cursor: 'pointer', borderRadius: 4, color: file && file.path === f.path ? ACCENT : '#dfe7f2', background: file && file.path === f.path ? 'rgba(0,212,255,0.08)' : undefined, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, f.path))),
        h('div', { style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', padding: 8 } },
          file ? h(React.Fragment, null,
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 } },
              h('span', { style: { fontFamily: MONO, fontSize: 11, color: ACCENT, flex: 1 } }, file.path),
              editing ? h('button', { className: 'fa-btn fa-primary', onClick: save, disabled: busy }, busy ? 'Saving…' : 'Save as a step') : h('button', { className: 'fa-btn', onClick: () => { setDraft(file.content); setEditing(true); } }, 'Edit'),
              editing ? h('button', { className: 'fa-btn fa-quiet', onClick: () => setEditing(false) }, 'Cancel') : null),
            editing ? h('textarea', { className: 'fa-editor', value: draft, onChange: e => setDraft(e.target.value), spellCheck: false, style: { flex: 1 } })
              : h('pre', { style: { margin: 0, flex: 1, overflow: 'auto', fontFamily: MONO, fontSize: 11.5, lineHeight: 1.5, color: '#dfe7f2', whiteSpace: 'pre-wrap' } }, file.content))
            : h('div', { className: 'fa-empty' }, 'Pick a file.'))) : null,
      view === 'changes' ? h('div', { className: 'fa-body' },
        steps.map(st => h('div', { key: st.sha, 'data-step': st.sha, style: { padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }, onClick: () => showDiff(st) },
            kindChip(st.kind),
            h('span', { style: { flex: 1, fontSize: 12.5, color: '#eef4fa' } }, st.summary.replace(/^Start: |^Undo: /, m => m)),
            h('span', { style: { fontFamily: MONO, fontSize: 9.5, color: 'rgba(255,255,255,0.45)', whiteSpace: 'nowrap' } }, (st.who === 'you' ? 'you' : st.who) + ' · ' + fmtTime(st.at))),
          st.receipt && st.receipt.files && st.receipt.files.length ? h('div', { style: { fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.45)', marginTop: 2 } }, st.receipt.files.map(f => f.path).concat((st.receipt.deleted || []).map(d => '− ' + d)).join(' · ')) : null,
          diff && diff.sha === st.sha ? h('div', { style: { marginTop: 6 } }, h(Diff, { diff: diff.text })) : null))) : null,
      view === 'artifacts' && artifactsTab ? artifactsTab : null,
      h('div', { className: 'fa-foot' },
        h('span', null, codebase.existing ? 'your folder · branch ' + codebase.branch : 'Friday\'s codebase · ' + (codebase.template || '')),
        h('span', null, (steps.length ? steps.length - 1 : 0) + ' step' + (steps.length === 2 ? '' : 's'))));
  }
  // The export download goes through the same authenticated fetch as every
  // other request, then hands the browser a blob to save.
  function exportZip(base, slug) {
    api(base + '/export').then(r => r.ok ? r.blob() : Promise.reject(new Error('HTTP ' + r.status))).then(blob => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = (slug || 'codebase') + '.zip';
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    }).catch(() => {});
  }
  function chipEl(text, color, title) {
    return h('span', { className: 'fa-kind', title, style: { color, borderColor: color + '55', background: color + '14' } }, text);
  }
  window.FridayCodebasePanel = CodebasePanel;

  // ── The host: one column beside the chat, or a tab over it ────────────
  const SIDE_MIN_HOST_W = 860;   // below this the panel is a tab over the chat
  const PANEL_MIN_W = 300, CHAT_MIN_W = 360;

  function FridayArtifactHost({ convId, mode, children }) {
    ensureCss();
    const [items, setItems] = useState([]);
    const [sel, setSel] = useState(null);
    const [codebase, setCodebase] = useState(null);
    const [stepKey, setStepKey] = useState(0);
    const [open, setOpen] = useState(() => ls.get('friday_artifact_panel_open', '1') === '1');
    const [width, setWidth] = useState(() => Math.max(PANEL_MIN_W, +ls.get('friday_artifact_panel_w', 440) || 440));
    const [hostW, setHostW] = useState(0);
    const [dragging, setDragging] = useState(false);
    const ref = useRef(null);
    const dragStart = useRef(null);

    useEffect(() => {
      if (!ref.current || typeof ResizeObserver === 'undefined') { setHostW(window.innerWidth); return; }
      const ro = new ResizeObserver(es => setHostW(es[0].contentRect.width));
      ro.observe(ref.current);
      return () => ro.disconnect();
    }, []);

    const refresh = useCallback((selectId) => {
      if (!convId) return Promise.resolve();
      return getJ('/api/artifacts?conversation_id=' + encodeURIComponent(convId)).then(d => {
        const list = d.artifacts || [];
        setItems(list);
        if (selectId) setSel(selectId);
        else setSel(s => list.some(i => i.id === s) ? s : (list.length ? list[list.length - 1].id : null));
      }).catch(() => {});
    }, [convId]);

    useEffect(() => { setItems([]); setSel(null); refresh(); }, [convId, refresh]);
    // Is this chat bound to a codebase? Read once per conversation.
    useEffect(() => {
      setCodebase(null);
      if (!convId) return;
      let dead = false;
      getJ('/api/conversations/' + encodeURIComponent(convId)).then(d => {
        const id = d && d.conversation && d.conversation.codebase;
        if (!id || dead) return;
        return getJ('/api/codebases/' + encodeURIComponent(id)).then(c => { if (!dead && c.codebase) { setCodebase(c.codebase); setOpen(true); } });
      }).catch(() => {});
      return () => { dead = true; };
    }, [convId]);

    // News from the server: an artifact_put landed in this conversation. The
    // event carries no content; the store is re-read.
    useEffect(() => {
      if (!convId) return;
      const unsub = busSubscribe(m => {
        if (m === null) { refresh(); return; }   // the stream is down: a poll
        if (m.conversation_id && m.conversation_id !== convId) return;
        if (m.type === 'codebase_step') { setStepKey(k => k + 1); setOpen(true); return; }
        refresh(m.artifact_id).then(() => { if (m.author !== 'you') setOpen(true); });
      });
      const onOpen = e => { const d = e.detail || {}; if (d.convId && d.convId !== convId) return; refresh(d.artifactId).then(() => setOpen(true)); };
      window.addEventListener('friday:artifact-open', onOpen);
      return () => { unsub(); window.removeEventListener('friday:artifact-open', onOpen); };
    }, [convId, refresh]);

    useEffect(() => { ls.set('friday_artifact_panel_open', open ? '1' : '0'); }, [open]);
    useEffect(() => { ls.set('friday_artifact_panel_w', width); }, [width]);

    // The divider drag.
    useEffect(() => {
      if (!dragging) return;
      const mv = e => { const st = dragStart.current; if (!st) return; const w = Math.min(hostW - CHAT_MIN_W - 6, Math.max(PANEL_MIN_W, st.w + (st.x - e.clientX))); setWidth(w); };
      const up = () => setDragging(false);
      window.addEventListener('mousemove', mv); window.addEventListener('mouseup', up);
      return () => { window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up); };
    }, [dragging, hostW]);

    const has = items.length > 0 || !!codebase;
    const side = hostW >= SIDE_MIN_HOST_W;
    const cur = items.find(i => i.id === sel) || items[items.length - 1];
    const panelW = Math.min(width, Math.max(PANEL_MIN_W, hostW - CHAT_MIN_W - 6));
    const onChanged = rec => refresh(rec && rec.id);
    const artifactPanel = items.length ? h(FridayArtifactPanel, { convId, items, selectedId: sel, onSelect: setSel, onCollapse: () => setOpen(false), tab: !side, width: panelW, onChanged }) : null;
    const panel = codebase
      ? h(CodebasePanel, { convId, codebase, artifactsTab: artifactPanel, onCollapse: () => setOpen(false), tab: !side, width: panelW, refreshKey: stepKey })
      : artifactPanel;
    const stripTitle = codebase ? codebase.title : (cur ? (KIND_GLYPH[cur.kind] || '') + ' ' + cur.title : 'Panel');
    const stripCount = codebase ? null : items.length;

    return h('div', { ref, className: 'fa-host', style: { flexDirection: side ? 'row' : 'column' }, 'data-artifact-host': has ? (open ? 'open' : 'closed') : 'none' },
      has && !side ? h('div', { className: 'fa-strip', role: 'tablist' },
        h('button', { role: 'tab', 'aria-selected': !open, className: open ? '' : 'fa-on', onClick: () => setOpen(false) }, 'Chat'),
        h('button', { role: 'tab', 'aria-selected': open, className: open ? 'fa-on' : '', onClick: () => setOpen(true), title: stripTitle },
          stripTitle, stripCount ? h('span', { className: 'fa-count' }, stripCount) : null)) : null,
      h('div', { className: 'fa-chat', style: has && !side && open ? { display: 'none' } : undefined }, children),
      has && side && open ? h('div', { className: 'fa-divider' + (dragging ? ' fa-dragging' : ''), title: 'Drag to resize', onMouseDown: e => { e.preventDefault(); dragStart.current = { x: e.clientX, w: panelW }; setDragging(true); } }) : null,
      has && open ? panel : null,
      has && side && !open ? h('div', { className: 'fa-rail', role: 'button', title: 'Open the panel', 'aria-label': 'Open the panel', onClick: () => setOpen(true) },
        h('span', { style: { color: ACCENT, fontSize: 13 } }, '⟨'),
        items.length ? h('span', { className: 'fa-rail-count' }, items.length) : null,
        h('span', { className: 'fa-rail-label' }, codebase ? 'CODEBASE' : 'PANEL')) : null);
  }
  window.FridayArtifactHost = FridayArtifactHost;
})();
