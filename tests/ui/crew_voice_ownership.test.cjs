const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const sourceCache = new Map();

// A fixed Git revision can prove the regression without editing the checkout.
function readSource(file) {
  const ref = process.env.FRIDAY_CREW_TEST_SOURCE_REF;
  if (!ref) return fs.readFileSync(path.join(root, file), 'utf8');
  if (!/^[a-f0-9]{7,40}$/.test(ref)) throw Error('Use a hexadecimal commit revision.');
  const key = ref + ':' + file;
  if (!sourceCache.has(key)) sourceCache.set(key, execFileSync('git', ['show', key],
    {cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:16*1024*1024}));
  return sourceCache.get(key);
}

function harness(file) {
  const source = readSource(file), marker = source.indexOf("window.addEventListener('friday:crew-voice',command)");
  const from = source.lastIndexOf('  useEffect(() => {', marker), to = source.indexOf('  const toggleVoice', marker);
  assert.ok(marker > 0 && from > 0 && to > marker, 'extract the real Crew command and conversation effects');
  const stopAt = source.search(/^  voiceStopRef\.current\s*=/m);
  const stopBody = source.indexOf('{', stopAt) + 1;
  const stopTail = source.slice(stopBody).search(/\n    const v\s*=/);
  assert.ok(stopAt > 0 && stopTail > 0, 'extract the real stop intent cleanup');

  const effects = [], listeners = new Map(), starts = [], activations = [], stops = [], flushes = [], frames = [], connections = [];
  const voiceKeepRef = {current:{wanted:false,crewConversationId:null}};
  const voiceRef = {current:{ws:null}}, voiceStartRef = {current:null}, voiceStopRef = {current:null};
  const crewVoicePendingRef = {current:null}, crewMainConversationRef = {current:'main'};
  let generation = 0;
  const context = vm.createContext({
    convId:'main',voiceKeepRef,voiceRef,voiceStartRef,voiceStopRef,crewVoicePendingRef,crewMainConversationRef,
    WebSocket:{OPEN:1},JSON,
    window:{FridayCrew:{enabled:cid=>['main','other','third'].includes(cid)},addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
    useEffect:fn=>effects.push(fn),openConversation:cid=>activations.push(cid),
    flushPlaybackRef:{current:reason=>flushes.push(reason)},
  });
  // Run just the real stop prefix. Device cleanup is mocked; the intent cleanup
  // is not, so a pending request surviving the normal mic Stop is observable.
  const clearIntent = vm.runInContext('(opts => {' + source.slice(stopBody, stopBody + stopTail) + '})', context);
  voiceStopRef.current = opts => {
    clearIntent(opts);stops.push(opts);generation++;
    voiceKeepRef.current.wanted=false;voiceRef.current.ws=null;
  };
  voiceStartRef.current = opts => {
    const attempt=++generation;
    voiceKeepRef.current.wanted=true;voiceKeepRef.current.crewConversationId=opts.conversationId;
    starts.push({conversationId:opts.conversationId,mainConversation:context.convId,
      finish(){
        if(attempt!==generation||!voiceKeepRef.current.wanted)return;
        connections.push(opts.conversationId);
        voiceRef.current.ws={readyState:1,send:message=>frames.push(JSON.parse(message))};
      }});
  };
  vm.runInContext(source.slice(from,to),context);
  assert.equal(effects.length,2);
  effects[0]();effects[1]();
  return {starts,activations,stops,flushes,frames,connections,voiceKeepRef,
    command(action,cid){listeners.get('friday:crew-voice')({detail:{action,conversationId:cid}});},
    commit(cid){context.convId=cid;crewMainConversationRef.current=cid;effects[1]();},
    stop(){voiceStopRef.current();},
  };
}

for (const file of ['index.html','ui_parts/app.html']) {
  test(file + ': a popout activates its chat before starting the shared mic', () => {
    const h=harness(file);h.command('start','other');
    assert.deepEqual(h.activations,['other']);assert.equal(h.starts.length,0,'no mic request while main chat still differs');
    h.commit('other');assert.equal(h.starts.length,1);assert.equal(h.starts[0].conversationId,h.starts[0].mainConversation);
    h.starts[0].finish();assert.deepEqual(h.connections,['other']);
  });
  test(file + ': another room cannot stop or quiet the active microphone', () => {
    const h=harness(file);h.command('start','main');h.starts[0].finish();const stops=h.stops.length;
    h.command('stop','other');h.command('quiet','other');
    assert.equal(h.stops.length,stops);assert.equal(h.frames.length,0);assert.equal(h.flushes.length,0);
    h.command('quiet','main');assert.deepEqual(h.frames,[{type:'barge'}]);assert.equal(h.flushes.length,1);
  });
  test(file + ': other-room Stop cannot cancel microphone acquisition', () => {
    const h=harness(file);h.command('start','main');h.command('stop','other');h.starts[0].finish();
    assert.deepEqual(h.connections,['main']);
  });
  test(file + ': matching Stop cancels pending microphone acquisition', () => {
    const h=harness(file);h.command('start','main');h.command('stop','main');h.starts[0].finish();
    assert.deepEqual(h.connections,[]);assert.equal(h.voiceKeepRef.current.wanted,false);
  });
  test(file + ': Stop before chat activation prevents a delayed voice start', () => {
    const h=harness(file);h.command('start','other');h.command('stop','other');h.commit('other');
    assert.equal(h.starts.length,0);
  });
  test(file + ': the normal microphone Stop also clears queued activation', () => {
    const h=harness(file);h.command('start','other');h.stop();h.commit('other');
    assert.equal(h.starts.length,0);
  });
  test(file + ': moving away cancels pending acquisition and queued activation', () => {
    const h=harness(file);h.command('start','main');h.commit('third');h.starts[0].finish();
    assert.deepEqual(h.connections,[]);
    h.command('start','other');h.commit('main');h.commit('other');
    assert.equal(h.starts.length,1,'discarded activation must not revive on a later visit');
  });
  test(file + ': only the latest popout request starts after activation', () => {
    const h=harness(file);h.command('start','other');h.command('start','third');
    assert.deepEqual(h.activations,['other','third']);assert.equal(h.starts.length,0);
    h.commit('third');assert.deepEqual(h.starts.map(s=>s.conversationId),['third']);
    h.starts[0].finish();assert.deepEqual(h.connections,['third']);
  });
}
