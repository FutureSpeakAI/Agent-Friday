/* Offline tracking-save queue regressions. No server, settings store or camera.
 * FRIDAY_BASELINE_REV runs these assertions against a committed implementation. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const relative = 'static/friday_holographic_workspace.js';
let source = fs.readFileSync(path.join(root, relative), 'utf8');
if (process.env.FRIDAY_BASELINE_REV) {
  const result = spawnSync('git', ['-c', 'safe.directory=' + root, 'show', process.env.FRIDAY_BASELINE_REV + ':' + relative],
    {cwd:root, encoding:'utf8', windowsHide:true});
  assert.equal(result.status, 0, result.stderr);source = result.stdout;
}
const implementation = {exports:{}};
vm.runInNewContext(source, {module:implementation, globalThis:{}});

function fixture(outcomes) {
  const live = {parallax_strength:1, depth_strength:1, head_smoothing:.35};
  const saved = {...live}, messages = [], requests = [];
  const host = {
    FridayTracking:{get:()=>({...live}), apply:patch=>Object.assign(live, patch)},
    apiFetch:async(_url, options)=>{
      const patch = JSON.parse(options.body).settings.tracking;
      requests.push(patch);
      const ok = outcomes.shift();assert.equal(typeof ok, 'boolean', 'Every synthetic request has an outcome');
      if (ok) Object.assign(saved, patch);
      return {ok, json:async()=>({status:ok ? 'ok' : 'error'})};
    }
  };
  return {tuner:implementation.exports.trackingTuner(host, message=>messages.push(message)), live, saved, messages, requests};
}

(async()=>{
  const a = fixture([false, true, true, false, true]);
  const settled = await Promise.allSettled([
    a.tuner.save({parallax_strength:.4}), a.tuner.save({depth_strength:.3})
  ]);
  assert.deepEqual(settled.map(result=>result.status), ['rejected', 'fulfilled']);
  assert.equal(a.live.parallax_strength, .4);
  assert.equal(a.saved.parallax_strength, 1);
  assert.equal(a.saved.depth_strength, .3);
  assert.match(a.messages.at(-1), /not saved/, 'A sibling success must not hide an unsaved dial');
  await a.tuner.save({head_smoothing:.75});
  assert.match(a.messages.at(-1), /not saved/, 'Further unrelated saves must retain the failed dial');
  await assert.rejects(a.tuner.save({parallax_strength:.4}), /could not be saved/);
  assert.match(a.messages.at(-1), /not saved/);
  await a.tuner.save({parallax_strength:.4});
  assert.equal(a.messages.at(-1), 'Tracking preferences saved.');
  assert.deepEqual(a.saved, a.live);
  console.log('PASS failed dial remains unsaved across sibling saves until its own successful retry');

  const b = fixture([false, true]);
  await Promise.allSettled([b.tuner.save({parallax_strength:.4}), b.tuner.save({parallax_strength:.6})]);
  assert.equal(b.saved.parallax_strength, .6);
  assert.equal(b.messages.at(-1), 'Tracking preferences saved.');
  console.log('PASS queued successful newer edit clears its own earlier failure');

  const c = fixture([true, false]);
  await Promise.allSettled([c.tuner.save({parallax_strength:.4}), c.tuner.save({parallax_strength:.6})]);
  assert.equal(c.saved.parallax_strength, .4);
  assert.equal(c.live.parallax_strength, .6);
  assert.match(c.messages.at(-1), /not saved/, 'Older success cannot clear a newer failed edit');
  console.log('PASS older same-dial success does not mark a newer failed edit saved');

  const d = fixture([true]);
  d.saved.neutral_face_width = .31; d.live.neutral_face_width = .24;
  await d.tuner.save({neutral_face_width:.24, head_response:.2});
  assert.deepEqual(d.requests, [{head_response:.2}]);
  assert.equal(d.saved.neutral_face_width, .31, 'A full live cfg must preserve legacy account calibration');
  await assert.rejects(d.tuner.save({neutral_face_width:.4}), /Calibrate distance/);
  assert.equal(d.requests.length, 1, 'Distance-only input must use the camera binding control');
  console.log('PASS browser-local distance never overwrites account calibration through the tuner');
})().catch(error=>{console.error(error);process.exitCode=1});
