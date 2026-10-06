/* Home adapts to the usable desktop area, including the avatar and dock. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const url = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3194/';
const output = process.env.FRIDAY_HOME_PROOFS;
const baseline = process.env.FRIDAY_BASELINE_REV;
const records = [];
async function inspect(page, name) {
  const state = await page.evaluate(() => {
    const main = document.querySelector('.fx-main[data-view="day"]');
    const box = e => { const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; };
    return {main:box(main),client:[main.clientWidth,main.clientHeight],scroll:[main.scrollWidth,main.scrollHeight],controls:[...main.querySelectorAll('textarea,select,button')].filter(e=>e.getClientRects().length).map(e=>({name:e.getAttribute('aria-label')||e.textContent.trim(),...box(e)}))};
  });
  records.push({name,...state});
  if(output)await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});
  assert.ok(state.scroll[0]<=state.client[0]+1 && state.scroll[1]<=state.client[1]+1, name+': Home must fit its available area, '+JSON.stringify(state));
  for(const control of state.controls)assert.ok(control.x>=state.main.x-1&&control.y>=state.main.y-1&&control.x+control.w<=state.main.x+state.main.w+1&&control.y+control.h<=state.main.y+state.main.h+1,name+': action clipped: '+control.name);
  console.log('PASS '+name);
}
(async()=>{
  assert.ok(os.freemem()>6*1073741824,'At least 6 GB free is required');
  if(output)fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const page=await browser.newPage();
    if(baseline){
      const r=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':static/friday_native_shell.css'],{cwd:root,encoding:'utf8',windowsHide:true});
      assert.equal(r.status,0,r.stderr);
      await page.route('**/static/friday_native_shell.css*',route=>route.fulfill({status:200,contentType:'text/css',body:r.stdout}));
    }
    await page.goto(url);await page.locator('.fx-main').waitFor();
    for(const [width,height] of [[1577,932],[1600,1000],[1280,720],[1069,932],[768,1024],[700,850],[390,844],[320,568],[568,320],[860,500]]){
      await page.setViewportSize({width,height});
      await expect.poll(()=>page.evaluate(()=>window.FridayHolographicWorkspace?.state.projectedAvatarBounds?.inside)).toBeTruthy();
      // Header packing, dock measurement and the scene resize settle together.
      await page.waitForTimeout(500);
      await inspect(page,'home-'+width+'x'+height);
    }
    await page.setViewportSize({width:1600,height:1000});
    await page.getByRole('button',{name:'Open chat with Friday',exact:true}).click();
    await expect(page.locator('.chat-panel.open')).toBeVisible();
    await page.waitForTimeout(500);await inspect(page,'home-with-chat');
    await page.getByRole('textbox',{name:'Ask Friday',exact:true}).fill('Prepare a short launch outline');
    await expect(page.getByRole('button',{name:'Continue →',exact:true})).toBeEnabled();
    console.log('PASS compact Home preserves the draft action');
  }finally{
    if(output)fs.writeFileSync(path.join(output,'inspection.json'),JSON.stringify(records,null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
