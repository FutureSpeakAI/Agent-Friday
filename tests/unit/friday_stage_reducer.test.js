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

// ── pointing and filter chips (a tiny fake DOM; the real renderer runs in the page) ──
function fakeDom(refsOnScreen) {
  const listeners = {};
  const els = {};
  refsOnScreen.forEach(r => {
    els[r] = { attrs: {}, style: { position: '' }, setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
      scrollIntoView() { this.scrolled = true; } };
  });
  global.document = {
    querySelector: sel => { const m = /data-fr-ref="(.*)"/.exec(sel); return m ? (els[m[1].replace(/\\(.)/g, '$1')] || null) : null; },
    addEventListener: (t, f) => { listeners[t] = f; }, removeEventListener: (t, f) => { if (listeners[t] === f) delete listeners[t]; },
    head: { appendChild() {} }, createElement: () => ({}), documentElement: { appendChild() {} }
  };
  global.getComputedStyle = () => ({ position: 'static' });
  global.matchMedia = () => ({ matches: false });
  return { els, listeners };
}
test('pointing numbers the rows in the order asked, counts only those on screen, and stops at 12', () => {
  const refs = Array.from({ length: 15 }, (_, i) => 'mail:a:t' + i);
  const dom = fakeDom(refs.filter(r => r !== 'mail:a:t1'));            // t1 is not rendered
  const out = FS.point(refs, { badges: 'numbers' });
  assert.deepStrictEqual(out, { ok: true, count: 11 });                 // 12 asked, one missing
  assert.strictEqual(dom.els['mail:a:t0'].attrs['data-fr-n'], '1');
  assert.strictEqual(dom.els['mail:a:t2'].attrs['data-fr-n'], '3', 'numbers follow the order asked, not the rows found');
  assert.strictEqual(dom.els['mail:a:t12'].attrs['data-fr-point'], undefined, 'the 13th is not outlined');
  assert.ok(dom.els['mail:a:t0'].scrolled);
  assert.strictEqual(dom.els['mail:a:t0'].style.position, 'relative');
  FS.clearPoints();
  assert.strictEqual(dom.els['mail:a:t0'].attrs['data-fr-point'], undefined);
  assert.strictEqual(dom.els['mail:a:t0'].style.position, '', 'the row\'s own position is put back');
});
test('badges can be switched off and a new pointing replaces the old one', () => {
  const dom = fakeDom(['r1', 'r2']);
  FS.point(['r1'], { badges: 'none' });
  assert.strictEqual(dom.els.r1.attrs['data-fr-n'], undefined); assert.strictEqual(dom.els.r1.attrs['data-fr-point'], 'on');
  FS.point(['r2']);
  assert.strictEqual(dom.els.r1.attrs['data-fr-point'], undefined); assert.strictEqual(dom.els.r2.attrs['data-fr-n'], '1');
  FS.clearPoints();
});
test('the adapter answers point and chips, reports who set each filter, and bumps rev only on a change', async () => {
  const dom = fakeDom(['r1', 'r2']);
  let filters = [];
  const cfg = { items: () => [{ ref: 'r1', n: 1, facets: {}, title: 't', who: 'w' }, { ref: 'r2', n: 2, facets: {}, title: 't', who: 'w' }],
    filters: () => filters, setFilter: (k, v) => { if (k === 'bad') return false; filters = filters.filter(f => f.key !== k).concat([{ key: k, value: v, label: k + ' ' + v, by: 'friday' }]); return true; },
    removeFilter: k => { filters = filters.filter(f => f.key !== k); return true; } };
  const ad = FS.makeAdapter('news', () => cfg);
  const s1 = ad.stage(), s1b = ad.stage();
  assert.strictEqual(s1.rev, s1b.rev, 'no change, no new rev');
  const p = await ad.run({ type: 'point', refs: ['r1', 'r2'], badges: 'numbers', id: 'pt_1' });
  assert.deepStrictEqual(p, { ok: true, count: 2 });
  assert.strictEqual(ad.stage().pointed.refs.length, 2);
  const c = await ad.run({ type: 'chips', set: [{ key: 'sort', value: 'time' }], remove: [] });
  assert.strictEqual(c.ok, true); assert.deepStrictEqual(c.filters.map(f => [f.key, f.by]), [['sort', 'friday']]);
  assert.ok(ad.stage().rev > s1.rev);
  const bad = await ad.run({ type: 'chips', set: [{ key: 'bad', value: 'x' }], remove: [] });
  assert.strictEqual(bad.ok, false); assert.strictEqual(bad.rejected[0].key, 'bad');
  const rm = await ad.run({ type: 'chips', set: [], remove: ['sort'] });
  assert.deepStrictEqual(rm.filters, []);
  assert.strictEqual((await ad.run({ type: 'nonsense' })).ok, false);
  FS.clearPoints();
});
test('the chips row shows only the filters Friday set, each with a way to remove it', () => {
  const h = (type, props, ...kids) => ({ type, props: props || {}, kids });
  const row = FS.chipsRow(h, [{ key: 'q', label: 'Search: x', by: 'owner' }, { key: 'sort', label: 'Newest', by: 'friday' }], () => {}, 'Friday');
  assert.strictEqual(row.kids[0].length, 1, 'only the friday chip');
  assert.ok(JSON.stringify(row).indexOf('Remove filter Newest') > 0);
  assert.strictEqual(FS.chipsRow(h, [{ key: 'q', by: 'owner' }], () => {}), null);
});
test('every ref-carrying row gets the outline base rule and the badge is drawn from tokens only', () => {
  const fs = require('fs'), src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'static', 'friday_stage.js'), 'utf8');
  const css = src.slice(src.indexOf('var STYLE = ['), src.indexOf("].join('');"));
  assert.ok(css.indexOf('[data-fr-ref]{outline:2px solid transparent') > 0);
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(css), 'no raw hex in the stage CSS');
  assert.ok(css.indexOf('var(--fr-cyan)') > 0 && css.indexOf('var(--fr-surface)') > 0);
});

Promise.all(pending).then(() => {
  let failed = 0;
  for (const [s, n] of results) { if (s !== 'ok') failed++; console.log(s === 'ok' ? 'PASS' : 'FAIL', n); }
  console.log(results.length - failed + ' passed, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
});
