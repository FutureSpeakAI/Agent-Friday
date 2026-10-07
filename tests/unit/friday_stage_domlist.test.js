// Node tests for domList (static/friday_stage.js): the stage of a workspace whose rows only carry data-fr-ref.
// Run by tests/unit/test_friday_stage_domlist.py. A small fake DOM stands in for the page.
const assert = require('assert');
const path = require('path');

class El {
  constructor(attrs) { this.attrs = Object.assign({}, attrs); this.children = []; this.parent = null; this.listeners = {}; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  closest(sel) { for (let e = this; e; e = e.parent) if (e.matches(sel)) return e; return null; }
  matches(sel) { const m = /^\[([a-z-]+)(?:="(.*)")?\]$/.exec(sel); return !!m && m[1] in this.attrs && (m[2] === undefined || this.attrs[m[1]] === m[2]); }
  querySelectorAll(sel) { const out = []; const walk = e => { e.children.forEach(c => { if (c.matches(sel)) out.push(c); walk(c); }); }; walk(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  append(c) { c.parent = this; this.children.push(c); return c; }
  appendChild(c) { return this.append(c); }
  removeChild(c) { this.children = this.children.filter(x => x !== c); c.parent = null; return c; }
  get parentNode() { return this.parent; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter(x => x !== f); }
  fire(t, e) { (this.listeners[t] || []).forEach(f => f(e)); }
}
const body = new El({});
const doc = new El({});
doc.body = body; doc.head = new El({});
doc.createElement = () => { const e = new El({}); e.style = {}; e.className = ''; Object.defineProperty(e, 'textContent', { set(v) { e._t = v; }, get() { return e._t || ''; } }); return e; };
doc.documentElement = new El({});
doc.head.style = {}; doc.documentElement.style = {};
global.document = doc;
global.window = { fridayDeskSoon: () => { global.__touched = (global.__touched || 0) + 1; } };
const FS = require(path.resolve(__dirname, '..', '..', 'static', 'friday_stage.js'));

const results = [], queue = [];
function test(name, fn) { queue.push([name, fn]); }

function list(rows) {
  const root = new El({});
  rows.forEach(r => root.append(new El(r)));
  return root;
}
const ROWS = [
  { 'data-fr-ref': 'event:e1', 'data-fr-title': 'Dentist', 'data-fr-facets': JSON.stringify({ kind: 'event', when: 'today', note: 'x'.repeat(300) }) },
  { 'data-fr-ref': 'event:e2', 'data-fr-title': 'Lunch', 'data-fr-facets': JSON.stringify({ kind: 'event', when: 'tomorrow' }) },
  { 'data-fr-ref': 'event:e3', 'data-fr-title': 'Call' }
];

test('the stage lists the rows with their refs, titles and bounded facets', () => {
  const root = list(ROWS);
  const off = FS.domList('calendar', { root: () => root, selectable: true });
  const st = FS.snapshot('calendar');
  assert.strictEqual(st.workspace, 'calendar');
  assert.deepStrictEqual(st.items.map(i => i.ref), ['event:e1', 'event:e2', 'event:e3']);
  assert.strictEqual(st.items[0].title, 'Dentist');
  assert.strictEqual(st.items[0].facets.note.length, 160, 'a facet is clipped');
  assert.strictEqual(st.items[1].n, 2);
  off();
  assert.strictEqual(FS.snapshot('calendar'), null, 'the stage goes with the list');
});

test('counts-only: no title leaves the page', () => {
  const root = list([{ 'data-fr-ref': 'health:1', 'data-fr-title': 'Cardiology referral', 'data-fr-facets': JSON.stringify({ kind: 'visit' }) }]);
  const off = FS.domList('health', { root: () => root, counts_only: true });
  const it = FS.snapshot('health').items[0];
  assert.strictEqual(it.title, '');
  assert.strictEqual(it.facets.kind, 'visit');
  off();
});

test('a tick from Friday is drawn on the row and counted; clearing removes it', () => {
  const root = list(ROWS);
  const off = FS.domList('calendar', { root: () => root, selectable: true });
  return FS.run({ type: 'select', workspace: 'calendar', selection: { mode: 'replace', refs: ['event:e1', 'event:e3'], label: 'Today' } }).then(r => {
    assert.strictEqual(r.result.applied, 2);
    assert.strictEqual(root.children[0].getAttribute('data-fr-sel'), 'on');
    assert.strictEqual(root.children[1].getAttribute('data-fr-sel'), null);
    const st = FS.snapshot('calendar');
    assert.deepStrictEqual(st.selection.refs, ['event:e1', 'event:e3']);
    assert.strictEqual(st.selection.source, 'friday');
    return FS.run({ type: 'clear_selection', workspace: 'calendar' });
  }).then(() => {
    assert.strictEqual(root.children[0].getAttribute('data-fr-sel'), null);
    off();
  });
});

test('the owner ticks with Ctrl-click and with a pinch event, and the source says so', () => {
  const root = list(ROWS);
  const off = FS.domList('calendar', { root: () => root, selectable: true });
  let prevented = false;
  doc.fire('click', { ctrlKey: true, target: root.children[1], preventDefault() { prevented = true; }, stopPropagation() {} });
  assert.strictEqual(prevented, true);
  assert.strictEqual(root.children[1].getAttribute('data-fr-sel'), 'on');
  doc.fire('friday:row-tick', { detail: { ref: 'event:e3' } });
  const st = FS.snapshot('calendar');
  assert.deepStrictEqual(st.selection.refs, ['event:e2', 'event:e3']);
  assert.strictEqual(st.selection.source, 'owner');
  doc.fire('click', { ctrlKey: false, target: root.children[0], preventDefault() { throw new Error('a plain click is not a tick'); }, stopPropagation() {} });
  off();
});

test('a list that is not selectable ignores ticks', () => {
  const root = list(ROWS);
  const off = FS.domList('workflows', { root: () => root });
  doc.fire('click', { ctrlKey: true, target: root.children[0], preventDefault() { throw new Error('no'); }, stopPropagation() {} });
  assert.deepStrictEqual(FS.snapshot('workflows').selection.refs, []);
  return FS.run({ type: 'select', workspace: 'workflows', selection: { mode: 'replace', refs: ['event:e1'] } }).then(r => {
    assert.strictEqual(r.result.ok === false || r.result.count === 0 || r.result.ok === true, true);
    assert.deepStrictEqual(FS.snapshot('workflows').selection.refs, []);
    off();
  });
});

test('pointing marks the rows by ref', () => {
  const root = list(ROWS);
  global.getComputedStyle = () => ({ position: 'relative' });
  const off = FS.domList('calendar', { root: () => root, selectable: true });
  return FS.run({ type: 'point', workspace: 'calendar', refs: ['event:e2'], id: 'pt1' }).then(r => {
    assert.strictEqual(r.result.count, 1);
    assert.strictEqual(root.children[1].getAttribute('data-fr-point'), 'on');
    assert.strictEqual(root.children[1].getAttribute('data-fr-n'), '1');
    FS.clearPoints();
    off();
  });
});

(async () => {
  for (const [name, fn] of queue) {
    try { await fn(); results.push(['ok', name]); } catch (e) { results.push(['FAIL', name + ': ' + e.message]); }
  }
  let failed = 0;
  for (const [s, n] of results) { if (s !== 'ok') failed++; console.log(s === 'ok' ? 'PASS' : 'FAIL', n); }
  console.log(results.length - failed + ' passed, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
})();
