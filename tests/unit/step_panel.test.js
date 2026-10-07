// Node tests for the step list reducer (static/friday_stage.js reduceSteps / stepsRunning).
// Run by tests/unit/test_step_panel_reducer.py.
const assert = require('assert');
const path = require('path');
const FS = require(path.resolve(__dirname, '..', '..', 'static', 'friday_stage.js'));

const results = [];
function test(name, fn) {
  try { fn(); results.push(['ok', name]); } catch (e) { results.push(['FAIL', name + ': ' + e.message]); }
}
const start = { type: 'steps', id: 'w1', title: 'Morning brief', steps: [{ n: 1, text: 'Gather', state: 'doing' }, { n: 2, text: 'Write', state: 'waiting' }] };

test('a steps event replaces the list', () => {
  const l = FS.reduceSteps(null, start);
  assert.strictEqual(l.title, 'Morning brief');
  assert.deepStrictEqual(l.steps.map(s => s.state), ['doing', 'waiting']);
});
test('a step_update changes one step and leaves the others', () => {
  const l = FS.reduceSteps(FS.reduceSteps(null, start), { type: 'step_update', id: 'w1', n: 1, state: 'done' });
  assert.deepStrictEqual(l.steps.map(s => s.state), ['done', 'waiting']);
});
test('an update for another list is ignored', () => {
  const l0 = FS.reduceSteps(null, start);
  assert.strictEqual(FS.reduceSteps(l0, { type: 'step_update', id: 'other', n: 1, state: 'done' }), l0);
});
test('an unknown state is refused and an unknown start state becomes waiting', () => {
  const l0 = FS.reduceSteps(null, start);
  assert.strictEqual(FS.reduceSteps(l0, { type: 'step_update', id: 'w1', n: 1, state: 'exploding' }), l0);
  const l1 = FS.reduceSteps(null, { type: 'steps', id: 'x', title: 't', steps: [{ n: 1, text: 'a', state: 'bogus' }] });
  assert.strictEqual(l1.steps[0].state, 'waiting');
});
test('the list is bounded to 40 steps and 80 characters of text', () => {
  const many = Array.from({ length: 60 }, (_, i) => ({ n: i + 1, text: 'x'.repeat(200), state: 'waiting' }));
  const l = FS.reduceSteps(null, { type: 'steps', id: 'b', title: 'y'.repeat(300), steps: many });
  assert.strictEqual(l.steps.length, 40);
  assert.strictEqual(l.steps[0].text.length, 80);
  assert.strictEqual(l.title.length, 80);
});
test('stepsRunning is true while any step is waiting, doing or held, and false once all have ended', () => {
  let l = FS.reduceSteps(null, start);
  assert.strictEqual(FS.stepsRunning(l), true);
  l = FS.reduceSteps(l, { type: 'step_update', id: 'w1', n: 1, state: 'done' });
  l = FS.reduceSteps(l, { type: 'step_update', id: 'w1', n: 2, state: 'held' });
  assert.strictEqual(FS.stepsRunning(l), true, 'a step needing the owner is still live');
  l = FS.reduceSteps(l, { type: 'step_update', id: 'w1', n: 2, state: 'stopped' });
  assert.strictEqual(FS.stepsRunning(l), false);
  assert.strictEqual(FS.stepsRunning(null), false);
});
test('a null event leaves the list alone', () => {
  assert.strictEqual(FS.reduceSteps(null, null), null);
});

let failed = 0;
for (const [s, n] of results) { if (s !== 'ok') failed++; console.log(s === 'ok' ? 'PASS' : 'FAIL', n); }
console.log(results.length - failed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
