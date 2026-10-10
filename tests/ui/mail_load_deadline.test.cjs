// A mail read that does not answer is shown as an error, never as an empty inbox.
// Drives the shipped static/friday_mail.js with a minimal React and a fetch
// that never answers /api/messages. Synthetic data only; no server, no network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/friday_mail.js'), 'utf8');
const response = body => ({ ok: true, status: 200, json: async () => body });
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

function text(tree) {
  if (tree == null || tree === false) return '';
  if (typeof tree !== 'object') return String(tree);
  if (Array.isArray(tree)) return tree.map(text).join(' ');
  return text(tree.children);
}
function nodes(tree) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  return [tree, ...nodes(tree.children)];
}

function harness(fetcher) {
  const slots = [], pending = [];
  let cursor = 0, changed = false, tree;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState(init) {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: typeof init === 'function' ? init() : init };
      return [slots[i].value, next => {
        const value = typeof next === 'function' ? next(slots[i].value) : next;
        if (!Object.is(slots[i].value, value)) { slots[i].value = value; changed = true; }
      }];
    },
    useRef(value) { const i = cursor++; if (!slots[i]) slots[i] = { current: value }; return slots[i]; },
    useMemo(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { deps, value: fn() }; return slots[i].value; },
    useCallback(fn, deps) { return React.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const i = cursor++, previous = slots[i];
      if (!previous || !same(previous.deps, deps)) pending.push(() => { if (previous && previous.cleanup) previous.cleanup(); slots[i] = { deps, cleanup: fn() }; });
    }
  };
  const noop = () => {};
  const stage = new Proxy({}, { get: (_, name) => name === 'emptySelection' ? () => ({ refs: [], held: {} })
    : name === 'reduceSelection' ? (s) => ({ changed: false, state: s })
    : name === 'chipsRow' ? () => null : noop });
  const win = { __FRIDAY_API_TOKEN: 'synthetic', __FRIDAY_MAIL_DEADLINE_MS: 150, fridayStage: stage,
    addEventListener: noop, removeEventListener: noop, matchMedia: () => ({ matches: false, addEventListener: noop }) };
  const document = { visibilityState: 'visible', getElementById: () => null, createElement: () => ({ style: {}, appendChild: noop, setAttribute: noop }),
    head: { appendChild: noop }, body: { appendChild: noop }, addEventListener: noop, removeEventListener: noop, querySelector: () => null, querySelectorAll: () => [] };
  const context = vm.createContext({ React, console, URL, setTimeout, clearTimeout, setInterval, clearInterval, AbortController,
    window: win, document, localStorage: { getItem: () => null, setItem: noop }, navigator: {}, fetch: fetcher,
    ResizeObserver: undefined, Set, Map });
  vm.runInContext(source, context);
  assert.equal(typeof win.FridayMailPanel, 'function', 'the panel registered itself');
  return {
    render() {
      let passes = 0;
      do {
        if (++passes > 30) throw new Error('effects did not settle');
        changed = false; cursor = 0;
        tree = win.FridayMailPanel();
        while (pending.length) pending.shift()();
      } while (changed);
      return tree;
    },
    unmount() { slots.forEach(s => s && s.cleanup && s.cleanup()); }
  };
}

const MAIL = { status: 'ok', total: 1, source: 'merged', errors: [], messages: [{
  id: 'm1', thread_id: 't1', account_id: 'a1', sender: 'Sam Example', sender_email: 'sam@example.test', subject: 'Synthetic hello',
  snippet: 'synthetic', unread: true, lane: 'all', archived: false, labels: ['INBOX'], timestamp: new Date().toISOString() }] };

test('a messages read that never answers becomes an error with Retry, not an empty list', async () => {
  const app = harness((url, opts) => {
    if (url.startsWith('/api/messages?')) return new Promise((_, reject) => { if (opts && opts.signal) opts.signal.addEventListener('abort', () => reject(new Error('aborted'))); });
    return Promise.resolve(response({ accounts: [], status: 'ok' }));
  });
  let tree = app.render();
  assert.match(text(tree), /Reading your mail/);
  await wait(400); tree = app.render();
  const banner = nodes(tree).find(n => n.props && n.props['data-testid'] === 'fm-load-failed');
  assert.ok(banner, 'the error banner is shown');
  assert.match(text(banner), /did not answer/);
  assert.ok(nodes(banner).some(n => n.type === 'button' && text(n) === 'Retry'), 'with a Retry');
  assert.doesNotMatch(text(tree), /Reading your mail|0 conversations/);
  app.unmount();
});

test('a refresh that does not answer leaves the earlier list on screen with a note', async () => {
  let calls = 0;
  const app = harness((url, opts) => {
    if (url.startsWith('/api/messages?')) {
      if (++calls === 1) return Promise.resolve(response(MAIL));
      return new Promise((_, reject) => { if (opts && opts.signal) opts.signal.addEventListener('abort', () => reject(new Error('aborted'))); });
    }
    return Promise.resolve(response({ accounts: [], status: 'ok' }));
  });
  app.render(); await wait(30);
  let tree = app.render();
  assert.match(text(tree), /Synthetic hello/);
  nodes(tree).find(n => n.type === 'button' && n.props['aria-label'] === 'Refresh').props.onClick();
  await wait(400); tree = app.render();
  assert.match(text(tree), /Synthetic hello/, 'the earlier list is kept');
  assert.ok(nodes(tree).some(n => n.props && n.props['data-testid'] === 'fm-refresh-failed'), 'with a could-not-refresh note');
  assert.equal(nodes(tree).some(n => n.props && n.props['data-testid'] === 'fm-load-failed'), false);
  app.unmount();
});
