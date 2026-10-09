const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

function block(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'shared model-switch implementation exists');
  return source.slice(from, to);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return {promise, resolve, reject};
}
function runtime(code) {
  const requests = [], timers = new Map();
  let timerId = 0;
  const context = vm.createContext({
    AbortController, console, Date, encodeURIComponent,
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { return 0; }, clearInterval() {},
    apiFetch(url, opts) { const d = deferred(); requests.push({...d, url, opts}); return d.promise; },
  });
  vm.runInContext(code, context);
  return {context, requests, timers};
}
function response(model, extra = {}) {
  return {ok:true, json:async () => ({status:'ok',conversation:{seat:model ? {model} : null},...extra})};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('latest completed model choice wins and failed changes keep the confirmed seat', async () => {
  const r = runtime(block('const fridaySeatSaves =', '// End conversation seat saves'));
  let seat = 'original'; const errors = [], saved = [];
  const pick = model => r.context.fridaySaveConversationSeat('test', {model}, value => {
    seat = value && value.model; saved.push(seat);
  }, error => errors.push(error.message));
  const first = pick('first'), second = pick('second');
  r.requests[1].resolve(response('second')); await second;
  r.requests[0].resolve(response('first')); await first;
  assert.equal(seat, 'second'); assert.deepEqual(saved, ['second']);
  const failed = pick('third');
  r.requests[2].resolve({ok:false,status:503,json:async()=>({error:'Provider unavailable'})});
  await failed;
  assert.equal(seat, 'second'); assert.match(errors[0], /Provider unavailable/);
});

test('refused saves show the server reason and timeouts allow a later choice', async () => {
  const r = runtime(block('const fridaySeatSaves =', '// End conversation seat saves'));
  const errors = [], saved = [];
  const pick = model => r.context.fridaySaveConversationSeat('test', {model}, value => saved.push(value), error => errors.push(error.message));
  const refused = pick('first');
  r.requests[0].resolve(response('original', {note:'Another model is serving.'})); await refused;
  assert.match(errors[0], /Another model is serving/); assert.equal(saved.length, 0);
  const timed = pick('second');
  [...r.timers.values()][0]();
  r.requests[1].reject(Object.assign(new Error('aborted'), {name:'AbortError'})); await timed;
  assert.match(errors[1], /confirm/i);
  const later = pick('third'); r.requests[2].resolve(response('third')); await later;
  assert.equal(saved[0].model, 'third');
  const superseded = pick('fourth');
  r.requests[3].resolve(response('another-window', {superseded:true})); await superseded;
  assert.match(errors[2], /another window/);
  assert.equal(saved.length, 1);
});

test('picker polling shares one request and unmount ignores its late response', async () => {
  const r = runtime('');
  const updates = [], effects = [];
  r.context.useState = initial => [initial, value => updates.push(value)];
  r.context.useRef = initial => ({current:initial});
  r.context.useEffect = effect => effects.push(effect);
  r.context.React = {useCallback:fn => fn};
  vm.runInContext(block('function useIntelligence(', '// ── Shared pieces'), r.context);
  const hook = r.context.useIntelligence(2000, 'picker');
  const clean = effects[0]();
  hook.reload(); hook.reload();
  assert.equal(r.requests.length, 1);
  assert.equal(r.requests[0].url, '/api/intelligence?view=picker');
  clean();
  r.requests[0].resolve(response('late')); await flush();
  assert.equal(updates.length, 0);
  assert.equal(r.requests[0].opts.signal.aborted, true);
});

test('both chat surfaces use acknowledged saves and both dropdowns use the picker feed', () => {
  for (const name of ['const bindSeat =', 'const bindConvSeat =']) {
    const from = source.indexOf(name);
    const body = source.slice(from, source.indexOf('\n  };', from));
    assert.match(body, /fridaySaveConversationSeat/);
    assert.match(body, /epoch|chatEpoch/);
    assert.doesNotMatch(body, /catch\(\(\) => \{\}\)/);
  }
  assert.match(source, /useIntelligence\(2000, 'picker'\)/);
  assert.match(source, /useIntelligence\(2000, 'picker', open\)/);
  const mirror = fs.readFileSync(path.join(root, 'ui_parts/app.html'), 'utf8');
  assert.ok(mirror.includes(block('function useIntelligence(', '// ── Shared pieces')));
  assert.ok(mirror.includes(block('const fridaySeatSaves =', '// End conversation seat saves')));
});

test('pending selection retries with its reservation and stops after a newer choice', async () => {
  const r = runtime(block('const fridaySeatSaves =', '// End conversation seat saves'));
  const saved = [], errors = [], pending = [];
  const pick = model => r.context.fridaySaveConversationSeat('test', {model}, value => saved.push(value.model),
    error => errors.push(error.message), message => pending.push(message));
  const first = pick('first');
  r.requests[0].resolve(response('original', {seat_pending:true,seat_change_id:'reservation'}));
  await flush();
  assert.equal(pending.length, 1);
  const [timer, resume] = [...r.timers.entries()][0]; r.timers.delete(timer); resume();
  await flush();
  assert.equal(JSON.parse(r.requests[1].opts.body).seat_change_id, 'reservation');
  r.requests[1].resolve(response('original', {seat_pending:true,seat_change_id:'reservation'}));
  await flush();
  const second = pick('second');
  r.requests[2].resolve(response('second')); await second;
  const [oldTimer, oldResume] = [...r.timers.entries()][0]; r.timers.delete(oldTimer); oldResume();
  await first;
  assert.deepEqual(saved, ['second']); assert.deepEqual(errors, []);
  assert.equal(r.requests.length, 3, 'old pending choice must not send another write');
});

test('persistent unknown discovery becomes visible and clears after a useful result', async () => {
  const r = runtime('');
  let now = 0;
  const updates = [], effects = [];
  r.context.Date = {now:() => now};
  r.context.useState = initial => [initial, value => updates.push(value)];
  r.context.useRef = initial => ({current:initial});
  r.context.useEffect = effect => effects.push(effect);
  r.context.React = {useCallback:fn => fn};
  vm.runInContext(block('function useIntelligence(', 'const fridaySeatSaves ='), r.context);
  const hook = r.context.useIntelligence(2000, 'picker');
  const unknown = {ok:true,json:async()=>({status:'ok',models:[],catalog_reading:'unknown'})};
  let poll = hook.reload(); r.requests[0].resolve(unknown); await poll;
  now = 11000;
  poll = hook.reload(); r.requests[1].resolve(unknown); await poll;
  assert.ok(updates.some(value => value && value.kind === 'discovery'));
  poll = hook.reload();
  r.requests[2].resolve({ok:true,json:async()=>({status:'ok',models:[{id:'ready'}],catalog_reading:'fresh'})});
  await poll;
  assert.equal(updates.at(-2), null, 'fresh catalogue clears the discovery error');
});

test('local models with unknown residency are not labeled cloud or cold', () => {
  const r = runtime('');
  r.context.E = (tag, props, ...children) => ({tag, props, children});
  r.context.fmtGB = () => '';
  vm.runInContext(block('function StateChip(', '/** One selectable model.'), r.context);
  vm.runInContext(block('function consequence(', '// ── Data'), r.context);
  const model = {id:'local:test',local:true,state:'unknown'};
  assert.match(r.context.StateChip({m:model}).children.join(' '), /unknown/);
  const consequence = r.context.consequence(model);
  assert.match(consequence, /local.*load state not shown/);
  assert.doesNotMatch(consequence, /cold|cloud|answers immediately|checked when/);
});

test('the served UI scripts parse', () => {
  let count = 0;
  for (const match of source.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    if (/\bsrc\s*=/.test(match[1]) || !match[2].trim()) continue;
    new vm.Script(match[2], {filename:'index.html'});
    count += 1;
  }
  assert.ok(count > 0);
});
