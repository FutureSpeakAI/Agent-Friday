const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/friday_artifacts.js'), 'utf8');
const response = (body, ok = true) => ({ ok, status: ok ? 200 : 409, json: async () => body });
const settle = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function nodes(tree) {
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...tree.children.flat(Infinity).flatMap(nodes)];
}
const button = (tree, label) => nodes(tree).find(node => node.type === 'button' && node.children.includes(label));
const area = tree => nodes(tree).find(node => node.type === 'textarea');
const sourceText = tree => nodes(tree).filter(node => node.type === 'pre').flatMap(node => node.children).join('');
const runButton = tree => nodes(tree).find(node => node.props['data-ask-run']);
const input = tree => nodes(tree).find(node => node.props['aria-label'] === 'Command to run');

// Drive the shipped component with real effects and delayed network results.
// No timer, server, model, or browser process is needed for these race checks.
function harness(fetcher = () => undefined, initial = {}) {
  const slots = [], pending = [], requests = [], styles = [], listeners = new Map();
  let cursor = 0, changed = false, mounted = true, lateWrites = 0, tree;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const React = {
    Fragment: 'fragment',
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState(initialValue) {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: typeof initialValue === 'function' ? initialValue() : initialValue };
      return [slots[i].value, next => {
        if (!mounted) lateWrites++;
        const value = typeof next === 'function' ? next(slots[i].value) : next;
        if (!Object.is(slots[i].value, value)) { slots[i].value = value; changed = true; }
      }];
    },
    useRef(value) { const i = cursor++; if (!slots[i]) slots[i] = { current: value }; return slots[i]; },
    useMemo(fn, deps) {
      const i = cursor++;
      if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { deps, value: fn() };
      return slots[i].value;
    },
    useCallback(fn, deps) { return React.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const i = cursor++, previous = slots[i];
      if (!previous || !same(previous.deps, deps)) {
        pending.push(() => { if (previous && previous.cleanup) previous.cleanup(); slots[i] = { deps, cleanup: fn() }; });
      }
    }
  };
  const defaults = url => {
    if (url.endsWith('/header')) return { text: 'Codebase · ready' };
    if (url.endsWith('/preview')) return { html: '<p>Preview</p>' };
    if (url.endsWith('/files')) return { files: [{ path: 'a.py', bytes: 4 }, { path: 'b.py', bytes: 4 }] };
    if (url.includes('/file?')) return { content: url.includes('a.py') ? 'source A' : 'source B' };
    return { status: 'ok' };
  };
  const context = vm.createContext({ React, console, URL, setTimeout, clearTimeout,
    EventSource: class EventSource {},
    document: { getElementById: id => styles.find(style => style.id === id), createElement: () => ({}), head: { appendChild: style => styles.push(style) } },
    window: { __FRIDAY_API_TOKEN: 'test-session', FridayRepoAtlas: function Atlas() {}, // pragma: allowlist secret — synthetic transport fixture
      addEventListener: (name, fn) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
      removeEventListener: (name, fn) => listeners.get(name)?.delete(fn) },
    fetch: (url, opts = {}) => { requests.push({ url, opts }); return fetcher(url, opts) || Promise.resolve(response(defaults(url))); }
  });
  vm.runInContext(source, context);
  let props = { codebase: { id: 'alpha', title: 'Alpha', source_snapshot: true }, convId: 'chat-alpha', tab: true, workspace: true, ...initial };
  return {
    requests, styles,
    get lateWrites() { return lateWrites; },
    render(next) {
      if (next) props = { ...props, ...next };
      let passes = 0;
      do {
        if (++passes > 20) throw new Error('Component effects did not settle');
        changed = false; cursor = 0;
        tree = context.window.FridayCodebasePanel(props);
        while (pending.length) pending.shift()();
      } while (changed);
      return tree;
    },
    dispatch(name, event) { listeners.get(name)?.forEach(fn => fn(event)); },
    unmount() { slots.forEach(slot => { if (slot && slot.cleanup) slot.cleanup(); }); mounted = false; }
  };
}
async function ready(fetcher, props) {
  const app = harness(fetcher, props); app.render(); await settle(); app.render(); return app;
}
async function editFile(app) {
  button(app.render(), 'Files').props.onClick();
  button(app.render(), 'a.py').props.onClick(); await settle();
  button(app.render(), 'Edit').props.onClick();
  area(app.render()).props.onChange({ target: { value: 'my unsaved draft' } });
  app.render();
}

test('the workspace panel initializes its own CSS and keeps its native tools', async () => {
  const views = [];
  const app = await ready(undefined, { onViewChange: view => views.push(view) });
  const tree = app.render();
  assert.match(tree.props.className, /fa-codebase-workspace/);
  assert.equal(app.styles.length, 1);
  assert.deepEqual(nodes(tree).filter(node => node.props.role === 'tab').flatMap(node => node.children), ['Understand', 'Files', 'Changes', 'Preview', 'Run']);
  assert.equal(nodes(tree).some(node => node.props['aria-label'] === 'Collapse'), false);
  assert.equal(nodes(tree).some(node => node.props.className === 'fa-title-row'), false);
  button(tree, 'Files').props.onClick(); app.render();
  assert.equal(views.at(-1), 'files');
  assert.match(app.styles[0].textContent, /@container \(max-width:460px\)/);
  assert.ok(nodes(app.render()).some(node => node.type === 'button' && node.props.className === 'fa-codebase-file-button'));
});

test('a slow file response cannot replace the most recently selected source', async () => {
  const slow = deferred();
  const app = await ready(url => url.includes('/file?path=a.py') ? slow.promise : undefined);
  button(app.render(), 'Files').props.onClick();
  button(app.render(), 'a.py').props.onClick();
  button(app.render(), 'b.py').props.onClick(); await settle();
  assert.equal(sourceText(app.render()), 'source B');
  slow.resolve(response({ content: 'obsolete A' })); await settle();
  assert.equal(sourceText(app.render()), 'source B');
});

test('a requested initial view does not report the legacy default back to its owner', async () => {
  const views = [];
  const app = await ready(undefined, { codebase: { id: 'alpha', title: 'Alpha' }, wantView: { view: 'understand' }, onViewChange: view => views.push(view) });
  assert.equal(app.render().props['data-codebase-view'], 'understand');
  assert.deepEqual(views, ['understand']);
  app.render({ wantView: { view: 'files' } });
  assert.equal(app.render().props['data-codebase-view'], 'files');
  assert.deepEqual(views, ['understand', 'files']);
});

test('a departed repository cannot commit late reads into its replacement', async () => {
  const slow = deferred();
  const app = harness(url => url.includes('/alpha/') ? slow.promise : undefined);
  app.render();
  app.render({ codebase: { id: 'beta', title: 'Beta', source_snapshot: true }, convId: 'chat-beta' }); await settle();
  button(app.render(), 'Files').props.onClick();
  slow.resolve(response({ files: [{ path: 'wrong-owner.py' }] })); await settle();
  assert.equal(button(app.render(), 'wrong-owner.py'), undefined);
  assert.ok(button(app.render(), 'b.py'));
});

test('unsaved edits guard file changes, tab requests, and page departure until canceled', async () => {
  const dirty = [], views = [];
  const app = await ready(undefined, { onDirtyChange: value => dirty.push(value), onViewChange: view => views.push(view) });
  await editFile(app);
  const count = app.requests.filter(request => request.url.includes('/file?')).length;
  button(app.render(), 'b.py').props.onClick();
  button(app.render(), 'Preview').props.onClick();
  const tree = app.render({ wantView: { view: 'changes' } });
  assert.equal(tree.props['data-codebase-view'], 'files');
  assert.equal(area(tree).props.value, 'my unsaved draft');
  assert.equal(app.requests.filter(request => request.url.includes('/file?')).length, count);
  assert.equal(dirty.at(-1), true);
  assert.equal(views.at(-1), 'files');
  const event = { preventDefault() { this.prevented = true; } };
  app.dispatch('beforeunload', event); assert.equal(event.prevented, true);
  button(tree, 'Cancel').props.onClick(); app.render();
  assert.equal(dirty.at(-1), false);
  button(app.render(), 'b.py').props.onClick(); await settle();
  assert.equal(sourceText(app.render()), 'source B');
  app.unmount(); assert.equal(dirty.at(-1), false);
});

test('an in-flight refresh cannot overwrite a newly opened file draft', async () => {
  const slow = deferred(); let reads = 0;
  const app = await ready(url => url.includes('/file?path=a.py') && ++reads === 2 ? slow.promise : undefined);
  button(app.render(), 'Files').props.onClick(); button(app.render(), 'a.py').props.onClick(); await settle();
  app.render({ refreshKey: 1 });
  button(app.render(), 'Edit').props.onClick();
  area(app.render()).props.onChange({ target: { value: 'draft stays' } });
  slow.resolve(response({ content: 'refreshed source' })); await settle();
  assert.equal(area(app.render()).props.value, 'draft stays');
  button(app.render(), 'Cancel').props.onClick();
  assert.equal(sourceText(app.render()), 'source A');
});

test('failed saves preserve the exact draft and a later save commits the captured path', async () => {
  const posted = [], dirty = []; let accept = false;
  const app = await ready((url, opts) => {
    if (url.endsWith('/file') && opts.method === 'POST') {
      posted.push(JSON.parse(opts.body));
      return Promise.resolve(response(accept ? { step: { summary: 'Saved' } } : { error: 'File changed elsewhere' }, accept));
    }
  }, { onDirtyChange: value => dirty.push(value) });
  await editFile(app);
  button(app.render(), 'Save as a step').props.onClick(); await settle();
  assert.equal(area(app.render()).props.value, 'my unsaved draft');
  assert.equal(dirty.at(-1), true);
  assert.match(JSON.stringify(app.render()), /File changed elsewhere/);
  accept = true;
  button(app.render(), 'Save as a step').props.onClick(); await settle();
  assert.equal(area(app.render()), undefined);
  assert.equal(sourceText(app.render()), 'my unsaved draft');
  assert.equal(dirty.at(-1), false);
  assert.deepEqual(posted, [{ path: 'a.py', content: 'my unsaved draft' }, { path: 'a.py', content: 'my unsaved draft' }]);
});

test('unmounted panels ignore delayed save completion', async () => {
  const slow = deferred();
  const app = await ready((url, opts) => opts.method === 'POST' ? slow.promise : undefined);
  await editFile(app);
  button(app.render(), 'Save as a step').props.onClick(); app.unmount();
  slow.resolve(response({ step: { summary: 'Saved' } })); await settle();
  assert.equal(app.lateWrites, 0);
});

for (const workspace of [true, false]) {
  test(`${workspace ? 'workspace' : 'chat'} Run uses its owning sender and prevents duplicate submission`, async () => {
    const slow = deferred(), sent = [];
    const owner = 'chat-alpha';
    const app = await ready(undefined, { workspace, convId: owner,
      onCodebaseAsk: message => { sent.push({ conversation_id: owner, message }); return slow.promise; } });
    button(app.render(), workspace ? 'Run' : 'Terminal').props.onClick();
    input(app.render()).props.onChange({ target: { value: 'npm test' } });
    const run = runButton(app.render());
    const done = run.props.onClick(); run.props.onClick(); await settle();
    assert.deepEqual(sent, [{ conversation_id: owner, message: 'Run this in the codebase and tell me the result: `npm test`' }]);
    assert.equal(runButton(app.render()).props.disabled, true);
    assert.equal(app.requests.some(request => request.url === '/api/chat/send'), false);
    slow.resolve(); await done;
    assert.equal(input(app.render()).props.value, '');
    assert.match(JSON.stringify(app.render()), /Follow this request in the chat/);
  });
}

test('Run sender rejection keeps the command visible and permits retry', async () => {
  let calls = 0;
  const app = await ready(undefined, { onCodebaseAsk: async () => { calls++; throw new Error('Chat is no longer bound to this repository'); } });
  button(app.render(), 'Run').props.onClick(); input(app.render()).props.onChange({ target: { value: 'npm test' } });
  await runButton(app.render()).props.onClick();
  assert.equal(input(app.render()).props.value, 'npm test');
  assert.match(JSON.stringify(app.render()), /no longer bound/);
  assert.equal(Boolean(runButton(app.render()).props.disabled), false);
  await runButton(app.render()).props.onClick(); assert.equal(calls, 2);
});

test('workspace Run refuses a missing sender and a busy owning chat', async () => {
  let calls = 0;
  const app = await ready();
  button(app.render(), 'Run').props.onClick(); input(app.render()).props.onChange({ target: { value: 'npm test' } });
  await runButton(app.render()).props.onClick();
  assert.match(JSON.stringify(app.render()), /Open this codebase in its chat/);
  app.render({ chatBusy: true, onCodebaseAsk: async () => { calls++; } });
  assert.equal(runButton(app.render()).props.disabled, true);
  await runButton(app.render()).props.onClick(); assert.equal(calls, 0);
  assert.equal(app.requests.some(request => request.url === '/api/chat/send'), false);
});
