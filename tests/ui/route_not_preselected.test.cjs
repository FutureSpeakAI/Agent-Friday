// North-star A16: Settings > Models presses no routing mode until the owner
// picks one. ModelSoupCard is rendered from the served UI with a minimal React
// stand-in: with routing_mode_picked false no mode button is pressed and the
// card says nothing is picked and what runs meanwhile; once picked, exactly
// the picked mode is pressed. The page must not fall back to a mode of its own.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

function slice(name) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name + ' exists');
  const end = source.indexOf('\n}\n', start) + 3;
  return source.slice(start, end);
}

function harness() {
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) });
  const StSection = (props) => createElement('section', { title: props.title }, ...(props.children || []));
  const ctx = vm.createContext({ React: { createElement }, E: createElement, StSection,
    fridayName: () => 'Friday', FRIDAY_BRAND: { name: 'Agent Friday' }, console });
  vm.runInContext(slice('ModelSoupCard'), ctx);
  return ctx;
}

function walk(node, fn) {
  if (!node || typeof node !== 'object') return;
  fn(node);
  for (const c of node.children || []) walk(c, fn);
}

function render(node) {
  if (!node || typeof node !== 'object') return node;
  if (typeof node.type === 'function') return render(node.type({ ...node.props, children: node.children }));
  return { ...node, children: (node.children || []).map(render) };
}

function text(node) {
  let out = '';
  walk(node, n => { for (const c of n.children || []) if (typeof c === 'string' || typeof c === 'number') out += c; });
  return out;
}

const MODES = [
  { id: 'local_only', label: 'Local only', help: 'h' },
  { id: 'local_preferred', label: 'Local preferred', help: 'h' },
  { id: 'cloud_only', label: 'Cloud only', help: 'h' },
];

function card(d, mode) {
  const ctx = harness();
  return render(ctx.ModelSoupCard({ soup: { error: 'not built in this test' }, d, mode,
    setMode: () => {}, savingMode: null, reload: () => {} }));
}

function pressed(tree) {
  const out = [];
  walk(tree, n => { if (n.type === 'button' && n.props['aria-pressed'] === true) out.push(n); });
  return out;
}

test('the call site passes no mode while the route is not picked', () => {
  assert.ok(!source.includes("d.routing_mode || 'local_preferred'"),
    'the page must not pick local_preferred for a server that sent none');
  assert.ok(source.includes("mode: d.routing_mode_picked === false ? '' : (d.routing_mode || '')"));
});

test('with no route picked, no mode is pressed and the card says so', () => {
  const tree = card({ routing_modes: MODES, routing_mode: 'cloud_only', routing_mode_picked: false }, '');
  assert.equal(pressed(tree).length, 0, 'nothing is pre-selected');
  const t = text(tree);
  assert.ok(t.includes('Not chosen yet: nothing is picked for you.'), t);
  assert.ok(t.includes('uses its starting setting, Cloud only.'), t);
});

test('a picked route is pressed, and only that one', () => {
  const tree = card({ routing_modes: MODES, routing_mode: 'local_only', routing_mode_picked: true }, 'local_only');
  const on = pressed(tree);
  assert.equal(on.length, 1);
  assert.ok(text(on[0]).includes('Local only'));
  assert.ok(!text(tree).includes('Not chosen yet'));
});
