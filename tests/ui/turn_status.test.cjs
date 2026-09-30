// The long-turn status line under the chat ("Still working (5m, round 4) —
// Reasoning (step 4)") is built from /api/chat/turn/<id>/liveness. Its
// contract is `step` = an integer round and `label` = text; the line must stay
// readable when a payload breaks that contract instead of printing
// "round [object Object]".
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

function statusText() {
  const start = 'function fridayTurnStatusText(', end = '// End turn status text';
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'the status line has one named formatter');
  const context = vm.createContext({});
  vm.runInContext(source.slice(from, to) + '\nthis.f = fridayTurnStatusText;', context);
  return context.f;
}

test('the chat indicator renders the status through the formatter', () => {
  assert.match(source, /fridayTurnStatusText\(turnStatus\)/);
  assert.doesNotMatch(source, /", round " \+ turnStatus\.step/);
});

test('an integer round and a text label read as intended', () => {
  const f = statusText();
  assert.equal(f({elapsed_s: 312, step: 4, label: 'Reasoning (step 4)'}),
    'Still working (5m, round 4) — Reasoning (step 4)');
  assert.equal(f(null), 'Friday is thinking');
});

test('a step record where the round belongs is left out, never printed', () => {
  const f = statusText();
  const bad = [{type: 'tool', name: 'search_wiki'}, '4', 4.5, true, [4], NaN, -1];
  for (const step of bad) {
    const text = f({elapsed_s: 312, step, label: 'Reasoning (step 4)'});
    assert.equal(text, 'Still working (5m) — Reasoning (step 4)', String(step));
  }
  for (const label of [{text: 'x'}, ['a'], 7]) {
    const text = f({elapsed_s: 30, step: 2, label});
    assert.equal(text, 'Still working (30s, round 2)');
  }
});

test('the quiet branch tolerates missing numbers', () => {
  const text = statusText()({quiet: true, elapsed_s: undefined, quiet_for_s: {}});
  assert.ok(!/object Object|NaN|undefined/.test(text), text);
});
