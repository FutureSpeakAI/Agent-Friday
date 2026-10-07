const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {Coordinator,normalize} = require('../../static/friday_crew_playback.js');
const base={session_id:'session',conversation_id:'conversation',epoch:1};
const start={...base,type:'crew_speech_start',utterance_id:'one',speaker_id:'agent',label:'Researcher',provider:'cloud',model:'voice-model',voice_id:'voice',encoding:'pcm_s16le',sample_rate:24000};
function harness(decodeAudioData=async()=>{throw Error('unexpected decode');}) {
  const posted=[],sent=[],states=[],errors=[];
  const player=new Coordinator({port:{postMessage:m=>posted.push(m)},audioContext:{decodeAudioData},send:m=>sent.push(m),onState:m=>states.push(m),onError:m=>errors.push(m)});
  player.receive({type:'crew_session',...base});
  return {player,posted,sent,states,errors};
}
const audio=(seq=0,id='one')=>({...base,type:'crew_audio',utterance_id:id,seq,data:Buffer.from([0,0,1,0]).toString('base64')});
test('end alone never claims playback, and a finished acknowledgement requires every sealed sample',()=>{
  const h=harness();h.player.receive(start);h.player.receive(audio());
  h.player.receive({...base,type:'crew_speech_end',utterance_id:'one',total_samples:2});
  assert.equal(h.sent.length,0);
  h.player.worklet({type:'drained'});assert.equal(h.sent.length,0);
  h.player.worklet({type:'crew_started',utterance_id:'one',epoch:1,played_samples:1});
  h.player.worklet({type:'crew_finished',utterance_id:'one',epoch:1,played_samples:2});
  assert.deepEqual(h.sent.map(x=>x.status),['started','finished']);
  h.player.receive(start);assert.equal(h.player.active,null,'completed utterance cannot replay');
});
test('quiet drops trailing data until the server advances the room epoch',()=>{
  const h=harness();h.player.receive(start);h.player.receive(audio());h.player.quiet();
  const count=h.posted.length;h.player.receive(audio(1));h.player.receive({...start,utterance_id:'late'});
  assert.equal(h.posted.length,count);assert.equal(h.sent.at(-1).status,'interrupted');
  h.player.receive({...base,type:'crew_interrupted',epoch:2});
  h.player.receive({...start,epoch:1,utterance_id:'old'});assert.equal(h.player.active,null);
  h.player.receive({...start,epoch:2,utterance_id:'new'});assert.equal(h.player.active.meta.utterance_id,'new');
});
test('out of order and malformed PCM terminate once with a visible failure',()=>{
  const h=harness();h.player.receive(start);h.player.receive(audio(2));
  assert.equal(h.sent.at(-1).status,'failed');assert.match(h.errors[0],/lost/);
  h.player.receive(audio());assert.equal(h.sent.length,1);
  h.player.receive({...start,utterance_id:'two'});
  h.player.receive({...audio(0,'two'),data:'AA=='});assert.equal(h.sent.at(-1).status,'failed');
});
test('a tool-only host turn seals zero samples and a pre-start synthesis error remains visible',()=>{
  const h=harness();h.player.receive(start);
  h.player.receive({...base,type:'crew_speech_end',utterance_id:'one',total_samples:0});
  h.player.worklet({type:'crew_finished',utterance_id:'one',epoch:1,played_samples:0});
  assert.equal(h.sent.at(-1).status,'finished');assert.equal(h.sent.at(-1).total_samples,0);
  h.player.receive({...base,type:'crew_speech_error',utterance_id:'synthesis-failure',speaker_id:'agent',message:'Provider unavailable'});
  assert.equal(h.errors.at(-1),'Provider unavailable');assert.equal(h.sent.at(-1).status,'failed');
  h.player.receive({...start,utterance_id:'current'});
  h.player.receive({...base,type:'crew_speech_error',utterance_id:'synthesis-failure',speaker_id:'agent',message:'late error'});
  assert.equal(h.player.active.meta.utterance_id,'current');
});
test('a decode completing after interruption cannot enqueue sound',async()=>{
  let resolve;let input;
  const h=harness(bytes=>{input=bytes;return new Promise(r=>resolve=r);});
  h.player.receive({...start,encoding:'audio/mpeg'});
  h.player.receive(audio());h.player.receive(audio(1));
  h.player.receive({...base,type:'crew_speech_end',utterance_id:'one',total_samples:null});
  assert.equal(input.byteLength,8,'encoded chunks join before decoding');
  h.player.quiet();const count=h.posted.length;
  resolve({duration:1,sampleRate:24000,length:2,numberOfChannels:1,getChannelData:()=>new Float32Array([.1,.2])});
  await new Promise(r=>setImmediate(r));assert.equal(h.posted.length,count);
});
test('encoded audio is downmixed and resampled once into the shared player',async()=>{
  const buffer={duration:4/48000,sampleRate:48000,length:4,numberOfChannels:2,getChannelData:c=>new Float32Array(c?[0,0,0,0]:[1,1,1,1])};
  assert.deepEqual([...normalize(buffer)],[.5,.5]);
  const h=harness(async()=>buffer);h.player.receive({...start,encoding:'audio/wav'});h.player.receive(audio());
  h.player.receive({...base,type:'crew_speech_end',utterance_id:'one',total_samples:null});
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(h.posted.slice(-2).map(m=>m.type),['samples','crew_end']);
  assert.equal(h.posted.at(-1).total_samples,2);
});
function worklet(rate=24000) {
  let Processor;const events=[];
  const ctx=vm.createContext({sampleRate:rate,Float32Array,Math,AudioWorkletProcessor:class{constructor(){this.port={postMessage:m=>events.push(m)};}},registerProcessor:(_,value)=>Processor=value});
  vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../static/js/friday_pcm_player.worklet.js'),'utf8'),ctx);
  const player=new Processor({processorOptions:{srcRate:24000}});
  const send=m=>player.port.onmessage({data:m});
  const render=(blocks=1)=>{for(let i=0;i<blocks;i++)player.process([],[[new Float32Array(128)]]);};
  return {player,send,render,events};
}
test('sealed short speech below jitter prefill drains completely at native device rate',()=>{
  const h=worklet(48000);h.send({type:'crew_begin',utterance_id:'short',epoch:2});
  h.send({type:'samples',utterance_id:'short',epoch:2,data:new Float32Array(400).fill(.1)});
  h.render(4);assert.equal(h.events.length,0,'unsealed short burst waits for prefill');
  h.send({type:'crew_end',utterance_id:'short',epoch:2,total_samples:400});h.render(12);
  const finished=h.events.filter(e=>e.type==='crew_finished');assert.equal(finished.length,1);assert.equal(finished[0].played_samples,400);
});
test('a worklet underrun does not finish an unsealed utterance, but its later tail does',()=>{
  const h=worklet();h.send({type:'crew_begin',utterance_id:'stream',epoch:3});
  h.send({type:'samples',utterance_id:'stream',epoch:3,data:new Float32Array(4000).fill(.1)});h.render(40);
  assert.equal(h.events.filter(e=>e.type==='crew_finished').length,0);
  h.send({type:'samples',utterance_id:'stream',epoch:3,data:new Float32Array(40).fill(.1)});
  h.send({type:'crew_end',utterance_id:'stream',epoch:3,total_samples:4040});h.render(8);
  assert.equal(h.events.find(e=>e.type==='crew_finished').played_samples,4040);
});
test('worklet ignores flushed tagged chunks and preserves ordinary legacy PCM',()=>{
  const h=worklet();h.send({type:'crew_begin',utterance_id:'old',epoch:1});h.send({type:'flush'});
  h.send({type:'samples',utterance_id:'old',epoch:1,data:new Float32Array(4000)});assert.equal(h.player.writeIdx,0);
  h.send({type:'samples',data:new Float32Array(4000).fill(.2)});h.render(2);assert.ok(h.events.some(m=>m.type==='active'));
});
test('Crew ignores legacy playback control after epoch advance and old socket delivery',()=>{
  const h=harness();h.player.receive(start);h.player.quiet();
  const source=fs.readFileSync(path.resolve(__dirname,'../../index.html'),'utf8');
  const startAt=source.indexOf('    ws.onmessage = ev => {',source.indexOf('voiceStartRef.current ='));
  const endAt=source.indexOf('      // Feed one base64 PCM chunk',startAt);
  assert.ok(startAt>0&&endAt>startAt);
  const ws={},leaked=[],voiceRef={current:{ws}},keep={current:{}},v={crewPlayback:h.player};
  const context=vm.createContext({ws,voiceRef,voiceKeepRef:keep,v,leaked,Date,JSON,window:{},_stale:()=>false});
  vm.runInContext(source.slice(startAt,endAt)+'leaked.push(m.type);};',context);
  ws.onmessage({data:JSON.stringify({...base,type:'crew_interrupted',epoch:2})});
  for(const type of ['interrupted','turn_end','tts_pause','tts_resume','audio'])ws.onmessage({data:JSON.stringify({type})});
  assert.deepEqual(leaked,[]);assert.equal(h.player.blocked,false);
  voiceRef.current.ws={};ws.onmessage({data:JSON.stringify({...start,epoch:2,utterance_id:'old-socket'})});
  assert.equal(h.player.active,null);
});
test('durable speech receipts qualify full replies without empty transcript bubbles',()=>{
  const window={React:{createElement:()=>{},useState:()=>{},useEffect:()=>{},useRef:()=>{}}};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../static/friday_crew.js'),'utf8'),{window,Map,JSON});
  const rows=[{id:'reply',text:'Full generated answer',meta:{kind:'crew_result',task_id:'task'}},{id:'receipt',text:'',meta:{kind:'crew_playback',task_id:'task',audio_state:'interrupted',played_samples:24000}},{id:'silent',text:'',meta:{kind:'crew_speech'}}];
  const visible=window.FridayCrew.messages(rows);
  assert.equal(visible.length,1);assert.equal(visible[0].text,'Full generated answer');assert.equal(visible[0].meta.audio_state,'interrupted');
  assert.equal(rows[0].meta.audio_state,undefined,'rendering does not mutate persisted task result');
});
