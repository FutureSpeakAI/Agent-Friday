// Messages > Clean up (static/friday_inbox_cleanup.js), rendered with a minimal
// React stand-in: nothing is ticked until the person ticks it, "Tick all" is a
// press, the unsubscribe button does nothing with nothing ticked, the ticks it
// sends are exactly the ticked ids with the owner's own requested_by, senders
// that need the person are never offered a tick, and the found ledger never
// adds its two numbers together.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(root, 'static/friday_inbox_cleanup.js'), 'utf8');

const BOARD = {
  job: { state: 'ready', source: { kind: 'gmail', label: 'your Gmail' }, scanned: 120 },
  rows: {
    one_click: [
      { id: 'shop.example', name: 'Weekly Deals', domain: 'shop.example', count: 9, per_month: 4.5, streak: 9,
        streak_words: "You haven't opened any of the last 9.", route: 'one_click', why: '', url: '', host: 'unsub.shop.example', mailto: '', ticked: false },
      { id: 'deals.example', name: 'Flash Sale', domain: 'deals.example', count: 6, per_month: 3, streak: 6,
        streak_words: "You haven't opened any of the last 6.", route: 'one_click', why: '', url: '', host: 'unsub.deals.example', mailto: '', ticked: false },
    ],
    needs_you: [
      { id: 'other.example', name: 'Other', domain: 'other.example', count: 3, per_month: 1, streak: 3,
        streak_words: "You haven't opened any of the last 3.", route: 'site', why: 'It asks you to unsubscribe on its own page.',
        url: 'https://other.example/leave', host: 'other.example', mailto: '', ticked: false },
    ],
    spam: [],
  },
  found: { confirmed: [{ id: 'a', name: 'A', per_month: 2 }], possible: [{ id: 'b', name: 'B', per_month: 3 }],
    still_sending: [], confirmed_per_month: 2, possible_per_month: 3 },
  accounts: [{ id: 'acct', email: 'me@example.com' }],
  archive_hint: 'hint',
};

function mount(board) {
  // A tiny hook runtime: state lives in slots and survives re-renders.
  const slots = []; let slot = 0; const posts = [];
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) });
  const React = {
    createElement, Fragment: 'Fragment',
    useState: init => { const i = slot++; if (!(i in slots)) slots[i] = typeof init === 'function' ? init() : init; return [slots[i], v => { slots[i] = typeof v === 'function' ? v(slots[i]) : v; }]; },
    useEffect: () => {},
  };
  const win = {};
  const ctx = vm.createContext({ React, window: win, fetch: (url, opts) => { posts.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null }); return Promise.resolve({ ok: true, json: async () => ({}) }); }, console });
  vm.runInContext(SRC, ctx);
  slots[0] = board;          // the board as loaded
  const render = () => { slot = 0; return expand(createElement(win.FridayInboxCleanup, null)); };
  function expand(n) {
    if (!n || typeof n !== 'object') return n;
    if (typeof n.type === 'function') return expand(n.type({ ...n.props, children: n.children }));
    return { ...n, children: (n.children || []).map(expand) };
  }
  return { render, posts };
}

function all(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (pred(node)) out.push(node);
  for (const c of node.children || []) all(c, pred, out);
  return out;
}
const text = n => all(n, () => true).flatMap(x => (x.children || []).filter(c => typeof c === 'string' || typeof c === 'number')).join('');
const buttons = tree => all(tree, n => n.type === 'button');
const byText = (tree, t) => buttons(tree).find(b => text(b).startsWith(t));

test('nothing is ticked until the person ticks it, and only one-click senders have a tick', () => {
  const m = mount(BOARD);
  const tree = m.render();
  const boxes = all(tree, n => n.type === 'input' && n.props.type === 'checkbox');
  assert.equal(boxes.length, 2, 'one tick per one-click sender; none for senders that need the person');
  assert.ok(boxes.every(b => b.props.checked === false));
  const go = byText(tree, 'Tick the ones to leave');
  assert.ok(go && go.props.disabled === true);
});

test('Tick all is a press, and the request carries exactly the ticked ids as the owner', () => {
  const m = mount(BOARD);
  byText(m.render(), 'Tick all').props.onClick();
  let tree = m.render();
  assert.ok(all(tree, n => n.type === 'input' && n.props.type === 'checkbox').every(b => b.props.checked === true));
  // untick one
  all(tree, n => n.type === 'input' && n.props.id === 'ic-deals.example')[0].props.onChange({ target: { checked: false } });
  tree = m.render();
  const go = byText(tree, 'Unsubscribe from 1 ticked');
  assert.ok(go && !go.props.disabled);
  go.props.onClick();
  const sent = m.posts.find(p => p.url === '/api/inbox-cleanup/unsubscribe');
  assert.deepEqual(sent.body, { senders: ['shop.example'], requested_by: 'ui:inbox-cleanup' });
});

test('a sender that needs the person shows its page, and the ledger never adds its two numbers', () => {
  const tree = mount(BOARD).render();
  const t = text(tree);
  assert.ok(t.includes("I won't contact these for you"));
  assert.ok(all(tree, n => n.type === 'a' && n.props.href === 'https://other.example/leave' && n.props.rel === 'noopener noreferrer').length === 1);
  assert.ok(t.includes('Confirmed: stopped sending') && t.includes('Asked, not checked yet'));
  const nums = all(tree, n => n.props && n.props.className === 'ic-num').map(text);
  assert.deepEqual(nums, ['1', '1'], 'two separate counts');
  assert.ok(t.includes('About 2 fewer') && t.includes('about 3 a month'));
  assert.ok(!/about 5\b/.test(t), 'confirmed (2) and possible (3) are never summed');
});
