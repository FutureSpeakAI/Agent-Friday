/* The hand cursor layer: one target registry, a steady reticle, snap, pinch freeze, dwell,
 * pinch-drag, two-hand zoom, and big mode. Sits after the page's tracking engine
 * (window.FridayTracking shapes the raw hand into screen coordinates and decides the pinch
 * with its own hysteresis); this layer only decides WHAT the cursor is on and WHAT a pinch
 * does. The maths is in static/hand_cursor_core.js (window.FridayHandCore), tested in node.
 *
 * Invariants:
 *   - every actionable element is a target with no per-screen code (the discovery test
 *     walks each workspace and fails on anything the registry misses);
 *   - a click lands at the point frozen at pinch onset, on the locked target;
 *   - a guarded action (send, delete, spend, publish, approve) needs the hold to complete;
 *   - orbs keep their own gravity and click: this layer feeds them a pointer and a click;
 *   - visual state changes are limited to one per 334 ms; opacity and scale only.
 */
(function () {
  'use strict';
  const Core = window.FridayHandCore;
  if (!Core) { console.warn('[hand-cursor] core missing'); return; }

  // ── Registry ─────────────────────────────────────────────────────────────────
  const ACTIONABLE = [
    'button', 'a[href]', 'input:not([type=hidden])', 'select', 'textarea', 'summary', 'label[for]',
    '[role=button]', '[role=link]', '[role=option]', '[role=menuitem]', '[role=menuitemradio]', '[role=menuitemcheckbox]',
    '[role=tab]', '[role=switch]', '[role=radio]', '[role=checkbox]', '[role=slider]', '[role=treeitem]',
    '[tabindex]:not([tabindex="-1"])', '[contenteditable=""]', '[contenteditable=true]',
    '[data-fr-target]', '[onclick]', '.dock-btn', '.news-card', '.row[role=option]', '.sec',
  ].join(',');
  const TARGET_SELECTOR = ACTIONABLE;
  let cache = { t: 0, list: [] };
  let dirty = true;
  const mo = new MutationObserver(() => { dirty = true; });
  function watch() { mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style', 'hidden', 'disabled', 'aria-disabled', 'aria-hidden', 'inert', 'data-fr-guarded'] }); }
  window.addEventListener('scroll', () => { dirty = true; }, true);
  window.addEventListener('resize', () => { dirty = true; });

  function visible(el, r) {
    if (!r || r.width < 2 || r.height < 2) return false;
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
    if (el.disabled || el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') return false;
    if (el.closest('[inert],[aria-hidden="true"],[hidden],[data-fr-target="off"]')) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.pointerEvents === 'none' || +cs.opacity < 0.05) return false;
    return true;
  }
  function isGuarded(el) {
    const force = el.dataset && el.dataset.frGuarded === 'off' ? 'off' : (el.closest('[data-fr-guarded]:not([data-fr-guarded="off"])') ? 'on' : null);
    const text = (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '');
    return Core.classifyGuard(text, { force, danger: el.classList.contains('btn-danger') || el.classList.contains('danger') });
  }
  /** Every actionable thing on screen, as snap targets. Rebuilt at most every 120 ms or on change. */
  function targets(now) {
    if (!dirty && now - cache.t < 120) return cache.list;
    const list = [];
    const seen = new Set();
    document.querySelectorAll(TARGET_SELECTOR).forEach((el, i) => {
      if (seen.has(el)) return; seen.add(el);
      // The innermost actionable wins: a card containing a button registers both, and the
      // snap controller's tie rule favours the smaller one.
      const r = el.getBoundingClientRect();
      if (!visible(el, r)) return;
      list.push({ id: 'el:' + i + ':' + (el.id || el.className || el.tagName), el, rect: { left: r.left, top: r.top, width: r.width, height: r.height }, guarded: isGuarded(el), weight: el.dataset && el.dataset.frWeight ? +el.dataset.frWeight : 1 });
    });
    if (typeof window.fridayOrbTargets === 'function') {
      for (const o of window.fridayOrbTargets()) {
        const s = 44; // an orb is ~12 px on screen; give it a reachable box
        list.push({ id: 'orb:' + o.id, orb: o, rect: { left: o.sx - s / 2, top: o.sy - s / 2, width: s, height: s }, guarded: false, weight: 1.4 });
      }
    }
    cache = { t: now, list }; dirty = false;
    return list;
  }
  /** The target an element belongs to, if it is registered. Used by the discovery test. */
  function has(el) { return targets(performance.now()).some(t => t.el === el || (t.el && t.el.contains(el))); }

  // ── Reticle and highlight ──────────────────────────────────────────────────
  let reticle, ring, arc, snapBox;
  function ensureDom() {
    reticle = document.getElementById('hand-cursor');
    if (!reticle) return false;
    if (!reticle.querySelector('.fr-arc')) {
      reticle.innerHTML = '<svg class="fr-arc" viewBox="0 0 40 40" aria-hidden="true"><circle class="fr-arc-bg" cx="20" cy="20" r="17"/><circle class="fr-arc-fill" cx="20" cy="20" r="17"/></svg>';
      arc = reticle.querySelector('.fr-arc-fill');
    }
    if (!snapBox) { snapBox = document.createElement('div'); snapBox.className = 'fr-snap-box'; snapBox.setAttribute('aria-hidden', 'true'); document.body.appendChild(snapBox); }
    return true;
  }
  const limiter = Core.changeLimiter(334);
  let shownState = 'free';
  function setState(s, now) {
    const v = limiter(s, now); if (v === shownState || v == null) return;
    reticle.classList.remove('fr-free', 'fr-locked', 'fr-pinched', 'fr-held'); reticle.classList.add('fr-' + v); shownState = v;
  }
  function setArc(p) { if (!arc) return; const C = 2 * Math.PI * 17; arc.style.strokeDashoffset = String(C * (1 - Math.max(0, Math.min(1, p)))); arc.style.opacity = p > 0 ? '1' : '0'; }
  let highlighted = null;
  function highlight(t) {
    if (!snapBox && !ensureDom()) return;
    const el = t && t.el || null;
    if (highlighted && highlighted !== el) { highlighted.classList.remove('fr-snap'); dispatchHover(highlighted, false); }
    if (el && highlighted !== el) { el.classList.add('fr-snap'); dispatchHover(el, true); }
    highlighted = el;
    if (t) { const r = t.rect; snapBox.style.cssText = 'left:' + (r.left - 4) + 'px;top:' + (r.top - 4) + 'px;width:' + (r.width + 8) + 'px;height:' + (r.height + 8) + 'px;opacity:1'; }
    else snapBox.style.opacity = '0';
  }
  // Menus that open on hover (the scene menu) need to see a pointer arrive and leave.
  function dispatchHover(el, enter) {
    const r = el.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
    for (const type of enter ? ['pointerover', 'pointerenter', 'mouseover', 'mouseenter', 'mousemove'] : ['pointerout', 'pointerleave', 'mouseout', 'mouseleave']) {
      try { el.dispatchEvent(new MouseEvent(type, { bubbles: !/enter|leave/.test(type), cancelable: true, clientX: x, clientY: y, view: window })); } catch (e) { /* ignore */ }
    }
  }

  // ── Clicking ─────────────────────────────────────────────────────────────────
  function targetUsable(t, point) {
    if (!t) return false;
    if (t.orb) return typeof window.fridayOrbTargets === 'function' && window.fridayOrbTargets().some(o => o.id === t.orb.id);
    const el = t.el;
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (!visible(el, r)) return false;
    const x = point && point.x >= r.left && point.x <= r.right ? point.x : r.left + r.width / 2;
    const y = point && point.y >= r.top && point.y <= r.bottom ? point.y : r.top + r.height / 2;
    const top = document.elementFromPoint(x, y);
    return !!top && (top === el || el.contains(top));
  }
  function clickTarget(t, point, now, held) {
    if (!t) return false;
    if (!targetUsable(t, point)) return false;
    if (t.orb) { if (typeof window.fridayOrbClickAt !== 'function') return false; window.fridayOrbClickAt(t.orb.sx, t.orb.sy); return true; }
    const el = t.el; if (!el || !el.isConnected) return false;
    if (isGuarded(el) && !held) return false;
    const r = el.getBoundingClientRect();
    // Click INSIDE the element at the frozen point if it is inside, else at its centre.
    const x = point.x >= r.left && point.x <= r.right ? point.x : r.left + r.width / 2;
    const y = point.y >= r.top && point.y <= r.bottom ? point.y : r.top + r.height / 2;
    const init = { bubbles: true, cancelable: true, clientX: x, clientY: y, view: window, button: 0, buttons: 1, pointerId: 1, pointerType: 'touch', isPrimary: true };
    try { el.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
    for (const [ctor, type] of [[PointerEvent, 'pointerdown'], [MouseEvent, 'mousedown'], [PointerEvent, 'pointerup'], [MouseEvent, 'mouseup']]) {
      try { el.dispatchEvent(new ctor(type, init)); } catch (e) { /* ignore */ }
    }
    if (!targetUsable(t, { x, y }) || (isGuarded(el) && !held)) return false;
    try { el.dispatchEvent(new MouseEvent('click', init)); } catch (e) { if (typeof el.click === 'function') el.click(); }
    if (typeof window.spawnRipple === 'function') window.spawnRipple(x, y, 'rgba(0,212,255,0.9)');
    return true;
  }
  function scrollableAncestor(el) {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 4) return n;
    }
    return document.scrollingElement;
  }
  function dragFrom(t, frozen, delta) {
    const el = t && t.el ? t.el : document.elementFromPoint(frozen.x, frozen.y);
    if (!el) return;
    if (el.matches('input[type="range"]')) {
      const style = getComputedStyle(el);
      if (style.writingMode.startsWith('vertical') || el.getAttribute('orient') === 'vertical') return false;
      if (!dragFrom.active) {
        dragFrom.active = { kind: 'range', target: t, point: frozen, x: frozen.x, changed: false };
        try { el.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
      }
      const state = dragFrom.active, rect = el.getBoundingClientRect();
      if (state.kind !== 'range' || state.target.el !== el) return false;
      state.x += delta.x;
      state.point = { x: Math.max(rect.left + 1, Math.min(rect.right - 1, state.x)), y: rect.top + rect.height / 2 };
      if (!targetUsable(t, state.point) || isGuarded(el)) { endDrag(false); return false; }
      const number = (raw, fallback) => raw !== '' && Number.isFinite(Number(raw)) ? Number(raw) : fallback;
      const min = number(el.min, 0), max = Math.max(min, number(el.max, 100));
      let fraction = Math.max(0, Math.min(1, (state.x - rect.left) / rect.width));
      if (style.direction === 'rtl') fraction = 1 - fraction;
      let value = min + fraction * (max - min);
      const step = number(el.step, 1);
      if (el.step !== 'any') value = min + Math.round((value - min) / (step > 0 ? step : 1)) * (step > 0 ? step : 1);
      value = Number(Math.max(min, Math.min(max, value)).toFixed(12));
      if (Number(el.value) !== value) {
        // React tracks the instance setter. The native setter lets its input
        // handler observe the changed value and retain controlled state.
        const previous = el.value;
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, String(value));
        if (el.value !== previous) {
          state.changed = true;
          el.dispatchEvent(new Event('input', { bubbles: true }));
        }
      }
      return;
    }
    // A window's title bar already drags with mouse events; drive it rather than re-implement.
    const bar = el.closest('.fwin-bar, [data-fr-draggable]');
    if (bar) {
      const init = { bubbles: true, cancelable: true, view: window, button: 0, buttons: 1 };
      if (!dragFrom.active) { bar.dispatchEvent(new MouseEvent('mousedown', Object.assign({ clientX: frozen.x, clientY: frozen.y }, init))); dragFrom.active = { x: frozen.x, y: frozen.y }; }
      dragFrom.active.x += delta.x; dragFrom.active.y += delta.y;
      document.dispatchEvent(new MouseEvent('mousemove', Object.assign({ clientX: dragFrom.active.x, clientY: dragFrom.active.y }, init)));
      return;
    }
    const sc = scrollableAncestor(el); if (sc) { sc.scrollTop -= delta.y; sc.scrollLeft -= delta.x; }
  }
  function endDrag(commit = true) {
    const state = dragFrom.active; dragFrom.active = null;
    if (!state) return;
    if (state.kind === 'range') {
      if (commit && state.changed && targetUsable(state.target, state.point) && !isGuarded(state.target.el)) state.target.el.dispatchEvent(new Event('change', { bubbles: true }));
    } else document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window, clientX: state.x, clientY: state.y }));
  }

  // ── The per-frame step ───────────────────────────────────────────────────────
  const snap = Core.snapController({ engageRadius: 40, releaseRadius: 64, friction: 0.35 });
  const pinch = Core.pinchMachine({ onset: 0.5, release: 0.5, dragSlopPx: 36, holdMs: 700 }); // a pinch moves the tracked midpoint up to ~30 px; past 36 px it is a drag // the engine's hysteresis is authoritative; strength is 0 or 1
  const dwell = Core.dwellMachine({ dwellMs: 700 });
  const zoom = Core.zoomTracker();
  let locked = null, pinchTarget = null, pinchDragTarget = null, lastPoint = { x: -100, y: -100 }, trackingOn = false, secondHand = null, lastZoomEmit = 0;
  const cfg = () => (window.FridayTracking && window.FridayTracking.cfg) || {};

  /** Called once per animation frame by the page with the engine's shaped hand. */
  function frame(h) {
    if (!ensureDom()) return;
    const now = h.t != null ? h.t : performance.now();
    const c = cfg();
    const big = document.body.classList.contains('fr-big');
    snap.set({ scale: big ? 1.5 : 1, engageRadius: +c.snap_radius || 40, releaseRadius: (+c.snap_radius || 40) * 1.6 });
    dwell.set({ dwellMs: Math.max(200, +c.dwell_ms || 700) });
    if (!h.visible) { setState('free', now); highlight(null); locked = null; pinchTarget = pinchDragTarget = null; snap.release(); pinch.reset(); dwell.reset(); endDrag(); reticle.classList.add('hidden'); return; }
    reticle.classList.remove('hidden');
    const raw = { x: h.x, y: h.y };
    const frozen = pinch.frozen;
    // Snap decides the target from the live point unless we are frozen in a pinch.
    const res = pinch.pinched ? { target: pinchTarget, point: frozen, locked: !!pinchTarget }
      : c.snap === false ? { target: targetAt(raw), point: raw, locked: false }
      : snap.update(raw, targets(now).filter(t => Core.edgeDistance(raw, t.rect) <= (+c.snap_radius || 40) * 1.6 * (big ? 1.5 : 1) && targetUsable(t)));
    // Revalidate every frame: opening a popup, disabling a button or leaving
    // a workspace must revoke the old target even during a frozen pinch.
    if (res.target && !targetUsable(res.target, frozen || (c.snap === false ? raw : null))) {
      res.target = null; pinchTarget = pinchDragTarget = null; snap.release(); endDrag(false);
    }
    if (res.target !== locked) { locked = res.target; highlight(locked); if (locked && locked.orb && typeof window.fridayOrbPointer === 'function') window.fridayOrbPointer(locked.orb.sx, locked.orb.sy); }
    else if (locked && locked.el) { const lr = locked.el.getBoundingClientRect(); if (Math.abs(lr.left - locked.rect.left) > 0.5 || Math.abs(lr.width - locked.rect.width) > 0.5 || Math.abs(lr.top - locked.rect.top) > 0.5) { locked.rect = { left: lr.left, top: lr.top, width: lr.width, height: lr.height }; highlight(locked); } }
    const drawAt = frozen || (locked && c.snap !== false ? Core.center(locked.rect) : res.point);
    reticle.style.left = drawAt.x + 'px'; reticle.style.top = drawAt.y + 'px';
    lastPoint = drawAt;
    if (locked && locked.orb && !frozen && typeof window.fridayOrbPointer === 'function') window.fridayOrbPointer(drawAt.x, drawAt.y);

    // Click method: dwell or pinch.
    if (c.click_method === 'dwell') {
      const d = dwell.update(locked && !locked.guarded ? locked.id : null, now);
      setArc(d.progress);
      if (d.fire) { clickTarget(locked, drawAt, now); }
      setState(locked ? 'locked' : 'free', now);
      return;
    }
    if (!pinch.pinched && h.pinching) {
      pinchTarget = locked;
      // Blank content and title bars can still start a scroll or window drag,
      // but never become an unregistered fallback click target on release.
      const el = document.elementFromPoint(raw.x, raw.y);
      pinchDragTarget = pinchTarget || (el ? { el, rect: el.getBoundingClientRect() } : null);
    }
    const ev = pinch.update(h.pinching ? 1 : 0, raw, now, !!(pinchTarget && (pinchTarget.el ? isGuarded(pinchTarget.el) : pinchTarget.guarded)));
    if (!ev) { setState(pinch.pinched ? 'pinched' : (locked ? 'locked' : 'free'), now); if (!pinch.pinched) setArc(0); return; }
    switch (ev.type) {
      case 'pinchStart': setState('pinched', now); break;
      case 'holdProgress': setArc(ev.progress); break;
      case 'hold': setArc(1); setState('held', now); break;
      case 'drag':
        if (pinchDragTarget && targetUsable(pinchDragTarget, ev.point)) {
          if (dragFrom(pinchDragTarget, ev.point, ev.delta) === false) { pinchTarget = pinchDragTarget = locked = null; snap.release(); highlight(null); }
        } else endDrag(false);
        break;
      case 'click': setArc(0); clickTarget(pinchTarget, ev.point, now, ev.guarded === true); pinchTarget = pinchDragTarget = null; setState('free', now); break;
      case 'cancel': pinchTarget = pinchDragTarget = null; setArc(0); setState('free', now); if (typeof window.fridayToast === 'function') window.fridayToast('Hold the pinch to ' + actionWord(locked) + '.'); break;
      case 'pinchEnd': pinchTarget = pinchDragTarget = null; endDrag(); setArc(0); setState('free', now); break;
    }
  }
  function actionWord(t) { const s = t && t.el ? (t.el.getAttribute('aria-label') || t.el.textContent || '').trim().toLowerCase().slice(0, 24) : ''; return s || 'do that'; }
  function targetAt(p) {
    const top = document.elementFromPoint(p.x, p.y);
    const el = top && top.closest(TARGET_SELECTOR);
    if (!el) return null;
    const t = targets(performance.now()).find(item => item.el === el);
    return t && targetUsable(t, p) ? t : null;
  }

  /** A second hand's point (screen px) for two-hand zoom, or null when it is gone. */
  function second(p) {
    secondHand = p;
    if (!p || !pinch.pinched) { zoom.reset(); return; }
    const s = zoom.update(lastPoint, p);
    const now = performance.now();
    if (s != null && now - lastZoomEmit > 50) { lastZoomEmit = now; window.dispatchEvent(new CustomEvent('friday:hand-zoom', { detail: { scale: s, at: lastPoint } })); }
  }

  // ── Big mode ─────────────────────────────────────────────────────────────────
  let bigSetting = 'auto';
  // Long rows of actions fold in big mode: the first four stay as big cards, the rest sit
  // behind one "More" card (HIG hand-cursor.md §2.2). Rows opt in by selector or data-fr-fold.
  const FOLD_SELECTOR = '[data-fr-fold], .news-toolbar, .notif-dropdown .notif-head, .landing-cluster .landing-actions';
  const FOLD_KEEP = 4;
  function foldRows(on) {
    document.querySelectorAll(FOLD_SELECTOR).forEach(row => {
      const kids = Array.from(row.children).filter(k => !k.classList.contains('fr-more'));
      const more = row.querySelector(':scope > .fr-more');
      if (!on || kids.length <= FOLD_KEEP + 1) {
        kids.forEach(k => { k.classList.remove('fr-folded'); });
        if (more) more.remove();
        return;
      }
      const open = more && more.getAttribute('aria-expanded') === 'true';
      kids.forEach((k, i) => k.classList.toggle('fr-folded', i >= FOLD_KEEP && !open));
      if (!more) {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'fr-more btn'; b.setAttribute('aria-expanded', 'false');
        b.textContent = 'More'; b.title = 'The rest of this row';
        b.addEventListener('click', () => { const o = b.getAttribute('aria-expanded') !== 'true'; b.setAttribute('aria-expanded', String(o)); b.textContent = o ? 'Less' : 'More'; foldRows(true); });
        row.appendChild(b);
      }
    });
  }
  function applyBig() {
    const on = bigSetting === 'on' || (bigSetting === 'auto' && trackingOn);
    document.body.classList.toggle('fr-big', on);
    foldRows(on);
    if (on && !applyBig.watching) { applyBig.watching = true; new MutationObserver(() => { if (document.body.classList.contains('fr-big')) { clearTimeout(applyBig.t); applyBig.t = setTimeout(() => foldRows(true), 120); } }).observe(document.body, { childList: true, subtree: true }); }
    dirty = true;
    if (locked && locked.el) setTimeout(() => { if (locked && locked.el && locked.el.isConnected) { const r = locked.el.getBoundingClientRect(); locked.rect = { left: r.left, top: r.top, width: r.width, height: r.height }; highlight(locked); } }, 400);
    return on;
  }
  function setTracking(on) { trackingOn = !!on; if (!on) { highlight(null); locked = null; pinchTarget = pinchDragTarget = null; snap.release(); pinch.reset(); dwell.reset(); endDrag(); } return applyBig(); }
  function applySettings(s) { if (s && s.big_mode) bigSetting = s.big_mode; return applyBig(); }
  function setBigMode(mode) { bigSetting = mode === 'on' || mode === 'off' ? mode : 'auto'; return applyBig(); }

  // ── Voice actions: next, select, back (HIG hand-cursor §2.5) ─────────────────
  function next(dir) {
    if (!ensureDom()) return { ok: false, code: 'CURSOR_NO_TARGET' };
    const list = targets(performance.now()).filter(t => t.el && targetUsable(t)).sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
    if (!list.length) return { ok: false, code: 'CURSOR_NO_TARGET' };
    let i = locked ? list.findIndex(t => t.id === locked.id) : -1;
    if (i < 0 && dir < 0) i = 0;
    i = (i + (dir < 0 ? -1 : 1) + list.length) % list.length;
    locked = list[i]; highlight(locked); snap.release();
    const cpt = Core.center(locked.rect); if (ensureDom()) { reticle.classList.remove('hidden'); reticle.style.left = cpt.x + 'px'; reticle.style.top = cpt.y + 'px'; }
    return { ok: true, code: 'CURSOR_MOVED', label: actionWord(locked) };
  }
  function select() {
    if (!locked || !targetUsable(locked)) { locked = null; highlight(null); snap.release(); return { ok: false, code: 'CURSOR_NO_TARGET' }; }
    if (locked.el ? isGuarded(locked.el) : locked.guarded) return { ok: false, code: 'CURSOR_GUARDED', label: actionWord(locked) };
    if (!clickTarget(locked, Core.center(locked.rect), performance.now())) return { ok: false, code: 'CURSOR_NO_TARGET' };
    return { ok: true, code: 'CURSOR_SELECTED', label: actionWord(locked) };
  }
  function back() { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return { ok: true, code: 'CURSOR_BACK' }; }

  document.addEventListener('DOMContentLoaded', watch);
  if (document.body) watch();

  window.FridayHandCursor = { frame, second, refresh: () => { dirty = true; }, targets: () => targets(performance.now()), has, setTracking, applySettings, setBigMode, next, select, back, isGuarded, get locked() { return locked; }, get big() { return document.body.classList.contains('fr-big'); } };
})();
