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
  var POINT_CAP = 12;               // rows outlined and numbered at once; the rest are counted
  var POINT_FADE_MS = 12000;        // the outlines fade after this, or on the owner's next input

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

  // ── pointing: the reticle's locked outline and a numbered badge ─────────────

  // Which refs get a badge (the first POINT_CAP) and how many more are only counted.
  function pointPlan(refs, cap) {
    var list = (refs || []).filter(function (r) { return r; });
    var n = cap || POINT_CAP;
    return { badged: list.slice(0, n), more: Math.max(0, list.length - n) };
  }

  // Brand tokens only (--fr-*). The ring is an outline whose colour fades in once (no opacity or scale
  // on the row itself, so its text never flickers); the badge is an 18 px ring holding a numeral.
  var STYLE = [
    '[data-fr-ref]{outline:2px solid transparent;outline-offset:-2px;transition:outline-color var(--fr-reveal-time,0.35s)}',
    '[data-fr-point]{outline-color:var(--fr-cyan)}',
    '[data-fr-held]{box-shadow:inset 2px 0 0 var(--fr-warn)}',
    '[data-fr-sel]{background-image:linear-gradient(var(--fr-cyan-soft),var(--fr-cyan-soft));box-shadow:inset 2px 0 0 var(--fr-cyan)}',
    '[data-fr-sel][data-fr-held]{box-shadow:inset 2px 0 0 var(--fr-warn)}',
    '[data-fr-point][data-fr-n]::after{content:attr(data-fr-n);position:absolute;top:6px;right:8px;z-index:2;width:18px;height:18px;',
    'box-sizing:border-box;border:1.5px solid var(--fr-cyan);border-radius:50%;background:var(--fr-surface);color:var(--fr-cyan);',
    'font:600 10px/15px var(--fr-font-mono,monospace);text-align:center;pointer-events:none}',
    '.fr-chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center}',
    '.fr-chip{display:inline-flex;align-items:center;gap:6px;padding:2px 4px 2px 10px;border-radius:999px;border:1px solid var(--fr-cyan);',
    'background:var(--fr-cyan-soft);font-size:11px;color:var(--fr-text);white-space:nowrap}',
    '.fr-chip .by{color:var(--fr-dim)}',
    '.fr-chip .needs-you{color:var(--fr-warn)}',
    '.fr-chip button{background:none;border:0;color:inherit;cursor:pointer;padding:0 6px;font-size:12px;line-height:1;border-radius:999px}',
    '.fr-chip button:hover{background:var(--fr-cyan-soft)}'
  ].join('');
  var styled = false;
  function ensureStyle() {
    if (styled || typeof document === 'undefined') return;
    styled = true;
    var el = document.createElement('style');
    el.id = 'fr-stage-style';
    el.textContent = STYLE;
    (document.head || document.documentElement).appendChild(el);
  }

  var pointing = { els: [], refs: [], id: '', timer: null, stop: null };

  function clearPoints() {
    pointing.els.forEach(function (el) {
      el.removeAttribute('data-fr-point');
      el.removeAttribute('data-fr-n');
      if (el.__frPos != null) { el.style.position = el.__frPos; delete el.__frPos; }
    });
    if (pointing.timer) clearTimeout(pointing.timer);
    if (pointing.stop) pointing.stop();
    pointing = { els: [], refs: [], id: '', timer: null, stop: null };
  }

  function esc(ref) {
    return (typeof CSS !== 'undefined' && CSS.escape) ? CSS.escape(ref) : String(ref).replace(/["\\]/g, '\\$&');
  }

  // point(refs, {root, badges, id}) -> {ok, count}: count is the rows actually on screen, which is what
  // the server reports. The outlines go on the next owner input or after 12 s.
  function point(refs, opts) {
    opts = opts || {};
    ensureStyle();
    clearPoints();
    var root = opts.root || document;
    var plan = pointPlan(refs);
    var els = [];
    plan.badged.forEach(function (r, i) {
      var el = root.querySelector('[data-fr-ref="' + esc(r) + '"]');
      if (!el) return;
      els.push(el);
      el.setAttribute('data-fr-point', 'on');
      if (opts.badges !== 'none') el.setAttribute('data-fr-n', String(i + 1));
      if (getComputedStyle(el).position === 'static') { el.__frPos = el.style.position; el.style.position = 'relative'; }
    });
    pointing.els = els; pointing.refs = plan.badged.slice(); pointing.id = opts.id || '';
    if (els[0] && els[0].scrollIntoView) {
      var calm = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
      els[0].scrollIntoView({ block: 'nearest', behavior: calm ? 'auto' : 'smooth' });
    }
    pointing.timer = setTimeout(clearPoints, POINT_FADE_MS);
    var gone = function () { clearPoints(); };
    // the owner's next input ends it, but not the command's own repaint
    var gone = function () { clearPoints(); };
    var arm = setTimeout(function () {
      document.addEventListener('pointerdown', gone, true);
      document.addEventListener('keydown', gone, true);
    }, 300);
    pointing.stop = function () {
      clearTimeout(arm);
      document.removeEventListener('pointerdown', gone, true);
      document.removeEventListener('keydown', gone, true);
    };
    return { ok: true, count: els.length };
  }

  function pointedNow() { return pointing.refs.length ? { id: pointing.id, refs: pointing.refs.slice() } : null; }

  // The removable chips for filters Friday set: "Unread only · by Friday  x". `h` is React.createElement.
  function chipsRow(h, filters, onRemove, brand) {
    var mine = (filters || []).filter(function (f) { return f.by === 'friday'; });
    if (!mine.length) return null;
    return h('div', { className: 'fr-chips', role: 'group', 'aria-label': 'Filters ' + (brand || 'Friday') + ' set', 'data-testid': 'fr-chips' },
      mine.map(function (f) {
        return h('span', { key: f.key, className: 'fr-chip', 'data-filter': f.key }, f.label || f.key,
          h('span', { className: 'by' }, '\u00b7 by ' + (brand || 'Friday')),
          h('button', { onClick: function () { onRemove(f.key); }, title: 'Remove this filter', 'aria-label': 'Remove filter ' + (f.label || f.key) }, '\u00d7'));
      }));
  }

  // ── the page side: adapters, stage reports, commands ─────────────────────

  // ── the hand cursor: what the reticle is on, for "this" ──────────────────────

  var CURSOR_MEMORY_MS = 3000;
  var cursorRec = { ref: '', state: '', t: 0, since: 0 };

  // cursor(ref, state): called every frame by the hand cursor with the row ref its locked target belongs
  // to ('' when it is on nothing, or on a guarded control), and 'locked' or 'pinched'. A change is
  // reported soon; the same thing seen again only keeps the time fresh.
  function cursor(ref, state) {
    var now = Date.now();
    if (ref) {
      if (cursorRec.ref !== ref || cursorRec.state !== state) { cursorRec = { ref: ref, state: state, t: now, since: now }; touch(250); }
      else cursorRec.t = now;
    } else if (cursorRec.ref && cursorRec.state !== 'left') {
      cursorRec = { ref: cursorRec.ref, state: 'left', t: now, since: cursorRec.since };
      touch(250);
    }
  }

  // The target for the stage: still on it, or left it within the last 3 s (age_s says how long ago).
  function cursorNow(known) {
    if (!cursorRec.ref) return null;
    if (known && known.indexOf(cursorRec.ref) < 0) return null;
    var age = (Date.now() - cursorRec.t) / 1000;
    if (cursorRec.state === 'left') {
      if (age * 1000 > CURSOR_MEMORY_MS) return null;
      return { ref: cursorRec.ref, state: 'locked', age_s: Math.round(age * 10) / 10 };
    }
    return { ref: cursorRec.ref, state: cursorRec.state, age_s: 0 };
  }

  // ── step lists: what Friday is doing, step by step, and whether it can be stopped ─────

  var STEP_STATES = ['waiting', 'doing', 'done', 'held', 'skipped', 'stopped', 'failed'];
  var STEP_LIVE = ['waiting', 'doing', 'held'];

  // reduceSteps(list, evt) -> the new list (or null). evt: {type:'steps', id, title, steps:[{n, text, state}]} replaces the
  // list; {type:'step_update', id, n, state} changes one step and is ignored for any other list.
  function reduceSteps(list, evt) {
    if (!evt) return list || null;
    if (evt.type === 'steps') {
      var steps = (evt.steps || []).slice(0, 40).map(function (s, i) {
        return { n: s.n || i + 1, text: String(s.text || '').slice(0, 80), state: STEP_STATES.indexOf(s.state) >= 0 ? s.state : 'waiting' };
      });
      return { id: String(evt.id || ''), title: String(evt.title || '').slice(0, 80), steps: steps };
    }
    if (evt.type === 'step_update' && list && String(evt.id) === list.id && STEP_STATES.indexOf(evt.state) >= 0) {
      return { id: list.id, title: list.title, steps: list.steps.map(function (s) { return s.n === evt.n ? { n: s.n, text: s.text, state: evt.state } : s; }) };
    }
    return list || null;
  }
  function stepsRunning(list) {
    return !!list && list.steps.some(function (s) { return STEP_LIVE.indexOf(s.state) >= 0; });
  }

  // ── fields Friday may write into, and never submit ───────────────────────────
  //
  // A workspace registers the fields a person types into (a reply, a quick-add line, a workflow's
  // steps) with read() and write(). Friday writes text into one with a `fill` command; the page marks
  // it "Friday wrote this" with an Undo, and nothing is ever sent, saved or created by a fill: only
  // the owner's own button does that.

  var FILL_MAX = 8000;
  var fieldMap = {};            // ws -> { key -> {key, label, read(), write(text)} }
  var fillState = {};           // 'ws:key' -> { prev, text, flags, at }
  var autoAdapters = {};

  function fieldList(ws) {
    var m = fieldMap[ws] || {};
    return Object.keys(m).slice(0, 12).map(function (k) {
      return { key: k, label: m[k].label || k, filled_by: fillState[ws + ':' + k] && currentText(ws, k) === fillState[ws + ':' + k].text ? 'friday' : null };
    });
  }
  function currentText(ws, key) {
    var f = (fieldMap[ws] || {})[key];
    try { return f ? String(f.read() == null ? '' : f.read()) : ''; } catch (e) { return ''; }
  }

  // A workspace with fields and no list still reports its stage, so the server sees the fields.
  function ensureFieldsAdapter(ws) {
    if (adapters[ws]) return;
    var rev = { json: '', n: 0 };
    var ad = {
      auto: true,
      stage: function () {
        var st = { workspace: ws, items: [], loaded: 0, total_hint: 0, selection: { id: '', refs: [], count: 0, label: '', source: '', beyond_loaded: 0 },
                   filters: [], focus: null, open: null, cursor: null, fields: fieldList(ws), held: [], pointed: null };
        var json = JSON.stringify(st);
        if (json !== rev.json) rev = { json: json, n: rev.n + 1 };
        st.rev = rev.n;
        return st;
      },
      run: function (a) { return a.type === 'stage_request' ? { ok: true } : { ok: false, reason: 'not supported here yet' }; }
    };
    autoAdapters[ws] = ad;
    adapters[ws] = ad;
  }
  function dropFieldsAdapter(ws) {
    if (autoAdapters[ws] && adapters[ws] === autoAdapters[ws]) delete adapters[ws];
    delete autoAdapters[ws];
  }

  // registerField(ws, {key, label, read(), write(text)}) -> the function that unregisters it.
  function registerField(ws, f) {
    ensureStyle();
    (fieldMap[ws] = fieldMap[ws] || {})[f.key] = f;
    ensureFieldsAdapter(ws);
    touch(250);
    return function () {
      if (fieldMap[ws] && fieldMap[ws][f.key] === f) delete fieldMap[ws][f.key];
      delete fillState[ws + ':' + f.key];
      if (fieldMap[ws] && !Object.keys(fieldMap[ws]).length) { delete fieldMap[ws]; dropFieldsAdapter(ws); }
      touch(250);
    };
  }

  // fillField(ws, key, text, mode, flags) -> {ok, field, undo, reason?}. `insert` appends after what is there.
  function fillField(ws, key, text, mode, flags) {
    var f = (fieldMap[ws] || {})[key];
    if (!f) return { ok: false, reason: 'not a field here' };
    var add = String(text == null ? '' : text).slice(0, FILL_MAX);
    var prev = currentText(ws, key);
    var next = mode === 'insert' && prev ? prev + (/\s$/.test(prev) ? '' : ' ') + add : add;
    try { f.write(next); } catch (e) { return { ok: false, reason: 'the field would not take it' }; }
    fillState[ws + ':' + key] = { prev: prev, text: currentText(ws, key) || next, flags: (flags || []).slice(0, 4).map(String), at: Date.now() };
    touch(200);
    return { ok: true, field: key, undo: true, prev_len: prev.length };
  }

  // undoFill(ws, key): put the field back as it was before Friday wrote; false when nothing to undo.
  function undoFill(ws, key) {
    var st = fillState[ws + ':' + key], f = (fieldMap[ws] || {})[key];
    if (!st || !f) return false;
    try { f.write(st.prev); } catch (e) { return false; }
    delete fillState[ws + ':' + key];
    touch(200);
    return true;
  }

  // The note beside a field Friday wrote: "Friday wrote this · Undo", and what in it came from something she read.
  // null once the owner has changed the text (it is theirs now). `h` is React.createElement; onChange re-renders.
  function fillChip(h, ws, key, brand, onChange) {
    var st = fillState[ws + ':' + key];
    if (!st || currentText(ws, key) !== st.text) return null;
    return h('div', { className: 'fr-chips', role: 'status', 'data-testid': 'fr-fill-' + key },
      h('span', { className: 'fr-chip' }, (brand || 'Friday') + ' wrote this · ' + ((fieldMap[ws] || {})[key] || {}).label,
        h('button', { onClick: function () { undoFill(ws, key); if (onChange) onChange(); }, 'aria-label': 'Undo what ' + (brand || 'Friday') + ' wrote' }, 'Undo')),
      (st.flags || []).map(function (t, i) { return h('span', { key: i, className: 'fr-chip', style: { borderColor: 'var(--fr-warn)' } }, '\u26a0 Check: ' + t); }));
  }

  // The notes for several fields at once ("Friday wrote this" for each one she filled): keys, or all of the
  // workspace's fields when keys is falsy. null when none is filled.
  function fillChips(h, ws, keys, brand, onChange) {
    var list = keys || Object.keys(fieldMap[ws] || {});
    var out = list.map(function (k) {
      var st = fillState[ws + ':' + k];
      if (!st || currentText(ws, k) !== st.text) return null;
      var f = (fieldMap[ws] || {})[k] || {};
      return h('span', { key: k, className: 'fr-chip', 'data-testid': 'fr-fill-' + k }, (brand || 'Friday') + ' wrote ' + (f.label || k),
        h('button', { onClick: function () { undoFill(ws, k); if (onChange) onChange(); }, 'aria-label': 'Undo what ' + (brand || 'Friday') + ' wrote in ' + (f.label || k) }, 'Undo'));
    }).filter(Boolean);
    var flags = [];
    list.forEach(function (k) { var st = fillState[ws + ':' + k]; if (st && currentText(ws, k) === st.text) (st.flags || []).forEach(function (t) { flags.push(t); }); });
    if (!out.length) return null;
    return h('div', { className: 'fr-chips', role: 'status' }, out,
      flags.map(function (t, i) { return h('span', { key: 'f' + i, className: 'fr-chip', style: { borderColor: 'var(--fr-warn)' } }, '\u26a0 Check: ' + t); }));
  }

  var adapters = {};
  var commandTypes = { select: 1, clear_selection: 1, stage_request: 1, held: 1, point: 1, chips: 1, fill: 1 };

  /* makeAdapter(ws, get): the whole adapter for a list workspace from a small config the workspace
   * hands over fresh each time (so it always reads the latest state). cfg:
   *   items()    -> [{ref, n, facets, title, who}] the rows on screen, in order
   *   filters()  -> [{key, value, label, by}]
   *   setFilter(key, value) -> true/false/Promise: the workspace's own filter; removeFilter(key)
   *   loaded(), total() (optional), root() (the DOM node holding the rows, optional)
   * It answers stage_request, point and chips; a workspace that also ticks adds its own `run`. */
  function makeAdapter(ws, get) {
    var rev = { json: '', n: 0 };
    function known() { var c = get(); return (c.items ? c.items() : []).map(function (i) { return i.ref; }); }
    function selectionOf(c) {
      if (c.getSel) {
        var s = c.getSel(), k = known(), on = 0;
        s.refs.forEach(function (r) { if (k.indexOf(r) >= 0) on++; });
        return { id: s.id, refs: s.refs, count: s.refs.length, label: s.label, source: s.source, beyond_loaded: s.refs.length - on };
      }
      return c.selection ? c.selection() : { id: '', refs: [], count: 0, label: '', source: '', beyond_loaded: 0 };
    }
    function stage() {
      var c = get();
      var items = (c.items ? c.items() : []).slice(0, MAX_ROWS);
      var heldN = c.getSel ? Object.keys(c.getSel().held || {}).length : 0;
      var st = {
        workspace: ws, items: items, loaded: c.loaded ? c.loaded() : items.length,
        total_hint: c.total ? c.total() : 0,
        selection: selectionOf(c),
        filters: c.filters ? c.filters() : [], focus: c.focus ? c.focus() : null, open: c.open ? c.open() : null,
        cursor: cursorNow(items.map(function (i) { return i.ref; })), fields: fieldList(ws),
        held: heldN ? [{ card_id: 'pending', refs_count: heldN }] : [],
        pointed: pointedNow()
      };
      var json = JSON.stringify(st);
      if (json !== rev.json) rev = { json: json, n: rev.n + 1 };
      st.rev = rev.n;
      return st;
    }
    function run(a) {
      var c = get();
      if (a.type === 'stage_request') return { ok: true, rev: rev.n };
      if ((a.type === 'select' || a.type === 'clear_selection' || a.type === 'held') && c.getSel && c.setSel) {
        var evt = a.type === 'select' ? { type: 'select', mode: (a.selection || {}).mode || 'replace', refs: (a.selection || {}).refs || [],
                                          id: (a.selection || {}).id, label: (a.selection || {}).label }
          : a.type === 'clear_selection' ? { type: 'clear' }
          : { type: 'held', state: a.state, refs: a.refs || [], card_id: a.card_id };
        var r = reduceSelection(c.getSel(), evt, known());
        if (r.changed) c.setSel(r.state);
        if (a.type === 'held' && a.state === 'done' && c.rowsGone) c.rowsGone(a.refs || [], a);
        touch(200);
        return { ok: true, applied: r.applied, missing: r.missing, accepted: r.accepted, count: r.count, rev: rev.n + 1 };
      }
      if (a.type === 'point') {
        var r = point(a.refs || [], { root: c.root ? c.root() : null, badges: a.badges, id: a.id });
        touch(200);
        return r;
      }
      if (a.type === 'chips') {
        var applied = [], rejected = [];
        var jobs = [];
        (a.set || []).forEach(function (f) {
          jobs.push(Promise.resolve(c.setFilter ? c.setFilter(f.key, f.value) : false).then(function (ok) {
            (ok === false ? rejected : applied).push(ok === false ? { key: f.key, reason: 'not a filter here' } : f.key);
          }));
        });
        (a.remove || []).forEach(function (k) {
          jobs.push(Promise.resolve(c.removeFilter ? c.removeFilter(k) : false).then(function (ok) {
            (ok === false ? rejected : applied).push(ok === false ? { key: k, reason: 'not a filter here' } : k);
          }));
        });
        return Promise.all(jobs).then(function () {
          touch(200);
          // give the workspace one frame to show the result before it is read back
          return new Promise(function (res) { setTimeout(res, 60); });
        }).then(function () {
          var fs = (get().filters ? get().filters() : []);
          return { ok: rejected.length === 0, applied: applied, rejected: rejected, filters: fs,
                   reason: rejected.length ? 'that filter is not available here' : undefined };
        });
      }
      return { ok: false, reason: 'not supported here yet' };
    }
    return { stage: stage, run: run };
  }

  // register(ws, { stage(), run(action) -> Promise<result> }): returns the unregister function.
  function register(ws, adapter) {
    ensureStyle();
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
    if (action && action.type === 'fill') {
      var res = fillField(ws, action.field, action.text, action.mode, action.flags);
      var st0 = null;
      if (a) { try { st0 = a.stage(); } catch (e) { /* the result still stands */ } }
      return Promise.resolve({ result: res, stage: st0 });
    }
    if (!a) return Promise.resolve({ result: { ok: false, reason: 'not open' }, stage: null });
    return Promise.resolve(a.run(action)).then(function (result) {
      var st = null;
      try { st = a.stage(); } catch (e) { /* the result still stands */ }
      return { result: result, stage: st };
    }, function (e) {
      return { result: { ok: false, reason: String(e && e.message || e) }, stage: null };
    });
  }

  // ── lists whose rows carry their own ref ─────────────────────────────────────
  //
  // domList(ws, cfg) -> off(): the stage of a workspace whose component has no selection state of its own. Each
  // row only has to carry data-fr-ref="<kind>:<id>" (and data-fr-title, data-fr-facets='{"k":"v"}' where the
  // workspace may publish them). Ticks, if the list is `selectable`, are held here and drawn as data-fr-sel on
  // the rows (the same ground and edge as every other list); the owner ticks with Ctrl/Cmd-click or a pinch.
  // cfg: root() -> Element, rows (selector, default [data-fr-ref]), selectable, counts_only (no titles or who
  // leave the page: Health, Finance, Family), open() -> ref, filters() -> chips, brand.
  var DOM_FACET_MAX = 160;
  function domList(ws, cfg) {
    cfg = cfg || {};
    ensureStyle();
    var sel = emptySelection();
    var counts = !!cfg.counts_only;
    var selector = cfg.rows || '[data-fr-ref]';
    function rootEl() { return (cfg.root && cfg.root()) || document; }
    function rowEls() { return Array.prototype.slice.call(rootEl().querySelectorAll(selector)); }
    function facetsOf(el) {
      var raw = el.getAttribute('data-fr-facets');
      if (!raw) return {};
      try {
        var o = JSON.parse(raw), out = {};
        Object.keys(o).slice(0, 12).forEach(function (k) {
          var v = o[k];
          if (typeof v === 'string') out[k] = v.slice(0, DOM_FACET_MAX);
          else if (typeof v === 'number' || typeof v === 'boolean') out[k] = v;
        });
        return out;
      } catch (e) { return {}; }
    }
    function items() {
      return rowEls().slice(0, MAX_ROWS).map(function (el, i) {
        return { ref: el.getAttribute('data-fr-ref') || '', n: i + 1, facets: facetsOf(el),
                 title: counts ? '' : String(el.getAttribute('data-fr-title') || '').slice(0, 120), who: '' };
      }).filter(function (it) { return it.ref; });
    }
    function known() { return items().map(function (i) { return i.ref; }); }
    function paint() {
      rowEls().forEach(function (el) {
        var ref = el.getAttribute('data-fr-ref') || '';
        if (cfg.selectable && sel.refs.indexOf(ref) >= 0) el.setAttribute('data-fr-sel', 'on'); else el.removeAttribute('data-fr-sel');
        if (sel.held[ref]) el.setAttribute('data-fr-held', 'on'); else el.removeAttribute('data-fr-held');
      });
      chip();
    }
    var chipEl = null;
    function chip() {
      if (!cfg.selectable || typeof document === 'undefined') return;
      var text = chipText(sel, cfg.brand);
      if (!text) { if (chipEl && chipEl.parentNode) chipEl.parentNode.removeChild(chipEl); chipEl = null; return; }
      if (!chipEl) {
        chipEl = document.createElement('div');
        chipEl.className = 'fr-chip fr-dom-chip';
        chipEl.setAttribute('data-testid', 'fr-dom-chip');
        chipEl.setAttribute('role', 'status');
        chipEl.style.cssText = 'position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:10030';
        document.body.appendChild(chipEl);
      }
      chipEl.textContent = '';
      var span = document.createElement('span'); span.textContent = text; chipEl.appendChild(span);
      var held = Object.keys(sel.held).length;
      if (held) { var w = document.createElement('span'); w.className = 'needs-you'; w.textContent = 'waiting for your OK'; chipEl.appendChild(w); }
      var b = document.createElement('button'); b.type = 'button'; b.textContent = 'Clear'; b.setAttribute('aria-label', 'Clear the selection');
      b.onclick = function () { apply({ type: 'clear' }); };
      chipEl.appendChild(b);
    }
    function apply(evt) {
      var r = reduceSelection(sel, evt, known());
      if (r.changed) { sel = r.state; paint(); touch(200); }
      return r;
    }
    var mo = null, timer = null;
    function settle() {
      // rows that left the list (a refresh, a delete) leave the selection
      var have = {}; known().forEach(function (r) { have[r] = 1; });
      var gone = sel.refs.filter(function (r) { return !have[r]; });
      if (gone.length && cfg.selectable) apply({ type: 'rows_gone', refs: gone }); else paint();
      touch(300);
    }
    if (typeof MutationObserver === 'function') {
      mo = new MutationObserver(function () { if (timer) clearTimeout(timer); timer = setTimeout(settle, 350); });
      var el0 = (cfg.root && cfg.root()) || null;
      if (el0) mo.observe(el0, { childList: true, subtree: true });
    }
    function onClick(e) {
      if (!cfg.selectable || !(e.ctrlKey || e.metaKey)) return;
      var row = e.target && e.target.closest ? e.target.closest(selector) : null;
      if (!row) return;
      e.preventDefault(); e.stopPropagation();
      apply({ type: 'toggle', ref: row.getAttribute('data-fr-ref') || '' });
    }
    function onRowTick(e) {
      var ref = e.detail && e.detail.ref;
      if (cfg.selectable && ref) apply({ type: 'toggle', ref: ref });
    }
    var host = (typeof document !== 'undefined') ? document : null;
    if (host) { host.addEventListener('click', onClick, true); host.addEventListener('friday:row-tick', onRowTick); }
    var adapter = makeAdapter(ws, function () {
      var c = {
        root: rootEl, items: items, loaded: function () { return rowEls().length; },
        open: cfg.open ? cfg.open : null, filters: cfg.filters ? cfg.filters : null
      };
      if (cfg.selectable) {
        c.getSel = function () { return sel; };
        c.setSel = function (s) { sel = s; paint(); };
        c.rowsGone = function (refs) { apply({ type: 'rows_gone', refs: refs }); };
      }
      return c;
    });
    var unreg = register(ws, adapter);
    paint();
    return function () {
      unreg();
      if (mo) mo.disconnect();
      if (timer) clearTimeout(timer);
      if (host) { host.removeEventListener('click', onClick, true); host.removeEventListener('friday:row-tick', onRowTick); }
      if (chipEl && chipEl.parentNode) chipEl.parentNode.removeChild(chipEl);
      rowEls().forEach(function (el) { el.removeAttribute('data-fr-sel'); el.removeAttribute('data-fr-held'); });
    };
  }

  // Ask the page to send its stage soon (after the owner changes something).
  function touch(ms) {
    if (typeof window !== 'undefined' && typeof window.fridayDeskSoon === 'function') window.fridayDeskSoon(ms == null ? 250 : ms);
  }

  return {
    MAX_REFS: MAX_REFS, MAX_ROWS: MAX_ROWS, SWEEP_STAGGER_MS: SWEEP_STAGGER_MS, SWEEP_ROW_MS: SWEEP_ROW_MS,
    MIN_GAP_MS: MIN_GAP_MS,
    emptySelection: emptySelection, reduceSelection: reduceSelection, chipText: chipText,
    sweepDelays: sweepDelays, limiter: limiter, POINT_CAP: POINT_CAP, POINT_FADE_MS: POINT_FADE_MS,
    STEP_STATES: STEP_STATES, reduceSteps: reduceSteps, stepsRunning: stepsRunning,
    FILL_MAX: FILL_MAX, fieldList: fieldList, registerField: registerField, fillField: fillField, undoFill: undoFill, fillChip: fillChip, fillChips: fillChips,
    cursor: cursor, cursorNow: cursorNow, CURSOR_MEMORY_MS: CURSOR_MEMORY_MS,
    pointPlan: pointPlan, point: point, clearPoints: clearPoints, chipsRow: chipsRow, makeAdapter: makeAdapter, ensureStyle: ensureStyle,
    domList: domList, register: register, registered: registered, snapshot: snapshot, handles: handles, run: run, touch: touch
  };
}));
