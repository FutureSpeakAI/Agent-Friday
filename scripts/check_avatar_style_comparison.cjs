/* Compare every authored avatar in the actual isolated preview.
 * Saved frames require human review for appearance, clipping and view boxes.
 * This does not activate a camera or prove physical tracking behavior.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),os=require('node:os');
const http=require('node:http'),{createHash,randomUUID}=require('node:crypto');
const root=path.resolve(__dirname,'..');
const base=new URL(process.env.FRIDAY_DESIGN_URL||'http://127.0.0.1:3194/');
const outputBase=process.env.FRIDAY_AVATAR_PROOFS;
const timeout=Number(process.env.FRIDAY_AVATAR_TIMEOUT_SECONDS||3300)*1000;
const sizes=[{name:'wide',width:1600,height:1000},{name:'portrait',width:390,height:844}];
const displays=['simple','classic'],deadline=Date.now()+timeout;
const result={schema:2,startedAt:new Date().toISOString(),status:'not-run',visualReview:'pending',forms:[],planned:[],cases:[],assets:{},cleanup:{},
  limits:['Actual rendered frames with synthetic preview data only; every frame needs human visual review.',
    'Simple has a measured reserved stage. Classic exposes no projected-avatar bounds, so its fit and caption visibility are visual checks.',
    'No camera, microphone, physical head or hand tracking, real model, voice command or saved action is exercised.',
    'Reduced-motion, Companion arrangement and Balanced depth are used; other arrangements and live movement need separate checks.']};
let output,server,browser,activeContext,stopping=false,expect;
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
function bounded(promise,ms,label){
  let timer;return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label+' timed out')),ms);})]).finally(()=>clearTimeout(timer));
}
function gate(){
  assert.ok(!stopping&&Date.now()<deadline,'Avatar capture deadline reached');
  const lock=process.env.FRIDAY_SURFACE_SUITE_LOCK,holder=process.env.FRIDAY_CHECK_HOLDER;
  assert.ok(lock&&holder,'Run under the parent-owned shared check lease with FRIDAY_SURFACE_SUITE_LOCK and FRIDAY_CHECK_HOLDER');
  assert.equal(fs.readFileSync(lock,'utf8').split(/\r?\n/)[0],'holder: '+holder,'Another check owns the shared lease');
  assert.ok(os.freemem()>=6*2**30,'At least 6 GiB free is required');
}
function writeEvidence(){
  if(!output)return;
  result.updatedAt=new Date().toISOString();
  result.unvisited=result.planned.filter(id=>!result.cases.some(row=>row.id===id&&row.status!=='running'));
  fs.writeFileSync(path.join(output,'comparison.json'),JSON.stringify(result,null,2));
  const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tile=id=>{
    const row=result.cases.find(candidate=>candidate.id===id);
    return '<figure><figcaption>'+esc(id)+' · '+esc(row?.status||'unvisited')+'</figcaption>'+
      (row?.image?'<a href="'+esc(row.image)+'"><img loading="lazy" src="'+esc(row.image)+'" alt="'+esc(id)+'"></a>':'<p>No frame saved.</p>')+
      (row?.error?'<p>'+esc(row.error)+'</p>':'')+'</figure>';
  };
  const pairs=sizes.flatMap(size=>result.forms.map(form=>'<section><h2>'+esc(form.name)+' · '+size.name+'</h2><div>'+displays.map(display=>tile(caseId(display,form.index,size))).join('')+'</div></section>')).join('');
  fs.writeFileSync(path.join(output,'index.html'),'<!doctype html><meta charset="utf-8"><title>Friday avatar comparisons</title><style>body{background:#080d16;color:#dde7f5;font:15px/1.5 system-ui;margin:30px}section{margin:30px 0}section>div{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:0;min-width:0}img{width:100%;border:1px solid #234;border-radius:10px}figcaption{margin:8px 0;color:#89cfff}p{overflow-wrap:anywhere}@media(max-width:650px){section>div{grid-template-columns:1fr}}</style><h1>Friday · Simple and Classic</h1><p>'+esc(result.status)+' · '+result.forms.length+' forms discovered in the running preview. '+result.unvisited.length+' cases unvisited. Human visual review pending.</p><p>'+esc(result.error||'')+'</p><ul>'+result.limits.map(text=>'<li>'+esc(text)+'</li>').join('')+'</ul>'+pairs);
}
function getLocal(relative){
  gate();const url=new URL(relative,base);assert.equal(url.origin,base.origin);
  return new Promise((resolve,reject)=>{
    let timer,finished=false;
    const done=(error,value)=>{if(finished)return;finished=true;clearTimeout(timer);error?reject(error):resolve(value);};
    const req=http.get(url,res=>{
      res.on('error',done);
      if(res.statusCode!==200){done(new Error('Preview GET '+url.pathname+' returned '+res.statusCode));req.destroy();return;}
      let length=0;const chunks=[];
      res.on('data',chunk=>{length+=chunk.length;if(length>14*1024*1024)req.destroy(new Error('Preview response exceeds capture limit'));else chunks.push(chunk);});
      res.on('end',()=>done(null,Buffer.concat(chunks)));
    });
    req.on('error',done);timer=setTimeout(()=>req.destroy(new Error('Preview GET deadline')),Math.max(1,Math.min(12000,deadline-Date.now())));
  });
}
async function verifyPreview(){
  assert.equal(base.protocol,'http:');assert.ok(['127.0.0.1','localhost'].includes(base.hostname)&&base.port&&base.port!=='3000','Use the explicit isolated loopback preview, never live Friday');
  assert.equal(base.pathname,'/');assert.ok(!base.search&&!base.hash&&!base.username&&!base.password,'Use the preview root URL');
  const stored=fs.readFileSync(path.join(root,'index.html'),'utf8'),served=(await getLocal('/')).toString('utf8');
  const stripped=served.replace(/<script>window\.__FRIDAY_PREVIEW__=true;window\.__FRIDAY_API_TOKEN="design-preview";<\/script>/,'') // Match the synthetic preview marker, never a credential. # pragma: allowlist secret
    .replace(/<style>:root\{--friday-safe-bottom:26px!important\}[\s\S]*?<div class="fx-preview-label" role="note">Design preview · Sample data · No live actions<\/div>/,'');
  assert.notEqual(stripped,served,'The actual synthetic preview marker is required');
  assert.equal(hash(stripped),hash(stored),'Preview index must match this worktree apart from its known fixture marker');
  result.assets['index.html']=hash(stored);
  const files=new Set([...stored.matchAll(/<(?:script|link)\b[^>]*\b(?:src|href)="(\/static\/[^"?#]+\.(?:js|css))"/g)].map(match=>match[1]));
  assert.ok(files.has('/static/friday_holographic_workspace.js')&&files.has('/static/friday_display_style.js'),'Required scene and style assets must be present');
  for(const file of files){
    const local=fs.readFileSync(path.join(root,file.slice(1))),remote=await getLocal(file);
    assert.equal(hash(remote),hash(local),'Served asset mismatch: '+file);result.assets[file.slice(1)]=hash(local);
  }
  const health=JSON.parse((await getLocal('/api/health')).toString('utf8'));
  assert.equal(health.preview,true,'Preview health must identify itself');assert.equal(health.model_ready,false,'No model may run in the capture fixture');
  const settings=JSON.parse((await getLocal('/api/settings')).toString('utf8')).settings;
  assert.equal(settings?.model_routing?.mode,'local_only');assert.equal(settings?.voice_enabled,false);
  assert.equal(settings?.head_tracking_enabled,false);assert.equal(settings?.hand_tracking_enabled,false);
  result.fixture={preview:true,modelReady:false,devicesEnabled:false};writeEvidence();
}
const caseId=(display,index,size)=>display+'-'+String(index+1).padStart(2,'0')+'-'+size.name;
async function withPage(viewport,row,action){
  gate();let context,page;
  try{
    await bounded((async()=>{
    context=await bounded(browser.newContext({viewport:{width:viewport.width,height:viewport.height},reducedMotion:'reduce',serviceWorkers:'block',permissions:[]}),10000,'Page context creation');activeContext=context;
    context.setDefaultTimeout(8000);context.setDefaultNavigationTimeout(18000);
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin!==base.origin){row.network.external.push({origin:url.origin,type:request.resourceType()});return route.abort('blockedbyclient');}
      if(!['GET','HEAD'].includes(request.method())){
        row.network.blockedWrites.push({method:request.method(),path:url.pathname});
        return route.fulfill({status:409,json:{status:'blocked',message:'Avatar capture does not perform saved or outward actions.'}});
      }
      return route.continue();
    });
    await context.addInitScript(()=>{
      if(window.top===window)try{
        localStorage.setItem('friday.display-style.v1','simple');
        localStorage.setItem('friday_holographic_workspace_v1',JSON.stringify({mode:'balanced',arrangement:'companion',motion:'off'}));
      }catch(_){/* Initial opaque documents have no browser storage. */}
      window.__avatarDeviceRequests=0;window.__avatarSocketRequests=0;
      const refuse=()=>{window.__avatarDeviceRequests++;return Promise.reject(new DOMException('Devices are disabled during avatar capture','NotAllowedError'));};
      if(navigator.mediaDevices){navigator.mediaDevices.getUserMedia=refuse;navigator.mediaDevices.getDisplayMedia=refuse;}
      window.WebSocket=class{constructor(){window.__avatarSocketRequests++;throw new DOMException('Sockets are disabled during avatar capture','SecurityError');}};
      window.RTCPeerConnection=class{constructor(){window.__avatarSocketRequests++;throw new DOMException('Peer connections are disabled during avatar capture','SecurityError');}};
    });
    page=await bounded(context.newPage(),10000,'Page creation');row.pageErrors=[];
    page.on('pageerror',error=>row.pageErrors.push(error.message));
    page.on('response',response=>{if(response.status()>=400){const url=new URL(response.url());row.network.httpErrors.push({path:url.pathname,status:response.status()});}});
    context.on('page',other=>{if(other!==page)void other.close().catch(()=>{});});
    await bounded((async()=>{
      await page.goto(base.href,{waitUntil:'domcontentloaded'});
      assert.equal(await page.evaluate(()=>window.__FRIDAY_PREVIEW__),true);
      await page.waitForFunction(()=>window.fridayVibe?.getWorkspaceSceneState?.().ready&&window.FridayDisplayStyle&&window.FridayHolographicWorkspace,null,{timeout:20000});
      await action(page);
      assert.deepEqual(row.pageErrors,[],'Avatar rendering must remain free of application errors');
      const requests=await page.evaluate(()=>({devices:window.__avatarDeviceRequests,sockets:window.__avatarSocketRequests}));
      row.requests=requests;assert.equal(requests.devices,0,'Capture attempted to activate a device');
      assert.equal(requests.sockets,0,'Capture attempted to open a socket session');
    })(),Math.max(1,Math.min(80000,deadline-Date.now())),'Avatar '+row.id);
    })(),Math.max(1,Math.min(80000,deadline-Date.now())),'Avatar setup and capture '+row.id);
  }catch(error){
    row.status='failed';row.error=error.message;
    if(!page)stopping=true;
    if(page&&!page.isClosed()&&!row.image)try{
      const image=row.id+'-failed.png';await bounded(page.screenshot({path:path.join(output,image),animations:'disabled',timeout:6000}),7000,'Failure frame');row.image=image;
    }catch(captureError){row.captureError=captureError.message;}
    throw error;
  }finally{
    if(context){
      try{await bounded(context.close(),10000,'Page context cleanup');if(activeContext===context)activeContext=null;}
      catch(error){stopping=true;row.cleanupError=error.message;throw error;}
    }
  }
}
async function style(page,value){
  const trigger=page.locator('button[aria-label^="Display style:"]');
  if(!await trigger.isVisible())await page.getByRole('button',{name:'More Friday controls',exact:true}).click();
  await trigger.click();
  const dialog=page.getByRole('dialog',{name:'Display style',exact:true});
  await dialog.getByRole('radio',{name:value==='simple'?'Simple':'Classic',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.FridayDisplayStyle.get()),{timeout:8000}).toBe(value);
  if(await dialog.isVisible())await page.keyboard.press('Escape');
  const pullout=page.getByRole('dialog',{name:'Friday controls',exact:true});
  if(await pullout.isVisible())await page.keyboard.press('Escape');
}
async function measure(page){
  return page.evaluate(()=>{
    const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
    const visible=node=>node.getClientRects().length&&getComputedStyle(node).visibility!=='hidden'&&!node.closest('[hidden],[inert]');
    const api=window.FridayHolographicWorkspace,caption=document.querySelector('.friday-avatar-caption'),canvas=document.getElementById('friday-scene-canvas');
    return {scene:fridayVibe.getWorkspaceSceneState(),holo:api.state,layout:api.layoutRect,viewport:{x:0,y:0,w:innerWidth,h:innerHeight},
      canvas:canvas?{rect:rect(canvas),width:canvas.width,height:canvas.height,visible:!!visible(canvas),contextLost:window.__fridayRenderer?.getContext?.().isContextLost()}:null,
      caption:caption&&visible(caption)?{rect:rect(caption),text:caption.textContent.trim().slice(0,160)}:null,
      surfaces:[...document.querySelectorAll('.fx-main,[data-friday-spatial-surface],[data-friday-overlay="dialog"],[data-friday-overlay="menu"],.top-bar')].filter(visible).map(node=>({selector:node.className,rect:rect(node)}))};
  });
}
function assertGeometry(row){
  const state=row.measurement,finite=r=>r&&['x','y','w','h'].every(k=>Number.isFinite(r[k]))&&r.w>0&&r.h>0;
  const inside=(a,b)=>a.x>=b.x-2&&a.y>=b.y-2&&a.x+a.w<=b.x+b.w+2&&a.y+a.h<=b.y+b.h+2;
  const overlap=(a,b)=>Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>2&&Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>2;
  assert.ok(state.scene.ready&&state.canvas?.visible&&state.canvas.contextLost===false,'The actual WebGL scene must remain visible and ready');
  assert.ok(finite(state.canvas.rect)&&inside(state.canvas.rect,state.viewport),'Scene canvas must remain in the viewport');
  assert.equal(state.scene.handTracking,false,'Hand tracking must remain off');assert.equal(state.scene.head.seen,false,'No real or synthetic face may drive this visual comparison');
  if(row.display==='classic'){
    row.fitAssessment='Visual review required: Classic does not expose projected-avatar bounds';return;
  }
  const bounds=state.holo.projectedAvatarBounds,stage=state.layout.stage,frame=state.layout.stageFrame;
  assert.ok(finite(bounds)&&finite(stage)&&finite(frame),'Simple must provide actual projected-avatar and reserved-frame measurements');
  row.fill=Math.max(bounds.w/stage.w,bounds.h/stage.h);
  assert.ok(inside(stage,state.viewport)&&inside(frame,state.viewport),'Avatar stage and caption frame must remain inside the viewport');
  assert.ok(inside(bounds,stage),'Projected avatar must fit its reserved stage');
  assert.ok(row.fill>.75,'The avatar must fill more than 75% of at least one reserved-stage dimension');
  assert.ok(state.caption?.text&&inside(state.caption.rect,frame),'Avatar state caption must remain visible in the reserved frame');
  assert.ok(!overlap(bounds,state.caption.rect),'Projected avatar must not cover its state caption');
  for(const surface of state.surfaces)assert.ok(!overlap(surface.rect,frame),'Surface covers the avatar or caption: '+surface.selector);
  row.fitAssessment='Automated Simple projection checks passed; appearance, view boxes and visual caption legibility still require review';
}
async function runCase(display,form,size){
  gate();const row={id:caseId(display,form.index,size),display,form:form.name,formId:form.id,index:form.index,size,status:'running',visualReview:'pending',network:{external:[],blockedWrites:[],httpErrors:[]}};
  result.cases.push(row);writeEvidence();
  try{
    await withPage(size,row,async page=>{
      const forms=await page.evaluate(()=>fridayVibe.getStructures().map((form,index)=>({index,id:form.id,name:form.name})));
      assert.deepEqual(forms,result.forms,'The actual scene inventory changed during comparison');
      await style(page,display);await page.evaluate(index=>fridayVibe.setStructure(index),form.index);
      await page.waitForFunction(index=>{const state=fridayVibe.getWorkspaceSceneState();return state.ready&&state.scene.index===index&&state.transitionProgress>=.999;},form.index,{timeout:60000});
      if(display==='simple')await page.waitForFunction(()=>{
        const api=window.FridayHolographicWorkspace,bounds=api.state.projectedAvatarBounds;
        return !api.state.moving&&bounds&&bounds.w>0&&bounds.h>0;
      },null,{timeout:15000});
      await page.waitForTimeout(800);row.measurement=await measure(page);
      assert.equal(row.status,'running','Capture case has ended');
      const image=row.id+'.png';await page.screenshot({path:path.join(output,image),animations:'disabled',timeout:10000});
      assert.equal(row.status,'running','Capture case has ended');row.image=image;assertGeometry(row);
    });
    row.status='passed';
  }catch(error){row.status='failed';row.error=row.error||error.message;}
  finally{writeEvidence();}
}
async function main(){
  assert.ok(outputBase,'Set FRIDAY_AVATAR_PROOFS outside the repository');
  assert.ok(Number.isFinite(timeout)&&timeout>=60000&&timeout<=3600000,'Capture deadline must be 60–3600 seconds');
  const resolved=path.resolve(outputBase),relative=path.relative(root,resolved);
  assert.ok(relative.startsWith('..'+path.sep)||path.isAbsolute(relative),'Keep screenshots outside the repository');
  fs.mkdirSync(resolved,{recursive:true});
  const actual=fs.realpathSync(resolved),actualRelative=path.relative(fs.realpathSync(root),actual);
  assert.ok(actualRelative.startsWith('..'+path.sep)||path.isAbsolute(actualRelative),'Capture directory must not resolve into the repository');
  output=path.join(actual,new Date().toISOString().replace(/[:.]/g,'-')+'-'+randomUUID().slice(0,8));fs.mkdirSync(output);writeEvidence();
  try{
    gate();await verifyPreview();gate();
    const playwright=require('@playwright/test');expect=playwright.expect;
    const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
    assert.ok(typeof executablePath==='string'&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select an explicit installed dedicated test browser');
    result.browser={executablePath,sha256:hash(fs.readFileSync(executablePath))};writeEvidence();
    server=await playwright.chromium.launchServer({executablePath,headless:true,timeout:25000});
    browser=await playwright.chromium.connect(server.wsEndpoint(),{timeout:15000});
    result.browser.version=browser.version();
    result.discovery={id:'inventory',network:{external:[],blockedWrites:[],httpErrors:[]}};
    await withPage(sizes[0],result.discovery,async page=>{
      const forms=await page.evaluate(()=>fridayVibe.getStructures().map((form,index)=>({index,id:form.id,name:form.name})));
      assert.ok(forms.length>0&&forms.length<=64,'Scene inventory must be finite and nonempty');
      assert.ok(forms.every(form=>typeof form.id==='string'&&form.id.length>0&&typeof form.name==='string'&&form.name.length>0),'Actual scene forms need stable IDs and labels');
      assert.equal(new Set(forms.map(form=>form.id)).size,forms.length,'Scene form IDs must be unique');result.forms=forms;
    });
    result.planned=sizes.flatMap(size=>result.forms.flatMap(form=>displays.map(display=>caseId(display,form.index,size))));
    result.status='running';writeEvidence();
    for(const size of sizes)for(const form of result.forms)for(const display of displays)await runCase(display,form,size);
    result.status=result.cases.some(row=>row.status!=='passed')?'failed':'passed';
  }catch(error){result.status='failed';result.error=error.message;}
  finally{
    stopping=true;
    if(activeContext)try{await bounded(activeContext.close(),10000,'Final page cleanup');result.cleanup.context=true;}catch(error){result.cleanup.contextError=error.message;}
    if(browser)try{await bounded(browser.close(),10000,'Browser cleanup');result.cleanup.browser=true;}catch(error){result.cleanup.browserError=error.message;}
    if(server)try{await bounded(server.close(),10000,'Browser server cleanup');result.cleanup.server=true;}catch(error){
      result.cleanup.serverError=error.message;
      try{await bounded(server.kill(),10000,'Owned browser kill');result.cleanup.killed=true;}catch(killError){result.cleanup.killError=killError.message;}
    }
    if(result.cleanup.contextError||result.cleanup.killError)result.status='failed';result.finishedAt=new Date().toISOString();writeEvidence();
  }
  process.stdout.write(JSON.stringify({status:result.status,forms:result.forms.length,cases:result.cases.length,failed:result.cases.filter(row=>row.status==='failed').map(row=>row.id),unvisited:result.unvisited.length,visualReview:'pending',output})+'\n');
  if(result.status!=='passed')process.exitCode=1;
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
