// A chat send that fails is retried once and then marked on the owner's own
// bubble, never answered with a bare "[Connection error]" reply.
//
// index.html: fridayChatTurn retries a send the server never accepted (no
// `started` frame, network error, 5xx) once, after a wait and a health probe,
// with the SAME turn_id; when that fails too the error says 'not_received'. A
// turn the server accepted whose stream died says 'unfinished' and is not
// re-sent. ui_parts/app.html: fridayPostChatTurn, the same rule over the
// blocking endpoint. Both: fridayMarkFailedSend marks the bubble.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'ui_parts', 'app.html'), 'utf8');

function pageBlock(source, name) {
  const m = new RegExp('/\\* ' + name + ':begin \\*/\\n([\\s\\S]*?)/\\* ' + name + ':end \\*/').exec(source);
  assert.ok(m, 'the page carries a ' + name + ' block');
  return m[1];
}

function slice(source, start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'the page carries ' + start);
  return source.slice(from, to);
}

const enc = new TextEncoder();

// A Response-like object whose body streams the given SSE frames, then ends.
function sse(frames) {
  const chunks = frames.map(f => enc.encode(f));
  return {
    ok: true, status: 200,
    body: { getReader: () => ({ read: async () => chunks.length
      ? { done: false, value: chunks.shift() } : { done: true } }) },
  };
}
const json = (status, data) => ({ ok: status < 400, status, body: null, json: async () => data });

// Load the page's own functions with a scripted apiFetch and an instant clock.
function load(source, start, end, names, routes) {
  const calls = [];
  const apiFetch = async (url, opts) => {
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url, body });
    const handler = routes[url.replace(/\/api\/chat\/turn\/[^/]+\/liveness/, '/liveness')];
    assert.ok(handler, 'unexpected request ' + url);
    return handler(calls.filter(c => c.url === url).length, body);
  };
  const context = vm.createContext({
    window: {}, apiFetch, TextDecoder, Date, Math, JSON, Error, Promise,
    setTimeout: (fn) => { fn(); return 0; }, localStorage: { getItem: () => null, setItem() {} },
  });
  vm.runInContext(pageBlock(index, 'fridayShortcuts') + pageBlock(index, 'fridayHerName')
    + slice(source, start, end) + '\nthis.out = {' + names.join(',') + '};', context);
  return { fns: context.out, calls };
}

const DOWN = () => { throw new TypeError('Failed to fetch'); };
const OK_FRAMES = [': open\n\n', 'data: {"started":true,"turn_id":"turn-x","accepted":true}\n\n',
  'data: {"done":true,"payload":{"response":"Saved."}}\n\n'];

function indexTurn(routes) {
  return load(index, 'function fridayTurnFailure(', 'function useModelCatalog()',
    ['fridayChatTurn', 'fridayMarkFailedSend'], routes);
}

test('index: a send the server never accepted is retried once, same turn id', async () => {
  const { fns, calls } = indexTurn({
    '/api/chat/stream': n => (n === 1 ? DOWN() : sse(OK_FRAMES)),
    '/api/chat': DOWN,
    '/api/health': () => json(200, { status: 'ok' }),
  });
  const d = await fns.fridayChatTurn({ message: 'note this', turn_id: 'turn-x' });
  assert.equal(d.response, 'Saved.');
  const sends = calls.filter(c => c.url === '/api/chat/stream' || c.url === '/api/chat');
  assert.deepEqual(sends.map(c => c.body.turn_id), ['turn-x', 'turn-x', 'turn-x']);
  assert.ok(calls.some(c => c.url === '/api/health'), 'no health probe before the retry');
});

test('index: when the retry fails too, the failure is "not received"', async () => {
  const { fns, calls } = indexTurn({
    '/api/chat/stream': DOWN, '/api/chat': DOWN,
    '/api/health': () => json(200, { status: 'ok' }),
  });
  await assert.rejects(fns.fridayChatTurn({ message: 'x', turn_id: 'turn-y' }),
    e => e.turnFailure === 'not_received');
  assert.equal(calls.filter(c => c.url === '/api/chat/stream').length, 2, 'one retry, not more');
});

test('index: no retry while the server does not answer its health probe', async () => {
  const { fns, calls } = indexTurn({
    '/api/chat/stream': DOWN, '/api/chat': DOWN, '/api/health': DOWN,
  });
  await assert.rejects(fns.fridayChatTurn({ message: 'x', turn_id: 'turn-z' }),
    e => e.turnFailure === 'not_received');
  assert.equal(calls.filter(c => c.url === '/api/chat/stream').length, 1);
});

test('index: an accepted turn whose stream died is "unfinished" and is not re-sent', async () => {
  const { fns, calls } = indexTurn({
    '/api/chat/stream': () => sse([': open\n\n', 'data: {"started":true,"turn_id":"turn-u"}\n\n']),
    '/liveness': () => json(200, { state: 'gone', alive: false }),
    '/api/chat': () => { throw new Error('must not re-send an accepted turn'); },
  });
  await assert.rejects(fns.fridayChatTurn({ message: 'x', turn_id: 'turn-u' }),
    e => e.turnFailure === 'unfinished');
  assert.equal(calls.filter(c => c.url === '/api/chat/stream').length, 1);
});

test('app.html: the blocking send follows the same rule', async () => {
  const routes = (chat) => ({ '/api/chat': chat, '/api/health': () => json(200, { status: 'ok' }) });
  let r = load(app, 'function fridayTurnFailure(', 'function ChatSurface(',
    ['fridayPostChatTurn', 'fridayMarkFailedSend'],
    routes(n => (n === 1 ? DOWN() : json(200, { response: 'Saved.' }))));
  assert.equal((await r.fns.fridayPostChatTurn({ message: 'x', turn_id: 'turn-a' })).response, 'Saved.');
  assert.deepEqual(r.calls.filter(c => c.url === '/api/chat').map(c => c.body.turn_id), ['turn-a', 'turn-a']);
  r = load(app, 'function fridayTurnFailure(', 'function ChatSurface(',
    ['fridayPostChatTurn'], routes(() => json(503, {})));
  await assert.rejects(r.fns.fridayPostChatTurn({ message: 'x', turn_id: 'turn-b' }),
    e => e.turnFailure === 'not_received');
  assert.equal(r.calls.filter(c => c.url === '/api/chat').length, 2);
});

for (const [name, source, end] of [['index', index, 'function useModelCatalog()'],
                                   ['app.html', app, 'function ChatSurface(']]) {
  test(name + ': the failed send marks the owner\'s own bubble with its turn id', () => {
    const { fns } = load(source, 'function fridayTurnFailure(', end, ['fridayMarkFailedSend'], {});
    const msgs = [{ role: 'user', text: 'hi' }, { role: 'friday', text: 'hello' },
                  { role: 'user', text: 'note this' }];
    const out = fns.fridayMarkFailedSend(msgs, 'note this', 'not_received', 'turn-q');
    assert.equal(out.length, 3, 'no reply bubble is added');
    assert.equal(out[2].send_failed, 'not_received');
    assert.equal(out[2].turn_id, 'turn-q');
    assert.equal(out[0].send_failed, undefined);
  });
}
