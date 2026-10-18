/* The stage of the workspaces that used to be open-only (window.fridayWorkspaceStages).
 *
 * Calendar, Workflows, People, Career, Trust, Sites, System, the Chat Hub's conversations and the counts-only
 * Health, Finance and Family have no selection state of their own and never needed any. Each of their rows
 * carries data-fr-ref="<kind>:<id>" (and data-fr-title / data-fr-facets where the workspace may publish them);
 * this file notices when such rows are on the page and gives the workspace a stage through
 * friday_stage.js domList, so Friday can see the list, point at rows and (where it makes sense) tick them, with
 * the same rules as every other list: bounded, data not instructions, memory only, and for Health, Finance and
 * Family counts and kinds only, never a title.
 *
 * To add a workspace: give its rows data-fr-ref, add it to WORKSPACES below, and allow its facets in
 * services/screen_stage.py STAGE_FACETS. Nothing here changes anything: ticking shows, it never acts.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(root);
  else root.fridayWorkspaceStages = factory(root);
}(typeof self !== 'undefined' ? self : this, function (win) {
  'use strict';

  // window id -> what its rows are called in a ref, whether Friday may tick them, whether only counts leave
  var WORKSPACES = {
    calendar: { prefix: 'event:', selectable: true },
    meetings: { prefix: 'meeting:', selectable: true, counts_only: true },
    chat: { prefix: 'convo:', selectable: true },
    workflows: { prefix: 'wf:' },
    contacts: { prefix: 'person:' },
    career: { prefix: 'job:' },
    trust: { prefix: 'trust:' },
    futurespeak: { prefix: 'site:' },
    system: { prefix: 'card:' },
    health: { prefix: 'health:', counts_only: true },
    finance: { prefix: 'fin:', counts_only: true },
    family: { prefix: 'fam:', counts_only: true }
  };

  var live = {};      // ws -> off()

  // The kinds of row on the page now ("event:", "health:", ...), from one pass over the marked rows.
  function presentKinds() {
    var kinds = {};
    var els = document.querySelectorAll('[data-fr-ref]');
    for (var i = 0; i < els.length; i++) {
      var ref = String(els[i].getAttribute('data-fr-ref') || '');
      kinds[ref.slice(0, ref.indexOf(':') + 1)] = true;
    }
    return kinds;
  }

  function sync() {
    var FS = win.fridayStage;
    if (!FS || !FS.domList || typeof document === 'undefined') return;
    var kinds = presentKinds();
    Object.keys(WORKSPACES).forEach(function (id) {
      var w = WORKSPACES[id];
      var on = !!kinds[w.prefix];
      if (on && !live[id]) {
        live[id] = FS.domList(id, {
          rows: '[data-fr-ref^="' + w.prefix + '"]', selectable: !!w.selectable, counts_only: !!w.counts_only,
          root: function () { return document.body; }
        });
      } else if (!on && live[id]) {
        live[id]();
        delete live[id];
      }
    });
  }

  // Did a change add or remove a marked row (or something holding one)? Text and unmarked elements
  // changing (a clock, a status line) do not make the registrar look.
  function marked(node) {
    return !!(node && node.nodeType === 1 && ((node.hasAttribute && node.hasAttribute('data-fr-ref'))
      || (node.querySelector && node.querySelector('[data-fr-ref]'))));
  }
  function touchesRows(records) {
    for (var i = 0; i < (records || []).length; i++) {
      var r = records[i], j;
      for (j = 0; j < (r.addedNodes || []).length; j++) if (marked(r.addedNodes[j])) return true;
      for (j = 0; j < (r.removedNodes || []).length; j++) if (marked(r.removedNodes[j])) return true;
    }
    return false;
  }

  // At most SYNC_MS after the first change to the rows, however busy the page is. A timer that restarted
  // on every mutation never fired on a page that keeps changing (a clock, a status line, a live list):
  // rows that arrived then never got their stage.
  var SYNC_MS = 400;
  var timer = null;
  function soon(records) {
    if (timer || !touchesRows(records)) return;
    timer = setTimeout(function () { timer = null; sync(); }, SYNC_MS);
  }

  function start() {
    if (typeof MutationObserver !== 'function' || typeof document === 'undefined') return;
    new MutationObserver(soon).observe(document.body, { childList: true, subtree: true });
    sync();
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
  }

  return { WORKSPACES: WORKSPACES, sync: sync, live: function () { return Object.keys(live); } };
}));
