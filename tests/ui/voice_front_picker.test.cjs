// Settings -> Voice: "Local voice model" chooses the model that answers a
// local voice call (voice_front_model). Ternary Bonsai 1.7B is the default;
// the Qwen3 builds stay choosable once downloaded; a model that is not on
// this computer cannot be chosen, and a saved choice that is not downloaded
// says so instead of pretending.
//
// Runs the real VoiceFrontPicker source from index.html (and checks the
// app.html mirror carries the same choices) under a tiny hook shim.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const mirror = fs.readFileSync(path.join(root, 'ui_parts/app.html'), 'utf8');

function block(src) {
  const from = src.indexOf('const VOICE_FRONT_CHOICES');
  const to = src.indexOf('\n}\n', src.indexOf('function VoiceFrontPicker(', from));
  assert.ok(from >= 0 && to > from, 'VoiceFrontPicker exists');
  return src.slice(from, to) + '\n}\n';
}

function host(props, artifacts) {
  const states = [], effects = [];
  let si = 0, ei = 0, tree = null;
  const E = (type, p, ...children) => ({ type, props: p || {}, children: children.flat(Infinity) });
  const ctx = vm.createContext({
    React: { createElement: E }, StRow: 'StRow', console,
    apiFetch: () => Promise.resolve({ json: async () => ({ artifacts }) }),
    useState(init) {
      const i = si++;
      if (!(i in states)) states[i] = init;
      return [states[i], v => { states[i] = typeof v === 'function' ? v(states[i]) : v; render(); }];
    },
    useEffect(fn, deps) {
      const i = ei++;
      if (!effects[i]) effects[i] = { fn, pending: true };
    },
  });
  vm.runInContext(block(index) + 'this.C = VoiceFrontPicker; this.CH = VOICE_FRONT_CHOICES;', ctx);
  function render() {
    si = 0; ei = 0;
    tree = ctx.C(props);
    effects.forEach(e => { if (e && e.pending) { e.pending = false; e.fn(); } });
  }
  render();
  return { get tree() { return tree; }, choices: ctx.CH };
}

const flush = () => new Promise(r => setImmediate(r));
function find(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (pred(node)) out.push(node);
  (node.children || []).forEach(c => find(c, pred, out));
  return out;
}
const text = n => (n && typeof n === 'object') ? (n.children || []).map(text).join('') : (n == null || n === false ? '' : String(n));
const radios = t => find(t, n => n.props && n.props.role === 'radio');

test('Bonsai is chosen by default and the Qwen3 builds are offered', async () => {
  const saved = [];
  const h = host({ s: {}, save: d => saved.push(d) },
    [{ id: 'voice-front-bonsai-1.7b', installed: true }, { id: 'voice-front-4b', installed: true },
     { id: 'voice-front-1.7b', installed: false }]);
  await flush(); await flush();
  const r = radios(h.tree);
  assert.deepEqual(r.map(text), ['Ternary Bonsai 1.7B', 'Qwen3 4B', 'Qwen3 1.7B']);
  assert.equal(r[0].props['aria-checked'], true, 'Bonsai is the default');
  r[1].props.onClick();
  assert.equal(JSON.stringify(saved), JSON.stringify([{ voice_front_model: 'qwen3-4b-instruct-2507' }]), 'the 4B can be chosen');
  assert.equal(r[2].props.onClick, undefined, 'a model that is not downloaded cannot be chosen');
  assert.equal(r[2].props['aria-disabled'], true);
});

test('a saved choice that is not downloaded says the main model answers until it is', async () => {
  const h = host({ s: { voice_front_model: 'ternary-bonsai:1.7b' }, save() {} },
    [{ id: 'voice-front-bonsai-1.7b', installed: false }]);
  await flush(); await flush();
  const status = find(h.tree, n => n.props && n.props.role === 'status');
  assert.equal(status.length, 1);
  assert.match(text(status[0]), /not downloaded yet/);
});

test('the choices are the server enum, and app.html mirrors them', () => {
  const ids = [...block(index).matchAll(/id: '([^']+)', artifact/g)].map(m => m[1]);
  const routes = fs.readFileSync(path.join(root, 'src/agent_friday/routes/core_routes.py'), 'utf8');
  const enumLine = routes.split('\n').find(l => l.includes('"voice_front_model": ('));
  assert.deepEqual(ids.slice().sort(), [...enumLine.matchAll(/"([^"]+)"/g)].map(m => m[1]).slice(1).sort());
  assert.equal(block(mirror), block(index), 'the mirror carries the same component');
  assert.ok(mirror.includes('<VoiceFrontPicker s={s} save={save}/>'), 'the mirror renders it in Voice');
  assert.ok(index.includes('React.createElement(VoiceFrontPicker, { s: s, save: save })'), 'index renders it in Voice');
});
