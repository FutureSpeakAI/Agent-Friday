/* Native workspace presentation helpers. Data and actions remain with their components. */
(function () {
  'use strict';
  if (window.FridayWorkspaceCompositions) return;

  const menuSelector = '.ws-custom-root[data-ws="media"] .md-filter-menu';
  const overlaySelector = '.ws-custom-root :is(.news-backdrop,.news-side,.kw-modal,.md-ql,.fm-dialog,.fm-help,.fm-preview,.fm-compose,.fm-toast,.fr-comp-system-modal,.lb-pop,.fm-menu)';
  const floatingSelector = '.lb-pop,.fm-menu';
  const tracked = new Map();
  const listeners = new AbortController();
  let frame = 0;
  let destroyed = false;
  let treeObserver = null;
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(schedule) : null;
  let observed = new Set();

  function record(element) {
    if (!tracked.has(element)) tracked.set(element, {
      original: new Map(), written: new Map(), lastStyle: element.style.cssText,
      shift: {x: 0, y: 0}, occluded: element.getAttribute('data-fr-overlay-occluded')
    });
    return tracked.get(element);
  }
  function writeStyle(element, name, value) {
    const state = record(element);
    if (!state.original.has(name)) state.original.set(name, [element.style.getPropertyValue(name), element.style.getPropertyPriority(name)]);
    if (element.style.getPropertyValue(name) !== value) element.style.setProperty(name, value);
    state.written.set(name, element.style.getPropertyValue(name));
    state.lastStyle = element.style.cssText;
  }
  function release(element) {
    const state = tracked.get(element);
    if (!state) return;
    state.original.forEach(([value, priority], name) => {
      if (element.style.getPropertyValue(name) !== state.written.get(name)) return;
      if (value) element.style.setProperty(name, value, priority); else element.style.removeProperty(name);
    });
    if (state.occluded == null) element.removeAttribute('data-fr-overlay-occluded');
    else element.setAttribute('data-fr-overlay-occluded', state.occluded);
    tracked.delete(element);
  }
  function boxOf(element) {
    const rect = element.getBoundingClientRect();
    // Native window entry/resize effects translate and scale their stable plane.
    const sx = element.offsetWidth > 0 ? rect.width / element.offsetWidth : 1;
    const sy = element.offsetHeight > 0 ? rect.height / element.offsetHeight : 1;
    return {rect, sx: Number.isFinite(sx) && sx > 0 ? sx : 1, sy: Number.isFinite(sy) && sy > 0 ? sy : 1};
  }
  function exposed(element) {
    if (!element.isConnected || !element.getClientRects().length || element.closest('[hidden],[inert]')) return false;
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (style.visibility === 'hidden' || style.visibility === 'collapse' || style.display === 'none') return false;
    }
    return true;
  }

  // CSS containers, scrolling frames and the avatar stage can all constrain an
  // overlay. Use their actual intersection instead of assuming the viewport.
  function visibleBounds(root, element) {
    const r = root.getBoundingClientRect();
    const out = {left: Math.max(0, r.left), top: Math.max(0, r.top), right: Math.min(innerWidth, r.right), bottom: Math.min(innerHeight, r.bottom)};
    if (document.body.dataset.fridaySpatialLayout) {
      const style = getComputedStyle(document.body);
      const inset = side => Math.max(0, parseFloat(style.getPropertyValue('--friday-content-' + side)) || 0);
      out.left = Math.max(out.left, inset('left'));
      out.top = Math.max(out.top, inset('top'));
      out.right = Math.min(out.right, innerWidth - inset('right'));
      out.bottom = Math.min(out.bottom, innerHeight - inset('bottom'));
    }
    for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (!/(auto|scroll|hidden|clip)/.test(style.overflowX + style.overflowY)) continue;
      const {rect, sx, sy} = boxOf(parent);
      if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
        out.left = Math.max(out.left, rect.left + parent.clientLeft * sx);
        out.right = Math.min(out.right, rect.left + (parent.clientLeft + parent.clientWidth) * sx);
      }
      if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
        out.top = Math.max(out.top, rect.top + parent.clientTop * sy);
        out.bottom = Math.min(out.bottom, rect.top + (parent.clientTop + parent.clientHeight) * sy);
      }
    }
    return {...out, width: Math.max(0, out.right - out.left), height: Math.max(0, out.bottom - out.top)};
  }

  function fitOverlay(element) {
    const root = element.closest('.ws-custom-root');
    if (!root) return;
    const state = record(element);
    if (!exposed(element)) {
      element.setAttribute('data-fr-overlay-occluded', 'true');
      return;
    }
    const bounds = visibleBounds(root, element);
    if (bounds.width < 24 || bounds.height < 24) {
      element.setAttribute('data-fr-overlay-occluded', 'true');
      return;
    }
    element.removeAttribute('data-fr-overlay-occluded');
    const parent = element.offsetParent || root;
    const {rect, sx, sy} = boxOf(parent);
    const left = (bounds.left - rect.left) / sx - parent.clientLeft + parent.scrollLeft;
    const top = (bounds.top - rect.top) / sy - parent.clientTop + parent.scrollTop;
    const values = {left, top, width: bounds.width / sx, height: bounds.height / sy};
    Object.entries(values).forEach(([key, value]) => {
      const name = '--fr-overlay-' + key, next = Math.round(value * 100) / 100 + 'px';
      writeStyle(element, name, next);
    });
    if (element.matches(floatingSelector)) {
      // Keep native popup anchoring and transforms, adding only a bounded shift.
      const visual = boxOf(element), box = visual.rect;
      const x = box.left - state.shift.x * visual.sx, y = box.top - state.shift.y * visual.sy;
      // Messages supplies viewport click coordinates even inside a CSS container.
      const desiredX = element.matches('.fm-menu') ? parseFloat(element.style.left) : x;
      const desiredY = element.matches('.fm-menu') ? parseFloat(element.style.top) : y;
      const dx = (Math.max(bounds.left + 8, Math.min(Number.isFinite(desiredX) ? desiredX : x, bounds.right - box.width - 8)) - x) / visual.sx;
      const dy = (Math.max(bounds.top + 8, Math.min(Number.isFinite(desiredY) ? desiredY : y, bounds.bottom - box.height - 8)) - y) / visual.sy;
      writeStyle(element, 'translate', dx + 'px ' + dy + 'px');
      state.shift = {x: dx, y: dy};
    }
  }
  function refresh() {
    frame = 0;
    if (destroyed) return;
    const openMenus = menus();
    const overlays = Array.from(document.querySelectorAll(overlaySelector));
    const current = new Set([...openMenus, ...overlays]);
    [...tracked.keys()].filter(el => !current.has(el)).forEach(release);
    openMenus.forEach(sizeMenu);
    overlays.forEach(fitOverlay);
    const next = new Set([...current, ...[...current].map(el => el.closest('.ws-custom-root')).filter(Boolean)]);
    if (resizeObserver && (next.size !== observed.size || [...next].some(el => !observed.has(el)))) {
      resizeObserver.disconnect();
      next.forEach(el => resizeObserver.observe(el));
      observed = next;
    }
  }
  function schedule() { if (!destroyed && !frame) frame = requestAnimationFrame(refresh); }
  function affectsOverlay(target) {
    return target instanceof Element && [...tracked.keys()].some(element => target === element || target.contains(element));
  }
  function menus() { return Array.from(document.querySelectorAll(menuSelector + '[open]')); }
  function sizeMenu(menu) {
    const summary = menu.querySelector('summary');
    const main = menu.closest('.md-main');
    if (!summary || !main || !menu.open || !exposed(menu)) return;
    const toolbar = menu.closest('.md-toolbar') || summary;
    const {rect, sy} = boxOf(toolbar);
    const top = rect.bottom;
    const root = menu.closest('.ws-custom-root');
    const bottom = Math.min(main.getBoundingClientRect().bottom, root ? visibleBounds(root, menu).bottom : window.innerHeight - 16);
    writeStyle(menu, '--fr-filter-menu-height', Math.max(0, Math.min(440, (bottom - top) / sy - 12)) + 'px');
  }
  function closeMenu(menu, focus) {
    menu.open = false;
    release(menu);
    if (focus) menu.querySelector('summary')?.focus();
  }
  document.addEventListener('toggle', event => {
    if (event.target instanceof Element && event.target.matches(menuSelector)) {
      if (event.target.open) {
        menus().filter(menu => menu !== event.target).forEach(menu => closeMenu(menu, false));
        sizeMenu(event.target);
      }
    }
    schedule();
  }, {capture: true, signal: listeners.signal});
  document.addEventListener('pointerdown', event => {
    menus().forEach(menu => { if (!menu.contains(event.target)) closeMenu(menu, false); });
  }, {capture: true, signal: listeners.signal});
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target || target.closest('[role="dialog"]')) return;
    const workspace = target.closest('.ws-custom-root[data-ws="media"]');
    const menu = target.closest(menuSelector + '[open]') || workspace?.querySelector('.md-filter-menu[open]');
    if (!menu) return;
    event.preventDefault();
    event.stopPropagation();
    closeMenu(menu, true);
  }, {capture: true, signal: listeners.signal});
  window.addEventListener('resize', schedule, { passive: true, signal: listeners.signal });
  window.addEventListener('scroll', schedule, { passive: true, capture: true, signal: listeners.signal });
  window.addEventListener('friday:spatial-layout', schedule, { signal: listeners.signal });
  document.addEventListener('visibilitychange', schedule, { signal: listeners.signal });
  // Transforms can finish without a layout-size change or a DOM mutation.
  for (const type of ['animationend', 'animationcancel', 'transitionend', 'transitioncancel']) {
    document.addEventListener(type, event => { if (affectsOverlay(event.target)) schedule(); }, {capture: true, signal: listeners.signal});
  }
  function watch() {
    if (!document.body || destroyed) return;
    treeObserver = new MutationObserver(changes => {
      // Release detached nodes immediately, even when a background tab defers RAF.
      [...tracked.keys()].filter(element => !element.isConnected).forEach(release);
      for (const element of observed) {
        if (!element.isConnected) { resizeObserver?.unobserve(element); observed.delete(element); }
      }
      const relevant = changes.some(change => {
        if (change.type === 'childList') return true;
        const target = change.target, state = tracked.get(target);
        // Our own style writes are idempotent and do not schedule another fit.
        if (change.attributeName === 'style' && state?.lastStyle === target.style.cssText) return false;
        if (target === document.body) return true;
        return affectsOverlay(target);
      });
      if (relevant) schedule();
    });
    treeObserver.observe(document.body, { childList: true, subtree: true, attributes: true,
      attributeFilter: ['hidden', 'inert', 'class', 'style', 'data-friday-spatial-layout', 'data-friday-home-visible'] });
    schedule();
  }
  function destroy() {
    if (destroyed) return;
    destroyed = true;
    cancelAnimationFrame(frame); frame = 0;
    listeners.abort(); treeObserver?.disconnect(); resizeObserver?.disconnect();
    [...tracked.keys()].forEach(release); observed.clear();
    delete window.FridayWorkspaceCompositions;
  }
  if (document.body) watch(); else document.addEventListener('DOMContentLoaded', watch, { once: true, signal: listeners.signal });
  window.FridayWorkspaceCompositions = Object.freeze({ refresh: schedule, destroy });
})();
