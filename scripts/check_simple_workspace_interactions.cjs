'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawnSync}=require('node:child_process');
const {chromium,expect}=require('@playwright/test');
const root=path.resolve(__dirname,'..');
const base=process.env.FRIDAY_DESIGN_URL||'http://127.0.0.1:3194/';
const output=process.env.FRIDAY_INTERACTION_PROOFS;
const baseline=process.env.FRIDAY_BASELINE_REV;
(async()=>{
 assert.ok(os.freemem()>6*1073741824,'At least 6 GB free is required');
 assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname)&&new URL(base).port!=='3000','Use isolated preview');
 if(output){const rel=path.relative(root,path.resolve(output));assert.ok(rel.startsWith('..'+path.sep)||path.isAbsolute(rel),'Proof output must be outside the repository')}
 const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
 assert.ok(executablePath&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select the dedicated testing browser explicitly');
 const browser=await chromium.launch({executablePath,headless:true});
 process.stdout.write('Testing browser: '+await browser.version()+'\n');
 try{
 const page=await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let custom={library:{note:'Original workspace note'}},failChat=true,failReset=true,failClear=true,malformedReset=false,acks=[],resetCalls=0,chatHistory=[];
 let nextNote='Revised workspace note',delayNextRead=false,releaseRead,readFinished;
 if(baseline){
   const old=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':index.html'],{cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:12e6});
   assert.equal(old.status,0,old.stderr);
   // Only the document comes from the baseline; current assets and mocked APIs stay identical.
   await page.route(url=>url.origin===new URL(base).origin&&url.pathname===new URL(base).pathname,r=>r.request().resourceType()==='document'?r.fulfill({status:200,contentType:'text/html',body:old.stdout}):r.continue());
 }
 await page.addInitScript(()=>{const Native=window.EventSource;window.EventSource=class{constructor(url){if(!url.includes('/api/desktop/events'))return new Native(url);if(new URL(url,location.href).searchParams.get('kind')==='desktop')window.__desktopEvents=this}close(){}}});
 await page.route('**/api/desktop/cards',r=>r.fulfill({json:{status:'ok',cards:[]}}));
 await page.route('**/api/desktop/ack',r=>{acks.push(r.request().postDataJSON());return r.fulfill({json:{status:'ok'}})});
 await page.route('**/api/workspace/customizations',r=>r.fulfill({json:{status:'ok',customizations:custom}}));
 await page.route('**/api/workspace/library/chat',async r=>{
   if(r.request().method()==='GET'){
     const snapshot={status:'ok',chat:chatHistory.map(item=>({...item})),versions:[{id:'v1',label:'Original note'}],customization:{...custom.library}};
     if(delayNextRead){
       delayNextRead=false;
       await new Promise(resolve=>{releaseRead=resolve});
       await r.fulfill({json:snapshot});readFinished=true;return;
     }
     return r.fulfill({json:snapshot});
   }
   if(failChat)return r.fulfill({status:500,json:{status:'error',message:'Could not save request'}});
   custom.library={note:nextNote,density:'compact'};
   chatHistory.push({role:'user',text:r.request().postDataJSON().message},{role:'friday',text:'Applied the compact layout.',applied:true,revert_to:'v1'});
   return r.fulfill({json:{status:'ok',response:'Applied the compact layout.',applied:true,revert_to:'v1',customization:custom.library,versions:[{id:'v1',label:'Original note'}]}});
 });
 await page.route('**/api/workspace/library/reset',r=>{
   resetCalls++;
   if(malformedReset)return r.fulfill({status:200,json:{status:'ok'}});
   if(failReset)return r.fulfill({status:500,json:{status:'error',message:'Reset unavailable'}});
   custom.library={};return r.fulfill({json:{status:'ok',customization:{},versions:[]}});
 });
 await page.route('**/api/workspace/library/chat/clear',r=>r.fulfill({status:failClear?500:200,json:{status:failClear?'error':'ok'}}));
 await page.goto(base);await page.locator('.fx-main').waitFor();
 await page.evaluate(()=>window.fridayOpenWorkspaceChat('library','Library','',''));
 const salon=page.getByRole('dialog',{name:'Library customization',exact:true});
 await expect(salon).toBeVisible();
 const ws=page.locator('.fwin[data-friday-workspace="library"]');
 await expect(ws.getByText('Original workspace note',{exact:true})).toBeVisible();
 await salon.getByRole('button',{name:'Make this layout more compact',exact:true}).click();
 await expect(salon.getByRole('textbox')).toHaveValue('Make this layout more compact');
 await expect(salon.getByText('The request was not confirmed. Your draft is kept. Refresh workspace history before trying again.',{exact:true})).toBeVisible();
 await expect(ws.getByText('Original workspace note',{exact:true})).toBeVisible();
 await expect(salon.getByRole('button',{name:'Send',exact:true})).toBeDisabled();
 await salon.getByRole('button',{name:'Refresh workspace history',exact:true}).click();
 await expect(salon.getByRole('button',{name:'Send',exact:true})).toBeEnabled();
 failChat=false;await salon.getByRole('button',{name:'Send',exact:true}).click();
 await expect(ws.getByText('Revised workspace note',{exact:true})).toBeVisible();
 await salon.locator('button[title="Version history"]').click();
 page.on('dialog',()=>{throw new Error('The Salon must use its own protected confirmation surface')});
 const resetNotice=page.locator('#friday-toast-host > [role="status"]').filter({hasText:'Reset failed. Refresh workspace history before trying again.'});
 await salon.getByRole('button',{name:'Reset to default',exact:true}).click();
 await expect(salon.getByRole('button',{name:'Reset appearance',exact:true})).toBeVisible();
 assert.equal(resetCalls,0,'Showing a confirmation never resets the workspace');
 await salon.getByRole('button',{name:'Keep editing',exact:true}).click();
 assert.equal(resetCalls,0,'Cancelling a confirmation never resets the workspace');
 await salon.getByRole('button',{name:'Reset to default',exact:true}).click();
 await salon.getByRole('button',{name:'Reset appearance',exact:true}).click();
 await expect(resetNotice).toBeVisible();
 await expect(ws.getByText('Revised workspace note',{exact:true})).toBeVisible();
 // Each request has its own dismissible notice. Remove the first through its
 // actual UI so the next assertion proves a new error, not the earlier toast.
 await resetNotice.getByRole('button',{name:'Dismiss notification',exact:true}).click();await expect(resetNotice).toHaveCount(0);
 await salon.getByRole('button',{name:'Refresh workspace history',exact:true}).click();
 await expect(salon.getByText('Refresh workspace history to confirm the last result before continuing.',{exact:true})).toHaveCount(0);
 // HTTP success without the response contract must never clear the applied state.
 failReset=false;malformedReset=true;
 const malformedResponse=page.waitForResponse(r=>r.url().endsWith('/api/workspace/library/reset'));
 await salon.getByRole('button',{name:'Reset to default',exact:true}).click();
 await salon.getByRole('button',{name:'Reset appearance',exact:true}).click();
 await (await malformedResponse).finished();
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await expect(resetNotice).toBeVisible();
 await expect(ws.getByText('Revised workspace note',{exact:true})).toBeVisible();
 malformedReset=false;
 await salon.getByRole('button',{name:'Refresh workspace history',exact:true}).click();
 await expect(salon.getByText('Refresh workspace history to confirm the last result before continuing.',{exact:true})).toHaveCount(0);
 await salon.getByRole('button',{name:'clear history',exact:true}).click();
 await expect(page.getByText('Could not clear history. Refresh workspace history before trying again.',{exact:true})).toBeVisible();
 await expect(salon.getByText('Applied the compact layout.',{exact:true}).last()).toBeVisible();
 // A late initial history read must not replace a newer successful mutation.
 await salon.getByRole('button',{name:'Close',exact:true}).click();
 delayNextRead=true;readFinished=false;nextNote='Saved after a delayed history read';
 await page.evaluate(()=>window.fridayOpenWorkspaceChat('library','Library','',''));
 await expect(salon).toBeVisible();
 await expect.poll(()=>typeof releaseRead).toBe('function');
 await salon.getByRole('textbox').fill('Pin a newer workspace note');
 await salon.getByRole('button',{name:'Send',exact:true}).click();
 await expect(ws.getByText(nextNote,{exact:true})).toBeVisible();
 const delayedResponse=page.waitForResponse(r=>r.url().endsWith('/api/workspace/library/chat')&&r.request().method()==='GET');
 releaseRead();await (await delayedResponse).finished();
 await expect.poll(()=>readFinished).toBe(true);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await expect(ws.getByText(nextNote,{exact:true})).toBeVisible();
 await expect(salon.getByText('Applied the compact layout.',{exact:true}).last()).toBeVisible();
 // A saved mutation outside the panel (voice/history) refreshes the open workspace.
 custom.library={note:'Voice updated this note'};
 await page.evaluate(()=>window.__desktopEvents.onmessage({data:JSON.stringify({type:'workspace_customizations_changed'})}));
 await expect(ws.getByText('Voice updated this note',{exact:true})).toBeVisible();
 await salon.getByRole('textbox').fill('An unsent workspace idea');
 await salon.getByRole('button',{name:'Close',exact:true}).click();
 await expect(salon.getByRole('button',{name:'Discard draft',exact:true})).toBeVisible();
 await salon.getByRole('button',{name:'Keep editing',exact:true}).click();
 await expect(salon.getByRole('textbox')).toHaveValue('An unsent workspace idea');
 await salon.getByRole('button',{name:'Close',exact:true}).click();
 await salon.getByRole('button',{name:'Discard draft',exact:true}).click();
 // Home can retain Projects while a workspace is open. Summon must select Day as well as reveal Home.
 await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-view',{detail:{view:'projects'}})));
 await expect(page.locator('.fx-main[data-view="projects"]')).toBeAttached();
 await page.evaluate(()=>window.__desktopEvents.onmessage({data:JSON.stringify({type:'command',id:'show-home-test',actions:[{type:'landing',summon:true}]})}));
 await expect(page.locator('.fx-main[data-view="day"]')).toBeVisible();
 await expect.poll(()=>acks.find(a=>a.id==='show-home-test')?.result.landing.show).toBe(true);
 assert.deepEqual(errors,[]);
 if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'simple-home-after-voice-command.png'),animations:'disabled'})}
 console.log('PASS Salon keyboard starters, failed draft retention, apply, failed/malformed reset and clear preservation, delayed-read isolation, voice refresh and retained Projects-to-Day command');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
