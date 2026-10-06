/* Browser-free checks of the shipped scene hooks and workspace camera leases.
 * No camera, server, model, external asset or application process is started. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { trackingSession, spatialLayout, stageProjection, projectedSphere, constrainSpatialRect, fitRadialDistance } = require('../static/friday_holographic_workspace.js');
const THREE = require('../static/vendor/three-r128.min.js');
const root = path.resolve(__dirname, '..');
function extractHooks(relative) {
    const source = fs.readFileSync(path.join(root, relative), 'utf8').replace(/\r\n/g, '\n');
    const start = source.indexOf('        let hologramRevision = 0');
    const end = source.indexOf('\n        function onFaceResults(results)', start);
    const apiStart = source.indexOf('window.fridayVibe = {');
    const apiEnd = source.indexOf('\n};', apiStart);
    assert.ok(start >= 0 && end > start && apiStart >= 0 && apiEnd > apiStart, relative + ': native scene hooks not found');
    return { camera: source.slice(start, end), api: source.slice(apiStart, apiEnd + 3) };
}
const hooks = extractHooks('index.html');
function extractHeldMessage(relative) {
    const source=fs.readFileSync(path.join(root,relative),'utf8').replace(/\r\n/g,'\n');
    const start=source.indexOf('function heldMessage() {'),end=source.indexOf('\n            }',start);
    assert.ok(start>=0&&end>start,relative+': native heldMessage not found');
    return source.slice(start,end+14);
}
const heldSource=extractHeldMessage('index.html');
function checkHeldMessage(source) {
    let now=350;
    const context={heldNote:0,performance:{now:()=>now}};
    const held=vm.runInNewContext(source+'; heldMessage',context);
    assert.equal(held(),'','A fresh page must not report a frame-budget rollback');
    now=7999;assert.equal(held(),'');
    context.heldNote=10000;now=10500;
    assert.match(held(),/too heavy.*went back/,'A real recent rollback stays visible');
    now=18000;assert.equal(held(),'','The rollback note expires after eight seconds');
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no;}); return {promise,resolve,reject}; };
function host(options = {}) {
    const loading = options.loading || {promise:Promise.resolve()};
    const context = { loading:loading.promise, console: {warn() {}}, Promise };
    context.window = context;
    vm.createContext(context);
    vm.runInContext(`
        var isHologramMode=false, isFaceVisible=false, faceDetectionMP=null;
        var isHandTrackingMode=${!!options.hands}, cameraStarts=0, cameraStops=0;
        var cameraWanted=${!!options.hands}, sceneContextLost=false, renderer={}, scene={};
        var currFaceX=.1,currFaceY=.2,currFaceZ=.3,transitionProgress=.4,callHold=false;
        var FridayTracking={head:{seen:true}};
        var FridayCamera={state:{status:'off',detail:'',wanted:false}};
        var currentEvolutionIdx=0,EVOLUTION_PATH=[{id:'CUBES',name:'GENESIS LATTICE'}];
        function loadMediaPipe(){return loading;}
        function startSharedCamera(){cameraStarts++;cameraWanted=true;}
        function releaseCameraIfUnused(){if(!isHologramMode&&!isHandTrackingMode){cameraStops++;cameraWanted=false;}}
        function updateCameraIndicator(){}
        function onFaceResults(){}
        function FaceDetection(){this.setOptions=function(){};this.onResults=function(){};}
        ${options.unsafe ? hooks.camera.replace('if (revision !== hologramRevision || !isHologramMode) return hologramState();','/* stale-load guard removed */') : hooks.camera}
        ${hooks.api}
    `, context);
    return {context, vibe:context.fridayVibe, loading, session:trackingSession(context.fridayVibe)};
}
async function staleCase(options = {}) {
    const loading=deferred(), h=host({...options,loading});
    const started=h.session.acquire();
    assert.equal(h.vibe.getHologramState().busy,true);
    await h.session.release(); loading.resolve(); await started; await settle();
    assert.equal(h.context.cameraStarts,0,'A superseded MediaPipe load must never acquire the camera');
    assert.equal(h.context.isHologramMode,false);
}
async function run() {
    let passed=0;
    const check=async(name,fn)=>{await fn();passed++;process.stdout.write('PASS '+name+'\n');};
    await check('authoritative scene hooks match their build mirror',()=>assert.deepEqual(extractHooks('ui_parts/styles_and_scene.html'),hooks));
    await check('native rollback note is absent on cold start and expires after a real event',()=>{assert.equal(extractHeldMessage('ui_parts/styles_and_scene.html'),heldSource);checkHeldMessage(heldSource);});
    await check('negative control catches the former cold-start rollback message',()=>{const previous=heldSource.replace('heldNote > 0 && ','');assert.notEqual(previous,heldSource);assert.throws(()=>checkHeldMessage(previous),/fresh page must not report/);});
    await check('default construction never starts camera',()=>{const h=host();assert.equal(h.context.cameraStarts,0);assert.equal(h.session.owns,false);});
    await check('acquire receives exact owner lease and release stops its mode',async()=>{const h=host();await h.session.acquire();assert.equal(h.session.owns,true);assert.equal(h.context.cameraStarts,1);await h.session.release();assert.equal(h.context.isHologramMode,false);assert.equal(h.context.cameraWanted,false);});
    await check('late tracker load cannot start after release',()=>staleCase());
    await check('hands retain shared camera after owned head mode turns off',async()=>{const h=host({hands:true});await h.session.acquire();await h.session.release();assert.equal(h.context.isHologramMode,false);assert.equal(h.context.cameraWanted,true);assert.equal(h.context.cameraStops,0);});
    await check('existing native head tracking is observed without acquiring ownership',async()=>{const h=host();await h.vibe.setHologram(true,'native');const starts=h.context.cameraStarts;await h.session.acquire();assert.equal(h.session.owns,false);await h.session.release();assert.equal(h.context.isHologramMode,true);assert.equal(h.context.cameraStarts,starts);assert.equal(h.context.cameraStops,0);});
    await check('external native change supersedes lease and cannot be released by panel',async()=>{const h=host();await h.session.acquire();await h.vibe.setHologram(true,'native');assert.equal(h.session.superseded,true);assert.equal(h.session.owns,false);await h.session.release();assert.equal(h.context.isHologramMode,true);assert.equal(h.vibe.getHologramState().owner,'native');});
    await check('native Off while panel awaits loading wins the race',async()=>{const loading=deferred(),h=host({loading});const pending=h.session.acquire();await h.vibe.setHologram(false,'native');loading.resolve();await assert.rejects(()=>pending,/could not start/);assert.equal(h.context.cameraStarts,0);assert.equal(h.context.isHologramMode,false);});
    await check('failed tracking load releases only head mode and reports error',async()=>{const loading=deferred(),h=host({loading,hands:true});const pending=h.session.acquire();loading.reject(new Error('assets unavailable'));await assert.rejects(()=>pending,/could not load/);assert.equal(h.context.cameraWanted,true);assert.equal(h.context.cameraStops,0);assert.equal(h.session.busy,false);});
    await check('scene snapshot exposes renderer pose and real transition progress',()=>{const h=host();let s=h.vibe.getWorkspaceSceneState();assert.equal(s.transitionProgress,.4);assert.equal(s.head.x,.1);h.context.transitionProgress=2;assert.equal(h.vibe.getWorkspaceSceneState().transitionProgress,1);h.context.transitionProgress=NaN;assert.equal(h.vibe.getWorkspaceSceneState().transitionProgress,1);});
    await check('negative control catches removed async revision guard',async()=>{assert.notEqual(hooks.camera.replace('if (revision !== hologramRevision || !isHologramMode) return hologramState();',''),hooks.camera);await assert.rejects(()=>staleCase({unsafe:true}),/superseded MediaPipe load/);});
    await check('classic keeps original area and no reserved stage',()=>{const area={x:0,y:36,w:1600,h:800};const got=spatialLayout(area,{enabled:false});assert.equal(got.stage,null);assert.deepEqual(got.content,area);});
    await check('stage stays opposite docked chat with a clear middle work area',()=>{
        for(const rightChat of [false,true])for(const arrangement of ['companion','present','focus']) {
            const base={x:16,y:48,w:1100,h:780},got=spatialLayout(base,{enabled:true,viewportWidth:1600,rightChat,arrangement});
            assert.equal(got.layout,'wide');assert.ok(got.content.w>=440);
            assert.equal(got.stage.w+got.content.w+16,base.w);
            if(rightChat){assert.equal(got.stage.x,base.x);assert.equal(got.content.x,got.stage.x+got.stage.w+16);}
            else {assert.equal(got.content.x,base.x);assert.equal(got.stage.x,got.content.x+got.content.w+16);}
        }
    });
    await check('compact stacks stage above work without intersection',()=>{const got=spatialLayout({x:8,y:48,w:700,h:640},{enabled:true,viewportWidth:720,arrangement:'companion'});assert.equal(got.layout,'compact');assert.equal(got.content.y,got.stage.y+got.stage.h+16);assert.equal(got.content.h+got.stage.h+16,640);});
    await check('floating windows cannot cover the reserved stage',()=>{const area={x:360,y:50,w:850,h:700};const got=constrainSpatialRect({x:20,y:0,w:1200,h:900},area);assert.deepEqual(got,{x:360,y:50,w:850,h:700});assert.deepEqual(constrainSpatialRect({x:1100,y:600,w:200,h:200},area),{x:1010,y:550,w:200,h:200});});
    await check('radial stage fit moves distant forms closer and near forms away without orbiting',()=>{
        const target=new THREE.Vector3(2,-3,1),direction=new THREE.Vector3(3,4,12).normalize();
        for(const before of [6,24]) {
            const position=target.clone().add(direction.clone().multiplyScalar(before));
            fitRadialDistance(position,target,12);
            assert.ok(Math.abs(position.distanceTo(target)-12)<1e-10,'fitted distance is symmetric');
            assert.ok(position.clone().sub(target).normalize().distanceTo(direction)<1e-10,'native viewing direction is preserved');
        }
    });
    await check('projection puts avatar in stage without stretching pixels and inverse remains exact',()=>{
        const stage={x:1120,y:60,w:360,h:800},camera=new THREE.PerspectiveCamera(60,stage.w/stage.h,.1,1000);
        camera.position.set(0,0,10);camera.lookAt(0,0,0);camera.updateMatrixWorld();stageProjection(camera,stage,1600,1000);
        const center=new THREE.Vector3().project(camera),x=new THREE.Vector3(1,0,0).project(camera),y=new THREE.Vector3(0,1,0).project(camera);
        assert.ok(Math.abs((center.x+1)*800-(stage.x+stage.w/2))<1e-8);
        assert.ok(Math.abs((1-center.y)*500-(stage.y+stage.h/2))<1e-8);
        assert.ok(Math.abs((x.x-center.x)*800-(y.y-center.y)*500)<1e-8,'pixel aspect must remain square');
        const point=new THREE.Vector3(.4,.7,-.2),roundtrip=point.clone().project(camera).unproject(camera);assert.ok(point.distanceTo(roundtrip)<1e-8);
    });
    await check('projected sphere bounds enclose real points under off-axis stage projection',()=>{
        const camera=new THREE.PerspectiveCamera(60,.5,.1,1000),sphere=new THREE.Sphere(new THREE.Vector3(1,2,-1),3);
        camera.position.set(3,1,15);camera.lookAt(0,0,0);camera.updateMatrixWorld();
        camera.projectionMatrix.elements[8]=.6;camera.projectionMatrix.elements[9]=-.35;
        stageProjection(camera,{x:1150,y:50,w:400,h:800},1600,1000);
        const bounds=projectedSphere(camera,sphere,1600,1000);assert.ok(bounds&&bounds.w>0&&bounds.h>0);
        for(let lat=0;lat<=24;lat++)for(let lon=0;lon<48;lon++){
            const phi=Math.PI*lat/24,theta=2*Math.PI*lon/48;
            const p=new THREE.Vector3(Math.sin(phi)*Math.cos(theta),Math.cos(phi),Math.sin(phi)*Math.sin(theta)).multiplyScalar(sphere.radius).add(sphere.center).project(camera);
            const x=(p.x+1)*800,y=(1-p.y)*500;
            assert.ok(x>=bounds.x-1e-6&&x<=bounds.x+bounds.w+1e-6&&y>=bounds.y-1e-6&&y<=bounds.y+bounds.h+1e-6);
        }
    });
    process.stdout.write(passed+' native ownership and spatial layout checks passed; no browser, camera, server, or source patch was run.\n');
}
run().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
