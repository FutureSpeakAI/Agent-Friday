const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const blocks = [];
for (const file of ['index.html', 'ui_parts/app.html']) {
  test(`${file}: pilot control preserves privacy settings and reports comparison limits`, () => {
    const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');
    const start = source.indexOf('function LayaPilotPanel(');
    const end = source.indexOf('// End Laya chat pilot panel', start);
    assert.ok(start >= 0 && end > start);
    const block = source.slice(start, end); blocks.push(block);
    const saved = [];
    const context = vm.createContext({
      React:{createElement:(type, props, ...children) => ({type, props:props || {}, children})},
      StToggle:'toggle', AbortController,
      useState:initial => [initial, () => {}], useRef:initial => ({current:initial}),
      useCallback:fn => fn, useEffect:() => {},
    });
    vm.runInContext(block, context);
    const tree = context.LayaPilotPanel({s:{laya_pilot_enabled:false},save:delta => saved.push(delta)});
    const toggle = tree.children.find(n => n && n.type === 'toggle');
    toggle.props.onChange();
    assert.deepEqual(JSON.parse(JSON.stringify(saved)), [{laya_pilot_enabled:true}]);
    assert.match(JSON.stringify(tree), /not a measured speedup yet/);
    assert.match(source, /Laya chat pilot/);
  });
}
test('served and mirror pilot controls stay identical', () => {
  assert.equal(blocks.length, 2);
  assert.equal(blocks[0], blocks[1]);
});
