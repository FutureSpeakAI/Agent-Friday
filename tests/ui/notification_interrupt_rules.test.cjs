const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const blocks = [];

const RULES = [
  {id: 'scheduled_runs', words: 'A scheduled job that finished successfully.', on: true},
  {id: 'daily_briefing', words: 'The daily briefing that was delivered.', on: false},
];

function render(block, rules) {
  const posted = [];
  const context = vm.createContext({
    React: {
      createElement: (type, props, ...children) => ({type, props: props || {}, children}),
      // The first useState is `rules`; seed it.
      useState: initial => [rules !== undefined ? rules : initial, () => {}],
      useEffect: () => {},
    },
    StSection: 'StSection',
    apiFetch: (url, opts) => { posted.push({url, opts}); return {then: () => ({then: () => ({catch: () => {}})})}; },
  });
  vm.runInContext(block, context);
  return {tree: context.SettingsNotificationInterrupts(), posted};
}

function find(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (pred(node)) out.push(node);
  for (const c of (node.children || []).flat(Infinity)) find(c, pred, out);
  return out;
}

for (const file of ['index.html', 'ui_parts/app.html']) {
  const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');
  const start = source.indexOf('function SettingsNotificationInterrupts(');
  const end = source.indexOf('function SettingsTabBrowser(', start);
  assert.ok(start >= 0 && end > start, file + ': the panel is missing');
  const block = source.slice(start, end);
  blocks.push(block);

  test(`${file}: each rule is a labelled tick box in plain words`, () => {
    const {tree} = render(block, RULES);
    const text = JSON.stringify(tree);
    assert.match(text, /What may interrupt you/);
    assert.match(text, /A scheduled job that finished successfully\./);
    const boxes = find(tree, n => n.type === 'input' && n.props.type === 'checkbox');
    assert.deepEqual(boxes.map(b => b.props.checked), [true, false]);
    const labels = find(tree, n => n.type === 'label');
    assert.equal(labels.length, 2, 'each box sits inside a label that names it');
  });

  test(`${file}: ticking a box posts the rule and the opposite state`, () => {
    const {tree, posted} = render(block, RULES);
    const boxes = find(tree, n => n.type === 'input' && n.props.type === 'checkbox');
    boxes[1].props.onChange();
    boxes[0].props.onChange();
    const posts = posted.filter(p => p.opts && p.opts.method === 'POST');
    assert.deepEqual(posts.map(p => p.url), ['/api/notifications/interrupt-rules', '/api/notifications/interrupt-rules']);
    assert.deepEqual(JSON.parse(posts[0].opts.body), {id: 'daily_briefing', on: true});
    assert.deepEqual(JSON.parse(posts[1].opts.body), {id: 'scheduled_runs', on: false});
  });

  test(`${file}: nothing is drawn before the rules arrive`, () => {
    const {tree} = render(block, null);
    assert.equal(find(tree, n => n.type === 'input').length, 0);
  });
}

test('served and mirror panels stay identical', () => {
  assert.equal(blocks.length, 2);
  assert.equal(blocks[0], blocks[1]);
});
