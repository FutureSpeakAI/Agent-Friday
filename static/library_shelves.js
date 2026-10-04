/* The Library on shelves: the documents you gave Friday to read, standing in 3D.
 *
 * Folders stand as tall glass shelves in one arc; documents stand on their
 * shelf as cards; opening a document fans its sections out in place, in reading
 * order; opening a section lays its passages on a reading plane that faces the
 * camera. It is the Shelves layout of the one 3D engine (studio_files3d.js),
 * fed a Library record list; this file owns the data, the keys, the search
 * lights and the reading plane, not the drawing loop.
 *
 * Rules this file keeps:
 *   - colours come from the --fr-* tokens (or the engine's BRAND table when a
 *     token cannot be read); no literal colour appears here;
 *   - Orbitron only on small card labels, Inter for reading text, JetBrains Mono
 *     for page numbers and times; reading text is never under 12px on screen;
 *   - nothing is a figure: the search "walker" is light along a path, with no
 *     face, hands, eyes or pointing glyph;
 *   - a search lights its route only as real decisions arrive, paced to at most
 *     three visible lights a second; confidence is a line weight and a word,
 *     never a new hue; an empty wait is completely still;
 *   - a camera flight takes at most 1300 ms, any key or click skips it, and
 *     under reduced motion it is a cut;
 *   - sections and passages exist only for the opened document and section;
 *   - the workspace's text twin carries the same tree; keys never need the mouse.
 *
 * Loaded by index.html after library_ws.js; defines window.LibraryShelves3D.
 */
(function () {
  'use strict';
  if (window.LibraryShelves3D) return;
  const h = React.createElement;
  const { useState, useEffect, useRef } = React;

  function api(url) {
    const tok = window.__FRIDAY_API_TOKEN || '';
    return fetch(url, { headers: tok ? { 'X-Friday-Token': tok } : {} });
  }
  const getJSON = url => api(url).then(r => r.json());
  const clock = t => (window.__libraryReaderClock ? window.__libraryReaderClock(t) : String(Math.floor(t || 0)));

  // ── colours: tokens first, the engine's BRAND table when a token is unreadable ──
  function cssVar(name) {
    try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); } catch (_) { return ''; }
  }
  function parseColor(s) {
    s = String(s || '').trim();
    let m = /^#([0-9a-f]{3})$/i.exec(s);
    if (m) return parseInt(m[1].split('').map(c => c + c).join(''), 16);
    m = /^#([0-9a-f]{6})$/i.exec(s);
    if (m) return parseInt(m[1], 16);
    m = /^rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/i.exec(s);
    if (m) return (Number(m[1]) << 16) | (Number(m[2]) << 8) | Number(m[3]);
    return null;
  }
  const brand = key => (window.Friday3D && window.Friday3D.BRAND ? window.Friday3D.BRAND[key] : 0);
  function tokenColor(name, brandKey) {
    const c = parseColor(cssVar(name));
    return c != null ? c : brand(brandKey);
  }
  // A CSS colour string for 2D canvas drawing: the token itself, else the BRAND value.
  function cssColor(name, brandKey, alpha) {
    const v = cssVar(name);
    if (v && alpha == null) return v;
    const c = v ? parseColor(v) : null;
    const n = c != null ? c : brand(brandKey);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + (alpha == null ? 1 : alpha) + ')';
  }

  // ── confidence: a word and a line weight, never a hue ──
  function confidence(p) {
    p = Number(p);
    if (p >= 0.8) return { word: 'sure', radius: 0.07 };
    if (p >= 0.5) return { word: 'fairly sure', radius: 0.04 };
    return { word: 'a guess', radius: 0.02 };
  }

  // A cylinder between a and b: its midpoint, length and unit direction.
  function segmentSpec(a, b, radius) {
    const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const len = Math.hypot(d[0], d[1], d[2]);
    const dir = len > 1e-9 ? [d[0] / len, d[1] / len, d[2] / len] : [0, 1, 0];
    return { mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2], len, dir, radius };
  }

  // ── pacing: at most three visible lights a second ──
  // Jobs run in order, never closer together than minGap; nothing runs, and no
  // timer is armed, while the queue is empty.
  const MIN_GAP = 334;
  function createPacer(env) {
    const q = [];
    let last = -Infinity, timer = null;
    const gap = env.minGap || MIN_GAP;
    function pump() {
      timer = null;
      if (!q.length) return;
      const wait = last + gap - env.now();
      if (wait > 0) { timer = env.schedule(pump, wait); return; }
      const job = q.shift();
      last = env.now();
      job();
      if (q.length) timer = env.schedule(pump, gap);
    }
    return {
      push(job) { q.push(job); if (timer == null) pump(); },
      pending: () => q.length,
      clear() { q.length = 0; if (timer != null) { env.cancel(timer); timer = null; } }
    };
  }

  // Decisions and evidence in, paced visual events out. The first sight of a
  // node is the only light it ever gets in a search; a repeat is not a light.
  // The first decision after evidence begins a new search and clears the last.
  function createPathLights(env) {
    const pacer = createPacer(env);
    const lit = new Map();
    let evidenceSeen = false, run = 0;
    const keyOf = d => {
      const id = String(d.node_id == null ? '' : d.node_id);
      if (env.has && env.has(id)) return id;
      if (/^s:/.test(id) && d.doc_id != null) return 'd:' + d.doc_id;   // a section not drawn stands as its document
      return id;
    };
    return {
      onDecision(d) {
        d = d || {};
        if (evidenceSeen) { evidenceSeen = false; run++; lit.clear(); pacer.clear(); env.emit({ type: 'reset' }); }
        const key = keyOf(d);
        if (!key || lit.has(key)) return;
        const mine = run;
        lit.set(key, true);
        const c = confidence(d.p);
        pacer.push(() => { if (mine === run) env.emit({ type: 'light', key, p: d.p, word: c.word, radius: c.radius, title: d.title || '' }); });
      },
      onEvidence(e, bestKeys) {
        evidenceSeen = true;
        const mine = run;
        pacer.push(() => { if (mine === run) env.emit({ type: 'settle', keep: bestKeys || [], evidence: (e && e.evidence) || [] }); });
      },
      reset() { run++; lit.clear(); pacer.clear(); evidenceSeen = false; },
      pending: () => pacer.pending()
    };
  }

  // ── the announcement ──
  function announceText(o) {
    o = o || {};
    const n = o.count || 0;
    if (!n) return 'Nothing in your Library matched.';
    const what = 'Found ' + n + (n === 1 ? ' passage' : ' passages');
    if (!o.doc) return what + '.';
    return what + ' in ' + o.doc + (o.heading ? ', section ' + o.heading : '') + '.';
  }

  // ── the reading plane ──
  // Greedy wrap of text to maxW, where measure(text) is its width.
  function wrapToWidth(text, measure, maxW) {
    const out = [];
    String(text == null ? '' : text).split(/\n+/).forEach(par => {
      let line = '';
      par.split(/\s+/).filter(Boolean).forEach(word => {
        const next = line ? line + ' ' + word : word;
        if (line && measure(next) > maxW) { out.push(line); line = word; } else line = next;
      });
      if (line) out.push(line);
    });
    return out.length ? out : [''];
  }
  // Passages to lines, then lines to pages. A line knows its passage, so the
  // cited block can be outlined and paged to.
  function layoutPassages(passages, measure, maxW, perPage) {
    const lines = [];
    (passages || []).forEach((p, pi) => {
      const where = p.t_start != null ? clock(p.t_start) : (p.page ? 'p. ' + p.page : '');
      wrapToWidth(p.text, measure, maxW).forEach((t, k) => lines.push({ text: t, pi, label: k === 0 ? where : '' }));
    });
    const pages = [];
    for (let k = 0; k < lines.length; k += perPage) pages.push(lines.slice(k, k + perPage));
    if (!pages.length) pages.push([]);
    return pages;
  }
  function pageOfPassage(pages, pi) {
    for (let k = 0; k < pages.length; k++) if (pages[k].some(l => l.pi === pi)) return k;
    return 0;
  }
  // The camera distance that puts reading text at `target` px on screen, and the
  // size that text really is. planeW is the plane's width in world units for a
  // canvas canvasW px wide holding fontPx type.
  function readingScale(o) {
    const tv = Math.tan((o.fovDeg || 52) * Math.PI / 360);
    const worldPerPx = o.planeW / o.canvasW;
    const target = o.target || 14;
    const r = (o.fontPx * worldPerPx * o.viewH) / (2 * tv * target);
    const onScreen = o.fontPx * worldPerPx * o.viewH / (2 * tv * r);
    return { r, onScreen, screenPerCanvasPx: onScreen / o.fontPx };
  }
  const READ = { font: 16, lineH: 1.55, measure: 72, pad: 28, gutter: 84, world: 0.01, target: 14 };
  // Size one page of the plane from the type and the measure; paintReadingPage draws it.
  // A cited block gets a 2px outline (in screen px, whatever the plane's scale).
  function readingGeometry(ctx, page, o) {
    const lh = Math.round(READ.font * READ.lineH * 100) / 100;
    const cw = ctx.measureText('0').width || READ.font * 0.6;
    const W = Math.ceil(READ.pad * 2 + READ.gutter + READ.measure * cw);
    const head = o.heading ? lh * 1.7 : 0;
    const H = Math.ceil(READ.pad * 2 + head + page.length * lh + (o.footer ? lh : 0));
    return { W, H, lh, cw, head };
  }
  function paintReadingPage(ctx, page, geom, o) {
    const { W, H, lh, head } = geom;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = o.ground; ctx.fillRect(0, 0, W, H);
    ctx.textBaseline = 'alphabetic';
    let y = READ.pad;
    if (o.heading) {
      ctx.font = '600 ' + (READ.font + 2) + 'px Inter, sans-serif'; ctx.fillStyle = o.text;
      ctx.fillText(o.heading, READ.pad, y + READ.font + 2);
      y += head;
    }
    const boxes = {};
    page.forEach((l, k) => {
      const base = y + k * lh + READ.font;
      if (l.label) { ctx.font = '500 ' + READ.font + 'px JetBrains Mono, monospace'; ctx.fillStyle = o.dim; ctx.fillText(l.label, READ.pad, base); }
      ctx.font = '400 ' + READ.font + 'px Inter, sans-serif'; ctx.fillStyle = o.text;
      ctx.fillText(l.text, READ.pad + READ.gutter, base);
      const b = boxes[l.pi] || (boxes[l.pi] = { y0: base - READ.font, y1: base + lh - READ.font });
      b.y1 = base + lh - READ.font;
    });
    if (o.footer) {
      ctx.font = '500 ' + READ.font + 'px JetBrains Mono, monospace'; ctx.fillStyle = o.dim;
      ctx.fillText(o.footer, READ.pad, H - READ.pad / 2);
    }
    if (o.cite != null && boxes[o.cite]) {
      const b = boxes[o.cite];
      ctx.lineWidth = 2 / Math.max(0.05, o.screenPerCanvasPx || 1);
      ctx.strokeStyle = o.cyan;
      ctx.strokeRect(READ.pad + READ.gutter - 8, b.y0 - 3, W - READ.pad * 2 - READ.gutter + 16, b.y1 - b.y0 + 4);
    }
    return boxes;
  }
  // How many lines fit a stage viewH px tall at the target text size.
  function linesFor(viewH) {
    const lineScreen = READ.lineH * READ.target;
    return Math.max(6, Math.min(40, Math.floor((0.86 * viewH - 70) / lineScreen)));
  }

  // ── camera waypoints folder -> document -> section -> passage ──
  // frame(center, halfW, halfH, theta, phi, margin) is the engine's own framer.
  function focusPath(plan, o, frame) {
    const out = [];
    if (!plan) return out;
    const shelf = plan.shelves.find(s => s.key === o.folder);
    const phi = Math.PI / 2 - 0.06;
    if (shelf && o.levels >= 0) out.push(frame([shelf.cx, shelf.cy, shelf.cz], shelf.w / 2 + 1, shelf.h / 2 + 1, shelf.yaw, phi, 1.06));
    if (plan.fan && o.levels >= 1) {
      const f = plan.fan;
      const wide = Math.max(2.5, f.R * 0.9);
      out.push(frame([f.center.x, f.center.y, f.center.z], wide, 2.4, f.yaw, phi, 1.1));
    }
    if (plan.fan && o.slab != null && o.levels >= 2) {
      const sb = plan.fan.slabs.find(s => s.i === o.slab);
      if (sb) out.push(frame([sb.x, sb.y, sb.z], 2.4, 1.8, plan.fan.yaw, phi, 1.1));
    }
    if (plan.reading && o.reading && o.levels >= 3) {
      const r = plan.reading;
      out.push({ t: [r.x, r.y, r.z], theta: r.yaw, phi: Math.PI / 2, r: o.reading.r });
    }
    return out;
  }

  // ── styles (tokens only) ──
  const CSS = `
.ls-root{position:relative;flex:1 1 auto;min-height:340px;min-width:0;border-radius:10px;overflow:hidden;border:1px solid var(--fr-glass-edge);background:var(--fr-surface);outline:none;font-family:var(--fr-font-body);color:var(--fr-text)}
.ls-root:focus-visible{outline:2px solid var(--fr-cyan);outline-offset:-2px}
.ls-mount{position:absolute;inset:0}
.ls-cap{position:absolute;left:10px;bottom:10px;max-width:calc(100% - 20px);display:flex;flex-direction:column;gap:4px;padding:8px 11px;border-radius:8px;background:var(--fr-glass);border:1px solid var(--fr-glass-edge);backdrop-filter:var(--fr-glass-blur);pointer-events:none}
.ls-cap .ls-path{font-size:var(--fr-text-md);color:var(--fr-label)}
.ls-cap .ls-keys{font-size:var(--fr-text-md);color:var(--fr-dim)}
.ls-tip{position:fixed;z-index:50;pointer-events:none;max-width:280px;padding:5px 9px;border-radius:6px;background:var(--fr-glass);border:1px solid var(--fr-glass-edge);font-size:var(--fr-text-md);color:var(--fr-text)}
.ls-msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:20px;text-align:center;color:var(--fr-dim);font-size:var(--fr-text-base)}
.ls-live{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
`;
  function ensureStyles() {
    if (typeof document === 'undefined' || document.getElementById('library-shelves-styles')) return;
    const s = document.createElement('style'); s.id = 'library-shelves-styles'; s.textContent = CSS; document.head.appendChild(s);
  }

  // ── the component ──
  function LibraryShelves3D(props) {
    ensureStyles();
    const { nodes, status } = props || {};
    const mountRef = useRef(null), boxRef = useRef(null), coreRef = useRef(null);
    const [noGL, setNoGL] = useState(false);
    const [live, setLive] = useState('');
    const [hover, setHover] = useState(null);
    const [cap, setCap] = useState({ path: '', note: '' });
    const propsRef = useRef(props); propsRef.current = props;

    useEffect(() => {
      const F = window.Friday3D, THREE = window.THREE;
      if (!F || !THREE || !mountRef.current) { setNoGL(true); return undefined; }
      const core = createCore(F, THREE, mountRef.current, {
        props: () => propsRef.current, setLive, setHover, setCap, focusBox: () => boxRef.current && boxRef.current.focus({ preventScroll: true })
      });
      if (!core) { setNoGL(true); return undefined; }
      coreRef.current = core;
      return () => { core.dispose(); coreRef.current = null; };
    }, []);
    useEffect(() => { if (coreRef.current) coreRef.current.setNodes(nodes || []); }, [nodes]);
    const reading = status && status.reading > 0;

    return h('div', {
      className: 'ls-root', ref: boxRef, tabIndex: 0, role: 'application', 'aria-roledescription': '3D shelves',
      'aria-label': 'Your Library on shelves. Arrow keys move between shelves, documents and sections; Enter opens; Escape goes back one level.',
      onKeyDown: e => { if (coreRef.current && coreRef.current.key(e)) { e.preventDefault(); e.stopPropagation(); } },
      onPointerDownCapture: () => { if (coreRef.current) coreRef.current.skip(); }
    },
      h('div', { className: 'ls-mount', ref: mountRef }),
      noGL && h('div', { className: 'ls-msg' }, 'The 3D view is unavailable in this browser. Your documents are listed beside it.'),
      !noGL && (!nodes || !nodes.length) && h('div', { className: 'ls-msg' }, 'No documents yet.'),
      h('div', { className: 'ls-live', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' }, live),
      !noGL && h('div', { className: 'ls-cap' },
        h('div', { className: 'ls-path' }, cap.path || 'Library'),
        reading ? h('div', { className: 'ls-keys' }, 'Still reading ' + status.reading + ' documents; shelves fill as they finish.') : null,
        h('div', { className: 'ls-keys' }, 'Arrows move · Enter opens · Esc back' + (cap.note ? ' · ' + cap.note : ''))),
      hover && h('div', { className: 'ls-tip', style: { left: hover.x + 14, top: hover.y + 12 } }, hover.text));
  }

  // The imperative half: engine, data, lights, reading plane. Returns null when
  // the engine cannot start (no WebGL).
  function createCore(F, THREE, mount, ui) {
    const st = {
      nodes: [], byId: new Map(), sections: {}, passages: {}, openDoc: null, openSec: null, sel: null,
      items: [], idx: new Map(), plan: null, evi: [], eviIdx: 0, focusSeq: 0, page: 0, pages: null, cite: null, dazzle: 'full', disposed: false
    };
    let eng;
    try {
      eng = F.createEngine(mount, {
        onHover: (it, p) => ui.setHover(it && p ? { text: it.name, x: p.x, y: p.y } : null),
        onPick: (i, activate) => pick(i, activate),
        onInteract: () => { st.home = false; ui.focusBox(); }
      });
    } catch (_) { return null; }
    window.__libraryShelves3D = eng;
    eng.setIntro('rise');
    eng.setView('shelves');

    // Until the camera is moved, the home frame follows the stage's size.
    st.home = true;
    let ro = null;
    if (typeof ResizeObserver !== 'undefined') { ro = new ResizeObserver(() => { if (st.home && !st.disposed && mount.clientWidth > 80 && mount.clientHeight > 80) eng.reframe(); }); ro.observe(mount); }

    const cyan = () => tokenColor('--fr-cyan', 'cyan');
    F.registerCats({
      'lib:folder': { color: tokenColor('--fr-violet', 'violet'), label: 'Folders' },
      'lib:document': { color: tokenColor('--fr-cyan', 'cyan'), label: 'Documents' },
      'lib:section': { color: tokenColor('--fr-cat-blue', 'cyan'), label: 'Sections' }
    });

    // ── items: the folders and documents always; sections only for the opened document ──
    function buildList() {
      const out = [];
      const add = (n, extra) => {
        const i = out.length;
        out.push(Object.assign({ i, rel: n.id, name: n.title || '(untitled)', dir: false, size: 0, mtime: 0, ext: '', parent: -1, depth: 1, kids: [], strip: n.title || '', node: n }, extra));
      };
      st.nodes.filter(n => n.kind === 'folder').forEach(n => add(n, {
        cat: 'lib:folder', card: { title: n.title, sub: (n.documents || 0) + ' documents', badge: 'Folder' }, lib: { kind: 'folder', key: n.id }
      }));
      st.nodes.filter(n => n.kind === 'document').forEach(n => add(n, {
        cat: 'lib:document', card: { title: n.title, sub: n.pages ? n.pages + ' pages' : '', badge: n.ext || '' }, lib: { kind: 'document', key: n.id, folder: n.parent || '' }
      }));
      if (st.openDoc && st.sections[st.openDoc]) {
        st.sections[st.openDoc].forEach((s, k) => add(s, {
          cat: 'lib:section', name: s.title || '(untitled)', strip: ' ', card: { title: '', sub: '', badge: '' },
          lib: { kind: 'section', key: s.id, doc: st.openDoc, seq: k, label: s.title || '' }
        }));
      }
      return out;
    }
    function rebuild() {
      st.items = buildList();
      st.idx = new Map(st.items.map((it, i) => [it.rel, i]));
      eng.setData(st.items, 'library', '');
      st.plan = eng.getPlan();
      lightsRefresh();
      caption();
    }
    function setNodes(nodes) {
      st.nodes = nodes;
      st.byId = new Map(nodes.map(n => [n.id, n]));
      if (st.openDoc && !st.byId.has(st.openDoc)) { st.openDoc = null; st.openSec = null; hideReading(); }
      rebuild();
      if (!st.consumed && nodes.length) {
        st.consumed = true;
        const f = window.__libraryFocus;
        if (f && f.doc) { window.__libraryFocus = null; focusPassage(f); }
      }
    }

    // ── data for the opened document and section ──
    const CAP_SECTIONS = 60;
    function capSections(list, need) {
      if (list.length <= CAP_SECTIONS) return list;
      const levels = Array.from(new Set(list.map(s => s.level || 1))).sort((a, b) => a - b);
      let keep = list;
      for (const lv of levels) { keep = list.filter(s => (s.level || 1) <= lv || s.id === need); if (keep.length <= CAP_SECTIONS) break; }
      return keep.length > CAP_SECTIONS ? keep.slice(0, CAP_SECTIONS) : keep;
    }
    function loadSections(docKey, need) {
      if (st.sections[docKey]) return Promise.resolve(st.sections[docKey]);
      return getJSON('/api/library/tree?node=' + encodeURIComponent(docKey) + '&depth=3').then(d => {
        const all = ((d && d.nodes) || []).filter(n => n.kind === 'section');
        const hasPages = all.every(s => typeof s.page_from === 'number');
        const ordered = all.map((s, k) => [s, k]).sort((a, b) => (hasPages ? a[0].page_from - b[0].page_from : 0) || a[1] - b[1]).map(x => x[0]);
        st.sections[docKey] = capSections(ordered, need);
        return st.sections[docKey];
      }).catch(() => { st.sections[docKey] = []; return []; });
    }
    function loadPassages(secKey) {
      if (st.passages[secKey]) return Promise.resolve(st.passages[secKey]);
      return getJSON('/api/library/section/' + secKey.slice(2)).then(d => {
        const s = (d && d.section) || {};
        st.passages[secKey] = { heading: s.heading || '', passages: s.passages || [] };
        return st.passages[secKey];
      }).catch(() => ({ heading: '', passages: [] }));
    }

    // ── camera ──
    const frame = (c, hw, hh, th, ph, m) => eng.frame(c, hw, hh, th, ph, m);
    const docFolder = key => { const n = st.byId.get(key); return n ? n.parent || '' : ''; };
    function fly(path, ms) { if (path.length) { st.home = false; eng.flyTo(path, ms); } }
    function readingCam() {
      const cs = eng.camState();
      return { r: readingScale({ fovDeg: cs.fov, planeW: reading.Wp || 7, canvasW: reading.Wc || 700, fontPx: READ.font, viewH: cs.h, target: READ.target }).r };
    }

    // ── opening and backing out ──
    function openDocument(key, need) {
      if (st.openDoc === key) return Promise.resolve();
      st.openDoc = key; st.openSec = null; hideReading();
      return loadSections(key, need).then(() => { if (st.openDoc !== key || st.disposed) return; rebuild(); });
    }
    function closeDocument() { st.openDoc = null; st.openSec = null; hideReading(); rebuild(); }
    function openSection(secKey, cite) {
      st.openSec = secKey; st.cite = cite == null ? null : cite;
      return loadPassages(secKey).then(d => { if (st.openSec !== secKey || st.disposed) return null; showReading(secKey, d); return d; });
    }
    function select(id) {
      st.sel = id;
      const i = st.idx.get(id);
      eng.select(i == null ? -1 : i, false);
      caption();
    }
    function pick(i, activate) {
      const it = st.items[i];
      if (!it) { st.sel = null; eng.select(-1, false); caption(); return; }
      activateNode(it.node, activate);
    }
    // Choosing a document opens it in place; Enter or a double click on an open
    // one is the Reader. A section shows its passages.
    function activateNode(n, openReader) {
      select(n.id);
      if (n.kind === 'folder') {
        const s = st.plan && st.plan.shelves.find(x => x.key === n.id);
        if (s) fly([frame([s.cx, s.cy, s.cz], s.w / 2 + 1, s.h / 2 + 1, s.yaw, Math.PI / 2 - 0.06, 1.06)], 800);
      } else if (n.kind === 'document') {
        if (st.openDoc === n.id && openReader) { propsOpen(n); return; }
        openDocument(n.id).then(() => { flyToLevel(n.id, 1); announceOpen(n); });
      } else if (n.kind === 'section') {
        openSection(n.id).then(d => { if (d) flyToLevel(st.openDoc, 3, st.idx.get(n.id)); });
      }
    }
    function flyToLevel(docKey, levels, slab) {
      st.plan = eng.getPlan();
      if (!st.plan) return;
      const rc = st.plan.reading && levels >= 3 ? readingCam() : null;
      fly(focusPath(st.plan, { folder: docFolder(docKey), levels: Math.min(levels, 3), slab, reading: rc }, frame).slice(-1), 800);
    }
    function propsOpen(n) {
      const p = ui.props();
      if (p && p.onOpen) p.onOpen(n);
    }
    function back() {
      st.home = false;
      if (st.openSec) { st.openSec = null; hideReading(); const k = st.openDoc; select(k); flyToLevel(k, 1); return true; }
      if (st.openDoc) { const k = st.openDoc; closeDocument(); select(k); const s = st.plan && st.plan.shelves.find(x => x.key === docFolder(k)); if (s) fly([frame([s.cx, s.cy, s.cz], s.w / 2 + 1, s.h / 2 + 1, s.yaw, Math.PI / 2 - 0.06, 1.06)], 800); return true; }
      if (st.sel) { st.sel = null; eng.select(-1, false); eng.reframe(true); caption(); return true; }
      return false;
    }

    // ── focus a cited passage: folder -> document -> section -> passage, one flight ──
    function focusPassage(t) {
      const seq = ++st.focusSeq;
      const docKey = 'd:' + t.doc;
      if (!st.byId.has(docKey)) return Promise.resolve(false);
      const need = t.section != null ? 's:' + t.section : null;
      return openDocument(docKey, need).then(() => {
        if (seq !== st.focusSeq || st.disposed) return false;
        const secs = st.sections[docKey] || [];
        let sec = need ? secs.find(s => s.id === need) : null;
        if (!sec && t.page) sec = secs.find(s => typeof s.page_from === 'number' && s.page_from <= t.page && t.page <= (s.page_to == null ? s.page_from : s.page_to));
        if (!sec) sec = secs[0] || null;
        const go = sec ? openSection(sec.id, t.block) : Promise.resolve(null);
        return go.then(d => {
          if (seq !== st.focusSeq || st.disposed) return false;
          st.plan = eng.getPlan();
          select(sec ? sec.id : docKey);
          const levels = sec && d ? 3 : 1;
          const rc = levels >= 3 && st.plan && st.plan.reading ? readingCam() : null;
          fly(focusPath(st.plan, { folder: docFolder(docKey), levels, slab: sec ? st.idx.get(sec.id) : null, reading: rc }, frame), 1300);
          return { doc: st.byId.get(docKey), sec, heading: d && d.heading || (sec && sec.title) || '' };
        });
      });
    }
    function skip() { eng.skipFly(); if (eng.isAnimating()) eng.skipFx(); }

    // ── the reading plane ──
    const reading = { mesh: null, tex: null, canvas: null, Wp: 0, Wc: 0, pages: null, page: 0 };
    function showReading(secKey, d) {
      const cs = eng.camState();
      reading.canvas = reading.canvas || document.createElement('canvas');
      const ctx = reading.canvas.getContext('2d');
      ctx.font = '400 ' + READ.font + 'px Inter, sans-serif';
      const cw = ctx.measureText('0').width || READ.font * 0.6;
      const maxW = READ.measure * cw;
      const measure = s => ctx.measureText(s).width;
      reading.pages = layoutPassages(d.passages, measure, maxW, linesFor(cs.h));
      reading.heading = d.heading; reading.passages = d.passages;
      const cited = st.cite == null ? -1 : d.passages.findIndex(p => String(p.id) === String(st.cite) || String(p.block) === String(st.cite));
      reading.cited = cited >= 0 ? cited : null;
      reading.page = reading.cited != null ? pageOfPassage(reading.pages, reading.cited) : 0;
      drawPage();
      st.plan = eng.getPlan();
      placeReading();
    }
    function drawPage() {
      if (!reading.pages) return;
      const ctx = reading.canvas.getContext('2d');
      ctx.font = '400 ' + READ.font + 'px Inter, sans-serif';
      const page = reading.pages[reading.page] || [];
      const o = { heading: reading.heading, footer: reading.pages.length > 1 ? 'page ' + (reading.page + 1) + ' of ' + reading.pages.length : '' };
      const geom = readingGeometry(ctx, page, o);
      reading.canvas.width = geom.W; reading.canvas.height = geom.H;
      ctx.font = '400 ' + READ.font + 'px Inter, sans-serif';
      reading.Wc = geom.W; reading.Wp = geom.W * READ.world;
      const cs = eng.camState();
      const sc = readingScale({ fovDeg: cs.fov, planeW: reading.Wp, canvasW: geom.W, fontPx: READ.font, viewH: cs.h, target: READ.target });
      paintReadingPage(ctx, page, geom, Object.assign(o, {
        ground: cssColor('--fr-surface', 'ink'), text: cssColor('--fr-text', 'nebula'), dim: cssColor('--fr-dim', 'nebula'), cyan: cssColor('--fr-cyan', 'cyan'),
        cite: reading.cited, screenPerCanvasPx: sc.screenPerCanvasPx
      }));
      if (reading.tex) { reading.tex.needsUpdate = true; }
      reading.geom = geom;
      eng.invalidate();
    }
    function placeReading() {
      const r = st.plan && st.plan.reading;
      if (!r) return;
      const geom = reading.geom, Wp = geom.W * READ.world, Hp = geom.H * READ.world;
      if (reading.mesh) { eng.overlay.remove(reading.mesh); reading.mesh.geometry.dispose(); }
      if (!reading.tex) { reading.tex = new THREE.CanvasTexture(reading.canvas); reading.tex.minFilter = THREE.LinearFilter; reading.tex.anisotropy = 4; }
      else reading.tex.needsUpdate = true;
      if (!reading.mat) reading.mat = new THREE.MeshBasicMaterial({ map: reading.tex, side: THREE.DoubleSide, fog: false });
      reading.mesh = new THREE.Mesh(new THREE.PlaneGeometry(Wp, Hp), reading.mat);
      reading.mesh.position.set(r.x, r.y, r.z); reading.mesh.rotation.y = r.yaw; reading.mesh.renderOrder = 5;
      eng.overlay.add(reading.mesh);
      eng.invalidate();
    }
    function hideReading() {
      if (reading.mesh) { eng.overlay.remove(reading.mesh); reading.mesh.geometry.dispose(); reading.mesh = null; }
      reading.pages = null; reading.cited = null; st.cite = null;
      eng.invalidate();
    }
    function turnPage(d) {
      if (!reading.pages || reading.pages.length < 2) return false;
      const k = Math.max(0, Math.min(reading.pages.length - 1, reading.page + d));
      if (k === reading.page) return true;
      reading.page = k; drawPage(); placeReading();
      return true;
    }

    // ── lights: the path of a search, paced ──
    const lights = new Map();           // key -> {sprite, line, parent, radius, p, dim, from, to, t0}
    const tags = [];                    // numbered evidence tags
    const Y = new THREE.Vector3(0, 1, 0), tv = new THREE.Vector3(), tq = new THREE.Quaternion();
    const unitCyl = new THREE.CylinderGeometry(1, 1, 1, 8, 1, true);
    const pacerEnv = { now: () => performance.now(), schedule: (f, ms) => setTimeout(f, ms), cancel: id => clearTimeout(id), has: id => st.idx.has(id) };
    const path = createPathLights(Object.assign({}, pacerEnv, { emit: onLightEvent }));
    const parentOf = key => {
      const it = st.items[st.idx.get(key)];
      if (!it) return null;
      if (it.lib.kind === 'document') return it.lib.folder && st.idx.has(it.lib.folder) ? it.lib.folder : null;
      if (it.lib.kind === 'section') return it.lib.doc;
      return null;
    };
    const RAMP = 400;
    function setTarget(L, sprite, line) {
      const now = performance.now();
      L.from = { s: L.sprite ? L.sprite.material.opacity : 0, l: L.line ? L.line.material.opacity : 0 };
      L.to = { s: sprite, l: line }; L.t0 = now;
      if (eng.isReduced()) { if (L.sprite) L.sprite.material.opacity = sprite; if (L.line) L.line.material.opacity = line; L.to = null; eng.invalidate(); }
    }
    function lightOn(key, radius, p) {
      if (!st.idx.has(key) || lights.has(key)) return;
      const dz = eng.dazzle();
      const L = { key, radius, p, parent: parentOf(key), dim: false, sprite: null, line: null };
      if (dz > 0) {
        L.sprite = eng.glowSprite(cyan()); L.sprite.visible = true; L.sprite.material.opacity = 0; L.sprite.renderOrder = 4; eng.overlay.add(L.sprite);
      }
      if (L.parent) {
        L.line = new THREE.Mesh(unitCyl, new THREE.MeshBasicMaterial({ color: cyan(), transparent: true, opacity: 0, depthWrite: false, fog: false }));
        L.line.renderOrder = 4; eng.overlay.add(L.line);
      }
      lights.set(key, L);
      setTarget(L, 0.5 * dz, 0.9);
      eng.invalidate();
    }
    function lightsRefresh() { eng.invalidate(); }
    function onLightEvent(ev) {
      if (st.disposed) return;
      if (ev.type === 'reset') { clearLights(); return; }
      if (ev.type === 'light') { lightOn(ev.key, ev.radius, ev.p); return; }
      if (ev.type === 'settle') { settle(ev); }
    }
    function clearLights() {
      lights.forEach(L => { [L.sprite, L.line].forEach(o => { if (o) { eng.overlay.remove(o); if (o.material) o.material.dispose(); } }); });
      lights.clear();
      clearTags();
      st.evi = []; st.eviIdx = 0;
      eng.invalidate();
    }
    function clearTags() { tags.splice(0).forEach(t => { eng.overlay.remove(t.sprite); t.sprite.material.map.dispose(); t.sprite.material.dispose(); }); }
    function pathKeys(ev) {
      const keys = [];
      if (!ev) return keys;
      const dk = 'd:' + ev.doc_id;
      const f = docFolder(dk);
      if (f) keys.push(f);
      keys.push(dk);
      if (ev.section_id != null) keys.push('s:' + ev.section_id);
      return keys;
    }
    // One settle: parked routes go faint, the other passages get numbered tags,
    // ONE flight frames the best passage. The data is already in the list beside it.
    function settle(ev) {
      const list = ev.evidence || [];
      if (!list.length) { ui.setLive(announceText({ count: 0 })); return; }
      const keep = new Set(ev.keep);
      lights.forEach(L => { if (!keep.has(L.key)) { L.dim = true; setTarget(L, 0.14 * eng.dazzle(), 0.3); } });
      const best = list.reduce((a, b) => ((b.score || 0) > (a.score || 0) ? b : a), list[0]);
      st.evi = [best].concat(list.filter(e => e !== best));
      st.eviIdx = 0;
      makeTags(st.evi.slice(1));
      goEvidence(0, true);
    }
    function goEvidence(k, announce) {
      const e = st.evi[k];
      if (!e) return;
      focusPassage({ doc: e.doc_id, block: e.block_id, page: e.page, section: e.section_id }).then(r => {
        if (!announce || !r) return;
        const n = st.evi.length;
        ui.setLive(announceText({ count: n, doc: r.doc && r.doc.title, heading: r.heading }));
      });
    }
    function stepEvidence(d) {
      if (!st.evi.length) return false;
      st.eviIdx = (st.eviIdx + d + st.evi.length) % st.evi.length;
      goEvidence(st.eviIdx, false);
      return true;
    }
    function makeTags(others) {
      clearTags();
      const perDoc = {};
      others.forEach((e, k) => {
        const c = document.createElement('canvas'); c.width = c.height = 96;
        const x = c.getContext('2d');
        x.fillStyle = cssColor('--fr-surface', 'ink'); x.beginPath(); x.arc(48, 48, 42, 0, Math.PI * 2); x.fill();
        x.lineWidth = 5; x.strokeStyle = cssColor('--fr-cyan', 'cyan'); x.stroke();
        x.fillStyle = cssColor('--fr-text', 'nebula'); x.font = '700 44px JetBrains Mono, monospace'; x.textAlign = 'center'; x.textBaseline = 'middle';
        x.fillText(String(k + 1), 48, 50);
        const tex = new THREE.CanvasTexture(c);
        const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, fog: false }));
        sp.scale.set(0.6, 0.6, 1); eng.overlay.add(sp);
        const key = 'd:' + e.doc_id;
        const slot = perDoc[key] = (perDoc[key] || 0) + 1;
        tags.push({ sprite: sp, key, slot });
      });
      eng.invalidate();
    }

    // The frame hook: positions follow the cards; opacities ramp; nothing else moves.
    const unhook = eng.onFrame(now => {
      let moving = false;
      lights.forEach(L => {
        const i = st.idx.get(L.key);
        const p = i == null ? null : eng.itemPos(i);
        if (!p) { if (L.sprite) L.sprite.visible = false; if (L.line) L.line.visible = false; return; }
        if (L.sprite) {
          const sz = eng.itemSize(i) || [1, 1];
          L.sprite.position.set(p[0], p[1], p[2]); L.sprite.scale.setScalar(Math.max(sz[0], sz[1]) * 2.6); L.sprite.visible = true;
        }
        if (L.line) {
          const pi = st.idx.get(L.parent), pp = pi == null ? null : eng.itemPos(pi);
          if (pp) {
            const sp = segmentSpec(pp, p, L.radius);
            L.line.position.set(sp.mid[0], sp.mid[1], sp.mid[2]);
            tq.setFromUnitVectors(Y, tv.set(sp.dir[0], sp.dir[1], sp.dir[2]));
            L.line.quaternion.copy(tq); L.line.scale.set(sp.radius, sp.len, sp.radius); L.line.visible = true;
          } else L.line.visible = false;
        }
        if (L.to) {
          const k = Math.min(1, (now - L.t0) / RAMP);
          if (L.sprite) L.sprite.material.opacity = L.from.s + (L.to.s - L.from.s) * k;
          if (L.line) L.line.material.opacity = L.from.l + (L.to.l - L.from.l) * k;
          if (k >= 1) L.to = null; else moving = true;
        }
      });
      tags.forEach(t => {
        const i = st.idx.get(t.key), p = i == null ? null : eng.itemPos(i);
        if (!p) { t.sprite.visible = false; return; }
        const sz = eng.itemSize(i) || [1, 1];
        t.sprite.position.set(p[0] + (t.slot - 1) * 0.7 - 0.2, p[1] + sz[1] / 2 + 0.45, p[2] + 0.3); t.sprite.visible = true;
      });
      return moving;
    });

    // ── window events from the workspace ──
    const onDecision = e => path.onDecision(e.detail);
    const onEvidence = e => {
      const list = (e.detail && e.detail.evidence) || [];
      const best = list.reduce((a, b) => (!a || (b.score || 0) > (a.score || 0) ? b : a), null);
      path.onEvidence(e.detail, pathKeys(best));
    };
    const onFocus = e => { const d = e.detail; if (d && d.doc) focusPassage(d); };
    window.addEventListener('friday-library-decision', onDecision);
    window.addEventListener('friday-library-evidence', onEvidence);
    window.addEventListener('friday-library-focus', onFocus);

    // ── dazzle ──
    const setDazzle = v => { st.dazzle = v; eng.setDazzle(v); if (v === 'off') lights.forEach(L => { if (L.sprite) { eng.overlay.remove(L.sprite); L.sprite = null; } }); };
    const onDz = e => e && e.detail && setDazzle(e.detail);
    window.addEventListener('friday-dazzle', onDz);
    api('/api/settings').then(r => r.json()).then(d => { const v = ((d && (d.settings || d)) || {}).studio_dazzle; if (v) setDazzle(v); }).catch(() => {});

    // ── caption ──
    function caption() {
      const parts = [];
      const sel = st.sel && st.byId.get(st.sel);
      const secs = st.openDoc && st.sections[st.openDoc];
      if (st.openDoc) {
        const d = st.byId.get(st.openDoc);
        if (d && d.parent && st.byId.get(d.parent)) parts.push(st.byId.get(d.parent).title);
        if (d) parts.push(d.title);
        const s = st.openSec && secs && secs.find(x => x.id === st.openSec);
        if (s) parts.push(s.title);
      } else if (sel) {
        if (sel.parent && st.byId.get(sel.parent)) parts.push(st.byId.get(sel.parent).title);
        parts.push(sel.title);
      }
      ui.setCap({ path: parts.join('  ›  '), note: reading.pages && reading.pages.length > 1 ? 'PgUp / PgDn turn pages' : (st.evi.length > 1 ? '↑ ↓ step through passages' : '') });
    }
    function announceOpen(n) {
      const secs = st.sections[n.id] || [];
      ui.setLive('Opened ' + n.title + ', ' + secs.length + (secs.length === 1 ? ' section' : ' sections') + '.');
    }

    // ── keys ──
    function nodeAt(i) { const it = st.items[i]; return it ? it.node : null; }
    function key(e) {
      skip();
      const k = e.key;
      if (e.ctrlKey || e.metaKey || e.altKey) return false;
      const selI = st.sel != null && st.idx.has(st.sel) ? st.idx.get(st.sel) : -1;
      if (k === 'ArrowUp' || k === 'ArrowDown') {
        if (st.evi.length > 1) return stepEvidence(k === 'ArrowDown' ? 1 : -1);
        return moveSel(eng.nav(selI, k === 'ArrowUp' ? 'up' : 'down'));
      }
      if (k === 'ArrowLeft' || k === 'ArrowRight') {
        const cur = nodeAt(selI);
        if (cur && cur.kind === 'section' && st.openDoc) {
          const secs = st.sections[st.openDoc], at = secs.findIndex(s => s.id === cur.id);
          const nx = secs[Math.max(0, Math.min(secs.length - 1, at + (k === 'ArrowRight' ? 1 : -1)))];
          if (nx) { select(nx.id); if (st.openSec) openSection(nx.id).then(d => { if (d) flyToLevel(st.openDoc, 3, st.idx.get(nx.id)); }); }
          return true;
        }
        return moveSel(eng.nav(selI, k === 'ArrowLeft' ? 'left' : 'right'));
      }
      if (k === 'Enter') {
        const cur = nodeAt(selI);
        if (!cur) return moveSel(eng.nav(-1, 'right'));
        if (cur.kind === 'section' && st.openSec === cur.id) {
          const doc = st.byId.get(st.openDoc);
          if (doc) propsOpen(Object.assign({}, doc, { doc: Number(doc.id.slice(2)), block: st.cite != null ? st.cite : undefined, section: cur.id }));
          return true;
        }
        if (cur.kind === 'document' && st.openDoc === cur.id) { propsOpen(cur); return true; }
        activateNode(cur, false);
        return true;
      }
      if (k === 'Escape') return back();
      if (k === 'PageDown') return turnPage(1);
      if (k === 'PageUp') return turnPage(-1);
      if (k === 'r' || k === 'R') { st.home = true; eng.reframe(true); return true; }
      return false;
    }
    function moveSel(i) {
      if (i == null || i < 0) return true;
      const n = nodeAt(i);
      if (n) select(n.id);
      return true;
    }

    function dispose() {
      st.disposed = true;
      path.reset();
      window.removeEventListener('friday-library-decision', onDecision);
      window.removeEventListener('friday-library-evidence', onEvidence);
      window.removeEventListener('friday-library-focus', onFocus);
      window.removeEventListener('friday-dazzle', onDz);
      unhook();
      if (ro) ro.disconnect();
      clearLights(); hideReading();
      if (reading.tex) reading.tex.dispose();
      if (reading.mat) reading.mat.dispose();
      unitCyl.dispose();
      if (window.__libraryShelves3D === eng) window.__libraryShelves3D = null;
      eng.dispose();
    }
    return { setNodes, key, skip, dispose, focusPassage, litCount: () => lights.size, _state: st };
  }

  window.LibraryShelves3D = LibraryShelves3D;
  LibraryShelves3D.__internals = {
    confidence, segmentSpec, createPacer, createPathLights, announceText, wrapToWidth, layoutPassages, pageOfPassage,
    readingScale, readingGeometry, paintReadingPage, linesFor, focusPath, parseColor, createCore, READ, MIN_GAP
  };
})();
