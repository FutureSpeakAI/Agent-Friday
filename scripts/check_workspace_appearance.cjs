/* Mount the real appearance editor and geometry controller with bundled React.
 * Authenticated API responses are controlled doubles; no server or model runs. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawnSync}=require('node:child_process');
const {chromium,expect}=require('@playwright/test');
const root=path.resolve(__dirname,'..'),baseline=process.env.FRIDAY_BASELINE_REV,output=process.env.FRIDAY_APPEARANCE_PROOFS;
function read(file,optional=false){
  if(!baseline)return fs.readFileSync(path.join(root,file),'utf8');
  const r=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':'+file],{cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:5e6});
  if(optional&&r.status!==0)return null;
  assert.equal(r.status,0,r.stderr);return r.stdout;
}
// An exact private pre-fix copy isolates regressions added after the feature itself.
const source=process.env.FRIDAY_APPEARANCE_SOURCE?fs.readFileSync(process.env.FRIDAY_APPEARANCE_SOURCE,'utf8'):read('static/friday_appearance.js',true),results=[];
const pause=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const editor=page=>page.getByRole('dialog',{name:'Appearance of Synthetic Library',exact:true});
const note=page=>editor(page).getByLabel('Pinned workspace note',{exact:true});
const preview=page=>editor(page).getByRole('button',{name:'Preview changes',exact:true});
const apply=page=>editor(page).getByRole('button',{name:'Apply appearance',exact:true});
async function respond(page,kind='success',data){await page.evaluate(({kind,data})=>appearanceReply(kind,data),{kind,data});await pause(page);}
async function load(page){await respond(page);await expect(note(page)).toHaveValue('Original note');}
async function review(page,text='Synthetic appearance draft'){
  await note(page).fill(text);await preview(page).click();await respond(page);
  await expect(editor(page).getByRole('region',{name:'Appearance preview',exact:true})).toBeVisible();
}
async function snapshot(page){return page.evaluate(()=>appearanceSnapshot());}
async function privacy(page,on){await page.evaluate(on=>appearancePrivacy(on),on);await pause(page);}
(async()=>{
  if(!source){
    results.push({name:'appearance editor is available',status:'failed',message:'The selected baseline has no native appearance editor; feature absence is the expected negative result.'});
    process.stdout.write(JSON.stringify({baseline,results})+'\n');process.exitCode=1;return;
  }
  const minimum=Math.max(4,Number(process.env.FRIDAY_CHECK_MIN_GIB)||6);
  assert.ok(os.freemem()>minimum*1073741824,'Not enough free memory for the mounted editor checks');
  if(output){
    const relative=path.relative(root,path.resolve(output));
    assert.ok(relative.startsWith('..'+path.sep)||path.isAbsolute(relative),'Proof files stay outside the repository');
    fs.mkdirSync(output,{recursive:true});
  }
  const css=['static/friday_shared_surfaces.css','static/friday_native_shell.css','static/friday_holographic_workspace.css','static/friday_appearance.css'].map(file=>read(file)).join('\n');
  const spatial=read('static/friday_holographic_workspace.js');
  const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
  assert.ok(typeof executablePath==='string'&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select an explicit installed dedicated test browser');
  const browser=await chromium.launch({executablePath,headless:true});
  console.log(JSON.stringify({browser:{executablePath,version:browser.version()}}));
  async function run(name,test,{viewport={width:1600,height:1000},autoLoad=true,initialPrivate=false}={}){
    const page=await browser.newPage({viewport,reducedMotion:'reduce'}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    try{
      await page.route('**/*',route=>route.abort());
      await page.setContent(`<!doctype html><style>${css}</style><style>
        :root{--fr-surface:#101725;--fr-text:#edf4fc;--fr-label:#b1bfd1;--fr-cyan:#00d4ff;--fr-glass-edge:#334052;--fr-font-body:Arial,sans-serif;--fr-font-display:Arial,sans-serif}
        body{margin:0;background:#070d16;color:var(--fr-text);font:14px Arial,sans-serif}.top-bar{position:fixed;inset:0 0 auto;height:52px}.dock{display:none}
        #opener{min-height:40px;margin:6px}#synthetic-stage{position:fixed;left:var(--friday-avatar-left);top:var(--friday-avatar-top);width:var(--friday-avatar-width);height:var(--friday-avatar-height);display:grid;place-items:center;background:#102b3d;border-radius:20px;color:#5ee3ef;pointer-events:none}
      </style><body class="friday-experience-enabled friday-experience-desktop"><div class="top-bar"><button id="opener">Customize appearance</button></div><div class="dock"></div><div id="ui-root"></div><div id="synthetic-stage">Protected avatar stage</div><div id="state-indicator">READY</div><div id="presence-status">Synthetic layout fixture</div></body>`);
      await page.addScriptTag({path:path.join(root,'static/vendor/react-18.3.1.production.min.js')});
      await page.addScriptTag({path:path.join(root,'static/vendor/react-dom-18.3.1.production.min.js')});
      await page.evaluate(initialPrivate=>{
        window.__fridayOffRecord=initialPrivate;
        window.appearancePrivacy=on=>{window.__fridayOffRecord=on;dispatchEvent(new CustomEvent('friday:off-record',{detail:{on}}));};
        window.FridayDisplayStyle={get:()=> 'simple'};
        window.fridayDesktopArea=()=>{
          const area={x:16,y:64,w:innerWidth-32,h:innerHeight-80};
          return window.FridayHolographicWorkspace?.workspaceArea(area)||area;
        };
        const originalCreate=ReactDOM.createRoot.bind(ReactDOM);
        ReactDOM.createRoot=(...args)=>{const root=originalCreate(...args);window.appearanceForceUnmount=()=>root.unmount();return root;};
        const actualSet=window.setTimeout.bind(window),actualClear=window.clearTimeout.bind(window),timeouts=new Map();let timerId=10000000;
        window.setTimeout=(fn,delay,...args)=>{if(delay!==30000)return actualSet(fn,delay,...args);const id=++timerId;timeouts.set(id,()=>fn(...args));return id;};
        window.clearTimeout=id=>{if(!timeouts.delete(id))actualClear(id);};
        window.appearanceExpire=()=>{const next=timeouts.entries().next().value;if(!next)throw new Error('No request timeout');timeouts.delete(next[0]);next[1]();};
        const original={note:'Original note',density:'comfortable',accent:'#00d4ff',actions:[{label:'Existing action',prompt:'Synthetic existing prompt'}],css:'.ws-custom-root{color:inherit}',hidden:['.synthetic-optional']};
        let server={workspace:'library',revision:'a'.repeat(64),customization:{...original},versions:[]},requests=[],savedEvents=[];
        window.addEventListener('friday:appearance-saved',e=>savedEvents.push(e.detail));
        window.apiFetch=(url,options={})=>{
          if(url!=='/api/workspace/library/appearance')throw new Error('Unexpected authenticated fixture request: '+url);
          return new Promise((resolve,reject)=>{
            const record={url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null,resolve,reject,finished:false};requests.push(record);
            options.signal?.addEventListener('abort',()=>{record.finished=true;record.aborted=true;reject(new DOMException('Synthetic timeout','AbortError'));},{once:true});
          });
        };
        const merge=(before,patch)=>{const result={...before};for(const [key,value]of Object.entries(patch)){if(value===null)delete result[key];else result[key]=value;}return result;};
        window.appearanceSnapshot=()=>JSON.parse(JSON.stringify({server,requests:requests.map(({url,method,body,finished,aborted})=>({url,method,body,finished,aborted})),savedEvents}));
        window.appearanceExternalChange=()=>{server={...server,revision:'c'.repeat(64),customization:{...server.customization,note:'External change',density:'compact'}};};
        window.appearanceCommitBeforeAck=()=>{
          const request=requests.find(r=>!r.finished&&r.body?.apply);if(!request)throw new Error('No pending apply');
          server={...server,revision:'b'.repeat(64),customization:merge(server.customization,request.body.patch)};
          request.committed={status:'ok',...server,applied:true,changed:Object.keys(request.body.patch)};
        };
        window.appearanceLateServerCommit=()=>{
          const request=requests.findLast(r=>r.body?.apply);if(!request)throw new Error('No prior apply');
          server={...server,revision:'b'.repeat(64),customization:merge(server.customization,request.body.patch)};
          request.resolve({ok:true,status:200,json:async()=>({status:'ok',...server,applied:true,changed:Object.keys(request.body.patch)})});
        };
        window.appearanceReply=(kind='success',override)=>{
          const request=requests.find(r=>!r.finished);if(!request)throw new Error('No pending appearance request');request.finished=true;
          if(kind==='network'){request.reject(new Error('Synthetic connection failure'));return;}
          let status=200,data={status:'ok',...server};
          if(kind==='success'&&request.committed)data=request.committed;
          else if(kind==='success'&&request.body){
            const patch=request.body.patch;
            if(request.body.expected_revision!==server.revision){status=409;data={status:'conflict',message:'This workspace changed. Reload its appearance before applying your draft.'};}
            else if(request.body.apply){server={...server,revision:'b'.repeat(64),customization:merge(server.customization,patch)};data={status:'ok',...server,applied:true,changed:Object.keys(patch)};}
            else data={status:'ok',...server,preview:merge(server.customization,patch),changed:Object.keys(patch),applied:false};
          }
          if(kind==='http'){status=503;data={status:'error',message:'Synthetic save unavailable. Your draft is kept.'};}
          if(kind==='conflict'){status=409;data={status:'conflict',message:'This workspace changed. Reload its appearance before applying your draft.'};}
          if(kind==='malformed')data=override||{status:'ok'};
          request.resolve({ok:status<400,status,json:async()=>{if(kind==='json')throw new SyntaxError('Synthetic invalid JSON');return data;}});
        };
      },initialPrivate);
      await page.addScriptTag({content:spatial});
      await page.evaluate(()=>dispatchEvent(new Event('resize')));
      await page.addScriptTag({content:source});
      await page.evaluate(()=>{const opener=document.getElementById('opener');opener.focus();FridayWorkspaceAppearance.open({id:'library',title:'Synthetic Library',opener});});
      await expect(editor(page)).toBeVisible();
      await pause(page);
      await expect.poll(async()=> (await snapshot(page)).requests.length).toBe(initialPrivate?0:1);
      if(autoLoad&&!initialPrivate)await load(page);
      await test(page);
      assert.deepEqual(errors,[],'The actual mounted editor must not throw');
      results.push({name,status:'passed'});
    }catch(error){results.push({name,status:'failed',message:error.message});}
    finally{await page.close();}
  }
  try{
    await run('GET reads the exact workspace without creating an apply request',async page=>{
      const state=await snapshot(page);assert.deepEqual(state.requests.map(r=>r.method),['GET']);assert.equal(state.server.revision,'a'.repeat(64));
      await expect(apply(page)).toBeDisabled();await expect(preview(page)).toBeDisabled();
      await expect(editor(page).getByLabel('Spacing',{exact:true})).toHaveValue('comfortable');
    });
    await run('preview is read-only and apply sends exactly the reviewed patch and revision',async page=>{
      await note(page).fill('Reviewed note');await editor(page).getByLabel('Spacing',{exact:true}).selectOption('compact');
      await preview(page).click();await respond(page);
      const before=await snapshot(page),request=before.requests.at(-1).body;
      assert.equal(request.apply,false);assert.deepEqual(request.patch,{note:'Reviewed note',density:'compact'});assert.equal(before.server.customization.note,'Original note');
      await apply(page).click();await expect(apply(page)).toBeDisabled();
      const pending=await snapshot(page);assert.deepEqual(pending.requests.at(-1).body,{...request,apply:true});
      await respond(page);await expect(editor(page).getByText('Appearance saved. Earlier versions are available in workspace history.',{exact:true})).toBeVisible();
      const after=await snapshot(page);assert.equal(after.savedEvents.length,1);assert.equal(after.server.customization.css,before.server.customization.css);assert.deepEqual(after.server.customization.hidden,before.server.customization.hidden);
    });
    await run('editing invalidates the old preview before another apply',async page=>{
      await review(page,'First draft');await note(page).fill('Newer draft');await expect(apply(page)).toBeDisabled();
      await expect(editor(page).getByRole('region',{name:'Appearance preview',exact:true})).toHaveCount(0);
      await preview(page).click();await respond(page);await apply(page).click();
      assert.equal((await snapshot(page)).requests.at(-1).body.patch.note,'Newer draft');await respond(page);
    });
    await run('an editor opened privately cannot author or promote a draft',async page=>{
      await expect(editor(page).getByText(/Appearance editing is paused while Off the Record/)).toBeVisible();
      await expect(note(page)).toHaveCount(0);await expect(preview(page)).toBeDisabled();await expect(apply(page)).toBeDisabled();
      await expect(editor(page).getByRole('button',{name:'Reload current appearance',exact:true})).toBeDisabled();
      assert.equal((await snapshot(page)).requests.length,0);
      await privacy(page,false);assert.equal((await snapshot(page)).requests.length,0);
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await load(page);
      await review(page,'Authored after public reload');await apply(page).click();await respond(page);
      assert.equal((await snapshot(page)).server.customization.note,'Authored after public reload');
    },{initialPrivate:true});
    await run('privacy pauses fields and preserves the public draft until fresh reload and review',async page=>{
      await review(page,'Existing public draft');await privacy(page,true);
      await expect(note(page)).toBeDisabled();await expect(editor(page).getByLabel('Spacing',{exact:true})).toBeDisabled();
      await expect(editor(page).getByLabel('Choose accent color',{exact:true})).toBeDisabled();
      await expect(editor(page).getByLabel('Prompt 1',{exact:true})).toBeDisabled();
      await expect(preview(page)).toBeDisabled();await expect(apply(page)).toBeDisabled();
      await expect(editor(page).getByRole('region',{name:'Appearance preview',exact:true})).toHaveCount(0);
      await privacy(page,false);await expect(note(page)).toHaveValue('Existing public draft');
      await expect(preview(page)).toBeDisabled();await expect(apply(page)).toBeDisabled();
      assert.equal((await snapshot(page)).requests.length,2);
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await respond(page);
      await expect(note(page)).toHaveValue('Existing public draft');await expect(preview(page)).toBeEnabled();await expect(apply(page)).toBeDisabled();
      await preview(page).click();await respond(page);await apply(page).click();await respond(page);
      assert.equal((await snapshot(page)).server.customization.note,'Existing public draft');
    });
    await run('native edit refuses private input before the disabled render arrives',async page=>{
      await note(page).fill('Public note kept');
      await page.evaluate(()=>{
        window.__fridayOffRecord=true;
        const input=document.querySelector('.fr-appearance textarea');
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Privately authored text');
        input.dispatchEvent(new Event('input',{bubbles:true}));
      });
      await expect(note(page)).toBeDisabled();await expect(note(page)).toHaveValue('Public note kept');
      await privacy(page,true);await privacy(page,false);
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await respond(page);
      await preview(page).click();await respond(page);
      assert.equal((await snapshot(page)).requests.at(-1).body.patch.note,'Public note kept');
    });
    for(const action of ['Preview changes','Apply appearance'])await run('native '+action+' refuses a private render race',async page=>{
      if(action==='Apply appearance')await review(page);else await note(page).fill('Public draft');
      const count=(await snapshot(page)).requests.length;
      await page.evaluate(action=>{
        window.__fridayOffRecord=true;
        [...document.querySelectorAll('.fr-appearance button')].find(button=>button.textContent===action).click();
      },action);
      await expect(preview(page)).toBeDisabled();await expect(apply(page)).toBeDisabled();
      assert.equal((await snapshot(page)).requests.length,count);assert.equal((await snapshot(page)).savedEvents.length,0);
      await privacy(page,false);await expect(preview(page)).toBeDisabled();await expect(apply(page)).toBeDisabled();
    });
    await run('native reload refuses private mode before the disabled render arrives',async page=>{
      await review(page);await apply(page).click();await respond(page,'http');
      const count=(await snapshot(page)).requests.length;
      await page.evaluate(()=>{
        window.__fridayOffRecord=true;
        [...document.querySelectorAll('.fr-appearance button')].find(button=>button.textContent==='Reload current appearance').click();
      });
      await expect(editor(page).getByRole('button',{name:'Reload current appearance',exact:true})).toBeDisabled();
      await expect(note(page)).toHaveValue('Synthetic appearance draft');assert.equal((await snapshot(page)).requests.length,count);
    });
    await run('a synchronous privacy cycle revokes the still-rendered Apply target',async page=>{
      await review(page);const count=(await snapshot(page)).requests.length;
      await page.evaluate(()=>{
        appearancePrivacy(true);appearancePrivacy(false);
        [...document.querySelectorAll('.fr-appearance button')].find(button=>button.textContent==='Apply appearance').click();
      });
      await expect(apply(page)).toBeDisabled();assert.equal((await snapshot(page)).requests.length,count);
      await expect(note(page)).toHaveValue('Synthetic appearance draft');
    });
    await run('async publication checks current privacy before its change event arrives',async page=>{
      await note(page).fill('Public draft');await preview(page).click();
      await page.evaluate(()=>{window.__fridayOffRecord=true;appearanceReply();});
      await expect(note(page)).toBeDisabled();await expect(editor(page).getByRole('region',{name:'Appearance preview',exact:true})).toHaveCount(0);
      await expect(editor(page).getByText(/Its result is unconfirmed here/)).toBeVisible();await expect(apply(page)).toBeDisabled();
      assert.equal((await snapshot(page)).savedEvents.length,0);
    });
    await run('a GET from an earlier privacy lifetime cannot populate the editor',async page=>{
      await privacy(page,true);await privacy(page,false);await respond(page);
      await expect(note(page)).toHaveCount(0);assert.equal((await snapshot(page)).savedEvents.length,0);
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await load(page);
    },{autoLoad:false});
    await run('a preview from an earlier public lifetime cannot authorize Apply',async page=>{
      await note(page).fill('Public draft');await preview(page).click();await privacy(page,true);await privacy(page,false);await respond(page);
      await expect(editor(page).getByRole('region',{name:'Appearance preview',exact:true})).toHaveCount(0);
      await expect(note(page)).toHaveValue('Public draft');await expect(preview(page)).toBeDisabled();await expect(apply(page)).toBeDisabled();
      await expect(editor(page).getByText(/Its result is unconfirmed here/)).toBeVisible();
      assert.equal((await snapshot(page)).savedEvents.length,0);
    });
    await run('a late acknowledgement after a committed public save remains unconfirmed across a privacy cycle',async page=>{
      await review(page,'Public saved draft');await apply(page).click();await page.evaluate(()=>appearanceCommitBeforeAck());
      await privacy(page,true);await privacy(page,false);await respond(page);
      await expect(note(page)).toHaveValue('Public saved draft');await expect(preview(page)).toBeDisabled();await expect(apply(page)).toBeDisabled();
      await expect(editor(page).getByText(/Its result is unconfirmed here/)).toBeVisible();
      await expect(editor(page).getByText(/^Appearance saved\./)).toHaveCount(0);
      const state=await snapshot(page);assert.equal(state.savedEvents.length,0);assert.equal(state.server.customization.note,'Public saved draft');
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await respond(page);
      await expect(note(page)).toHaveValue('Public saved draft');await expect(preview(page)).toBeDisabled();assert.equal((await snapshot(page)).requests.at(-1).method,'GET');
    });
    await run('late request failure cannot replace the privacy boundary status',async page=>{
      await review(page);await apply(page).click();await privacy(page,true);await respond(page,'http');
      await expect(note(page)).toBeDisabled();await expect(note(page)).toHaveValue('Synthetic appearance draft');
      await expect(editor(page).getByText(/Its result is unconfirmed here/)).toBeVisible();await expect(editor(page).getByRole('alert')).toHaveCount(0);
      assert.equal((await snapshot(page)).savedEvents.length,0);
    });
    for(const kind of ['network','http','json','conflict'])await run(kind+' apply failure keeps the draft and requires reload',async page=>{
      await review(page);await apply(page).click();await respond(page,kind);
      await expect(editor(page).getByRole('alert')).toBeVisible();await expect(note(page)).toHaveValue('Synthetic appearance draft');
      await expect(apply(page)).toBeDisabled();await expect(preview(page)).toBeDisabled();
      assert.equal((await snapshot(page)).savedEvents.length,0);
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await respond(page);
      await expect(note(page)).toHaveValue('Synthetic appearance draft');await expect(preview(page)).toBeEnabled();
    });
    await run('CAS conflict preserves a draft against newer external state',async page=>{
      await review(page,'Local note');await page.evaluate(()=>appearanceExternalChange());await apply(page).click();await respond(page);
      await expect(editor(page).getByRole('alert')).toContainText('workspace changed');await expect(note(page)).toHaveValue('Local note');
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await respond(page);
      await preview(page).click();await respond(page);const request=(await snapshot(page)).requests.at(-1).body;
      assert.equal(request.expected_revision,'c'.repeat(64));assert.equal(request.patch.note,'Local note');await expect(apply(page)).toBeEnabled();
    });
    await run('timeout is uncertain and late server completion cannot discard the draft',async page=>{
      await review(page);await apply(page).click();await page.evaluate(()=>appearanceExpire());
      await expect(editor(page).getByRole('alert')).toContainText('timed out');await expect(note(page)).toHaveValue('Synthetic appearance draft');
      await page.evaluate(()=>appearanceLateServerCommit());await pause(page);
      await expect(note(page)).toHaveValue('Synthetic appearance draft');assert.equal((await snapshot(page)).savedEvents.length,0);await expect(apply(page)).toBeDisabled();
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await respond(page);
      await expect(preview(page)).toBeDisabled();await expect(note(page)).toHaveValue('Synthetic appearance draft');
    });
    await run('pending Apply cannot close or activate an old discard confirmation',async page=>{
      await review(page);await editor(page).getByRole('button',{name:'Close appearance editor',exact:true}).click();
      await expect(editor(page).getByRole('button',{name:'Discard draft',exact:true})).toBeVisible();await expect(apply(page)).toBeDisabled();
      await editor(page).getByRole('button',{name:'Keep editing',exact:true}).click();await apply(page).click();
      await page.keyboard.press('Escape');await expect(editor(page)).toBeVisible();await expect(editor(page).getByRole('button',{name:'Close appearance editor',exact:true})).toBeDisabled();
      await expect(editor(page).getByRole('button',{name:'Discard draft',exact:true})).toHaveCount(0);
      await respond(page);await editor(page).getByRole('button',{name:'Close appearance editor',exact:true}).click();await expect(editor(page)).toHaveCount(0);
    });
    await run('unmount during an outstanding apply suppresses late UI publication',async page=>{
      await review(page);await apply(page).click();await page.evaluate(()=>appearanceForceUnmount());await expect(editor(page)).toHaveCount(0);
      await respond(page);assert.equal((await snapshot(page)).savedEvents.length,0);await expect(page.locator('#opener')).toBeFocused();
    });
    for(const kind of ['preview','apply'])await run('malformed '+kind+' response leaves a recoverable draft',async page=>{
      if(kind==='apply'){await review(page);await apply(page).click();}
      else{await note(page).fill('Synthetic appearance draft');await preview(page).click();}
      await respond(page,'malformed');await expect(note(page)).toHaveValue('Synthetic appearance draft');
      await expect(editor(page).getByRole('alert')).toContainText('incomplete appearance');await expect(apply(page)).toBeDisabled();
    });
    await run('missing applied confirmation cannot claim a successful save',async page=>{
      await review(page);await apply(page).click();const state=await snapshot(page);
      await respond(page,'malformed',{status:'ok',...state.server});await expect(editor(page).getByRole('alert')).toContainText('did not confirm');
      await expect(note(page)).toHaveValue('Synthetic appearance draft');assert.equal((await snapshot(page)).savedEvents.length,0);
    });
    await run('initial GET failure can retry and closing returns focus',async page=>{
      await respond(page,'http');await expect(editor(page).getByRole('alert')).toBeVisible();
      await editor(page).getByRole('button',{name:'Reload current appearance',exact:true}).click();await respond(page);
      await expect(note(page)).toHaveValue('Original note');await editor(page).getByRole('button',{name:'Close appearance editor',exact:true}).click();
      await expect(editor(page)).toHaveCount(0);await expect(page.locator('#opener')).toBeFocused();
    },{autoLoad:false});
    await run('discard confirmation keeps or explicitly discards the unsent draft',async page=>{
      await note(page).fill('Keep this draft');await page.keyboard.press('Escape');
      await editor(page).getByRole('button',{name:'Keep editing',exact:true}).click();await expect(note(page)).toHaveValue('Keep this draft');
      await editor(page).getByRole('button',{name:'Close appearance editor',exact:true}).click();await editor(page).getByRole('button',{name:'Discard draft',exact:true}).click();
      await expect(editor(page)).toHaveCount(0);await expect(page.locator('#opener')).toBeFocused();assert.equal((await snapshot(page)).requests.length,1);
    });
    for(const viewport of [{width:320,height:568},{width:390,height:844},{width:568,height:320}])await run('protected stage and reachable actions at '+viewport.width+'x'+viewport.height,async page=>{
      await note(page).fill('Compact appearance draft');await preview(page).click();await respond(page);
      const geometry=await page.evaluate(()=>{
        const r=document.querySelector('.fr-appearance').getBoundingClientRect(),layout=FridayHolographicWorkspace.layoutRect;
        return {r:{x:r.x,y:r.y,w:r.width,h:r.height},layout,scroll:document.documentElement.scrollWidth,viewport:innerWidth};
      });
      const {r,layout}=geometry,c=layout.content,s=layout.stageFrame||layout.stage;
      assert.ok(r.x>=c.x-.5&&r.y>=c.y-.5&&r.x+r.w<=c.x+c.w+.5&&r.y+r.h<=c.y+c.h+.5,'Editor stays within the live work area');
      assert.ok(r.x+r.w<=s.x+.5||s.x+s.w<=r.x+.5||r.y+r.h<=s.y+.5||s.y+s.h<=r.y+.5,'Editor never covers the avatar stage');
      assert.ok(geometry.scroll<=geometry.viewport+1,'No horizontal document overflow');
      await expect(apply(page)).toBeVisible();await apply(page).click();await respond(page);
      if(output)await page.screenshot({path:path.join(output,'appearance-'+viewport.width+'x'+viewport.height+'.png')});
      await editor(page).getByRole('button',{name:'Close appearance editor',exact:true}).click();await expect(page.locator('#opener')).toBeFocused();
    },{viewport});
  }finally{
    if(output)fs.writeFileSync(path.join(output,'workspace-appearance-results.json'),JSON.stringify({baseline:baseline||null,results},null,2));
    await browser.close();
  }
  process.stdout.write(JSON.stringify({baseline:baseline||null,results})+'\n');
  if(results.some(result=>result.status==='failed'))process.exitCode=1;
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
