/* Behavioral UI checks against the isolated sample server. No application
 * process, real home, model, account or external action is involved. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawn} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const port = Number(process.env.FRIDAY_DESIGN_TEST_PORT || 3189);
const base = 'http://127.0.0.1:' + port;
let checks = 0;
function passed(name) { checks++; console.log('PASS ' + name); }
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
for (const m of html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (m[1].includes('src=') || /type=["'](?!text\/javascript|application\/javascript)/.test(m[1])) continue;
  new vm.Script(m[2]);
}
passed('served inline JavaScript parses');
const {babelParse} = require(path.join(path.dirname(require.resolve('playwright/package.json')),'lib/transform/babelBundle'));
const mirror = fs.readFileSync(path.join(root,'ui_parts/app.html'),'utf8');
const tag = '<script type="text/babel">';
babelParse(mirror.slice(mirror.indexOf(tag)+tag.length,mirror.lastIndexOf('</script>')),'mirror.jsx',false);
passed('hand-maintained JSX mirror parses');
const server = spawn(process.execPath, [path.join(__dirname,'preview_spatial_desktop.cjs')], {
  cwd: root, env: {...process.env, FRIDAY_DESIGN_PORT:String(port)}, windowsHide:true, stdio:['ignore','pipe','pipe']
});
let browser;
(async () => {
  await new Promise((resolve,reject) => {
    const timer=setTimeout(()=>reject(Error('Preview did not start')),10000);
    server.stdout.on('data',d=>{if(String(d).includes('Design preview:')){clearTimeout(timer);resolve();}});
    server.on('error',reject); server.on('exit',c=>{if(c)reject(Error('Preview exited '+c));});
    server.stderr.on('data',d=>process.stderr.write(d));
  });
  browser = await chromium.launch({headless:true,channel:process.env.FRIDAY_BROWSER_CHANNEL || undefined,
    args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const context = await browser.newContext({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
  const page = await context.newPage(); page.setDefaultTimeout(12000);
  const errors=[], requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST') requests.push(r.url());});
  const shot = async name => {
    if(!process.env.FRIDAY_DESIGN_SHOTS) return;
    fs.mkdirSync(process.env.FRIDAY_DESIGN_SHOTS,{recursive:true});
    await page.screenshot({path:path.join(process.env.FRIDAY_DESIGN_SHOTS,name+'.png')});
  };
  const desktopView = name => page.locator('.fx-desktop-views').getByRole('button',{name,exact:true});
  const go = async name => {
    if(name==='Settings') return switchTo(name);
    if(name==='Day') return page.getByTitle('Return to desktop',{exact:true}).click();
    if(name==='Projects'||name==='Activity') return page.locator('.top-bar .fx-shell-entry').filter({hasText:new RegExp('^'+name+'$')}).click();
    if(!await desktopView(name).isVisible()) await go('Projects');
    await desktopView(name).click();
  };
  const switchTo = async name => {
    await page.locator('.fx-switcher-trigger').click();
    await page.locator('.fx-switcher-search').fill(name);
    await page.locator('.fx-switcher-option').filter({has:page.getByText(name,{exact:true})}).click();
  };
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await expect(page.locator('.fx-main')).toHaveAttribute('data-view','day');
  await expect(page.locator('.fx-preview-label')).toContainText('Sample data');
  await shot('01-day'); passed('Day uses actual application shell with visible sample label');

  await go('Projects');
  await expect(desktopView('Projects')).toHaveAttribute('aria-current','page');
  await expect(page.locator('.fx-project-detail')).toContainText('Northstar launch');
  await shot('02-projects'); passed('Projects and compact navigation selection agree');
  await page.getByRole('button',{name:'New project',exact:true}).click();
  await page.getByLabel('Project name',{exact:true}).fill('Review workspace');
  await page.getByRole('button',{name:'Save project',exact:true}).click();
  await expect(page.locator('.fx-project-detail')).toContainText('Review workspace');
  passed('project creation updates real project UI through sample API');
  await page.locator('.fx-project-list').getByRole('button',{name:/Northstar launch/}).click();

  await go('Workspaces');
  await page.getByRole('button',{name:'Shape this view',exact:true}).click();
  await page.locator('.fx-view-controls').getByRole('button',{name:'List',exact:true}).click();
  await page.getByRole('button',{name:'Compact',exact:true}).click();
  await expect(page.locator('.fx-main')).toHaveAttribute('data-layout-preview','true');
  await expect(page.locator('.fx-main')).toHaveAttribute('data-arrangement','list');
  await page.locator('.fx-view-controls').getByRole('button',{name:'Cancel',exact:true}).click();
  await expect(page.locator('.fx-main')).toHaveAttribute('data-arrangement','grid');
  await page.getByRole('button',{name:'Shape this view',exact:true}).click();
  await page.getByRole('button',{name:'Compact',exact:true}).click();
  await page.getByRole('button',{name:'Apply',exact:true}).click();
  await expect(page.locator('.fx-main')).toHaveAttribute('data-density','compact');
  await page.getByRole('button',{name:'Shape this view',exact:true}).click();
  await page.getByRole('button',{name:'Restore previous layout',exact:true}).click();
  await expect(page.locator('.fx-main')).toHaveAttribute('data-density','comfortable');
  passed('layout preview, cancel, apply and restore change real view state');

  await go('Projects');
  await page.getByRole('button',{name:'Browse Library',exact:true}).click();
  await page.locator('.lb-root').getByRole('button',{name:'List',exact:true}).click();
  await page.locator('.lb-table tbody tr').filter({hasText:'Launch brief'}).click();
  await expect(page.locator('.fm-selection')).toContainText('Northstar launch');
  await page.locator('.fm-selection').getByRole('button',{name:/^Gather /}).click();
  await expect(page.locator('.fm-selection').getByRole('button',{name:/^Gathered /})).toBeDisabled();
  await page.locator('.lb-table tbody tr').filter({hasText:'Visual direction'}).click();
  await page.locator('.fm-selection').getByRole('button',{name:/^Gather /}).click();
  await shot('03-library-gather');
  await page.locator('.fm-selection').getByRole('button',{name:/Review materials/}).click();
  await expect(page.locator('.fm-tray .fm-row')).toHaveCount(2);
  await page.locator('.fm-tray').getByRole('checkbox').nth(0).check();
  await page.locator('.fm-tray').getByRole('checkbox').nth(1).check();
  await page.getByRole('button',{name:'Compare two',exact:true}).click();
  await expect(page.locator('.fm-compare article')).toHaveCount(2);
  await shot('04-materials-compare'); passed('Library selection gathers without leaving, deduplicates and compares in its project');
  await page.getByRole('button',{name:'Open source: Launch brief',exact:true}).click();
  await expect(page.locator('.lb-root')).toBeVisible();
  await expect(page.locator('.lr-root')).toBeVisible();
  await go('Projects');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.lr-root').count(),1);
  await expect(page.locator('.fm-tray .fm-row')).toHaveCount(2);
  passed('Open source returns to Library without dropping gathered references');

  await page.locator('.fx-project-detail').getByRole('button',{name:/A clearer opening for the launch/}).click();
  const input = page.locator('.chat-panel [data-chat-input]');
  await input.fill('Only this launch conversation.');
  await go('Projects');
  await page.locator('.fx-project-list').getByRole('button',{name:/Field notes/}).click();
  await expect(page.locator('.fm-tray .fm-row')).toHaveCount(0);
  await page.locator('.fx-project-detail').getByRole('button',{name:/Ideas worth returning to/}).click();
  await expect(input).toHaveValue('');
  await input.fill('Only the field notes.');
  await go('Projects');
  await page.locator('.fx-project-list').getByRole('button',{name:/Northstar launch/}).click();
  await page.getByRole('button',{name:'Discuss materials',exact:true}).click();

  await expect(input).toHaveValue(/Only this launch conversation\.[\s\S]*Launch brief[\s\S]*Visual direction/);
  assert(!(await input.inputValue()).includes('Only the field notes.'));
  const inputBox=await input.boundingBox(), composerBox=await page.locator('.chat-panel .fx-chat-composer').boundingBox();
  assert(inputBox.width >= composerBox.width-24, 'The chat draft fills the composer inside its frame');
  assert.equal(requests.filter(u=>/\/api\/chat(?:\/stream)?$/.test(u)).length,0);
  passed('project materials and unsent drafts remain separate; Discuss never sends');

  await page.locator('.fx-switcher-trigger').click();
  await page.locator('.fx-switcher-search').fill('Media');
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.fx-switcher-option')).toBeFocused();
  await shot('05-workspace-switcher');
  await page.keyboard.press('Escape');
  await expect(page.locator('.fx-switcher-popover')).toHaveCount(0);
  await expect(page.locator('.fx-switcher-trigger')).toBeFocused();
  passed('workspace menu search, keyboard focus and Escape restoration');

  // A browser popup refusal must stay visible and truthful.
  await page.evaluate(()=>{window.__testOpen=window.open;window.open=()=>null;});
  await page.locator('.fx-switcher-trigger').click(); await page.locator('.fx-switcher-search').fill('Media');
  await page.locator('.fx-switcher-option').click({modifiers:['Control']});
  await expect(page.locator('.fx-switcher-error')).toContainText('could not open');
  await page.evaluate(()=>{window.open=window.__testOpen;delete window.__testOpen;});
  await page.keyboard.press('Escape'); passed('blocked new-tab opens show an actionable error');

  await switchTo('Library');
  await page.getByRole('button',{name:'Browse this PC',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Search files',exact:true})).toBeVisible();
  await expect(page.locator('.lb-insp')).toHaveCount(0);
  await page.getByRole('button',{name:'▥ City',exact:true}).click();
  const fileSurface=page.locator('.ff3-body [tabindex="0"]').first();
  await fileSurface.focus(); await page.keyboard.press('ArrowRight');
  await expect(page.locator('.fm-selection strong')).toContainText('.md');
  const fileName=await page.locator('.fm-selection strong').innerText();
  await page.locator('.fm-gather').click(); await shot('09-spatial-files');
  await page.getByRole('button',{name:'Review materials',exact:true}).click();
  await expect(page.locator('.fm-tray .fm-row')).toHaveCount(3);
  await page.getByRole('button',{name:'Open source: '+fileName,exact:true}).click();
  await expect(page.getByRole('button',{name:'▥ City',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.fm-selection strong')).toHaveText(fileName);
  passed('spatial files gather by keyboard and return to the same arrangement and selection');

  await switchTo('Media');
  await page.locator('.md-card').filter({hasText:'Opening direction'}).click();
  await expect(page.locator('.fm-selection strong')).toHaveText('Opening direction');
  if (await page.locator('.md-ql').isVisible()) await page.getByRole('button',{name:'Close quick look',exact:true}).click();
  await page.locator('.fm-gather').click(); await shot('10-media');
  await page.getByRole('button',{name:'Review materials',exact:true}).click();
  await expect(page.locator('.fm-tray .fm-row')).toHaveCount(4);
  const checkbox=page.locator('.fm-tray input[type="checkbox"]').first();
  await checkbox.focus(); await page.keyboard.press('Space');
  await expect(checkbox).toBeChecked();
  await expect(page.locator('.md-ql')).toHaveCount(0);
  passed('Media shares the materials flow and hidden workspaces do not capture its keys');

  await page.goto(base+'/?chrome=chat&conversation=design-field',{waitUntil:'domcontentloaded'});
  await expect(page.locator('.top-bar')).toHaveAttribute('data-shell-kind','chat');
  await input.fill('Keep this draft while I switch tabs.');
  await switchTo('Library');
  await page.waitForURL('**/w/library?**');
  await expect(page.locator('.top-bar')).toHaveAttribute('data-shell-kind','tab');
  assert(new URL(page.url()).searchParams.get('conversation')==='design-field');
  assert(!new URL(page.url()).searchParams.has('draft_handoff'));
  await expect(input).toHaveValue('Keep this draft while I switch tabs.');
  await switchTo('Media'); await page.waitForURL('**/w/media?**');
  await expect(input).toHaveValue('Keep this draft while I switch tabs.');
  await page.locator('.fx-switcher-trigger').click();
  await expect(page.locator('.fx-switcher-option[aria-current="page"]')).toContainText('Media');
  await page.getByRole('button',{name:'← Back to desktop',exact:true}).click();
  await expect(page.locator('.fx-main')).toBeVisible();
  await expect(input).toHaveValue('Keep this draft while I switch tabs.');
  passed('chat → workspace → workspace → desktop preserves conversation and draft');

  await go('Activity'); await expect(page.locator('#fx-activity-panel')).toContainText('Review the launch direction');
  await page.getByRole('tab',{name:/Recent/}).click();
  await expect(page.locator('#fx-activity-panel')).toContainText('Finished · unverified');
  await shot('06-activity'); passed('Activity resolves loading and preserves unverified task status');

  await go('Settings'); await expect(page.locator('.fx-settings-heading')).toBeVisible(); await shot('07-settings');
  await go('Day'); await page.setViewportSize({width:1024,height:768});
  await expect(page.locator('.fx-main')).toBeVisible(); await shot('08-compact-desktop');
  const overflow = await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  assert.equal(overflow,false);
  await expect.poll(()=>page.evaluate(()=>document.querySelector('.dock').getBoundingClientRect().top-document.querySelector('.fx-shell').getBoundingClientRect().bottom),{message:'Desktop settles above the wrapped dock'}).toBeGreaterThanOrEqual(8);
  await expect(page.locator('.fx-rail')).toHaveCount(0);
  await expect(page.locator('.fr-lockup-maker').first()).toBeVisible();
  passed('settings and compact desktop render without overflow or dock overlap');
  await page.setViewportSize({width:1600,height:1000});
  const registry = fs.readFileSync(path.join(root,'static/workspace_registry.js'),'utf8');
  const workspaces = JSON.parse(registry.split('/*BEGIN JSON*/')[1].split('/*END JSON*/')[0]).workspaces.filter(w=>!w.held);
  for(const workspace of workspaces){
    await page.goto(base+'/?workspace='+workspace.id,{waitUntil:'domcontentloaded'});
    await expect(page.locator('.fwin[data-friday-workspace="'+workspace.id+'"] .ws-custom-root')).toBeVisible();
    await page.waitForTimeout(650);
    await expect(page.locator('.friday-surface-error')).toHaveCount(0);
  }
  passed('all 17 native workspace families render without contained component errors');
  await page.route('**/api/governance/receipts**',route=>route.fulfill({status:200,contentType:'application/json',body:'{"status":"ok"}'}));
  await page.goto(base+'/?workspace=system',{waitUntil:'domcontentloaded'});
  await expect(page.locator('.friday-surface-error')).toContainText('allow');
  await page.unroute('**/api/governance/receipts**');
  await page.reload({waitUntil:'domcontentloaded'});
  await expect(page.getByText('What Friday did',{exact:false}).first()).toBeVisible();
  await expect(page.locator('.friday-surface-error')).toHaveCount(0);
  passed('System detects a missing receipt schema and recovers with the complete native fixture');
  assert.deepEqual(errors,[]); passed('no uncaught application errors across exercised surfaces');
  console.log(checks+' checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();});
