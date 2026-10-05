/* See & Touch: the shared layer every workspace plugs into so Friday can see and mark what is on screen.
 *
 * A workspace registers an adapter; the page reports the adapter's stage with its state, and the
 * server's commands (select, clear_selection, stage_request, held) are run through it. The
 * selection reducer here is pure (no DOM, no React) and shared by every list, so it is tested in node.
 *
 * Rules held here:
 *   - one selection, one look: Friday's ticks and the owner's ticks are the same ticks; `source` says
 *     who made them and the chip says it in words;
 *   - the stage is bounded (<= 120 rows, <= 500 refs) and goes only to this server, never anywhere else;
 *   - motion is opacity and scale only, the whole sweep ends inside 350 ms, a reduced-motion page gets
 *     a 120 ms fade, and no element changes state more than three times a second.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.fridayStage = factory();
}(typeof window !== 'undefined' ? window : this, function () {
  'use strict';

  var MAX_REFS = 500;
  var MAX_ROWS = 120;
  var SWEEP_STAGGER_MS = 210;       // the last row starts at most this late
  var SWEEP_ROW_MS = 140;           // and takes this long: 210 + 140 = 350
  var MIN_GAP_MS = 334;             // one state change per element per 334 ms (the reticle's limit)

  // ── the selection reducer ──────────────────────────────────────────────────

  function emptySelection() {
    return { refs: [], id: '', label: '', source: '', rev: 0, held: {} };
  }

  function uniq(list) {
    var seen = {}, out = [];
    for (var i = 0; i < list.length; i++) {
      var r = String(list[i] || '');
      if (r && !seen[r]) { seen[r] = 1; out.push(r); }
    }
    return out;
  }

  function copy(s) {
    return { refs: s.refs.slice(), id: s.id, label: s.label, source: s.source, rev: s.rev, held: Object.assign({}, s.held) };
  }

  function count(known, refs) {
    var set = {}, n = 0, i;
    for (i = 0; i < known.length; i++) set[known[i]] = 1;
    for (i = 0; i < refs.length; i++) if (set[refs[i]]) n++;
    return n;
  }

  // An empty selection has no name and no author. What a pending card holds is separate from the
  // ticks: it stays held until the card is decided, however the owner moves around the list.
  function tidy(next) {
    if (!next.refs.length) { next.id = ''; next.label = ''; next.source = ''; }
  }

  /* reduceSelection(state, evt, known) -> { state, applied, missing, accepted, count, changed }
   *   known: the refs of the rows the list renders now.
   *   applied: ticked refs that are on the list; missing: ticked refs the list does not show;
   *   accepted: how many of the event's refs the selection took (at most MAX_REFS).
   * Events: select {mode: replace|add|remove, refs, id, label}, toggle {ref}, owner_set {refs}, clear,
   *   owner_nav (the owner changed folder or search: ticks go, as they always did), friday_reveal (a
   *   reveal Friday made: ticks stay), held {state: held|done|declined|expired, refs, card_id}, rows_gone {refs}. */
  function reduceSelection(state, evt, known) {
    known = known || [];
    var prev = state || emptySelection();
    var next = copy(prev);
    var accepted = 0;
    var hadPrior = prev.refs.length > 0;
    var changed = true;
    switch (evt.type) {
      case 'select': {
        var asked = uniq(evt.refs || []);
        var refs = asked.slice(0, MAX_REFS);
        accepted = refs.length;
        var mode = evt.mode || 'replace';
        if (mode === 'replace') {
          next.refs = refs;
          next.id = evt.id || '';
          next.label = evt.label || '';
          next.source = 'friday';
        } else if (mode === 'add') {
          next.refs = uniq(prev.refs.concat(refs)).slice(0, MAX_REFS);
          if (!hadPrior) { next.id = evt.id || ''; next.label = evt.label || ''; next.source = 'friday'; }
          else if (prev.source !== 'friday') next.source = 'mixed';
        } else if (mode === 'remove') {
          var drop = {}; refs.forEach(function (r) { drop[r] = 1; });
          next.refs = prev.refs.filter(function (r) { return !drop[r]; });
        }
        tidy(next);
        break;
      }
      case 'toggle': {
        var r0 = String(evt.ref || '');
        var at = prev.refs.indexOf(r0);
        next.refs = at >= 0 ? prev.refs.filter(function (r) { return r !== r0; }) : prev.refs.concat([r0]);
        if (!hadPrior) { next.id = ''; next.label = ''; next.source = 'owner'; }
        else if (prev.source === 'friday' || prev.source === 'mixed') next.source = 'mixed';
        tidy(next);
        break;
      }
      case 'owner_set': {
        next.refs = uniq(evt.refs || []).slice(0, MAX_REFS);
        next.id = ''; next.label = ''; next.source = next.refs.length ? 'owner' : '';
        tidy(next);
        break;
      }
      case 'clear':
      case 'owner_nav': {
        if (!hadPrior) { changed = false; break; }
        next.refs = []; tidy(next);
        break;
      }
      case 'friday_reveal':
        changed = false;
        break;
      case 'held': {
        var rs = uniq(evt.refs || []);
        if (evt.state === 'held') {
          rs.forEach(function (r) { next.held[r] = evt.card_id || 'card'; });
        } else if (evt.state === 'done') {
          var gone = {}; rs.forEach(function (r) { gone[r] = 1; delete next.held[r]; });
          next.refs = prev.refs.filter(function (r) { return !gone[r]; });
          tidy(next);
        } else {                                    // declined, expired: the ticks stay
          rs.forEach(function (r) { delete next.held[r]; });
        }
        break;
      }
      case 'rows_gone': {
        var gs = {}; (evt.refs || []).forEach(function (r) { gs[r] = 1; delete next.held[r]; });
        next.refs = prev.refs.filter(function (r) { return !gs[r]; });
        tidy(next);
        break;
      }
      default:
        changed = false;
    }
    if (changed && JSON.stringify([prev.refs, prev.held, prev.id, prev.label, prev.source]) ===
        JSON.stringify([next.refs, next.held, next.id, next.label, next.source])) changed = false;
    if (changed) next.rev = prev.rev + 1;
    var applied = count(known, next.refs);
    return { state: changed ? next : prev, applied: applied, missing: next.refs.length - applied,
             accepted: accepted, count: next.refs.length, changed: changed };
  }

  // The chip's words: "Newsletters · 142 · Friday selected", "(edited)" once the owner changed it.
  function chipText(state, brand) {
    if (!state || !state.refs.length) return '';
    var name = brand || 'Friday';
    var who = state.source === 'friday' ? (name + ' selected')
      : state.source === 'mixed' ? (state.label ? (name + ' selected') : ('You and ' + name + ' selected')) : 'You selected';
    var label = state.label ? state.label + (state.source === 'mixed' ? ' (edited)' : '') + ' · ' : '';
    return '✓ ' + label + state.refs.length + ' · ' + who;
  }

  // ── motion ────────────────────────────────────────────────────────────────

  // Delays (ms) for a top-to-bottom sweep over n rows: the last row starts by SWEEP_STAGGER_MS, so the
  // whole sweep ends within SWEEP_STAGGER_MS + SWEEP_ROW_MS = 350 ms however many rows there are.
  function sweepDelays(n) {
    var out = [];
    for (var i = 0; i < n; i++) out.push(n <= 1 ? 0 : Math.round(i * SWEEP_STAGGER_MS / (n - 1)));
    return out;
  }

  // ≤ 1 state change per element per MIN_GAP_MS: remembers when each key last changed.
  function limiter(now) {
    var last = {};
    return function allow(key) {
      var t = (now || Date.now)();
      if (last[key] != null && t - last[key] < MIN_GAP_MS) return false;
      last[key] = t;
      return true;
    };
  }

  // ── the page side: adapters, stage reports, commands ─────────────────────

  var adapters = {};
  var commandTypes = { select: 1, clear_selection: 1, stage_request: 1, held: 1 };

  // register(ws, { stage(), run(action) -> Promise<result> }): returns the unregister function.
  function register(ws, adapter) {
    adapters[ws] = adapter;
    return function () { if (adapters[ws] === adapter) delete adapters[ws]; };
  }

  function registered(ws) { return !!adapters[ws]; }

  // The stage of one workspace for the state report; null when it is not on the page.
  function snapshot(ws) {
    var a = adapters[ws];
    if (!a) return null;
    try { return a.stage(); } catch (e) { return null; }
  }

  function handles(type) { return !!commandTypes[type]; }

  // run(action): the answer the server's ack carries: {result, stage}.
  function run(action) {
    var ws = (action && action.workspace) || 'messages';
    var a = adapters[ws];
    if (!a) return Promise.resolve({ result: { ok: false, reason: 'not open' }, stage: null });
    return Promise.resolve(a.run(action)).then(function (result) {
      var st = null;
      try { st = a.stage(); } catch (e) { /* the result still stands */ }
      return { result: result, stage: st };
    }, function (e) {
      return { result: { ok: false, reason: String(e && e.message || e) }, stage: null };
    });
  }

  // Ask the page to send its stage soon (after the owner changes something).
  function touch(ms) {
    if (typeof window !== 'undefined' && typeof window.fridayDeskSoon === 'function') window.fridayDeskSoon(ms == null ? 250 : ms);
  }

  return {
    MAX_REFS: MAX_REFS, MAX_ROWS: MAX_ROWS, SWEEP_STAGGER_MS: SWEEP_STAGGER_MS, SWEEP_ROW_MS: SWEEP_ROW_MS,
    MIN_GAP_MS: MIN_GAP_MS,
    emptySelection: emptySelection, reduceSelection: reduceSelection, chipText: chipText,
    sweepDelays: sweepDelays, limiter: limiter,
    register: register, registered: registered, snapshot: snapshot, handles: handles, run: run, touch: touch
  };
}));
