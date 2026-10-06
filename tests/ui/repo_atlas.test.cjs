const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'static/friday_repo_atlas.js'), 'utf8');
const graph = id => ({ project: { name: id, languages: ['Python'], description: 'Synthetic source graph' },
  nodes: [{ id: 'file:' + id, type: 'file', name: id + '.py', filePath: id + '.py', summary: 'PRIVATE SUMMARY', tags: [] }],
  edges: [], layers: [], tour: [], friday: { mode: 'local', coverage: { filesScanned: 1, filesParsed: 1 } } });
const response = body => ({ ok: true, json: async () => body });
const settle = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

// This harness drives the component's real effects and event handlers. Delayed
// requests deliberately ignore aborts, proving the ownership check itself.
function harness(fetcher, timers = { setTimeout, clearTimeout }) {
  const slots = [], effects = [];
  let cursor = 0;
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    Fragment: 'fragment',
    useState: initial => {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, next => { slots[i].value = typeof next === 'function' ? next(slots[i].value) : next; }];
    },
    useRef: initial => { const i = cursor++; if (!slots[i]) slots[i] = { current: initial }; return slots[i]; },
    useMemo: fn => { cursor++; return fn(); },
    useEffect: (fn, deps) => {
      const i = cursor++, old = slots[i];
      if (!old || deps.some((d, j) => !Object.is(d, old.deps[j]))) {
        effects.push(() => { if (old && old.cleanup) old.cleanup(); slots[i] = { deps, cleanup: fn() }; });
      }
    }
  };
  const context = vm.createContext({ React, window: { __FRIDAY_API_TOKEN: 'example-session' }, fetch: fetcher, AbortController, ...timers,
    document: { getElementById: () => true, createElement: () => ({}), head: { appendChild() {} } } });
  vm.runInContext(source, context);
  let props = { codebase: { id: 'alpha', title: 'Alpha' }, convId: 'conversation-alpha', onOpenFile() {}, onAsk: async () => {} };
  return {
    data: context.window.FridayRepoAtlasData,
    render(next) { if (next) props = Object.assign({}, props, next); cursor = 0; return context.window.FridayRepoAtlas(props); },
    effects() { while (effects.length) effects.shift()(); },
    unmount() { slots.forEach(slot => { if (slot && slot.cleanup) slot.cleanup(); }); }
  };
}
function flatten(tree) {
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...tree.children.flat(Infinity).flatMap(flatten)];
}
function button(tree, label) { return flatten(tree).find(n => n.type === 'button' && n.children.includes(label)); }
function text(tree) { return JSON.stringify(tree); }

test('the atlas is shipped before its host in both page sources', () => {
  for (const name of ['index.html', 'ui_parts/styles_and_scene.html']) {
    const page = fs.readFileSync(path.join(root, name), 'utf8');
    assert.ok(page.indexOf('/static/friday_repo_atlas.js') > 0);
    assert.ok(page.indexOf('/static/friday_repo_atlas.js') < page.indexOf('/static/friday_artifacts.js'));
  }
});

test('bounded neighborhoods exclude dangling and unrelated edges', () => {
  const api = harness(() => {}).data;
  const nodes = Array.from({ length: 10 }, (_, n) => ({ id: String(n) }));
  const edges = nodes.slice(1).map(n => ({ source: '0', target: n.id }));
  edges.push({ source: '0', target: 'missing' }, { source: '8', target: '9' });
  const result = api.neighborhood({ nodes, edges }, '0');
  assert.equal(result.total, 9);
  assert.equal(result.nodes.length, 7);
  assert.equal(result.edges.length, 6);
  assert.ok(result.edges.every(e => e.source === '0' && e.target !== 'missing'));
  assert.equal(api.neighborhood({ nodes, edges }, 'absent').nodes.length, 0);
});

test('relationship arrows honor imported forward, backward, and bidirectional links', () => {
  const { edgeArrow } = harness(() => {}).data;
  const edge = { source: 'alpha', target: 'beta', direction: 'forward' };
  assert.equal(edgeArrow(edge, 'alpha'), '→ ');
  assert.equal(edgeArrow(edge, 'beta'), '← ');
  edge.direction = 'backward';
  assert.equal(edgeArrow(edge, 'alpha'), '← ');
  assert.equal(edgeArrow(edge, 'beta'), '→ ');
  edge.direction = 'bidirectional';
  assert.equal(edgeArrow(edge, 'alpha'), '↔ ');
  assert.equal(edgeArrow(edge, 'beta'), '↔ ');
});

test('the galaxy neighborhood retains keyboard selection and isolates SVG paint references', async () => {
  const atlas = graph('alpha');
  atlas.nodes.push({ id: 'function:parse', name: 'parse', type: 'function' });
  atlas.edges.push({ source: 'file:alpha', target: 'function:parse', type: 'contains' });
  atlas.edges.push(...Array.from({ length: 6000 }, () => ({ source: 'function:parse', target: 'file:alpha', type: 'references', direction: 'backward' })));
  const app = harness(() => Promise.resolve(response({ atlas })));
  app.render(); app.effects(); await settle();
  const graphElement = flatten(app.render()).find(n => typeof n.type === 'function' && n.type.name === 'Graph');
  const rendered = graphElement.type(graphElement.props), parts = flatten(rendered);
  const ids = new Set(parts.filter(n => n.props.id).map(n => n.props.id));
  const paint = parts.flatMap(n => [n.props.fill, n.props.stroke]).filter(v => typeof v === 'string' && v.startsWith('url(#'));
  assert.ok(paint.length >= 5);
  assert.ok(paint.every(value => ids.has(value.slice(5, -1))));
  assert.equal(parts.filter(n => n.props.className === 'fra-orb').length, 2);
  assert.equal(parts.filter(n => n.props.className === 'fra-filament').length, 1);
  const selectable = parts.filter(n => n.props.role === 'button');
  assert.ok(selectable.every(n => /^translate\([\d.]+,[\d.]+\)$/.test(n.props.transform)));
  assert.ok(selectable.every(n => n.props.tabIndex === 0));
  const next = flatten(graphElement.type(graphElement.props));
  assert.ok(next.filter(n => n.props.id).every(n => !ids.has(n.props.id)), 'simultaneous chat graphs own different paint IDs');
  let prevented = false;
  selectable.find(n => n.props['aria-label'] === 'Select parse').props.onKeyDown({ key: ' ', preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.match(text(app.render()), /"selection"|parse/);
  assert.equal(flatten(app.render()).find(n => n.type === 'h3').children[0], 'parse');
  app.unmount();
});

test('chat actions carry references, await completion, and prevent duplicate sends', async () => {
  const pending = deferred(), calls = [], requests = [];
  const app = harness(url => { requests.push(url); return Promise.resolve(response({ status: 'ok', atlas: graph('alpha') })); });
  app.render({ onAsk: message => { calls.push(message); return pending.promise; } }); app.effects(); await settle();
  const learn = button(app.render(), 'Learn');
  const first = learn.props.onClick();
  learn.props.onClick();
  assert.equal(calls.length, 1);
  assert.match(calls[0], /"mode":"learn"/);
  assert.match(calls[0], /"codebase_id":"alpha"/);
  assert.match(calls[0], /"node_id":"file:alpha"/);
  assert.doesNotMatch(calls[0], /PRIVATE SUMMARY/);
  assert.ok(requests.every(url => url.includes('/atlas')), 'the owning chat is the only sender');
  assert.equal(button(app.render(), 'Learn').props.disabled, true);
  assert.doesNotMatch(text(app.render()), /Follow this request/);
  pending.resolve(); await first;
  assert.match(text(app.render()), /Follow this request/);
  assert.equal(button(app.render(), 'Learn').props.disabled, false);
});

test('callback failures are visible and an already busy chat cannot send', async () => {
  let calls = 0;
  const app = harness(() => Promise.resolve(response({ status: 'ok', atlas: graph('alpha') })));
  app.render({ chatBusy: true, onAsk: async () => { calls++; throw new Error('The selected local model is unavailable.'); } }); app.effects(); await settle();
  assert.equal(button(app.render(), 'Explore').props.disabled, true);
  await button(app.render(), 'Explore').props.onClick();
  assert.equal(calls, 0);
  app.render({ chatBusy: false });
  await button(app.render(), 'Explore').props.onClick();
  const alert = flatten(app.render()).find(n => n.props.role === 'alert');
  assert.ok(alert);
  assert.match(text(alert), /local model is unavailable/);
  assert.doesNotMatch(text(app.render()), /Sent to this chat/);
});

test('a delayed previous repository response cannot replace the active map', async () => {
  const slow = deferred();
  const app = harness(url => url.includes('/alpha/') ? slow.promise : Promise.resolve(response({ atlas: graph('beta') })));
  app.render(); app.effects();
  app.render({ codebase: { id: 'beta' }, convId: 'conversation-beta' }); app.effects(); await settle();
  assert.match(text(app.render()), /beta.py/);
  slow.resolve(response({ atlas: graph('alpha') })); await settle();
  assert.match(text(app.render()), /beta.py/);
  assert.doesNotMatch(text(app.render()), /alpha.py/);
});

test('a completed previous chat action does not announce success in the next chat', async () => {
  const slow = deferred();
  const app = harness(url => Promise.resolve(response({ atlas: graph(url.includes('/beta/') ? 'beta' : 'alpha') })));
  app.render({ onAsk: () => slow.promise }); app.effects(); await settle();
  const pending = button(app.render(), 'Adapt').props.onClick();
  app.render({ codebase: { id: 'beta' }, convId: 'conversation-beta' }); app.effects(); await settle();
  slow.resolve(); await pending;
  assert.doesNotMatch(text(app.render()), /Follow this request/);
  assert.match(text(app.render()), /beta.py/);
  app.unmount();
});

test('the source action preserves the verified relative file reference', async () => {
  const opened = [];
  const app = harness(() => Promise.resolve(response({ atlas: graph('alpha') })));
  app.render({ onOpenFile: file => opened.push(file) }); app.effects(); await settle();
  button(app.render(), 'Open source').props.onClick();
  assert.equal(opened[0].path, 'alpha.py');
  assert.throws(() => app.data.atlasMessage('unknown', 'alpha', ''), /Unknown atlas action/);
});

test('a stalled map request ends with a visible retry instead of endless loading', async () => {
  let expire;
  const app = harness((url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
  }), { setTimeout: callback => { expire = callback; return 1; }, clearTimeout() {} });
  app.render(); app.effects(); expire(); await settle();
  assert.match(text(app.render()), /Reading the map took too long/);
  assert.equal(button(app.render(), 'Refresh').props.disabled, false);
});

// Exercise the actual window sender used by ChatSurface, including the epoch
// guard. Network/model responses are synthetic; message handling is production.
function chatSender(turn) {
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const start = page.indexOf('  const send = async (overrideMsg, opts) => {', page.indexOf('function ConversationWindow('));
  const end = page.indexOf('\n  const uploadFile =', start);
  assert.ok(start > 0 && end > start);
  const state = { messages: [], draft: 'Unsent question', loading: false, epoch: { current: 1 } };
  const context = vm.createContext({ input: state.draft, loading: false, cid: 'conversation-alpha', SH: { agentSettings: {} }, epoch: state.epoch,
    setInput: v => { state.draft = v; }, setMsgs: f => { state.messages = f(state.messages); },
    setLoading: v => { state.loading = v; }, setPausePending() {}, fridayChatTurn: turn, fridayRunActions() {}, window: {} });
  vm.runInContext(page.slice(start, end) + '\nthis.send = send;', context);
  return { state, send: context.send };
}

test('the owning sender renders refusal and preserves an unfinished draft for atlas actions', async () => {
  const calls = [];
  const app = chatSender(async body => { calls.push(body); return { local_only_refused: true, response: 'The local model is unavailable.' }; });
  await app.send('Learn from this repository', { preserveDraft: true });
  assert.equal(app.state.draft, 'Unsent question');
  assert.equal(calls[0].conversation_id, 'conversation-alpha');
  assert.equal(app.state.messages[0].text, 'Learn from this repository');
  assert.equal(app.state.messages[1].text, 'The local model is unavailable.');
  assert.equal(app.state.loading, false);
  await app.send('A typed follow-up');
  assert.equal(app.state.draft, '');
});

test('the owning sender cannot put a delayed atlas response in the next conversation', async () => {
  const turn = deferred(), app = chatSender(() => turn.promise);
  const sending = app.send('Learn from this repository', { preserveDraft: true });
  app.state.epoch.current++;
  app.state.messages = [{ role: 'user', text: 'Next conversation' }];
  turn.resolve({ response: 'Old conversation reply' });
  await sending;
  assert.deepEqual(app.state.messages, [{ role: 'user', text: 'Next conversation' }]);
});
