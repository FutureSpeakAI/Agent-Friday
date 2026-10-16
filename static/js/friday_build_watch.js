/* Friday build watch: "Agent Friday was updated, reload to get the new version."

   An open tab keeps running the JavaScript it loaded. After an update and a
   restart the server serves new files, but the tab still runs the old ones
   until someone reloads it, and nothing told them to (field install report,
   Part 1 #10 and Part 2 #9).

   The server stamps every page it serves with the build it came from
   (window.__FRIDAY_BUILD_ID__, see routes/core_routes._build_id_script) and
   reports the same id as `build_id` in /api/health. When a later health
   answer carries a DIFFERENT id, this shows one calm notice with a Reload
   button. It never reloads by itself: someone may be halfway through a
   message, and a reload would throw that away. Reload is theirs to press.

   Fed by the top bar's health ping (check(id)); it also asks /api/health
   itself when the tab comes back (visible, online) or when nobody has fed it
   for a while, so a workspace opened as its own tab is covered too. */
(function (root) {
  'use strict';

  var POLL_MS = 60000;      // self-poll cadence when nothing feeds us
  var FED_FRESH_MS = 45000; // a feed this recent makes a self-poll redundant
  var MIN_GAP_MS = 10000;   // never poll more often than this

  var loaded = (typeof root.__FRIDAY_BUILD_ID__ === 'string' && root.__FRIDAY_BUILD_ID__) || null;
  var state = { loaded: loaded, seen: null, shownFor: null, dismissedFor: null, lastFed: 0, lastPoll: 0 };

  // The notice names the application, so it carries the mark: the page's
  // FRIDAY_BRAND.name (brand.PAGE_NAMES), never the unmarked .product.
  function productName() {
    var b = root.FRIDAY_BRAND || {};
    return b.name || 'Agent Friday™';
  }

  function reducedMotion() {
    try {
      return !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) { return false; }
  }

  // Unsent text on the page: the chat composer or any other free-text box.
  // Advisory only (it adds a line to the notice); single-line settings inputs
  // are left out because they are usually pre-filled, not drafts.
  function hasDraft() {
    try {
      var doc = root.document;
      if (!doc || !doc.querySelectorAll) return false;
      var fields = doc.querySelectorAll('textarea, [contenteditable="true"]');
      for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        var v = (f.value != null ? f.value : f.textContent) || '';
        if (String(v).trim()) return true;
      }
    } catch (e) { /* a check that throws must not hide the notice */ }
    return false;
  }

  function toastHost(doc) {
    var host = doc.getElementById('friday-toast-host');
    if (!host) {
      // The same host, place and look as fridayToast (index.html), so the
      // notice is Friday's one toast surface rather than a second one.
      host = doc.createElement('div');
      host.id = 'friday-toast-host';
      host.setAttribute('data-friday-overlay', 'toast');
      host.setAttribute('aria-label', 'Notifications');
      host.style.cssText = 'position:fixed;z-index:99999;right:16px;bottom:16px;' +
        'display:flex;flex-direction:column;gap:8px;align-items:flex-end;' +
        'pointer-events:none;max-width:min(520px,calc(100vw - 32px));max-height:40vh;overflow:auto';
      doc.body.appendChild(host);
    }
    return host;
  }

  function button(doc, label, aria) {
    var b = doc.createElement('button');
    b.type = 'button';
    b.textContent = label;
    if (aria) b.setAttribute('aria-label', aria);
    b.style.cssText = 'margin-left:10px;min-height:28px;padding:2px 10px;cursor:pointer;' +
      'font:inherit;border-radius:5px;background:transparent;color:inherit;' +
      'border:1px solid currentColor';
    return b;
  }

  function hide() {
    var doc = root.document;
    var el = doc && doc.getElementById && doc.getElementById('friday-build-notice');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function show(newId) {
    var doc = root.document;
    if (!doc || !doc.body) return;
    hide();
    var el = doc.createElement('div');
    el.id = 'friday-build-notice';
    // Polite: announced when the reader is idle, never interrupting.
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.setAttribute('data-build-id', newId);
    var calm = reducedMotion();
    el.style.cssText = 'pointer-events:auto;font-family:var(--fr-font-mono, JetBrains Mono, monospace);' +
      'font-size:var(--fr-text-md, 12px);line-height:1.5;padding:10px 13px;border-radius:8px;' +
      'color:var(--fr-text, #e8eef8);background:var(--fr-surface, #0a0e1a);' +
      'border:1px solid var(--fr-cyan, #00d4ff);box-shadow:0 8px 28px rgba(0,0,0,0.45);' +
      (calm ? '' : 'opacity:0;transform:translateY(6px);transition:opacity .16s,transform .16s');
    var msg = doc.createElement('span');
    msg.textContent = productName() + ' was updated — reload to get the new version.';
    el.appendChild(msg);
    if (hasDraft()) {
      var note = doc.createElement('div');
      note.style.cssText = 'color:var(--fr-dim, rgba(255,255,255,0.46));margin-top:4px';
      note.textContent = 'You have unsent text on this page; reloading clears it, so send or copy it first.';
      el.appendChild(note);
    }
    var reload = button(doc, 'Reload', 'Reload to get the new version');
    reload.onclick = function () { try { root.location.reload(); } catch (e) {} };
    var later = button(doc, 'Later', 'Dismiss the update notice');
    later.onclick = function () { state.dismissedFor = newId; hide(); };
    el.appendChild(reload);
    el.appendChild(later);
    toastHost(doc).appendChild(el);
    state.shownFor = newId;
    if (!calm && root.requestAnimationFrame) {
      root.requestAnimationFrame(function () { el.style.opacity = '1'; el.style.transform = 'translateY(0)'; });
    }
  }

  // The one decision: a known id from the server that is not the id this
  // page loaded with. Unknown on either side means "cannot tell", never
  // "updated": a server that cannot hash its files must not nag.
  function check(id) {
    state.lastFed = Date.now();
    if (typeof id !== 'string' || !id || !state.loaded) return false;
    state.seen = id;
    if (id === state.loaded) { if (state.shownFor) hide(); state.shownFor = null; return false; }
    if (id === state.dismissedFor || id === state.shownFor) return true;
    show(id);
    return true;
  }

  function poll(force) {
    var now = Date.now();
    if (!state.loaded || typeof root.fetch !== 'function') return;
    if (!force && now - state.lastPoll < MIN_GAP_MS) return;
    state.lastPoll = now;
    var headers = {};
    if (root.__FRIDAY_API_TOKEN) headers['X-Friday-Token'] = root.__FRIDAY_API_TOKEN;
    root.fetch('/api/health', { cache: 'no-store', headers: headers })
      .then(function (r) { return r.json(); })
      .then(function (d) { check(d && d.build_id); })
      .catch(function () { /* down or restarting: the next poll will see it */ });
  }

  function onReturn() {
    if (!root.document || root.document.visibilityState !== 'hidden') poll(false);
  }

  if (root.addEventListener) {
    root.addEventListener('online', onReturn);
    root.addEventListener('focus', onReturn);
  }
  if (root.document && root.document.addEventListener) {
    root.document.addEventListener('visibilitychange', onReturn);
  }
  if (root.setInterval) {
    root.setInterval(function () {
      if (Date.now() - state.lastFed >= FED_FRESH_MS) poll(false);
    }, POLL_MS);
  }

  root.fridayBuildWatch = { check: check, poll: poll, state: state, hasDraft: hasDraft };
})(typeof window !== 'undefined' ? window : this);
