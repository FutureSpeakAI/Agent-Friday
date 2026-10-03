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
 * Defines window.FridayChart = { series, renderSVG, PALETTE }.
 */
(function (root) {
  'use strict';
  const PALETTE = ['#00d4ff', '#b794f6', '#00ff80', '#f59e0b', '#ff6dd9', '#4f8cff', '#ffd166', '#9be7ff'];
  const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const isNum = v => typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v)));
  const fmtNum = v => Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : Math.abs(v) >= 1e4 ? (v / 1e3).toFixed(0) + 'k' : (+(+v).toFixed(2)).toString();

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
    const legend = S.map((s, i) => ({ name: s.name, color: PALETTE[i % PALETTE.length] }));
    const open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="100%" preserveAspectRatio="' + (type === 'pie' || type === 'donut' ? 'xMidYMid meet' : 'none') + '" role="img" aria-label="' + esc(spec.title || 'chart') + '">';

    if (type === 'pie' || type === 'donut') {
      const pts = S[0].points.filter(p => p.y != null && p.y > 0);
      const total = pts.reduce((a, p) => a + p.y, 0) || 1;
      const cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 14, r0 = type === 'donut' ? R * 0.55 : 0;
      let a0 = -Math.PI / 2, out = open;
      if (spec.title) out += text(W / 2, 16, spec.title, 'fill="#f1f6fb" font-size="12" font-weight="600" text-anchor="middle" font-family="' + FONT + '"');
      pts.forEach((p, i) => {
        const a1 = a0 + 2 * Math.PI * p.y / total, big = a1 - a0 > Math.PI ? 1 : 0;
        const P = (a, r) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
        const [x0, y0] = P(a0, R), [x1, y1] = P(a1, R), [xi0, yi0] = P(a0, r0), [xi1, yi1] = P(a1, r0);
        const d = r0 ? 'M' + x0 + ',' + y0 + ' A' + R + ',' + R + ' 0 ' + big + ' 1 ' + x1 + ',' + y1 + ' L' + xi1 + ',' + yi1 + ' A' + r0 + ',' + r0 + ' 0 ' + big + ' 0 ' + xi0 + ',' + yi0 + ' Z'
          : 'M' + cx + ',' + cy + ' L' + x0 + ',' + y0 + ' A' + R + ',' + R + ' 0 ' + big + ' 1 ' + x1 + ',' + y1 + ' Z';
        const mid = (a0 + a1) / 2, [lx, ly] = P(mid, (R + r0) / 2 + (r0 ? 0 : R * 0.15));
        out += '<path d="' + d + '" fill="' + PALETTE[i % PALETTE.length] + '" stroke="#0b0e14" stroke-width="1.5"><title>' + esc(p.x + ': ' + fmtNum(p.y) + ' (' + (100 * p.y / total).toFixed(1) + '%)') + '</title></path>';
        if (p.y / total > 0.06) out += text(lx, ly, (100 * p.y / total).toFixed(0) + '%', 'fill="#0b0e14" font-size="10" font-weight="700" text-anchor="middle" dominant-baseline="middle" font-family="' + FONT + '"');
        a0 = a1;
      });
      return { svg: out + '</svg>', legend: pts.map((p, i) => ({ name: String(p.x), color: PALETTE[i % PALETTE.length] })), empty: false };
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
      S.forEach((s, si) => s.points.forEach((p, i) => {
        if (p.y == null) return;
        const x = stacked ? padL + i * gw + (gw - bw) / 2 : padL + i * gw + gw * 0.14 + si * bw;
        const base = stacked ? acc[i] : 0; if (stacked) acc[i] += p.y;
        const y0 = sy(base), y1 = sy(base + p.y);
        out += '<rect x="' + x + '" y="' + Math.min(y0, y1) + '" width="' + Math.max(1, bw - 2) + '" height="' + Math.max(1, Math.abs(y1 - y0)) + '" fill="' + PALETTE[si % PALETTE.length] + '" rx="2" opacity="0.92"><title>' + esc(s.name + ' · ' + p.x + ': ' + fmtNum(p.y)) + '</title></rect>';
      }));
    } else {
      S.forEach((s, si) => {
        const col = PALETTE[si % PALETTE.length];
        const pts = s.points.map((p, i) => p.y == null ? null : [sx(i), sy(p.y)]);
        const segs = []; let cur = [];
        pts.forEach(p => { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } }); if (cur.length) segs.push(cur);
        if (type !== 'scatter') segs.forEach(seg => { out += '<polyline points="' + seg.map(q => q.join(',')).join(' ') + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'; });
        if (type === 'area') segs.forEach(seg => { const b = sy(Math.max(0, yMin)); out += '<polygon points="' + seg.map(q => q.join(',')).concat([seg[seg.length - 1][0] + ',' + b, seg[0][0] + ',' + b]).join(' ') + '" fill="' + col + '" opacity="0.16"/>'; });
        pts.forEach((q, i) => { if (q) out += '<circle cx="' + q[0] + '" cy="' + q[1] + '" r="' + (type === 'scatter' ? 4 : 3) + '" fill="' + col + '" stroke="#0b0e14" stroke-width="1"><title>' + esc(s.name + ' · ' + s.points[i].x + ': ' + fmtNum(s.points[i].y)) + '</title></circle>'; });
      });
    }
    return { svg: out + '</svg>', legend: (S.length > 1 || spec.legend) ? legend : [], empty: false };
  }

  root.FridayChart = { series, renderSVG, PALETTE };
})(typeof window !== 'undefined' ? window : globalThis);
