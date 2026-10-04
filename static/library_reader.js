/* The Library's Reader: the exact page and paragraph a footnote points at.
 *
 * What it shows is pixels and plain text only:
 *   - a PDF page is an image the server rendered (a blob: URL made here), with
 *     the cited paragraph's box drawn over it in a canvas;
 *   - every other document is shown as text, never as HTML, SVG or script;
 *   - a recording plays from the second the words were said, two seconds early.
 * Nothing in a document can load a remote resource or run: links stay text.
 *
 * Defines window.LibraryReader({block, doc, page, onClose, onStep}).
 */
(function () {
  'use strict';
  if (window.LibraryReader) return;
  const h = React.createElement;
  const { useState, useEffect, useRef } = React;

  function api(url, opts) {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  }
  const json = url => api(url).then(r => r.json().then(d => { d.__http = r.status; return d; }));
  const clock = t => {
    t = Math.max(0, Math.floor(t || 0));
    const m = Math.floor(t / 60), s = t % 60, hh = Math.floor(m / 60);
    return (hh ? hh + ':' + String(m % 60).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
  };
  const cssVar = (name, dflt) => {
    try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || dflt; } catch (_) { return dflt; }
  };

  // A page image as a blob: URL plus the page's size in points (to place the box).
  function usePage(doc, page, enabled) {
    const [state, setState] = useState({ url: null, pt: null, pages: null, error: null });
    useEffect(() => {
      if (!enabled || !doc || !page) { setState({ url: null, pt: null, pages: null, error: null }); return undefined; }
      let live = true, made = null;
      api('/api/library/page/' + doc + '/' + page + '.webp?w=900').then(r => {
        if (!r.ok) return r.json().then(j => { throw new Error(j.error || 'Couldn’t read this page.'); });
        const pt = (r.headers.get('X-Page-Points') || '').split(',').map(Number);
        const pages = Number(r.headers.get('X-Pdf-Pages')) || null;
        return r.blob().then(b => { made = URL.createObjectURL(b); if (live) setState({ url: made, pt: pt.length === 2 ? pt : null, pages, error: null }); });
      }).catch(e => { if (live) setState({ url: null, pt: null, pages: null, error: e.message }); });
      return () => { live = false; if (made) URL.revokeObjectURL(made); };
    }, [doc, page, enabled]);
    return state;
  }

  function PageWithBox({ img, box }) {
    const imgRef = useRef(null), cvRef = useRef(null);
    const draw = () => {
      const im = imgRef.current, cv = cvRef.current;
      if (!im || !cv) return;
      cv.width = im.clientWidth; cv.height = im.clientHeight;
      const g = cv.getContext('2d');
      g.clearRect(0, 0, cv.width, cv.height);
      if (!box || !img.pt) return;
      const sx = cv.width / img.pt[0], sy = cv.height / img.pt[1];
      g.strokeStyle = cssVar('--fr-cyan', '#00d4ff');
      g.lineWidth = 2;
      g.strokeRect(box[0] * sx - 3, box[1] * sy - 3, (box[2] - box[0]) * sx + 6, (box[3] - box[1]) * sy + 6);
    };
    useEffect(draw, [img.url, box]);
    useEffect(() => { window.addEventListener('resize', draw); return () => window.removeEventListener('resize', draw); }, [img.url, box]);
    return h('div', { className: 'lr-page' },
      h('img', { ref: imgRef, src: img.url, alt: 'The page', onLoad: draw }),
      h('canvas', { ref: cvRef, className: 'lr-box', 'aria-hidden': 'true' }));
  }

  // ── Reader v2: pdf.js, vendored and hardened ────────────────────────────────
  // The original file is drawn by pdf.js from this server's own files only: no
  // font or script evaluation (isEvalSupported false), no XFA forms, no annotation
  // layer (so no link in a document is ever followed), and every resource
  // (character maps, fonts, image decoders, the worker) comes from the vendored
  // copy. It adds text selection over the original layout; the page image above
  // stays as the fallback when this cannot load.
  const PDFJS_BASE = '/static/vendor/pdfjs-6.4.299/';
  let pdfjsPromise = null;
  function loadPdfJs() {
    if (!pdfjsPromise) {
      pdfjsPromise = import(PDFJS_BASE + 'legacy/pdf.min.mjs').then(m => {
        m.GlobalWorkerOptions.workerSrc = PDFJS_BASE + 'legacy/pdf.worker.min.js';
        return m;
      }).catch(() => null);
    }
    return pdfjsPromise;
  }
  function usePdfJs() {
    const [m, setM] = useState(null);
    useEffect(() => { let live = true; loadPdfJs().then(x => { if (live) setM(x); }); return () => { live = false; }; }, []);
    return m;
  }
  function pdfOptions(doc) {
    const tok = window.__FRIDAY_API_TOKEN || '';
    return {
      url: '/api/library/raw/' + doc,
      httpHeaders: tok ? { 'X-Friday-Token': tok } : {},
      isEvalSupported: false,
      enableXfa: false,
      useSystemFonts: false,
      cMapUrl: PDFJS_BASE + 'cmaps/',
      cMapPacked: true,
      standardFontDataUrl: PDFJS_BASE + 'standard_fonts/',
      wasmUrl: PDFJS_BASE + 'wasm/',
      iccUrl: PDFJS_BASE + 'iccs/',
      verbosity: 0
    };
  }
  function PdfJsPage({ pdfjs, doc, page, box, onPages, onFail }) {
    const holder = useRef(null), cvRef = useRef(null), textRef = useRef(null), boxRef = useRef(null);
    const [pdf, setPdf] = useState(null);
    useEffect(() => {
      let live = true, task = null;
      setPdf(null);
      try {
        task = pdfjs.getDocument(pdfOptions(doc));
        task.promise.then(p => { if (live) { setPdf(p); onPages && onPages(p.numPages); } }).catch(() => live && onFail && onFail());
      } catch (_) { onFail && onFail(); }
      return () => { live = false; try { task && task.destroy(); } catch (_) {} };
    }, [doc]);
    useEffect(() => {
      if (!pdf) return undefined;
      let live = true, renderTask = null, textLayer = null;
      pdf.getPage(Math.min(Math.max(1, page), pdf.numPages)).then(pg => {
        if (!live) return;
        const base = pg.getViewport({ scale: 1 });
        const width = Math.min(holder.current ? holder.current.clientWidth || 900 : 900, 1000);
        const viewport = pg.getViewport({ scale: width / base.width });
        const cv = cvRef.current, dpr = window.devicePixelRatio || 1;
        cv.width = Math.floor(viewport.width * dpr); cv.height = Math.floor(viewport.height * dpr);
        cv.style.width = viewport.width + 'px'; cv.style.height = viewport.height + 'px';
        const ctx = cv.getContext('2d');
        renderTask = pg.render({ canvasContext: ctx, viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null });
        renderTask.promise.catch(() => {});
        const tl = textRef.current;
        tl.replaceChildren();
        tl.style.width = viewport.width + 'px'; tl.style.height = viewport.height + 'px';
        tl.style.setProperty('--scale-factor', String(viewport.scale));
        tl.style.setProperty('--total-scale-factor', String(viewport.scale));
        try {
          textLayer = new pdfjs.TextLayer({ textContentSource: pg.streamTextContent(), container: tl, viewport });
          textLayer.render().catch(() => {});
        } catch (_) {}
        const bx = boxRef.current;
        bx.width = viewport.width; bx.height = viewport.height;
        const g = bx.getContext('2d');
        g.clearRect(0, 0, bx.width, bx.height);
        if (box) {
          g.strokeStyle = cssVar('--fr-cyan', '#00d4ff'); g.lineWidth = 2;
          const s = viewport.scale;
          g.strokeRect(box[0] * s - 3, box[1] * s - 3, (box[2] - box[0]) * s + 6, (box[3] - box[1]) * s + 6);
        }
      }).catch(() => live && onFail && onFail());
      return () => { live = false; try { renderTask && renderTask.cancel(); } catch (_) {} try { textLayer && textLayer.cancel(); } catch (_) {} };
    }, [pdf, page, box]);
    return h('div', { className: 'lr-page lr-pdfjs', ref: holder },
      h('canvas', { ref: cvRef, 'aria-label': 'The page' }),
      h('div', { ref: textRef, className: 'lr-textlayer' }),
      h('canvas', { ref: boxRef, className: 'lr-box', 'aria-hidden': 'true' }));
  }

  function LibraryReader(props) {
    const { block, doc, page: wantPage, onClose, onStep } = props;
    const [b, setB] = useState(null);
    const [err, setErr] = useState(null);
    const [page, setPage] = useState(wantPage || 1);
    const audioRef = useRef(null);
    useEffect(() => {
      setB(null); setErr(null);
      if (!block) return undefined;
      let live = true;
      json('/api/library/block/' + block).then(d => {
        if (!live) return;
        if (d.status === 'ok') { setB(d.block); if (d.block.page) setPage(d.block.page); }
        else setErr('This source is no longer in your Library.');
      }).catch(() => live && setErr('Couldn’t reach Friday.'));
      return () => { live = false; };
    }, [block]);
    useEffect(() => { if (!block && wantPage) setPage(wantPage); }, [wantPage, block]);
    const docId = b ? b.doc_id : doc;
    const isPdf = b ? b.page_image : !!doc;
    const pdfjs = usePdfJs();
    const [v2Failed, setV2Failed] = useState(false);
    const [v2Pages, setV2Pages] = useState(null);
    const useV2 = !!(pdfjs && isPdf && docId && !v2Failed);
    const img = usePage(docId, page, !!isPdf && !!docId && !useV2);
    useEffect(() => { setV2Failed(false); }, [docId]);
    const timed = b && b.t_start != null;
    useEffect(() => {
      const a = audioRef.current;
      if (a && timed) { try { a.currentTime = Math.max(0, b.t_start - 2); } catch (_) {} }
    }, [b, timed]);
    useEffect(() => {
      const k = e => {
        if (e.key === 'Escape') { e.preventDefault(); onClose && onClose(); }
        else if (e.key === 'ArrowRight' && isPdf) setPage(p => Math.min((useV2 ? v2Pages : img.pages) || p + 1, p + 1));
        else if (e.key === 'ArrowLeft' && isPdf) setPage(p => Math.max(1, p - 1));
        else if (e.key === 'ArrowDown' && onStep) { e.preventDefault(); onStep(1); }
        else if (e.key === 'ArrowUp' && onStep) { e.preventDefault(); onStep(-1); }
      };
      window.addEventListener('keydown', k);
      return () => window.removeEventListener('keydown', k);
    }, [isPdf, img.pages, v2Pages, useV2, onClose, onStep]);

    if (err) return h('div', { className: 'lr-root' }, h('div', { className: 'lr-empty' }, err, h('button', { className: 'btn', onClick: onClose }, 'Back')));
    const where = b ? [b.title, b.page ? 'p. ' + b.page : (timed ? clock(b.t_start) : null)].filter(Boolean).join(' · ') : 'Page ' + page;
    return h('div', { className: 'lr-root' },
      h('div', { className: 'lr-head' },
        h('button', { className: 'btn', onClick: onClose, 'aria-label': 'Back to the Library' }, '‹ Back'),
        h('div', { className: 'lr-where' }, where),
        isPdf && h('div', { className: 'lr-pager' },
          h('button', { className: 'btn', disabled: page <= 1, onClick: () => setPage(p => Math.max(1, p - 1)), 'aria-label': 'Previous page' }, '‹'),
          h('span', { className: 'lr-num' }, page + ((useV2 ? v2Pages : img.pages) ? ' / ' + (useV2 ? v2Pages : img.pages) : '')),
          h('button', { className: 'btn', disabled: (useV2 ? v2Pages : img.pages) ? page >= (useV2 ? v2Pages : img.pages) : false, onClick: () => setPage(p => p + 1), 'aria-label': 'Next page' }, '›'))),
      h('div', { className: 'lr-cols' },
        isPdf && h('div', { className: 'lr-left' },
          useV2 ? h(PdfJsPage, { pdfjs, doc: docId, page, box: b && b.page === page ? b.bbox : null, onPages: setV2Pages, onFail: () => setV2Failed(true) })
            : img.error ? h('div', { className: 'lr-empty' }, img.error)
              : img.url ? h(PageWithBox, { img, box: b && b.page === page ? b.bbox : null })
                : h('div', { className: 'lr-empty' }, 'Reading the page…')),
        h('div', { className: 'lr-right' },
          b && b.section && h('div', { className: 'lr-section' }, b.section),
          timed && b.doc_kind === 'media' && h('audio', { ref: audioRef, controls: true, preload: 'metadata', src: '/api/library/raw/' + b.doc_id, style: { width: '100%' } }),
          b && h('div', { className: 'lr-text', tabIndex: 0 },
            (b.neighbours || []).map(n => h('p', { key: n.id, className: n.current ? 'lr-cur' : 'lr-near' }, n.text))),
          !b && h('div', { className: 'lr-empty' }, isPdf ? 'No paragraph is selected; use the arrows to turn the page.' : 'Nothing selected.'))));
  }

  const CSS = `
.lr-root{display:flex;flex-direction:column;gap:10px;flex:1 1 auto;min-height:0;min-width:0;color:var(--fr-text);font-family:var(--fr-font-body)}
.lr-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.lr-where{font-size:var(--fr-text-md);color:var(--fr-label);font-family:var(--fr-font-mono);flex:1 1 auto}
.lr-pager{display:flex;align-items:center;gap:6px}
.lr-num{font-family:var(--fr-font-mono);font-size:var(--fr-text-sm);color:var(--fr-dim)}
.lr-cols{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:14px;flex:1 1 auto;min-height:0}
.lr-cols>.lr-right:only-child{grid-column:1 / -1}
.lr-left,.lr-right{overflow:auto;min-height:0}
.lr-page{position:relative;display:inline-block;max-width:100%;border:1px solid var(--fr-glass-edge);border-radius:8px;overflow:hidden;background:#fff}
.lr-page img{display:block;max-width:100%;height:auto}
.lr-box{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.lr-pdfjs{position:relative;width:100%;max-width:1000px;background:#fff}
.lr-pdfjs canvas:first-child{display:block}
.lr-textlayer{position:absolute;inset:0;overflow:clip;line-height:1}
.lr-textlayer span,.lr-textlayer br{color:transparent;position:absolute;white-space:pre;cursor:text;transform-origin:0 0}
.lr-textlayer ::selection{background:rgba(0,212,255,.3)}
.lr-section{font-family:var(--fr-font-display);font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);text-transform:uppercase;color:var(--fr-dim);margin-bottom:6px}
.lr-text{max-width:72ch;font-size:var(--fr-text-base);line-height:1.55;user-select:text}
.lr-text p{margin:0 0 10px;white-space:pre-wrap;overflow-wrap:anywhere}
.lr-near{color:var(--fr-dim)}
.lr-cur{color:var(--fr-text);border-left:2px solid var(--fr-cyan);padding-left:10px}
.lr-empty{color:var(--fr-dim);font-size:var(--fr-text-md);padding:12px;display:flex;gap:10px;align-items:center}
@media (max-width:900px){.lr-cols{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){.lr-root *{transition:none!important;animation:none!important}}
`;
  const st = document.createElement('style');
  st.id = 'library-reader-styles';
  st.textContent = CSS;
  document.head.appendChild(st);
  window.LibraryReader = LibraryReader;
  window.__libraryReaderClock = clock;
})();
