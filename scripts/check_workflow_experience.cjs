/* Workflow review against scripts/preview_spatial_desktop.cjs only.
 * All workflow reads and writes are synthetic browser-route fixtures. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const base = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3194';
const output = process.env.FRIDAY_WORKFLOW_PROOFS;
const baseline = process.env.FRIDAY_BASELINE_REV;
const artifactBaseline = process.env.FRIDAY_ARTIFACT_BASELINE_REV;
const versionBaseline = process.env.FRIDAY_ARTIFACT_VERSION_BASELINE_REV;
const origin = new URL(base);
assert.ok(['127.0.0.1', 'localhost'].includes(origin.hostname), 'Only a loopback synthetic preview is allowed');
const gate = () => assert.ok(os.freemem() >= 6 * 1073741824, 'At least 6 GB free is required');
const now = Math.floor(Date.now() / 1000);
const projects = [{id:'sample-project',name:'Field research'},{id:'other-project',name:'Creative notes'}];
const conversations = [{id:'sample-chat',title:'Research conversation',project_id:'sample-project'},{id:'other-chat',title:'Creative conversation',project_id:'other-project'}];
const seed = () => [{
  id:'wf:research-brief',slug:'research-brief',name:'Weekly research brief',description:'Compare the source notes and return an editable brief.',
  project_id:'sample-project',conversation_id:'sample-chat',inputs:['Source notes'],success_criteria:'Cite the sources and mark uncertainty.',output:{kind:'artifact',title:'Research brief'},notify:'on_change',revision:2,
  steps:[{name:'Read the sources',prompt:'Read the project notes.',retries:2,with_context:false,seat:'sample-seat'},{name:'Write the brief',prompt:'Prepare an editable brief with source references.'}],
  when:null,when_text:'Only when you run it',enabled:true,running:false,asks_first:[],
  last_run:{run_id:'run-unverified',status:'unverified',at:now-60,summary:'A draft was prepared, but its saved artifact could not be confirmed.',conversation_id:'sample-chat',task_ids:['sample-task'],verification:{status:'unverified',checks:[{name:'artifact_saved',status:'unverified',detail:'No matching artifact was read back.'}]},delivery:{status:'failed',error:'The conversation write was interrupted.'},outputs:[]}
}, {
  id:'wf:notes-digest',slug:'notes-digest',name:'Notes digest',description:'Keep a short collection of useful ideas.',
  project_id:'other-project',conversation_id:'other-chat',inputs:[],success_criteria:'Keep source links.',output:{kind:'artifact'},notify:'on_complete',revision:1,
  steps:[{name:'Collect the ideas',prompt:'Read the notes and save a digest.'}],when:{trigger:'daily',spec:{hour:9,minute:0}},when_text:'Every day at 9 AM',enabled:true,running:false,asks_first:[],
  last_run:{run_id:'run-verified',status:'finished',at:now-300,summary:'The saved digest is ready to review.',conversation_id:'other-chat',task_ids:['other-task'],verification:{status:'verified',scope:'deliverable_structure',checks:[{name:'artifact_saved',status:'passed',detail:'The saved version was read back.'},{name:'requested_quality',status:'unverified',detail:'Source quality still needs review.'}]},delivery:{status:'delivered'},outputs:[{kind:'artifact',artifact_id:'sample-digest',conversation_id:'other-chat',version:2,title:'Saved digest'}]}
}, {id:'sch:sample-schedule',slug:null,schedule_id:'sample-schedule',name:'Legacy source check',description:'A scheduled prompt saved before workflow definitions.',steps:[{name:'Check sources',prompt:'Read the selected source.'}],when:{trigger:'daily',spec:{hour:10,minute:0}},when_text:'Every day at 10 AM',enabled:true,running:false,asks_first:[],last_run:null}];
const starter = {id:'sample-starter',name:'Research brief',description:'Turn selected sources into an editable brief.',steps:[{name:'Read and draft',prompt:'Read the selected sources and save an editable brief.'}],output:{kind:'artifact',title:'Research brief'},success_criteria:'Link the sources.'};
const errors = [], frames = [];

async function capture(page, name, selector = '.wfx') {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const geometry = await page.evaluate(selector => {
    const host = document.querySelector(selector);
    const rect = host.getBoundingClientRect();
    const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
    const overflowing = [...host.querySelectorAll('input,select,textarea,button,a')].filter(visible).filter(e => {const r=e.getBoundingClientRect();return r.left < rect.left-2 || r.right > rect.right+2;}).map(e=>e.getAttribute('aria-label')||e.textContent);
    return {width:innerWidth,height:innerHeight,style:window.FridayDisplayStyle.get(),host:{left:rect.left,right:rect.right,width:rect.width},overflowing};
  }, selector);
  assert.deepEqual(geometry.overflowing, [], name+': controls leave the workspace');
  assert.ok(geometry.host.right <= geometry.width+2, name+': workspace leaves the viewport');
  frames.push({name,...geometry});
  if(output) await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});
  console.log('PASS '+name);
}
async function topOfWorkspace(page) {
  await page.locator('.wfx').evaluate(el => { for(let p=el;p;p=p.parentElement) p.scrollTop=0; });
}

(async () => {
  gate();
  const probe = await fetch(base+'/api/seat').then(r=>r.json());
  assert.equal(probe.preview,true,'Refusing a server that is not the synthetic preview');
  if(output) { const relative=path.relative(root,path.resolve(output)); assert.ok(relative.startsWith('..') || path.isAbsolute(relative),'Screenshots stay outside the repository'); fs.mkdirSync(output,{recursive:true}); }
  const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
  if(executablePath!==undefined){
    assert.ok(path.isAbsolute(executablePath),'The explicit test browser must use an absolute path');
    assert.ok(fs.statSync(executablePath).isFile(),'The explicit test browser must be an existing file');
  }
  const browserSelection={requested_executable:executablePath??null,channel:executablePath===undefined?'chrome':null};
  const browser=await chromium.launch({headless:true,...(executablePath===undefined?{channel:'chrome'}:{executablePath})});
  try {
    browserSelection.version=browser.version();
    const page = await browser.newPage({viewport:{width:1480,height:1000},reducedMotion:'reduce'});
    page.setDefaultTimeout(15000);
    page.on('pageerror',e=>errors.push(e.message));
    const requests=[]; let workflows=seed(); let failRepeat=true;
    const templates=[{id:'career-search',slug:'career-search',name:'Career search',description:'Find and evaluate selected roles using your career profile.',step_count:3,installed:false}];
    await page.route('**/api/schedules/sample-schedule**',async route=>{
      const request=route.request(),pathname=new URL(request.url()).pathname,body=request.postDataJSON();
      requests.push({pathname,body});const w=workflows.find(x=>x.schedule_id==='sample-schedule');
      if(pathname.endsWith('/run-now'))w.running=true;else if(request.method()==='PATCH')w.enabled=body.enabled;
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'ok',run_id:'legacy-run',schedule:{...w}})});
    });
    await page.route('**/api/workflows/**',async route=>{
      const req=route.request(),pathname=new URL(req.url()).pathname;
      const answer=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      if(pathname.endsWith('/overview'))return answer({status:'ok',workflows,projects,conversations,templates,routines:[],pending_approvals:0});
      if(pathname.endsWith('/starters'))return answer({status:'ok',starters:[starter]});
      const body=req.postDataJSON(); requests.push({pathname,body});
      if(pathname.endsWith('/templates/career-search/add')){
        const created=!templates[0].installed;templates[0].installed=true;
        if(created)workflows.push({id:'wf:career-search',slug:'career-search',name:'Career search',description:templates[0].description,steps:[{name:'Review the career profile',prompt:'Read the available career profile.',retries:0,with_context:true}],revision:1,when:null,enabled:true,running:false,asks_first:[]});
        return answer({status:'ok',slug:'career-search',created,schedule_id:null});
      }
      if(pathname.endsWith('/save')){
        const slug=body.slug||'new-research',existing=workflows.find(w=>w.slug===slug);
        const value={...body,id:'wf:'+slug,slug,when_text:body.when?'Every weekday at 9 AM':'Only when you run it',running:false,asks_first:[],last_run:existing?.last_run};
        workflows=workflows.filter(w=>w.slug!==slug).concat(value);
        return answer({status:'ok',slug});
      }
      if(pathname.endsWith('/action')){
        const w=workflows.find(x=>x.slug===body.slug);
        if(body.action==='repeat'&&failRepeat){failRepeat=false;return answer({status:'error',message:'The selected model is unavailable. Choose a ready model before trying again.'},409);}
        if(body.action==='repeat'){w.running=true;w.last_run={...w.last_run,run_id:'run-repeated',status:'running',steps:[{status:'waiting_for_approval',task_id:'sample-task'}]};}
        if(body.action==='retry_delivery')w.last_run.delivery={status:'delivered'};
        if(body.action==='stop'){w.running=false;w.last_run={...w.last_run,status:'stopped'};}
        if(body.action==='pause')w.enabled=false;
        if(body.action==='resume')w.enabled=true;
        return answer({status:'ok',note:body.action==='learn'?'Reusable procedure saved.':'Request accepted.'});
      }
      if(pathname.endsWith('/draft'))return answer({status:'ok',draft:{...starter,request:body.text}});
      return answer({status:'error',message:'Unknown synthetic operation'},400);
    });
    if(baseline){
      const previous=spawnSync('git',['show',baseline+':index.html'],{cwd:root,encoding:'utf8',maxBuffer:8e6,windowsHide:true});assert.equal(previous.status,0,previous.stderr);
      await page.route('**/w/workflows*',route=>route.fulfill({status:200,contentType:'text/html',body:previous.stdout}));
    }
    await page.goto(base+'/w/workflows');
    const research=page.getByTestId('wf-card').filter({hasText:'Weekly research brief'});
    const digest=page.getByTestId('wf-card').filter({hasText:'Notes digest'});
    await expect(research).toContainText('Finished · not verified');
    await research.getByRole('button',{name:'Details',exact:true}).click();
    await expect(research).toContainText('Result not verified');
    await expect(research).toContainText('Delivery failed');
    await expect(research.getByRole('button',{name:'Keep as reusable procedure'})).toHaveCount(0);
    await digest.getByRole('button',{name:'Details',exact:true}).click();
    await expect(digest).toContainText('Deliverable verified');
    await expect(digest).toContainText('requested quality: unverified');
    await expect(digest.getByRole('link',{name:'Saved digest'})).toHaveAttribute('href',/conversation=other-chat&artifact=sample-digest&version=2/);
    if(baseline)throw new Error('Baseline unexpectedly passed the new workflow contract');

    for(const style of ['simple','classic']){
      await page.evaluate(value=>window.FridayDisplayStyle.set(value),style);
      await expect.poll(()=>page.evaluate(()=>window.FridayDisplayStyle.get())).toBe(style);
      await page.setViewportSize({width:1480,height:1000});await topOfWorkspace(page);await capture(page,style+'-workflow-results');
      await page.setViewportSize({width:390,height:844});await topOfWorkspace(page);await capture(page,style+'-workflow-results-compact');
    }
    await page.setViewportSize({width:1480,height:1000});
    const repeatsBeforeDelivery=requests.filter(r=>r.body.action==='repeat').length;
    await research.getByRole('button',{name:'Retry delivery',exact:true}).click();
    await expect(research).toContainText('Delivered to the conversation');
    assert.deepEqual(requests.find(r=>r.body.action==='retry_delivery').body,{action:'retry_delivery',slug:'research-brief',run_id:'run-unverified'});
    assert.equal(requests.filter(r=>r.body.action==='repeat').length,repeatsBeforeDelivery,'Retry delivery never executes the workflow again');
    await research.getByRole('button',{name:'Do again',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('selected model is unavailable');
    await expect(research.getByRole('button',{name:'Do again',exact:true})).toBeEnabled();
    await research.getByRole('button',{name:'Do again',exact:true}).click();
    await expect(research.getByRole('button',{name:'Stop run',exact:true})).toBeVisible();
    await expect(research).toContainText('Needs your decision');
    await research.getByRole('button',{name:'Stop run',exact:true}).click();
    await expect(research).toContainText('Stopped partway');
    assert.deepEqual(requests.find(r=>r.body.action==='stop').body,{action:'stop',slug:'research-brief',run_id:'run-repeated'});
    await digest.getByRole('switch').click();await expect(digest.getByRole('switch')).toHaveAttribute('aria-checked','false');
    await digest.getByRole('switch').click();await expect(digest.getByRole('switch')).toHaveAttribute('aria-checked','true');
    await digest.getByRole('button',{name:'Keep as reusable procedure'}).click();await expect(page.getByRole('status')).toContainText('Reusable procedure saved');
    assert.ok(requests.some(r=>r.body.action==='learn'&&r.body.run_id==='run-verified'));
    const legacy=page.getByTestId('wf-card').filter({hasText:'Legacy source check'});
    await legacy.getByRole('switch').click();await expect(legacy.getByRole('switch')).toHaveAttribute('aria-checked','false');
    await legacy.getByRole('switch').click();await expect(legacy.getByRole('switch')).toHaveAttribute('aria-checked','true');
    await legacy.getByRole('button',{name:'Run now',exact:true}).click();await expect(legacy.getByRole('button',{name:'Run in progress',exact:true})).toBeVisible();
    assert.ok(requests.some(r=>r.pathname==='/api/schedules/sample-schedule/run-now'),'Legacy schedules run through their own scoped endpoint');

    await research.getByRole('button',{name:'Edit',exact:true}).click();
    await expect(page.getByRole('combobox',{name:'Workflow project',exact:true})).toHaveValue('sample-project');
    await expect(page.getByRole('combobox',{name:'Result conversation',exact:true})).toHaveValue('sample-chat');
    await page.getByRole('combobox',{name:'Workflow project',exact:true}).selectOption('other-project');
    await expect(page.getByRole('combobox',{name:'Result conversation',exact:true})).toHaveValue('');
    await page.getByRole('combobox',{name:'Result conversation',exact:true}).selectOption('other-chat');
    await page.getByRole('combobox',{name:'Workflow notifications',exact:true}).selectOption('on_change');
    await page.locator('#wf-inputs').fill('Source notes\nA second source');
    await page.locator('#wf-checks').fill('Cite both sources and distinguish claims from evidence.');
    for(const style of ['simple','classic']){
      await page.evaluate(value=>window.FridayDisplayStyle.set(value),style);
      await page.setViewportSize({width:1480,height:1000});await capture(page,style+'-workflow-editor');
      await page.setViewportSize({width:390,height:844});await capture(page,style+'-workflow-editor-compact');
    }
    await page.getByRole('button',{name:'Save changes',exact:true}).click();
    await expect(page.getByTestId('wf-editor')).toHaveCount(0);
    const saved=requests.filter(r=>r.pathname.endsWith('/save')).at(-1).body;
    assert.equal(saved.revision,2,'Saving includes the revision that was edited');
    assert.equal(saved.project_id,'other-project');assert.equal(saved.conversation_id,'other-chat');assert.equal(saved.notify,'on_change');
    assert.deepEqual(saved.inputs,['Source notes','A second source']);assert.equal(saved.steps[0].retries,2);assert.equal(saved.steps[0].with_context,false);assert.equal(saved.steps[0].seat,'sample-seat');
    await research.getByRole('button',{name:'Details',exact:true}).click();
    await research.getByRole('button',{name:'Make routine',exact:true}).click();
    await expect(page.getByRole('combobox',{name:'How often',exact:true})).toHaveValue('weekdays');
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    await page.getByRole('combobox',{name:'Filter workflows by project',exact:true}).selectOption('sample-project');
    await expect(page.getByTestId('wf-card')).toHaveCount(0);
    await page.getByRole('combobox',{name:'Filter workflows by project',exact:true}).selectOption('');
    await page.getByText('Start from an example',{exact:true}).click();
    await expect(page.getByText('Start from an example',{exact:true})).toHaveCount(1);
    await expect(page.getByTestId('wf-career-starter')).toContainText('Choose when to run after adding');
    for(const style of ['simple','classic']){
      await page.evaluate(value=>window.FridayDisplayStyle.set(value),style);
      await page.setViewportSize({width:1480,height:1000});await topOfWorkspace(page);await capture(page,style+'-workflow-starters');
      await page.setViewportSize({width:390,height:844});await topOfWorkspace(page);await capture(page,style+'-workflow-starters-compact');
    }
    const beforeCareerAdd=requests.length;
    await page.getByRole('button',{name:'Add Career search workflow',exact:true}).click();
    await expect(page.getByTestId('wf-career-starter').getByRole('button',{name:'Review workflow',exact:true})).toBeVisible();
    assert.deepEqual(requests.slice(beforeCareerAdd).map(r=>r.pathname),['/api/workflows/templates/career-search/add'],'Adding Career saves it without running or scheduling');
    await page.getByTestId('wf-career-starter').getByRole('button',{name:'Review workflow',exact:true}).click();
    await expect(page.locator('#wf-name')).toHaveValue('Career search');
    await expect(page.getByRole('combobox',{name:'How often',exact:true})).toHaveValue('manual');
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    await page.getByText('Start from an example',{exact:true}).click();
    const beforeSave=requests.filter(r=>r.pathname.endsWith('/save')).length;
    await page.getByRole('button',{name:'Research brief Turn selected sources into an editable brief.'}).click();
    await expect(page.getByTestId('wf-editor')).toBeVisible();
    assert.equal(requests.filter(r=>r.pathname.endsWith('/save')).length,beforeSave,'A starter must remain an unsaved draft');
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    await page.goto(base+'/w/workflows?project_id=other-project');
    await expect(page.getByRole('combobox',{name:'Filter workflows by project',exact:true})).toHaveValue('other-project');
    await expect(page.getByTestId('wf-card')).toHaveCount(2);
    workflows[0].running=true;workflows[0].last_run={...workflows[0].last_run,status:'running',steps:[{status:'queued-for-seat',task_id:'sample-task'}]};
    await page.reload();
    await expect(page.getByTestId('wf-card').filter({hasText:workflows[0].name})).toContainText('Waiting for a model');

    await page.setViewportSize({width:1480,height:1000});
    await page.goto(base+'/?workspace=settings&tab=voice');
    await page.evaluate(()=>window.fridayOpenWorkspace({workspace:'settings',tab:'voice',section:'Conversation style'}));
    const voice=page.locator('[data-st-section="Conversation style"]');
    await expect(page.getByRole('combobox',{name:'Voice answer depth',exact:true})).toBeVisible();
    await page.getByRole('combobox',{name:'Voice answer depth',exact:true}).selectOption('detailed');
    await page.getByRole('combobox',{name:'Voice speaking pace',exact:true}).selectOption('measured');
    await expect.poll(()=>page.evaluate(()=>fetch('/api/settings').then(r=>r.json()).then(d=>d.settings.voice_speaking_pace))).toBe('measured');
    for(const style of ['simple','classic']){
      await page.evaluate(value=>window.FridayDisplayStyle.set(value),style);
      for(const width of [1480,390]){
        await page.setViewportSize({width,height:width===390?844:1000});
        await page.evaluate(()=>fridayScrollToSettingsSection('Conversation style'));
        if(width===390)await expect.poll(()=>voice.evaluate(el=>el.getBoundingClientRect().width)).toBeGreaterThan(260);
        await capture(page,style+'-voice-preferences'+(width===390?'-compact':''),'[data-st-section="Conversation style"]');
      }
    }

    const artifactReads=[];
    const sampleArtifact={id:'another-artifact',kind:'markdown',title:'A different saved artifact',version:3,content:'# Another document\n\nThis is the latest result.',created_at:now,updated_at:now};
    await page.route('**/api/artifacts**',route=>{
      const u=new URL(route.request().url());artifactReads.push({pathname:u.pathname,version:u.searchParams.get('version')});
      if(u.searchParams.get('version')==='99')return route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({status:'error',message:'Version unavailable'})});
      const record=u.searchParams.get('version')==='2'?{...sampleArtifact,version:2,content:'# Evidence version\n\nThe exact saved result for this run.'}:sampleArtifact;
      const body=u.pathname==='/api/artifacts'?{status:'ok',artifacts:[sampleArtifact]}:{status:'ok',artifact:record,versions:[]};
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
    });
    if(artifactBaseline||versionBaseline){
      const previous=spawnSync('git',['show',(artifactBaseline||versionBaseline)+':static/friday_artifacts.js'],{cwd:root,encoding:'utf8',maxBuffer:2e6,windowsHide:true});assert.equal(previous.status,0,previous.stderr);
      await page.route('**/static/friday_artifacts.js*',route=>route.fulfill({status:200,contentType:'text/javascript',body:previous.stdout}));
    }
    await page.setViewportSize({width:1480,height:1000});
    const missing=page.getByRole('complementary',{name:'Requested artifact',exact:true});
    if(!versionBaseline){
      await page.goto(base+'/?chrome=chat&conversation=design-direction&artifact=missing-artifact');
      await expect(missing).toHaveAttribute('data-artifact-link-state','missing');
      await expect(missing).toContainText('This artifact is no longer available in this conversation.');
      assert.ok(!artifactReads.some(r=>r.pathname.endsWith('/another-artifact')),'An exact result link never opens another artifact automatically');
      if(artifactBaseline)throw new Error('Baseline unexpectedly passed exact artifact destination');
      await capture(page,'missing-artifact-link','.fa-host');
      await missing.getByRole('button',{name:'Browse other artifacts',exact:true}).click();
      await expect(page.locator('.fa-panel')).toContainText('Another document');
      assert.ok(artifactReads.some(r=>r.pathname.endsWith('/another-artifact')),'Browsing another artifact is an explicit choice');
    }
    await page.goto(base+'/?chrome=chat&conversation=design-direction&artifact=another-artifact&version=2');
    await expect(page.getByRole('complementary',{name:'Artifact panel',exact:true})).toHaveAttribute('data-artifact-shown','another-artifact@2');
    if(versionBaseline)throw new Error('Baseline unexpectedly passed exact artifact version');
    await expect(page.locator('.fa-panel')).toContainText('The exact saved result for this run.');
    assert.ok(artifactReads.some(r=>r.pathname.endsWith('/another-artifact')&&r.version==='2'),'Result links read the evidenced version');
    await capture(page,'exact-artifact-version','.fa-host');
    const beforeMissingVersion=artifactReads.length;
    await page.goto(base+'/?chrome=chat&conversation=design-direction&artifact=another-artifact&version=99');
    await expect(missing).toHaveAttribute('data-artifact-link-state','missing-version');
    await expect(missing).toContainText('This artifact version is no longer available.');
    assert.ok(!artifactReads.slice(beforeMissingVersion).some(r=>r.pathname.endsWith('/another-artifact')&&r.version!=='99'),'An unavailable version never falls back to latest');
    await capture(page,'missing-artifact-version','.fa-host');
    await missing.getByRole('button',{name:'Browse other artifacts',exact:true}).click();
    await expect(page.getByRole('complementary',{name:'Artifact panel',exact:true})).toHaveAttribute('data-artifact-shown','another-artifact@3');
    assert.deepEqual(errors,[],'No browser errors');
    if(output)fs.writeFileSync(path.join(output,'workflow-proof.json'),JSON.stringify({browser:browserSelection,frames,actions:requests.map(r=>r.body.action).filter(Boolean),errors},null,2));
    console.log('PASS workflow editing, ownership, outcome honesty, scoped actions and responsive display styles');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
