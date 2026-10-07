/* Isolated UI fixture: actual desktop scene and Crew UI, synthetic records only. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawn}=require('node:child_process');
const {chromium,expect}=require('@playwright/test');
const root=path.resolve(__dirname,'..'),port=Number(process.env.FRIDAY_CREW_TEST_PORT||3192),base='http://127.0.0.1:'+port;
for(const file of ['friday_crew.js','friday_crew_playback.js','js/friday_pcm_player.worklet.js'])new vm.Script(fs.readFileSync(path.join(root,'static',file),'utf8'));
for(const match of fs.readFileSync(path.join(root,'index.html'),'utf8').matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g))if(!match[1].includes('src=')&&!/type=["'](?!text\/javascript|application\/javascript)/.test(match[1]))new vm.Script(match[2]);
const {babelParse}=require(path.join(path.dirname(require.resolve('playwright/package.json')),'lib/transform/babelBundle'));
const mirror=fs.readFileSync(path.join(root,'ui_parts/app.html'),'utf8'),tag='<script type="text/babel">';
babelParse(mirror.slice(mirror.indexOf(tag)+tag.length,mirror.lastIndexOf('</script>')),'mirror.jsx',false);
const server=spawn(process.execPath,[path.join(__dirname,'preview_spatial_desktop.cjs')],{cwd:root,env:{...process.env,FRIDAY_DESIGN_PORT:String(port)},windowsHide:true,stdio:['ignore','pipe','pipe']});
let browser,activePage;
const profile={id:'crew-1234567890abcdef',revision:1,status:'active',name:'Mira',role:'Research',persona:'Careful and concise.',provider:'sample-cloud',model:'sample-reasoner',voice:{provider:'sample-voice',model:'sample-speech',voice_id:'sample_voice'},caption:{label:'Mira'},project_ids:['design-launch'],grants:[],skills:[],allowed_tools:[],memory:{notes:'',read:false,write:false},max_steps:12,time_budget_s:180,offline:null};
let agents=[profile],room={conversation_id:'design-direction',revision:0,project_id:'design-launch',member_ids:[],enabled:false},messages=[],conflict=false;
let crewRoomWrites=0,taskError=false,hubTasks=[{task_id:'hub-task',agent_id:profile.id,speaker_name:'Mira',status:'running',conversation_id:'design-direction',project_id:'design-launch',created_at:1,updated_at:2}];
const capabilities={status:'ok',supported_tools:['read_file'],skills:[{id:'sample-skill',name:'Sample research',description:'Fixture skill'}],projects:[{id:'design-launch',name:'Northstar launch'}],providers:[{id:'sample-cloud',label:'Sample Cloud',available:true,models:[{id:'sample-reasoner',label:'Sample Reasoner'}]}],voice_providers:[{id:'sample-voice',label:'Sample Voice',available:true,models:[{id:'sample-speech',label:'Sample Speech'}],voices:[{id:'sample_voice',label:'Sample voice'}]}],offline:{operational:false,message:'Offline bindings are saved only.'},limits:{memory_notes:8000,persona:8000,role:1000,max_steps:200,time_budget_s:3600}};
(async()=>{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture did not start')),10000);server.stdout.on('data',d=>{if(String(d).includes('Design preview:')){clearTimeout(timer);resolve();}});server.on('error',reject);server.stderr.on('data',d=>process.stderr.write(d));});
  browser=await chromium.launch({headless:true,channel:process.env.FRIDAY_BROWSER_CHANNEL||undefined,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=activePage=await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(10000);
  const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE ERROR:',e.message);});
  await page.route('**/api/desktop/cards',route=>route.request().method()==='GET'
    ? route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',cards:[]})}) : route.fallback());
  await page.route('**/api/crew/**',async route=>{
    const request=route.request(),url=new URL(request.url()),method=request.method(),body=method==='GET'?{}:request.postDataJSON();
    let data,status=200;
    if(url.pathname.includes('/rooms/')&&method!=='GET')crewRoomWrites++;
    if(url.pathname.endsWith('/capabilities'))data=capabilities;
    else if(url.pathname.endsWith('/tasks')){data=taskError?{status:'error',message:'Sample task service unavailable'}:{status:'ok',tasks:hubTasks};status=taskError?503:200;}
    else if(url.pathname.endsWith('/agents')&&method==='GET')data={status:'ok',agents};
    else if(url.pathname.endsWith('/agents')&&method==='POST'){const agent={...body,id:'crew-fedcba0987654321',revision:1};agents.push(agent);data={status:'ok',agent};status=201;}
    else if(url.pathname.endsWith('/retire')){const id=url.pathname.split('/').at(-2),i=agents.findIndex(a=>a.id===id);assert.equal(body.revision,agents[i].revision);agents[i]={...agents[i],status:'retired',revision:body.revision+1};data={status:'ok',agent:agents[i]};}
    else if(url.pathname.includes('/agents/')&&method==='PATCH'){
      if(conflict){conflict=false;agents[0]={...agents[0],revision:2,role:'New saved role'};data={status:'error',message:'This agent changed. Reload before saving.',current:agents[0]};status=409;}
      else{assert.equal(body.revision,agents[0].revision);agents[0]={...agents[0],...body,revision:body.revision+1};data={status:'ok',agent:agents[0]};}
    }else if(url.pathname.endsWith('/turns')){
      if(method==='POST'){
        assert.equal(body.room_revision,room.revision);
        messages=[{id:'sample-request',role:'user',text:body.text,ts:1,meta:{kind:'crew_request',task_id:'sample-task'}},{id:'sample-result',role:'friday',text:'Here is the sample research result.',ts:2,meta:{kind:'crew_result',speaker_id:profile.id,speaker_name:'Mira',task_id:'sample-task',provider:'sample-cloud',model:'sample-reasoner',status:'completed'}}];
        data={status:'ok',task_id:'sample-task',agent_id:profile.id,request_id:body.request_id};status=202;
      }else data={status:'ok',messages,tasks:[]};
    }else{
      if(method==='PUT'){assert.equal(body.revision,room.revision);room={...room,...body,revision:room.revision+1};}
      data={status:'ok',room};
    }
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.route('**/api/conversations/design-direction/messages',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',messages})}));
  const open=async()=>{
    const entry=page.getByTitle('Crew agents and room');
    if(!await entry.isVisible())await page.getByRole('button',{name:'Open chat with Friday',exact:true}).click();
    await entry.click();await expect(page.getByRole('dialog',{name:'Friday Crew'})).toBeVisible();
    await expect(page.locator('.fr-crew-roster')).toContainText('Mira');
  };
  const shot=async name=>{if(process.env.FRIDAY_CREW_SHOTS){fs.mkdirSync(process.env.FRIDAY_CREW_SHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.FRIDAY_CREW_SHOTS,name+'.png')});}};
  await page.goto(base+'/?conversation=design-direction',{waitUntil:'domcontentloaded'});await open();
  await page.locator('.fr-crew-roster').getByRole('button',{name:/Mira/}).click();
  await page.getByLabel('Role',{exact:true}).fill('Unsaved research role');
  await page.getByRole('button',{name:'New agent',exact:true}).click();
  await page.locator('.fr-crew-roster').getByRole('button',{name:/Mira/}).click();
  await expect(page.getByLabel('Role',{exact:true})).toHaveValue('Unsaved research role');
  await page.getByRole('button',{name:'Close Crew'}).click();await open();
  await expect(page.getByLabel('Role',{exact:true})).toHaveValue('Unsaved research role');
  conflict=true;await page.getByRole('button',{name:'Save agent',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('This agent changed');
  await expect(page.getByLabel('Role',{exact:true})).toHaveValue('Unsaved research role');
  await page.getByRole('button',{name:'Load saved revision'}).click();
  await expect(page.getByLabel('Role',{exact:true})).toHaveValue('New saved role');
  await page.getByLabel('Role',{exact:true}).fill('Updated role');await page.getByRole('button',{name:'Save agent',exact:true}).click();
  await expect(page.locator('.fr-crew-notices')).toContainText('Agent saved');
  await page.locator('.fr-crew-members').getByLabel('Mira',{exact:true}).check();
  await page.getByRole('button',{name:'Assemble room',exact:true}).click();
  await expect(page.getByRole('button',{name:'Save room',exact:true})).toBeVisible();
  await page.getByLabel('Hand off to',{exact:true}).selectOption(profile.id);
  await page.getByLabel('Request',{exact:true}).fill('Compare these sample ideas.');await page.getByRole('button',{name:'Hand off',exact:true}).click();
  await expect(page.locator('.fr-crew-byline')).toContainText('Mira',{timeout:8000});
  await shot('crew-desktop');
  await page.getByLabel('Voice provider',{exact:true}).scrollIntoViewIfNeeded();await shot('crew-profile-desktop');
  const geometry=await page.evaluate(()=>({dialog:(()=>{const b=document.querySelector('.fr-crew-dialog').getBoundingClientRect();return{x:b.x,y:b.y,w:b.width,h:b.height};})(),stage:window.FridayHolographicWorkspace?.state?.spatial?.stageFrame || window.FridayHolographicWorkspace?.stageRect}));
  if(geometry.stage){const a=geometry.dialog,b=geometry.stage;assert(a.x+a.w<=b.x+1||a.x>=b.x+b.w-1||a.y+a.h<=b.y+1||a.y>=b.y+b.h-1,'Crew preserves the avatar stage');}
  await page.getByRole('button',{name:'Close Crew'}).click();await page.reload({waitUntil:'domcontentloaded'});
  if(!await page.locator('.fr-crew-byline').isVisible())await page.getByRole('button',{name:'Open chat with Friday',exact:true}).click();
  await expect(page.locator('.fr-crew-byline')).toContainText('Mira');await expect(page.locator('.fr-crew-byline')).toHaveAttribute('title',/sample-task/);
  await page.evaluate(()=>window.FridayCrew.propose({name:'Suggested specialist',role:'Review',provider:'sample-cloud',model:'sample-reasoner'}));
  await expect(page.getByRole('dialog',{name:'Friday Crew'})).toBeVisible();await expect(page.getByLabel('Name',{exact:true})).toHaveValue('Suggested specialist');
  assert.equal(agents.length,1,'proposal is unsaved');
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.locator('.fr-crew-dialog').evaluate(e=>e.getBoundingClientRect().right)).toBeLessThanOrEqual(390);
  await expect.poll(()=>page.locator('.fr-crew-scroll').evaluate(e=>e.scrollWidth-e.clientWidth)).toBeLessThanOrEqual(1);
  await shot('crew-narrow');
  await page.getByLabel('Name',{exact:true}).scrollIntoViewIfNeeded();await shot('crew-profile-narrow');
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog',{name:'Friday Crew'})).toHaveCount(0);
  await page.setViewportSize({width:1600,height:1000});await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.locator('.fx-switcher-trigger').click();await page.locator('.fx-switcher-search').fill('Crew');
  await expect(page.locator('.fx-switcher-option').filter({hasText:'Crew'})).toHaveCount(1);
  await page.locator('.fx-switcher-option').filter({hasText:'Crew'}).click();
  const hub=page.getByTestId('crew-workspace');await expect(hub).toBeVisible();
  const card=hub.locator('[data-agent-id="'+profile.id+'"]');await expect(card).toContainText('sample-reasoner');await expect(card).toContainText('sample_voice');await expect(card).toContainText('Northstar launch');
  await expect(hub.locator('.fr-crew-work-list')).toContainText('Mira');await expect(hub.locator('.fr-crew-work-list')).toContainText('Working');
  await expect(hub.getByRole('link',{name:'Open chat',exact:true})).toHaveAttribute('target','_blank');await expect(hub.getByRole('link',{name:'Open chat',exact:true})).toHaveAttribute('rel',/noopener/);
  const roomWritesBeforeHub=crewRoomWrites;
  await hub.getByRole('button',{name:'Edit Mira',exact:true}).click();
  const editor=page.getByRole('dialog',{name:'Crew agent profile'});await expect(editor).toBeVisible();await expect(editor.getByText('This chat’s room',{exact:true})).toHaveCount(0);
  await editor.getByLabel('Role',{exact:true}).fill('Hub draft survives closing');await editor.getByRole('button',{name:'Close Crew'}).click();
  await page.locator('.fx-switcher-trigger').click();await page.locator('.fx-switcher-home').click();
  await page.locator('.fx-switcher-trigger').click();await page.locator('.fx-switcher-search').fill('Crew');await page.locator('.fx-switcher-option').filter({hasText:'Crew'}).click();
  await expect(hub).toBeVisible();await expect(editor).toHaveCount(0);
  await hub.getByRole('button',{name:'Edit Mira',exact:true}).click();await expect(editor.getByLabel('Role',{exact:true})).toHaveValue('Hub draft survives closing');
  await editor.getByRole('button',{name:'Save agent',exact:true}).click();await expect(editor.locator('.fr-crew-notices')).toContainText('Agent saved');
  await editor.getByRole('button',{name:'Suspend',exact:true}).click();await expect(card).toContainText('Suspended');
  await editor.getByRole('button',{name:'Reactivate',exact:true}).click();await expect(card).toContainText('Active');
  await editor.getByRole('button',{name:'Close Crew'}).click();
  await hub.getByRole('button',{name:'Create agent',exact:true}).click();await editor.getByLabel('Name',{exact:true}).fill('Atlas');
  await editor.getByLabel('Provider',{exact:true}).selectOption('sample-cloud');await editor.getByLabel('Model',{exact:true}).selectOption('sample-reasoner');
  await editor.getByLabel('Voice provider',{exact:true}).selectOption('sample-voice');await editor.getByLabel('Voice model',{exact:true}).selectOption('sample-speech');await editor.getByLabel('Voice ID',{exact:true}).fill('sample_voice');
  await editor.getByRole('button',{name:'Save agent',exact:true}).click();await expect(editor.locator('.fr-crew-notices')).toContainText('Agent saved');
  page.once('dialog',dialog=>dialog.accept());await editor.getByRole('button',{name:'Retire',exact:true}).click();await expect(editor).toContainText('Agent retired');await editor.getByRole('button',{name:'Close Crew'}).click();
  await expect(hub.locator('[data-agent-id="crew-fedcba0987654321"]')).toContainText('Retired');
  assert.equal(crewRoomWrites,roomWritesBeforeHub,'hub profile editing never mutates a chat room');
  await shot('crew-hub-desktop');await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>hub.evaluate(e=>e.scrollWidth-e.clientWidth)).toBeLessThanOrEqual(1);await shot('crew-hub-narrow');
  await page.goto(base+'/w/crew',{waitUntil:'domcontentloaded'});await expect(hub).toBeVisible();await expect(card).toContainText('Mira');
  hubTasks=[];await hub.getByRole('button',{name:'Refresh',exact:true}).click();await expect(hub).toContainText('No Crew work to show');
  taskError=true;await hub.getByRole('button',{name:'Refresh',exact:true}).click();await expect(hub.getByRole('alert')).toContainText('Could not refresh current work');
  taskError=false;agents=[];await hub.getByRole('button',{name:'Refresh',exact:true}).click();await expect(hub).toContainText('Build your first Crew agent');
  assert.deepEqual(errors,[]);
  console.log('PASS Crew room and hub: editor reuse, lifecycle, drafts, revisions, dispatch, attribution, menu/standalone entry, work states and responsive layout');
})().catch(async e=>{console.error(e.stack||e);if(activePage&&process.env.FRIDAY_CREW_SHOTS){fs.mkdirSync(process.env.FRIDAY_CREW_SHOTS,{recursive:true});await activePage.screenshot({path:path.join(process.env.FRIDAY_CREW_SHOTS,'failure.png')});console.error((await activePage.locator('body').innerText()).slice(-6000));}process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();});
