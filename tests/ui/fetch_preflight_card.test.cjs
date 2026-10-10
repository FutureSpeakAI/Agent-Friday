// Settings -> Models: the pre-fetch card must offer Fetch and Cancel, and
// must come into view when Get is pressed.
//
// Reported 2026-10-09: "It's not fetching from Friday's settings menu." The
// server log showed GET /api/models/fetch/preflight?model=ternary-bonsai:1.7b
// answered 200 twice and no POST /api/models/download ever followed. Two
// faults in the card: its `wrap` helper took ONE child, so every line after
// the title, including the Fetch and Cancel buttons, was dropped; and the
// card renders at the foot of the Local models list, ~1,950 px below the Get
// that opened it, so nothing visible happened next to the button.
//
// This runs the real FetchPreflightCard source from index.html under a tiny
// hook shim (no React, no DOM).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

function block(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'FetchPreflightCard exists in index.html');
  return source.slice(from, to);
}

// A minimal function-component host: state, effects after render, refs
// attached to the element that carries `ref`.
function host(Component, props, fetchImpl) {
  const states = [], refs = [], effects = [];
  let si = 0, ri = 0, ei = 0, tree = null;
  const scrolled = [];
  const E = (type, p, ...children) => ({ type, props: p || {}, children: children.flat(Infinity) });
  const ctx = vm.createContext({
    E, console, encodeURIComponent, String,
    apiFetch: fetchImpl,
    useState(init) {
      const i = si++;
      if (!(i in states)) states[i] = init;
      return [states[i], v => { states[i] = typeof v === 'function' ? v(states[i]) : v; render(); }];
    },
    useRef(init) {
      const i = ri++;
      if (!(i in refs)) refs[i] = { current: init === undefined ? null : init };
      return refs[i];
    },
    useEffect(fn, deps) {
      const i = ei++;
      const prev = effects[i];
      const changed = !prev || !deps || deps.some((d, k) => d !== prev.deps[k]);
      if (changed) effects[i] = { fn, deps, pending: true };
    },
  });
  vm.runInContext(block('function FetchPreflightCard(', '\n}\n') + '\n}\nthis.C = FetchPreflightCard;', ctx);
  function attach(node) {
    if (!node || typeof node !== 'object') return;
    if (node.props && node.props.ref) {
      node.props.ref.current = { scrollIntoView: (o) => scrolled.push(o || {}) };
    }
    (node.children || []).forEach(attach);
  }
  function render() {
    si = 0; ri = 0; ei = 0;
    tree = ctx.C(props);
    attach(tree);
    effects.forEach(e => { if (e && e.pending) { e.pending = false; e.fn(); } });
  }
  render();
  return { get tree() { return tree; }, scrolled };
}

const flush = () => new Promise(r => setImmediate(r));
const okCard = {
  status: 'ok', model_id: 'ternary-bonsai:1.7b', row: { fetch: 'download', modality: 'text' },
  card: { what: 'Fetch Ternary Bonsai 1.7B (text).', where: 'Ternary Bonsai 1.7B -- GPU',
          what_stands_down: 'Nothing changes until you choose to use it.',
          how_long: { note: 'download time depends on your network connection' },
          feel: '1152 MiB free' },
};

function find(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (pred(node)) out.push(node);
  (node.children || []).forEach(c => find(c, pred, out));
  return out;
}
const text = n => (n && typeof n === 'object') ? (n.children || []).map(text).join('') : (n == null || n === false ? '' : String(n));
const buttons = tree => find(tree, n => n.type === 'button');

test('the loaded card shows its five lines and a Fetch that confirms with the card', async () => {
  const confirmed = [], closed = [];
  const h = host(null, {
    modelId: 'ternary-bonsai:1.7b', confirming: false,
    onConfirm: c => confirmed.push(c), onClose: () => closed.push(1),
  }, () => Promise.resolve({ json: async () => okCard }));
  await flush(); await flush();
  const all = text(h.tree);
  for (const want of ['Fetch Ternary Bonsai 1.7B', 'Where', 'What stands down', 'How long', 'What it will feel like']) {
    assert.ok(all.includes(want), 'card shows ' + want + '; rendered: ' + all.slice(0, 200));
  }
  const labels = buttons(h.tree).map(text);
  assert.deepEqual(labels, ['Fetch', 'Cancel'], 'the card offers Fetch and Cancel');
  buttons(h.tree)[0].props.onClick();
  assert.equal(confirmed.length, 1, 'Fetch hands the card to onConfirm');
  assert.equal(confirmed[0].model_id, 'ternary-bonsai:1.7b');
  buttons(h.tree)[1].props.onClick();
  assert.equal(closed.length, 1, 'Cancel closes the card');
});

test('an error card keeps its Close button', async () => {
  const closed = [];
  const h = host(null, { modelId: 'x', onConfirm() {}, onClose: () => closed.push(1) },
    () => Promise.resolve({ json: async () => ({ status: 'error', message: 'nope' }) }));
  await flush(); await flush();
  assert.ok(text(h.tree).includes('nope'));
  const b = buttons(h.tree);
  assert.deepEqual(b.map(text), ['Close']);
  b[0].props.onClick();
  assert.equal(closed.length, 1);
});

test('the card brings itself into view when it opens', async () => {
  const h = host(null, { modelId: 'ternary-bonsai:1.7b', onConfirm() {}, onClose() {} },
    () => Promise.resolve({ json: async () => okCard }));
  await flush(); await flush();
  assert.ok(h.scrolled.length >= 1, 'the card asks to be scrolled into view (it renders far below the Get that opened it)');
});
