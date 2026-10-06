/* The width policy must keep every command reachable without overlapping chrome. */
const assert = require('node:assert/strict');
const {packTopbar} = require('../static/friday_responsive_topbar.js');
const fixture = [
  {id:'brand', brand:true, width:176, priority:1000},
  {id:'workspaces', width:125, priority:100},
  {id:'projects', width:78, priority:75},
  {id:'activity', width:72, priority:40},
  {id:'depth', width:84, priority:65},
  {id:'model', width:180, priority:50},
  {id:'approvals', width:160, priority:95},
  {id:'chat', width:34, priority:90},
  {id:'settings', width:34, priority:35},
  {id:'resources', width:360, priority:5},
  {id:'not-mounted', width:0, priority:5}
].map((item, order) => ({...item, order}));

function verify(plan, available, items) {
  const shown = items.filter(item => plan.visible.includes(item.id));
  const used = shown.reduce((sum, item) => sum + item.width, 0) + Math.max(0, shown.length - 1) * 8 + (plan.overflow.length ? 48 : 0);
  assert.ok(used <= available, `Header uses ${used}px of ${available}px`);
  assert.equal(new Set([...plan.visible, ...plan.overflow]).size, items.filter(item => item.width > 0).length, 'Every rendered item has exactly one destination');
  assert.ok(plan.visible.includes('brand'), 'The whole brand stays in the header');
}
for (const width of [300, 370, 580, 680, 748, 1049, 1260, 1580, 1900]) {
  const plan = packTopbar(width, fixture, 40, 8);
  verify(plan, width, fixture);
}
const phone = packTopbar(300, fixture, 40, 8);
assert.ok(phone.overflow.includes('workspaces'), 'Workspace access moves into the pullout when it cannot fit');
assert.ok(phone.overflow.includes('activity'), 'Activity remains reachable on a phone');
assert.ok(packTopbar(370, fixture, 40, 8).visible.includes('workspaces'), 'Workspace access returns when measured room allows it');
assert.equal(packTopbar(1900, fixture, 40, 8).overflow.length, 0, 'No unnecessary overflow trigger on a wide screen');
const longModel = fixture.map(item => item.id === 'model' ? {...item, width:500} : item);
verify(packTopbar(748, longModel, 40, 8), 748, longModel);
assert.throws(() => verify({visible:fixture.filter(item => item.width).map(item => item.id), overflow:[]}, 300, fixture), /Header uses/, 'Negative control: the original unbounded row must fail');
console.log('Responsive topbar: measured packing, reachability, and negative-control checks passed.');
