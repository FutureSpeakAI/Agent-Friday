const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const blocks = [];

function render(block, items, st) {
  const posted = [];
  let n = 0;
  const context = vm.createContext({
    React:{createElement:(type, props, ...children) => ({type, props:props || {}, children})},
    // The first useState is `items`; seed it with a queue.
    useState:initial => [n++ === 0 ? items : initial, () => {}],
    useCallback:fn => fn, useEffect:() => {},
    apiFetch:(url, opts) => { posted.push({url, opts}); return {then:() => ({then:() => ({catch:() => ({finally:() => {}})})})}; },
  });
  vm.runInContext(block, context);
  return {tree: context.LayaLabelQueue({st, reload:() => {}}), posted};
}

function find(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (pred(node)) out.push(node);
  for (const c of (node.children || []).flat(Infinity)) find(c, pred, out);
  return out;
}

for (const file of ['index.html', 'ui_parts/app.html']) {
  test(`${file}: the owner answers yes or no, and the scorecard is shown`, () => {
    const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');
    const start = source.indexOf('function LayaLabelQueue(');
    const end = source.indexOf('// End Laya label queue', start);
    assert.ok(start >= 0 && end > start);
    const block = source.slice(start, end); blocks.push(block);
    assert.match(source, /LayaLabelQueue[ ,]/);
    const items = [{subject:'tool:mcp_github_search_users', keyword_says_outside:false,
                    laya_says_outside:true, disagree:true, probe:true}];
    const st = {evidence:{keyword:{right:0, wrong:1}, laya:{right:1, wrong:0}, probes:{}}};
    const {tree, posted} = render(block, items, st);
    const text = JSON.stringify(tree);
    assert.match(text, /Does this reach outside your machine\?/);
    assert.match(text, /mcp_github_search_users/);
    assert.match(text, /Laya 1 of 1/);
    const yes = find(tree, n => n.type === 'button' && n.children.includes('Yes'))[0];
    yes.props.onClick();
    const post = posted.find(p => p.url === '/api/decisions/labels');
    assert.deepEqual(JSON.parse(post.opts.body),
                     {subject:'tool:mcp_github_search_users', reaches_outside:true});
  });
}
test('served and mirror label queues stay identical', () => {
  assert.equal(blocks.length, 2);
  assert.equal(blocks[0], blocks[1]);
});
