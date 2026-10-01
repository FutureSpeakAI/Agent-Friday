/**
 * A photosensitivity meter for rendered frames (WCAG 2.3.1, "Three Flashes
 * or Below Threshold"), run inside the page.
 *
 * Install with page.addInitScript({ path }) and call
 * window.__flashMeter.start(canvas, options). Each frame is judged by the
 * page's own clock. Under Playwright's fake clock every frame is 1/60 s of
 * scene time, so a slow renderer changes how long a run takes, never what it
 * measures.
 *
 * A WebGL canvas must be read in the same task that drew it: once control
 * returns to the event loop the browser presents and clears its drawing
 * buffer, and under the fake clock each animation callback is its own task.
 * So for the scene, start with { drawnBy: composer }: the meter wraps that
 * object's render() and samples straight after each frame is drawn. With no
 * drawnBy (a 2D canvas), it samples on every animation frame.
 *
 * The definitions follow WCAG 2.2 "general flash and red flash thresholds":
 *   - relative luminance per pixel, sRGB linearised;
 *   - a transition is a change of 0.1 or more of the maximum relative
 *     luminance, with the darker state below 0.80. Each pixel's transitions
 *     are measured from its last extreme, so a slow ramp counts once it
 *     has gone far enough;
 *   - a red transition: either state a saturated red (R/(R+G+B) >= 0.8),
 *     and the change in (R-G-B) x 320 is 20 or more;
 *   - a flash is a pair of opposing transitions. A frame has a flash event
 *     when, within some 10-degree field (341x256 of 1024x768, scaled to the
 *     canvas), 25% or more of the pixels made a transition in the same
 *     direction;
 *   - consecutive events in the same direction merge. More than six
 *     transitions (three flashes) in any one second fails.
 *
 * Also "gentle": within any 10-degree field, the average lightness (CIE
 * L*, 0-100, per pixel, then averaged over the field) may move by less
 * than GENTLE_L over any 100 ms. The scene is dark, so a pulse can be far
 * below the WCAG luminance step and still be a visible flicker: from 0.004
 * to 0.02 luminance is under a fifth of that step, but it is L* 3.6 to
 * 15.5. Averaging lightness per pixel weighs a change by its area, so a
 * small spark moves a field by about its share of it.
 */
(function () {
  const W = 160, H = 100;                       // the sample grid
  const FIELD_W = Math.round(W * 341 / 1024), FIELD_H = Math.round(H * 256 / 768);
  const FIELD_AREA = FIELD_W * FIELD_H, FLASH_SHARE = 0.25, STRIDE = 4;
  const STEP = 0.1, DARK = 0.8, RED_STEP = 20;
  const GENTLE_L = 6, GENTLE_S = 0.1;
  const lstar = y => (y > 0.008856 ? 116 * Math.cbrt(y) - 16 : 903.3 * y);
  const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const LUT = new Float32Array(256); for (let i = 0; i < 256; i++) LUT[i] = lin(i);

  function makeTracker() {
    const ref = new Float32Array(W * H), dir = new Int8Array(W * H);
    let init = false;
    // One frame of values: returns [upMask, downMask] of the pixels that
    // made a transition this frame.
    return function (vals, step, darkOk) {
      const up = new Uint8Array(W * H), dn = new Uint8Array(W * H);
      if (!init) { ref.set(vals); init = true; return [up, dn]; }
      for (let i = 0; i < vals.length; i++) {
        const v = vals[i], r = ref[i];
        if (dir[i] >= 0 && v > r) { if (dir[i] === 1) { ref[i] = v; continue; } }
        if (dir[i] <= 0 && v < r) { if (dir[i] === -1) { ref[i] = v; continue; } }
        const d = v - r;
        if (Math.abs(d) >= step && (!darkOk || darkOk(v, r))) {
          const s = d > 0 ? 1 : -1;
          if (s !== dir[i]) { (s > 0 ? up : dn)[i] = 1; dir[i] = s; }
          ref[i] = v;
        }
      }
      return [up, dn];
    };
  }

  // The largest share of any 10-degree field covered by a mask.
  function worstField(mask) {
    const S = new Uint32Array((W + 1) * (H + 1));
    for (let y = 0; y < H; y++) {
      let row = 0;
      for (let x = 0; x < W; x++) { row += mask[y * W + x]; S[(y + 1) * (W + 1) + x + 1] = S[y * (W + 1) + x + 1] + row; }
    }
    let best = 0;
    for (let y = 0; y + FIELD_H <= H; y += STRIDE) for (let x = 0; x + FIELD_W <= W; x += STRIDE) {
      const a = S[(y + FIELD_H) * (W + 1) + x + FIELD_W] - S[y * (W + 1) + x + FIELD_W]
              - S[(y + FIELD_H) * (W + 1) + x] + S[y * (W + 1) + x];
      if (a > best) best = a;
    }
    return best / FIELD_AREA;
  }
  // Average lightness of every 10-degree field, on the STRIDE grid.
  function fieldMeans(L) {
    const S = new Float64Array((W + 1) * (H + 1)), out = [];
    for (let y = 0; y < H; y++) {
      let row = 0;
      for (let x = 0; x < W; x++) { row += L[y * W + x]; S[(y + 1) * (W + 1) + x + 1] = S[y * (W + 1) + x + 1] + row; }
    }
    for (let y = 0; y + FIELD_H <= H; y += STRIDE) for (let x = 0; x + FIELD_W <= W; x += STRIDE) {
      out.push((S[(y + FIELD_H) * (W + 1) + x + FIELD_W] - S[y * (W + 1) + x + FIELD_W]
               - S[(y + FIELD_H) * (W + 1) + x] + S[y * (W + 1) + x]) / FIELD_AREA);
    }
    return out;
  }

  let canvas = null, ctx = null, running = false, label = '', lost = false, dark = false;
  // While the GPU has the scene's context the canvas shows nothing, and the
  // page draws nothing to sample. The meter records what the user sees: a
  // black frame when the context goes and another when it comes back, so the
  // first frames after the restore are measured against black. A segment in
  // which the context went says so in its report.
  function watch(c) {
    if (!c || c.__flashWatched) return;
    c.__flashWatched = true;
    c.addEventListener('webglcontextlost', () => { lost = true; dark = true; sample(); });
    c.addEventListener('webglcontextrestored', () => { sample(); dark = false; });
  }
  let lumT, redT, events, frames, means, meanHist, segments = [];

  function reset() {
    lost = false;
    lumT = makeTracker(); redT = makeTracker();
    events = { lum: [], red: [] }; frames = 0; means = []; meanHist = [];
  }
  function event(list, t, d) {
    if (list.length && list[list.length - 1].d === d) return;   // same direction merges
    list.push({ t, d });
  }
  function maxInOneSecond(list) {
    let best = 0, j = 0;
    for (let i = 0; i < list.length; i++) {
      while (list[i].t - list[j].t >= 1.0) j++;
      best = Math.max(best, i - j + 1);
    }
    return best;
  }

  let onFrame = true;
  function tick() { if (!running || !onFrame) return; requestAnimationFrame(tick); sample(); }
  function sample() {
    if (!running) return;
    const t = performance.now() / 1000;
    let d;
    if (dark) d = new Uint8ClampedArray(W * H * 4);
    else { ctx.drawImage(canvas, 0, 0, W, H); d = ctx.getImageData(0, 0, W, H).data; }
    const L = new Float32Array(W * H), Ls = new Float32Array(W * H), Rd = new Float32Array(W * H);
    let sum = 0;
    for (let i = 0, p = 0; i < d.length; i += 4, p++) {
      const r = d[i], g = d[i + 1], b = d[i + 2];
      L[p] = 0.2126 * LUT[r] + 0.7152 * LUT[g] + 0.0722 * LUT[b];
      Ls[p] = lstar(L[p]);
      sum += L[p];
      const R = LUT[r], G = LUT[g], B = LUT[b], tot = R + G + B;
      Rd[p] = tot > 0 && R / tot >= 0.8 ? Math.max(0, (R - G - B) * 320) : 0;
    }
    const [lu, ld] = lumT(L, STEP, (a, b) => Math.min(a, b) < DARK);
    const [ru, rd] = redT(Rd, RED_STEP, null);
    if (worstField(lu) >= FLASH_SHARE) event(events.lum, t, 1);
    if (worstField(ld) >= FLASH_SHARE) event(events.lum, t, -1);
    if (worstField(ru) >= FLASH_SHARE) event(events.red, t, 1);
    if (worstField(rd) >= FLASH_SHARE) event(events.red, t, -1);
    // Gentle: each field's lightness against the frame at least 100 ms ago
    // (the newest such frame: frames need not land exactly 100 ms apart).
    const fm = fieldMeans(Ls);
    meanHist.push({ t, fm });
    while (meanHist.length > 2 && t - meanHist[1].t >= GENTLE_S - 1e-6) meanHist.shift();
    const old = meanHist[0];
    let swing = 0;
    if (t - old.t >= GENTLE_S - 1e-6) for (let k = 0; k < fm.length; k++) swing = Math.max(swing, Math.abs(fm[k] - old.fm[k]));
    means.push({ t, whole: sum / (W * H), swing });
    frames++;
  }

  window.__flashMeter = {
    limits: { transitionsPerSecond: 6, gentleLightness: GENTLE_L, field: [FIELD_W, FIELD_H], grid: [W, H] },
    start(c, options) {
      const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
      ctx = cv.getContext('2d', { willReadFrequently: true });
      reset(); running = true;
      const drawer = options && options.drawnBy;
      if (drawer) { onFrame = false; this.rehook(c, drawer); }
      else { canvas = c; watch(c); onFrame = true; requestAnimationFrame(tick); }
    },
    // After the page rebuilds its scene (the GPU took the context away and
    // gave it back), follow the new canvas and the new drawer.
    rehook(c, drawer) {
      canvas = c; watch(c);
      if (drawer && !drawer.__flashHooked) {
        const render = drawer.render;
        drawer.render = function () { const r = render.apply(this, arguments); sample(); return r; };
        drawer.__flashHooked = true;
      }
    },
    // Close the current segment of measurement and start another.
    mark(name) {
      if (label) segments.push(this.report(label));
      label = name; reset();
    },
    report(name) {
      const whole = means.map(m => m.whole);
      let worstRatio = 1;
      for (let i = 1; i < whole.length; i++) {
        const a = whole[i - 1], b = whole[i];
        if (a > 1e-4 && b > 1e-4) worstRatio = Math.max(worstRatio, b / a, a / b);
      }
      const lumMax = maxInOneSecond(events.lum), redMax = maxInOneSecond(events.red);
      const swing = means.reduce((m, x) => Math.max(m, x.swing), 0);
      return { name: name || label, frames, seconds: means.length ? +(means[means.length - 1].t - means[0].t).toFixed(2) : 0,
               transitionsPerSecond: lumMax, redTransitionsPerSecond: redMax,
               flashesPerSecond: Math.floor(lumMax / 2), redFlashesPerSecond: Math.floor(redMax / 2),
               swing: +swing.toFixed(2), wholeFrameRatio: +worstRatio.toFixed(3),
               contextLost: lost,
               ok: lumMax <= 6 && redMax <= 6 && swing < GENTLE_L };
    },
    finish() { if (label) segments.push(this.report(label)); label = ''; running = false; return segments; },
  };
})();
