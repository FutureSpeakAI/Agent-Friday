/* Hand cursor core: pure functions, no DOM.
 *
 * The hand tracker gives a noisy point thirty times a second. This module turns it into a
 * steady cursor that lands on what the user means: a One Euro filter removes jitter without
 * adding lag, a snap controller locks onto the nearest actionable target with hysteresis so
 * the lock never flickers between neighbours, a pinch machine freezes the cursor at pinch
 * onset (the pinch motion itself drags a raw cursor off small targets) and tells a click from
 * a drag from a hold, and a dwell machine offers click-by-hovering as an alternative.
 *
 * Loaded by the page onto the global as FridayHandCore, and by node tests through module.exports.
 * Invariants a test can hold:
 *   - snap: once locked, the lock survives until the cursor is farther than releaseRadius
 *     from the locked rect; it engages only within engageRadius; smaller targets win ties.
 *   - pinch: the point reported for a click is the point at onset, never the release point.
 *   - dwell: a target cannot fire twice without the cursor leaving it.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.FridayHandCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // ── One Euro filter (Casiez, Roussel, Vogel 2012) ──────────────────────────────
  function lowPass(alpha, value, prev) { return prev == null ? value : alpha * value + (1 - alpha) * prev; }
  function alphaFor(cutoffHz, dtSeconds) {
    const tau = 1 / (2 * Math.PI * cutoffHz);
    return 1 / (1 + tau / Math.max(dtSeconds, 1e-6));
  }
  function oneEuro(opts) {
    const o = Object.assign({ minCutoff: 1.0, beta: 0.02, dCutoff: 1.0 }, opts || {});
    let x = null, dx = null, t = null;
    return {
      /** Push a sample at time tNow (seconds). Returns the filtered value. */
      push(value, tNow) {
        if (t == null) { x = value; dx = 0; t = tNow; return value; }
        const dt = Math.max(tNow - t, 1e-6); t = tNow;
        const rawD = (value - x) / dt;
        dx = lowPass(alphaFor(o.dCutoff, dt), rawD, dx);
        const cutoff = o.minCutoff + o.beta * Math.abs(dx);
        x = lowPass(alphaFor(cutoff, dt), value, x);
        return x;
      },
      set(opts2) { Object.assign(o, opts2); },
      reset() { x = dx = t = null; },
      get value() { return x; },
    };
  }
  /** A 2-D point filter: two One Euro filters sharing options. */
  function pointFilter(opts) {
    const fx = oneEuro(opts), fy = oneEuro(opts);
    return {
      push(p, tNow) { return { x: fx.push(p.x, tNow), y: fy.push(p.y, tNow) }; },
      set(o) { fx.set(o); fy.set(o); },
      reset() { fx.reset(); fy.reset(); },
    };
  }

  // ── Geometry ────────────────────────────────────────────────────────────────────
  /** Distance from a point to a rect's edge; 0 inside. rect = {left, top, width, height}. */
  function edgeDistance(p, r) {
    const dx = Math.max(r.left - p.x, 0, p.x - (r.left + r.width));
    const dy = Math.max(r.top - p.y, 0, p.y - (r.top + r.height));
    return Math.hypot(dx, dy);
  }
  function center(r) { return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }
  function area(r) { return Math.max(r.width, 1) * Math.max(r.height, 1); }

  // ── Snap controller: nearest target, hysteresis, friction ──────────────────────
  // targets: [{ id, rect, weight? }]. weight > 1 makes a target "heavier" (orbs, primaries).
  function snapController(opts) {
    const o = Object.assign({ engageRadius: 40, releaseRadius: 64, friction: 0.35, scale: 1 }, opts || {});
    let lockedId = null;
    function pick(p, targets) {
      let best = null, bestD = Infinity, bestA = Infinity;
      for (const t of targets) {
        if (!t || !t.rect || t.disabled) continue;
        const d = edgeDistance(p, t.rect) / (t.weight || 1);
        if (d > o.engageRadius * o.scale) continue;
        const a = area(t.rect);
        // Nearest wins; on a near-tie (within 2 px) the smaller target wins, so a big card
        // does not out-pull the small button sitting on it.
        if (d < bestD - 2 || (Math.abs(d - bestD) <= 2 && a < bestA)) { best = t; bestD = d; bestA = a; }
      }
      return best;
    }
    return {
      /** Feed the filtered cursor and the current targets. Returns { target, point, locked }. */
      update(p, targets) {
        let target = null;
        if (lockedId != null) {
          const cur = targets.find(t => t && t.id === lockedId);
          if (cur && !cur.disabled && edgeDistance(p, cur.rect) <= o.releaseRadius * o.scale) target = cur;
          else lockedId = null;
        }
        if (!target) { target = pick(p, targets); lockedId = target ? target.id : null; }
        if (!target) return { target: null, point: p, locked: false };
        // Friction: the drawn point is pulled toward the target's centre by the friction gain,
        // but never jumps; the reticle itself is drawn on the target by the caller.
        const c = center(target.rect);
        const point = { x: p.x + (c.x - p.x) * o.friction, y: p.y + (c.y - p.y) * o.friction };
        return { target, point, locked: true };
      },
      release() { lockedId = null; },
      set(o2) { Object.assign(o, o2); },
      get lockedId() { return lockedId; },
    };
  }

  // ── Pinch machine: freeze at onset; click, drag or hold on the way out ────────
  // Feed pinch(strength 0..1, point, tMs). Thresholds have hysteresis too: onset above 0.6,
  // release below 0.4, so a wavering pinch does not double-fire.
  function pinchMachine(opts) {
    const o = Object.assign({ onset: 0.6, release: 0.4, dragSlopPx: 24, holdMs: 700 }, opts || {});
    let pinched = false, start = null, startT = 0, last = null, dragging = false, holdFired = false, guarded = false;
    return {
      /**
       * @returns an event or null: { type: 'pinchStart'|'drag'|'click'|'holdProgress'|'hold'|'cancel'|'pinchEnd', point, delta?, progress?, durationMs? }
       */
      update(strength, point, tMs, isGuarded) {
        if (!pinched) {
          if (strength >= o.onset) {
            pinched = true; start = { x: point.x, y: point.y }; startT = tMs; last = start; dragging = false; holdFired = false; guarded = !!isGuarded;
            return { type: 'pinchStart', point: start };
          }
          return null;
        }
        // While pinched the cursor is FROZEN at `start` for clicks; movement only matters for drags.
        const moved = Math.hypot(point.x - start.x, point.y - start.y);
        if (strength <= o.release) {
          pinched = false;
          const dur = tMs - startT;
          if (dragging) return { type: 'pinchEnd', point: start, durationMs: dur };
          if (guarded) {
            // A guarded action fires only after the hold completed; an early release cancels.
            return holdFired ? { type: 'click', point: start, durationMs: dur, guarded: true } : { type: 'cancel', point: start, durationMs: dur };
          }
          return { type: 'click', point: start, durationMs: dur };
        }
        if (!dragging && moved > o.dragSlopPx && !guarded) { dragging = true; last = start; }
        if (dragging) {
          const delta = { x: point.x - last.x, y: point.y - last.y }; last = { x: point.x, y: point.y };
          return { type: 'drag', point: start, delta };
        }
        if (guarded && !holdFired) {
          const progress = Math.min(1, (tMs - startT) / o.holdMs);
          if (progress >= 1) { holdFired = true; return { type: 'hold', point: start, progress: 1 }; }
          return { type: 'holdProgress', point: start, progress };
        }
        return null;
      },
      /** The frozen point while pinched, else null. */
      get frozen() { return pinched ? start : null; },
      get pinched() { return pinched; },
      set(o2) { Object.assign(o, o2); },
      reset() { pinched = false; start = null; dragging = false; holdFired = false; },
    };
  }

  // ── Dwell machine: hover a locked target for dwellMs to click ────────────────
  function dwellMachine(opts) {
    const o = Object.assign({ dwellMs: 650 }, opts || {});
    let id = null, since = 0, fired = false;
    return {
      /** Feed the locked target id (or null) and the time. Returns { progress, fire }. */
      update(targetId, tMs) {
        if (targetId == null) { id = null; fired = false; return { progress: 0, fire: false }; }
        if (targetId !== id) { id = targetId; since = tMs; fired = false; }
        if (fired) return { progress: 1, fire: false };
        const progress = Math.min(1, (tMs - since) / o.dwellMs);
        if (progress >= 1) { fired = true; return { progress: 1, fire: true }; }
        return { progress, fire: false };
      },
      set(o2) { Object.assign(o, o2); },
      reset() { id = null; fired = false; },
    };
  }

  // ── Two-hand zoom: distance between two points → scale factor ─────────────────
  function zoomTracker() {
    let base = null;
    return {
      update(a, b) {
        if (!a || !b) { base = null; return null; }
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (base == null) { base = d; return 1; }
        return d / Math.max(base, 1);
      },
      reset() { base = null; },
    };
  }

  // ── Guarded actions: what needs a held pinch or a spoken yes in big mode ──────
  // Judged by the control's own words. Verbs that leave the machine, destroy, spend or
  // approve are guarded; words that stop, close, step back or only prepare are not, even
  // beside a guarded verb ("Cancel send", "Draft a reply"). A container can force either
  // way with data-fr-guarded (see the DOM layer); this is the default.
  const GUARD_WORDS = /(send(?: it| now| all)?|post(?: now)?|publish|delete|erase|remove|pay|buy|purchase|spend|transfer|approve(?: & continue| and continue)?|release|submit|share)/i;
  const SAFE_WORDS = /(cancel|deny|stop|close|later|change|reply|draft|preview|undo|back|dismiss|edit)/i;
  function classifyGuard(text, opts) {
    const t = String(text || '').trim().slice(0, 80);
    if (opts && opts.force === 'on') return true;
    if (opts && opts.force === 'off') return false;
    if (opts && opts.danger) return true;
    if (!t) return false;
    return GUARD_WORDS.test(t) && !SAFE_WORDS.test(t);
  }

  // ── Rate limiter for visual state changes (photosensitivity: <= 3 per second) ──
  function changeLimiter(minIntervalMs) {
    let lastT = -Infinity, lastV;
    return function (value, tMs) {
      if (value === lastV) return lastV;
      if (tMs - lastT < (minIntervalMs || 334)) return lastV;
      lastT = tMs; lastV = value; return value;
    };
  }

  return { oneEuro, pointFilter, edgeDistance, center, snapController, pinchMachine, dwellMachine, zoomTracker, changeLimiter, classifyGuard };
});
