/* Display styles preserve working state while changing the desktop presentation.
 * Run only against the isolated synthetic preview; no model or camera is started. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const base = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3194/';
const output = process.env.FRIDAY_DISPLAY_STYLE_PROOFS;
const baseline = process.env.FRIDAY_BASELINE_REV;
const records = [];
const gate = () => assert.ok(os.freemem() > 6 * 1073741824, 'At least 6 GB free is required');
const title = style => style[0].toUpperCase() + style.slice(1);
const control = page => page.locator('button[aria-label^="Display style:"]');
const dialog = page => page.getByRole('dialog', {name:'Display style', exact:true});

async function openStyles(page) {
  gate();
  const trigger = control(page);
  await expect(trigger).toHaveCount(1);
  if (!await trigger.isVisible()) {
    const more = page.getByRole('button', {name:'More Friday controls', exact:true});
    await expect(more).toBeVisible();
    await more.click();
  }
  await trigger.click();
  await expect(dialog(page)).toBeVisible();
  return dialog(page);
}

async function chooseStyle(page, style) {
  const popup = await openStyles(page);
  await popup.getByRole('radio', {name:title(style), exact:true}).click();
  await expect.poll(() => page.evaluate(() => window.FridayDisplayStyle?.get())).toBe(style);
  await expect(dialog(page)).toBeHidden();
  await expect(control(page)).toHaveAttribute('aria-label', 'Display style: ' + title(style));
}

async function proof(page, name) {
  const state = await page.evaluate(() => {
    const visible = element => !!element && !!element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden' && !element.closest('[hidden],[inert]');
    const rect = element => {const r=element.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};};
    return {
      style:window.FridayDisplayStyle?.get(), width:innerWidth, height:innerHeight,
      simpleEnabled:document.body.classList.contains('friday-experience-enabled'),
      homeVisible:visible(document.querySelector('.fx-main[data-view="day"]')),
      classicVisible:visible(document.querySelector('[data-testid="landing-cluster"]')),
      scene:window.fridayVibe?.getWorkspaceSceneState?.().scene,
      menus:[...document.querySelectorAll('.friday-topbar-pullout[data-open="true"],dialog[open]')].filter(visible).map(element=>({name:element.getAttribute('aria-label')||element.className,...rect(element)}))
    };
  });
  records.push({name,...state});
  for (const menu of state.menus) assert.ok(menu.x>=-1 && menu.y>=-1 && menu.x+menu.w<=state.width+1 && menu.y+menu.h<=state.height+1, name+': menu leaves the viewport: '+menu.name);
  if (output) await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});
  console.log('PASS '+name);
}

async function assertSameScene(page) {
  assert.deepEqual(await page.evaluate(() => ({
    renderer:window.__displayStyleProbe.renderer===window.__fridayRenderer,
    scene:window.fridayVibe.getWorkspaceSceneState().scene.index,
    expected:window.__displayStyleProbe.scene
  })), {renderer:true,scene:4,expected:4}, 'Changing display style preserves the selected avatar and renderer');
}

(async () => {
  gate();
  if (output) {
    const destination=path.resolve(output), relative=path.relative(root,destination);
    assert.ok(relative==='..' || relative.startsWith('..'+path.sep) || path.isAbsolute(relative), 'Proof outputs must stay outside the repository');
    fs.mkdirSync(destination,{recursive:true});
  }
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    const page=await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
    page.setDefaultTimeout(15000);
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    if (baseline) {
      const old=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':index.html'],{cwd:root,encoding:'utf8',maxBuffer:8e6,windowsHide:true});
      assert.equal(old.status,0,old.stderr);
      await page.route('**/*',route=>new URL(route.request().url()).pathname==='/'
        ? route.fulfill({status:200,contentType:'text/html',body:old.stdout}) : route.continue());
    }
    await page.goto(base);
    await page.locator('.fx-main[data-view="day"]').waitFor();
    assert.equal(await control(page).count(),1,'The shared header must expose a display-style control');
    if (baseline) return;
    await expect.poll(()=>page.evaluate(()=>window.FridayDisplayStyle?.get())).toBe('simple');
    await page.waitForFunction(()=>window.fridayVibe?.getWorkspaceSceneState?.().ready);
    await page.evaluate(()=>fridayVibe.setStructure(4));
    await page.waitForFunction(()=>fridayVibe.getWorkspaceSceneState().scene.index===4 && fridayVibe.getWorkspaceSceneState().transitionProgress>=.999);
    await page.evaluate(()=>{window.__displayStyleProbe={renderer:window.__fridayRenderer,scene:fridayVibe.getWorkspaceSceneState().scene.index};});
    const prompt='Keep this outline while I compare desktop styles.';
    await page.getByRole('textbox',{name:'Ask Friday',exact:true}).fill(prompt);
    const popup=await openStyles(page);
    await expect(popup.getByRole('radio',{name:'Simple',exact:true})).toBeChecked();
    await expect(popup.getByRole('radio',{name:'Advanced',exact:true})).toBeDisabled();
    await expect(popup.getByText('In development',{exact:true})).toBeVisible();
    await proof(page,'style-choices');
    await page.keyboard.press('Escape');
    await expect(dialog(page)).toBeHidden();
    await expect(control(page)).toBeFocused();

    await chooseStyle(page,'classic');
    await expect(page.locator('.fx-main[data-view="day"]')).toBeHidden();
    await expect(page.locator('[data-testid="landing-cluster"]')).toBeVisible();
    await expect(page.locator('body')).not.toHaveClass(/friday-experience-enabled/);
    await assertSameScene(page);
    await proof(page,'classic-desktop');
    await chooseStyle(page,'simple');
    await expect(page.getByRole('textbox',{name:'Ask Friday',exact:true})).toHaveValue(prompt);
    await expect(page.locator('[data-testid="landing-cluster"]')).toBeHidden();
    await assertSameScene(page);
    await proof(page,'simple-draft-preserved');

    await page.getByRole('button',{name:'Switch workspace',exact:true}).click();
    const switcher=page.getByRole('dialog',{name:'Switch workspace',exact:true});
    await switcher.getByRole('searchbox').fill('Library');
    await expect(switcher.locator('.fx-switcher-option')).toHaveCount(1);
    await switcher.locator('.fx-switcher-option').click();
    const workspace=page.locator('.fwin[data-friday-workspace="library"]');
    await expect(workspace).toBeVisible();
    await page.evaluate(()=>{window.__displayStyleProbe.workspace=document.querySelector('.fwin[data-friday-workspace="library"] .ws-custom-root');});
    const composition=()=>page.evaluate(()=>getComputedStyle(document.querySelector('.fwin[data-friday-workspace="library"] .ws-custom-root')).getPropertyValue('--fr-comp-gap').trim());
    await expect.poll(composition).not.toBe('');
    await chooseStyle(page,'classic');
    await expect(workspace).toBeVisible();
    assert.equal(await page.evaluate(()=>window.__displayStyleProbe.workspace===document.querySelector('.fwin[data-friday-workspace="library"] .ws-custom-root')),true,'Classic preserves the open workspace instance');
    await expect.poll(composition).toBe('');
    await assertSameScene(page);
    await proof(page,'classic-workspace');
    await page.getByRole('button',{name:/^Switch workspace/}).click();
    await page.getByRole('button',{name:'← Back to desktop',exact:true}).click();
    await expect(workspace).toBeHidden();
    await expect(page.locator('[data-testid="landing-cluster"]')).toBeVisible();
    await page.getByRole('button',{name:/^Switch workspace/}).click();
    await page.getByRole('dialog',{name:'Switch workspace',exact:true}).getByRole('searchbox').fill('Library');
    await page.locator('.fx-switcher-option').click();
    await expect(workspace).toBeVisible();
    assert.equal(await page.evaluate(()=>window.__displayStyleProbe.workspace===document.querySelector('.fwin[data-friday-workspace="library"] .ws-custom-root')),true,'Classic desktop navigation preserves workspace state');
    await chooseStyle(page,'simple');
    await expect(workspace).toBeVisible();
    assert.equal(await page.evaluate(()=>window.__displayStyleProbe.workspace===document.querySelector('.fwin[data-friday-workspace="library"] .ws-custom-root')),true,'Simple restores styling without replacing workspace state');
    await expect.poll(composition).not.toBe('');
    await assertSameScene(page);
    await proof(page,'simple-workspace-restored');

    await chooseStyle(page,'classic');
    await page.reload();
    await expect(control(page)).toHaveAttribute('aria-label','Display style: Classic');
    await expect.poll(()=>page.evaluate(()=>window.FridayDisplayStyle?.get())).toBe('classic');
    await page.goto(new URL('/w/library',base).href);
    await expect(page.locator('.ws-tab')).toBeVisible();
    await expect(control(page)).toHaveAttribute('aria-label','Display style: Classic');
    await proof(page,'classic-standalone-persisted');
    await chooseStyle(page,'simple');
    await page.reload();
    await expect(control(page)).toHaveAttribute('aria-label','Display style: Simple');
    await expect.poll(()=>page.evaluate(()=>window.FridayDisplayStyle?.get())).toBe('simple');
    await proof(page,'simple-standalone-persisted');

    await page.setViewportSize({width:320,height:568});
    await page.goto(base);
    await expect(control(page)).toHaveAttribute('aria-label','Display style: Simple');
    await openStyles(page);
    const advanced=await dialog(page).getByRole('radio',{name:'Advanced',exact:true}).boundingBox();
    const choicesBox=await dialog(page).boundingBox();
    assert.ok(advanced.y+advanced.height<=choicesBox.y+choicesBox.height,'All three styles fit the compact chooser');
    await proof(page,'portrait-style-choices');
    await dialog(page).getByRole('radio',{name:'Classic',exact:true}).click();
    await expect.poll(()=>page.evaluate(()=>window.FridayDisplayStyle?.get())).toBe('classic');
    await chooseStyle(page,'simple');
    await expect(page.getByRole('textbox',{name:'Ask Friday',exact:true})).toBeVisible();
    await expect(page.getByRole('dialog',{name:'Friday controls',exact:true})).toBeHidden();
    await proof(page,'portrait-simple-restored');
    await page.setViewportSize({width:1600,height:1000});
    await page.getByRole('button',{name:/^Switch workspace/}).click();
    await page.getByRole('dialog',{name:'Switch workspace',exact:true}).getByRole('searchbox').fill('Settings');
    await page.locator('.fx-switcher-option').click();
    await page.getByRole('button',{name:'Appearance',exact:true}).click();
    const settings=page.locator('.st-root');
    await expect(settings.getByRole('radio',{name:'Advanced',exact:true})).toBeDisabled();
    await settings.getByRole('radio',{name:'Classic',exact:true}).click();
    await expect(control(page)).toHaveAttribute('aria-label','Display style: Classic');
    await settings.getByRole('radio',{name:'Simple',exact:true}).click();
    await expect(control(page)).toHaveAttribute('aria-label','Display style: Simple');
    await proof(page,'appearance-style-setting');
    await chooseStyle(page,'classic');
    await page.goto(new URL('/w/media',base).href);
    const media=page.locator('.md-lib');
    await expect(media.locator(':scope > .md-rail')).toBeVisible();
    const mediaBox=await media.boundingBox(), cardsBox=await media.locator(':scope > .md-main').boundingBox();
    assert.ok(cardsBox.width>mediaBox.width*.6,'Classic Media retains its sidebar and full-width cards area');
    await proof(page,'classic-media');
    await chooseStyle(page,'simple');
    await expect(page.locator('.md-filter-menu')).toBeVisible();
    await expect(media.locator(':scope > .md-rail')).toHaveCount(0);
    await proof(page,'simple-media');
    assert.deepEqual(errors,[],'No application errors while switching display styles');
    console.log('PASS style persistence, live state, native Classic presentation, and portrait access');
  } finally {
    if (output) fs.writeFileSync(path.join(output,'inspection.json'),JSON.stringify(records,null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
