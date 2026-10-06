/* Bounded, browser-free lifecycle and trust-boundary checks for the proof bridge.
 * The async tracker and camera owner follow the original scene's ownership:
 * loading can finish after Off, and acquisition consults wanted on completion.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../friday-remake/scene-bridge.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

function harness(bridge = source, options = {}) {
  const sent = [], listeners = new Map(), docListeners = new Map(), intervals = new Map();
  let intervalId = 0;
  const tracker = options.tracker || { promise: Promise.resolve() };
  const styleValues = {
    '--holo-par-x': '5.4px', '--holo-par-y': '-2.4px', '--holo-tilt-x': '1deg',
    '--holo-tilt-y': '2.1deg', '--holo-depth': '1.06', '--holo-rim': '.2',
  };
  const parent = { postMessage(data, origin) { sent.push({ data: JSON.parse(JSON.stringify(data)), origin }); } };
  const sandbox = {
    parent, console, trackerLoadPromise: tracker.promise, sceneContextLost: false,
    currFaceX: .3, currFaceY: -.2, currFaceZ: .1, callHold: false,
    performance: { now: () => 400 }, matchMedia: () => ({ matches: false }),
    setInterval(fn) { intervals.set(++intervalId, fn); return intervalId; },
    clearInterval(id) { intervals.delete(id); },
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
    document: {
      hidden: false,
      createElement: () => ({ textContent: '' }),
      head: { appendChild() {} },
      documentElement: { dataset: {}, style: { getPropertyValue: name => styleValues[name] || '' } },
      addEventListener(type, fn) { if (!docListeners.has(type)) docListeners.set(type, []); docListeners.get(type).push(fn); },
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(`
    var cameraStarts = 0, cameraStops = 0, structureIndex = 0, isHologramMode = false;
    window.FridayCamera = {
      state: { status: 'off', detail: '', wanted: false },
      start() { cameraStarts++; this.state.wanted = true; this.state.status = 'live'; },
      stop() { cameraStops++; this.state.wanted = false; this.state.status = 'off'; },
    };
    async function toggleHologram() {
      isHologramMode = !isHologramMode;
      if (isHologramMode) {
        await window.trackerLoadPromise;
        window.FridayCamera.start();
      } else { window.FridayCamera.stop(); }
    }
    window.fridayVibe = {
      getStructures: () => [{id:'CUBES',name:'GENESIS LATTICE'},{id:'ICOSAHEDRON',name:'DYSON SPHERE'}],
      getStructure: () => ({index: structureIndex, name: structureIndex ? 'DYSON SPHERE' : 'GENESIS LATTICE'}),
      setStructure: index => { structureIndex = index; },
      isHologramOn: () => isHologramMode,
      toggleHologram: () => { toggleHologram(); },
    };
    window.FridayTracking = {
      head: { x: .7, y: .6, z: .5, baseline: null, seen: false, debug: null },
      cfg: {},
      debugHead(x,y,boxW) { this.head.debug = boxW ? {x,y,boxW} : null; },
      apply(config) { Object.assign(this.cfg, config); },
    };
    window.__fridayRenderer = {};
    window.fridayDebugScene = () => ({scene:{}});
  `, sandbox);
  vm.runInContext(bridge, sandbox, { filename: 'scene-bridge.js' });
  const send = (type, data = {}, overrides = {}) => {
    const event = { origin: 'http://127.0.0.1:3193', source: parent, data: {source:'friday-remake',type,...data}, ...overrides };
    for (const fn of listeners.get('message') || []) fn(event);
  };
  return {
    sandbox, sent, send,
    tick: () => { for (const fn of intervals.values()) fn(); },
    hide: hidden => { sandbox.document.hidden = hidden; for (const fn of docListeners.get('visibilitychange') || []) fn(); },
    close: () => { for (const fn of listeners.get('pagehide') || []) fn(); },
    last: type => sent.filter(s => s.data.type === type).at(-1)?.data,
  };
}

async function staleLoadCannotAcquire(bridge) {
  const load = deferred(), h = harness(bridge, {tracker:load});
  h.send('scene:tracking', {enabled:true,userInitiated:true});
  assert.equal(h.sandbox.cameraStarts, 0, 'Loading must not have acquired a camera yet');
  h.send('scene:tracking', {enabled:false});
  load.resolve(); await flush();
  assert.equal(h.sandbox.cameraStarts, 0, 'A tracker finishing after Off must not reach camera acquisition');
  assert.equal(h.sandbox.isHologramMode, false);
  assert.equal(h.sandbox.FridayCamera.state.wanted, false);
  assert.equal(h.last('status').trackingBusy, false);
}

async function run() {
  let passed = 0;
  const check = async (name, body) => { await body(); passed++; process.stdout.write('PASS ' + name + '\n'); };

  await check('boot and ordinary commands never request camera', async () => {
    const h = harness(); h.tick(); h.send('scene:catalog'); h.send('scene:mode',{mode:'immersive'}); h.send('scene:set',{index:1});
    await flush(); assert.equal(h.sandbox.cameraStarts,0); assert.equal(h.last('ready').ready,true);
    assert.ok(h.sent.every(s => s.origin === 'http://127.0.0.1:3193'));
  });
  await check('foreign origin, sibling window, and wrong sender label are ignored', () => {
    const h = harness(); const count = h.sent.length;
    h.send('scene:set',{index:1},{origin:'https://example.invalid'});
    h.send('scene:set',{index:1},{source:{}});
    h.send('scene:set',{index:1,source:'untrusted'});
    h.send('scene:tracking',{enabled:true,userInitiated:true},{origin:'http://127.0.0.1:3194'});
    assert.equal(h.sandbox.structureIndex,0); assert.equal(h.sandbox.cameraStarts,0); assert.equal(h.sent.length,count);
  });
  await check('camera enable requires explicit gesture and visibility', async () => {
    const h = harness(); h.send('scene:tracking',{enabled:true}); await flush();
    assert.match(h.last('error').message,/button/); assert.equal(h.sandbox.cameraStarts,0);
    h.send('scene:visibility',{visible:false});
    h.send('scene:tracking',{enabled:true,userInitiated:true}); await flush();
    assert.match(h.last('error').message,/visible/); assert.equal(h.sandbox.cameraStarts,0);
  });
  await check('On → Off during deferred tracker load never acquires', () => staleLoadCannotAcquire(source));
  await check('hidden revokes pending tracking and prevents stale startup', async () => {
    const load = deferred(), h = harness(source,{tracker:load});
    h.send('scene:tracking',{enabled:true,userInitiated:true}); h.send('scene:visibility',{visible:false});
    load.resolve(); await flush();
    assert.equal(h.sandbox.cameraStarts,0); assert.equal(h.sandbox.isHologramMode,false); assert.equal(h.sandbox.callHold,true);
    h.send('scene:visibility',{visible:true}); await flush();
    assert.equal(h.sandbox.cameraStarts,0); assert.equal(h.sandbox.callHold,false);
  });
  await check('hidden releases an active camera; visible never restarts it', async () => {
    const h = harness(); h.send('scene:tracking',{enabled:true,userInitiated:true}); await flush();
    assert.equal(h.sandbox.cameraStarts,1); assert.equal(h.sandbox.FridayCamera.state.wanted,true);
    h.hide(true); await flush();
    assert.equal(h.sandbox.FridayCamera.state.wanted,false); assert.equal(h.sandbox.isHologramMode,false);
    h.hide(false); await flush(); assert.equal(h.sandbox.cameraStarts,1); assert.equal(h.sandbox.callHold,false);
    h.send('scene:tracking',{enabled:true,userInitiated:true}); await flush(); assert.equal(h.sandbox.cameraStarts,2);
  });
  await check('catalog and valid selection work; malformed selection cannot reach renderer', () => {
    const h = harness(); h.send('scene:catalog',{requestId:'catalog-1'});
    assert.deepEqual(h.last('catalog'), {source:'friday-scene',type:'catalog',requestId:'catalog-1',scenes:[{index:0,id:'CUBES',name:'GENESIS LATTICE'},{index:1,id:'ICOSAHEDRON',name:'DYSON SPHERE'}]});
    h.send('scene:set',{index:1}); assert.equal(h.sandbox.structureIndex,1);
    for (const index of [-1,2,1.5,'0',NaN]) { h.send('scene:set',{index}); assert.equal(h.sandbox.structureIndex,1); assert.match(h.last('error').message,/Unknown/); }
  });
  await check('preview stays labeled and emits exact renderer pose and CSS', () => {
    const h = harness(); h.send('scene:preview-pose',{pose:{x:.2,y:.1,z:.3}}); h.tick();
    const pose = h.last('pose').pose;
    assert.equal(pose.source,'preview'); assert.equal(pose.x,.3); assert.equal(pose.y,-.2); assert.equal(pose.z,.1);
    assert.deepEqual(pose.css,{parX:5.4,parY:-2.4,tiltX:1,tiltY:2.1,depth:1.06,rim:.2});
    assert.equal(h.sandbox.cameraStarts,0);
    h.send('scene:preview-pose',{pose:{x:Infinity,y:0,z:0}}); assert.match(h.last('error').message,/finite/);
    h.send('scene:preview-pose',{pose:null}); h.tick(); assert.equal(h.last('pose').pose.source,'neutral');
  });
  await check('page teardown stops camera and pose stream', async () => {
    const h = harness(); h.send('scene:tracking',{enabled:true,userInitiated:true}); await flush();
    h.tick(); h.close(); const count=h.sent.length; h.tick();
    assert.equal(h.sandbox.FridayCamera.state.wanted,false); assert.equal(h.sent.length,count);
  });
  await check('negative control: removing acquisition gate makes stale-start test fail', async () => {
    const unsafe = source.replace('if (!requestedTracking || !parentVisible || document.hidden || stopped)', 'if (false)');
    assert.notEqual(unsafe,source);
    await assert.rejects(() => staleLoadCannotAcquire(unsafe), /finishing after Off/);
  });
  await check('negative control: removing trust boundary admits foreign commands', () => {
    const unsafe = source.replace("if (event.origin !== PARENT_ORIGIN || event.source !== window.parent) return;", '/* trust check removed for negative control */');
    assert.notEqual(unsafe,source);
    const h=harness(unsafe); h.send('scene:set',{index:1},{origin:'https://example.invalid'});
    assert.throws(() => assert.equal(h.sandbox.structureIndex,0), assert.AssertionError);
  });
  process.stdout.write(`${passed} bridge checks passed; includes two deliberately broken negative controls. No browser or camera was used.\n`);
}
run().catch(error => { process.stderr.write(error.stack+'\n'); process.exitCode=1; });
