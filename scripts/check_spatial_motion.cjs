/* Exercise the shared geometry and real DOM/CSS controller without a server,
 * model or camera. Avatar projection has separate camera-adapter coverage. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const {chromium} = require('@playwright/test');
const root = path.resolve(__dirname,'..');
const baseline = process.env.FRIDAY_BASELINE_REV;
function read(file) {
  if (!baseline) return fs.readFileSync(path.join(root,file),'utf8');
  const result = spawnSync('git',['-c','safe.directory='+root,'show',baseline+':'+file],
    {cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:4e6});
  assert.equal(result.status,0,result.stderr); return result.stdout;
}
const source = read('static/friday_holographic_workspace.js');
const css = read('static/friday_shared_surfaces.css')+'\n'+read('static/friday_native_shell.css')+'\n'+read('static/friday_holographic_workspace.css');
const sandbox = {module:{exports:{}}};
vm.runInNewContext(source,sandbox);
const core = sandbox.module.exports;
const results = [];
async function check(name,run) {
  try { await run(); results.push({name,status:'passed'}); }
  catch(error) { results.push({name,status:'failed',message:error.message}); }
}
function clear(layout) {
  const s=layout.stageFrame || layout.stage,c=layout.content;
  assert.ok(s && c);
  assert.ok(s.x+s.w<=c.x+.001 || c.x+c.w<=s.x+.001 || s.y+s.h<=c.y+.001 || c.y+c.h<=s.y+.001,
    'avatar stage and workspace must remain disjoint');
}
function layout(arrangement,options={}) {
  return core.spatialLayout({x:16,y:64,w:1568,h:820},
    {enabled:true,viewportWidth:1600,arrangement,...options});
}
function equalRect(actual,expected,diagnostics=false,tolerance=.01) {
  for(const key of ['x','y','w','h']) {
    const delta=Math.abs(actual[key]-expected[key]);
    assert.ok(delta<tolerance,key+' must remain stable'+(diagnostics?' (actual='+actual[key]+', expected='+expected[key]+', delta='+delta+', tolerance='+tolerance+', rect='+JSON.stringify(actual)+', layout='+JSON.stringify(expected)+')':''));
  }
}
(async()=>{
  await check('interrupted same-side motion preserves every separating frame',()=>{
    assert.equal(typeof core.createLayoutMotion,'function','layout changes need a coordinated motion controller');
    const a=layout('focus'),b=layout('present'),c=layout('companion');
    const motion=core.createLayoutMotion(a); motion.request(b,0,{enabled:true});
    for(let t=0;t<=112;t+=16) clear(motion.frame(t));
    const interrupted=motion.current; motion.request(c,112,{enabled:true});
    equalRect(motion.current.stage,interrupted.stage);
    for(let t=128;t<=432;t+=16) clear(motion.frame(t));
    equalRect(motion.current.content,c.content); assert.equal(motion.active,false);
  });
  await check('side switches and compact topology settle without crossing',()=>{
    const motion=core.createLayoutMotion(layout('companion'));
    const opposite=layout('companion',{rightChat:true});
    motion.request(opposite,0,{enabled:true}); clear(motion.current); assert.equal(motion.active,false);
    const portrait=core.spatialLayout({x:8,y:64,w:304,h:420},{enabled:true,compact:true,viewportWidth:320});
    motion.request(portrait,20,{enabled:true}); equalRect(motion.current.stage,portrait.stage); clear(motion.current);
  });
  await check('hold pauses elapsed motion and releases without a jump',()=>{
    const motion=core.createLayoutMotion(layout('focus'));
    motion.request(layout('present'),0,{enabled:true}); motion.frame(48);
    const held=motion.current;
    for(let t=64;t<1000;t+=16) equalRect(motion.frame(t,true).stage,held.stage);
    motion.frame(1000); assert.ok(motion.active,'release resumes remaining time'); clear(motion.current);
    for(let t=1016;t<1400;t+=16) motion.frame(t);
    assert.equal(motion.active,false);
  });
  await check('reduced/off, safety resize and slow rendering settle safely',()=>{
    const a=layout('focus'),b=layout('present'),motion=core.createLayoutMotion(a);
    motion.request(b,0,{enabled:false}); equalRect(motion.current.stage,b.stage);
    motion.request(a,20,{enabled:true}); motion.frame(40);
    motion.request(b,50,{enabled:true,immediate:true}); equalRect(motion.current.stage,b.stage);
    motion.request(a,60,{enabled:true}); [120,180,240].forEach(t=>motion.frame(t));
    assert.equal(motion.active,false); assert.ok(motion.fallbackUntil>240); clear(motion.current);
    motion.request(b,250,{enabled:true}); assert.equal(motion.active,false);
  });

  const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
  assert.ok(typeof executablePath==='string'&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select an explicit installed dedicated test browser');
  const browser=await chromium.launch({executablePath,headless:true});
  console.log(JSON.stringify({browser:{executablePath,version:browser.version()}}));
  async function mounted(run,viewport={width:1600,height:1000}) {
    const page=await browser.newPage({viewport,reducedMotion:'no-preference'}), errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body></body></html>'}));
    await page.goto('http://friday.test');
    await page.setContent(`<!doctype html><style>${css}</style><style>
      .top-bar{position:fixed;inset:0 0 auto;height:52px}.dock{display:none}
      .friday-avatar-caption{position:fixed;overflow:hidden;box-sizing:border-box;font:12px sans-serif}
      #state-indicator,#presence-status{line-height:16px}.fwin{position:fixed;box-sizing:border-box}
    </style><body class="friday-experience-enabled friday-experience-desktop">
      <div class="top-bar"></div><div class="dock"></div><div id="ui-root"></div>
      <div id="state-indicator">READY</div><div id="presence-status">Friday is here</div>
      <input id="editor" aria-label="Test draft"><div id="window" class="fwin"></div></body>`);
    await page.evaluate(()=>{
      window.__style='simple'; window.FridayDisplayStyle={get:()=>__style};
      window.__deviceRequests=0;
      if(!navigator.mediaDevices)Object.defineProperty(navigator,'mediaDevices',{value:{},configurable:true});
      navigator.mediaDevices.getUserMedia=async()=>{__deviceRequests++;throw new Error('No device capture allowed');};
      window.FridayTracking={hand:{seen:false,pinching:false},head:{seen:false},get:()=>({})};
      window.FridayHandCursor={locked:null};
      window.fridayDesktopArea=()=>{
        const rect={x:16,y:64,w:innerWidth-32,h:innerHeight-80};
        return window.FridayHolographicWorkspace?.workspaceArea(rect) || rect;
      };
      window.__snap=()=>FridayHolographicWorkspace.state.spatial;
    });
    await page.addScriptTag({content:source});
    await page.evaluate(()=>dispatchEvent(new Event('resize')));
    try { await run(page); assert.deepEqual(errors,[]); assert.equal(await page.evaluate(()=>__deviceRequests),0); }
    finally { await page.close(); }
  }
  try {
    await check('actual mounted arrangement moves through safe intermediate frames',()=>mounted(async page=>{
      const trace=await page.evaluate(async()=>{
        const before=__snap(); FridayHolographicWorkspace.setArrangement('present');
        const immediate=__snap(),frames=[];
        await new Promise(resolve=>{const started=performance.now();const tick=()=>{
          frames.push(__snap()); if(performance.now()-started<500)requestAnimationFrame(tick);else resolve();
        };requestAnimationFrame(tick);});
        return {before,immediate,frames,after:__snap()};
      });
      equalRect(trace.immediate.stage,trace.before.stage);
      assert.ok(trace.frames.some(f=>Math.abs(f.stage.w-trace.before.stage.w)>1 && Math.abs(f.stage.w-trace.after.stage.w)>1),
        'the mounted controller must publish an actual intermediate geometry');
      trace.frames.forEach(clear); assert.ok(trace.after.stage.w>trace.before.stage.w);
    }));
    await check('native DOM surface follows every frame without rerendering',()=>mounted(async page=>{
      const samples=await page.evaluate(async()=>{
        const element=document.getElementById('window');
        // These native surfaces have legacy, high-specificity geometry in
        // shared CSS. Registered frame coordinates must win that cascade.
        element.className='fwin friday-workspace-studio thread-panel';
        const unregister=FridayHolographicWorkspace.registerSurface(element,{kind:'window',getRect:()=>FridayHolographicWorkspace.layoutRect.content});
        FridayHolographicWorkspace.setArrangement('focus');
        const values=[];
        await new Promise(resolve=>{const start=performance.now();const tick=()=>{
          const r=element.getBoundingClientRect(),style=element.style;
          const css=Object.fromEntries(['x','y','w','h'].map(axis=>[axis,parseFloat(style.getPropertyValue('--friday-surface-'+axis))]));
          values.push({layout:__snap(),css,rect:{x:r.x,y:r.y,w:r.width,h:r.height}});
          if(performance.now()-start<480)requestAnimationFrame(tick);else resolve();
        };requestAnimationFrame(tick);});
        unregister();return {values,marker:element.hasAttribute('data-friday-spatial-surface')};
      });
      samples.values.forEach(sample=>{
        clear(sample.layout);equalRect(sample.css,sample.layout.content,true);
        // Rendered pixels may round; controller coordinates keep the strict comparison.
        equalRect(sample.rect,sample.layout.content,true,.02);
      });
      assert.equal(samples.marker,false);
    }));
    await check('typing, composition and a real hand lock defer motion then release',()=>mounted(async page=>{
      for(const mode of ['typing','composition','hand']) {
        const snapshots=await page.evaluate(async mode=>{
          const editor=document.getElementById('editor'),before=__snap();
          if(mode==='hand'){FridayTracking.hand.seen=true;FridayHandCursor.locked={id:'test'};}
          else editor.dispatchEvent(new Event(mode==='composition'?'compositionstart':'input',{bubbles:true}));
          FridayHolographicWorkspace.setArrangement(FridayHolographicWorkspace.state.arrangement==='present'?'focus':'present');
          await new Promise(resolve=>setTimeout(resolve,180));const held=__snap();
          if(mode==='hand'){FridayTracking.hand.seen=false;FridayHandCursor.locked=null;}
          if(mode==='composition')editor.dispatchEvent(new Event('compositionend',{bubbles:true}));
          await new Promise(resolve=>setTimeout(resolve,850));return {before,held,after:__snap()};
        },mode);
        equalRect(snapshots.before.stage,snapshots.held.stage);clear(snapshots.after);
        assert.ok(Math.abs(snapshots.after.stage.w-snapshots.before.stage.w)>1);
      }
    }));
    await check('focused control alone does not freeze a requested layout',()=>mounted(async page=>{
      const result=await page.evaluate(async()=>{
        document.getElementById('editor').focus();const before=__snap();
        FridayHolographicWorkspace.setArrangement('focus');
        await new Promise(resolve=>setTimeout(resolve,500));return {before,after:__snap()};
      });
      assert.notEqual(result.before.stage.w,result.after.stage.w);clear(result.after);
    }));
    await check('multiple pointers and independent drag leases retain their holds',()=>mounted(async page=>{
      const result=await page.evaluate(async()=>{
        const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms)),before=__snap();
        document.dispatchEvent(new PointerEvent('pointerdown',{pointerId:1,bubbles:true}));
        document.dispatchEvent(new PointerEvent('pointerdown',{pointerId:2,bubbles:true}));
        FridayHolographicWorkspace.setArrangement('present');await pause(90);
        document.dispatchEvent(new PointerEvent('pointerup',{pointerId:1,bubbles:true}));await pause(220);
        const onePointer=__snap();
        document.dispatchEvent(new PointerEvent('pointerup',{pointerId:2,bubbles:true}));await pause(600);
        const moved=__snap(),releaseA=FridayHolographicWorkspace.holdLayout('drag'),releaseB=FridayHolographicWorkspace.holdLayout('drag');
        FridayHolographicWorkspace.setArrangement('focus');releaseA();await pause(300);
        const oneLease=__snap();releaseB();await pause(600);
        return {before,onePointer,moved,oneLease,after:__snap()};
      });
      equalRect(result.before.stage,result.onePointer.stage);equalRect(result.moved.stage,result.oneLease.stage);
      assert.notEqual(result.after.stage.w,result.moved.stage.w);clear(result.after);
    }));
    await check('compact resize overrides input hold and Classic releases containment',()=>mounted(async page=>{
      await page.evaluate(()=>{
        FridayHolographicWorkspace.holdLayout('drag');
        FridayHolographicWorkspace.registerSurface(document.getElementById('window'),{getRect:()=>({x:900,y:80,w:600,h:700})});
        window.__compactResizeObserved=false;
        const onResize=()=>{
          if(innerWidth!==320 || innerHeight!==568)return;
          window.removeEventListener('resize',onResize);
          requestAnimationFrame(()=>{window.__compactResizeObserved=true;});
        };
        window.addEventListener('resize',onResize);
      });
      await page.setViewportSize({width:320,height:568});
      await page.waitForFunction(()=>window.__compactResizeObserved===true,null,{timeout:5000});
      const narrow=await page.evaluate(()=>({layout:__snap(),moving:FridayHolographicWorkspace.state.moving}));
      clear(narrow.layout);assert.equal(narrow.moving,false);assert.ok(narrow.layout.content.x+narrow.layout.content.w<=320);
      await page.evaluate(()=>{__style='classic';document.body.classList.remove('friday-experience-enabled');dispatchEvent(new Event('friday:display-style'));});
      assert.equal(await page.evaluate(()=>document.getElementById('window').hasAttribute('data-friday-spatial-surface')),false);
    }));
    await check('motion setting persists and reset preserves unrelated preferences',()=>mounted(async page=>{
      const result=await page.evaluate(()=>{
        localStorage.setItem('unrelated-card','keep');FridayHolographicWorkspace.setMode('immersive');
        FridayHolographicWorkspace.setMotion('off');FridayHolographicWorkspace.setArrangement('focus');
        const saved=JSON.parse(localStorage.getItem('friday_holographic_workspace_v1'));
        FridayHolographicWorkspace.resetLayout();return {saved,state:FridayHolographicWorkspace.state,card:localStorage.getItem('unrelated-card')};
      });
      assert.equal(result.saved.motion,'off');assert.equal(result.state.arrangement,'companion');
      assert.equal(result.state.motion,'on');assert.equal(result.state.mode,'immersive');assert.equal(result.card,'keep');
    }));
    await check('reduced motion suppresses intermediate geometry',()=>mounted(async page=>{
      await page.emulateMedia({reducedMotion:'reduce'});
      const result=await page.evaluate(()=>{FridayHolographicWorkspace.setArrangement('focus');return FridayHolographicWorkspace.state;});
      assert.equal(result.moving,false);clear(result.spatial);
    }));
  } finally { await browser.close(); }
  process.stdout.write(JSON.stringify({baseline:baseline||null,results})+'\n');
  if(results.some(result=>result.status==='failed')) process.exitCode=1;
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
