// The first-run conversation's step cards, rendered from both UI sources with a
// minimal React stand-in: nothing is selected before the person chooses, the
// recommended option is labelled, an option this computer cannot offer is
// shown with its reason and cannot be pressed, and the receipt says who
// decided each line.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

function slice(source, name) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name + ' exists');
  const end = source.indexOf('\n}\n', start) + 3;
  return source.slice(start, end);
}

function harness(source, names) {
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) });
  const React = { createElement, useState: init => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: v => ({ current: v }), Fragment: 'Fragment' };
  const ctx = vm.createContext({ React, fridayName: () => 'Friday', apiFetch: () => Promise.resolve({}),
    scJSON: () => Promise.resolve({ ok: true, data: {} }), console });
  vm.runInContext(names.map(n => slice(source, n)).join('\n'), ctx);
  return ctx;
}

function walk(node, fn) {
  if (!node || typeof node !== 'object') return;
  fn(node);
  for (const c of node.children || []) walk(c, fn);
}

function render(node) {
  // Expand function components one level at a time until only host elements remain.
  if (!node || typeof node !== 'object') return node;
  if (typeof node.type === 'function') return render(node.type({ ...node.props, children: node.children }));
  return { ...node, children: (node.children || []).map(render) };
}

function text(node) {
  let out = '';
  walk(node, n => { for (const c of n.children || []) if (typeof c === 'string' || typeof c === 'number') out += c; });
  return out;
}

const POSTURE = {
  recommended: 'public',
  cards: [
    { value: 'local', label: 'Only on this PC', means: 'Every model runs here.', costs: 'No cost.', recommended: false,
      available: false, reason: "This computer can't run thinking well enough on its own" },
    { value: 'public', label: 'Cloud for public things', means: 'Public things may use the cloud.', costs: 'Your bill.',
      recommended: true, available: true, reason: '' },
    { value: 'everything', label: 'Cloud for everything', means: 'Chat may use the cloud.', costs: 'Your bill.',
      recommended: false, available: true, reason: '' },
  ],
};

// index.html is the served UI (precompiled); ui_parts/app.html is its JSX
// mirror, so the cards are rendered from the first and the mirror is held to
// the same components and strings.
for (const file of ['index.html']) {
  const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');

  test(`${file}: the posture card labels the recommended one and offers nothing it cannot keep`, () => {
    const ctx = harness(source, ['SetupPostureCard']);
    const picked = [];
    const tree = render(ctx.SetupPostureCard({ posture: POSTURE, busy: false, onPick: v => picked.push(v) }));
    const buttons = [];
    walk(tree, n => { if (n.type === 'button') buttons.push(n); });
    assert.equal(buttons.length, 3);
    assert.equal(buttons[0].props.disabled, true, 'the local card cannot be chosen here');
    assert.equal(buttons[1].props.disabled, false);
    assert.match(text(tree), /can't run thinking well enough on its own\./);
    const recs = [];
    walk(tree, n => { if (n.props && n.props.className === 'sc-rec') recs.push(text(n)); });
    assert.deepEqual(recs, ['recommended']);
    // nothing chosen until a press
    let selected = 0;
    walk(tree, n => { if (n.props && (n.props.checked || n.props['aria-checked'] === true || / on\b/.test(n.props.className || ''))) selected++; });
    assert.equal(selected, 0);
    buttons[1].props.onClick();
    assert.deepEqual(picked, ['public']);
  });

  test(`${file}: routines start unticked and save sends every routine with on false`, () => {
    const ctx = harness(source, ['SetupRoutinesCard']);
    let sent = null;
    const routines = [
      { id: 'sch_news_morning', label: 'Morning front page', what: 'news', when: 'every day at 07:00',
        time: '07:00', where: 'runs on this PC', finish_notice: true },
      { id: 'podcast', label: 'Podcast', what: 'spoken', when: 'after each edition', time: '', where: 'runs on this PC',
        finish_notice: true },
    ];
    const tree = render(ctx.SetupRoutinesCard({ routines, busy: false, onSave: v => { sent = v; } }));
    const boxes = [];
    walk(tree, n => { if (n.type === 'input' && n.props.type === 'checkbox') boxes.push(n); });
    assert.equal(boxes.length, 2, 'one tick box per routine, no extra options until ticked');
    assert.ok(boxes.every(b => b.props.checked === false));
    let save = null;
    walk(tree, n => { if (n.type === 'button') save = n; });
    assert.equal(text(save), 'Schedule none');
    save.props.onClick();
    assert.deepEqual(Object.keys(sent.routines), ['sch_news_morning', 'podcast']);
    assert.ok(Object.values(sent.routines).every(r => r.on === false));
  });

  test(`${file}: the receipt names who decided each line`, () => {
    const ctx = harness(source, ['SetupReceipt']);
    const tree = render(ctx.SetupReceipt({ rows: [
      { label: 'Where I think', value: 'Cloud for public things', how: 'you chose' },
      { label: 'Monthly ceiling', value: '$10.00 a month', how: 'recommended, accepted by you' },
      { label: 'My pronouns', value: 'my name instead', how: 'default because skipped' },
    ], title: 'What we set up', note: 'note' }));
    const t = text(tree);
    for (const s of ['Who decided', 'you chose', 'recommended, accepted by you', 'default because skipped']) {
      assert.ok(t.includes(s), s);
    }
  });

}

for (const file of ['index.html', 'ui_parts/app.html']) {
  const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');

  test(`${file}: the step cards exist in the served UI and its mirror`, () => {
    for (const name of ['SetupReceipt', 'SetupVoicesCard', 'SetupAboutCard', 'SetupPostureCard',
      'SetupRoutinesCard', 'SetupMemoryRulesCard', 'scLocalVoice']) {
      assert.ok(source.includes('function ' + name + '('), name);
    }
  });

  test(`${file}: the conversation sends its time zone, offers the recommended button and never times out`, () => {
    const chat = slice(source, 'SetupChat');
    assert.match(chat, /client_tz/);
    assert.match(chat, /\/api\/setup-chat\/use-recommended/);
    assert.doesNotMatch(chat, /setTimeout\(\(\) => setLeaving\(false\)/, '"Set up later" waits for the person, not a timer');
    assert.match(chat, /Keep going/);
    // her lines are spoken only with this computer's own voice
    assert.match(source, /function scLocalVoice\(/);
    assert.match(slice(source, 'scLocalVoice'), /localService/);
    assert.doesNotMatch(chat, /\/api\/voice\/tts/);
  });
}

test('index.html: the consent screens no longer set a routing mode the person never chose', () => {
  const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const consent = slice(source, 'ConsentFlow');
  assert.doesNotMatch(consent, /mode \|\| 'cloud_only'/);
});
