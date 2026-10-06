const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Depth = require('../../../../../static/friday_workspace_depth.js');

function events(target = {}) {
  const listeners = new Map();
  target.addEventListener = (type, fn) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); };
  target.removeEventListener = (type, fn) => listeners.get(type)?.delete(fn);
  target.emit = type => { for (const fn of listeners.get(type) || []) fn(); };
  target.listenerCount = () => [...listeners.values()].reduce((count, set) => count + set.size, 0);
  return target;
}
function fixture() {
  let time = 0, next = 0;
  const tasks = new Map(), values = new Map(), attrs = new Map();
  const media = events({ matches: false });
  const win = events({ performance: { now: () => time }, matchMedia: () => media });
  function add(kind, fn, delay) { const id = ++next; tasks.set(id, { kind, fn, at: time + delay }); return id; }
  win.requestAnimationFrame = fn => add('frame', fn, 16);
  win.cancelAnimationFrame = id => tasks.delete(id);
  win.setTimeout = (fn, delay) => add('timer', fn, delay);
  win.clearTimeout = id => tasks.delete(id);
  const doc = events({ defaultView: win, hidden: false, visibilityState: 'visible' });
  const root = { ownerDocument: doc, style: {
    getPropertyValue: key => values.get(key)?.value || '',
    getPropertyPriority: key => values.get(key)?.priority || '',
    setProperty: (key, value, priority = '') => values.set(key, { value, priority }),
    removeProperty: key => values.delete(key)
  }, getAttribute: key => attrs.get(key) ?? null,
  setAttribute: (key, value) => attrs.set(key, value), removeAttribute: key => attrs.delete(key) };
  return { root, doc, win, media, tasks,
    number: name => parseFloat(root.style.getPropertyValue(name)),
    advance(ms) {
      const until = time + ms; let count = 0;
      for (;;) {
        const pending = [...tasks].filter(([, task]) => task.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!pending) break;
        if (++count > 10000) throw Error('Unexpected perpetual scheduling');
        time = pending[1].at; tasks.delete(pending[0]); pending[1].fn(time);
      }
      time = until;
    }, frames: () => [...tasks.values()].filter(task => task.kind === 'frame').length
  };
}

const checked = [];
function check(name, fn) { fn(); checked.push(name); }
check('flat default schedules no work and never acquires input', () => {
  const f = fixture(), d = new Depth({ root: f.root });
  assert.equal(d.state.mode, 'flat'); assert.equal(d.state.source, 'off'); assert.equal(f.tasks.size, 0);
  d.setPose({ x: 1, y: 1, z: 1, seen: true }); f.advance(1000);
  assert.equal(f.number('--friday-depth-x'), 0); assert.equal(f.tasks.size, 0); d.destroy();
});
check('clamps invalid and excessive pose values, then stops settling', () => {
  const f = fixture(), d = new Depth({ root: f.root });
  d.setMode('immersive').setSource('preview').setPose({ x: Infinity, y: -3, z: 2, seen: true }); f.advance(550);
  assert.equal(f.number('--friday-depth-x'), 0); assert.equal(f.number('--friday-depth-y'), -12);
  assert.equal(f.number('--friday-depth-z'), 1); assert.equal(f.frames(), 0);
  assert.equal(f.tasks.size, 1); assert.equal(d.state.seen, true); d.destroy();
});
check('stale input eases to neutral after 600ms and becomes entirely idle', () => {
  const f = fixture(), d = new Depth({ root: f.root });
  d.setMode('immersive').setSource('camera').setPose({ x: 1, y: 1, z: 1, seen: true }); f.advance(599);
  assert.equal(f.number('--friday-depth-x'), 12); assert.equal(d.state.stale, false);
  f.advance(17); assert.ok(f.number('--friday-depth-x') > 0 && f.number('--friday-depth-x') < 12);
  assert.equal(d.state.stale, true); assert.equal(d.state.seen, false);
  f.advance(1000); assert.equal(f.number('--friday-depth-x'), 0); assert.equal(f.tasks.size, 0); d.destroy();
});
check('quiet mode bounds movement to 3px and source Off neutralizes immediately', () => {
  const f = fixture(), d = new Depth({ root: f.root });
  d.setMode('quiet').setSource('preview').setPose({ x: 1, y: 1, z: 1, seen: true }); f.advance(550);
  assert.equal(f.number('--friday-depth-x'), 3); assert.equal(f.number('--friday-depth-z'), 0.25);
  d.setSource('off'); assert.equal(f.number('--friday-depth-x'), 0); assert.equal(f.tasks.size, 0);
  assert.throws(() => d.setMode('unknown'), RangeError); assert.throws(() => d.setSource('mouse'), RangeError); d.destroy();
});
check('hiding releases scheduling and forgets pose until a fresh visible sample', () => {
  const f = fixture(), d = new Depth({ root: f.root });
  d.setMode('balanced').setSource('camera').setPose({ x: 1, y: 1, z: 1, seen: true }); f.advance(100);
  f.doc.hidden = true; f.doc.visibilityState = 'hidden'; f.doc.emit('visibilitychange');
  assert.equal(f.tasks.size, 0); assert.equal(f.number('--friday-depth-x'), 0);
  d.setPose({ x: 1, y: 1, z: 1, seen: true });
  f.doc.hidden = false; f.doc.visibilityState = 'visible'; f.doc.emit('visibilitychange');
  assert.equal(f.tasks.size, 0); assert.equal(d.state.seen, false);
  d.setPose({ x: 1, y: 0, z: 0, seen: true }); f.advance(100); assert.ok(f.number('--friday-depth-x') > 0); d.destroy();
});
check('reduced motion removes pose response and all animation scheduling', () => {
  const f = fixture(), d = new Depth({ root: f.root });
  d.setMode('immersive').setSource('camera').setPose({ x: 1, y: 1, z: 1, seen: true }); f.advance(100);
  f.media.matches = true; f.media.emit('change');
  assert.equal(f.number('--friday-depth-x'), 0); assert.equal(f.number('--friday-depth-z'), 0); assert.equal(f.tasks.size, 0);
  d.setPose({ x: -1, y: -1, z: -1, seen: true }); f.advance(1000);
  assert.equal(f.tasks.size, 0); assert.equal(f.number('--friday-light-x'), 50); d.destroy();
});
check('page lifecycle and destroy release listeners and restore prior owned styles', () => {
  const f = fixture(); f.root.style.setProperty('--friday-depth-x', '2px', 'important');
  f.root.setAttribute('data-friday-depth-mode', 'prior');
  const d = new Depth({ root: f.root }); d.setMode('immersive').setSource('preview').setPose({ x: 1, seen: true });
  f.win.emit('pagehide'); assert.equal(f.tasks.size, 0); f.win.emit('pageshow'); assert.equal(f.tasks.size, 0);
  d.setPose({ x: 1, seen: true }); d.destroy(); d.destroy();
  assert.equal(f.tasks.size, 0); assert.equal(f.doc.listenerCount() + f.win.listenerCount() + f.media.listenerCount(), 0);
  assert.equal(f.root.style.getPropertyValue('--friday-depth-x'), '2px');
  assert.equal(f.root.style.getPropertyPriority('--friday-depth-x'), 'important');
  assert.equal(f.root.getAttribute('data-friday-depth-mode'), 'prior');
  assert.equal(d.state.destroyed, true); d.setPose({ x: 1, seen: true }); assert.equal(f.tasks.size, 0);
});
check('state snapshots cannot mutate controller state', () => {
  const f = fixture(), d = new Depth({ root: f.root }); const copy = d.state; copy.pose.x = 100;
  assert.equal(d.state.pose.x, 0); d.destroy();
});
console.log(JSON.stringify({ passed: checked.length, checks: checked }, null, 2));
