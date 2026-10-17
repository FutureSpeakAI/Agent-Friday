// Privacy & Data > Locks, rendered from both UI sources' served copy (index.html)
// with a minimal React stand-in: the honest limit is always on screen, a line
// that can open a Windows page offers that button and no other line does, and
// a record that was changed is named.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

function slice(source, name) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name + ' exists');
  return source.slice(start, source.indexOf('\n}\n', start) + 3);
}

const VIEW = {
  honest_limit: 'Your files are locked on disk where Friday has a lock for them. While Friday is running, anything you run on this PC under your own Windows account could read what Friday can read.',
  lines: [
    { key: 'lock', title: 'Your lock', state: 'off', words: 'No passphrase is set.' },
    { key: 'disk', title: 'Your disk', state: 'unknown', words: "I couldn't tell.", open_page: 'device_encryption' },
    { key: 'records', title: 'My records', state: 'changed', words: 'One record was changed after it was written: Setup record (line 1 was changed after it was written).',
      checks: [{ name: 'Setup record', ok: false, why: 'line 1 was changed after it was written' }] },
    { key: 'held', title: 'Held from the cloud', state: 'held', words: '1 mail archive or statement I opened is held.',
      held_files: ['D:\\Exports\\All mail.mbox'], can_reset: true },
  ],
};

test('the Locks section shows every line, one Windows button, and the honest limit', () => {
  const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/\r\n/g, '\n');
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) });
  let slot = 0;
  const slots = [VIEW, ''];
  const ctx = vm.createContext({
    React: { createElement }, useState: init => { const i = slot++; return [i in slots ? slots[i] : init, () => {}]; },
    useEffect: () => {}, useCallback: fn => fn, apiFetch: () => Promise.resolve({ json: async () => ({}) }),
    StSection: ({ title, desc, children }) => createElement('section', { title, desc }, children),
  });
  vm.runInContext(slice(source, 'SettingsLocks'), ctx);
  const expand = n => (!n || typeof n !== 'object') ? n
    : typeof n.type === 'function' ? expand(n.type({ ...n.props, children: n.children }))
    : { ...n, children: (n.children || []).map(expand) };
  const tree = expand(ctx.SettingsLocks());
  const all = (n, f, o = []) => { if (n && typeof n === 'object') { if (f(n)) o.push(n); (n.children || []).forEach(c => all(c, f, o)); } return o; };
  const text = all(tree, () => true).flatMap(n => (n.children || []).filter(c => typeof c === 'string')).join(' ');
  assert.equal(tree.props.title, 'Locks');
  assert.ok(text.includes('The honest limit.') && text.includes('under your own Windows account could read'));
  const buttons = all(tree, n => n.type === 'button');
  const label = b => (b.children || []).filter(c => typeof c === 'string').join('');
  assert.equal(buttons.filter(b => label(b) === "Open Windows' settings").length, 1, 'only the disk line opens a Windows page');
  const ask = buttons.filter(b => label(b) === 'Ask me about this file');
  assert.equal(ask.length, 1);
  assert.equal(ask[0].props['aria-label'], 'Ask me about D:\\Exports\\All mail.mbox', 'each held file is named to a screen reader');
  assert.equal(buttons.filter(b => label(b) === 'Start the list fresh\u2026').length, 1);
  assert.equal(all(tree, n => n.props && n.props.role === 'status').length, 1, 'the status line is always there');
  assert.ok(text.includes("Setup record: changed (line 1 was changed after it was written)"));
  for (const l of VIEW.lines) assert.ok(text.includes(l.title), l.title);
});

test('the never-send list is shown by name, each entry with its own Remove button', () => {
  const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/\r\n/g, '\n');
  const view = { honest_limit: 'x', lines: [
    { key: 'where', title: 'Where I think', state: 'chosen', words: 'Your never-send list has 3 items (below).',
      never_send: [{ entry: '[card numbers]', label: 'Card numbers', kind: 'category' },
                   { entry: '[bank and account numbers]', label: 'Bank and account numbers', kind: 'category' },
                   { entry: 'Riverside Clinic', label: 'Riverside Clinic', kind: 'words' }] }] };
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) });
  let slot = 0;
  const slots = [view, ''];
  const posted = [];
  const ctx = vm.createContext({
    React: { createElement }, useState: init => { const i = slot++; return [i in slots ? slots[i] : init, () => {}]; },
    useEffect: () => {}, useCallback: fn => fn,
    apiFetch: (url, opts) => { posted.push([url, opts && opts.body]); return Promise.resolve({ json: async () => ({ ok: true }) }); },
    StSection: ({ title, desc, children }) => createElement('section', { title, desc }, children),
  });
  vm.runInContext(slice(source, 'SettingsLocks'), ctx);
  const tree = ctx.SettingsLocks();
  const all = (n, f, o = []) => { if (n && typeof n === 'object') { if (f(n)) o.push(n); (n.children || []).forEach(c => all(c, f, o)); } return o; };
  const list = all(tree, n => n.props && n.props['aria-label'] === 'Your never-send list');
  assert.equal(list.length, 1, 'the never-send list is on the page');
  const label = b => (b.children || []).filter(c => typeof c === 'string').join('');
  const removes = all(tree, n => n.type === 'button' && label(n) === 'Remove');
  assert.deepEqual(removes.map(b => b.props['aria-label']), [
    'Remove Card numbers from the never-send list',
    'Remove Bank and account numbers from the never-send list',
    'Remove Riverside Clinic from the never-send list']);
  removes[0].props.onClick();
  assert.equal(posted.at(-1)[0], '/api/locks/never-send/remove');
  assert.deepEqual(JSON.parse(posted.at(-1)[1]), { entry: '[card numbers]' });
});
