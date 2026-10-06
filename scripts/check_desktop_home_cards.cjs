/* A synthetic Home board proves live refresh, action routing and failure recovery.
 * The server API is separately covered by test_desktop_home_cards_routes.py. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const base = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3194/';
const output = process.env.FRIDAY_CARD_PROOFS;
const baseline = process.env.FRIDAY_BASELINE_REV;
const payload = (id, title, body = 'Choose a passage and bring it into the conversation.') => ({id, title, body, priority:50, actions:[{label:'Open Library',workspace:'library'}], created_at:100,updated_at:100});
const deck = page => page.getByRole('region', {name:'Home cards', exact:true});
async function proof(page, name) {
  const state = await page.evaluate(() => {
    const main = document.querySelector('.fx-main[data-view="day"]');
    const card = document.querySelector('.fx-home-card-deck');
    const rect = el => {const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
    return {main:rect(main),deck:rect(card),client:[main.clientWidth,main.clientHeight],scroll:[main.scrollWidth,main.scrollHeight]};
  });
  assert.ok(state.scroll[0] <= state.client[0] + 1 && state.scroll[1] <= state.client[1] + 1, name + ': Home overflow ' + JSON.stringify(state));
  assert.ok(state.deck.x >= state.main.x - 1 && state.deck.y >= state.main.y - 1 && state.deck.x + state.deck.w <= state.main.x + state.main.w + 1 && state.deck.y + state.deck.h <= state.main.y + state.main.h + 1, name + ': card leaves Home work area');
  if(output)await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});
  console.log('PASS ' + name);
}
(async () => {
  assert.ok(os.freemem() > 6 * 1073741824, 'At least 6 GB free is required');
  assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname) && new URL(base).port !== '3000', 'Use an isolated preview, never live Friday');
  if(output){
    const rel=path.relative(root,path.resolve(output));
    assert.ok(rel.startsWith('..'+path.sep)||path.isAbsolute(rel),'Proof output must be outside the repository');
    fs.mkdirSync(output,{recursive:true});
  }
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    let cards=[payload('review-notes','Review your notes')], failDelete=false, failRead=false;
    let holdNextRead=false, releaseRead, readCount=0;
    if(baseline){
      for(const asset of ['friday_experience.js','friday_native_shell.css']){
        const old=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':static/'+asset],{cwd:root,encoding:'utf8',windowsHide:true});
        assert.equal(old.status,0,old.stderr);
        await page.route('**/static/'+asset+'*',route=>route.fulfill({status:200,contentType:asset.endsWith('.js')?'text/javascript':'text/css',body:old.stdout}));
      }
    }
    await page.route('**/api/desktop/cards**',async route=>{
      const request=route.request(), method=request.method();
      let status=200, data;
      if(method==='GET'){
        readCount++;
        if(failRead){status=503;data={status:'error',message:'Cards are temporarily unavailable.'};}
        else data={status:'ok',cards:cards.map(card=>({...card}))};
        if(holdNextRead){holdNextRead=false;await new Promise(resolve=>{releaseRead=resolve})}
      }else if(method==='DELETE'){
        if(failDelete){status=503;data={status:'error',message:'The card could not be dismissed.'};}
        else{cards=cards.filter(c=>c.id!==decodeURIComponent(new URL(request.url()).pathname.split('/').pop()));data={status:'ok',removed:true};}
      }else if(method==='POST'){
        const incoming=request.postDataJSON();cards=cards.filter(c=>c.id!==incoming.id).concat(incoming);data={status:'ok',card:incoming};
      }
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto(base,{waitUntil:'domcontentloaded'});
    await expect(deck(page).getByRole('heading',{name:'Review your notes',exact:true})).toBeVisible();
    await page.waitForTimeout(500);await proof(page,'authored-card-wide');
    // A save notification can arrive while the preceding read still contains
    // an older snapshot. It must queue a fresh read rather than wait for polling.
    holdNextRead=true;
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-cards-changed')));
    await expect.poll(()=>typeof releaseRead).toBe('function');
    const beforeNotification=readCount;
    cards=cards.map(card=>({...card,body:'A newer card arrived during the pending read.'}));
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-cards-changed')));
    releaseRead();
    await expect(deck(page).locator('.fx-home-card-body')).toHaveText('A newer card arrived during the pending read.');
    assert.ok(readCount>beforeNotification,'A saved card queues another read immediately');
    const literal='<img src=x onerror=alert(1)>';
    cards.push(payload('literal',literal,('A long card stays readable.\n').repeat(60)));
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-cards-changed')));
    await expect(deck(page).getByText('1 of 2',{exact:true})).toBeVisible();
    await deck(page).getByRole('button',{name:'Next Home card',exact:true}).click();
    await expect(deck(page).getByRole('heading',{name:literal,exact:true})).toBeVisible();
    assert.equal(await deck(page).locator('img').count(),0,'Card content is plain text');
    await deck(page).getByRole('button',{name:'Read card: '+literal,exact:true}).click();
    await expect(deck(page)).toHaveAttribute('data-expanded','true');
    await expect(deck(page).getByRole('heading',{name:literal,exact:true})).toBeFocused();
    await proof(page,'card-full-text');
    failDelete=true;
    await deck(page).getByRole('button',{name:'Dismiss card: '+literal,exact:true}).click();
    await expect(deck(page).getByRole('alert')).toContainText('could not be dismissed');
    await expect(deck(page).getByRole('heading',{name:literal,exact:true})).toBeVisible();
    failDelete=false;
    await deck(page).getByRole('button',{name:'Dismiss card: '+literal,exact:true}).click();
    await expect(deck(page).getByRole('button',{name:'Undo dismiss',exact:true})).toBeVisible();
    await deck(page).getByRole('button',{name:'Undo dismiss',exact:true}).click();
    await expect.poll(()=>cards.length).toBe(2);
    await expect(deck(page).getByRole('button',{name:'Undo dismiss',exact:true})).toHaveCount(0);
    await deck(page).getByRole('button',{name:'Back to Home',exact:true}).click();
    for(const [width,height] of [[1280,720],[768,1024],[390,844],[320,568],[568,320]]){
      await page.setViewportSize({width,height});await page.waitForTimeout(500);
      await proof(page,'authored-card-'+width+'x'+height);
      await deck(page).getByRole('button',{name:/^Read card:/}).click();
      await proof(page,'read-card-'+width+'x'+height);
      await deck(page).getByRole('button',{name:'Back to Home',exact:true}).click();
    }
    await page.setViewportSize({width:1600,height:1000});
    await deck(page).getByRole('button',{name:/^Read card:/}).click();
    await deck(page).getByRole('button',{name:'Open Library',exact:true}).click();
    await expect(page.locator('.fwin[data-friday-workspace="library"]')).toBeVisible();
    await page.getByRole('button',{name:'Close Library',exact:true}).click();
    await expect(deck(page)).toBeVisible();
    failRead=true;
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-cards-changed')));
    await expect(deck(page).getByRole('alert')).toContainText('temporarily unavailable');
    await expect(deck(page).getByRole('heading',{name:'Review your notes',exact:true})).toBeVisible();
    failRead=false;
    await deck(page).getByRole('button',{name:'Retry',exact:true}).click();
    await expect(deck(page).getByRole('alert')).toHaveCount(0);
    await page.reload({waitUntil:'domcontentloaded'});
    await expect(deck(page).getByRole('heading',{name:'Review your notes',exact:true})).toBeVisible();
    assert.deepEqual(errors,[]);
    console.log('PASS live card refresh including pending-read notifications, plain text, actions, failure retention, undo, reload and responsive reading');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
