/* Actual scene projection and DOM-cover proof. Synthetic orbs exist only in
 * this isolated browser; requests cannot mutate the preview's durable state. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {chromium}=require('@playwright/test');
const base=process.env.FRIDAY_DESIGN_URL||'http://127.0.0.1:3194/';
const out=process.env.FRIDAY_ORB_PROOFS,root=path.resolve(__dirname,'..'),records=[];
async function capture(page,name,state){
  records.push({name,...state});
  await page.screenshot({path:path.join(out,name+'.png'),animations:'disabled'});
  console.log('PASS '+name);
}
(async()=>{
  const url=new URL(base);
  assert.ok(['127.0.0.1','localhost'].includes(url.hostname)&&url.port==='3194','Use the isolated preview on port 3194');
  assert.ok(os.freemem()>6*2**30,'At least 6 GB free is required');
  assert.ok(out,'Set FRIDAY_ORB_PROOFS outside the repository');
  const relative=path.relative(root,path.resolve(out));
  assert.ok(relative.startsWith('..'+path.sep)||path.isAbsolute(relative),'Keep proof outputs outside the repository');
  fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    for(const display of ['simple','classic']){
      const page=await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
      try{
        await page.addInitScript(value=>{
          localStorage.setItem('friday.display-style.v1',value);
          if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=()=>Promise.reject(new Error('Camera is disabled for this proof'));
          window.__orbReceipts=[];
          // Observe the actual manager's click result without opening a task
          // window that would itself cover the next scene interaction.
          window.addEventListener('fridayOrbClicked',event=>{
            window.__orbReceipts.push(event.detail.id);event.stopImmediatePropagation();
          },true);
        },display);
        await page.route('**/api/**',route=>{
          if(new URL(route.request().url()).pathname==='/api/desktop/cards')return route.fulfill({json:{status:'ok',cards:[]}});
          return ['GET','HEAD'].includes(route.request().method())?route.continue():route.fulfill({json:{status:'ok'}});
        });
        await page.goto(base);
        await page.waitForFunction(()=>window.__fridayRenderer&&window.FridayHandCursor&&window.fridayVibe?.getWorkspaceSceneState?.().ready);
        assert.equal(await page.evaluate(()=>FridayDisplayStyle.get()),display);
        if(display==='simple')await page.waitForFunction(()=>FridayHolographicWorkspace.stageRect?.w>0);
        await page.evaluate(()=>{for(const orb of fridayGetOrbs())fridayRemoveOrb(orb.id);});
        let selected=null;
        for(const [i,angle] of [.375,.125,.25,.5,.625,.75,.875,0].entries()){
          const id='orb-proof-'+display+'-'+i;
          await page.evaluate(({id,angle})=>{
            const random=Math.random;
            try{Math.random=()=>angle;fridayAddOrb({id,label:'Interaction proof',category:'monitoring',icon:'◇'});}
            finally{Math.random=random;}
          },{id,angle});
          await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
          const available=await page.waitForFunction(id=>{
            const orb=fridayOrbTargets().find(item=>item.id===id);if(!orb)return false;
            const top=document.elementFromPoint(orb.sx,orb.sy);
            return !!top&&[document.body,document.documentElement,document.getElementById('ui-root'),document.getElementById('ui-inner')].includes(top);
          },id,{timeout:6000}).then(()=>true,()=>false);
          if(available){selected=id;break;}
          await page.evaluate(id=>fridayRemoveOrb(id),id);
        }
        assert.ok(selected,display+': a real projected orb must land in exposed scene space');
        const positive=await page.evaluate(id=>{
          const orb=fridayOrbTargets().find(item=>item.id===id),canvas=window.__fridayRenderer.domElement;
          const rect=canvas.getBoundingClientRect(),top=document.elementFromPoint(orb.sx,orb.sy);
          FridayHandCursor.refresh();FridayHandCursor.frame({x:orb.sx,y:orb.sy,visible:true,pinching:false,t:performance.now()});
          const locked=FridayHandCursor.locked?.orb?.id,result=FridayHandCursor.select();
          return {id,locked,selected:result.ok,receipts:[...__orbReceipts],pointerEvents:getComputedStyle(canvas).pointerEvents,
            hit:top.id||top.tagName,canvas:{x:rect.x,y:rect.y,w:rect.width,h:rect.height},orb,
            stage:FridayHolographicWorkspace.stageRect};
        },selected);
        assert.equal(positive.pointerEvents,'none','The proof uses the actual pointer-transparent canvas');
        assert.equal(positive.locked,selected);assert.equal(positive.selected,true);
        assert.deepEqual(positive.receipts,[selected],'The real orb manager must emit the selected orb');
        assert.deepEqual(positive.canvas,{x:0,y:0,w:1600,h:1000},'Stage fitting must retain the full-viewport canvas');
        await capture(page,display+'-visible-orb',positive);
        const behindId='orb-proof-behind-'+display;
        await page.evaluate(id=>{const random=Math.random;try{Math.random=()=>.75;fridayAddOrb({id,label:'Hidden interaction proof',category:'monitoring'});}finally{Math.random=random;}},behindId);
        await page.waitForFunction(id=>FridayOrbScene._state(id)?.behind===true,behindId);
        const behind=await page.evaluate(id=>{const s=FridayOrbScene._state(id);return {offered:fridayOrbTargets().some(o=>o.id===id),result:fridayOrbClickAt(s.sx,s.sy),receipts:[...__orbReceipts]};},behindId);
        assert.equal(behind.offered,false,'Behind-avatar orbs must not be offered for hand selection');
        assert.equal(behind.result.ok,false,'A click on a hidden orb must not report success');assert.deepEqual(behind.receipts,[selected]);
        await capture(page,display+'-behind-avatar-orb',behind);
        await page.evaluate(id=>fridayRemoveOrb(id),behindId);
        const covered=await page.evaluate(id=>{
          const cover=document.createElement('div');cover.id='orb-proof-cover';
          cover.style.cssText='position:fixed;inset:0;background:#111;z-index:2147483000';document.body.appendChild(cover);
          const result=FridayHandCursor.select();
          const orb=fridayOrbTargets().find(item=>item.id===id);
          FridayHandCursor.frame({x:orb.sx,y:orb.sy,visible:true,pinching:false,t:performance.now()});
          return {selected:result.ok,locked:!!FridayHandCursor.locked?.orb,receipts:[...__orbReceipts]};
        },selected);
        assert.equal(covered.selected,false);assert.equal(covered.locked,false);assert.deepEqual(covered.receipts,[selected]);
        await capture(page,display+'-covered-orb',covered);
        await page.evaluate(()=>document.getElementById('orb-proof-cover').remove());
        const modal=await page.evaluate(id=>{
          const orb=fridayOrbTargets().find(item=>item.id===id);
          FridayHandCursor.refresh();FridayHandCursor.frame({x:orb.sx,y:orb.sy,visible:true,pinching:false,t:performance.now()});
          const before=FridayHandCursor.locked?.orb?.id;
          FridayHolographicWorkspace.open();
          const result=FridayHandCursor.select();
          return {before,selected:result.ok,receipts:[...__orbReceipts],open:document.querySelector('.fr-holo-dialog')?.open};
        },selected);
        assert.equal(modal.before,selected);assert.equal(modal.open,true);assert.equal(modal.selected,false);
        assert.deepEqual(modal.receipts,[selected]);await capture(page,display+'-modal-orb',modal);
      }finally{await page.close();}
    }
  }finally{
    fs.writeFileSync(path.join(out,'orb-interactions.json'),JSON.stringify(records,null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1});
