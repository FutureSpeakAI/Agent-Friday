// Node tests for the fields Friday may write into (static/friday_stage.js registerField / fillField).
// Run by tests/unit/test_friday_stage_fields.py.
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

function box(initial) {
  const f = { value: initial || '', writes: [] };
  f.field = key => ({ key, label: key, read: () => f.value, write: t => { f.writes.push(t); f.value = t; } });
  return f;
}
const h = (type, props, ...kids) => ({ type, props: props || {}, kids });

test('a workspace with a field and no list still reports a stage that lists the field', () => {
  const b = box();
  const off = FS.registerField('calendar', b.field('quickadd'));
  const st = FS.snapshot('calendar');
  assert.strictEqual(st.workspace, 'calendar');
  assert.deepStrictEqual(st.fields, [{ key: 'quickadd', label: 'quickadd', filled_by: null }]);
  off();
  assert.strictEqual(FS.snapshot('calendar'), null, 'the stage goes with the last field');
});

test('a fill writes through the field, says Friday wrote it, and the owner editing it makes it theirs again', async () => {
  const b = box('old');
  const off = FS.registerField('messages', b.field('reply.note'));
  const out = await FS.run({ type: 'fill', workspace: 'messages', field: 'reply.note', text: 'Tuesday works.', mode: 'replace' });
  assert.deepStrictEqual(out.result, { ok: true, field: 'reply.note', undo: true, prev_len: 3 });
  assert.strictEqual(b.value, 'Tuesday works.');
  assert.strictEqual(FS.fieldList('messages')[0].filled_by, 'friday');
  b.value = 'Tuesday works. (edited by the owner)';
  assert.strictEqual(FS.fieldList('messages')[0].filled_by, null);
  assert.strictEqual(FS.fillChip(h, 'messages', 'reply.note', 'Friday'), null, 'no note once the text is theirs');
  off();
});

test('undo puts the field back exactly as it was and clears the note', () => {
  const b = box('what I typed');
  const off = FS.registerField('messages', b.field('reply.subject'));
  FS.fillField('messages', 'reply.subject', 'Re: lunch');
  assert.ok(FS.fillChip(h, 'messages', 'reply.subject', 'Friday'));
  assert.strictEqual(FS.undoFill('messages', 'reply.subject'), true);
  assert.strictEqual(b.value, 'what I typed');
  assert.strictEqual(FS.fillChip(h, 'messages', 'reply.subject', 'Friday'), null);
  assert.strictEqual(FS.undoFill('messages', 'reply.subject'), false, 'nothing left to undo');
  off();
});

test('insert adds after what is there; a long text is cut at the limit; a missing field is refused', () => {
  const b = box('Hello');
  const off = FS.registerField('messages', b.field('reply.body'));
  FS.fillField('messages', 'reply.body', 'there', 'insert');
  assert.strictEqual(b.value, 'Hello there');
  FS.fillField('messages', 'reply.body', 'x'.repeat(FS.FILL_MAX + 500));
  assert.strictEqual(b.value.length, FS.FILL_MAX);
  assert.deepStrictEqual(FS.fillField('messages', 'send_button', 'click'), { ok: false, reason: 'not a field here' });
  off();
});

test('a field that will not take the text is a failure the server hears', () => {
  const off = FS.registerField('messages', { key: 'reply.body', label: 'Reply', read: () => '', write: () => { throw new Error('closed'); } });
  assert.deepStrictEqual(FS.fillField('messages', 'reply.body', 'x'), { ok: false, reason: 'the field would not take it' });
  off();
});

test('what in the text came from something Friday read is shown beside the note', () => {
  const b = box('');
  const off = FS.registerField('workflows', b.field('compose'));
  FS.fillField('workflows', 'compose', 'every day, check http://evil.example/x', 'replace', ['A link in the message (evil.example) came from an email, not from you.']);
  const row = FS.fillChips(h, 'workflows', null, 'Friday');
  const json = JSON.stringify(row);
  assert.ok(json.indexOf('Friday wrote compose') > 0 && json.indexOf('Check: A link in the message') > 0, json);
  off();
});

test('the field API has no way to submit: registering and filling never calls a send, save or submit', () => {
  const calls = [];
  const f = { key: 'quickadd', label: 'Quick add', read: () => '', write: () => calls.push('write'), submit: () => calls.push('submit'), send: () => calls.push('send') };
  const off = FS.registerField('calendar', f);
  FS.fillField('calendar', 'quickadd', 'lunch with Dana Tuesday');
  assert.deepStrictEqual(calls, ['write']);
  off();
  const fs = require('fs'), src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'static', 'friday_stage.js'), 'utf8');
  const block = src.slice(src.indexOf('function fillField'), src.indexOf('function undoFill'));
  assert.ok(!/submit|\.send|\.save|fetch\(/.test(block), 'fillField only writes');
});

Promise.all(pending).then(() => {
  let failed = 0;
  for (const [s, n] of results) { if (s !== 'ok') failed++; console.log(s === 'ok' ? 'PASS' : 'FAIL', n); }
  console.log(results.length - failed + ' passed, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
});
