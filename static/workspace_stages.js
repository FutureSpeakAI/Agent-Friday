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

  function present(prefix) {
    return !!document.querySelector('[data-fr-ref^="' + prefix + '"]');
  }

  function sync() {
    var FS = win.fridayStage;
    if (!FS || !FS.domList || typeof document === 'undefined') return;
    Object.keys(WORKSPACES).forEach(function (id) {
      var w = WORKSPACES[id];
      var on = present(w.prefix);
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

  var timer = null;
  function soon() { if (timer) clearTimeout(timer); timer = setTimeout(sync, 400); }

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
