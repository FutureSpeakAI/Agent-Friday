/* Keys for the model switcher that is already in the top bar.

   The top bar's "Model selection" pill (docs/design/hig/README.md: the model is one of the
   top bar's eight controls) shows the current model and opens a Quick Switch list. Stephen,
   2026-11-13: "model switching ... fast and easy". The pill is one click; this file is the keys:

     Ctrl+Alt+M   open the list with the current model focused; again to close it
     1 .. 9       while it is open: pick that line of the Quick Switch list
     Up / Down    while it is open: move through the Quick Switch list
     Esc          while it is open: close it and give focus back to the pill

   It adds no control: it drives the pill and its list as they are, so a pick is the pill's own
   (the same save, the same seat gate, the same "seat health" it shows). Voice is the existing
   switch_model tool. Ctrl+Alt+<letter> is the family the tray keys use; the letter is matched by
   the character typed, so a keyboard whose AltGr (Ctrl+Alt) writes a character on that key keeps it.
   The pure parts are exported for the node tests. */
(function (root) {
  'use strict';
  var PILL = 'button[aria-label="Model selection"]';
  var MENU = '.friday-model-menu';

  function isOpenKey(e) {
    return !!e && !!e.ctrlKey && !!e.altKey && !e.shiftKey && !e.metaKey && (e.key === 'm' || e.key === 'M');
  }

  // 1..9 with no modifier, outside a text field: the line to pick (1-based), else 0.
  function digitOf(e) {
    if (!e || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return 0;
    return /^[1-9]$/.test(e.key || '') ? Number(e.key) : 0;
  }

  function inTextField(el) {
    var tag = el && el.tagName ? String(el.tagName).toUpperCase() : '';
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!(el && el.isContentEditable);
  }

  // The buttons under the "Quick Switch" heading, up to the next heading.
  function quickButtons(menu) {
    var out = [], on = false;
    var kids = (menu && menu.children) ? Array.prototype.slice.call(menu.children) : [];
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i], cls = String(k.className || '');
      if (cls.indexOf('model-selector-tier') >= 0) {
        on = String(k.textContent || '').trim().toLowerCase() === 'quick switch';
        continue;
      }
      if (on && String(k.tagName || '').toUpperCase() === 'BUTTON') out.push(k);
    }
    return out;
  }

  function currentOf(buttons) {
    for (var i = 0; i < buttons.length; i++) {
      if (String(buttons[i].title || '') === 'Current orchestrator') return buttons[i];
    }
    return buttons[0] || null;
  }

  function wire(doc) {
    function pill() { return doc.querySelector(PILL); }
    function menu() { return doc.querySelector(MENU); }
    function close() { var p = pill(); if (menu() && p) { p.click(); p.focus && p.focus(); } }

    function onKey(e) {
      if (isOpenKey(e)) {
        var p = pill();
        if (!p) return;
        e.preventDefault();
        if (menu()) { close(); return; }
        p.click();
        // The list renders on the next frame; focus the current model so Enter keeps it and a digit or arrow moves.
        (root.setTimeout || setTimeout)(function () {
          var cur = currentOf(quickButtons(menu()));
          if (cur && cur.focus) cur.focus();
        }, 30);
        return;
      }
      var m = menu();
      if (!m) return;
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (inTextField(e.target)) return;
      var n = digitOf(e);
      if (n) {
        var btn = quickButtons(m)[n - 1];
        if (btn) { e.preventDefault(); btn.click(); }
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        var list = quickButtons(m);
        if (!list.length) return;
        var at = list.indexOf(doc.activeElement);
        var next = e.key === 'ArrowDown' ? (at + 1) % list.length : (at <= 0 ? list.length - 1 : at - 1);
        e.preventDefault();
        list[next].focus && list[next].focus();
      }
    }
    doc.addEventListener('keydown', onKey, true);
    return onKey;
  }

  var api = { isOpenKey: isOpenKey, digitOf: digitOf, quickButtons: quickButtons, currentOf: currentOf, wire: wire };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root && root.document) { root.fridayModelShortcut = api; wire(root.document); }
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
