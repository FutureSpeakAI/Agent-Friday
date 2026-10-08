/* Mount the served Salon and its actual root opener/guard/key contract.
 * Fetch, neighboring workspaces and notices are controlled fixture boundaries. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawnSync}=require('node:child_process');
const {chromium,expect}=require('@playwright/test');
const root=path.resolve(__dirname,'..'),baseline=process.env.FRIDAY_BASELINE_REV,output=process.env.FRIDAY_STUDIO_OWNERSHIP_PROOFS;
let html=fs.readFileSync(path.join(root,'index.html'),'utf8');
if(baseline){
  const old=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':index.html'],{cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:12e6,timeout:15000});
  assert.equal(old.status,0,old.stderr||old.error?.message);html=old.stdout;
}
function region(start,end,from=0){
  const first=html.indexOf(start,from),last=html.indexOf(end,first+start.length);
  assert.ok(first>=0&&last>first,'Actual served source contains '+start+' through '+end);
  return html.slice(first,last);
}
const component=region('function WorkspaceChat(','class Safe extends React.Component');
const registration=region('  const [wsChat, setWsChat] =','  /* Conversations open as WINDOWS');
const openerAt=html.indexOf('window.fridayOpenWorkspaceChat =');
assert.ok(openerAt>=0,'The actual root Salon opener is present');
const effectAt=html.lastIndexOf('  useEffect(() => {',openerAt),effectEnd=html.indexOf('  }, []);',openerAt);
assert.ok(effectAt>=0&&effectEnd>openerAt,'The root opener effect has a bounded region');
const opener=html.slice(effectAt,effectEnd+'  }, []);'.length);
const keyedSurface=region('  const shellWsChat =','  const shellChatPanel =');
const enter=region('function fridayIsComposing(','window.fridayEnterSends = fridayEnterSends;');
const pause=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const panel=(page,name='Synthetic Library')=>page.getByRole('dialog',{name:name+' customization',exact:true});
const input=page=>panel(page).getByRole('textbox');
const send=page=>panel(page).getByRole('button',{name:'Send',exact:true});
const refresh=page=>panel(page).getByRole('button',{name:'Refresh workspace history',exact:true});
async function snapshot(page){return page.evaluate(()=>studioSnapshot());}
async function reply(page,kind='success',select={}){await page.evaluate(({kind,select})=>studioReply(kind,select),{kind,select});await pause(page);}
async function open(page,id='calendar',title='Synthetic Calendar',seed=''){
  await page.evaluate(({id,title,seed})=>fridayOpenWorkspaceChat(id,title,'',seed),{id,title,seed});await pause(page);
}
async function privacy(page,on){await page.evaluate(on=>studioPrivacy(on),on);await pause(page);}
async function start(page,text='Synthetic workspace request'){await input(page).fill(text);await send(page).click();}
(async()=>{
  const minimum=Math.max(4,Number(process.env.FRIDAY_CHECK_MIN_GIB)||6);
  assert.ok(os.freemem()>minimum*1073741824,'Not enough free memory for mounted Salon checks');
  if(output){const relative=path.relative(root,path.resolve(output));assert.ok(relative.startsWith('..'+path.sep)||path.isAbsolute(relative),'Proofs stay outside the repository');fs.mkdirSync(output,{recursive:true});}
  const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
  assert.ok(typeof executablePath==='string'&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select an explicit installed dedicated test browser');
  const results=[],browser=await chromium.launch({executablePath,headless:true});
  console.log(JSON.stringify({browser:{executablePath,version:browser.version()}}));
  async function run(name,test,{autoLoad=true,initialPrivate=false,reducedMotion='no-preference'}={}){
    const page=await browser.newPage({viewport:{width:1200,height:900},reducedMotion}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    try{
      await page.route('**/*',route=>route.abort());
      await page.setContent('<style>body{margin:0;background:#101620;color:#ddd;font:14px Arial,sans-serif}button{min-height:32px}textarea{min-height:44px}#notices{max-width:650px}</style><div id="root"></div><div id="notices" role="log"></div>');
      await page.addScriptTag({path:path.join(root,'static/vendor/react-18.3.1.production.min.js')});
      await page.addScriptTag({path:path.join(root,'static/vendor/react-dom-18.3.1.production.min.js')});
      await page.evaluate(initialPrivate=>{
        window.__fridayOffRecord=initialPrivate;
        window.studioPrivacy=on=>{window.__fridayOffRecord=on;dispatchEvent(new CustomEvent('friday:off-record',{detail:{on}}));};
        const nativeSet=setTimeout.bind(window),nativeClear=clearTimeout.bind(window),timeouts=new Map();let next=10000000;
        window.setTimeout=(fn,delay,...args)=>{if(delay!==30000)return nativeSet(fn,delay,...args);const id=++next;timeouts.set(id,()=>fn(...args));return id;};
        window.clearTimeout=id=>{if(!timeouts.delete(id))nativeClear(id);};
        window.studioExpire=()=>{const item=timeouts.entries().next().value;if(!item)throw new Error('No bounded request timer');timeouts.delete(item[0]);item[1]();};
        window.fixtureRequests=[];window.fixtureChanges=[];window.fixtureOpens=[];window.fixtureNotices=[];window.fixtureScrolls=[];
        const nativeScroll=Element.prototype.scrollIntoView;
        Element.prototype.scrollIntoView=function(options){fixtureScrolls.push(options?.behavior||'auto');return nativeScroll.call(this,options);};
        window.fetch=(url,options={})=>{
          const match=/^\/api\/workspace\/(library|calendar)\/(chat|reset|revert|chat\/clear)$/.exec(url);
          if(!match)throw new Error('Unexpected fixture request: '+url);
          return new Promise((resolve,reject)=>{
            const request={id:fixtureRequests.length,workspace:match[1],path:match[2],method:options.method||'GET',body:options.body?JSON.parse(options.body):null,resolve,reject,finished:false};fixtureRequests.push(request);
            options.signal?.addEventListener('abort',()=>{request.finished=true;request.aborted=true;reject(new DOMException('Synthetic timeout','AbortError'));},{once:true});
          });
        };
        window.studioReply=(kind='success',select={})=>{
          const request=fixtureRequests.find(r=>!r.finished&&Object.entries(select).every(([key,value])=>r[key]===value));
          if(!request)throw new Error('No matching pending Salon request');request.finished=true;
          if(kind==='network'){request.reject(new Error('Synthetic transport failure'));return;}
          const original={note:'Original '+request.workspace+' note'},versions=[{id:'v1',label:'Original appearance'}];
          let data={status:'ok',customization:original,versions};
          if(request.method==='GET')data.chat=[];
          else if(request.path==='chat')data={...data,response:'Synthetic applied reply',applied:true,revert_to:'v1',customization:{note:'Applied '+request.body.message}};
          else if(request.path==='reset')data={...data,customization:{}};
          else if(request.path==='chat/clear')data={status:'ok',chat:[]};
          if(kind==='partial')data={status:'ok'};
          if(kind==='http')data={status:'error',message:'Synthetic refusal'};
          request.resolve({ok:kind!=='http',status:kind==='http'?503:200,json:async()=>data});
        };
      },initialPrivate);
      await page.addScriptTag({content:`
        const {useState,useRef,useEffect,useCallback,useContext}=React;
        const WsCustomCtx=React.createContext({map:{},setCust:()=>{}});
        const fridayName=()=> 'Friday',fridayTM=text=>text;
        const FridayDoc=({text})=>React.createElement('span',null,text);
        const useFridaySpatialSurface=()=>{};
        const useFridayToast=()=>useCallback(text=>{fixtureNotices.push(text);const line=document.createElement('p');line.textContent=text;document.getElementById('notices').appendChild(line);},[]);
        ${enter}
        ${component}
        function Fixture(){
          ${registration}
          const [map,setMap]=useState({});
          const openWs=id=>fixtureOpens.push(id);
          const setCust=useCallback((id,patch)=>{fixtureChanges.push({id,patch});setMap(previous=>({...previous,[id]:patch}));},[]);
          ${opener}
          ${keyedSurface}
          window.studioSnapshot=()=>({selected:wsChat?.wsId||null,map,requests:fixtureRequests.map(({id,workspace,path,method,body,finished,aborted})=>({id,workspace,path,method,body,finished,aborted})),changes:fixtureChanges.slice(),opens:fixtureOpens.slice(),notices:fixtureNotices.slice(),scrolls:fixtureScrolls.slice()});
          return React.createElement(WsCustomCtx.Provider,{value:{map,setCust}},shellWsChat);
        }
        const fixtureRoot=ReactDOM.createRoot(document.getElementById('root'));
        window.studioForceUnmount=()=>fixtureRoot.unmount();fixtureRoot.render(React.createElement(Fixture));
      `});
      await expect.poll(()=>page.evaluate(()=>typeof window.fridayOpenWorkspaceChat)).toBe('function');
      await open(page,'library','Synthetic Library');await expect(panel(page)).toBeVisible();
      if(autoLoad&&!initialPrivate){await expect.poll(async()=>(await snapshot(page)).requests.length).toBe(1);await reply(page);}
      await test(page);assert.deepEqual(errors,[],'Actual mounted Salon and root opener must not throw');results.push({name,status:'passed'});
    }catch(error){results.push({name,status:'failed',message:error.message});}
    finally{await page.close();}
  }
  try{
    await run('pending request blocks actual root replacement and closing',async page=>{
      await start(page);await open(page);await expect(panel(page)).toBeVisible();await expect(panel(page,'Synthetic Calendar')).toHaveCount(0);
      await panel(page).getByRole('button',{name:'Close',exact:true}).click();await expect(panel(page)).toBeVisible();
      const pending=await snapshot(page);assert.equal(pending.selected,'library');assert.deepEqual(pending.opens,['library']);assert.equal(pending.requests.length,2);
      await reply(page);await panel(page).getByRole('button',{name:'Close',exact:true}).click();await expect(panel(page)).toHaveCount(0);
    });
    await run('unsent replacement names its target and supports Keep then explicit Discard',async page=>{
      await input(page).fill('Unsent Library idea');await open(page);
      await expect(panel(page).getByRole('alert')).toContainText('open Synthetic Calendar');
      await panel(page).getByRole('button',{name:'Keep editing',exact:true}).click();await expect(input(page)).toHaveValue('Unsent Library idea');
      assert.deepEqual((await snapshot(page)).opens,['library']);await open(page);
      await panel(page).getByRole('button',{name:'Discard draft',exact:true}).click();await expect(panel(page,'Synthetic Calendar')).toBeVisible();
      assert.equal((await snapshot(page)).selected,'calendar');await reply(page);
    });
    await run('same-workspace quick action cannot silently replace a draft',async page=>{
      await input(page).fill('Keep my Library idea');await open(page,'library','Synthetic Library','Synthetic quick action');
      await expect(panel(page).getByRole('alert')).toContainText('open Synthetic Library');assert.equal((await snapshot(page)).requests.length,1);
      await panel(page).getByRole('button',{name:'Keep editing',exact:true}).click();await expect(input(page)).toHaveValue('Keep my Library idea');
      await open(page,'library','Synthetic Library','Synthetic quick action');await panel(page).getByRole('button',{name:'Discard draft',exact:true}).click();
      await expect.poll(async()=>(await snapshot(page)).requests.filter(r=>r.method==='POST').length).toBe(1);
      assert.equal((await snapshot(page)).requests.find(r=>r.method==='POST').body.message,'Synthetic quick action');await reply(page,'success',{method:'POST'});
    });
    await run('timeout keeps the draft, unlocks closing and requires history before retry',async page=>{
      await start(page);await page.evaluate(()=>studioExpire());await expect(input(page)).toHaveValue('Synthetic workspace request');await expect(send(page)).toBeDisabled();
      await expect(page.locator('#notices')).toContainText('timed out');
      await panel(page).getByRole('button',{name:'Close',exact:true}).click();await expect(panel(page).getByRole('button',{name:'Discard draft',exact:true})).toBeVisible();
      await panel(page).getByRole('button',{name:'Keep editing',exact:true}).click();await expect(send(page)).toBeDisabled();
      await refresh(page).click();await reply(page);await expect(send(page)).toBeEnabled();await expect(input(page)).toHaveValue('Synthetic workspace request');
    });
    for(const action of ['reset','reply'])await run('partial ok '+action+' cannot replace customization or claim success',async page=>{
      const changes=(await snapshot(page)).changes.length;
      if(action==='reply')await start(page);
      else{await panel(page).getByRole('button',{name:'Version history',exact:true}).click();await panel(page).getByRole('button',{name:'Reset to default',exact:true}).click();await panel(page).getByRole('button',{name:'Reset appearance',exact:true}).click();}
      await reply(page,'partial');assert.equal((await snapshot(page)).changes.length,changes);assert.equal((await snapshot(page)).map.library.note,'Original library note');
      await expect(panel(page).getByText('Refresh workspace history to confirm the last result before continuing.',{exact:true})).toBeVisible();
      assert.ok(!(await snapshot(page)).notices.some(text=>text.startsWith('✨ Applied:')||text==='Workspace reset to default'));
      if(action==='reply')await expect(input(page)).toHaveValue('Synthetic workspace request');
    });
    await run('privacy cycle during pending reply suppresses late customization and keeps its public draft',async page=>{
      await start(page);const changes=(await snapshot(page)).changes.length;await privacy(page,true);await expect(input(page)).toBeDisabled();
      await privacy(page,false);await reply(page);assert.equal((await snapshot(page)).changes.length,changes);
      await expect(input(page)).toHaveValue('Synthetic workspace request');await expect(send(page)).toBeDisabled();
      await expect(panel(page).getByText('Synthetic applied reply',{exact:true})).toHaveCount(0);
    });
    await run('native private input is refused before its disabled render',async page=>{
      await input(page).fill('Public draft');await page.evaluate(()=>{
        window.__fridayOffRecord=true;const node=document.querySelector('.friday-workspace-studio textarea');
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(node,'Private text');node.dispatchEvent(new Event('input',{bubbles:true}));
      });
      await expect(input(page)).toHaveValue('Public draft');await privacy(page,true);await expect(input(page)).toBeDisabled();
      await privacy(page,false);await refresh(page).click();await reply(page);await expect(input(page)).toHaveValue('Public draft');
    });
    await run('initial private Salon cannot author a saved-history request',async page=>{
      await expect(input(page)).toBeDisabled();await expect(send(page)).toBeDisabled();
      await expect(panel(page).getByText(/Off the record: workspace changes are paused/)).toBeVisible();
      assert.equal((await snapshot(page)).requests.length,0);
      await privacy(page,false);await expect(send(page)).toBeDisabled();await refresh(page).click();await reply(page);await input(page).fill('Public after reload');await expect(send(page)).toBeEnabled();
    },{initialPrivate:true});
    await run('a private native-opener seed cannot become a later public draft',async page=>{
      await open(page,'library','Synthetic Library','Privately supplied seed');await expect(input(page)).toHaveValue('');
      assert.equal((await snapshot(page)).requests.length,0);
      await privacy(page,false);await refresh(page).click();await reply(page);await expect(input(page)).toHaveValue('');await expect(send(page)).toBeDisabled();
      await input(page).fill('New public request');await send(page).click();
      assert.equal((await snapshot(page)).requests.at(-1).body.message,'New public request');await reply(page);
    },{initialPrivate:true});
    await run('current private global suppresses completion before the event arrives',async page=>{
      await start(page);const changes=(await snapshot(page)).changes.length;
      await page.evaluate(()=>{window.__fridayOffRecord=true;studioReply();});await pause(page);
      assert.equal((await snapshot(page)).changes.length,changes);await expect(input(page)).toHaveValue('Synthetic workspace request');
      await expect(panel(page).getByText('Synthetic applied reply',{exact:true})).toHaveCount(0);
    });
    await run('synchronous privacy cycle revokes an old native Send target',async page=>{
      await input(page).fill('Public draft');const count=(await snapshot(page)).requests.length;
      await page.evaluate(()=>{studioPrivacy(true);studioPrivacy(false);[...document.querySelectorAll('.friday-workspace-studio button')].find(button=>button.textContent==='Send').click();});
      await pause(page);assert.equal((await snapshot(page)).requests.length,count);await expect(input(page)).toHaveValue('Public draft');await expect(send(page)).toBeDisabled();
    });
    await run('only the latest successful history read clears uncertainty',async page=>{
      await expect.poll(async()=>(await snapshot(page)).requests.length).toBe(1);
      await start(page);await reply(page,'http',{method:'POST'});await expect(send(page)).toBeDisabled();
      await reply(page,'success',{id:0});await expect(send(page)).toBeDisabled();assert.equal((await snapshot(page)).changes.length,0);
      await refresh(page).click();await refresh(page).click();
      const reads=(await snapshot(page)).requests.filter(r=>r.method==='GET'&&!r.finished);assert.equal(reads.length,2);
      await reply(page,'success',{id:reads[0].id});await expect(send(page)).toBeDisabled();assert.equal((await snapshot(page)).changes.length,0);
      await reply(page,'success',{id:reads[1].id});await expect(send(page)).toBeEnabled();assert.equal((await snapshot(page)).changes.length,1);
    },{autoLoad:false});
    await run('privacy invalidates a pending history read until a fresh public read',async page=>{
      await expect.poll(async()=>(await snapshot(page)).requests.length).toBe(1);
      await privacy(page,true);await privacy(page,false);await reply(page);assert.equal((await snapshot(page)).changes.length,0);
      await input(page).fill('Public draft');await expect(send(page)).toBeDisabled();await refresh(page).click();await reply(page);await expect(send(page)).toBeEnabled();
    },{autoLoad:false});
    await run('forced unmount revokes an outstanding mutation callback',async page=>{
      await start(page);const before=await snapshot(page);await page.evaluate(()=>studioForceUnmount());await expect(panel(page)).toHaveCount(0);
      // Real unmount aborts the pending request; its promise must not publish a late result.
      await pause(page);const after=await snapshot(page);assert.equal(after.changes.length,before.changes.length);
      assert.equal(after.requests.at(-1).aborted,true);
    });
    for(const reducedMotion of ['reduce','no-preference'])await run('Salon scrolling honors '+reducedMotion,async page=>{
      await start(page);await reply(page);const scrolls=(await snapshot(page)).scrolls;
      assert.ok(scrolls.length>=2,'The actual message/loading effect requests scrolling');
      assert.ok(scrolls.every(behavior=>behavior===(reducedMotion==='reduce'?'auto':'smooth')),'Explicit scroll behavior follows the current motion preference');
    },{reducedMotion});
  }finally{
    if(output)fs.writeFileSync(path.join(output,'workspace-chat-ownership-results.json'),JSON.stringify({baseline:baseline||null,results},null,2));
    await browser.close();
  }
  process.stdout.write(JSON.stringify({baseline:baseline||null,results})+'\n');if(results.some(result=>result.status==='failed'))process.exitCode=1;
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
