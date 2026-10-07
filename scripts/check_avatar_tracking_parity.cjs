/* Camera-free regression for the served tracking engine and avatar framing.
 * FRIDAY_AVATAR_BASELINE optionally checks a committed earlier implementation. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const THREE = require('../static/vendor/three-r128.min.js');
const baseline = process.env.FRIDAY_AVATAR_BASELINE;
function source(file) {
    if (!baseline) return fs.readFileSync(path.join(root,file),'utf8');
    const r = spawnSync('git',['-c','safe.directory='+root,'show',baseline+':'+file],{cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:12e6});
    assert.equal(r.status,0,r.stderr);
    return r.stdout;
}
const html = source('index.html');
const implementation = {exports:{}};
vm.runInNewContext(source('static/friday_holographic_workspace.js'),{module:implementation,globalThis:{}});
const api = implementation.exports;
const engine = html.match(/\/\/ <tracking-engine>\r?\n([\s\S]*?)\/\/ <\/tracking-engine>/)[1];
function trackingContext(block=engine) {
    const context = {innerWidth:1600,innerHeight:1000,screen:{width:1600},Math,Number,
        document:{body:{classList:{toggle(){}}},documentElement:{style:{setProperty(){}}}},
        dockDepthK:()=>1,resetDockVars(){},matchMedia:()=>({matches:false})};
    context.window=context;
    vm.runInNewContext(block,context);
    return context;
}
function verifySensitivity(block=engine) {
    const ctx=trackingContext(block),tk=ctx.FridayTracking;
    tk.apply({screen_width_cm:60,parallax_strength:.7,depth_strength:.5,head_smoothing:.8});
    const glass={w:12,h:8};
    const classic=tk.eyeOffset(.2,-.1,1,glass);
    const physicalWidth=tk.viewportCm();
    ctx.FridayHolographicWorkspace={stageRect:{x:1100,y:50,w:360,h:820}};
    assert.equal(tk.viewportCm(),physicalWidth,'A reserved stage must not redefine the physical screen');
    const simple=tk.eyeOffset(.2,-.1,1,glass);
    assert.deepEqual(simple,classic,'The same head movement and setting must retain the same relative response');
    const tallGlass={w:4,h:18};
    const tall=tk.eyeOffset(.2,-.1,1,tallGlass);
    assert.ok(Math.abs(tall.x/tallGlass.w-classic.x/glass.w)<1e-12,'Stage aspect must preserve horizontal sensitivity');
    assert.ok(Math.abs(tall.y/tallGlass.h-classic.y/glass.h)<1e-12,'Stage aspect must preserve vertical sensitivity');
    tk.apply({parallax_strength:.35});
    assert.equal(tk.eyeOffset(.2,-.1,1,glass).x,classic.x/2,'Real sensitivity must halve the camera response');
    assert.equal(tk.get().head_smoothing,.8,'Tuning sensitivity must keep personal steadiness');
}
async function run() {
    verifySensitivity();
    console.log('PASS physical tracking scale and adjustable gain are independent of display style');
    const old=engine.replace('return Math.max(1, window.innerWidth * perPx);',
        'return Math.max(1, (window.FridayHolographicWorkspace?.stageRect?.w || window.innerWidth) * perPx);');
    assert.notEqual(old,engine);
    assert.throws(()=>verifySensitivity(old),/physical screen/);
    const oldVertical=engine.replace('WEBCAM_TAN_Y * realCm * perCmY * lat','WEBCAM_TAN_Y * realCm * perCm * lat');
    assert.notEqual(oldVertical,engine);
    assert.throws(()=>verifySensitivity(oldVertical),/vertical sensitivity/);
    console.log('PASS negative control detects narrow-stage sensitivity amplification');
    assert.equal(typeof api.trackingTuner,'function','Scene & depth must expose the native tracking tuner');
    let cfg={parallax_strength:1,depth_strength:1,head_smoothing:.35,hand_gain:3.4};
    const statuses=[],requests=[];
    const host={FridayTracking:{get:()=>({...cfg}),apply:patch=>Object.assign(cfg,patch)},
        apiFetch:async(url,options)=>{requests.push(JSON.parse(options.body));return{ok:true,json:async()=>({status:'ok'})};}};
    const tuner=api.trackingTuner(host,message=>statuses.push(message));
    tuner.live({parallax_strength:.4});
    assert.equal(cfg.parallax_strength,.4,'Dragging must affect the camera immediately');
    assert.equal(requests.length,0,'Live preview must not flood durable storage');
    await Promise.all([tuner.save({parallax_strength:.4}),tuner.save({depth_strength:.3})]);
    assert.equal(requests.length,2);
    assert.deepEqual(requests,[{settings:{tracking:{parallax_strength:.4}}},{settings:{tracking:{depth_strength:.3}}}]);
    assert.equal(cfg.hand_gain,3.4,'Comfort edits preserve hand preferences');
    assert.equal(statuses.at(-1),'Tracking preferences saved.');
    host.apiFetch=async()=>({ok:false,json:async()=>({status:'error'})});
    await assert.rejects(tuner.save({head_smoothing:.7}),/could not be saved/);
    assert.match(statuses.at(-1),/not saved/);
    console.log('PASS native comfort controls preview, serialize partial saves and report failures');
    const boss=new THREE.Group(),body=new THREE.Mesh(new THREE.SphereGeometry(7),new THREE.MeshBasicMaterial());
    boss.add(body);
    const tunnel=new THREE.Mesh(new THREE.BoxGeometry(40,40,200),new THREE.MeshBasicMaterial());
    tunnel.userData.fridayStageScenery=true;
    const group=new THREE.Group();group.add(boss,tunnel);
    api.setStageScenery({EDEN:group},true);
    assert.equal(tunnel.visible,false,'Simple must not display the enclosing corridor as a view box');
    assert.equal(body.visible,true,'Simple keeps the authored avatar');
    api.setStageScenery({EDEN:group},false);
    assert.equal(tunnel.visible,true,'Classic retains the immersive original corridor');
    for(const file of ['index.html','ui_parts/styles_and_scene.html']) {
        assert.match(source(file),/gEden\.children\.forEach\(child => \{ if \(child !== edenLady\) child\.userData\.fridayStageScenery = true;/,
            file+': only the focal Rez figure is framed as the avatar');
    }
    console.log('PASS Simple excludes enclosing scenery and Classic restores it');
    const material=new THREE.PointsMaterial({size:.25,sizeAttenuation:true});
    const points=new THREE.Points(new THREE.BufferGeometry(),material),terrain=new THREE.Group();terrain.add(points);
    api.scaleStagePoints({MANDELBROT:terrain},2);
    assert.equal(material.size,.5,'Points must follow the projection magnification');
    api.scaleStagePoints({MANDELBROT:terrain},2);
    assert.equal(material.size,.5,'Repeated frames must not multiply brightness indefinitely');
    api.scaleStagePoints({MANDELBROT:terrain},1);
    assert.equal(material.size,.25,'Classic restores the authored point size');
    const oceanMaterial=new THREE.PointsMaterial();
    const uniforms=api.prepareStageField(oceanMaterial);
    const shader={uniforms:{},vertexShader:'void main() {\n#include <begin_vertex>\n}',fragmentShader:'void main() {\n#include <color_fragment>\n}'};
    oceanMaterial.onBeforeCompile(shader,{});
    assert.match(shader.vertexShader,/smoothstep\(12\.0, 18\.0, length\(position\.xz\)\)/);
    assert.match(shader.fragmentShader,/mix\(1\.0, vFridayFieldEdge, uFridayFieldStage\)/);
    assert.equal(uniforms.uFridayFieldStage.value,0,'Classic starts with the authored unmasked wave field');
    const ocean=new THREE.Points(new THREE.BufferGeometry(),oceanMaterial);ocean.userData.fridayStageField=true;
    const oceanGroup=new THREE.Group();oceanGroup.add(ocean);
    api.setStageScenery({GRID:oceanGroup},true);assert.equal(uniforms.uFridayFieldStage.value,1);
    api.setStageScenery({GRID:oceanGroup},false);assert.equal(uniforms.uFridayFieldStage.value,0);
    ocean.userData.fridayStagePointFloor=5;
    api.scaleStagePoints({GRID:oceanGroup},1,2);
    const nativeShader={uniforms:{},vertexShader:THREE.ShaderLib.points.vertexShader,fragmentShader:THREE.ShaderLib.points.fragmentShader};
    oceanMaterial.onBeforeCompile(nativeShader,{});
    assert.match(nativeShader.vertexShader,/gl_PointSize = max\(gl_PointSize, uFridayStagePointFloor\)/);
    assert.equal(nativeShader.uniforms.uFridayStagePointFloor.value,10,'The luminous core must remain visible at the renderer pixel ratio');
    assert.match(nativeShader.vertexShader,/vFridayFieldEdge =/,'Visible point cores must retain the soft field edge');
    api.scaleStagePoints({GRID:oceanGroup},1,0);
    assert.equal(nativeShader.uniforms.uFridayStagePointFloor.value,0,'Classic restores perspective attenuation without a point floor');
    for (const file of ['index.html','ui_parts/styles_and_scene.html']) {
        const block=source(file).match(/\/\/ <dirac-cloud>([\s\S]*?)\/\/ <\/dirac-cloud>/)[1];
        const context={THREE,Math};vm.runInNewContext(block+'\nthis.dirac=FridayDirac;',context);
        const cloud=new THREE.Group();context.dirac.build(cloud,{count:96});
        const mat=cloud.children[0].material;
        assert.match(mat.vertexShader,/gl_PointSize = max\(uFridayStagePointFloor, clamp\(/);
        api.scaleStagePoints({QUANTUM:cloud},1,2);
        assert.equal(mat.uniforms.uFridayStagePointFloor.value,4.8,'Dirac retains a visible core at the display pixel ratio');
        api.scaleStagePoints({QUANTUM:cloud},1,0);
        assert.equal(mat.uniforms.uFridayStagePointFloor.value,0,'Classic retains the original Dirac sprite sizes');
    }
    console.log('PASS terrain point scale is reversible and Ocean carrier edges fade only in Simple');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
