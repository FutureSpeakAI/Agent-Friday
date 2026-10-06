/* End-to-end orb selection into the existing recorded-reasoning viewer.
 * All processes, records and trace events are synthetic browser fixtures. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawnSync}=require('node:child_process');
const {chromium,expect}=require('@playwright/test');
const base=process.env.FRIDAY_DESIGN_URL||'http://127.0.0.1:3194/';
const root=path.resolve(__dirname,'..'),out=process.env.FRIDAY_ORB_PROOFS,baseline=process.env.FRIDAY_ORB_BASELINE;
const records=[];
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const trace=(id,text,task)=>({trace_id:id,task_id:task||null,label:'Recorded research',kind:'background',status:'running',
  model:'example-local-model',seat:'local',started:Date.now()/1000-12,events:[{type:'note',text,t:Date.now()/1000-5}]});
async function capture(page,name){records.push({name});await page.screenshot({path:path.join(out,name+'.png'),animations:'disabled'});console.log('PASS '+name);}
(async()=>{
  const url=new URL(base);assert.ok(['127.0.0.1','localhost'].includes(url.hostname)&&url.port==='3194');
  assert.ok(os.freemem()>6*2**30,'At least 6 GB free is required');assert.ok(out,'Set FRIDAY_ORB_PROOFS outside the repository');
  const rel=path.relative(root,path.resolve(out));assert.ok(rel.startsWith('..'+path.sep)||path.isAbsolute(rel));fs.mkdirSync(out,{recursive:true});
  let oldHtml=null;
  if(baseline){const r=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':index.html'],{cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:12e6});assert.equal(r.status,0,r.stderr);oldHtml=r.stdout;}
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{for(const display of ['simple','classic']){
    const page=await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(15000);
    const errors=[],clicks=[],details=new Map(),archives=new Map(),reads=[];let processes=[],live=[],events=[],cursor=0;
    try{
      page.on('pageerror',e=>errors.push(e.message));
      await page.exposeFunction('__orbClickReceipt',d=>clicks.push(d));
      await page.addInitScript(value=>{localStorage.setItem('friday.display-style.v1',value);
        if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=()=>Promise.reject(new Error('No camera or microphone in this proof'));
        window.addEventListener('fridayOrbClicked',e=>{void window.__orbClickReceipt(e.detail);});},display);
      await page.route('**/api/**',async route=>{
        const req=route.request(),u=new URL(req.url());
        if(!['GET','HEAD'].includes(req.method()))return route.fulfill({json:{status:'ok'}});
        if(u.pathname==='/api/desktop/cards')return route.fulfill({json:{status:'ok',cards:[]}});
        if(u.pathname==='/api/processes')return route.fulfill({json:{processes}});
        if(u.pathname==='/api/tasks')return route.fulfill({json:{tasks:[]}});
        if(u.pathname.startsWith('/api/tasks/')){
          const id=decodeURIComponent(u.pathname.slice('/api/tasks/'.length));reads.push(id);
          const d=details.get(id);if(!d)return route.fulfill({status:404,json:{error:'Task not found'}});
          if(d.wait)await d.wait.promise;
          return route.fulfill({status:d.http||200,json:d.body||d}).catch(()=>{});
        }
        if(u.pathname==='/api/traces/live')return route.fulfill({json:u.searchParams.has('snapshot')?{traces:live,cursor,labels:{}}:{events:events.filter(e=>e.seq>Number(u.searchParams.get('since')||0)),cursor,labels:{}}});
        if(u.pathname==='/api/traces')return route.fulfill({json:{traces:live.filter(t=>t.task_id===u.searchParams.get('task'))}});
        if(u.pathname.startsWith('/api/traces/')){const id=decodeURIComponent(u.pathname.split('/').at(-1));const t=archives.get(id)||live.find(x=>x.trace_id===id);return route.fulfill({status:t?200:404,json:t?{tree:t,focus:id,labels:{}}:{error:'Trace not found'}});}
        return route.continue();
      });
      if(oldHtml)await page.route(base,route=>route.fulfill({contentType:'text/html',body:oldHtml}));
      const primary={id:'orb-example-primary',label:'Research activity',category:'monitoring',icon:'◇',status:'running',model:'example-local-model',task_id:'task-example-primary',trace_id:'trace-example-primary'};
      const task={task_id:primary.task_id,trace_id:primary.trace_id,status:'running',model:primary.model,log:['Read the public project brief',...Array.from({length:24},(_,i)=>'Public activity checkpoint '+(i+1))],steps:[],result:''};
      details.set(primary.id,task);live=[trace(primary.trace_id,'Compared the public brief with the recorded requirements.',primary.task_id),trace('trace-example-unrelated','Unrelated activity must not appear.')];
      await page.goto(base);await page.waitForFunction(()=>window.__fridayRenderer&&window.FridayHandCursor&&window.fridayVibe?.getWorkspaceSceneState?.().ready);
      assert.equal(await page.evaluate(()=>FridayDisplayStyle.get()),display);
      if(display==='simple')await page.waitForFunction(()=>FridayHolographicWorkspace.stageRect?.w>0);
      processes=[primary];await page.evaluate(row=>fridaySyncOrbs([row]),primary);
      await expect.poll(()=>page.evaluate(id=>{const o=fridayOrbTargets().find(x=>x.id===id);if(!o)return false;const top=document.elementFromPoint(o.sx,o.sy);return [document.body,document.documentElement,document.getElementById('ui-root'),document.getElementById('ui-inner')].includes(top);},primary.id),{timeout:20000}).toBe(true);
      const point=await page.evaluate(id=>fridayOrbTargets().find(x=>x.id===id),primary.id);
      await page.mouse.click(point.sx,point.sy);
      const panel=page.locator('.thread-panel');
      await expect(panel).toBeVisible();await expect(panel).toContainText('Compared the public brief');
      await expect(panel.getByLabel('Reasoning thread')).toContainText('Compared the public brief');
      await expect(panel).not.toContainText('Unrelated activity must not appear');
      await expect.poll(()=>clicks.length).toBeGreaterThan(0);assert.equal(clicks.at(-1).task_id,primary.task_id);assert.equal(clicks.at(-1).trace_id,primary.trace_id);
      await panel.getByText('Activity and result',{exact:true}).click();await expect(panel).toContainText('Read the public project brief');
      await expect(panel.locator('details').first()).toHaveAttribute('open','');
      assert.ok(await panel.locator('.thread-body').evaluate(el=>el.scrollHeight>el.clientHeight),'The reading-position case needs real overflowing activity');
      await panel.locator('.thread-body').evaluate(el=>{el.scrollTop=0;});
      task.log=[...task.log,'Observed refresh checkpoint'];
      await expect(panel).toContainText('Observed refresh checkpoint');
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      assert.equal(await panel.locator('.thread-body').evaluate(el=>el.scrollTop),0,'A process refresh must not pull the reader away from the reasoning header');
      events.push({seq:++cursor,trace_id:primary.trace_id,type:'note',text:'Public checklist complete; preparing the result.'});
      await expect(panel.getByLabel('Reasoning thread')).toContainText('Public checklist complete');
      await capture(page,display+'-mouse-correlated-thread');
      await panel.getByRole('button',{name:'Close this process panel'}).click();
      await expect(panel).toBeHidden();
      await expect.poll(()=>page.evaluate(id=>{const o=fridayOrbTargets().find(x=>x.id===id);if(!o)return false;const top=document.elementFromPoint(o.sx,o.sy);return [document.body,document.documentElement,document.getElementById('ui-root'),document.getElementById('ui-inner')].includes(top);},primary.id),{timeout:20000}).toBe(true);
      const hand=await page.evaluate(id=>{const o=fridayOrbTargets().find(x=>x.id===id);FridayHandCursor.refresh();FridayHandCursor.frame({x:o.sx,y:o.sy,visible:true,pinching:false,t:performance.now()});return {locked:FridayHandCursor.locked?.orb?.id,result:FridayHandCursor.select()};},primary.id);
      assert.equal(hand.locked,primary.id);assert.equal(hand.result.ok,true);await expect(panel).toBeVisible();await expect(panel.getByLabel('Reasoning thread')).toContainText('Compared the public brief');
      await capture(page,display+'-hand-correlated-thread');
      // A new selection starts at its reasoning header even after a long record.
      await page.setViewportSize({width:568,height:320});
      if(await panel.locator('details').first().getAttribute('open')===null)await panel.getByText('Activity and result',{exact:true}).click();
      await panel.locator('.thread-body').evaluate(el=>{el.scrollTop=el.scrollHeight;});
      assert.ok(await panel.locator('.thread-body').evaluate(el=>el.scrollTop)>0,'The new-selection case needs a scrolled record');
      const second={...primary,id:'orb-example-second',label:'Second public activity'};processes.push(second);details.set(second.id,{...task});
      await page.evaluate(({rows,id})=>{fridaySyncOrbs(rows);FridayOrbHands.act('open',[id],'voice');},{rows:processes,id:second.id});
      const secondPanel=page.getByRole('region',{name:'Process: Second public activity',exact:true});
      await expect(secondPanel.getByLabel('Reasoning thread')).toContainText('Compared the public brief');
      assert.ok(await secondPanel.locator('.thread-body').evaluate(el=>el.scrollHeight>el.clientHeight),'The new record must also overflow');
      assert.equal(await secondPanel.locator('.thread-body').evaluate(el=>el.scrollTop),0,'A new orb selection must start at the reasoning header');
      await capture(page,display+'-new-selection-starts-at-reasoning');
      await page.setViewportSize({width:1600,height:1000});
      // A process born without a trace link learns it from a later detail poll.
      const process={id:'orb-example-process',label:'Process steps',category:'monitoring',status:'running'};processes.push(process);
      details.set(process.id,{task_id:process.id,process:true,status:'running',log:[],steps:[{type:'tool',name:'read_file',status:'ok',duration_ms:18,ts:1700000002}],result:''});
      await page.evaluate(({rows,id})=>{fridaySyncOrbs(rows);FridayOrbHands.act('open',[id],'voice');},{rows:processes,id:process.id});
      const processPanel=page.getByRole('region',{name:'Process: Process steps',exact:true});await expect(processPanel).toContainText('Tool: read_file · ok · 18ms');
      await expect(processPanel).toContainText('No reasoning trace is linked');
      const lateTrace=trace('trace-example-process','A public execution summary for the process.');live.push(lateTrace);
      events.push({seq:++cursor,type:'start',trace_id:lateTrace.trace_id,trace:lateTrace},{seq:++cursor,type:'note',trace_id:lateTrace.trace_id,text:lateTrace.events[0].text});
      details.set(process.id,{...details.get(process.id),trace_id:'trace-example-process'});
      await expect(processPanel.getByLabel('Reasoning thread')).toContainText('A public execution summary');
      await capture(page,display+'-late-trace-and-structured-step');
      const same={id:'task-example-own-orb',label:'Task-owned orb',category:'monitoring',status:'running'};processes.push(same);
      details.set(same.id,{task_id:same.id,status:'running',log:[],result:''});
      live.push(trace('trace-example-task-owned','The task owns this recorded thread.',same.id));
      await page.evaluate(({rows,id})=>{fridaySyncOrbs(rows);FridayOrbHands.act('open',[id],'voice');},{rows:processes,id:same.id});
      await expect(page.getByRole('region',{name:'Process: Task-owned orb',exact:true}).getByLabel('Reasoning thread')).toContainText('The task owns this recorded thread.');
      await capture(page,display+'-task-id-without-click-metadata');
      const archived={id:'orb-example-archived',label:'Archived helper',category:'monitoring',status:'complete',trace_id:'trace-example-archived-child'};processes.push(archived);
      const child={...trace(archived.trace_id,'The selected helper recorded this public conclusion.'),status:'complete',ended:Date.now()/1000};
      const parent={...trace('trace-example-parent','UNRELATED PARENT CONTENT'),status:'complete',children:[child,{...trace('trace-example-sibling','UNRELATED SIBLING CONTENT'),status:'complete'}]};
      archives.set(archived.trace_id,parent);details.set(archived.id,{task_id:archived.id,process:true,status:'complete',trace_id:archived.trace_id,log:[]});
      await page.evaluate(({rows,id})=>{fridaySyncOrbs(rows);FridayOrbHands.act('open',[id],'voice');},{rows:processes,id:archived.id});
      const archivePanel=page.getByRole('region',{name:'Process: Archived helper',exact:true});
      await expect(archivePanel.getByLabel('Reasoning thread')).toContainText('The selected helper recorded this public conclusion.');
      await expect(archivePanel).not.toContainText('UNRELATED PARENT CONTENT');await expect(archivePanel).not.toContainText('UNRELATED SIBLING CONTENT');
      await capture(page,display+'-exact-archived-child');
      // A late result from an older selection cannot change the visible record.
      const slow={id:'orb-example-slow',label:'Slow record',category:'monitoring',status:'running'},wait=deferred();processes.push(slow);
      details.set(slow.id,{wait,body:{task_id:slow.id,process:true,status:'complete',log:['STALE RECORD'],result:'STALE RESULT'}});
      await page.evaluate(({rows,id})=>{fridaySyncOrbs(rows);FridayOrbHands.act('open',[id],'voice');},{rows:processes,id:slow.id});
      await expect.poll(()=>reads.includes(slow.id)).toBe(true);
      await page.evaluate(id=>FridayOrbHands.act('open',[id],'voice'),process.id);wait.resolve();
      await expect(processPanel.getByLabel('Reasoning thread')).toContainText('A public execution summary');await expect(processPanel).not.toContainText('STALE');
      details.set(process.id,{http:503,body:{error:'Synthetic failure'}});await expect(processPanel).toContainText('Could not refresh this process');await expect(processPanel).toContainText('A public execution summary');
      await capture(page,display+'-read-failure-preserves-record');
      details.delete(process.id);await expect(processPanel).toContainText('This process is no longer available');
      await capture(page,display+'-missing-record');
      details.set(process.id,{task_id:process.id,process:true,status:'complete',trace_id:'trace-example-process',log:[],steps:[],result:'Recorded process completed.'});
      lateTrace.status='complete';lateTrace.ended=Date.now()/1000;
      events.push({seq:++cursor,type:'end',trace_id:lateTrace.trace_id,trace:{status:'complete',ended:lateTrace.ended}});
      await expect(processPanel).not.toContainText('This process is no longer available');
      if(await processPanel.locator('details').first().getAttribute('open')===null)await processPanel.getByText('Activity and result',{exact:true}).click();
      await expect(processPanel).toContainText('Recorded process completed.');
      for(const [width,height] of [[390,844],[320,568],[568,320]]){
        await page.setViewportSize({width,height});await expect(processPanel).toBeVisible();
        const bounds=await processPanel.boundingBox(),stage=await page.evaluate(()=>FridayHolographicWorkspace.stageRect);
        assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+.5&&bounds.y>=0&&bounds.y+bounds.height<=height+.5,JSON.stringify({display,width,height,bounds}));
        if(stage)assert.ok(bounds.x>=stage.x+stage.w-1||bounds.x+bounds.width<=stage.x+1||bounds.y>=stage.y+stage.h-1||bounds.y+bounds.height<=stage.y+1,'Process panel must remain clear of the avatar');
        await processPanel.getByRole('button',{name:'Close this process panel'}).click({trial:true});
        const summaryColors=await processPanel.locator('summary').first().evaluate(el=>{const probe=document.createElement('span');probe.style.color='var(--fr-text)';el.appendChild(probe);const colors={actual:getComputedStyle(el).color,expected:getComputedStyle(probe).color};probe.remove();return colors;});
        assert.equal(summaryColors.actual,summaryColors.expected,'Activity text must use the brand foreground in every display style');
        await processPanel.locator('.thread-body').evaluate(el=>{el.scrollTop=el.scrollHeight;});
        const result=processPanel.getByText('Recorded process completed.',{exact:true});
        await expect(result).toBeInViewport();
        assert.equal(await result.evaluate(el=>{const r=el.getBoundingClientRect(),top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return !!top&&(top===el||el.contains(top));}),true,'Recorded result must be reachable above the dock');
        await processPanel.locator('.thread-body').evaluate(el=>{el.scrollTop=0;});
        await expect(processPanel.getByText('REASONING',{exact:true})).toBeInViewport();
        await capture(page,display+'-'+width+'x'+height+'-correlated-thread');
      }
      assert.deepEqual(errors,[],'No page errors');
    }finally{await page.close();}
  }}finally{fs.writeFileSync(path.join(out,'reasoning-proof.json'),JSON.stringify(records,null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
