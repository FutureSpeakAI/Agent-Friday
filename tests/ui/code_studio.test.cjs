const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/friday_code_studio.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const response = body => ({ ok: true, json: async () => ({ status: 'ok', ...body }) });
const studies = [
  { id: 'alpha', title: 'Alpha', conversation_id: 'chat-alpha', source_snapshot: {} },
  { id: 'beta', title: 'Beta', conversation_id: 'chat-beta', source_snapshot: {} }
];
const originals = [{ name: 'Original One', path: '/projects/one', branch: 'main', dirty: 0 }, { name: 'Original Two', path: '/projects/two', branch: 'topic', dirty: 3 }];

function harness(customFetch) {
  const slots = [], effects = [], requests = [], opens = [], asks = [];
  let cursor = 0;
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState(initial) {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, next => { slots[i].value = typeof next === 'function' ? next(slots[i].value) : next; }];
    },
    useRef(initial) { const i = cursor++; if (!slots[i]) slots[i] = { current: initial }; return slots[i]; },
    useMemo(fn, deps) {
      const i = cursor++, old = slots[i];
      if (!old || deps.some((d, j) => !Object.is(d, old.deps[j]))) slots[i] = { deps, value: fn() };
      return slots[i].value;
    },
    useCallback(fn, deps) { return this.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const i = cursor++, old = slots[i];
      if (!old || deps.some((d, j) => !Object.is(d, old.deps[j]))) effects.push(() => { if (old && old.cleanup) old.cleanup(); slots[i] = { deps, cleanup: fn() }; });
    }
  };
  // Hooks are destructured by the component, as React's real hooks are.
  React.useCallback = (fn, deps) => React.useMemo(() => fn, deps);
  const fetcher = async (url, options) => {
    requests.push({ url, options });
    if (customFetch) { const value = customFetch(url, options); if (value !== undefined) return value; }
    if (url === '/api/codebases') return response({ codebases: studies });
    if (url === '/api/repos/scan') return response({ repos: originals });
    if (url.startsWith('/api/conversations/')) {
      const id = url.split('/').pop();
      return response({ conversation: { id, codebase: id.replace('chat-', '') } });
    }
    throw new Error('Unexpected test URL: ' + url);
  };
  const window = { FridayCodebasePanel: 'NativePanel', __FRIDAY_API_TOKEN: 'example-session', dispatchEvent() {}, addEventListener() {}, removeEventListener() {}, matchMedia: () => ({ matches: false }) };
  const context = vm.createContext({ React, window, fetch: fetcher, setTimeout, CustomEvent: function () {} });
  vm.runInContext(source, context);
  let props = { chat: { active: true, conversationId: 'chat-alpha', busy: false, surface: 'NATIVE CHAT', openConversation: async id => { opens.push(id); }, ask: async (message, id) => { asks.push({ message, id }); } }, tools: { Git: 'NativeGit' } };
  const api = {
    requests, opens, asks,
    render(next) { if (next) props = { ...props, ...next }; cursor = 0; return window.FridayCodeStudio(props); },
    effects() { while (effects.length) effects.shift()(); },
    async ready(next) { let tree = this.render(next); this.effects(); await settle(); tree = this.render(); this.effects(); await settle(); return this.render(); },
    unmount() { slots.forEach(slot => { if (slot && slot.cleanup) slot.cleanup(); }); }
  };
  return api;
}
function flat(tree) { return !tree || typeof tree !== 'object' ? [] : [tree, ...tree.children.flat(Infinity).flatMap(flat)]; }
function words(tree) { return !tree || typeof tree !== 'object' ? String(tree || '') : tree.children.flat(Infinity).map(words).join(' '); }
function button(tree, name) { const node = flat(tree).find(n => n.type === 'button' && words(n).trim() === name); assert.ok(node, 'Missing button: ' + name); return node; }
function project(tree, name) { const node = flat(tree).find(n => n.type === 'button' && n.props.className && n.props.className.includes('cs-project') && words(n).includes(name)); assert.ok(node, 'Missing project: ' + name); return node; }
const panel = tree => flat(tree).find(n => n.type === 'NativePanel');

test('selecting the second original keeps its Git target and never creates a codebase', async () => {
  const app = harness(); let tree = await app.ready();
  project(tree, 'Original Two').props.onClick(); tree = await app.ready();
  assert.equal(tree.props['data-selection'], 'repo:/projects/two');
  button(tree, 'Repository Git tools').props.onClick(); tree = await app.ready();
  const git = flat(tree).find(n => n.type === 'NativeGit');
  assert.equal(git.props.selectedRepo, 'Original Two');
  assert.equal(git.props.repos[0].path, '/projects/two');
  assert.equal(app.requests.filter(r => r.options && r.options.method === 'POST').length, 0);
  app.unmount();
});

test('study intake uses an explicit separate-copy request and retains the original path', async () => {
  const app = harness((url, options) => options && options.method === 'POST' ? response({ codebase: { id: 'study-two', title: 'Original Two', conversation_id: 'chat-study-two', source_snapshot: {} } }) : undefined);
  let tree = await app.ready(); project(tree, 'Original Two').props.onClick(); tree = await app.ready();
  button(tree, 'Study a copy').props.onClick(); tree = await app.ready();
  const form = flat(tree).find(n => n.type === 'form');
  await form.props.onSubmit({ preventDefault() {} }); tree = await app.ready();
  const post = app.requests.find(r => r.options && r.options.method === 'POST');
  assert.deepEqual(JSON.parse(post.options.body), { title: 'Original Two', study_path: '/projects/two' });
  assert.equal(post.options.headers['X-Friday-Token'], 'example-session');
  assert.equal(tree.props['data-selection'], 'codebase:study-two');
  app.unmount();
});

test('unsaved file edits prevent project replacement and creation forms', async () => {
  const app = harness(); let tree = await app.ready(); project(tree, 'Alpha').props.onClick(); tree = await app.ready();
  panel(tree).props.onDirtyChange(true); tree = app.render();
  project(tree, 'Beta').props.onClick(); tree = await app.ready();
  assert.equal(tree.props['data-selection'], 'codebase:alpha');
  button(tree, 'New codebase').props.onClick(); tree = await app.ready();
  assert.equal(flat(tree).some(n => n.type === 'form'), false);
  assert.match(words(tree), /Save or cancel/);
  panel(tree).props.onDirtyChange(false); tree = app.render();
  project(tree, 'Beta').props.onClick(); tree = await app.ready();
  assert.equal(tree.props['data-selection'], 'codebase:beta');
  app.unmount();
});

test('a conversation bound elsewhere cannot receive an atlas action', async () => {
  const app = harness(url => url.startsWith('/api/conversations/') ? response({ conversation: { id: 'chat-alpha', codebase: 'another' } }) : undefined);
  let tree = await app.ready(); project(tree, 'Alpha').props.onClick(); tree = await app.ready();
  assert.match(words(tree), /no longer connected/);
  assert.equal(panel(tree).props.chatBusy, true);
  await assert.rejects(panel(tree).props.onCodebaseAsk('Explain'), /Open this codebase/);
  assert.equal(app.asks.length, 0); app.unmount();
});

test('late selection checks cannot open the previous codebase conversation', async () => {
  const old = deferred();
  const app = harness(url => url === '/api/conversations/chat-alpha' ? old.promise : undefined);
  let tree = await app.ready(); project(tree, 'Alpha').props.onClick(); tree = await app.ready();
  project(tree, 'Beta').props.onClick(); tree = await app.ready();
  old.resolve(response({ conversation: { id: 'chat-alpha', codebase: 'alpha' } })); tree = await app.ready();
  assert.deepEqual(app.opens, ['chat-beta']); assert.equal(tree.props['data-selection'], 'codebase:beta'); app.unmount();
});

test('atlas actions reach the exact owning native sender and refuse after selection changes', async () => {
  const app = harness(); let tree = await app.ready(); project(tree, 'Alpha').props.onClick(); tree = await app.ready();
  const oldAsk = panel(tree).props.onCodebaseAsk;
  await oldAsk('Explain the selected node');
  assert.deepEqual(app.asks, [{ message: 'Explain the selected node', id: 'chat-alpha' }]);
  project(tree, 'Beta').props.onClick(); tree = await app.ready();
  await assert.rejects(oldAsk('Late request'), /Open this codebase/);
  assert.equal(app.asks.length, 1); app.unmount();
});

test('a background Code workspace does not retarget the active conversation', async () => {
  const app = harness(); let tree = await app.ready({ chat: { active: false, conversationId: 'elsewhere', openConversation: async () => assert.fail('Background selection changed the active chat') } });
  project(tree, 'Alpha').props.onClick(); tree = await app.ready();
  assert.equal(app.requests.some(r => r.url.startsWith('/api/conversations/')), false);
  assert.equal(panel(tree).props.chatBusy, true); app.unmount();
});

test('an unavailable deep-linked codebase never falls back to a different project', async () => {
  const app = harness(); const tree = await app.ready({ navTarget: { codebase_id: 'missing' } });
  assert.equal(tree.props['data-selection'], 'codebase:missing');
  assert.match(words(tree), /codebase is unavailable/); assert.equal(panel(tree), undefined); app.unmount();
});

test('refresh cannot replay a consumed navigation request over the selected project', async () => {
  const app = harness(); let tree = await app.ready({ navTarget: { codebase_id: 'alpha' } });
  project(tree, 'Beta').props.onClick(); tree = await app.ready();
  button(tree, 'Refresh').props.onClick(); tree = await app.ready();
  assert.equal(tree.props['data-selection'], 'codebase:beta'); app.unmount();
});

test('a catalog request begun before creation cannot erase the new codebase', async () => {
  const lateCatalog = deferred(); let codebaseReads = 0;
  const app = harness((url, options) => {
    if (url === '/api/codebases' && options && options.method === 'POST') return response({ codebase: { id: 'created', title: 'Created', conversation_id: 'chat-created' } });
    if (url === '/api/codebases' && ++codebaseReads === 2) return lateCatalog.promise;
  });
  let tree = await app.ready();
  button(tree, 'Refresh').props.onClick(); tree = await app.ready();
  button(tree, 'New codebase').props.onClick(); tree = await app.ready();
  const form = flat(tree).find(n => n.type === 'form');
  flat(form).find(n => n.type === 'input').props.onChange({ target: { value: 'Created' } }); tree = app.render();
  await flat(tree).find(n => n.type === 'form').props.onSubmit({ preventDefault() {} }); tree = await app.ready();
  lateCatalog.resolve(response({ codebases: studies })); tree = await app.ready();
  assert.equal(panel(tree).props.codebase.id, 'created'); app.unmount();
});

test('a changed catalog preserves the selected dirty draft owner while checking its binding again', async () => {
  let changed = false;
  const app = harness(url => {
    if (changed && url === '/api/codebases') return response({ codebases: [{ ...studies[0], conversation_id: 'rebound' }, studies[1]] });
    if (changed && url === '/api/conversations/chat-alpha') return response({ conversation: { id: 'chat-alpha', codebase: null } });
  });
  let tree = await app.ready(); project(tree, 'Alpha').props.onClick(); tree = await app.ready();
  panel(tree).props.onDirtyChange(true); tree = app.render(); changed = true;
  button(tree, 'Refresh').props.onClick(); tree = await app.ready();
  assert.equal(panel(tree).props.convId, 'chat-alpha');
  assert.equal(panel(tree).props.chatBusy, true);
  assert.match(words(tree), /draft is kept/);
  await assert.rejects(panel(tree).props.onCodebaseAsk('Wrong conversation'), /Open this codebase/);
  app.unmount();
});

test('a deep-link publishes its codebase after a delayed catalog resolves', async () => {
  const delayed = deferred(), states = [];
  const app = harness(url => url === '/api/codebases' ? delayed.promise : undefined);
  await app.ready({ navTarget: { codebase_id: 'alpha' }, onNavState: state => states.push(state) });
  delayed.resolve(response({ codebases: studies })); await app.ready();
  assert.equal(states.at(-1).codebase_id, 'alpha'); app.unmount();
});

test('choosing an existing project leaves an unsubmitted creation form', async () => {
  const app = harness(); let tree = await app.ready();
  button(tree, 'New codebase').props.onClick(); tree = await app.ready();
  assert.ok(flat(tree).find(n => n.type === 'form'));
  project(tree, 'Alpha').props.onClick(); tree = await app.ready();
  assert.equal(flat(tree).some(n => n.type === 'form'), false);
  assert.equal(panel(tree).props.codebase.id, 'alpha'); app.unmount();
});
