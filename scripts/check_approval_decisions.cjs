/* Approval cards live in Friday's approval tray, and any card there can be decided at any time: run without a
 * browser against a tiny fake of React and fetch.
 *
 * Invariants (each held by a named check below):
 *  - no approval card renders in the chat: the chat shows at most one line ("Waiting on your approval in the
 *    tray.") with no buttons, and an approval card body is drawn only by tray surfaces;
 *  - in the tray (the popup runs for real), any card can be approved or denied at once, in any order, each sending
 *    its own request and each clearing on its own answer, whatever else is still running;
 *  - a decision in flight makes THAT card busy for the whole page and no other card (the decide request returns
 *    only after the approved action has run, so a per-surface busy flag would freeze every other card on it);
 *  - the same card clicked again while its decision is in flight sends nothing and says so;
 *  - a click that cannot apply says why (a refusal, a missing card, no connection); there is no silent no-op;
 *  - a finished or failed decision frees its card, so it can be tried again;
 *  - index.html and ui_parts/app.html carry the same tracker, decide function and surface handlers. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = process.argv[2] ? path.resolve(process.argv[2]) : path.join(__dirname, '..');   // a root argument lets a test check a copy
const FILES = ['index.html', 'ui_parts/app.html'];
const text = Object.fromEntries(FILES.map(f => [f, fs.readFileSync(path.join(root, f), 'utf8')]));
const NL = String.fromCharCode(10);

function between(file, start, end) {
  const t = text[file], i = t.indexOf(start);
  assert.ok(i >= 0, file + ' has no "' + start.trim() + '"');
  const j = t.indexOf(end, i + start.length);
  assert.ok(j > i, file + ': no "' + end.trim() + '" after "' + start.trim() + '"');
  return t.slice(i, j);
}
function fn(file, name, end) { return between(file, 'function ' + name + '(', end); }
const END = {
  note: NL + 'function ExperienceApprovals',
  experience: NL + '// The shell-owned approval slot',
  slot: NL + 'window.FridayApprovalSlot = ApprovalSlot;',
  popups: NL + '  const pos = {',
};
const TRACKER_START = '// Which cards have a decision on its way';
const decideBlock = file => between(file, TRACKER_START, '// One approval card');
const checks = [];
const check = (name, body) => checks.push([name, body]);
const tick = () => new Promise(r => setTimeout(r, 0));

// ── the real tracker and decide function, with fake page globals ───────────────────────────────────────────────
function pageWorld() {
  const toasts = [], posted = [], dropped = [], pending = {};
  const hooks = { states: [], effects: [], cursor: 0 };
  return {
    toasts, posted, dropped, pending, hooks,
    render: f => { hooks.cursor = 0; hooks.effects.length = 0; return f(); },
    fridayToast: t => toasts.push(t), fridayName: () => 'Friday', fridayApprovalFeed: { drop: id => dropped.push(id) },
    useState: init => { const s = hooks.states, i = hooks.cursor++; if (!(i in s)) s[i] = init; return [s[i], v => { s[i] = typeof v === 'function' ? v(s[i]) : v; }]; },
    useEffect: f => { hooks.effects.push(f); },
    fetch: (url, opts) => new Promise((resolve, reject) => {
      const id = decodeURIComponent(url.split('/')[3]);
      posted.push({ id, decision: JSON.parse(opts.body).decision });
      pending[id] = { resolve: body => resolve({ json: () => Promise.resolve(body) }), reject };
    }),
  };
}
function loadDecide(file, w) {
  return new Function('fetch', 'fridayToast', 'fridayName', 'fridayApprovalFeed', 'useState', 'useEffect',
    decideBlock(file) + NL + 'return { fridayDeciding, fridayDecideApproval, useDeciding };')(
    w.fetch, w.fridayToast, w.fridayName, w.fridayApprovalFeed, w.useState, w.useEffect);
}

for (const file of FILES) {
  check(file + ': three cards, decided out of order while the first is still running, each sends its own request', async () => {
    const w = pageWorld(), { fridayDecideApproval, fridayDeciding } = loadDecide(file, w);
    const first = fridayDecideApproval('a1', 'approve');                // the slow one (its action is running)
    const third = fridayDecideApproval('a3', 'deny');
    const second = fridayDecideApproval('a2', 'approve');
    assert.deepEqual(w.posted.map(p => p.id + ':' + p.decision), ['a1:approve', 'a3:deny', 'a2:approve']);
    assert.ok(fridayDeciding.get('a1') && fridayDeciding.get('a2') && fridayDeciding.get('a3'));
    w.pending.a3.resolve({ ok: true, won: true });
    await third;
    assert.deepEqual(w.dropped, ['a3']);                                // a3 cleared; a1 and a2 still running, not dropped
    assert.equal(fridayDeciding.get('a3'), undefined);
    assert.ok(fridayDeciding.get('a1') && fridayDeciding.get('a2'));
    w.pending.a1.resolve({ ok: true, won: true }); w.pending.a2.resolve({ ok: true, won: true });
    await Promise.all([first, second]);
    assert.deepEqual(w.dropped.sort(), ['a1', 'a2', 'a3']);
    assert.equal(w.toasts.length, 0);
  });
  check(file + ': the same card clicked again while its decision is running sends nothing and says so', async () => {
    const w = pageWorld(), { fridayDecideApproval } = loadDecide(file, w);
    const one = fridayDecideApproval('a1', 'approve');
    const again = fridayDecideApproval('a1', 'approve');
    assert.equal(w.posted.length, 1);
    assert.equal(again, one, 'the second click gets the same decision, not a second request');
    assert.deepEqual(w.toasts, ['Already working on that one.']);
    w.pending.a1.resolve({ ok: true, won: true }); await one;
  });
  check(file + ': a refusal says why, in the server\'s own words', async () => {
    const w = pageWorld(), { fridayDecideApproval } = loadDecide(file, w);
    const p = fridayDecideApproval('a1', 'approve');
    w.pending.a1.resolve({ ok: false, error: 'needs_approve_button', message: 'This one is approved with its own button on this PC.' });
    const d = await p;
    assert.equal(d.ok, false);
    assert.equal(w.toasts.length, 1);
    assert.match(w.toasts[0], /This one is approved with its own button on this PC\./);
    assert.deepEqual(w.dropped, [], 'a refused card is not dropped');
  });
  check(file + ': a card that is gone, or an answer with no reason, still says something', async () => {
    const w = pageWorld(), { fridayDecideApproval } = loadDecide(file, w);
    const p = fridayDecideApproval('a1', 'approve');
    w.pending.a1.resolve({ ok: false, error: 'not found' });
    await p;
    const q = fridayDecideApproval('a2', 'approve');
    w.pending.a2.resolve(null);
    await q;
    assert.equal(w.toasts.length, 2);
    assert.ok(w.toasts.every(t => /did not go through|still waiting/i.test(t)), w.toasts.join(' | '));
  });
  check(file + ': no connection says so and leaves the card waiting, and the card can be tried again', async () => {
    const w = pageWorld(), { fridayDecideApproval, fridayDeciding } = loadDecide(file, w);
    const p = fridayDecideApproval('a1', 'approve');
    w.pending.a1.reject(new Error('offline'));
    assert.equal(await p, null);
    assert.match(w.toasts[0], /Could not reach Friday to record that\. The card is still waiting\./);
    assert.equal(fridayDeciding.get('a1'), undefined, 'a failed decision frees its card');
    const retry = fridayDecideApproval('a1', 'approve');
    assert.equal(w.posted.length, 2, 'tried again');
    w.pending.a1.resolve({ ok: true, won: true }); await retry;
  });
  check(file + ': a card another page already decided says what happened', async () => {
    const w = pageWorld(), { fridayDecideApproval } = loadDecide(file, w);
    const p = fridayDecideApproval('a1', 'approve');
    w.pending.a1.resolve({ ok: true, already_decided: true, approval: { status: 'denied', decided_by: 'voice' } });
    await p;
    assert.deepEqual(w.toasts, ['Already decided: this was declined by voice.']);
  });
  check(file + ': a card shows itself busy from the shared tracker, and only that card, and is told when that changes', async () => {
    const w = pageWorld(), { fridayDecideApproval, useDeciding } = loadDecide(file, w);
    const busyOf = id => w.render(() => useDeciding(id));
    assert.equal(busyOf('a9'), false);
    for (const effect of w.hooks.effects.splice(0)) effect();           // the card subscribes when it mounts
    const before = w.hooks.states[0];
    const p = fridayDecideApproval('a9', 'approve');
    assert.notEqual(w.hooks.states[0], before, 'the card is told a decision started');
    assert.equal(busyOf('a9'), true);
    assert.equal(busyOf('a2'), false, 'another card is not busy');
    w.pending.a9.resolve({ ok: true, won: true }); await p;
    assert.equal(busyOf('a9'), false);
  });
}

// ── components, with the real code and a stand-in card body ────────────────────────────────────────────────────
function mount(src, name, deps) {
  const states = [];
  let cursor = 0;
  const args = Object.assign({
    React: { createElement: (type, p, ...children) => ({ type, props: p || {}, children: children.flat() }) },
    useState: init => { const i = cursor++; if (!(i in states)) states[i] = init; return [states[i], v => { states[i] = typeof v === 'function' ? v(states[i]) : v; }]; },
    useEffect: () => {}, useRef: init => ({ current: init }),
  }, deps);
  const f = new Function(...Object.keys(args), src + NL + 'return ' + name + ';')(...Object.values(args));
  return props => { cursor = 0; return f(props); };
}
// The tray's own list, run for real from a page file; `plan` stands in for the server's grouping.
const expandingReact = { Fragment: 'Fragment', createElement: (type, p, ...children) => typeof type === 'function'
  ? type(Object.assign({}, p, { children: children.flat() })) : { type, props: p || {}, children: children.flat() } };
function trayList(file, decide, plan) {
  const React = expandingReact;
  return mount(fn(file, 'ApprovalTrayList', NL + 'function LaneNeedsYou('), 'ApprovalTrayList', {
    React, useApprovalPlan: plan, LaneBatch: 'LaneBatch', ApprovalCardBody: 'ApprovalCardBody', fridayDecideApproval: decide });
}
const walk = (tree, visit) => { (function go(n) { if (!n || typeof n !== 'object') return; visit(n); (n.children || []).forEach(go); })(tree); };
const bodies = tree => { const out = []; walk(tree, n => { if (n.type === 'ApprovalCardBody') out.push(n.props); }); return out; };
const words = tree => { const out = []; walk(tree, n => (n.children || []).forEach(c => { if (typeof c === 'string') out.push(c); })); return out.join(''); };
const feed = [1, 2, 3].map(n => ({ approval_id: 'a' + n, title: 'Card ' + n, payload: { conversation_id: 'c1' } }));
const cardOnAnotherChat = { approval_id: 'a9', title: 'Elsewhere', payload: { conversation_id: 'c2' } };

const TRAY_ALLOWED = ['SystemWS', 'LaneBatch', 'LaneBatchItem', 'ApprovalTrayList', 'LaneNeedsYou', 'StandaloneApprovals', 'ApprovalPopups', 'ExperienceApprovals', 'ApprovalSlot'];
for (const file of FILES) {
  check(file + ': no approval card renders in the chat: ChatApprovalCards is gone and the chat says one line', () => {
    assert.ok(!text[file].includes('ChatApprovalCards'), file + ' still has ChatApprovalCards');
    assert.ok(!text[file].includes('chat-approval-cards'), file + ' still has the chat cards\' test id');
    const note = fn(file, 'ChatApprovalNote', END.note);
    assert.doesNotMatch(note, /ApprovalCardBody|fridayDecideApproval|button/, 'the chat line must have no card and no buttons');
    const render = mount(note, 'ChatApprovalNote', { useApprovalFeed: () => feed.concat([cardOnAnotherChat]) });
    const tree = render({ convId: 'c1' });
    assert.deepEqual(bodies(tree), [], 'a card body rendered in the chat');
    let buttons = 0; walk(tree, n => { if (n.type === 'button' || n.type === 'a') buttons++; });
    assert.equal(buttons, 0, 'the chat line has a button or a link');
    assert.equal(words(tree), '3 waiting on your approval in the tray.');
    assert.equal(words(mount(note, 'ChatApprovalNote', { useApprovalFeed: () => [feed[0]] })({ convId: 'c1' })), 'Waiting on your approval in the tray.');
    assert.equal(mount(note, 'ChatApprovalNote', { useApprovalFeed: () => [cardOnAnotherChat] })({ convId: 'c1' }), null, 'a card of another chat says nothing here');
    assert.equal(mount(note, 'ChatApprovalNote', { useApprovalFeed: () => feed })({ convId: '' }), null);
  });
  check(file + ': the chat page draws the one-line note, not cards', () => {
    assert.ok(/ChatApprovalNote, ?\{ ?convId: ?convId ?\}|<ChatApprovalNote convId=\{convId\} ?\/>/.test(text[file]), file + ' does not mount ChatApprovalNote with the chat\'s id');
  });
  check(file + ': an approval card body is drawn only by tray surfaces', () => {
    const t = text[file];
    const re = /createElement\(ApprovalCardBody|h\(ApprovalCardBody|<ApprovalCardBody/g;
    let m, seen = 0;
    while ((m = re.exec(t))) {
      const before = t.slice(0, m.index), at = before.lastIndexOf(NL + 'function ');
      const name = (/^function (\w+)/.exec(t.slice(at + 1)) || [])[1];
      assert.ok(TRAY_ALLOWED.includes(name), file + ': a card body is drawn inside ' + name + ', which is not a tray surface');
      seen++;
    }
    assert.ok(seen >= 5, 'found only ' + seen + ' card body sites; the check no longer sees the page');
  });
  check(file + ': the card body takes its busy state from the shared tracker, not from the surface that holds it', () => {
    const t = text[file], at = t.indexOf('function ApprovalCardBody(');
    assert.ok(at >= 0, file + ' has no ApprovalCardBody');
    assert.match(t.slice(at, at + 500), /busy = useDeciding\(a\.approval_id\);/, 'ApprovalCardBody must read busy from useDeciding(a.approval_id)');
  });
  check(file + ': System > Approvals hands its clicks on to the page-wide decision, with no busy guard of its own', () => {
    const t = text[file], at = t.search(/const decideApproval ?= ?async ?\(id, ?decision\) ?=> ?\{/);
    assert.ok(at >= 0, file + ' has no System > Approvals decide handler');
    const decide = t.slice(at, t.indexOf(NL + '  };', at));
    assert.doesNotMatch(decide, /apBusy\) ?return/, 'System > Approvals still swallows a click while one card on it is busy');
    assert.match(decide, /await fridayDecideApproval\(id, ?decision\);/);
  });
  check(file + ': the tray popup hands its clicks on to the page-wide decision, with no busy guard of its own', () => {
    const popups = fn(file, 'ApprovalPopups', END.popups);
    assert.doesNotMatch(popups, /if \(busy\) return;/, 'ApprovalPopups still swallows a click while a card on it is busy');
    assert.match(popups, /const decide = \(id, decision\) => \{\s*fridayDecideApproval\(id, decision\);\s*\};/);
  });
  for (const [name, end, props] of [['ExperienceApprovals', END.experience, {}]]) {
    check(file + ': ' + name + ' hands every click on to the page-wide decision, whatever else on it is busy', async () => {
      const calls = [];
      const decide = (id, d) => { calls.push(id + ':' + d); return new Promise(() => {}); };   // a decision that never returns
      const render = mount(fn(file, name, end), name, { useApprovalFeed: () => feed, fridayDecideApproval: decide, ApprovalCardBody: 'ApprovalCardBody' });
      bodies(render(props))[0].onDecide('a1', 'approve');
      await tick();
      bodies(render(props))[1].onDecide('a2', 'approve');                // the surface re-rendered after the first click
      bodies(render(props))[2].onDecide('a3', 'deny');
      assert.deepEqual(calls, ['a1:approve', 'a2:approve', 'a3:deny'], name + ' dropped a click');
    });
  }
  check(file + ': ApprovalSlot hands every click on to the page-wide decision, whatever else in it is busy', async () => {
    const calls = [];
    const decide = (id, d) => { calls.push(id + ':' + d); return new Promise(() => {}); };
    const fetched = () => Promise.resolve({ json: () => Promise.resolve({ ok: true, ids: ['a1', 'a2', 'a3'] }) });
    const states = []; let cursor = 0; const effects = [];
    const make = new Function('React', 'useState', 'useEffect', 'useApprovalFeed', 'fetch', 'fridayDecideApproval', 'ApprovalCardBody',
      fn(file, 'ApprovalSlot', END.slot) + NL + 'return ApprovalSlot;');
    const slot = make({ createElement: (type, p, ...c) => ({ type, props: p || {}, children: c.flat() }) },
      init => { const i = cursor++; if (!(i in states)) states[i] = init; return [states[i], v => { states[i] = v; }]; },
      f => effects.push(f), () => feed, fetched, decide, 'ApprovalCardBody');
    const props = { subject: 's', kind: '' };
    slot(props); for (const f of effects.splice(0)) f(); await tick(); await tick();
    for (const [i, [id, d]] of [['a1', 'approve'], ['a2', 'approve'], ['a3', 'deny']].entries()) { cursor = 0; bodies(slot(props))[i].onDecide(id, d); }
    assert.deepEqual(calls, ['a1:approve', 'a2:approve', 'a3:deny'], 'ApprovalSlot dropped a click');
  });
}

// The tray itself, run for real from the served page: three waiting cards, decided out of order, the first slow.
check('index.html: the tray popup, run for real: three cards decided out of order while the first is still running', async () => {
  const w = pageWorld(), { fridayDecideApproval } = loadDecide('index.html', w);
  const win = { addEventListener() {}, removeEventListener() {}, __FRIDAY_STANDALONE__: false };
  const render = mount(fn('index.html', 'ApprovalPopups', NL + '// The context slot of the top bar'), 'ApprovalPopups', {
    useApprovalFeed: () => feed, fridayDecideApproval, ApprovalTrayList: trayList('index.html', fridayDecideApproval, () => []),
    React: expandingReact, ApprovalCardBody: 'ApprovalCardBody', fridayApprovalQuiet: () => false,
    fridayAnnounce: () => {}, fridayName: () => 'Friday', window: win, document: {}, CSS: { escape: s => s }, setTimeout: () => 0,
  });
  const cards = () => bodies(render({}));
  assert.equal(cards().length, 3, 'the tray shows all three waiting cards');
  cards()[0].onDecide('a1', 'approve');                                   // slow: its action runs inside the request
  cards()[2].onDecide('a3', 'deny');                                      // out of order, while a1 is still running
  cards()[1].onDecide('a2', 'approve');
  assert.deepEqual(w.posted.map(p => p.id + ':' + p.decision), ['a1:approve', 'a3:deny', 'a2:approve'], 'the tray dropped a click');
  w.pending.a2.resolve({ ok: true, won: true }); await tick();
  w.pending.a3.resolve({ ok: true, won: true }); await tick();
  assert.deepEqual(w.dropped, ['a2', 'a3'], 'each card clears on its own answer, not on the slow one');
  w.pending.a1.resolve({ ok: true, won: true }); await tick();
  assert.deepEqual(w.dropped, ['a2', 'a3', 'a1']);
  assert.equal(w.toasts.length, 0);
});

check('index.html and ui_parts/app.html carry the same tracker, decide function and surface handlers', () => {
  assert.equal(decideBlock('index.html'), decideBlock('ui_parts/app.html'));
  for (const [name, end] of [['ChatApprovalNote', END.note], ['ExperienceApprovals', END.experience]]) {
    assert.equal(fn('index.html', name, end), fn('ui_parts/app.html', name, end), name + ' differs between the page files');
  }
});

// ── one request, one tray card (several actions grouped, each still its own approval) ──────────────────────────
const NEXT = NL + 'function LaneNeedsYou(';
const nodes = (tree, type) => { const out = []; walk(tree, n => { if (n.type === type) out.push(n); }); return out; };
const five = [1, 2, 3, 4, 5].map(n => ({ approval_id: 'w' + n, title: 'Edit ' + n, payload: {} }));
const batchOf = ids => ({ batch_id: 'b-' + ids.join('+'), approval_ids: ids, count: ids.length, sentence: ids.length + ' wiki edits', items: [] });
for (const file of FILES) {
  check(file + ': a planned group is one tray card, the rest stay single, and no plan leaves every card single', () => {
    const w = pageWorld(), { fridayDecideApproval } = loadDecide(file, w);
    const render = plan => trayList(file, fridayDecideApproval, () => plan)({ items: five });
    const grouped = render([batchOf(['w1', 'w2', 'w3', 'w4', 'w5'])]);
    assert.equal(nodes(grouped, 'LaneBatch').length, 1, 'five edits must be one card');
    assert.equal(bodies(grouped).length, 0, 'a grouped edit also drew its own card');
    const mixed = render([batchOf(['w1', 'w2', 'w3'])]);
    assert.equal(nodes(mixed, 'LaneBatch').length, 1);
    assert.deepEqual(bodies(mixed).map(b => b.a.approval_id), ['w4', 'w5']);
    assert.equal(bodies(render([])).length, 5, 'with no plan every card shows on its own');
    assert.equal(nodes(render([batchOf(['w1', 'gone'])]), 'LaneBatch').length, 0, 'a group naming a card that is gone must not show');
    assert.equal(bodies(render([batchOf(['w1', 'gone'])])).length, 5);
  });
  check(file + ': a grouped card lists each action with its own Reject, marks what came from outside text, and shows no dead buttons', () => {
    const src = fn(file, 'LaneBatch', NEXT);
    assert.doesNotMatch(src, /onDecide: \(\) => \{\}/, 'the group lists cards whose buttons do nothing');
    const calls = [];
    const decide = (id, d) => { calls.push(id + ':' + d); return { then: f => f() }; };
    const item = { approval_id: 'w1', title: 'Fix a line', action_description: 'correct_wiki {"new_text":"mars is red"}',
      provenance: { flags: [{ arg: 'new_text', value: 'mars is red', source: 'a web page' }] } };
    const render = mount(fn(file, 'LaneBatchItem', NL + 'function LaneBatch('), 'LaneBatchItem', {
      React: expandingReact, useDeciding: () => false, fridayDecideApproval: decide, fridayMarkOutside: fridayMark(file) });
    let rejected = 0;
    const tree = render({ it: item, onRejected: () => { rejected++; } });
    const marked = nodes(tree, 'mark').map(m => m.children.join(''));
    assert.ok(marked.includes('mars is red'), 'the outside text is not marked: ' + JSON.stringify(marked));
    assert.match(words(tree), /From a web page: /);
    const parts = fridayMark(file)('say mars is red now', [{ value: 'mars is red' }, { value: 'mars is red' }, { value: 'x' }]);
    assert.deepEqual(parts.map(x => typeof x === 'string' ? x : '<' + x.children.join('') + '>'), ['say ', '<mars is red>', ' now'], 'outside text is not marked in place');
    const reject = nodes(tree, 'button').find(b => b.props['data-testid'] === 'lane-batch-reject-one');
    assert.ok(reject && !reject.props.disabled, 'no Reject this one button');
    reject.props.onClick();
    assert.deepEqual(calls, ['w1:deny']);
    assert.equal(rejected, 1, 'the group did not refresh after one was rejected');
    assert.ok(nodes(render({ it: item }), 'button').length >= 1);
  });
  check(file + ': deciding a group sends one request per group, other groups are free, and a refusal says why', async () => {
    const posts = [], toasts = [], pending = {};
    const { fridayDeciding } = loadDecide(file, pageWorld());
    const f = new Function('fetch', 'fridayToast', 'fridayName', 'fridayApprovalFeed', 'fridayDeciding',
      fn(file, 'fridayDecideBatch', NL + '// The text with every detail') + NL + 'return fridayDecideBatch;')(
      (url, o) => new Promise(res => { posts.push(decodeURIComponent(url.split('/')[4])); pending[posts.length - 1] = body => res({ json: () => Promise.resolve(body) }); }),
      t => toasts.push(t), () => 'Friday', { drop() {} }, fridayDeciding);
    f(batchOf(['w1', 'w2']), 'approve'); f(batchOf(['w1', 'w2']), 'approve'); f(batchOf(['w3', 'w4']), 'deny');
    assert.equal(posts.length, 2, 'one group sent twice, or another group was frozen');
    assert.deepEqual(toasts, ['Already working on that one.']);
    pending[0]({ ok: false, error: 'nope', message: 'That group is not what it was.' }); await tick(); await tick();
    assert.ok(toasts.includes('That group is not what it was.'), 'a refusal said nothing: ' + JSON.stringify(toasts));
    assert.equal(fridayDeciding.get(batchOf(['w1', 'w2']).batch_id), undefined, 'the group stays busy after it settled');
  });
}
check('index.html and ui_parts/app.html carry the same grouped-card code', () => {
  for (const [name, end] of [['fridayDecideBatch', NL + '// The text with every detail'], ['fridayMarkOutside', NL + 'function LaneBatchItem('],
    ['LaneBatchItem', NL + 'function LaneBatch('], ['LaneBatch', NL + '// How the tray\'s pending cards group'],
    ['useApprovalPlan', NL + '// The tray\'s list'], ['ApprovalTrayList', NEXT]]) {
    assert.equal(fn('index.html', name, end), fn('ui_parts/app.html', name, end), name + ' differs between the page files');
  }
});
function fridayMark(file) {
  return new Function('React', fn(file, 'fridayMarkOutside', NL + 'function LaneBatchItem(') + NL + 'return fridayMarkOutside;')(expandingReact);
}

(async () => {
  let failed = 0;
  for (const [name, body] of checks) {
    try { await body(); console.log('ok   ' + name); }
    catch (e) { failed++; console.error('FAIL ' + name + NL + '     ' + String(e && e.message || e).split(NL)[0]); }
  }
  if (failed) { console.error(failed + ' of ' + checks.length + ' checks failed'); process.exit(1); }
  console.log('all checks hold (' + checks.length + ')');
})();
