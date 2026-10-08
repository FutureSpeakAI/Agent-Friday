const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.env.CODE_STUDIO_SOURCE_ROOT || path.resolve(__dirname, '../..');
const pages = ['index.html', 'ui_parts/app.html'];
const read = name => fs.readFileSync(path.join(root, name), 'utf8').replace(/\r\n/g, '\n');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = body => ({ ok: true, json: async () => body });

// Run the actual App sender, rather than an alternate chat window's sender.
// Every request and response is synthetic; no server or model is contacted.
function sender(name, options = {}) {
  const page = read(name), app = page.indexOf('function App(');
  const start = page.indexOf('  const sendChat', app), end = page.indexOf('  const sendPrompt', start);
  assert.ok(start > app && end > start);
  const state = { id: 'chat-a', draft: 'Unfinished thought', messages: [], busy: false, pause: null, calls: [], actions: [], updates: [] };
  const epoch = { current: 1 };
  const context = vm.createContext({
    chatIn: state.draft, chatLoad: false, convId: state.id, chatEpoch: epoch,
    getConversationId: () => state.id, setChatIn: v => { state.draft = v; },
    setChatMsgs: v => { const apply = () => { state.messages = typeof v === 'function' ? v(state.messages) : v; }; options.deferUpdates ? state.updates.push(apply) : apply(); },
    setChatLoad: v => { state.busy = v; }, setPausePending: v => { state.pause = v; },
    inFlightEpoch: { current: -1 }, inFlightSince: { current: 0 }, inFlightTurn: { current: null }, setTurnStatus() {},
    agentSettings: {}, pauseAnswered: {}, curOrchId: 'local', voiceOn: false, focusWin: 'code', cameraOn: false,
    apiFetch: async () => options.forecast ? options.forecast.promise : response({}),
    fridayChatTurn: async (body, piece, trace) => { state.calls.push(body); state.piece = piece; state.trace = trace; return options.turn ? options.turn.promise : { response: 'Grounded answer' }; },
    fetch: async (url, init) => { state.calls.push(JSON.parse(init.body)); return response(options.turn ? await options.turn.promise : { response: 'Grounded answer' }); },
    fridayRunActions: actions => state.actions.push(actions), window: {}
  });
  vm.runInContext(page.slice(start, end) + '\nthis.send = sendChat;', context);
  return { state, epoch, send: context.send, switchTo(id) { state.id = id; epoch.current++; state.messages = [{ role: 'user', text: 'New chat' }]; state.busy = true; state.pause = null; } };
}

for (const name of pages) {
  test(name + ': repository requests use the owning chat and preserve its draft', async () => {
    const app = sender(name);
    await app.send('Learn codebase cb-a node file:main.py', { preserveDraft: true, expectedConversationId: 'chat-a' });
    assert.equal(app.state.draft, 'Unfinished thought');
    assert.equal(app.state.calls[0].conversation_id, 'chat-a');
    assert.equal(app.state.calls[0].workspace, 'code');
    assert.equal(app.state.messages.at(-1).text, 'Grounded answer');
    assert.equal(app.state.busy, false);
    await assert.rejects(app.send('Wrong owner', { expectedConversationId: 'chat-b' }), /conversation/);
    assert.equal(app.state.calls.length, 1);
    await app.send('Typed follow-up');
    assert.equal(app.state.draft, '');
  });

  test(name + ': switching while forecast waits cannot dispatch the old request', async () => {
    const forecast = deferred(), app = sender(name, { forecast });
    const sending = app.send('Learn cb-a', { preserveDraft: true, expectedConversationId: 'chat-a' });
    app.switchTo('chat-b');
    forecast.resolve(response({ will_pause: true }));
    await assert.rejects(sending, /conversation changed/);
    assert.equal(app.state.calls.length, 0);
    assert.equal(app.state.pause, null);
    assert.equal(app.state.messages[0].text, 'New chat');
    assert.equal(app.state.busy, true);
  });

  test(name + ': late replies, stream cleanup and actions cannot affect a newer chat', async () => {
    const turn = deferred(), app = sender(name, { turn });
    const sending = app.send('Explore cb-a', { preserveDraft: true, expectedConversationId: 'chat-a' });
    await tick();
    assert.equal(app.state.calls.length, 1);
    app.switchTo('chat-b');
    app.state.messages.push({ role: 'friday', text: 'New live answer', streaming: true });
    if (app.state.piece) app.state.piece('OLD DELTA');
    if (app.state.trace) app.state.trace('old-trace');
    turn.resolve({ response: 'Old final answer', privacy_hold: { text: 'Old hold' }, actions: [{ type: 'navigate' }] });
    await assert.rejects(sending, /request was sent to its original conversation/);
    assert.equal(app.state.messages.length, 2);
    assert.equal(app.state.messages[1].text, 'New live answer');
    assert.equal(app.state.messages[1].streaming, true);
    assert.equal(app.state.messages[1].trace_id, undefined);
    assert.equal(app.state.busy, true);
    assert.equal(app.state.pause, null);
    assert.equal(app.state.actions.length, 0);
  });

  test(name + ': privacy refusal remains visible and releases the native busy state', async () => {
    const turn = deferred(), app = sender(name, { turn });
    const sending = app.send('Adapt cb-a', { preserveDraft: true, expectedConversationId: 'chat-a' });
    await tick();
    turn.resolve({ privacy_hold: { text: 'Approval is required.' } });
    await sending;
    assert.match(app.state.messages.at(-1).text, /Approval is required/);
    assert.equal(app.state.pause.kind, 'privacy_hold');
    assert.equal(app.state.busy, false);
    assert.equal(app.state.draft, 'Unfinished thought');
  });
}

test('native streamed React updaters recheck their owner when applied', async () => {
  const turn = deferred(), app = sender('index.html', { turn, deferUpdates: true });
  const sending = app.send('Learn cb-a', { expectedConversationId: 'chat-a' });
  await tick();
  app.state.piece('An old token');
  app.switchTo('chat-b');
  app.state.updates.splice(0).forEach(apply => apply());
  turn.resolve({ response: 'Old answer' });
  await assert.rejects(sending, /request was sent to its original conversation/);
  assert.equal(app.state.messages.length, 1);
  assert.equal(app.state.messages[0].text, 'New chat');
});

function loader(name) {
  const page = read(name), app = page.indexOf('function App(');
  const start = page.indexOf('  const openConversation =', app);
  const end = page.indexOf('\n  }, []);', start) + '\n  }, []);'.length;
  assert.ok(start > app && end > start);
  const pending = new Map(), state = { id: null, messages: [], busy: false, pause: null, uploading: null }, epoch = { current: 0 };
  const context = vm.createContext({
    React: { useCallback: fn => fn }, chatEpoch: epoch, conversationReadyRef: { current: null }, conversationLoadRef: { current: null }, convIdRef: { current: null },
    getConversationId: () => state.id, setConvId: id => { state.id = id; }, setConvOpen() {}, setConvSeat() {},
    setChatMsgs: v => { state.messages = v; }, setChatLoad: v => { state.busy = v; }, setPausePending: v => { state.pause = v; }, setUploading: v => { state.uploading = v; },
    apiFetch: url => {
      if (!url.endsWith('/messages')) return Promise.resolve(response({ conversation: {} }));
      const request = deferred(); pending.set(url.split('/')[3], request); return request.promise;
    }
  });
  vm.runInContext(page.slice(start, end) + '\nthis.open = openConversation;', context);
  return { state, pending, open: context.open };
}

for (const name of pages) {
  test(name + ': opening a code chat awaits its transcript and rejects stale readiness', async () => {
    const app = loader(name);
    app.state.uploading = 'Previous attachment';
    let ready = false;
    const first = app.open('chat-a', { requireReady: true }).then(() => { ready = true; });
    if (name === 'index.html') assert.equal(app.state.uploading, null);
    const rejected = assert.rejects(first, /changed/);
    await tick();
    assert.equal(ready, false);
    const second = app.open('chat-b', { requireReady: true });
    app.pending.get('chat-a').resolve(response({ messages: [{ role: 'friday', text: 'Old transcript' }] }));
    app.pending.get('chat-b').resolve(response({ messages: [{ role: 'friday', text: 'Correct transcript' }] }));
    await rejected;
    assert.equal(await second, true);
    assert.equal(app.state.messages[0].text, 'Correct transcript');
    const failed = app.open('chat-c', { requireReady: true });
    app.pending.get('chat-c').resolve({ ok: false });
    await assert.rejects(failed, /Could not load/);
    assert.match(app.state.messages[0].text, /Could not load/);
  });

  test(name + ': Code reuses an existing transcript load and does not reset a live chat', async () => {
    const app = loader(name);
    const initial = app.open('chat-a');
    const pending = app.pending.get('chat-a');
    let ready = false;
    const reused = app.open('chat-a', { requireReady: true, reuseReady: true }).then(() => { ready = true; });
    assert.equal(app.pending.get('chat-a'), pending);
    await tick();
    assert.equal(ready, false);
    pending.resolve(response({ messages: [{ role: 'friday', text: 'Loaded transcript' }] }));
    await Promise.all([initial, reused]);
    app.state.messages.push({ role: 'user', text: 'Current request' });
    app.state.busy = true;
    await app.open('chat-a', { requireReady: true, reuseReady: true });
    assert.equal(app.pending.get('chat-a'), pending);
    assert.equal(app.state.messages.at(-1).text, 'Current request');
    assert.equal(app.state.busy, true);
    const failing = app.open('chat-b');
    const strict = app.open('chat-b', { requireReady: true, reuseReady: true });
    app.pending.get('chat-b').resolve({ ok: false });
    await assert.rejects(strict, /Could not load/);
    assert.equal(await failing, false);
    const retry = app.open('chat-b', { requireReady: true, reuseReady: true });
    app.pending.get('chat-b').resolve(response({ messages: [] }));
    assert.equal(await retry, true);
  });
}

test('Code bridge hands the real native surface and guarded sender to Studio', async () => {
  const page = read('index.html'), start = page.indexOf('  const codeChat = {'), end = page.indexOf('\n  const wsMap', start);
  assert.ok(start > 0 && end > start);
  const sent = [], opened = [], queued = [], props = { chatMsgs: [{ text: 'Real transcript' }] };
  let activeId = 'chat-a';
  const context = vm.createContext({
    codeChatActive: true, convId: 'chat-a', chatLoad: false, getConversationId: () => activeId, nativeChatProps: props,
    chatEpoch: { current: 1 }, setChatMsgs: value => queued.push(value),
    ChatSurface: 'native-chat', React: { createElement: (type, p) => ({ type, props: p }) },
    sendChat: async (message, opts) => sent.push({ message, opts }), openConversation: async (...args) => opened.push(args)
  });
  const ownerStart = page.indexOf('  const ownedChatUpdate =', page.indexOf('function App('));
  const ownerEnd = page.indexOf('\n  };', ownerStart) + '\n  };'.length;
  vm.runInContext(page.slice(ownerStart, ownerEnd), context);
  const get = () => vm.runInContext('(() => {' + page.slice(start, end) + ';return codeChat;})()', context);
  const active = get();
  assert.equal(active.surface.type, 'native-chat');
  assert.equal(active.surface.props.mode, 'code');
  assert.equal(active.surface.props.chatMsgs, props.chatMsgs);
  await active.ask('Learn cb-a', 'chat-a');
  assert.equal(sent[0].opts.preserveDraft, true);
  assert.equal(sent[0].opts.expectedConversationId, 'chat-a');
  await active.openConversation('chat-a');
  assert.equal(opened[0][1].requireReady, true);
  assert.equal(opened[0][1].reuseReady, true);
  await assert.rejects(active.ask('Wrong', 'chat-b'), /conversation/);
  active.surface.props.setChatMsgs(previous => [...previous, { text: 'Queued old status' }]);
  assert.equal(queued.length, 1);
  activeId = 'chat-b'; context.chatEpoch.current++;
  const currentMessages = [{ text: 'New conversation' }];
  assert.equal(queued.shift()(currentMessages), currentMessages);
  active.surface.props.setChatMsgs(previous => [...previous, { text: 'Late old status' }]);
  assert.equal(queued.length, 0);
  context.codeChatActive = false;
  const inactive = get();
  assert.equal(inactive.surface, null);
  await assert.rejects(inactive.ask('Background', 'chat-a'), /conversation/);
  await assert.rejects(inactive.openConversation('chat-a'), /Code workspace/);
  assert.equal(sent.length, 1);
});

test('Code shell bypasses the duplicate artifact host and the registry carries repository selection', () => {
  const page = read('index.html'), start = page.indexOf('function FridayChatShell('), end = page.indexOf('function ChatSurface(', start);
  const context = vm.createContext({ window: { FridayArtifactHost: 'artifact-host' }, React: { createElement: (type, props, ...children) => ({ type, props, children }) } });
  vm.runInContext(page.slice(start, end) + '\nthis.shell = FridayChatShell;', context);
  assert.equal(context.shell({ mode: 'code', children: 'native controls' }).props['data-code-chat-surface'], '');
  assert.equal(context.shell({ mode: 'panel' }).type, 'artifact-host');
  for (const name of pages) {
    const source = read(name), a = source.indexOf('function CodeWS({chat})'), b = source.indexOf('function DevCodeLogs()', a);
    let targetHandler, reporter;
    const studio = { window: { FridayCodeStudio: 'studio' }, React: context.React, useState: () => [null, () => {}], useRef: value => ({ current: value }), useTabState: (name, fn) => { reporter = fn; }, useNavTarget: (name, fn) => { targetHandler = fn; }, DevRepos: 1, DevGit: 2, DevFiles: 3, DevProcs: 4, DevCodeLogs: 5 };
    const scope = vm.createContext(studio);
    vm.runInContext(source.slice(a, b) + '\nthis.render = CodeWS;', scope);
    const result = scope.render({ chat: 'native-owner' });
    assert.equal(result.type, 'studio');
    assert.equal(result.props.chat, 'native-owner');
    assert.equal(typeof targetHandler, 'function');
    result.props.onNavState({ tab: 'files', codebase_id: 'cb-a', path: 'main.py' });
    assert.equal(reporter().codebase_id, 'cb-a');
    assert.equal(reporter().path, 'main.py');
  }
});

test('Code chat keeps model and native message controls without a second conversation selector', () => {
  const page = read('index.html');
  const start = page.indexOf('function ChatSurface('), end = page.indexOf('/* The typing-indicator line', start);
  const element = (type, props, ...children) => ({ type, props: props || {}, children });
  const flat = node => node && typeof node === 'object' ? [node, ...node.children.flat(Infinity).flatMap(flat)] : [];
  // These are separate initial mounts; the new-chat lifecycle is covered by its own tests.
  const cleanups = [];
  const context = vm.createContext({
    React: {
      createElement: element,
      useRef: initial => ({ current: initial }),
      useState(initial) {
        let value = typeof initial === 'function' ? initial() : initial;
        return [value, next => { value = typeof next === 'function' ? next(value) : next; }];
      },
      useEffect(effect) { const cleanup = effect(); if (typeof cleanup === 'function') cleanups.push(cleanup); }
    }, window: {}, useSnapMenuOpener: () => ({ bind: {}, open: false, anchor: { current: null } }),
    FridayChatShell: 'chat-shell', ConversationBar: 'conversation-bar', PodcastButton: 'podcast', FridayChatInput: 'composer',
    fridayName: () => 'Assistant', fridayTM: value => value
  });
  vm.runInContext(page.slice(start, end) + '\nthis.surface = ChatSurface;', context);
  const props = { convId: 'chat-a', convList: [], agentSettings: {}, audioInputDevices: [], audioOutputDevices: [], chatMsgs: [], chatIn: '', chatLoad: false, voiceOn: false };
  const code = flat(context.surface({ ...props, mode: 'code' }));
  const panel = flat(context.surface({ ...props, mode: 'panel' }));
  const named = (nodes, title) => nodes.find(node => node.props.title === title || node.props['aria-label'] === title);
  assert.equal(code.find(node => node.type === 'conversation-bar').props.fixedConversation, true);
  assert.equal(panel.find(node => node.type === 'conversation-bar').props.fixedConversation, false);
  assert.equal(code.some(node => node.children.includes('+ New Chat')), false);
  assert.equal(code.some(node => node.children.includes('♥ Heartbeat')), false);
  assert.equal(panel.some(node => node.children.includes('+ New Chat')), true);
  assert.equal(panel.some(node => node.children.includes('♥ Heartbeat')), true);
  assert.equal(named(code, 'Generate a source dossier for this conversation — every claim, its source, confidence, and link'), undefined);
  assert.ok(named(panel, 'Generate a source dossier for this conversation — every claim, its source, confidence, and link'));
  assert.ok(code.find(node => node.children.some(child => typeof child === 'string' && child.includes('Cite Sources'))));
  assert.ok(code.find(node => node.type === 'composer'));
  assert.ok(code.find(node => Object.hasOwn(node.props, 'data-friday-voice-toggle')));
  assert.ok(named(code, 'Open this conversation as a window'));
  assert.ok(named(code, 'Open this conversation in a new tab'));
  assert.ok(named(code, 'Attach a file — image, PDF or text'));

  const barStart = page.indexOf('function ConversationBar('), barEnd = page.indexOf('/* ── The chat sidebar', barStart);
  let picker = false, switched = false;
  const scope = vm.createContext({ E: element, ConversationSeatPicker: 'seat-picker', ConversationRow: 'conversation-row', prettyModel: model => model });
  vm.runInContext(page.slice(barStart, barEnd) + '\nthis.bar = ConversationBar;', scope);
  const barProps = { convList: [{ id: 'chat-a', title: 'Selected codebase' }], convId: 'chat-a', convSeat: null, open: true, pickerOpen: true,
    setOpen: () => { switched = true; }, onRefresh() {}, setPickerOpen: value => { picker = value; } };
  const fixed = flat(scope.bar({ ...barProps, fixedConversation: true }));
  assert.equal(named(fixed, 'Switch conversation'), undefined);
  assert.equal(fixed.some(node => node.type === 'conversation-row'), false);
  assert.ok(fixed.find(node => node.type === 'seat-picker'));
  const containedPopup = fixed.find(node => node.props.className === 'fr-code-model-popup');
  assert.equal(containedPopup.props.style.position, 'static');
  assert.equal(containedPopup.props.style.flexBasis, '100%');
  assert.equal(containedPopup.props.style.width, '100%');
  named(fixed, 'Model for this conversation').props.onClick();
  assert.equal(picker, false);
  assert.equal(switched, false);
  const ordinary = flat(scope.bar(barProps));
  assert.equal(ordinary.some(node => node.props.className === 'fr-code-model-popup'), false);
  assert.ok(ordinary.find(node => node.props.style && node.props.style.position === 'absolute' && node.children.some(child => child && child.type === 'seat-picker')));
  named(ordinary, 'Switch conversation').props.onClick();
  assert.equal(switched, true);
  assert.ok(ordinary.find(node => node.type === 'conversation-row'));
  assert.match(read('ui_parts/app.html'), /mode!=='code'&&<button onClick=\{createNewChat\} disabled=\{newChatState\.pending\}/);
  cleanups.forEach(cleanup => cleanup());
});

test('the contained Code model picker retains model selection and ordinary popup sizing', () => {
  const page = read('index.html'), start = page.indexOf('function ModelPicker('), end = page.indexOf('// ── Surface 1:', start);
  const element = (type, props, ...children) => ({ type, props: props || {}, children });
  const flat = node => node && typeof node === 'object' ? [node, ...node.children.flat(Infinity).flatMap(flat)] : [];
  const context = vm.createContext({ E: element, useState: initial => [initial, () => {}], suitability: () => ({ ok: true }), ModelRow: 'model-row', LoadFailure: 'load-failure' });
  vm.runInContext(page.slice(start, end) + '\nthis.picker = ModelPicker;', context);
  let selected = null, closed = false;
  const model = { id: 'local-example', label: 'Example local model', local: true };
  const rendered = context.picker({ intel: { models: [model] }, need: {}, current: null, onPick: value => { selected = value.id; }, onClose: () => { closed = true; } });
  assert.equal(rendered.props.className, 'fr-model-picker');
  assert.equal(rendered.props.style.width, 400, 'ordinary picker sizing stays unchanged');
  flat(rendered).find(node => node.type === 'model-row').props.onPick(model);
  assert.equal(selected, model.id);
  assert.equal(closed, true);
});

test('the legacy fallback opens Git for the repository that was selected', async () => {
  const page = read('index.html'), component = page.includes('function LegacyCodeWS(') ? 'LegacyCodeWS' : 'CodeWS';
  const start = page.indexOf('function ' + component + '('), end = page.indexOf('// ═══ NEWS WORKSPACE', start);
  const slots = [], effects = [];
  let cursor = 0;
  const repositories = [{ name: 'Repo A', path: '/synthetic/a' }, { name: 'Repo B', path: '/synthetic/b' }];
  const element = (type, props, ...children) => ({ type, props: props || {}, children });
  const flat = node => node && typeof node === 'object' ? [node, ...node.children.flat(Infinity).flatMap(flat)] : [];
  const context = vm.createContext({
    React: { createElement: element }, CODE_TABS: [{ id: 'repos', label: 'Repos' }, { id: 'git', label: 'Git' }],
    DevRepos: 'repositories', DevGit: 'git', DevVibe: 'vibe', DevFiles: 'files', DevProcs: 'processes',
    useTabState() {}, useNavTarget() {},
    useState: initial => {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: initial };
      return [slots[i].value, value => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }];
    },
    useRef: initial => { const i = cursor++; if (!slots[i]) slots[i] = { current: initial }; return slots[i]; },
    useEffect: (fn, deps) => { const i = cursor++; if (!slots[i]) { slots[i] = { deps }; effects.push(fn); } },
    fetch: async url => response(url.startsWith('/api/repos/') ? { repos: repositories } : { events: [] }),
    EventSource: function () { this.close = () => {}; }
  });
  vm.runInContext(page.slice(start, end) + '\nthis.fallback = ' + component + ';', context);
  const render = () => { cursor = 0; return context.fallback({}); };
  render();
  const cleanups = effects.splice(0).map(fn => fn());
  await tick();
  const picker = flat(render()).find(node => node.type === 'repositories');
  assert.equal(picker.props.repos.length, 2);
  picker.props.onPick(picker.props.repos[1]);
  const git = flat(render()).find(node => node.type === 'git');
  assert.equal(git.props.selectedRepo, 'Repo B');
  assert.equal(git.props.repos[0].name, 'Repo A');
  cleanups.forEach(fn => { if (typeof fn === 'function') fn(); });
  const mirror = read('ui_parts/app.html');
  assert.match(mirror, /onPick=\{r=>\{setSelectedRepo\(r.name\);setTab\('git'\)\}\}/);
  assert.match(mirror, /<DevGit repos=\{repos\} onLog=\{\(\)=>\{\}\} onRefresh=\{scanRepos\} selectedRepo=\{selectedRepo\}/);
});

function nativeAction(name, action, deferUpdates = false) {
  const page = read(name), app = page.indexOf('function App(');
  const ownerStart = page.indexOf('  const ownedChatUpdate =', app);
  const ownerEnd = page.indexOf('\n  };', ownerStart) + '\n  };'.length;
  assert.ok(ownerStart > app && ownerEnd > ownerStart);
  const state = { id: 'chat-a', epoch: { current: 1 }, messages: [], busy: false, uploading: null, seat: null, updates: [], calls: [], toasts: [], refreshes: 0 };
  const pending = deferred();
  const setter = key => value => {
    const apply = () => { state[key] = typeof value === 'function' ? value(state[key]) : value; };
    deferUpdates ? state.updates.push(apply) : apply();
  };
  const context = vm.createContext({
    getConversationId: () => state.id, chatEpoch: state.epoch, convId: 'chat-a',
    setChatMsgs: setter('messages'), setChatLoad: setter('busy'), setUploading: setter('uploading'), setConvSeat: setter('seat'),
    setConvPickerOpen() {}, refreshConvs: () => { state.refreshes++; }, prettyModel: model => model, fridayToast: message => state.toasts.push(message),
    apiFetch: (url, init) => { state.calls.push({ url, init }); return pending.promise; },
    fetch: (url, init) => { state.calls.push({ url, init }); return pending.promise; },
    FormData: class { constructor() { this.fields = []; } append(...value) { this.fields.push(value); } },
    fridaySeatSaves: new Map(), AbortController, setTimeout, clearTimeout
  });
  let implementation = page.slice(ownerStart, ownerEnd);
  if (name === 'index.html' && action !== 'seat') {
    const start = page.indexOf('async function fridayAnalyzeFile('), end = page.indexOf('// ═══ FRIDAY SAYS', start);
    implementation += '\n' + page.slice(start, end);
  }
  if (action === 'seat') {
    const start = page.indexOf('async function fridaySaveConversationSeat('), end = page.indexOf('// End conversation seat saves', start);
    implementation += '\n' + page.slice(start, end);
    const bindStart = page.indexOf('  const bindConvSeat =', app), bindEnd = page.indexOf('\n  const chatEpoch', bindStart);
    implementation += '\n' + page.slice(bindStart, bindEnd) + '\nthis.invoke = bindConvSeat;';
  } else {
    const declaration = action === 'attachment' ? '  const uploadFile =' : name === 'index.html' ? '  const showSources =' : '  const showSources=async';
    const start = page.indexOf(declaration, app);
    const end = name === 'index.html' ? page.indexOf('\n', start) : page.indexOf('\n  };', start) + '\n  };'.length;
    implementation += '\n' + page.slice(start, end) + '\nthis.invoke = ' + (action === 'attachment' ? 'uploadFile' : 'showSources') + ';';
  }
  vm.runInContext(implementation, context);
  return { state, pending, invoke: context.invoke, flush() { state.updates.splice(0).forEach(apply => apply()); }, switchTo(id) {
    state.id = id; state.epoch.current++; state.messages = [{ role: 'user', text: 'New conversation' }]; state.busy = true; state.uploading = 'new-file.txt'; state.seat = { model: 'new-seat' };
  } };
}

for (const [name, action] of [['index.html', 'attachment'], ['index.html', 'sources'], ['ui_parts/app.html', 'sources']]) {
  const result = action === 'attachment'
    ? { analysis: 'File analysis', vision_events: [{ text: 'Local analysis only' }] }
    : { markdown: 'Daily source dossier' };
  const invoke = app => app.invoke({ name: 'example.txt', size: 24 });
  test(name + ': ' + action + ' results cannot enter a later conversation or clear its progress', async () => {
    const app = nativeAction(name, action), started = invoke(app);
    assert.equal(app.state.calls.length, 1);
    if (action === 'attachment') {
      assert.equal(app.state.calls[0].url, '/api/analyze');
      assert.equal(app.state.calls[0].init.body.fields[0][0], 'file');
    } else assert.equal(app.state.calls[0].url, '/api/sources/dossier/current');
    app.switchTo('chat-b');
    app.pending.resolve(response(result));
    await started;
    assert.equal(app.state.messages.length, 1);
    assert.equal(app.state.messages[0].text, 'New conversation');
    assert.equal(app.state.busy, true);
    assert.equal(app.state.uploading, 'new-file.txt');
  });

  test(name + ': ' + action + ' succeeds normally and guards deferred React updates', async () => {
    const ordinary = nativeAction(name, action), started = invoke(ordinary);
    ordinary.pending.resolve(response(result));
    await started;
    assert.equal(ordinary.state.messages.at(-1).text, result.analysis || result.markdown);
    if (action === 'attachment') assert.equal(ordinary.state.uploading, null);
    else assert.equal(ordinary.state.busy, false);
    const delayed = nativeAction(name, action, true), queued = invoke(delayed);
    delayed.pending.resolve(response(result));
    await queued;
    assert.ok(delayed.state.updates.length > 0);
    delayed.switchTo('chat-b'); delayed.flush();
    assert.equal(delayed.state.messages.length, 1);
    assert.equal(delayed.state.messages[0].text, 'New conversation');
    assert.equal(delayed.state.busy, true);
    assert.equal(delayed.state.uploading, 'new-file.txt');
  });
}

test('the actual model binding PATCH stays with its captured chat after a selection change', async () => {
  const stale = nativeAction('index.html', 'seat');
  stale.state.id = 'chat-b';
  stale.invoke({ id: 'chosen-seat', providers: ['local'] });
  assert.equal(stale.state.calls.length, 0);
  const app = nativeAction('index.html', 'seat');
  app.invoke({ id: 'chosen-seat', providers: ['local'] });
  assert.equal(app.state.calls[0].url, '/api/conversations/chat-a');
  assert.equal(JSON.parse(app.state.calls[0].init.body).seat.model, 'chosen-seat');
  app.switchTo('chat-b');
  app.pending.resolve(response({ conversation: { seat: { model: 'chosen-seat', provider: 'local' } } }));
  await tick();
  assert.equal(app.state.seat.model, 'new-seat');
  assert.equal(app.state.messages.length, 1);
  assert.equal(app.state.refreshes, 0);
});

test('model binding updates its owning chat, but queued updates recheck identity', async () => {
  const result = response({ conversation: { seat: { model: 'chosen-seat', provider: 'local' } } });
  const ordinary = nativeAction('index.html', 'seat');
  ordinary.invoke({ id: 'chosen-seat', providers: ['local'] }); ordinary.pending.resolve(result);
  await tick();
  assert.equal(ordinary.state.seat.model, 'chosen-seat');
  assert.match(ordinary.state.messages.at(-1).text, /chosen-seat/);
  assert.equal(ordinary.state.refreshes, 1);
  const delayed = nativeAction('index.html', 'seat', true);
  delayed.invoke({ id: 'chosen-seat', providers: ['local'] }); delayed.pending.resolve(result);
  await tick();
  assert.ok(delayed.state.updates.length > 0);
  delayed.switchTo('chat-b'); delayed.flush();
  assert.equal(delayed.state.seat.model, 'new-seat');
  assert.equal(delayed.state.messages.length, 1);
});
