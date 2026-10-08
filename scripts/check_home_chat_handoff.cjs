/* The real Home component, real conversation draft hook and real root ownership
 * closures are mounted together. Surrounding services/chrome are synthetic. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium,expect} = require('@playwright/test');
const root=path.resolve(__dirname,'..');
const baseline=process.env.FRIDAY_BASELINE_REV;
const output=process.env.FRIDAY_HOME_HANDOFF_PROOFS;
function ownerSource(file) {
  let source=fs.readFileSync(path.join(root,file),'utf8');
  if(baseline){const old=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':'+file],{cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:12e6});assert.equal(old.status,0,old.stderr);source=old.stdout;}
  source=source.replace(/\r\n/g,'\n');
  const hookStart=source.indexOf('function useFridayConversationDrafts('), hookEnd=source.indexOf('\nfunction ExperienceApprovals(',hookStart);
  const ownerStart=source.indexOf('  const showChat = on => {'), ownerEnd=source.indexOf('  useEffect(() => {\n    const draft = event => {',ownerStart);
  assert.ok(hookStart>=0&&hookEnd>hookStart,'Conversation draft hook has a bounded source region in '+file);
  assert.ok(ownerStart>=0&&ownerEnd>ownerStart,'Root Home draft flow has a bounded source region in '+file);
  return {hook:source.slice(hookStart,hookEnd),owner:source.slice(ownerStart,ownerEnd)};
}
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
(async()=>{
  assert.ok(os.freemem()>6*1073741824,'At least 6 GB free is required');
  if(output){const relative=path.relative(root,path.resolve(output));assert.ok(relative.startsWith('..'+path.sep)||path.isAbsolute(relative),'Proofs stay outside the repository');fs.mkdirSync(output,{recursive:true});}
  const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
  assert.ok(typeof executablePath==='string'&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select an explicit installed dedicated test browser');
  const browser=await chromium.launch({executablePath,headless:true});
  console.log(JSON.stringify({browser:{executablePath,version:browser.version()}}));
  const results=[];
  async function run(file,name,check) {
    const page=await browser.newPage({viewport:{width:1100,height:900}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    try{
      const source=ownerSource(file);
      await page.route('**/*',route=>route.abort());
      await page.setContent('<style>body{font:14px sans-serif}textarea{display:block;width:90%;min-height:60px}button{min-height:32px}#owner{padding:12px}</style><div id="root"></div>');
      for(const asset of ['vendor/react-18.3.1.production.min.js','vendor/react-dom-18.3.1.production.min.js','friday_experience.js'])await page.addScriptTag({path:path.join(root,'static',asset)});
      await page.addScriptTag({content:`
        const {useState,useRef,useEffect}=React, E=React.createElement, FRIDAY_BRAND={name:'Friday'};
        const fixtureToasts=[], fixtureOpens=[], fixtureRequests=[];
        const fridayToast=text=>fixtureToasts.push(text);
        const fixtureRows=[{id:'personal',title:'Personal without a project property'},{id:'other-personal',title:'Another personal chat',project:null},{id:'project-chat',title:'Project A chat',project:'project-a'}];
        const fixtureApi=(url,options={})=>{
          if(url==='/api/desktop/board')return Promise.resolve({ok:true,json:async()=>({status:'ok',revision:1,generated_at:1,cards:[],hidden_cards:[],sources:[]})});
          if(url.startsWith('/api/projects/'))return Promise.resolve({ok:true,json:async()=>({status:'ok',files:[]})});
          if(url==='/api/conversations'&&options.method==='POST')return new Promise((resolve,reject)=>fixtureRequests.push({body:JSON.parse(options.body),resolve,reject}));
          throw new Error('Unexpected fixture request '+url);
        };
        window.finishHomeCreation=(kind='success')=>{
          const request=fixtureRequests.find(row=>!row.finished);if(!request)throw new Error('No pending creation');request.finished=true;
          if(kind==='network'){request.reject(new Error('Synthetic unavailable connection'));return;}
          const id=kind==='number'?17:kind==='blank'?' ':'created-context';
          const conversation={id,project:request.body.project,title:'Created for Home'};
          if(kind==='success')fixtureRows.push(conversation);
          request.resolve({ok:kind!=='http',json:async()=>kind==='http'?{status:'error',message:'Synthetic refusal'}:{status:'ok',conversation}});
        };
        ${source.hook}
        function RootFixture(){
          const {convId,setConvId,chatIn,setChatIn,getConversationId,getConversationRevision}=useFridayConversationDrafts('personal');
          const [chatOpen,setChatOpen]=useState(false),[convList,setConvList]=useState(fixtureRows.slice());
          const experienceEnabled=true,experienceDesktop=true,apiFetch=fixtureApi;
          const refreshConvs=async()=>setConvList(fixtureRows.slice());
          const openConversation=id=>{fixtureOpens.push(id);setConvId(id);};
          ${source.owner}
          const globalOpen=typeof showGlobalChat==='function'?showGlobalChat:()=>showChat(true);
          const register=typeof registerHomeDraft==='function'?registerHomeDraft:undefined;
          window.handoffFixture={select:setConvId,setText:setChatIn,open:globalOpen,register,
            snapshot:()=>({id:getConversationId(),revision:getConversationRevision?.(),text:chatIn,open:chatOpen,opens:fixtureOpens.slice(),toasts:fixtureToasts.slice(),requests:fixtureRequests.map(row=>row.body)})};
          return E('div',{id:'owner'},
            E('button',{onClick:globalOpen},'Global chat'),E('button',{onClick:()=>showChat(false)},'Hide global chat'),
            chatOpen&&E('textarea',{'aria-label':'Root chat draft',value:chatIn,onChange:event=>setChatIn(event.target.value)}),
            E(FridayExperience,{enabled:true,desktopVisible:true,chatOpen,
              projects:[{id:'project-a',name:'Project A'},{id:'project-b',name:'Project B'},{id:'project-new',name:'Project with no chat'}],
              conversations:convList,workspaces:[],activeConversationId:convId,apiFetch,
              onRegisterHomeDraft:register,onDraftChat:experienceDraft,onShowChat:globalOpen}));
        }
        window.rootFixture=ReactDOM.createRoot(document.getElementById('root'));window.rootFixture.render(E(RootFixture));
      `});
      await expect(page.locator('.fx-day-composer textarea')).toBeVisible();
      await check(page);
      assert.deepEqual(errors,[],'Mounted actual owner/Home code has no runtime errors');
      results.push({file,name,status:'passed'});console.log('PASS '+file+': '+name);
    }catch(error){results.push({file,name,status:'failed',error:error.message});console.error('FAIL '+file+': '+name+': '+error.message);}
    finally{await page.close();}
  }
  const home=page=>page.locator('.fx-day-composer textarea');
  const chat=page=>page.getByRole('textbox',{name:'Root chat draft',exact:true});
  try{
    for(const file of ['index.html','ui_parts/app.html']){
      await run(file,'personal null context appends once without retargeting or sending',async page=>{
        await page.evaluate(()=>handoffFixture.setText('Existing chat draft'));
        await home(page).fill('Continue this thought');
        await page.getByRole('button',{name:'Global chat',exact:true}).evaluate(button=>{button.click();button.click();});
        await expect(chat(page)).toHaveValue('Existing chat draft\n\nContinue this thought');
        await expect(home(page)).toHaveCount(0);
        const state=await page.evaluate(()=>handoffFixture.snapshot());
        assert.equal(state.id,'personal');assert.deepEqual(state.opens,[]);assert.deepEqual(state.requests,[]);
        await page.getByRole('button',{name:'Hide global chat',exact:true}).click();
        await expect(home(page)).toHaveValue('');
      });
      await run(file,'matching project transfers into the selected project chat only',async page=>{
        await page.getByLabel('Conversation project',{exact:true}).selectOption('project-a');
        await home(page).fill('Project-only note');
        await page.evaluate(()=>{handoffFixture.select('project-chat');handoffFixture.setText('Existing project draft');});
        await page.getByRole('button',{name:'Global chat',exact:true}).click();
        await expect(chat(page)).toHaveValue('Existing project draft\n\nProject-only note');
        assert.equal((await page.evaluate(()=>handoffFixture.snapshot())).id,'project-chat');
      });
      await run(file,'mismatched project preserves both drafts and never navigates',async page=>{
        await page.evaluate(()=>handoffFixture.setText('Personal stays personal'));
        await page.getByLabel('Conversation project',{exact:true}).selectOption('project-a');
        await home(page).fill('Keep this with Project A');
        await page.getByRole('button',{name:'Global chat',exact:true}).click();
        await expect(chat(page)).toHaveValue('Personal stays personal');
        const state=await page.evaluate(()=>handoffFixture.snapshot());
        assert.equal(state.id,'personal');assert.deepEqual(state.opens,[]);assert.equal(state.toasts.length,1);
        await page.getByRole('button',{name:'Hide global chat',exact:true}).click();
        await expect(home(page)).toHaveValue('Keep this with Project A');
      });
      await run(file,'null current conversation does not steal a project draft',async page=>{
        await page.getByLabel('Conversation project',{exact:true}).selectOption('project-a');
        await home(page).fill('A scoped draft');
        await page.evaluate(()=>handoffFixture.select(null));
        await page.getByRole('button',{name:'Global chat',exact:true}).click();
        await expect(chat(page)).toHaveValue('');
        assert.equal((await page.evaluate(()=>handoffFixture.snapshot())).id,null);
        await page.getByRole('button',{name:'Hide global chat',exact:true}).click();await expect(home(page)).toHaveValue('A scoped draft');
      });
      await run(file,'older registration cleanup cannot erase a newer owner',async page=>{
        await page.evaluate(()=>{
          if(!handoffFixture.register)throw new Error('Home has no draft registration');
          const old=handoffFixture.register(()=>({text:'Old owner',projectId:null,consume:()=>true}));
          let consumed=false;
          handoffFixture.register(()=>consumed?null:{text:'New owner',projectId:null,consume:()=>{consumed=true;return true;}});old();
        });
        await page.getByRole('button',{name:'Global chat',exact:true}).click();await expect(chat(page)).toHaveValue('New owner');
      });
      await run(file,'Continue deliberately selects the matching personal chat and preserves project draft',async page=>{
        await page.evaluate(()=>{handoffFixture.setText('Personal saved draft');handoffFixture.select('project-chat');handoffFixture.setText('Project saved draft');});
        await home(page).fill('A personal follow-up');
        await page.getByRole('button',{name:'Continue →',exact:true}).click();
        await expect(chat(page)).toHaveValue('Personal saved draft\n\nA personal follow-up');
        assert.equal((await page.evaluate(()=>handoffFixture.snapshot())).id,'personal');
        await page.evaluate(()=>handoffFixture.select('project-chat'));await expect(chat(page)).toHaveValue('Project saved draft');
      });
      for(const path of ['different','aba'])await run(file,'pending Home creation cannot take ownership after '+path,async page=>{
        await page.getByLabel('Conversation project',{exact:true}).selectOption('project-new');await home(page).fill('Retain this Home draft');
        await page.getByRole('button',{name:'Continue →',exact:true}).click();
        await expect(page.getByRole('button',{name:'Preparing…',exact:true})).toBeDisabled();
        await page.evaluate(()=>{handoffFixture.select('other-personal');handoffFixture.setText('A newer chat draft');});
        if(path==='aba')await page.evaluate(()=>{handoffFixture.select('personal');handoffFixture.setText('Changed after returning');});
        await expect.poll(async()=>{
          const state=await page.evaluate(()=>handoffFixture.snapshot());
          return {id:state.id,revision:state.revision,text:state.text,open:state.open};
        },{timeout:5000,message:'The newer chat draft is rendered before the pending Home reply'}).toEqual({
          id:path==='aba'?'personal':'other-personal',revision:path==='aba'?2:1,
          text:path==='aba'?'Changed after returning':'A newer chat draft',open:false
        });
        const before=await page.evaluate(()=>handoffFixture.snapshot());
        await page.evaluate(()=>finishHomeCreation());await settle(page);
        await expect(page.locator('.fx-error')).toContainText('active chat changed');
        await expect(home(page)).toHaveValue('Retain this Home draft');
        const after=await page.evaluate(()=>handoffFixture.snapshot());
        for(const key of ['id','revision','text','open','opens'])assert.deepEqual(after[key],before[key],key);
      });
      for(const kind of ['network','http','number','blank'])await run(file,kind+' creation failure preserves both Home and chat drafts',async page=>{
        await page.evaluate(()=>handoffFixture.setText('Existing chat text'));
        await page.getByLabel('Conversation project',{exact:true}).selectOption('project-new');await home(page).fill('Unsent Home text');
        await page.getByRole('button',{name:'Continue →',exact:true}).click();await page.evaluate(kind=>finishHomeCreation(kind),kind);
        await expect(page.locator('.fx-error')).toBeVisible();await expect(home(page)).toHaveValue('Unsent Home text');
        const state=await page.evaluate(()=>handoffFixture.snapshot());assert.equal(state.id,'personal');assert.equal(state.text,'Existing chat text');assert.equal(state.open,false);assert.deepEqual(state.opens,[]);
      });
    }
  }finally{if(output)fs.writeFileSync(path.join(output,'home-handoff-results.json'),JSON.stringify({baseline:baseline||null,results},null,2));await browser.close();}
  assert.equal(results.filter(result=>result.status==='failed').length,0,'All Home owner/handoff regressions pass');
})().catch(error=>{console.error(error);process.exitCode=1;});
