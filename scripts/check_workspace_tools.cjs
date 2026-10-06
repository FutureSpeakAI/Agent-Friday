/* Grouped workspace controls against the isolated synthetic preview. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const base = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3194/';
const output = process.env.FRIDAY_TOOL_PROOFS;
const baseline = process.env.FRIDAY_BASELINE_REV;
const records = [];
const overlap = (a,b) => Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>1 && Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>1;
const gate = () => assert.ok(os.freemem() > 6*1073741824, '6 GB free required');
async function proof(page,name) {
  await expect.poll(()=>page.evaluate(()=>{
    const spatial=window.FridayHolographicWorkspace?.state.spatial;
    if(!spatial?.stage)return [];
    const areas=[spatial.stage,spatial.caption].filter(Boolean);
    return [...document.querySelectorAll('.fwin:not(.closing),.ws-tools-menu,.snap-menu')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden').flatMap(e=>{
      const r=e.getBoundingClientRect();
      return areas.every(a=>Math.min(r.right,a.x+a.w)-Math.max(r.left,a.x)<=1||Math.min(r.bottom,a.y+a.h)-Math.max(r.top,a.y)<=1)?[]:[{element:e.className,box:{x:r.x,y:r.y,w:r.width,h:r.height},spatial}];
    });
  }),{message:'Workspace and portal positions settle after reserved-layout changes'}).toEqual([]);
  const state = await page.evaluate(() => {
    const rect = e => {const r=e.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height};};
    const visible = e => e.getClientRects().length && getComputedStyle(e).visibility!=='hidden' && !e.closest('[hidden],[inert]');
    const bars = [...document.querySelectorAll('.fwin-bar')].filter(visible).map(bar=>({box:rect(bar),items:[bar.querySelector('.fwin-title'),...bar.querySelectorAll('button')].filter(e=>e&&visible(e)).map(e=>({label:e.getAttribute('aria-label')||e.textContent,...rect(e)}))}));
    return {width:innerWidth,height:innerHeight,bars,menus:[...document.querySelectorAll('.ws-tools-menu,.snap-menu')].filter(visible).map(e=>({name:e.getAttribute('aria-label'),...rect(e)})),spatial:window.FridayHolographicWorkspace?.state.spatial};
  });
  const failures=[];
  for(const bar of state.bars)for(let i=0;i<bar.items.length;i++){
    const item=bar.items[i];
    if(item.x<bar.box.x-1||item.x+item.w>bar.box.x+bar.box.w+1||item.y+item.h>bar.box.y+bar.box.h+1)failures.push('Toolbar overflow: '+item.label);
    for(const other of bar.items.slice(i+1))if(overlap(item,other))failures.push('Toolbar overlap: '+item.label+' / '+other.label);
  }
  for(const menu of state.menus){
    if(menu.x<0||menu.y<0||menu.x+menu.w>state.width+1||menu.y+menu.h>state.height+1)failures.push('Menu leaves viewport: '+menu.name);
    for(const area of [state.spatial?.stage,state.spatial?.caption].filter(Boolean))if(overlap(menu,area))failures.push('Menu covers avatar/state: '+menu.name);
  }
  records.push({name,...state,failures});
  if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});}
  assert.deepEqual(failures,[],name);console.log('PASS '+name);
}
(async()=>{
  gate();const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
    page.setDefaultTimeout(10000);const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    if(baseline){
      const old=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':index.html'],{cwd:root,encoding:'utf8',maxBuffer:8e6,windowsHide:true});
      assert.equal(old.status,0,old.stderr);
      await page.route('**/?workspace=library',route=>route.fulfill({status:200,contentType:'text/html',body:old.stdout}));
    }
    await page.goto(new URL('?workspace=library',base).href);
    const win=page.locator('.fwin[data-friday-workspace="library"]');
    await expect(win).toBeVisible();
    if(baseline){
      assert.equal(await win.locator('[data-ws-customize="library"]').count(),1,'A workspace must expose the new pickaxe Customize entry');
      return;
    }
    const customize=page.getByRole('button',{name:'Customize Library',exact:true});
    const more=page.getByRole('button',{name:'More tools for Library',exact:true});
    const leaf=name=>page.getByRole('menuitem',{name,exact:true});
    await expect(customize).toBeVisible();await expect(more).toBeVisible();
    await proof(page,'desktop-toolbar');
    await customize.press('ArrowDown');
    await expect(page.getByRole('menu',{name:'Customize Library',exact:true})).toBeVisible();
    await expect(leaf('Workspace chat for Library')).toBeFocused();
    await expect(leaf('Improve Library')).toBeDisabled();
    await proof(page,'customize-scopes');
    await page.keyboard.press('Control+Alt+c');
    await expect(page.locator('.chat-panel.open')).toBeVisible();
    await proof(page,'customize-with-chat');
    await page.keyboard.press('Control+Alt+c');
    await page.keyboard.press('Escape');await expect(customize).toBeFocused();
    await customize.click();await leaf('Workspace chat for Library').click();
    await expect(page.getByRole('dialog',{name:'Library customization',exact:true})).toBeVisible();
    await expect(page.getByRole('menu',{name:'Customize Library',exact:true})).toBeHidden();
    await proof(page,'workspace-customization');
    await page.goto(new URL('?workspace=library',base).href);
    await more.click();await expect(leaf('Start voice for Library')).toBeVisible();
    await expect(leaf('Start voice for Library')).toBeFocused();
    await page.keyboard.press('ArrowDown');await expect(leaf('Version history for Library')).toBeFocused();
    await proof(page,'more-tools');
    await leaf('Version history for Library').click();
    await expect(win.getByText('History — Library',{exact:true})).toBeVisible();
    await expect(page.getByRole('menu',{name:'More tools for Library',exact:true})).toBeHidden();
    await page.goto(new URL('?workspace=library',base).href);
    await page.evaluate(()=>{window.__voiceCalls=[];window.fridayStartWorkspaceVoice=id=>window.__voiceCalls.push(id);});
    await more.click();await leaf('Start voice for Library').click();
    assert.deepEqual(await page.evaluate(()=>window.__voiceCalls),['library']);
    await page.evaluate(()=>{window.open=()=>null;});
    await more.click();await leaf('Open Library in its own tab').click();
    await expect(win).toBeVisible();await expect(win.locator('.fwin-tab-blocked')).toContainText('blocked');
    await proof(page,'blocked-tab-preserves-work');
    await page.goto(new URL('?workspace=library',base).href);
    await more.click();
    const [tab]=await Promise.all([page.waitForEvent('popup'),leaf('Open Library in its own tab').click()]);
    await tab.waitForURL('**/w/library**');
    await expect(tab.locator('[data-standalone="library"]')).toBeVisible();
    await expect(win).toBeHidden();await tab.close();
    console.log('PASS a real menu click moves the workspace into its own tab');
    await page.goto(new URL('?workspace=library',base).href);
    await page.getByRole('button',{name:'Arrange Library',exact:true}).click();
    await expect(page.getByRole('menu',{name:'Snap layouts',exact:true})).toBeVisible();
    await proof(page,'arrange-menu');
    const initiallyMaxed=await win.evaluate(element=>element.classList.contains('maxed'));
    await page.locator('[data-fwin-togglemax="library"]').click();
    await expect.poll(()=>win.evaluate(element=>element.classList.contains('maxed'))).toBe(!initiallyMaxed);
    await page.getByRole('button',{name:'Arrange Library',exact:true}).click();
    await page.locator('[data-fwin-togglemax="library"]').click();
    await expect.poll(()=>win.evaluate(element=>element.classList.contains('maxed'))).toBe(initiallyMaxed);
    await page.getByRole('button',{name:'Arrange Library',exact:true}).click();
    await page.locator('[data-testid="snap-menu"] [data-snap="left_half"]').click();
    await expect(win).toHaveAttribute('data-snap','left_half');
    await proof(page,'narrow-window-on-desktop');
    for(const [width,height] of [[768,1024],[860,500],[390,844],[320,568],[568,320]]){
      gate();await page.setViewportSize({width,height});
      await expect(customize).toBeVisible();await expect(more).toBeVisible();
      await proof(page,'toolbar-'+width+'x'+height);
      await more.click();await proof(page,'tools-'+width+'x'+height);
      if(width===320){
        await leaf('Arrange Library').click();
        await expect(page.getByRole('menu',{name:'Snap layouts',exact:true})).toBeVisible();
        await proof(page,'portrait-arrange');
      }
      await page.keyboard.press('Escape');await expect(more).toBeFocused();
    }
    await page.setViewportSize({width:320,height:568});
    await page.goto(new URL('/w/library',base).href);
    const globalMore=page.getByRole('button',{name:'More Friday controls',exact:true});
    await globalMore.click();await customize.click();
    await expect(page.getByRole('menu',{name:'Customize Library',exact:true})).toBeVisible();
    await proof(page,'standalone-nested-customize');
    await page.keyboard.press('Escape');await expect(customize).toBeFocused();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeVisible();
    await customize.click();await leaf('Workspace chat for Library').click();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeHidden();
    await expect(page.getByRole('dialog',{name:'Library customization',exact:true})).toBeVisible();
    await page.goto(new URL('/w/library',base).href);
    await globalMore.click();await more.click();await leaf('Version history for Library').click();
    await expect(page.locator('.ws-tab-hist')).toBeVisible();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeHidden();
    console.log('PASS standalone tools dismiss their own and enclosing menus');
    await page.setViewportSize({width:1600,height:1000});
    await page.addInitScript(()=>{window.FRIDAY_INSTALLED_BUNDLES=[{id:'sample-lab',label:'Sample Lab',glyph:'◇'}];});
    let improveCalls=0;
    await page.route('**/api/workspaces/sample-lab/improve',route=>{assert.equal(route.request().method(),'POST');improveCalls++;return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({conversation_id:'sample-salon',label:'Sample Lab'})});});
    await page.goto(new URL('?workspace=sample-lab',base).href);
    await expect(page.locator('.fwin[data-friday-workspace="sample-lab"]')).toBeVisible();
    await page.evaluate(()=>{window.__salonOpened=null;window.fridayOpenChatWindow=(id,title)=>{window.__salonOpened={id,title};};});
    await page.getByRole('button',{name:'Customize Sample Lab',exact:true}).click();
    await expect(leaf('Improve Sample Lab')).toBeEnabled();await proof(page,'bundle-customize');
    await leaf('Improve Sample Lab').click();
    await expect.poll(()=>page.evaluate(()=>window.__salonOpened)).toEqual({id:'sample-salon',title:'Improve Sample Lab'});
    assert.equal(improveCalls,1,'The Salon opens only after choosing its codebase action');
    console.log('PASS bundle codebase path retains the existing native Salon route');
    await page.getByRole('button',{name:'Close Sample Lab',exact:true}).click();
    await expect(page.locator('.fwin[data-friday-workspace="sample-lab"]')).toBeHidden();
    assert.deepEqual(errors,[],'No application errors');
  }finally{if(output){fs.mkdirSync(output,{recursive:true});fs.writeFileSync(path.join(output,'inspection.json'),JSON.stringify(records,null,2));}await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
