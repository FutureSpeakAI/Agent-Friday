// Privacy & Data > Privacy map, rendered from the served copy (index.html) with
// a minimal React stand-in: every store shows where it lives, how long it is
// kept and its controls; every destination shows its counts and a receipts
// button that asks for that destination's rows; the channels the log does not
// see yet, the off-the-record notice and the honest limit are always shown.
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
  stores: [
    { key: 'conversations', name: 'Your conversations', holds: 'Every chat.', where: ['conversations'],
      encrypted: 'no', encrypted_words: 'Readable on this disk (not encrypted yet).',
      retention_words: 'Kept until you delete it.', size_words: '2.0 KB in 1 file.',
      controls: [{ kind: 'export', label: 'Export all my data', words: 'A zip.', href: '/api/data/export' },
                 { kind: 'delete', label: 'Erase everything', words: 'Type ERASE.', nav: { workspace: 'settings', tab: 'privacy', section: 'Your data' } }] },
  ],
  left: { readable: true, destinations: [
    { to: 'anthropic', label: 'Anthropic (Claude)', sent: 2, sent_with_parts_withheld: 1, stopped: 1,
      files_or_images: 0, background: 1, first: 1760000000, last: 1760000100, fields: ['messages'],
      receipts: '/api/privacy/map/left?to=anthropic' }],
    not_recorded: [{ key: 'mcp', what: 'Calls to tool servers you added (MCP)', record: 'Not recorded as a send yet.' }] },
  mail: { sent: 1, failed: 0, recipients: [{ to: 'a@example.com', count: 1, last: 1760000000 }] },
  grants: { actions: [], files: 2, nav: { workspace: 'settings', tab: 'privacy', section: 'File access' } },
  services: { readable: true, connected: [{ label: 'Synthetic service', group: 'tools', may: ['Read things'] }], remote: [] },
  vault_access: { allowed_7_days: 3, refused_7_days: 1, last_allowed: 1760000000 },
  off_record: { on: false, words: 'Off the record, Friday saves nothing about the conversation. Services you connected (mail, calendar, publishing) can still keep their own records of anything they are asked to do.' },
  export: { kind: 'export', label: 'Export all my data', words: 'A zip.', href: '/api/data/export' },
  erase: { kind: 'delete', label: 'Erase everything', words: 'Type ERASE.', nav: { workspace: 'settings', tab: 'privacy', section: 'Your data' } },
  honest_limit: 'This map lists what Friday itself keeps and sends.',
};

function render(fetched, opened, navs) {
  const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/\r\n/g, '\n');
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) });
  let slot = 0;
  const slots = [VIEW, '', null];
  const ctx = vm.createContext({
    React: { createElement }, useState: init => { const i = slot++; return [i in slots ? slots[i] : init, () => {}]; },
    useEffect: () => {}, useCallback: fn => fn, encodeURIComponent,
    apiFetch: url => { fetched.push(url); return Promise.resolve({ json: async () => ({ rows: [] }) }); },
    window: { open: url => opened.push(url), fridayOpenWorkspace: t => navs.push(t) },
    Date,
    StSection: ({ title, desc, children }) => createElement('section', { title, desc }, children),
  });
  vm.runInContext(slice(source, 'SettingsPrivacyMap'), ctx);
  const expand = n => (!n || typeof n !== 'object') ? n
    : typeof n.type === 'function' ? expand(n.type({ ...n.props, children: n.children }))
    : { ...n, children: (n.children || []).map(expand) };
  return expand(ctx.SettingsPrivacyMap());
}

const all = (n, f, o = []) => { if (n && typeof n === 'object') { if (f(n)) o.push(n); (n.children || []).forEach(c => all(c, f, o)); } return o; };
const label = b => (b.children || []).filter(c => typeof c === 'string').join('');

test('the privacy map shows each store, each destination, and the honest limit', () => {
  const fetched = [], opened = [], navs = [];
  const tree = render(fetched, opened, navs);
  const text = all(tree, () => true).flatMap(n => (n.children || []).filter(c => typeof c === 'string')).join(' ');
  assert.equal(tree.props.title, 'Privacy map');
  for (const s of ['Your conversations', 'Where: conversations', 'Kept until you delete it.', 'Readable on this disk',
                   'Anthropic (Claude)', '2 items sent (1 with parts withheld), 1 stopped before leaving', '1 from background work',
                   'Calls to tool servers you added (MCP)', 'a@example.com: 1', 'Synthetic service: Read things',
                   'In the last 7 days, private data was used by a model on this PC or allowed to the cloud 3 times, and refused 1 time.', 'When you go off the record.', 'can still keep their own records',
                   'The honest limit.', 'This map lists what Friday itself keeps and sends.']) {
    assert.ok(text.includes(s), 'missing: ' + s);
  }
  assert.equal(all(tree, n => n.props && n.props.role === 'status').length, 1);
});

test('controls open the existing export or the place that deletes, and receipts ask for that destination', () => {
  const fetched = [], opened = [], navs = [];
  const tree = render(fetched, opened, navs);
  const buttons = all(tree, n => n.type === 'button');
  buttons.find(b => label(b) === 'Export all my data').props.onClick();
  assert.deepEqual(opened, ['/api/data/export']);
  buttons.find(b => label(b) === 'Erase everything').props.onClick();
  assert.deepEqual(navs[0], { workspace: 'settings', tab: 'privacy', section: 'Your data' });
  const show = buttons.filter(b => label(b) === 'Show the receipts');
  assert.equal(show.length, 1);
  assert.equal(show[0].props['aria-label'], 'Show the receipts for Anthropic (Claude)');
  show[0].props.onClick();
  assert.equal(fetched.at(-1), '/api/privacy/map/left?to=anthropic');
});
