// Node tests for the hand cursor's part in the stage (static/friday_stage.js cursor + makeAdapter selection).
// Run by tests/unit/test_hand_cursor_stage.py.
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

let clock = 1000000;
const realNow = Date.now;
Date.now = () => clock;

test('the locked target reaches the stage within one report and carries age 0 while it is held', () => {
  FS.cursor('mail:a:t7', 'locked');
  const c = FS.cursorNow(['mail:a:t7', 'mail:a:t8']);
  assert.deepStrictEqual(c, { ref: 'mail:a:t7', state: 'locked', age_s: 0 });
  clock += 200; FS.cursor('mail:a:t7', 'pinched');
  assert.strictEqual(FS.cursorNow(['mail:a:t7']).state, 'pinched');
});

test('leaving the row keeps it as "this" for three seconds, with its age, then forgets it', () => {
  FS.cursor('mail:a:t7', 'locked');
  FS.cursor('', '');                                   // the reticle moved onto nothing
  clock += 1500;
  const c = FS.cursorNow(['mail:a:t7']);
  assert.strictEqual(c.ref, 'mail:a:t7'); assert.strictEqual(c.state, 'locked'); assert.ok(c.age_s >= 1.4 && c.age_s <= 1.6, String(c.age_s));
  clock += 2000;
  assert.strictEqual(FS.cursorNow(['mail:a:t7']), null);
});

test('a ref that is not on this list is not reported by this list', () => {
  FS.cursor('mail:a:t9', 'locked');
  assert.strictEqual(FS.cursorNow(['mail:a:t1']), null);
  assert.ok(FS.cursorNow(['mail:a:t9']));
});

test('a guarded control is never reported: the hand cursor passes an empty ref for it', () => {
  FS.cursor('mail:a:t1', 'locked');
  FS.cursor('', '');                                   // what hand_cursor.js reports for a guarded target
  clock += 4000;
  assert.strictEqual(FS.cursorNow(['mail:a:t1']), null);
  const fs = require('fs');
  const hc = fs.readFileSync(path.resolve(__dirname, '..', '..', 'static', 'hand_cursor.js'), 'utf8');
  const fn = hc.slice(hc.indexOf('function reportCursor'), hc.indexOf('// A list row that opts in'));
  assert.ok(fn.indexOf('!t.guarded') > 0 && fn.indexOf('!t.orb') > 0, 'only a non-guarded row target is a ref');
});

test('the stage the adapter reports includes the cursor and the keyboard row', () => {
  FS.cursor('lib:d:12', 'locked');
  const ad = FS.makeAdapter('library', () => ({ items: () => [{ ref: 'lib:d:12', n: 1, facets: {}, title: 't', who: '' }], focus: () => 'lib:d:12' }));
  const st = ad.stage();
  assert.strictEqual(st.cursor.ref, 'lib:d:12'); assert.strictEqual(st.focus, 'lib:d:12');
});

test('select, clear and held go through the shared reducer and the answer is what the list took', async () => {
  let sel = FS.emptySelection();
  const rows = ['media:c1', 'media:c2', 'media:c3'];
  const cfg = { items: () => rows.map((r, i) => ({ ref: r, n: i + 1, facets: {}, title: 't', who: '' })), getSel: () => sel, setSel: s => { sel = s; }, rowsGone: (refs) => { cfg.gone = refs; } };
  const ad = FS.makeAdapter('media', () => cfg);
  const out = await ad.run({ type: 'select', selection: { id: 'sel_1', refs: ['media:c1', 'media:c2', 'media:zz'], label: 'Drafts', mode: 'replace' } });
  assert.deepStrictEqual([out.ok, out.applied, out.missing, out.accepted, out.count], [true, 2, 1, 3, 3]);
  assert.strictEqual(ad.stage().selection.beyond_loaded, 1); assert.strictEqual(ad.stage().selection.source, 'friday');
  await ad.run({ type: 'held', state: 'held', refs: ['media:c1'], card_id: 'ap_1' });
  assert.strictEqual(ad.stage().held[0].refs_count, 1);
  await ad.run({ type: 'held', state: 'done', refs: ['media:c1', 'media:c2'], card_id: 'ap_1' });
  assert.deepStrictEqual(cfg.gone, ['media:c1', 'media:c2']);
  assert.deepStrictEqual(sel.refs, ['media:zz']);
  const cl = await ad.run({ type: 'clear_selection' });
  assert.strictEqual(cl.count, 0); assert.strictEqual(sel.refs.length, 0);
});

Promise.all(pending).then(() => {
  Date.now = realNow;
  let failed = 0;
  for (const [s, n] of results) { if (s !== 'ok') failed++; console.log(s === 'ok' ? 'PASS' : 'FAIL', n); }
  console.log(results.length - failed + ' passed, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
});
