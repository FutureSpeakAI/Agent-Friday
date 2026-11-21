/* The approval slot component (NS-41.3-1), run without a browser against a tiny fake of React.
 * Rules held (each by a named check below, and shown able to fail by the negative controls):
 *  - index.html and ui_parts/app.html carry the same component text;
 *  - the slot draws only cards the server named, from the shell's feed, in the server's order;
 *  - nothing but `subject` and `kind` from the document reaches the slot's output (V12);
 *  - a failed or refused ask leaves an empty slot, not a card;
 *  - the frame is styled by shell tokens only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = process.argv[2] ? path.resolve(process.argv[2]) : path.join(__dirname, '..');   // a root argument lets a test check a copy
function source(file) {
  const text = fs.readFileSync(path.join(root, file), 'utf8');
  const start = text.indexOf('function ApprovalSlot(');
  assert.ok(start >= 0, file + ' has no ApprovalSlot');
  const end = text.indexOf('window.FridayApprovalSlot = ApprovalSlot;', start);
  assert.ok(end > start, file + ' does not export ApprovalSlot');
  return text.slice(start, end);
}
const html = source('index.html');
assert.equal(source('ui_parts/app.html'), html, 'index.html and ui_parts/app.html must carry the same ApprovalSlot');

function load(src, env) {
  return new Function('React', 'useState', 'useEffect', 'useApprovalFeed', 'fetch', 'fridayDecideApproval',
    'ApprovalCardBody', src + '\nreturn ApprovalSlot;')(
    env.React, env.useState, env.useEffect, env.useApprovalFeed, env.fetch, env.decide, 'ApprovalCardBody');
}
async function render(src, props, feed, answer) {
  const asked = [];
  const effects = [];
  const state = [];
  let cursor = 0;
  const env = {
    React: { createElement: (type, p, ...children) => ({ type, props: p || {}, children: children.flat() }) },
    useState: (init) => { const i = cursor++; if (!(i in state)) state[i] = init; return [state[i], (v) => { state[i] = v; }]; },
    useEffect: (fn) => { effects.push(fn); },
    useApprovalFeed: () => feed,
    fetch: (url) => { asked.push(url); return answer(url); },
    decide: () => Promise.resolve(),
  };
  const component = load(src, env);
  component(props);
  for (const fn of effects.splice(0)) fn();
  await new Promise((r) => setTimeout(r, 0));
  cursor = 0;
  return { out: component(props), asked };
}
// One mounted slot whose props change: the state survives, the second answer is held back.
async function renderChange(src, first, second, feed, firstAnswer) {
  const effects = [];
  const state = [];
  let cursor = 0;
  let answer = firstAnswer;
  const env = {
    React: { createElement: (type, p, ...children) => ({ type, props: p || {}, children: children.flat() }) },
    useState: (init) => { const i = cursor++; if (!(i in state)) state[i] = init; return [state[i], (v) => { state[i] = v; }]; },
    useEffect: (fn) => { effects.push(fn); },
    useApprovalFeed: () => feed,
    fetch: () => answer(),
    decide: () => Promise.resolve(),
  };
  const component = load(src, env);
  component(first);
  for (const fn of effects.splice(0)) fn();
  await new Promise((r) => setTimeout(r, 0));
  cursor = 0;
  component(first);
  answer = () => new Promise(() => {});
  cursor = 0;
  component(second);
  for (const fn of effects.splice(0)) fn();
  cursor = 0;
  return component(second);
}
// The answer for the OLD subject arrives after the props changed to the new one.
async function renderLateAnswer(src, first, second, feed, lateBody) {
  const effects = [];
  const state = [];
  let cursor = 0;
  let release;
  let calls = 0;
  const env = {
    React: { createElement: (type, p, ...children) => ({ type, props: p || {}, children: children.flat() }) },
    useState: (init) => { const i = cursor++; if (!(i in state)) state[i] = init; return [state[i], (v) => { state[i] = v; }]; },
    useEffect: (fn) => { effects.push(fn); },
    useApprovalFeed: () => feed,
    fetch: () => (++calls === 1 ? new Promise((r) => { release = () => r({ json: () => Promise.resolve(lateBody) }); }) : new Promise(() => {})),
    decide: () => Promise.resolve(),
  };
  const component = load(src, env);
  component(first);
  for (const fn of effects.splice(0)) fn();
  cursor = 0;
  component(second);
  for (const fn of effects.splice(0)) fn();
  release();
  await new Promise((r) => setTimeout(r, 0));
  cursor = 0;
  return component(second);
}
const ok = (body) => () => Promise.resolve({ json: () => Promise.resolve(body) });
const card = (id) => ({ approval_id: id, title: 'T ' + id });

(async () => {
  // Only the cards the server named, in its order; others in the feed are left out.
  let r = await render(html, { subject: 'crew:c1' }, [card('a'), card('b'), card('c')], ok({ ok: true, ids: ['c', 'a'] }));
  assert.deepEqual(r.out.children.map((c) => c.props.a.approval_id), ['c', 'a']);
  assert.equal(r.asked.length, 1);
  assert.match(r.asked[0], /^\/api\/liquid\/approval-slot\?subject=crew%3Ac1$/);

  // Kind is passed on, encoded.
  r = await render(html, { subject: 'crew:c1', kind: 'send email' }, [card('a')], ok({ ok: true, ids: ['a'] }));
  assert.match(r.asked[0], /&kind=send%20email$/);

  // A named id the feed does not hold draws nothing.
  r = await render(html, { subject: 'crew:c1' }, [card('a')], ok({ ok: true, ids: ['zzz'] }));
  assert.equal(r.out, null);

  // Nothing waiting: the shell's own line. Refused, malformed or unreachable: a line that says it could
  // not check, never "nothing is waiting" and never a card.
  const line = (o) => o.children[0].children[0];
  r = await render(html, { subject: 'crew:c1' }, [card('a')], ok({ ok: true, ids: [] }));
  assert.equal(r.out.props['data-testid'], 'approval-slot');
  assert.equal(line(r.out), 'Nothing is waiting here.');
  assert.equal(r.out.children[0].props.role, 'status', 'the empty and failed lines are announced');
  for (const answer of [ok({ ok: false, error: 'bad_subject' }), ok({ ok: true, ids: 'a' }), () => Promise.reject(new Error('down'))]) {
    r = await render(html, { subject: 'crew:c1' }, [card('a')], answer);
    assert.equal(line(r.out), 'Could not check what is waiting.');
    assert.ok(!JSON.stringify(r.out).includes('"a"'), 'no card on a failed ask');
  }
  // Still asking: nothing yet, so a slow answer never flashes "nothing is waiting".
  r = await render(html, { subject: 'crew:c1' }, [card('a')], () => new Promise(() => {}));
  assert.equal(r.out, null);

  // A changed subject never shows the previous subject's cards while its own answer is still out.
  let out = await renderChange(html, { subject: 'crew:a' }, { subject: 'crew:b' }, [card('A1'), card('B1')], ok({ ok: true, ids: ['A1'] }));
  assert.equal(out, null, 'the slot for crew:b must not hold the card of crew:a');
  out = await renderChange(html, { subject: 'crew:a', kind: 'x' }, { subject: 'crew:a', kind: 'y' }, [card('A1')], ok({ ok: true, ids: ['A1'] }));
  assert.equal(out, null, 'a changed kind is a different ask too');

  out = await renderLateAnswer(html, { subject: 'crew:a' }, { subject: 'crew:b' }, [card('A1'), card('B1')], { ok: true, ids: ['A1'] });
  assert.equal(out, null, 'a late answer for crew:a must not be drawn in the slot for crew:b');

  // The document's own extras never reach the output (V12): no id, text, style, children or label.
  r = await render(html, { subject: 'crew:c1', id: 'x', className: 'evil', style: { color: 'red' }, children: 'hi', label: 'Approve all', 'aria-label': 'Approve' },
    [card('a')], ok({ ok: true, ids: ['a'] }));
  assert.equal(r.out.props['aria-label'], 'Waiting for your decision');
  assert.equal(r.out.props.className, undefined);
  assert.equal(r.out.props.id, undefined);
  assert.ok(!JSON.stringify(r.out.props.style).includes('red'));
  assert.ok(!JSON.stringify(r.out).includes('Approve all') && !JSON.stringify(r.out).includes('"hi"'));

  // The frame uses shell tokens only: every colour value in its style is a var(--fr-*).
  const style = r.out.props.style;
  for (const key of ['border', 'background', 'color']) assert.match(style[key], /var\(--fr-[a-z-]+\)/, key);
  assert.ok(!/#|rgb|hsl/i.test(JSON.stringify(style)), 'no literal colours in the frame');

  // Negative controls: the checks above can fail.
  const leaky = html.replace("'aria-label': 'Waiting for your decision'", "'aria-label': subject");
  r = await render(leaky, { subject: 'crew:c1' }, [card('a')], ok({ ok: true, ids: ['a'] }));
  assert.throws(() => assert.equal(r.out.props['aria-label'], 'Waiting for your decision'));
  const everything = html.replace('ids.map(id => items.find(a => a.approval_id === id)).filter(Boolean)', 'items');
  r = await render(everything, { subject: 'crew:c1' }, [card('a'), card('b')], ok({ ok: true, ids: ['a'] }));
  assert.throws(() => assert.deepEqual(r.out.children.map((c) => c.props.a.approval_id), ['a']));
  const unkeyed = html.replace('got && got.asked === asked ? got.ids : null', 'got ? got.ids : null');
  assert.notEqual(unkeyed, html, 'the mutant must differ');
  out = await renderLateAnswer(unkeyed, { subject: 'crew:a' }, { subject: 'crew:b' }, [card('A1'), card('B1')], { ok: true, ids: ['A1'] });
  assert.notEqual(out, null, 'negative control: without the key the late answer for crew:a is drawn under crew:b');
  out = await renderChange(unkeyed, { subject: 'crew:a' }, { subject: 'crew:b' }, [card('A1'), card('B1')], ok({ ok: true, ids: ['A1'] }));
  assert.notEqual(out, null, 'negative control: without the key the old ids are drawn under the new subject');
  console.log('approval slot: all checks hold');
})().catch((e) => { console.error(e); process.exit(1); });
