const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');

for (const file of ['index.html', 'ui_parts/app.html']) {
  const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');
  test(`${file}: privacy control uses the effective consent and dedicated save`, async () => {
    const start = source.indexOf('function CloudConsentRow(');
    assert.ok(start >= 0, 'privacy control exists in both UI sources');
    const end = source.indexOf('\n}\n', start) + 3;
    const requests = [];
    const updates = [];
    const states = [null, false, ''];
    let slot = 0;
    const createElement = (type, props, ...children) => ({type, props:props || {}, children});
    const context = vm.createContext({
      React:{createElement}, E:createElement, Date,
      useState:initial => [states[slot++] ?? initial, value => updates.push(value)],
      apiFetch:(url, opts) => {
        requests.push({url, opts});
        return Promise.resolve({json:async () => ({status:'ok', capability:{capable:true}})});
      },
    });
    vm.runInContext(source.slice(start, end), context);
    const render = () => {
      slot = 0;
      return context.CloudConsentRow({consent:{answered:true, choice:'cloud_guarded'}, onChanged:() => updates.push('reload')});
    };
    const nodes = tree => [tree, ...tree.children.flat(Infinity).filter(x => x && typeof x === 'object').flatMap(nodes)];
    const first = nodes(render());
    assert.match(JSON.stringify(first), /cloud, safeguards on/);
    first.find(n => n.type === 'button' && n.children.includes('Re-answer')).props.onClick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests[0].url, '/api/privacy/cloud-consent?assess=1');
    states[0] = {capability:{capable:true}};
    nodes(render()).find(n => n.type === 'button' && n.children.includes('Allow unrestricted cloud')).props.onClick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests[1].url, '/api/privacy/cloud-consent');
    assert.equal(requests[1].opts.method, 'POST');
    assert.deepEqual(JSON.parse(requests[1].opts.body), {choice:'cloud_unrestricted'});
    assert.ok(updates.includes('reload'));
    assert.ok(source.includes('function PrivacyCloudConsent('));
    assert.doesNotMatch(source, /label="Unrestricted Cloud"[^\n]*unrestricted_cloud/);
  });
}
