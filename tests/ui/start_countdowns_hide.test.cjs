const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const PAGES = ['index.html', 'ui_parts/app.html'];
const FUTURE = 4102444800;      // 2100-01-01
const PAST = 946684800;         // 2000-01-01
const blocks = [];

function hideBlock(file) {
  const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');
  const start = source.indexOf('/* fridayCountdownHide:begin */');
  const end = source.indexOf('/* fridayCountdownHide:end */', start);
  assert.ok(start >= 0 && end > start, file + ': the hide rules are missing');
  return source.slice(start, end);
}

function rules(file) {
  const context = vm.createContext({Date, Object});
  vm.runInContext(hideBlock(file) + ';this.out={fridayCountdownPasses,fridayCountdownHidden,fridayCountdownsHiding}', context);
  return context.out;
}

for (const file of PAGES) {
  blocks.push(hideBlock(file));

  test(`${file}: a countdown passes at its time, else at the end of its day`, () => {
    const {fridayCountdownPasses} = rules(file);
    assert.equal(fridayCountdownPasses({id: 'a', at: FUTURE}), FUTURE);
    const endOfDay = new Date('2100-06-15T23:59:59').getTime() / 1000;
    assert.equal(fridayCountdownPasses({id: 'b', date: '2100-06-15'}), endOfDay);
    const soon = fridayCountdownPasses({id: 'c'});
    assert.ok(soon > Date.now() / 1000, 'an item with no time still gets a future expiry');
  });

  test(`${file}: a hidden countdown stays hidden until it passes`, () => {
    const {fridayCountdownHidden} = rules(file);
    const hidden = {'todo:t1': FUTURE, 'wiki:p#0': PAST};
    assert.equal(fridayCountdownHidden({id: 'todo:t1'}, hidden, Date.now()), true);
    assert.equal(fridayCountdownHidden({id: 'wiki:p#0'}, hidden, Date.now()), false);
    assert.equal(fridayCountdownHidden({id: 'todo:other'}, hidden, Date.now()), false);
    assert.equal(fridayCountdownHidden({id: 'todo:t1'}, undefined, Date.now()), false);
  });

  test(`${file}: hiding one keeps the others still in force and drops those that have passed`, () => {
    const {fridayCountdownsHiding} = rules(file);
    const before = {'todo:old': PAST, 'todo:keep': FUTURE};
    const after = fridayCountdownsHiding(before, {id: 'todo:new', at: FUTURE + 1}, Date.now());
    assert.equal(JSON.stringify(after), JSON.stringify({'todo:keep': FUTURE, 'todo:new': FUTURE + 1}));
    assert.deepEqual(before, {'todo:old': PAST, 'todo:keep': FUTURE}, 'the saved map is not changed in place');
  });
}

test('served and mirror hide rules stay identical', () => {
  assert.equal(blocks.length, 2);
  assert.equal(blocks[0], blocks[1]);
});

// The served page is compiled, so its component can run here; the JSX mirror is held to the same words below.
function render(items, hidden, onHide) {
  const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/\r\n/g, '\n');
  const start = source.indexOf('function FridayCountdowns(');
  const end = source.indexOf('// The wordmark, AGENT FRIDAY', start);
  assert.ok(start >= 0 && end > start);
  const context = vm.createContext({
    React: {createElement: (type, props, ...children) => ({type, props: props || {}, children}), Fragment: 'Fragment'},
    useState: init => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    fridayCountdownWhen: () => 'in 3 days',
    Date, Object,
  });
  vm.runInContext(hideBlock('index.html') + source.slice(start, end), context);
  return context.FridayCountdowns({items, hidden, onHide});
}

function find(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (pred(node)) out.push(node);
  for (const child of (node.children || []).flat(Infinity)) find(child, pred, out);
  return out;
}

const ITEMS = [{id: 'todo:t1', label: 'Send the grant report', emoji: '', why: 'a deadline you set', days: 3},
               {id: 'wiki:people/dana-reyes.md#0', label: "Dana's birthday", why: 'from your wiki', days: 12}];

test('each countdown has a named hide control and a hidden one is not drawn', () => {
  const tree = render(ITEMS, {'todo:t1': FUTURE}, () => {});
  const text = JSON.stringify(tree);
  assert.ok(!text.includes('Send the grant report') && text.includes("Dana's birthday"));
  const buttons = find(tree, n => n.type === 'button' && n.props.className === 'cd-hide');
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].props['aria-label'], "Hide Dana's birthday");
});

test('pressing hide hands the countdown to the saver', () => {
  const seen = [];
  const tree = render(ITEMS, {}, c => seen.push(c.id));
  find(tree, n => n.type === 'button' && n.props.className === 'cd-hide').forEach(b => b.props.onClick());
  assert.deepEqual(seen, ['todo:t1', 'wiki:people/dana-reyes.md#0']);
});

test('with no saver or no id there is no hide control', () => {
  assert.equal(find(render(ITEMS, {}, undefined), n => n.props && n.props.className === 'cd-hide').length, 0);
  assert.equal(find(render([{label: 'No id', days: 1}], {}, () => {}), n => n.props && n.props.className === 'cd-hide').length, 0);
});

test('the jsx mirror carries the same component words', () => {
  const mirror = fs.readFileSync(path.join(root, 'ui_parts/app.html'), 'utf8');
  for (const part of ['function FridayCountdowns({items,hidden,onHide}){', '!fridayCountdownHidden(c,hidden,now)',
                      'className="cd-hide"', "aria-label={'Hide '+(c.label||c.name)}", 'onClick={()=>onHide(c)}',
                      "<FridayCountdowns items={countdowns} hidden={agentSettings.dismissed_countdowns} onHide={hideCountdown}/>",
                      "left:'var(--fr-chat-dock-left,0px)',right:'var(--fr-chat-dock,0px)'",
                      'saveAgentSettings({dismissed_countdowns: fridayCountdownsHiding(agentSettings.dismissed_countdowns, c, Date.now())})']) {
    assert.ok(mirror.includes(part), 'missing in ui_parts/app.html: ' + part);
  }
});

test('the cluster sits in the space the chat tray leaves, in both pages', () => {
  const mirror = fs.readFileSync(path.join(root, 'ui_parts/app.html'), 'utf8');
  const served = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.ok(mirror.includes("left:'var(--fr-chat-dock-left,0px)',right:'var(--fr-chat-dock,0px)'"));
  assert.ok(served.includes("left: 'var(--fr-chat-dock-left,0px)'") && served.includes("right: 'var(--fr-chat-dock,0px)'"));
});
