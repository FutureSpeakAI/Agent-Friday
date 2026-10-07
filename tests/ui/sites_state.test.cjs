'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({window: {}, React: {createElement() {}}, URL, Date});
vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../../static/friday_sites.js'), 'utf8'), context);
const state = context.window.FridaySitesState;

test('result links reject credentials and active or ambiguous URLs', () => {
  for (const value of ['javascript:alert(1)', '//example.com', 'https://user:secret@example.com/', '/\\example.com', 'https://example.com/\nnext', 'http://example.com']) {
    assert.equal(state.safeUrl(value), null, value);
  }
  assert.equal(state.safeUrl('https://example.com/'), 'https://example.com/');
  assert.equal(state.safeUrl('https://example.com/', true), null, 'previews must remain on the local server');
});

test('preview addresses admit only exact trusted wrapper paths', () => {
  const accepted = '/api/sites/preview-frame/p' + 'a'.repeat(48);
  assert.equal(state.previewUrl(accepted), accepted);
  for (const value of ['/api/sites/preview/site/build/index.html', 'http://localhost:3199/',
    'http://p' + 'a'.repeat(48) + '.localhost:3199/', '//localhost' + accepted, 'https://example.com' + accepted,
    accepted + '/index.html', accepted + '?token=1', accepted + '#fragment', accepted.slice(0, -1),
    accepted + '\n', null, {}]) assert.equal(state.previewUrl(value), null, String(value));
});

test('imported renewal dates keep their meaning and do not become verified expiration', () => {
  const now = Date.parse('2027-01-01T12:00:00Z');
  const imported = {next_date_kind: 'renews', next_date: '2027-01-20', source: 'user_import'};
  const renews = state.domainDate({verified: false, expire_date: '2027-01-02', imported}, now);
  assert.equal(renews.kind, 'renews'); assert.equal(renews.verified, false); assert.equal(renews.urgent, false);
  const expires = state.domainDate({verified: false, imported: {...imported, next_date_kind: 'expires'}}, now);
  assert.equal(expires.urgent, true); assert.equal(expires.days, 19); assert.equal(expires.source, 'user_import');
  const verified = state.domainDate({verified: true, expire_date: '2027-02-15T00:00:00Z', imported}, now);
  assert.equal(verified.kind, 'expires'); assert.equal(verified.verified, true); assert.equal(verified.urgent, false);
  assert.equal(state.domainDate({}, now).days, null);
});

test('inventory import rejects ambiguous rows and preserves explicit renews/expires', () => {
  const rows = state.parseImport('example.com, Renews, 2027-01-20\nexample.org\tExpires\t2027-02-01');
  assert.equal(rows[0].next_date_kind, 'renews'); assert.equal(rows[1].next_date_kind, 'expires');
  for (const text of ['', 'example.com, January 20', 'example.com, paid, 2027-01-20', 'example.com, Expires, 01/20/2027']) assert.throws(() => state.parseImport(text));
});

test('publication labels preserve the boundary of the available evidence', () => {
  assert.equal(state.deploymentLabel({status: 'provider_accepted'}), 'Host accepted the upload');
  assert.equal(state.deploymentLabel({status: 'verified_live'}), 'Verified live');
  assert.equal(state.deploymentLabel({status: 'previously_verified'}), 'Previously verified');
  assert.equal(state.deploymentLabel({status: 'certificate_pending'}), 'Certificate not confirmed');
  assert.equal(state.deploymentLabel({status: 'new_unrecognized_state'}), 'Status unavailable');
});
