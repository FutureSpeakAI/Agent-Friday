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
  ],
};

test('the Locks section shows every line, one Windows button, and the honest limit', () => {
  const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/\r\n/g, '\n');
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) });
  let slot = 0;
  const slots = [VIEW, ''];
  const ctx = vm.createContext({
    React: { createElement }, useState: init => { const i = slot++; return [i in slots ? slots[i] : init, () => {}]; },
    useEffect: () => {}, apiFetch: () => Promise.resolve({ json: async () => ({}) }),
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
  assert.equal(buttons.length, 1, 'only the disk line opens a Windows page');
  assert.ok(text.includes("Setup record: changed (line 1 was changed after it was written)"));
  for (const l of VIEW.lines) assert.ok(text.includes(l.title), l.title);
});
