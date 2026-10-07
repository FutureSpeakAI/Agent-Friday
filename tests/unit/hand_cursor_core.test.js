// Node tests for static/hand_cursor_core.js. Run by tests/unit/test_hand_cursor_core.py.
const assert = require('assert');
const path = require('path');
const core = require(path.resolve(__dirname, '..', '..', 'static', 'hand_cursor_core.js'));

const results = [];
function test(name, fn) { try { fn(); results.push(['ok', name]); } catch (e) { results.push(['FAIL', name + ': ' + e.message]); } }
const rect = (left, top, w, h) => ({ left, top, width: w, height: h });

// ── One Euro ──
test('one euro passes a constant through unchanged', () => {
  const f = core.oneEuro(); let v;
  for (let i = 0; i < 30; i++) v = f.push(100, i / 30);
  assert.ok(Math.abs(v - 100) < 1e-9);
});
test('one euro removes jitter: output variance far below input variance', () => {
  const f = core.oneEuro({ minCutoff: 1.0, beta: 0.02 });
  let seed = 7; const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280 - 0.5; };
  const outs = [];
  for (let i = 0; i < 300; i++) outs.push(f.push(200 + rnd() * 20, i / 30));
  const tail = outs.slice(100);
  const mean = tail.reduce((a, b) => a + b, 0) / tail.length;
  const varOut = tail.reduce((a, b) => a + (b - mean) ** 2, 0) / tail.length;
  assert.ok(varOut < (20 * 20 / 12) / 4, 'variance ' + varOut);
});
test('one euro follows a fast move within a few frames (no lag on intent)', () => {
  const f = core.oneEuro({ minCutoff: 1.0, beta: 0.02 });
  for (let i = 0; i < 10; i++) f.push(0, i / 30);
  let v; for (let i = 10; i < 25; i++) v = f.push(400, i / 30);
  assert.ok(v > 380, 'reached ' + v);
});

// ── Snap hysteresis ──
test('snap engages only within engageRadius', () => {
  const s = core.snapController({ engageRadius: 40, releaseRadius: 64 });
  const T = [{ id: 'a', rect: rect(100, 100, 30, 30) }];
  assert.strictEqual(s.update({ x: 200, y: 115 }, T).target, null);   // 70 px away
  assert.strictEqual(s.update({ x: 160, y: 115 }, T).target.id, 'a'); // 30 px away
});
test('snap holds the lock until releaseRadius, then lets go (hysteresis)', () => {
  const s = core.snapController({ engageRadius: 40, releaseRadius: 64 });
  const T = [{ id: 'a', rect: rect(100, 100, 30, 30) }];
  s.update({ x: 160, y: 115 }, T);
  assert.strictEqual(s.update({ x: 185, y: 115 }, T).target.id, 'a');  // 55 px: beyond engage, inside release
  assert.strictEqual(s.update({ x: 200, y: 115 }, T).target, null);    // 70 px: released
  assert.strictEqual(s.update({ x: 185, y: 115 }, T).target, null);    // 55 px: not re-engaged
});
test('snap never flickers between two neighbours while locked', () => {
  const s = core.snapController({ engageRadius: 40, releaseRadius: 64 });
  const T = [{ id: 'a', rect: rect(100, 100, 30, 30) }, { id: 'b', rect: rect(150, 100, 30, 30) }];
  s.update({ x: 125, y: 115 }, T); // inside a
  const seen = new Set();
  for (let x = 125; x <= 160; x += 1) seen.add(s.update({ x, y: 115 }, T).target.id); // walk onto b, still within 64 of a
  assert.deepStrictEqual([...seen], ['a']);
});
test('smaller target wins a tie so a big card does not out-pull its button', () => {
  const s = core.snapController({ engageRadius: 40, releaseRadius: 64 });
  const T = [{ id: 'card', rect: rect(0, 0, 400, 200) }, { id: 'btn', rect: rect(300, 150, 40, 24) }];
  assert.strictEqual(s.update({ x: 320, y: 160 }, T).target.id, 'btn');
});
test('friction pulls the drawn point toward the lock but never jumps', () => {
  const s = core.snapController({ engageRadius: 40, releaseRadius: 64, friction: 0.35 });
  const T = [{ id: 'a', rect: rect(100, 100, 30, 30) }];
  const r = s.update({ x: 140, y: 115 }, T);
  assert.ok(r.point.x < 140 && r.point.x > 115);
});
test('a disabled target cannot be locked and an existing lock on it releases', () => {
  const s = core.snapController();
  const T = [{ id: 'a', rect: rect(100, 100, 30, 30) }];
  s.update({ x: 115, y: 115 }, T);
  assert.strictEqual(s.update({ x: 115, y: 115 }, [{ id: 'a', rect: rect(100, 100, 30, 30), disabled: true }]).target, null);
});

// ── Pinch freeze ──
test('pinch click reports the ONSET point, not the release point', () => {
  const m = core.pinchMachine();
  assert.strictEqual(m.update(0.9, { x: 10, y: 10 }, 0).type, 'pinchStart');
  assert.strictEqual(m.update(0.9, { x: 18, y: 14 }, 100), null);          // drift during the pinch, under the slop
  const ev = m.update(0.1, { x: 22, y: 16 }, 200);
  assert.strictEqual(ev.type, 'click');
  assert.deepStrictEqual(ev.point, { x: 10, y: 10 });
});
test('pinch thresholds have hysteresis: a waver between 0.4 and 0.6 does not double-fire', () => {
  const m = core.pinchMachine();
  m.update(0.9, { x: 0, y: 0 }, 0);
  assert.strictEqual(m.update(0.5, { x: 0, y: 0 }, 50), null);
  assert.strictEqual(m.update(0.45, { x: 0, y: 0 }, 60), null);
  assert.strictEqual(m.update(0.2, { x: 0, y: 0 }, 70).type, 'click');
  assert.strictEqual(m.update(0.5, { x: 0, y: 0 }, 80), null); // not a new onset
});
test('frozen point is exposed while pinched and null otherwise', () => {
  const m = core.pinchMachine();
  assert.strictEqual(m.frozen, null);
  m.update(0.9, { x: 5, y: 6 }, 0);
  assert.deepStrictEqual(m.frozen, { x: 5, y: 6 });
  m.update(0.0, { x: 50, y: 60 }, 10);
  assert.strictEqual(m.frozen, null);
});
test('moving past the slop while pinched becomes a drag and does not click on release', () => {
  const m = core.pinchMachine({ dragSlopPx: 24 });
  m.update(0.9, { x: 0, y: 0 }, 0);
  const d = m.update(0.9, { x: 0, y: 40 }, 100);
  assert.strictEqual(d.type, 'drag'); assert.deepStrictEqual(d.delta, { x: 0, y: 40 });
  const d2 = m.update(0.9, { x: 0, y: 55 }, 130);
  assert.deepStrictEqual(d2.delta, { x: 0, y: 15 });
  assert.strictEqual(m.update(0.1, { x: 0, y: 55 }, 200).type, 'pinchEnd');
});
test('a guarded target needs the hold to complete; an early release cancels', () => {
  const m = core.pinchMachine({ holdMs: 700 });
  m.update(0.9, { x: 0, y: 0 }, 0, true);
  assert.strictEqual(m.update(0.9, { x: 0, y: 0 }, 350).type, 'holdProgress');
  assert.strictEqual(m.update(0.1, { x: 0, y: 0 }, 400).type, 'cancel');
  m.update(0.9, { x: 0, y: 0 }, 1000, true);
  assert.strictEqual(m.update(0.9, { x: 0, y: 0 }, 1700).type, 'hold');
  const c = m.update(0.1, { x: 0, y: 0 }, 1800);
  assert.strictEqual(c.type, 'click'); assert.strictEqual(c.guarded, true);
});

// ── Dwell ──
test('dwell fires once after dwellMs on a steady target and not again until the cursor leaves', () => {
  const d = core.dwellMachine({ dwellMs: 650 });
  assert.strictEqual(d.update('a', 0).fire, false);
  assert.ok(d.update('a', 325).progress > 0.45);
  assert.strictEqual(d.update('a', 650).fire, true);
  assert.strictEqual(d.update('a', 2000).fire, false);
  d.update(null, 2100);
  assert.strictEqual(d.update('a', 2200).fire, false);
  assert.strictEqual(d.update('a', 2850).fire, true);
});
test('dwell resets when the target changes', () => {
  const d = core.dwellMachine({ dwellMs: 650 });
  d.update('a', 0); d.update('a', 500);
  assert.ok(d.update('b', 600).progress < 0.2);
});

// ── Zoom and the change limiter ──
test('two-hand zoom reports the scale against the first distance', () => {
  const z = core.zoomTracker();
  assert.strictEqual(z.update({ x: 0, y: 0 }, { x: 100, y: 0 }), 1);
  assert.ok(Math.abs(z.update({ x: 0, y: 0 }, { x: 150, y: 0 }) - 1.5) < 1e-9);
  assert.strictEqual(z.update(null, { x: 1, y: 1 }), null);
});
test('change limiter allows at most one visual state change per 334 ms', () => {
  const lim = core.changeLimiter(334);
  assert.strictEqual(lim('a', 0), 'a');
  assert.strictEqual(lim('b', 100), 'a');
  assert.strictEqual(lim('b', 340), 'b');
});

// ── Guarded actions ──
test('guard words: sends, deletes, spends, publishes and approvals are guarded', () => {
  for (const t of ['Send', 'Send now', 'Post now', 'Publish', 'Delete this record', 'Erase everything', 'Pay $4', 'Approve & Continue', 'Release', 'Transfer funds', 'Share']) assert.strictEqual(core.classifyGuard(t), true, t);
});
test('guard words: stops, closes, drafts and previews are not, even beside a guarded verb', () => {
  for (const t of ['Cancel', 'Cancel send', 'Deny', 'Close', 'Draft a reply', 'Preview post', 'Undo delete', 'Reply', 'Later', 'Change', 'Compose', 'Refresh', '']) assert.strictEqual(core.classifyGuard(t), false, t);
});
test('guard: a container can force it on or off, and a danger class forces it on', () => {
  assert.strictEqual(core.classifyGuard('Open', { force: 'on' }), true);
  assert.strictEqual(core.classifyGuard('Send', { force: 'off' }), false);
  assert.strictEqual(core.classifyGuard('Clear', { danger: true }), true);
});

test('a pinch on a tick row ticks it, and a pinch held for 700 ms opens it', () => {
  assert.strictEqual(core.pinchIntent(120), 'tick');
  assert.strictEqual(core.pinchIntent(699), 'tick');
  assert.strictEqual(core.pinchIntent(700), 'open');
  assert.strictEqual(core.pinchIntent(1500), 'open');
  assert.strictEqual(core.pinchIntent(500, 400), 'open');
});

const failed = results.filter(r => r[0] === 'FAIL');
for (const [s, n] of results) console.log(s + ' ' + n);
console.log(results.length - failed.length + ' passed, ' + failed.length + ' failed');
process.exit(failed.length ? 1 : 0);
