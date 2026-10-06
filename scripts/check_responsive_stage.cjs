/* Native browser regression for responsive chrome, live state and avatar fit.
 * Run against the synthetic preview only; no model or camera is acquired. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const url = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3194/';
const output = process.env.FRIDAY_RESPONSIVE_PROOFS;
const baseline = process.env.FRIDAY_BASELINE_REV;
const overlap = (a,b) => Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>1 && Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>1;
const results = [];
function gate() { assert.ok(os.freemem()>6*1073741824, 'At least 6 GB free is required for this browser check'); }
async function inspect(page, name) {
  const state = await page.evaluate(() => {
    const box = e => { const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; };
    const visible = e => e.getClientRects().length && getComputedStyle(e).visibility!=='hidden' && !e.closest('[hidden],[inert]');
    const header=document.querySelector('.top-bar');
    const cells=[...header.querySelectorAll('.friday-topbar-cell[data-overflow="false"],.friday-topbar-more')].filter(visible);
    const oldControls=[...header.querySelectorAll('button,a,[role="checkbox"]')].filter(visible);
    const caption=document.querySelector('.friday-avatar-caption'), status=document.querySelector('#state-indicator');
    const fit=window.FridayHolographicWorkspace?.state;
    return {width:innerWidth,height:innerHeight,header:box(header),controls:(cells.length?cells:oldControls).map(e=>({name:e.getAttribute('aria-label')||e.textContent.trim().slice(0,40),...box(e)})),caption:caption&&{...box(caption),scroll:caption.scrollHeight,client:caption.clientHeight},status:status&&{...box(status),text:status.textContent.trim(),integrated:status.parentElement===caption},fit,
      surfaces:[...document.querySelectorAll('.fx-shell,.fwin:not(.closing),.chat-panel.open,.fr-holo-dialog[open],.friday-topbar-pullout[data-open="true"]')].filter(visible).map(e=>({name:e.className,...box(e)}))};
  });
  const failures=[];
  const check=(value,message)=>{if(!value)failures.push(message);};
  check(state.status?.integrated, 'Native live state must be integrated into the avatar caption');
  check(state.status?.text.length>0, 'Native state text must remain populated');
  check(state.caption && state.status.y>=state.caption.y-1 && state.status.y+state.status.h<=state.caption.y+state.caption.h+1, 'Native state must fit its caption');
  state.controls.forEach((a,i)=>{
    check(a.x>=-1&&a.y>=-1&&a.x+a.w<=state.width+1&&a.y+a.h<=state.header.y+state.header.h+1, 'Header item outside its row: '+a.name);
    state.controls.slice(i+1).forEach(b=>check(!overlap(a,b),'Header items overlap: '+a.name+' / '+b.name));
  });
  for(const surface of state.surfaces) {
    if(state.fit?.spatial.stage)check(!overlap(surface,state.fit.spatial.stage),'Surface covers avatar: '+surface.name);
    if(state.caption)check(!overlap(surface,state.caption),'Surface covers native state: '+surface.name);
    check(surface.x>=-1&&surface.y>=state.header.y+state.header.h-1&&surface.x+surface.w<=state.width+1&&surface.y+surface.h<=state.height+1,'Surface leaves viewport: '+surface.name);
  }
  if(state.fit?.projectedAvatarBounds){
    const b=state.fit.projectedAvatarBounds,s=state.fit.spatial.stage;
    check(b.inside,'Projected avatar crosses its stage');
    if(!baseline)check(Math.max(b.w/s.w,b.h/s.h)>.75,'Avatar under-fills its stage');
  }
  results.push({name,...state,failures});
  if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});}
  assert.deepEqual(failures,[],name);
  console.log('PASS '+name);
}
(async()=>{
  gate();const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
    const page=await browser.newPage({viewport:{width:1069,height:932}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    if(baseline){
      const files=['index.html','static/friday_holographic_workspace.js','static/friday_shared_surfaces.css','static/friday_native_shell.css'];
      const old=new Map(files.map(file=>{const r=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':'+file],{cwd:root,encoding:'utf8',maxBuffer:8e6,windowsHide:true});assert.equal(r.status,0,r.stderr);return[file,r.stdout];}));
      await page.route('**/*',route=>{const u=new URL(route.request().url()),file=u.pathname==='/'?'index.html':decodeURIComponent(u.pathname.slice(1));const body=old.get(file);return body?route.fulfill({status:200,contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'text/html',body}):route.continue();});
    }
    await page.goto(url);await page.locator('.fx-main').waitFor();
    await page.waitForFunction(()=>window.FridayHolographicWorkspace?.state.projectedAvatarBounds?.inside);
    if(baseline){await inspect(page,'before-1069');return;}
    for(const [width,height] of [[1600,1000],[1280,720],[1069,932],[768,1024],[700,850],[390,844],[320,568],[568,320]]){
      gate();await page.setViewportSize({width,height});
      await expect(page.locator('.friday-responsive-topbar')).toHaveAttribute('data-ready','true');
      await expect.poll(()=>page.evaluate(()=>{
        const bar=document.querySelector('.top-bar'),r=bar.getBoundingClientRect();
        return [...bar.querySelectorAll('.friday-topbar-cell[data-overflow="false"],.friday-topbar-more')].filter(e=>e.getClientRects().length&&!e.closest('[hidden],[inert]')).every(e=>{const b=e.getBoundingClientRect();return b.left>=r.left-1&&b.right<=r.right+1&&b.bottom<=r.bottom+1;});
      }),{message:'Measured header packing settles after a viewport change'}).toBeTruthy();
      await expect.poll(()=>page.evaluate(()=>{const s=FridayHolographicWorkspace.state;return s.projectedAvatarBounds?.inside && s.spatial.stageFrame.y>=document.querySelector('.top-bar').getBoundingClientRect().bottom;})).toBeTruthy();
      await expect.poll(()=>page.evaluate(()=>{const s=FridayHolographicWorkspace.state,b=s.projectedAvatarBounds,a=s.spatial.stage;return b?Math.max(b.w/a.w,b.h/a.h):0;}),{message:'Avatar grows into its resized stage'}).toBeGreaterThan(.75);
      await inspect(page,'home-'+width+'x'+height);
      const trigger=page.getByRole('button',{name:'More Friday controls',exact:true});
      if(await trigger.isVisible()){
        await trigger.press('ArrowDown');await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeVisible();
        await inspect(page,'menu-'+width+'x'+height);
        await page.keyboard.press('Escape');await expect(trigger).toBeFocused();
      }
    }
    await page.setViewportSize({width:280,height:720});
    const more=page.getByRole('button',{name:'More Friday controls',exact:true});
    await more.click();
    const workspace=page.getByRole('button',{name:'Switch workspace',exact:true});
    await workspace.click();
    const switcher=page.getByRole('dialog',{name:'Switch workspace',exact:true});
    await expect(switcher).toBeVisible();
    await switcher.getByRole('searchbox').fill('Settings');
    await expect(switcher.locator('.fx-switcher-option')).toHaveCount(1);
    await page.keyboard.press('Escape');await expect(switcher).toBeHidden();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeVisible();
    await expect(workspace).toBeFocused();
    await page.getByRole('button',{name:'Model quick switch',exact:true}).click();
    await expect(page.locator('.friday-model-menu')).toBeVisible();
    await page.keyboard.press('Escape');await expect(page.locator('.friday-model-menu')).toBeHidden();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeVisible();
    await workspace.click();await switcher.getByRole('searchbox').fill('Settings');
    await switcher.locator('.fx-switcher-option').click();
    await expect(page.locator('.st-root')).toBeVisible();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeHidden();
    console.log('PASS nested workspace/model controls preserve focus and Escape ownership');
    await page.goto(url);await page.locator('.fx-main').waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('button',{name:'More Friday controls',exact:true}).click();
    await page.getByRole('button',{name:'Scene and workspace depth',exact:true}).click();
    await expect(page.locator('.fr-holo-dialog')).toBeVisible();await inspect(page,'portrait-scene-controls');
    await page.locator('[data-holo-details]').click();
    await expect(page.locator('.friday-scene-menu')).toBeVisible();
    console.log('PASS advanced native scene controls remain reachable');
    await page.keyboard.press('Escape');
    await page.evaluate(()=>fridayVibe.setMood('THINKING'));
    await expect(page.locator('#mood-text')).toContainText('THINKING');await inspect(page,'portrait-thinking');
    await page.evaluate(()=>fridayVibe.setMood('IDLE'));
    await page.setViewportSize({width:320,height:568});
    await page.goto(new URL('/w/library',url).href);
    await page.getByRole('button',{name:'More Friday controls',exact:true}).click();
    await page.getByRole('button',{name:'Customize Library',exact:true}).click();
    await page.getByRole('menuitem',{name:'Workspace chat for Library',exact:true}).click();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeHidden();
    await expect(page.getByRole('dialog',{name:'Library customization',exact:true})).toBeVisible();
    const studioBox=await page.getByRole('dialog',{name:'Library customization',exact:true}).boundingBox();
    assert.ok(studioBox.x>=0&&studioBox.y>=0&&studioBox.x+studioBox.width<=320&&studioBox.y+studioBox.height<=568,'Standalone customization stays within a phone viewport');
    if(output)await page.screenshot({path:path.join(output,'standalone-customize.png'),animations:'disabled'});
    await page.goto(new URL('/w/library',url).href);
    await page.getByRole('button',{name:'More Friday controls',exact:true}).click();
    await page.getByRole('button',{name:'More tools for Library',exact:true}).click();
    await page.getByRole('menuitem',{name:'Version history for Library',exact:true}).click();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeHidden();
    await expect(page.locator('.ws-tab-hist')).toBeVisible();
    if(output)await page.screenshot({path:path.join(output,'standalone-history.png'),animations:'disabled'});
    console.log('PASS standalone workspace tools reveal their destination after overflow closes');
    assert.deepEqual(errors,[],'No native application errors');
  }finally{
    if(output)fs.writeFileSync(path.join(output,'responsive-inspection.json'),JSON.stringify(results,null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
