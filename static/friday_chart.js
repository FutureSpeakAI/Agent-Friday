/* Friday's chart renderer: rows plus a small spec in, an SVG string out.
 *
 * One renderer, two homes. The artifact panel (static/friday_artifacts.js)
 * draws charts with it live, and a published chart page carries this very
 * file inlined so the page draws itself in the visitor's browser with no
 * library, no network and no telemetry (docs/design/active/vibe-coding-salon.md
 * §4.2, §4.10.1). It depends on nothing: no framework, no DOM access.
 *
 * Spec: { type: bar|line|area|pie|donut|scatter, title?, columns: [..],
 *         rows: [[..]] | [{..}], x?: column, y?: [columns], series?: [{name,
 *         data: [[x, y]]}], stacked?: bool, legend?: bool }
 *
 * Accessible (WCAG 2.2 1.1.1 and 1.4.1; north star 33.1-12): series are told
 * apart without colour (each has its own line dash, marker shape and bar or
 * slice pattern, and the legend draws the same), the SVG is named by a
 * sentence that says what the chart shows, and tableHTML gives the same data
 * as a table.
 *
 * Defines window.FridayChart = { series, renderSVG, describe, tableHTML,
 * legendSwatch, look, PALETTE }.
 */
(function (root) {
  'use strict';
  const PALETTE = ['#00d4ff', '#b794f6', '#00ff80', '#f59e0b', '#ff6dd9', '#4f8cff', '#ffd166', '#9be7ff'];
  const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const isNum = v => typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v)));
  const fmtNum = v => Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : Math.abs(v) >= 1e4 ? (v / 1e3).toFixed(0) + 'k' : (+(+v).toFixed(2)).toString();

  // Told apart without colour: series i has its own dash, marker and fill pattern as well as its own colour.
  const DASHES = ['', '7 3', '2 3', '9 3 2 3', '1 3', '12 4', '5 2 1 2', '3 3'];
  const MARKERS = ['circle', 'square', 'diamond', 'triangle', 'triangle-down', 'cross', 'ring', 'square-open'];
  const PATTERNS = ['solid', 'hatch', 'dots', 'cross-hatch', 'vertical', 'horizontal', 'back-hatch', 'grid'];
  const look = i => ({ color: PALETTE[i % PALETTE.length], dash: DASHES[i % DASHES.length],
    marker: MARKERS[i % MARKERS.length], pattern: PATTERNS[i % PATTERNS.length] });
  let uid = 0;
  const tag = (name, attrs, inner) => '<' + name + ' ' + attrs + (inner ? '>' + inner + '</' + name + '>' : '/>');
  const pts2 = list => 'points="' + list.map(p => p.map(v => +v.toFixed(2)).join(',')).join(' ') + '"';

  function markerSVG(kind, x, y, r, col, inner) {
    const fill = 'fill="' + col + '" stroke="#0b0e14" stroke-width="1"', open = 'fill="#0b0e14" stroke="' + col + '" stroke-width="1.8"';
    switch (kind) {
      case 'square': case 'square-open':
        return tag('rect', 'x="' + (x - r) + '" y="' + (y - r) + '" width="' + 2 * r + '" height="' + 2 * r + '" ' + (kind === 'square' ? fill : open), inner);
      case 'diamond': return tag('polygon', pts2([[x, y - r * 1.35], [x + r * 1.35, y], [x, y + r * 1.35], [x - r * 1.35, y]]) + ' ' + fill, inner);
      case 'triangle': return tag('polygon', pts2([[x, y - r * 1.35], [x + r * 1.2, y + r], [x - r * 1.2, y + r]]) + ' ' + fill, inner);
      case 'triangle-down': return tag('polygon', pts2([[x, y + r * 1.35], [x + r * 1.2, y - r], [x - r * 1.2, y - r]]) + ' ' + fill, inner);
      case 'cross': return tag('path', 'd="M' + (x - r) + ',' + (y - r) + 'L' + (x + r) + ',' + (y + r) + 'M' + (x + r) + ',' + (y - r) + 'L' + (x - r) + ',' + (y + r) +
        '" stroke="' + col + '" stroke-width="2.4" stroke-linecap="round" fill="none"', inner);
      case 'ring': return tag('circle', 'cx="' + x + '" cy="' + y + '" r="' + r + '" ' + open, inner);
      default: return tag('circle', 'cx="' + x + '" cy="' + y + '" r="' + r + '" ' + fill, inner);
    }
  }

  function patternDef(id, kind, col) {
    const ink = 'stroke="#0b0e14" stroke-opacity="0.6" stroke-width="1.6"';
    const hatch = '<path d="M-2,2 L2,-2 M0,8 L8,0 M6,10 L10,6" ' + ink + '/>';
    const back = '<path d="M-2,6 L2,10 M0,0 L8,8 M6,-2 L10,2" ' + ink + '/>';
    const marks = { hatch: hatch, 'back-hatch': back, 'cross-hatch': hatch + back,
      vertical: '<path d="M4,0 L4,8" ' + ink + '/>', horizontal: '<path d="M0,4 L8,4" ' + ink + '/>',
      grid: '<path d="M4,0 L4,8 M0,4 L8,4" ' + ink + '/>',
      dots: '<circle cx="4" cy="4" r="1.7" fill="#0b0e14" fill-opacity="0.6"/>' }[kind] || '';
    return '<pattern id="' + id + '" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="' + col + '"/>' + marks + '</pattern>';
  }

  // The fill for each of n series or slices (a colour, or a pattern over it) and the <defs> the patterns need.
  function paints(n) {
    const id = 'fc' + (++uid) + '-';
    let defs = '';
    const fill = [];
    for (let i = 0; i < n; i++) {
      const L = look(i);
      if (L.pattern === 'solid') fill.push(L.color);
      else { defs += patternDef(id + i, L.pattern, L.color); fill.push('url(#' + id + i + ')'); }
    }
    return { defs: defs ? '<defs>' + defs + '</defs>' : '', fill };
  }

  /** A small SVG for a legend entry that draws what the chart draws: the dash and marker, or the fill pattern. */
  function legendSwatch(l) {
    const L = l || {};
    const open = '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="12" viewBox="0 0 26 12" aria-hidden="true" focusable="false">';
    if (L.kind === 'line' || L.kind === 'area' || L.kind === 'scatter') {
      const line = L.kind === 'scatter' ? '' : '<line x1="1" y1="6" x2="25" y2="6" stroke="' + L.color + '" stroke-width="2"' + (L.dash ? ' stroke-dasharray="' + L.dash + '"' : '') + '/>';
      return open + line + markerSVG(L.marker, 13, 6, 3.2, L.color) + '</svg>';
    }
    const id = 'fcl' + (++uid);
    const fill = L.pattern && L.pattern !== 'solid' ? 'url(#' + id + ')' : L.color;
    return open + (fill !== L.color ? '<defs>' + patternDef(id, L.pattern, L.color) + '</defs>' : '') +
      '<rect x="5" y="1" width="16" height="10" rx="2" fill="' + fill + '"/></svg>';
  }

  function series(spec) {
    spec = spec || {};
    if (Array.isArray(spec.series) && spec.series.length) {
      return { x: null, series: spec.series.map((s, i) => ({ name: s.name || ('series ' + (i + 1)), points: (s.data || []).map(p => Array.isArray(p) ? { x: p[0], y: Number(p[1]) } : { x: p.x, y: Number(p.y) }) })) };
    }
    const cols = (spec.columns || []).map(c => typeof c === 'object' ? c.name : c);
    let rows = spec.rows || [];
    if (rows.length && !Array.isArray(rows[0])) rows = rows.map(r => cols.map(c => r[c]));
    const xi = spec.x != null ? Math.max(0, cols.indexOf(spec.x)) : 0;
    let ys = Array.isArray(spec.y) ? spec.y.map(c => cols.indexOf(c)).filter(i => i >= 0) : (spec.y != null ? [cols.indexOf(spec.y)].filter(i => i >= 0) : []);
    if (!ys.length) ys = cols.map((_, i) => i).filter(i => i !== xi && rows.some(r => isNum(r[i])));
    return { x: cols[xi], series: ys.map(yi => ({ name: cols[yi], points: rows.map(r => ({ x: r[xi], y: isNum(r[yi]) ? Number(r[yi]) : null })) })) };
  }

  // Every x value in the order first seen, across all series (a series given as points may skip some).
  function xValues(S) {
    const seen = new Set(), out = [];
    S.forEach(s => s.points.forEach(p => { const k = String(p.x); if (!seen.has(k)) { seen.add(k); out.push(p.x); } }));
    return out;
  }

  const KIND_WORD = { bar: 'Bar chart', line: 'Line chart', area: 'Area chart', pie: 'Pie chart', donut: 'Donut chart', scatter: 'Scatter chart' };

  /** One or two sentences that say what the chart shows: its kind, its series and where each is lowest and highest. */
  function describe(spec) {
    spec = spec || {};
    const type = String(spec.type || 'bar').toLowerCase();
    const sr = series(spec), S = sr.series.filter(s => s.points.length);
    let out = (KIND_WORD[type] || 'Chart') + (spec.title ? ': ' + spec.title : '') + '.';
    if (!S.length) return out + ' No data.';
    if (type === 'pie' || type === 'donut') {
      const pts = S[0].points.filter(p => p.y != null && p.y > 0), total = pts.reduce((a, p) => a + p.y, 0) || 1;
      const shown = pts.slice(0, 12).map(p => p.x + ' ' + (100 * p.y / total).toFixed(0) + '%');
      return out + ' ' + pts.length + (pts.length === 1 ? ' part: ' : ' parts: ') + shown.join(', ') + (pts.length > 12 ? ', and ' + (pts.length - 12) + ' more' : '') + '.';
    }
    const xs = xValues(S);
    out += ' ' + S.length + ' series' + ' over ' + xs.length + ' ' + (sr.x ? String(sr.x) + ' values' : 'points') +
      (xs.length > 1 ? ', from ' + xs[0] + ' to ' + xs[xs.length - 1] : '') + '.';
    S.slice(0, 8).forEach(s => {
      const ok = s.points.filter(p => p.y != null && isFinite(p.y));
      if (!ok.length) return;
      const lo = ok.reduce((a, p) => p.y < a.y ? p : a), hi = ok.reduce((a, p) => p.y > a.y ? p : a);
      out += ' ' + s.name + ': lowest ' + fmtNum(lo.y) + ' at ' + lo.x + ', highest ' + fmtNum(hi.y) + ' at ' + hi.x + '.';
    });
    if (S.length > 8) out += ' And ' + (S.length - 8) + ' more series.';
    return out;
  }

  /** The chart's data as an HTML table (a text alternative a screen reader can move through cell by cell). */
  function tableHTML(spec) {
    spec = spec || {};
    const sr = series(spec), S = sr.series;
    if (!S.length) return '';
    const pie = /^(pie|donut)$/i.test(String(spec.type || ''));
    const xs = xValues(S);
    const at = S.map(s => { const m = new Map(); s.points.forEach(p => m.set(String(p.x), p.y)); return m; });
    const head = '<tr><th scope="col">' + esc(sr.x || (pie ? 'Part' : 'Point')) + '</th>' + S.map(s => '<th scope="col">' + esc(s.name) + '</th>').join('') + '</tr>';
    const body = xs.map(x => '<tr><th scope="row">' + esc(x) + '</th>' + at.map(m => {
      const v = m.get(String(x));
      return '<td class="num">' + (v == null || (typeof v === 'number' && !isFinite(v)) ? '' : esc(v)) + '</td>';
    }).join('') + '</tr>').join('');
    return '<table class="fc-table"><caption>' + esc(spec.title ? spec.title + ': the data' : 'The data in this chart') + '</caption><thead>' + head + '</thead><tbody>' + body + '</tbody></table>';
  }

  function niceTicks(min, max, n) {
    if (!(max > min)) { max = min + 1; }
    const span = max - min, step0 = span / Math.max(1, n), mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag;
    const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step, out = [];
    for (let v = lo; v <= hi + step / 2; v += step) out.push(+v.toFixed(10));
    return out;
  }

  const FONT = 'Inter, system-ui, sans-serif', MONO = "'JetBrains Mono', monospace";
  const text = (x, y, s, attrs) => '<text x="' + x + '" y="' + y + '" ' + attrs + '>' + esc(s) + '</text>';

  /** Returns { svg: string, legend: [{name, color}], empty: bool }. */
  function renderSVG(spec, W, H) {
    spec = spec || {};
    W = Math.max(240, W || 480); H = Math.max(160, H || 300);
    const type = String(spec.type || 'bar').toLowerCase();
    const S = series(spec).series;
    if (!S.length || !S[0].points.length) return { svg: '', legend: [], empty: true };
    const legend = S.map((s, i) => Object.assign({ name: s.name, kind: type }, look(i)));
    const open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="100%" preserveAspectRatio="' + (type === 'pie' || type === 'donut' ? 'xMidYMid meet' : 'none') + '" role="img" aria-label="' + esc(describe(spec)) + '">';

    if (type === 'pie' || type === 'donut') {
      const pts = S[0].points.filter(p => p.y != null && p.y > 0);
      const total = pts.reduce((a, p) => a + p.y, 0) || 1;
      const cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 14, r0 = type === 'donut' ? R * 0.55 : 0;
      const P0 = paints(pts.length);
      let a0 = -Math.PI / 2, out = open + P0.defs;
      if (spec.title) out += text(W / 2, 16, spec.title, 'fill="#f1f6fb" font-size="12" font-weight="600" text-anchor="middle" font-family="' + FONT + '"');
      pts.forEach((p, i) => {
        const a1 = a0 + 2 * Math.PI * p.y / total, big = a1 - a0 > Math.PI ? 1 : 0;
        const P = (a, r) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
        const [x0, y0] = P(a0, R), [x1, y1] = P(a1, R), [xi0, yi0] = P(a0, r0), [xi1, yi1] = P(a1, r0);
        const d = r0 ? 'M' + x0 + ',' + y0 + ' A' + R + ',' + R + ' 0 ' + big + ' 1 ' + x1 + ',' + y1 + ' L' + xi1 + ',' + yi1 + ' A' + r0 + ',' + r0 + ' 0 ' + big + ' 0 ' + xi0 + ',' + yi0 + ' Z'
          : 'M' + cx + ',' + cy + ' L' + x0 + ',' + y0 + ' A' + R + ',' + R + ' 0 ' + big + ' 1 ' + x1 + ',' + y1 + ' Z';
        const mid = (a0 + a1) / 2, [lx, ly] = P(mid, (R + r0) / 2 + (r0 ? 0 : R * 0.15));
        out += '<path d="' + d + '" fill="' + P0.fill[i] + '" stroke="#0b0e14" stroke-width="1.5"><title>' + esc(p.x + ': ' + fmtNum(p.y) + ' (' + (100 * p.y / total).toFixed(1) + '%)') + '</title></path>';
        if (p.y / total > 0.06) out += text(lx, ly, (100 * p.y / total).toFixed(0) + '%', 'fill="#0b0e14" font-size="10" font-weight="700" text-anchor="middle" dominant-baseline="middle" font-family="' + FONT + '"');
        a0 = a1;
      });
      return { svg: out + '</svg>', legend: pts.map((p, i) => Object.assign({ name: String(p.x), kind: type }, look(i))), empty: false };
    }

    const cats = S[0].points.map(p => p.x);
    const allY = S.flatMap(s => s.points.map(p => p.y)).filter(v => v != null && isFinite(v));
    const stacked = !!spec.stacked && type !== 'scatter';
    let yMin = Math.min(0, ...allY), yMax = Math.max(0, ...allY);
    if (stacked) yMax = Math.max(0, ...cats.map((_, i) => S.reduce((a, s) => a + Math.max(0, s.points[i] ? s.points[i].y || 0 : 0), 0)));
    const ticks = niceTicks(yMin, yMax, 5);
    yMin = ticks[0]; yMax = ticks[ticks.length - 1];
    const padL = 12 + Math.max(...ticks.map(t => fmtNum(t).length)) * 6.5, padR = 12, padT = spec.title ? 26 : 12, padB = 30;
    const iw = W - padL - padR, ih = H - padT - padB;
    const sy = v => padT + ih - (v - yMin) / (yMax - yMin || 1) * ih;
    const numericX = type === 'scatter' && cats.every(isNum);
    const xs = numericX ? cats.map(Number) : null;
    const xMin = xs ? Math.min(...xs) : 0, xMax = xs ? Math.max(...xs) : 1;
    const sx = i => numericX ? padL + (xs[i] - xMin) / (xMax - xMin || 1) * iw : padL + (i + 0.5) * iw / cats.length;
    let out = open;
    if (spec.title) out += text(padL, 16, spec.title, 'fill="#f1f6fb" font-size="12" font-weight="600" font-family="' + FONT + '"');
    ticks.forEach(t => {
      out += '<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + sy(t) + '" y2="' + sy(t) + '" stroke="' + (t === 0 ? 'rgba(255,255,255,0.28)' : 'rgba(255,255,255,0.07)') + '" stroke-width="1"/>';
      out += text(padL - 6, sy(t), fmtNum(t), 'fill="rgba(255,255,255,0.55)" font-size="10" text-anchor="end" dominant-baseline="middle" font-family="' + MONO + '"');
    });
    const every = Math.ceil(cats.length / Math.max(1, Math.floor(iw / 64)));
    cats.forEach((c, i) => { if (i % every === 0) out += text(sx(i), H - padB + 14, String(c).slice(0, 14), 'fill="rgba(255,255,255,0.6)" font-size="10" text-anchor="middle" font-family="' + FONT + '"'); });
    if (type === 'bar') {
      const gw = iw / cats.length, bw = stacked ? gw * 0.62 : (gw * 0.72) / S.length, acc = cats.map(() => 0);
      const P1 = paints(S.length);
      out += P1.defs;
      S.forEach((s, si) => s.points.forEach((p, i) => {
        if (p.y == null) return;
        const x = stacked ? padL + i * gw + (gw - bw) / 2 : padL + i * gw + gw * 0.14 + si * bw;
        const base = stacked ? acc[i] : 0; if (stacked) acc[i] += p.y;
        const y0 = sy(base), y1 = sy(base + p.y);
        out += '<rect x="' + x + '" y="' + Math.min(y0, y1) + '" width="' + Math.max(1, bw - 2) + '" height="' + Math.max(1, Math.abs(y1 - y0)) + '" fill="' + P1.fill[si] + '" rx="2" opacity="0.92"><title>' + esc(s.name + ' · ' + p.x + ': ' + fmtNum(p.y)) + '</title></rect>';
      }));
    } else {
      S.forEach((s, si) => {
        const L = look(si), col = L.color;
        const pts = s.points.map((p, i) => p.y == null ? null : [sx(i), sy(p.y)]);
        const segs = []; let cur = [];
        pts.forEach(p => { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } }); if (cur.length) segs.push(cur);
        if (type !== 'scatter') segs.forEach(seg => { out += '<polyline points="' + seg.map(q => q.join(',')).join(' ') + '" fill="none" stroke="' + col + '" stroke-width="2"' + (L.dash ? ' stroke-dasharray="' + L.dash + '"' : '') + ' stroke-linejoin="round" stroke-linecap="round"/>'; });
        if (type === 'area') segs.forEach(seg => { const b = sy(Math.max(0, yMin)); out += '<polygon points="' + seg.map(q => q.join(',')).concat([seg[seg.length - 1][0] + ',' + b, seg[0][0] + ',' + b]).join(' ') + '" fill="' + col + '" opacity="0.16"/>'; });
        pts.forEach((q, i) => { if (q) out += markerSVG(L.marker, q[0], q[1], type === 'scatter' ? 4 : 3.2, col, '<title>' + esc(s.name + ' · ' + s.points[i].x + ': ' + fmtNum(s.points[i].y)) + '</title>'); });
      });
    }
    return { svg: out + '</svg>', legend: (S.length > 1 || spec.legend) ? legend : [], empty: false };
  }

  root.FridayChart = { series, renderSVG, describe, tableHTML, legendSwatch, look, PALETTE };
})(typeof window !== 'undefined' ? window : globalThis);
