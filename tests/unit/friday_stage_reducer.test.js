// Node tests for static/friday_stage.js. Run by tests/unit/test_friday_stage_reducer.py.
const assert = require('assert');
const path = require('path');
const FS = require(path.resolve(__dirname, '..', '..', 'static', 'friday_stage.js'));

const results = [], pending = [];
function test(name, fn) {
  try {
    const r = fn();
    if (r && typeof r.then === 'function') pending.push(r.then(() => results.push(['ok', name]), e => results.push(['FAIL', name + ': ' + e.message])));
    else results.push(['ok', name]);
  } catch (e) { results.push(['FAIL', name + ': ' + e.message]); }
}
const refs = (n, from) => Array.from({ length: n }, (_, i) => 'mail:a:t' + ((from || 1) + i));
const red = (s, e, known) => FS.reduceSelection(s, e, known || []);

test('replace ticks exactly the refs and counts what the list shows and what it does not', () => {
  const r = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: refs(10), id: 'sel_1', label: 'Newsletters' }, refs(6));
  assert.strictEqual(r.state.refs.length, 10);
  assert.strictEqual(r.applied, 6); assert.strictEqual(r.missing, 4); assert.strictEqual(r.accepted, 10); assert.strictEqual(r.count, 10);
  assert.strictEqual(r.state.source, 'friday'); assert.strictEqual(r.state.label, 'Newsletters');
});
test('replace replaces; add unions; remove subtracts', () => {
  let s = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: refs(3), id: 'a', label: 'A' }).state;
  s = red(s, { type: 'select', mode: 'replace', refs: refs(2, 10), id: 'b', label: 'B' }).state;
  assert.deepStrictEqual(s.refs, refs(2, 10));
  s = red(s, { type: 'select', mode: 'add', refs: refs(2, 11) }).state;
  assert.deepStrictEqual(s.refs, ['mail:a:t10', 'mail:a:t11', 'mail:a:t12']);
  s = red(s, { type: 'select', mode: 'remove', refs: ['mail:a:t11'] }).state;
  assert.deepStrictEqual(s.refs, ['mail:a:t10', 'mail:a:t12']);
  assert.strictEqual(s.label, 'B', 'the name survives an edit');
});
test('removing the last ref empties the selection and its name', () => {
  let s = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: ['mail:a:t1'], id: 'a', label: 'A' }).state;
  s = red(s, { type: 'select', mode: 'remove', refs: ['mail:a:t1'] }).state;
  assert.deepStrictEqual(s.refs, []); assert.strictEqual(s.label, ''); assert.strictEqual(s.source, '');
});
test('an owner toggle on a Friday selection makes it mixed and keeps its id; on none it is the owner\'s', () => {
  let s = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: refs(3), id: 'sel_9', label: 'Newsletters' }).state;
  s = red(s, { type: 'toggle', ref: 'mail:a:t2' }).state;
  assert.strictEqual(s.source, 'mixed'); assert.strictEqual(s.id, 'sel_9'); assert.strictEqual(s.refs.length, 2);
  assert.ok(FS.chipText(s).indexOf('(edited)') > 0, FS.chipText(s));
  const o = red(FS.emptySelection(), { type: 'toggle', ref: 'mail:a:t1' }).state;
  assert.strictEqual(o.source, 'owner'); assert.strictEqual(o.id, '');
});
test('Friday adding to the owner\'s ticks makes it mixed', () => {
  let s = red(FS.emptySelection(), { type: 'toggle', ref: 'mail:a:t1' }).state;
  s = red(s, { type: 'select', mode: 'add', refs: ['mail:a:t5'] }).state;
  assert.strictEqual(s.source, 'mixed');
});
test('the owner changing folder or search clears; Friday\'s own reveal does not', () => {
  const s = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: refs(3), id: 'a', label: 'A' }).state;
  assert.strictEqual(red(s, { type: 'owner_nav' }).state.refs.length, 0);
  assert.strictEqual(red(s, { type: 'friday_reveal' }).state.refs.length, 3);
  assert.strictEqual(red(s, { type: 'friday_reveal' }).changed, false);
});
test('held, done and declined: done removes the rows, declined keeps the ticks', () => {
  let s = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: refs(3), id: 'a', label: 'A' }).state;
  s = red(s, { type: 'held', state: 'held', refs: refs(3), card_id: 'ap_1' }).state;
  assert.strictEqual(Object.keys(s.held).length, 3);
  const dec = red(s, { type: 'held', state: 'declined', refs: refs(3), card_id: 'ap_1' }).state;
  assert.strictEqual(dec.refs.length, 3); assert.strictEqual(Object.keys(dec.held).length, 0);
  const done = red(s, { type: 'held', state: 'done', refs: refs(3), card_id: 'ap_1' }).state;
  assert.strictEqual(done.refs.length, 0); assert.strictEqual(Object.keys(done.held).length, 0);
  const part = red(s, { type: 'held', state: 'done', refs: refs(2), card_id: 'ap_1' }).state;
  assert.deepStrictEqual(part.refs, ['mail:a:t3']);
});
test('a pending card keeps holding its rows however the owner moves around the list', () => {
  let s = red(FS.emptySelection(), { type: 'held', state: 'held', refs: refs(2), card_id: 'ap_1' }).state;
  s = red(s, { type: 'owner_nav' }).state;
  s = red(s, { type: 'clear' }).state;
  s = red(s, { type: 'select', mode: 'replace', refs: refs(5), id: 'a', label: 'A' }).state;
  s = red(s, { type: 'owner_set', refs: refs(1) }).state;
  assert.strictEqual(Object.keys(s.held).length, 2);
  s = red(s, { type: 'held', state: 'expired', refs: refs(2), card_id: 'ap_1' }).state;
  assert.strictEqual(Object.keys(s.held).length, 0);
});
test('rev rises on every change and stays put on a no-op', () => {
  let s = FS.emptySelection(), last = s.rev;
  const steps = [{ type: 'select', mode: 'replace', refs: refs(2), id: 'a', label: 'A' }, { type: 'toggle', ref: 'mail:a:t1' },
    { type: 'held', state: 'held', refs: ['mail:a:t2'], card_id: 'c' }, { type: 'clear' }];
  for (const e of steps) { s = red(s, e).state; assert.ok(s.rev > last, JSON.stringify(e)); last = s.rev; }
  const again = red(s, { type: 'clear' });
  assert.strictEqual(again.changed, false); assert.strictEqual(again.state.rev, last);
});
test('refs beyond the cap are not taken, and accepted says so', () => {
  const r = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: refs(700), id: 'a', label: 'A' });
  assert.strictEqual(r.state.refs.length, FS.MAX_REFS); assert.strictEqual(r.accepted, FS.MAX_REFS);
});
test('duplicate refs are counted once', () => {
  const r = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: ['mail:a:t1', 'mail:a:t1', 'mail:a:t2'] });
  assert.strictEqual(r.count, 2);
});
test('the sweep ends inside 350 ms however many rows, and staggers top to bottom', () => {
  for (const n of [1, 2, 6, 80, 120, 500]) {
    const d = FS.sweepDelays(n);
    assert.strictEqual(d.length, n);
    assert.ok(Math.max.apply(null, d) + FS.SWEEP_ROW_MS <= 350, 'n=' + n);
    for (let i = 1; i < n; i++) assert.ok(d[i] >= d[i - 1]);
  }
});
test('the limiter allows one change per element per 334 ms', () => {
  let t = 1000; const allow = FS.limiter(() => t);
  assert.ok(allow('row1')); t += 100; assert.ok(!allow('row1')); assert.ok(allow('row2'));
  t += 240; assert.ok(allow('row1'));
});
test('the chip says who selected', () => {
  const s = red(FS.emptySelection(), { type: 'select', mode: 'replace', refs: refs(142), id: 'a', label: 'Newsletters' }).state;
  assert.strictEqual(FS.chipText(s), '✓ Newsletters · 142 · Friday selected');
  assert.strictEqual(FS.chipText(FS.emptySelection()), '');
});
test('the stage runs a command through the registered adapter and answers with its stage', async () => {
  const seen = [];
  const off = FS.register('messages', { stage: () => ({ rev: 3 }), run: a => { seen.push(a.type); return { ok: true, applied: 2 }; } });
  assert.ok(FS.registered('messages') && FS.handles('select') && !FS.handles('navigate'));
  const out = await FS.run({ type: 'select', workspace: 'messages' });
  assert.deepStrictEqual(out, { result: { ok: true, applied: 2 }, stage: { rev: 3 } });
  off(); assert.ok(!FS.registered('messages'));
  const none = await FS.run({ type: 'select', workspace: 'messages' });
  assert.strictEqual(none.result.ok, false);
});

Promise.all(pending).then(() => {
  let failed = 0;
  for (const [s, n] of results) { if (s !== 'ok') failed++; console.log(s === 'ok' ? 'PASS' : 'FAIL', n); }
  console.log(results.length - failed + ' passed, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
});
