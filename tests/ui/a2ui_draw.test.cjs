// The desktop's A2UI draw step (static/friday_a2ui.js) over the shared conformance suite
// (tests/fixtures/a2ui/conformance_v1.json, contract draft 3 section 5). The PC binds and checks;
// the browser only draws. Held here, for EVERY tree in the suite:
//  * only the allowed elements appear (no <a>, <input>, <form>, <img>, <iframe>, <script>, <textarea>);
//  * every bound string is escaped (a planted "<script>" draws as text);
//  * a record_table draws its columns and rows; a status_block draws its title;
//  * the shell look: density and accent land as data attributes, the summary as plain text;
//  * a refused view draws only the standard line.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

function load() {
  const sandbox = { window: {} };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(root, 'static', 'friday_a2ui.js'), 'utf8'), sandbox);
  return sandbox.window.FridayA2ui;
}

const suite = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'a2ui', 'conformance_v1.json'), 'utf8'));
const BANNED = ['<a ', '<a>', '<input', '<form', '<img', '<iframe', '<script', '<textarea', '<select', 'href=', 'src=', 'on'];

test('every tree in the shared suite draws with only the allowed elements', () => {
  const A = load();
  const ok = suite.cases.filter((c) => c.expect.ok && c.expect.tree);
  assert.ok(ok.length >= 4, 'the suite has render and shell cases');
  for (const c of ok) {
    const html = A.toHtml(c.expect.tree, c.expect.look || null);
    for (const b of BANNED.slice(0, -1)) assert.ok(!html.includes(b), c.name + ': ' + b);
    assert.ok(!/\son[a-z]+=/i.test(html), c.name + ': an event handler attribute');
  }
});

test('the Knowledge tree draws its sections table and his customization', () => {
  const A = load();
  const c = suite.cases.find((x) => x.kind === 'shell' && x.name.includes('recent hidden'));
  const html = A.toHtml(c.expect.tree, c.expect.look);
  assert.match(html, /<h2[^>]*>Knowledge<\/h2>/);
  assert.match(html, /<th>name<\/th><th>count<\/th>/);
  assert.match(html, /<td>entities<\/td><td>2<\/td>/);
  assert.ok(!html.includes('Recently changed'), 'the hidden part is not drawn');
  assert.match(html, /data-density="compact"/);
  assert.match(html, /data-accent="#3366ff"/);
  assert.match(html, /data-a2ui-summary[^>]*>My notes, mostly</);
});

test('bound text is escaped, never markup', () => {
  const A = load();
  const tree = [{ type: 'column', id: 'root', children: [
    { type: 'text_block', id: 't', style: 'body', text: '<script>alert(1)</script>' },
    { type: 'record_table', id: 'r', columns: ['title'], rows: [{ title: '<img src=x onerror=alert(1)>' }] },
  ] }];
  const html = A.toHtml(tree, null);
  assert.ok(!html.includes('<script>') && !html.includes('<img'));
  assert.ok(html.includes('&lt;script&gt;') && html.includes('&lt;img src=x onerror=alert(1)&gt;'));
});

test('an accent that is not #rrggbb is dropped, a refused view draws the standard line', () => {
  const A = load();
  const html = A.toHtml([{ type: 'text_block', id: 'root', style: 'body', text: 'x' }],
    { accent: 'red;background:url(x)', density: 'comfortable', summary: null });
  assert.ok(!html.includes('data-accent'));
  assert.match(A.refusedHtml(), /could not be shown/);
});
