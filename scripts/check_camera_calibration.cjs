/* Device binding behavior; no camera, model, server, or browser is opened. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),file='static/friday_holographic_workspace.js',baseline=process.env.FRIDAY_BASELINE_REV;
let source=fs.readFileSync(path.join(root,file),'utf8');
if(baseline){
  const result=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':'+file],{cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:4e6});
  assert.equal(result.status,0,result.stderr);source=result.stdout;
}
const context={module:{exports:{}}};vm.runInNewContext(source,context);
const make=context.module.exports.cameraCalibration,key='friday_camera_calibration_v1',results=[];
function fixture(options={}){
  assert.equal(typeof make,'function','explicit calibrations need a device-scoped binding store');
  const store=options.store||new Map(),events=[],legacy={neutral_face_width:.31};
  let id='camera-a',width=.24,fail=false,helper,cfg={...legacy,hand_gain:3.4};
  const win={localStorage:{getItem:k=>store.get(k)||null,setItem(k,v){if(fail)throw new Error('storage unavailable');store.set(k,v);}},
    FridayCamera:{state:{status:'live'}},
    fridayVibe:{getTrackedCameraId:()=>id,getTrackedFaceWidth:()=>width},
    FridayTracking:{get:()=>({...cfg}),apply:patch=>{cfg={...cfg,...(helper?helper.filterSettings(patch):patch)};},
      calibrate:value=>{cfg.neutral_face_width=value||0;return value||null;}},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    dispatchEvent:event=>events.push(event),
    navigator:{mediaDevices:{getUserMedia:()=>{throw new Error('must not capture');},enumerateDevices:()=>{throw new Error('must not enumerate');}}}};
  helper=make(win);return {helper,store,events,legacy,win,get cfg(){return cfg;},
    camera(value){id=value;},face(value){width=value;},failStorage(value=true){fail=value;}};
}
function check(name,test){try{test();results.push({name,status:'passed'});}catch(error){results.push({name,status:'failed',message:error.message});}}
check('legacy account calibration is never automatically bound',()=>{
  const f=fixture();f.helper.sync();assert.equal(f.cfg.neutral_face_width,0);assert.equal(f.legacy.neutral_face_width,.31);
  assert.equal(f.store.has(key),false);assert.equal(f.helper.state.verified,false);assert.match(f.helper.state.status,/Calibrate distance/);
});
check('explicit known-camera calibration restores only for that device',()=>{
  const f=fixture();assert.equal(f.helper.calibrateCurrent().ok,true);assert.equal(f.cfg.neutral_face_width,.24);
  f.camera('camera-b');f.helper.sync();assert.equal(f.cfg.neutral_face_width,0);
  f.face(.36);f.helper.calibrateCurrent();f.camera('camera-a');f.helper.sync();assert.equal(f.cfg.neutral_face_width,.24);
  f.camera('camera-b');f.helper.sync();assert.equal(f.cfg.neutral_face_width,.36);
  const reloaded=fixture({store:f.store});reloaded.helper.sync();assert.equal(reloaded.cfg.neutral_face_width,.24);
});
check('missing identity refuses calibration and clears live baseline only',()=>{
  const f=fixture();f.helper.calibrateCurrent();const saved=f.store.get(key);
  f.camera(null);const result=f.helper.calibrateCurrent();assert.equal(result.ok,false);assert.equal(f.cfg.neutral_face_width,0);
  assert.match(result.status,/identity is unavailable/);assert.equal(f.store.get(key),saved);assert.equal(f.legacy.neutral_face_width,.31);
});
check('stale, missing, preview and invalid face widths never overwrite binding',()=>{
  const f=fixture();f.helper.calibrateCurrent();const saved=f.store.get(key);
  // The native physical-face getter returns null for stale/debug input.
  for(const width of [null,undefined,0,.02,NaN,Infinity,3]){
    f.face(width);assert.equal(f.helper.calibrateCurrent().ok,false);assert.equal(f.store.get(key),saved);
  }
  assert.equal(f.cfg.neutral_face_width,.24);
});
check('camera change during the guarded read refuses the wrong binding',()=>{
  const f=fixture();f.win.fridayVibe.getTrackedFaceWidth=()=>{f.camera('camera-b');return .24;};
  assert.equal(f.helper.calibrateCurrent().ok,false);assert.equal(f.store.has(key),false);assert.equal(f.cfg.neutral_face_width,0);
});
check('late account settings cannot reinstate unrelated calibration',()=>{
  const f=fixture();f.helper.sync();f.win.FridayTracking.apply({neutral_face_width:.7,hand_gain:5});
  assert.equal(f.cfg.neutral_face_width,0);assert.equal(f.cfg.hand_gain,5);
  f.helper.calibrateCurrent();f.win.FridayTracking.apply({neutral_face_width:.7,head_response:.2});
  assert.equal(f.cfg.neutral_face_width,.24);assert.equal(f.cfg.head_response,.2);
});
check('local binding storage is bounded and evicts the oldest explicit calibration',()=>{
  const f=fixture();for(let i=0;i<10;i++){f.camera('camera-'+i);f.face(.2+i*.01);f.helper.calibrateCurrent();}
  const stored=JSON.parse(f.store.get(key));assert.equal(stored.cameras.length,8);assert.equal(stored.cameras[0].id,'camera-2');
  f.camera('camera-0');f.helper.sync();assert.equal(f.cfg.neutral_face_width,0);
});
check('corrupt stored binding is not trusted as a baseline',()=>{
  for(const raw of ['not json',JSON.stringify({version:1,cameras:[{id:'camera-a',width:'0.24'}]}),JSON.stringify({version:9,cameras:[{id:'camera-a',width:.24}]})]){
    const f=fixture({store:new Map([[key,raw]])});f.helper.sync();assert.equal(f.cfg.neutral_face_width,0);assert.equal(f.helper.state.verified,false);
  }
});
check('storage failure reports session-only calibration',()=>{
  const f=fixture();f.failStorage();const result=f.helper.calibrateCurrent();
  assert.equal(result.ok,true);assert.equal(result.saved,false);assert.match(result.status,/could not save/);
  assert.equal(f.cfg.neutral_face_width,.24);assert.equal(f.store.has(key),false);assert.equal(f.legacy.neutral_face_width,.31);
});
check('reset removes only the current camera and preserves account preferences',()=>{
  const f=fixture();f.helper.calibrateCurrent();f.camera('camera-b');f.face(.4);f.helper.calibrateCurrent();
  assert.equal(f.helper.resetCurrent().saved,true);assert.equal(f.cfg.neutral_face_width,0);
  f.camera('camera-a');f.helper.sync();assert.equal(f.cfg.neutral_face_width,.24);assert.equal(f.legacy.neutral_face_width,.31);
});
check('status events carry no device identifier and unchanged sync is quiet',()=>{
  const f=fixture();f.helper.sync();const count=f.events.length;f.helper.sync();assert.equal(f.events.length,count);
  f.helper.calibrateCurrent();assert.ok(f.events.length>count);
  assert.ok(f.events.every(event=>event.type==='friday:camera-calibration'&&!JSON.stringify(event.detail).includes('camera-a')));
});
process.stdout.write(JSON.stringify({baseline:baseline||null,results})+'\n');
if(results.some(result=>result.status==='failed'))process.exitCode=1;
