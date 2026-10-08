/* Render the actual isolated preview, without replacing its API responses.
 * Frames require human review. Shell/focus checks are not widget, provider,
 * physical device or real-model verification. Run under the shared check lease.
 */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const http=require('node:http'),{createHash}=require('node:crypto');
const {chromium,expect}=require('@playwright/test');
const root=path.resolve(__dirname,'..');
const base=new URL(process.env.FRIDAY_DESIGN_URL||'http://127.0.0.1:3194/');
const outputBase=path.resolve(process.env.FRIDAY_SURFACE_PROOFS||path.join(root,'../../outputs/friday-interactions/living-surfaces'));
const runId=new Date().toISOString().replace(/[:.]/g,'-'),output=path.join(outputBase,runId);
const views=['day','projects','explore','activity','workspaces'];
const sizes=(process.env.FRIDAY_SURFACE_SIZES||'1600x1000,390x844,568x320').split(',').map(value=>{
  const match=/^(\d{3,4})x(\d{3,4})$/.exec(value.trim());assert.ok(match,'Use comma-separated WIDTHxHEIGHT sizes');
  const width=Number(match[1]),height=Number(match[2]);assert.ok(width>=320&&width<=2560&&height>=300&&height<=1600,'Viewport outside capture bounds');
  return {width,height};
});
const timeout=Number(process.env.FRIDAY_SURFACE_TIMEOUT_SECONDS||3300)*1000;
const result={schema:1,startedAt:new Date().toISOString(),status:'not-run',scope:'Actual preview shell layout and sampled keyboard reachability',
  limits:['Synthetic in-memory fixtures only; no API handler or model execution proof.','A saved frame still needs human visual review.',
    'No microphone, camera, voice, hand or real face tracking is activated.','Workspace-specific CRUD, provider connections and every widget state require separate checks.',
    'Process thread uses an existing preview task; no recorded reasoning fixture or physical orb click is claimed.'],
  viewports:sizes,registry:[],held:[],assets:{},cases:[],cleanup:{}};
let server,browser,activeContext,stopping=false;
const deadline=Date.now()+timeout;
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
function bounded(promise,ms,label){
  let timer;return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label+' timed out')),ms);})]).finally(()=>clearTimeout(timer));
}
function gate(){
  assert.ok(!stopping&&Date.now()<deadline,'Capture deadline reached');
  const lock=process.env.FRIDAY_SURFACE_SUITE_LOCK,holder=process.env.FRIDAY_CHECK_HOLDER;
  assert.ok(lock&&holder,'Run under the parent-owned shared check lease; provide FRIDAY_SURFACE_SUITE_LOCK');
  assert.equal(fs.readFileSync(lock,'utf8').split(/\r?\n/)[0],'holder: '+holder,'Another check owns the shared lease');
  assert.ok(os.freemem()>=6*2**30,'At least 6 GiB free is required');
}
function writeEvidence(){
  result.finishedAt=new Date().toISOString();
  result.unvisited=(result.planned||[]).filter(id=>!result.cases.some(row=>row.id===id&&row.status!=='running'));
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(result,null,2));
  const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const rows=result.cases.map(row=>'<article><h2>'+esc(row.id)+' · '+esc(row.status)+'</h2><p>'+esc(row.error||row.scope||'')+'</p>'+
    (row.image?'<a href="'+esc(row.image)+'"><img loading="lazy" src="'+esc(row.image)+'" alt="'+esc(row.id)+'"></a>':'')+'</article>').join('');
  fs.writeFileSync(path.join(output,'index.html'),'<!doctype html><meta charset="utf-8"><title>Friday surface capture</title><style>body{background:#0b101b;color:#ecf3ff;font:16px/1.5 system-ui;margin:30px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}article{padding:16px;background:#141e2e;border:1px solid #334057;border-radius:12px}h2{font-size:16px}img{max-width:100%;height:auto}p{overflow-wrap:anywhere}</style><h1>Friday surface capture</h1><p>'+esc(result.status)+' · Automated bounds and sampled keyboard checks. All frames require human visual review. Synthetic preview data only.</p><main>'+rows+'</main>');
}
function getLocal(relative){
  const url=new URL(relative,base);assert.equal(url.origin,base.origin);
  return new Promise((resolve,reject)=>{
    let req,timer;const done=(error,value)=>{clearTimeout(timer);error?reject(error):resolve(value);};
    req=http.get(url,res=>{
      if(res.statusCode!==200){res.resume();done(new Error('Preview GET '+url.pathname+' returned '+res.statusCode));return;}
      let length=0;const chunks=[];
      res.on('data',chunk=>{length+=chunk.length;if(length>14*1024*1024)req.destroy(new Error('Preview response exceeds capture limit'));else chunks.push(chunk);});
      res.on('end',()=>done(null,Buffer.concat(chunks)));res.on('error',done);
    });
    req.on('error',done);timer=setTimeout(()=>req.destroy(new Error('Preview GET deadline')),12000);
  });
}
async function verifyPreview(){
  assert.equal(base.protocol,'http:');assert.ok(['127.0.0.1','localhost'].includes(base.hostname)&&base.port&&base.port!=='3000','Use the explicit isolated loopback preview, never live Friday');
  assert.equal(base.pathname,'/');assert.ok(!base.search&&!base.hash&&!base.username&&!base.password,'Use the preview root URL');
  const stored=fs.readFileSync(path.join(root,'index.html'),'utf8'),served=(await getLocal('/')).toString('utf8');
  const stripped=served.replace(/<script>window\.__FRIDAY_PREVIEW__=true;window\.__FRIDAY_API_TOKEN="design-preview";<\/script>/,'') // Match the synthetic preview marker, never a credential. # pragma: allowlist secret
    .replace(/<style>:root\{--friday-safe-bottom:26px!important\}[\s\S]*?<div class="fx-preview-label" role="note">Design preview · Sample data · No live actions<\/div>/,'');
  assert.notEqual(stripped,served,'The actual synthetic preview marker is required');
  assert.equal(hash(stripped),hash(stored),'Preview index must match this worktree, apart from its known fixture marker');
  result.assets['index.html']=hash(stored);
  for(const file of ['static/workspace_registry.js','static/friday_experience.js','static/friday_holographic_workspace.js','static/friday_appearance.js']){
    const local=fs.readFileSync(path.join(root,file)),remote=await getLocal('/'+file);
    assert.equal(hash(remote),hash(local),'Served asset mismatch: '+file);result.assets[file]=hash(local);
  }
  const catalog=fs.readFileSync(path.join(root,'static/workspace_registry.js'),'utf8').match(/\/\*BEGIN JSON\*\/([\s\S]+?)\/\*END JSON\*\//);
  assert.ok(catalog,'Registry JSON markers are present');
  const entries=JSON.parse(catalog[1]).workspaces.filter(w=>w.boundary?.kind==='native');
  assert.ok(entries.length&&entries.every(w=>/^[a-z0-9_-]+$/.test(w.id)&&typeof w.label==='string'));
  const health=JSON.parse((await getLocal('/api/health')).toString('utf8'));
  assert.equal(health.preview,true,'Preview health must identify itself');assert.equal(health.model_ready,false,'No model may run in the capture fixture');
  const settings=JSON.parse((await getLocal('/api/settings')).toString('utf8')).settings;
  assert.equal(settings?.model_routing?.mode,'local_only');assert.equal(settings?.voice_enabled,false);
  assert.equal(settings?.head_tracking_enabled,false);assert.equal(settings?.hand_tracking_enabled,false);
  const enabled=entries.filter(w=>!w.held||settings.held_features?.[w.held]===true),held=entries.filter(w=>!enabled.includes(w));
  result.registry=enabled.map(({id,label})=>({id,label}));result.held=held.map(({id,label,held})=>({id,label,gate:held}));
  result.fixture={preview:true,modelReady:false,devicesEnabled:false};return {enabled,held};
}
async function settle(page){
  await page.waitForFunction(()=>{
    const api=window.FridayHolographicWorkspace,box=api?.state?.projectedAvatarBounds;
    if(!api?.stageRect||api.state.moving||!box||box.w<=8||box.h<=8)return false;
    const now=performance.now(),key=JSON.stringify(api.layoutRect),prior=window.__surfaceCaptureSettled;
    if(!prior||prior.key!==key){window.__surfaceCaptureSettled={key,at:now};return false;}
    return now-prior.at>=200;
  },null,{timeout:20000,polling:'raf'});
}
async function geometry(page,scope){
  const measured=await scope.evaluate(element=>{
    const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
    const visible=node=>node.getClientRects().length&&getComputedStyle(node).visibility!=='hidden'&&!node.closest('[hidden],[inert]');
    const api=window.FridayHolographicWorkspace,layout=api?.layoutRect,state=api?.state;
    const caption=document.querySelector('.friday-avatar-caption');
    return {viewport:{x:0,y:0,w:innerWidth,h:innerHeight},target:rect(element),stage:api?.stageRect,content:layout?.content,
      protectedStage:layout?.stageFrame||api?.stageRect,avatar:state?.projectedAvatarBounds,layout:layout?.layout,
      caption:caption&&visible(caption)?{rect:rect(caption),text:caption.textContent.trim().slice(0,160)}:null,
      document:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},
      overflow:{x:element.scrollWidth-element.clientWidth,y:element.scrollHeight-element.clientHeight,mode:getComputedStyle(element).overflowY},
      protected:[...document.querySelectorAll('[data-friday-spatial-surface],[data-friday-overlay="dialog"],[data-friday-overlay="menu"],.fx-main,.fx-switcher-popover,.ws-tools-menu,.fr-holo-dialog[open],.friday-style-dialog[open],.friday-scene-menu,.friday-topbar-pullout[data-open="true"]')].filter(visible).map(node=>({selector:node.className,rect:rect(node)})),
      errors:[...document.querySelectorAll('.friday-surface-error')].filter(visible).map(node=>node.textContent.slice(0,220))};
  });
  const finite=rect=>rect&&['x','y','w','h'].every(key=>Number.isFinite(rect[key]))&&rect.w>0&&rect.h>0;
  const inside=(a,b)=>a.x>=b.x-2&&a.y>=b.y-2&&a.x+a.w<=b.x+b.w+2&&a.y+a.h<=b.y+b.h+2;
  const overlap=(a,b)=>Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>2&&Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>2;
  assert.ok(finite(measured.stage)&&finite(measured.content)&&finite(measured.avatar),'The actual projected avatar and layout must be measurable');
  assert.ok(inside(measured.target,measured.viewport),'Target escapes the viewport: '+JSON.stringify(measured.target));
  assert.ok(!overlap(measured.target,measured.protectedStage),'Captured surface covers the avatar or its state caption');
  assert.ok(inside(measured.stage,measured.viewport),'Avatar stage escapes the viewport');
  assert.ok(inside(measured.avatar,measured.stage),'Actual projected avatar escapes its reserved stage: '+JSON.stringify(measured.avatar));
  assert.ok(inside(measured.protectedStage,measured.viewport),'Avatar and caption reservation escapes the viewport');
  assert.ok(!overlap(measured.protectedStage,measured.content),'Avatar/caption and content rectangles overlap');
  assert.ok(measured.caption?.text&&inside(measured.caption.rect,measured.protectedStage),'Avatar state caption must remain visible in its reserved frame');
  assert.ok(measured.document.width<=measured.viewport.w+2&&measured.document.height<=measured.viewport.h+2,'The desktop itself must not scroll');
  for(const surface of measured.protected){
    assert.ok(inside(surface.rect,measured.viewport),'Protected surface escapes viewport: '+surface.selector);
    assert.ok(!overlap(surface.rect,measured.protectedStage),'Protected surface covers avatar or caption: '+surface.selector);
  }
  assert.deepEqual(measured.errors,[],'Native workspace error boundary is visible');return measured;
}
async function keyboardSample(page,scope){
  const candidates=scope.locator('button,input,textarea,select,a[href],summary,[tabindex="0"]');
  const indices=await candidates.evaluateAll(nodes=>nodes.flatMap((node,i)=>node.getClientRects().length&&!node.disabled&&!node.closest('[hidden],[inert]')&&getComputedStyle(node).visibility!=='hidden'?[i]:[]));
  assert.ok(indices.length,'Surface has no reachable native control');
  const arrowMenu=await scope.evaluate(element=>element.getAttribute('role')==='menu');
  await candidates.nth(indices[0]).focus();
  const samples=[];
  for(let step=0;step<Math.min(5,indices.length);step++){
    const sample=await scope.evaluate(parent=>{
      const el=document.activeElement,r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2,hit=document.elementFromPoint(x,y);
      return {inside:parent.contains(el),tag:el.tagName,label:(el.getAttribute('aria-label')||el.getAttribute('title')||el.textContent||'').trim().slice(0,100),
        bounds:{x:r.x,y:r.y,w:r.width,h:r.height},visible:r.width>0&&r.height>0&&r.x>=-2&&r.y>=-2&&r.right<=innerWidth+2&&r.bottom<=innerHeight+2,
        hit:!!hit&&(el===hit||el.contains(hit)||hit.contains(el)),disabled:!!el.disabled||!!el.closest('[inert]')};
    });
    if(!sample.inside)break;
    assert.ok(sample.visible&&sample.hit&&!sample.disabled,'Keyboard target is clipped or covered: '+JSON.stringify(sample));
    samples.push(sample);if(step+1<Math.min(5,indices.length))await page.keyboard.press(arrowMenu?'ArrowDown':'Tab');
  }
  assert.ok(samples.length,'Keyboard focus cannot enter this surface');return samples;
}
async function topControl(page,selector){
  const control=page.locator(selector).first();
  if(!await control.isVisible()){
    const more=page.getByRole('button',{name:'More Friday controls',exact:true});
    await expect(more).toBeVisible();await more.focus();await page.keyboard.press('Enter');
  }
  await expect(control).toBeVisible();return control;
}
async function openSwitcher(page){
  const trigger=await topControl(page,'.fx-switcher-trigger');await trigger.focus();await page.keyboard.press('Enter');
  const popup=page.getByRole('dialog',{name:'Switch workspace',exact:true});await expect(popup).toBeVisible();return {trigger,popup};
}
async function openWorkspace(page,entry){
  const {popup}=await openSwitcher(page),search=popup.getByRole('searchbox',{name:'Find a workspace',exact:true});
  await search.fill(entry.label);await search.press('ArrowDown');
  const options=popup.locator('.fx-switcher-option'),names=await options.locator('strong').allTextContents();
  const index=names.indexOf(entry.label);assert.ok(index>=0,'Registry workspace absent from switcher: '+entry.id);
  for(let i=0;i<index;i++)await page.keyboard.press('ArrowDown');
  await expect(options.nth(index)).toBeFocused();await page.keyboard.press('Enter');await expect(popup).toHaveCount(0);
  const surface=page.locator('.fwin[data-friday-workspace="'+entry.id+'"]');await expect(surface).toBeVisible();return surface;
}
async function toolsMenu(page,kind='customize'){
  const surface=await openWorkspace(page,{id:'library',label:'Library'}),trigger=surface.getByRole('button',{name:kind==='more'?'More tools for Library':'Customize Library',exact:true});
  await trigger.focus();await page.keyboard.press('ArrowDown');
  const menu=page.getByRole('menu',{name:kind==='more'?'More tools for Library':'Customize Library',exact:true});
  await expect(menu).toBeVisible();return {surface,trigger,menu};
}
async function capture(row,page,scope){
  await settle(page);row.geometry=await geometry(page,scope);
  assert.equal(row.status,'running','Capture case has ended');
  const image=row.id+'.png';await page.screenshot({path:path.join(output,image),animations:'disabled',timeout:10000});
  assert.equal(row.status,'running','Capture case has ended');row.image=image;
  row.keyboard=await keyboardSample(page,scope);
  await settle(page);row.geometryAfterFocus=await geometry(page,scope);
  row.observedStates=await scope.locator('[role="status"],[role="alert"]').allTextContents();
  row.observedStates=row.observedStates.slice(0,8).map(value=>value.trim().slice(0,220));
}
async function runCase(name,viewport,action){
  gate();const row={id:name+'-'+viewport.width+'x'+viewport.height,status:'running',scope:'Rendered geometry and sampled keyboard navigation',network:{external:[],blockedWrites:[],httpErrors:[]}};
  result.cases.push(row);writeEvidence();let context,page;
  try{
    context=await bounded(browser.newContext({viewport,reducedMotion:'reduce',serviceWorkers:'block',permissions:[]}),10000,'Page context creation');activeContext=context;
    context.setDefaultTimeout(8000);context.setDefaultNavigationTimeout(18000);
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin!==base.origin){row.network.external.push({origin:url.origin,type:request.resourceType()});return route.abort('blockedbyclient');}
      if(!['GET','HEAD'].includes(request.method())){
        let body;try{body=request.postDataJSON();}catch(_){}
        const preview=request.method()==='POST'&&url.pathname==='/api/workspace/library/appearance'&&body?.apply===false&&
          Object.keys(body).sort().join(',')==='apply,expected_revision,patch'&&JSON.stringify(body.patch)==='{"note":"Synthetic appearance acceptance draft"}';
        if(!preview){row.network.blockedWrites.push({method:request.method(),path:url.pathname});return route.fulfill({status:409,json:{status:'blocked',message:'This rendered acceptance capture does not perform saved or outward actions.'}});}
      }
      return route.continue();
    });
    await context.addInitScript(()=>{
      if(window.top===window)try{
        localStorage.setItem('friday.display-style.v1','simple');
        localStorage.setItem('friday_holographic_workspace_v1',JSON.stringify({mode:'balanced',arrangement:'companion',motion:'off'}));
      }catch(_){/* Initial opaque documents have no browser storage. */}
      window.__surfaceDeviceRequests=0;
      const refuse=()=>{window.__surfaceDeviceRequests++;return Promise.reject(new DOMException('Devices are disabled during rendered acceptance','NotAllowedError'));};
      if(navigator.mediaDevices){navigator.mediaDevices.getUserMedia=refuse;navigator.mediaDevices.getDisplayMedia=refuse;}
      window.WebSocket=class{constructor(){throw new DOMException('Socket sessions are disabled during rendered acceptance','SecurityError');}};
    });
    page=await bounded(context.newPage(),10000,'Page creation');const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('response',response=>{if(response.status()>=400){const url=new URL(response.url());row.network.httpErrors.push({path:url.pathname,status:response.status()});}});
    context.on('page',other=>{if(other!==page)void other.close();});
    await bounded((async()=>{
      await page.goto(base.href,{waitUntil:'domcontentloaded'});
      await expect(page.locator('.fx-main[data-view="day"]')).toBeVisible();
      assert.equal(await page.evaluate(()=>window.__FRIDAY_PREVIEW__),true);
      await page.waitForFunction(()=>typeof window.fridayOpenWorkspace==='function'&&window.FridayHolographicWorkspace?.stageRect);
      await action(page,row);assert.deepEqual(errors,[],'Uncaught errors in actual preview');
      assert.equal(await page.evaluate(()=>window.__surfaceDeviceRequests),0,'A capture action attempted to activate a device');
    })(),Math.max(1,Math.min(65000,deadline-Date.now())),'Surface '+row.id);
    row.status='passed';
  }catch(error){
    row.status='failed';row.error=error.message;
    if(!page)stopping=true; // Do not admit a second context after uncertain creation.
    if(page&&!page.isClosed())try{const image=row.id+'-failed.png';await bounded(page.screenshot({path:path.join(output,image),animations:'disabled',timeout:6000}),7000,'Failure frame');row.image=image;}catch(captureError){row.captureError=captureError.message;}
  }finally{
    if(context){await bounded(context.close(),10000,'Page context cleanup');if(activeContext===context)activeContext=null;}
    writeEvidence();
  }
}
async function main(){
  assert.ok(Number.isFinite(timeout)&&timeout>=60000&&timeout<=3600000,'Capture deadline must be 60–3600 seconds');
  assert.ok(sizes.length>=1&&sizes.length<=6,'Use one to six viewports');
  assert.equal(new Set(sizes.map(size=>size.width+'x'+size.height)).size,sizes.length,'Repeated viewports would overwrite evidence');
  const relative=path.relative(root,outputBase);assert.ok(relative.startsWith('..'+path.sep)||path.isAbsolute(relative),'Capture output must be outside the repository');
  fs.mkdirSync(output,{recursive:true});writeEvidence();
  try{
    gate();const {enabled,held}=await verifyPreview();
    const shared=['menu-workspace-switcher','menu-display-style','menu-friday-controls','menu-command-palette','keyboard-shortcuts',
      'menu-workspace-customize','menu-workspace-more','salon-draft','appearance-preview','home-card-editor','home-card-reader','home-card-sources',
      'process-thread','chat-beside-home','scene-and-depth','menu-original-scene'];
    result.planned=sizes.flatMap(size=>enabled.map(entry=>'workspace-'+entry.id).concat(views.map(view=>'desktop-'+view),shared).map(name=>name+'-'+size.width+'x'+size.height));
    result.status='running';writeEvidence();gate();
    const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
    assert.ok(typeof executablePath==='string'&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select an explicit installed dedicated test browser');
    result.browser={executablePath,sha256:hash(fs.readFileSync(executablePath))};writeEvidence();
    server=await chromium.launchServer({executablePath,headless:true,timeout:25000});
    browser=await chromium.connect(server.wsEndpoint(),{timeout:15000});
    result.browser.version=browser.version();
    for(const viewport of sizes){
      for(const entry of enabled)await runCase('workspace-'+entry.id,viewport,async(page,row)=>capture(row,page,await openWorkspace(page,entry)));
      for(const view of views)await runCase('desktop-'+view,viewport,async(page,row)=>{
        if(view!=='day')await page.evaluate(view=>dispatchEvent(new CustomEvent('friday:desktop-view',{detail:{view}})),view);
        const surface=page.locator('.fx-main[data-view="'+view+'"]');await expect(surface).toBeVisible();
        if(view==='day')await expect(surface.getByRole('button',{name:'Add card',exact:true})).toBeEnabled();
        if(view==='workspaces')for(const entry of held)await expect(surface.locator('.fx-workspace-card').filter({hasText:entry.label})).toHaveCount(0);
        await capture(row,page,surface);
      });
      await runCase('menu-workspace-switcher',viewport,async(page,row)=>{
        const {popup}=await openSwitcher(page),names=await popup.locator('.fx-switcher-option strong').allTextContents();
        assert.deepEqual([...names].sort(),enabled.map(w=>w.label).sort(),'Switcher must offer exactly the available native registry');
        await capture(row,page,popup);await page.keyboard.press('Escape');await expect(popup).toHaveCount(0);
        const focus=await page.evaluate(()=>document.activeElement?.className||'');assert.match(focus,/fx-switcher-trigger|friday-topbar-more/);
        for(const entry of held){await page.evaluate(id=>fridayOpenWorkspace({workspace:id}),entry.id);await expect(page.locator('.fwin[data-friday-workspace="'+entry.id+'"]')).toHaveCount(0);}
      });
      await runCase('menu-display-style',viewport,async(page,row)=>{
        const trigger=await topControl(page,'.friday-style-trigger');await trigger.focus();await page.keyboard.press('Enter');
        const surface=page.getByRole('dialog',{name:'Display style',exact:true});await expect(surface).toBeVisible();
        await expect(surface.getByRole('radio',{name:'Advanced',exact:true})).toBeDisabled();await capture(row,page,surface);
        await page.keyboard.press('Escape');await expect(surface).toHaveCount(0);
      });
      await runCase('menu-friday-controls',viewport,async(page,row)=>{
        const more=page.getByRole('button',{name:'More Friday controls',exact:true});
        if(!await more.isVisible()){
          row.scope+='; controls fit inline at this viewport';await capture(row,page,page.locator('.top-bar'));return;
        }
        await more.focus();await page.keyboard.press('Enter');
        const surface=page.getByRole('dialog',{name:'Friday controls',exact:true});await expect(surface).toBeVisible();await capture(row,page,surface);
        await page.keyboard.press('Escape');await expect(surface).not.toBeVisible();await expect(more).toBeFocused();
      });
      await runCase('menu-command-palette',viewport,async(page,row)=>{
        await page.keyboard.press('Control+k');const surface=page.locator('.cmd-palette');await expect(surface).toBeVisible();
        await capture(row,page,surface);await surface.locator('input').focus();await page.keyboard.press('Escape');await expect(surface).toHaveCount(0);
      });
      await runCase('keyboard-shortcuts',viewport,async(page,row)=>{
        await page.keyboard.press('?');const surface=page.getByRole('dialog',{name:'Keyboard shortcuts',exact:true});await expect(surface).toBeVisible();
        await capture(row,page,surface);await page.keyboard.press('Escape');await expect(surface).toHaveCount(0);
      });
      for(const kind of ['customize','more'])await runCase('menu-workspace-'+kind,viewport,async(page,row)=>{
        const {menu,trigger}=await toolsMenu(page,kind);await capture(row,page,menu);
        await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(trigger).toBeFocused();
      });
      await runCase('salon-draft',viewport,async(page,row)=>{
        const {menu}=await toolsMenu(page);await menu.getByRole('menuitem',{name:'Workspace chat for Library',exact:true}).click();
        const surface=page.getByRole('dialog',{name:'Library customization',exact:true});await expect(surface).toBeVisible();
        await surface.getByRole('textbox').fill('Synthetic unsent workspace appearance draft');await capture(row,page,surface);
        await surface.getByRole('button',{name:'Close',exact:true}).click();await expect(surface.getByRole('button',{name:'Discard draft',exact:true})).toBeVisible();
        await surface.getByRole('button',{name:'Keep editing',exact:true}).click();await expect(surface.getByRole('textbox')).toHaveValue('Synthetic unsent workspace appearance draft');
        await surface.getByRole('button',{name:'Close',exact:true}).click();await surface.getByRole('button',{name:'Discard draft',exact:true}).click();await expect(surface).toHaveCount(0);
      });
      await runCase('appearance-preview',viewport,async(page,row)=>{
        const {menu}=await toolsMenu(page);await menu.getByRole('menuitem',{name:'Preview appearance of Library',exact:true}).click();
        const surface=page.getByRole('dialog',{name:'Appearance of Library',exact:true});
        await expect(surface.getByLabel('Pinned workspace note',{exact:true})).toBeEnabled();
        await surface.getByLabel('Pinned workspace note',{exact:true}).fill('Synthetic appearance acceptance draft');
        await surface.getByRole('button',{name:'Preview changes',exact:true}).click();await expect(surface.getByRole('region',{name:'Appearance preview',exact:true})).toBeVisible();
        await capture(row,page,surface);await surface.getByRole('button',{name:'Close appearance editor',exact:true}).click();
        await surface.getByRole('button',{name:'Discard draft',exact:true}).click();await expect(surface).toHaveCount(0);
      });
      for(const kind of ['editor','reader','sources'])await runCase('home-card-'+kind,viewport,async(page,row)=>{
        const board=page.getByRole('region',{name:'Home cards',exact:true});await expect(board.getByRole('button',{name:'Add card',exact:true})).toBeEnabled();
        let surface;
        if(kind==='editor'){
          await board.getByRole('button',{name:'Add card',exact:true}).click();surface=board.getByRole('form',{name:'Add to Home',exact:true});
          await surface.getByLabel('Title',{exact:true}).fill('Synthetic unsaved card');await surface.getByLabel('Details',{exact:true}).fill('A local preview draft. No card is saved by this capture.');
        }else if(kind==='reader'){
          await board.getByRole('button',{name:/^Read card:/}).first().click();surface=board.getByRole('article',{name:'Reading Home card',exact:true});
        }else{await board.getByRole('button',{name:'Sources',exact:true}).click();surface=board.locator('.fx-board-sources');}
        await expect(surface).toBeVisible();await capture(row,page,surface);
        if(kind==='editor'){
          await surface.getByRole('button',{name:'Cancel',exact:true}).click();await surface.getByRole('button',{name:'Keep editing',exact:true}).click();
          await expect(surface.getByLabel('Title',{exact:true})).toHaveValue('Synthetic unsaved card');
          await surface.getByRole('button',{name:'Cancel',exact:true}).click();await surface.getByRole('button',{name:'Discard draft',exact:true}).click();await expect(surface).toHaveCount(0);
        }
      });
      await runCase('process-thread',viewport,async(page,row)=>{
        const tasks=await page.evaluate(async()=>{const response=await apiFetch('/api/tasks');if(!response.ok)throw new Error('Preview tasks unavailable');return (await response.json()).tasks;});
        const task=tasks.find(t=>t.result&&t.status==='completed_unverified');assert.ok(task,'The existing completed-unverified preview task is required');
        await page.evaluate(task=>dispatchEvent(new CustomEvent('fridayOrbClicked',{detail:{id:task.task_id,task_id:task.task_id,label:task.name,status:task.status,sx:innerWidth/2,sy:100}})),task);
        const surface=page.getByRole('region',{name:'Process: '+task.name,exact:true});await expect(surface).toBeVisible();
        await expect(surface.getByText('No reasoning was recorded for this process.',{exact:true})).toBeVisible();
        await surface.getByText('Activity and result',{exact:true}).click();await expect(surface.getByText(task.result,{exact:true})).toBeVisible();
        await capture(row,page,surface);row.scope+='; synthetic task event, no physical orb or trace correlation proof';
        await surface.getByRole('button',{name:'Close this process panel',exact:true}).click();await expect(surface).toHaveCount(0);
      });
      await runCase('chat-beside-home',viewport,async(page,row)=>{
        const trigger=await topControl(page,'button[aria-label^="Open chat with "]');await trigger.focus();await page.keyboard.press('Enter');
        const surface=page.locator('.chat-panel.open');await expect(surface).toBeVisible();
        await expect(page.locator('.fx-day-composer')).toHaveCount(0);await capture(row,page,surface);
        row.scope+='; chat shown without voice, sending or conversation mutation';
      });
      await runCase('scene-and-depth',viewport,async(page,row)=>{
        const trigger=await topControl(page,'button[aria-label="Scene and workspace depth"]');await trigger.focus();await page.keyboard.press('Enter');
        const surface=page.getByRole('dialog',{name:'Scene & depth',exact:true});await expect(surface).toBeVisible();
        await expect(surface.getByRole('button',{name:'Enable head tracking',exact:true})).toBeVisible();
        await capture(row,page,surface);await page.keyboard.press('Escape');await expect(surface).not.toBeVisible();
      });
      await runCase('menu-original-scene',viewport,async(page,row)=>{
        const trigger=await topControl(page,'button[aria-label="Scene selection"]');await trigger.focus();await page.keyboard.press('Enter');
        const surface=page.locator('.friday-scene-menu');await expect(surface).toBeVisible();await capture(row,page,surface);
        await page.keyboard.press('Escape');await expect(surface).not.toBeVisible();
      });
    }
    result.status=result.cases.some(row=>row.status!=='passed')?'failed':'passed';
  }catch(error){result.status='failed';result.error=error.message;}
  finally{
    stopping=true;
    if(activeContext)try{await bounded(activeContext.close(),10000,'Final page cleanup');result.cleanup.context=true;}catch(error){result.cleanup.contextError=error.message;}
    if(browser)try{await bounded(browser.close(),10000,'Browser cleanup');result.cleanup.browser=true;}catch(error){result.cleanup.browserError=error.message;}
    if(server)try{await bounded(server.close(),10000,'Browser server cleanup');result.cleanup.server=true;}catch(error){
      result.cleanup.serverError=error.message;try{await bounded(server.kill(),10000,'Owned browser kill');result.cleanup.killed=true;}catch(killError){result.cleanup.killError=killError.message;}
    }
    if(result.cleanup.killError||result.cleanup.contextError)result.status='failed';writeEvidence();
  }
  process.stdout.write(JSON.stringify({status:result.status,cases:result.cases.length,failed:result.cases.filter(row=>row.status==='failed').map(row=>row.id),output})+'\n');
  if(result.status!=='passed')process.exitCode=1;
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
