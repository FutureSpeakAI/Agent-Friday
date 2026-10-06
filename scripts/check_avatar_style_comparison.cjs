/* Compare authored avatar forms across display styles without acquiring a camera. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),os=require('node:os');
const {chromium,expect}=require('@playwright/test');
const base=process.env.FRIDAY_DESIGN_URL||'http://127.0.0.1:3194/';
const out=process.env.FRIDAY_AVATAR_PROOFS,records=[];
async function style(page,value){
 const trigger=page.locator('button[aria-label^="Display style:"]');
 if(!await trigger.isVisible())await page.getByRole('button',{name:'More Friday controls',exact:true}).click();
 await trigger.click();await page.getByRole('dialog',{name:'Display style',exact:true}).getByRole('radio',{name:value==='simple'?'Simple':'Classic',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>window.FridayDisplayStyle.get())).toBe(value);
}
(async()=>{
 assert.ok(out,'Set FRIDAY_AVATAR_PROOFS outside the repository');
 const relative=path.relative(path.resolve(__dirname,'..'),path.resolve(out));
 assert.ok(relative.startsWith('..'+path.sep)||path.isAbsolute(relative),'Keep screenshots outside the repository');
 assert.ok(new URL(base).port!=='3000'&&['127.0.0.1','localhost'].includes(new URL(base).hostname),'Use isolated preview');
 assert.ok(os.freemem()>6*2**30,'At least 6 GB free is required');
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
 const page=await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/desktop/cards',r=>r.fulfill({json:{status:'ok',cards:[]}}));
 await page.goto(base);await page.waitForFunction(()=>window.fridayVibe?.getWorkspaceSceneState?.().ready);
 const forms=await page.evaluate(()=>fridayVibe.getStructures());
 async function capture(display,index,size){
   assert.ok(os.freemem()>4*2**30,'Hard memory floor during capture');
   await page.evaluate(i=>fridayVibe.setStructure(i),index);
   await page.waitForFunction(i=>fridayVibe.getWorkspaceSceneState().scene.index===i&&fridayVibe.getWorkspaceSceneState().transitionProgress>=.999,index,{timeout:60000});
   if(display==='simple')await page.waitForFunction(()=>FridayHolographicWorkspace.state.projectedAvatarBounds?.inside);
   await page.waitForTimeout(800);
   const name=display+'-'+String(index+1).padStart(2,'0')+'-'+size;
   const state=await page.evaluate(()=>({scene:fridayVibe.getWorkspaceSceneState(),holo:FridayHolographicWorkspace.state}));
   if(display==='simple'){
     const b=state.holo.projectedAvatarBounds,s=state.holo.spatial.stage;
     assert.ok(b.inside&&Math.max(b.w/s.w,b.h/s.h)>.75,name+' must fill its reserved stage');
   }
   records.push({name,form:forms[index],...state});
   await page.screenshot({path:path.join(out,name+'.png'),animations:'disabled'});
   console.log('PASS '+name);
 }
 for(const display of ['simple','classic']){
   await style(page,display);
   for(let i=0;i<forms.length;i++)await capture(display,i,'wide');
 }
 await page.setViewportSize({width:390,height:844});
 for(const display of ['simple','classic']){
   await style(page,display);
   for(const i of [9,12,14])await capture(display,i,'portrait');
 }
 assert.deepEqual(errors,[],'Avatar rendering must remain free of application errors');
 }finally{
   fs.writeFileSync(path.join(out,'comparison.json'),JSON.stringify(records,null,2));
   const rows=records.filter(r=>r.name.startsWith('simple')).map(r=>{const peer=r.name.replace('simple','classic');return '<section><h2>'+String(r.form.label||r.form.name||r.form)+' · '+r.name.split('-').at(-1)+'</h2><div><figure><img src="'+r.name+'.png"><figcaption>Simple</figcaption></figure><figure><img src="'+peer+'.png"><figcaption>Classic</figcaption></figure></div></section>'}).join('');
   fs.writeFileSync(path.join(out,'index.html'),'<!doctype html><meta charset="utf-8"><title>Friday avatar comparisons</title><style>body{background:#080d16;color:#dde7f5;font:15px system-ui;margin:30px}h1{font-size:28px}section{margin:30px 0}section>div{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:0}img{width:100%;border:1px solid #234;border-radius:10px}figcaption{margin-top:8px;color:#89cfff}</style><h1>Friday · Simple and Classic</h1><p>Actual rendered frames with synthetic content. All 15 original avatar forms. Camera and microphone remain off.</p>'+rows);
   await browser.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1});

