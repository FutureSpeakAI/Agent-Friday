/* Mount the served ChatSurface with real React and conversation draft state.
 * Only the surrounding services are synthetic: no server, model or devices. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const baseline = process.env.FRIDAY_BASELINE_REV;
const output = process.env.FRIDAY_NEW_CHAT_PROOFS;
let html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
if (baseline) {
  const old = spawnSync('git', ['-c', 'safe.directory=' + root, 'show', baseline + ':index.html'],
    {cwd:root, encoding:'utf8', windowsHide:true, maxBuffer:12e6});
  assert.equal(old.status, 0, old.stderr); html = old.stdout;
}
function sourceFunction(name) {
  const start = html.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name + ' is present in the served source');
  const rest = html.slice(start), next = /\n(?:async )?function [A-Za-z_]\w*\(/.exec(rest.slice(20));
  assert.ok(next, name + ' has a bounded source region');
  return rest.slice(0, next.index + 20);
}
const mountedSource = ['useFridayConversationDrafts', 'ConversationRow', 'ConversationBar', 'ChatSurface']
  .map(sourceFunction).join('\n');

(async () => {
  assert.ok(os.freemem() > 6 * 1073741824, 'At least 6 GB free is required');
  if (output) {
    const relative = path.relative(root, path.resolve(output));
    assert.ok(relative.startsWith('..' + path.sep) || path.isAbsolute(relative), 'Proofs stay outside the repository');
    fs.mkdirSync(output, {recursive:true});
  }
  const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
  assert.ok(typeof executablePath==='string'&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select an explicit installed dedicated test browser');
  const browser = await chromium.launch({executablePath, headless:true});
  console.log(JSON.stringify({browser:{executablePath,version:browser.version()}}));
  let failures = 0;
  const results = [];
  async function run(name, check, mode = 'panel') {
    const page = await browser.newPage({viewport:{width:1000, height:900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    try {
      await page.route('**/*', route => route.abort());
      await page.setContent('<style>body{margin:0;background:#101620;color:#ddd;font:14px sans-serif}#host{display:flex;flex-direction:column;min-width:0;width:100%;box-sizing:border-box}button{min-height:30px}textarea{min-height:70px}</style><div id="root"></div>');
      await page.addScriptTag({path:path.join(root, 'static/vendor/react-18.3.1.production.min.js')});
      await page.addScriptTag({path:path.join(root, 'static/vendor/react-dom-18.3.1.production.min.js')});
      await page.addScriptTag({content:`
        const {useState,useRef,useEffect}=React, E=React.createElement;
        const FRIDAY_BRAND={name:'Friday'};
        const fridayName=()=>FRIDAY_BRAND.name, fridayTM=x=>x, prettyModel=x=>x;
        const fridayTurnStatusText=()=> 'Synthetic turn running';
        const renderFridayMarkdown=x=>x;
        const useSnapMenuOpener=()=>({open:false,bind:{},menu:{},anchor:{current:null}});
        const FridayChatShell=props=>E('div',{id:'host'},props.children);
        const FridayChatInput=props=>E('textarea',{'data-chat-input':'',value:props.value,onChange:e=>props.onChange(e.target.value)});
        const PrivacyHoldCard=()=>E('div',{'data-pause-fixture':''},'Synthetic pending decision');
        const PauseWarning=PrivacyHoldCard;
        const SendTo=()=>null, ChatReasoning=()=>null, PodcastButton=()=>null, SnapMenu=()=>null;
        const ConversationSeatPicker=()=>null;
        const fixtureRequests=[], fixtureOpens=[], fixtureConversations=[
          {id:'conv-main',title:'Synthetic Main'}, {id:'conv-other',title:'Synthetic Other'}];
        let fixtureRefreshes=0;
        const apiFetch=(url, options)=>{
          if(url!=='/api/conversations'||options.method!=='POST')throw new Error('Unexpected fixture request');
          if(JSON.stringify(JSON.parse(options.body))!==JSON.stringify({title:'New chat'}))throw new Error('Unexpected creation payload');
          return new Promise((resolve,reject)=>fixtureRequests.push({resolve,reject}));
        };
        window.finishCreation=(kind='success',id='conv-created')=>{
          const pending=fixtureRequests.find(r=>!r.finished);
          if(!pending)throw new Error('No pending creation'); pending.finished=true;
          if(kind==='network'){pending.reject(new Error('Synthetic transport error'));return;}
          let data={status:'ok',conversation:{id}};
          if(kind==='http')data={status:'error',message:'Synthetic refusal'};
          if(kind==='status')data={status:'error',conversation:{id}};
          if(kind==='missing')data={status:'ok'};
          if(kind==='number')data={status:'ok',conversation:{id:123}};
          if(kind==='blank')data={status:'ok',conversation:{id:' '}};
          if(kind==='same')data={status:'ok',conversation:{id:'conv-main'}};
          if(kind==='success')fixtureConversations.push({id,title:'Synthetic Created'});
          pending.resolve({ok:kind!=='http',status:kind==='http'?500:201,
            json:()=>kind==='json'?Promise.reject(new SyntaxError('Synthetic invalid JSON')):Promise.resolve(data)});
        };
      `});
      await page.addScriptTag({content:mountedSource});
      await page.evaluate(mode => {
        function Fixture() {
          const drafts = useFridayConversationDrafts('conv-main');
          const epoch = React.useRef(1), observed = React.useRef('conv-main');
          const [messages, setMessages] = React.useState([{role:'user', text:'Synthetic existing message'}]);
          const [loading, setLoading] = React.useState(true);
          const [pause, setPause] = React.useState({kind:'privacy_hold',message:'Synthetic pending decision'});
          const [list, setList] = React.useState(fixtureConversations.slice());
          const [visible, setVisible] = React.useState(true);
          const [listOpen, setListOpen] = React.useState(false);
          const openConversation = id => {
            fixtureOpens.push(id); epoch.current += 1;
            drafts.setConvId(id); setLoading(false); setPause(null); setListOpen(false);
            setMessages(id === 'conv-main' ? [{role:'user',text:'Synthetic existing message'}]
              : id === 'conv-other' ? [{role:'user',text:'Synthetic other message'}] : []);
          };
          React.useEffect(() => {
            if (observed.current !== drafts.convId) { observed.current = drafts.convId; openConversation(drafts.convId); }
          }, [drafts.convId]);
          window.fixtureSelect = openConversation;
          window.fixtureUnmount = () => setVisible(false);
          window.fixtureStartAnotherTurn = () => { epoch.current += 1; };
          window.fixtureSnapshot = () => ({id:drafts.convId, messages:messages.map(m=>m.text),
            draft:drafts.chatIn, loading, pause:!!pause, requests:fixtureRequests.length,
            refreshes:fixtureRefreshes, opens:fixtureOpens.slice(), epoch:epoch.current});
          const noop = () => {};
          return React.createElement(React.Fragment, null,
            React.createElement('div', {'data-selected-fixture':''}, drafts.convId),
            visible && React.createElement(ChatSurface, {
              mode, convId:drafts.convId, convList:list, convOpen:listOpen, setConvOpen:setListOpen,
              setConvId:drafts.setConvId, openConversation, chatEpoch:epoch,
              refreshConvs:() => {fixtureRefreshes++; setList(fixtureConversations.slice());},
              chatMsgs:messages, setChatMsgs:setMessages, chatIn:drafts.chatIn, setChatIn:drafts.setChatIn,
              chatLoad:loading, setChatLoad:setLoading, pausePending:pause, setPausePending:setPause,
              setChatOpen:() => setVisible(false), agentSettings:{}, audioInputDevices:[], audioOutputDevices:[],
              voiceStages:{}, voiceNotices:[], micTestMsg:'', speakerTestMsg:'', voiceOn:false,
              chatEnd:{current:null}, chatFileRef:{current:null}, audioDevicePopupRef:{current:null},
              refreshAudioDevicesRef:{current:noop}, sendChat:noop, bindConvSeat:noop,
              setConvPickerOpen:noop, toggleVoice:noop, setAudioDevicePopup:noop,
              toggleCiteSources:noop, testMicrophone:noop, testSpeaker:noop,
            }));
        }
        ReactDOM.createRoot(document.querySelector('#root')).render(React.createElement(Fixture));
      }, mode);
      await expect(page.getByRole('button', {name:'+ New Chat', exact:true})).toBeVisible();
      await page.locator('textarea[data-chat-input]').fill('Synthetic unsent draft');
      await check(page);
      assert.deepEqual(errors, [], 'The actual mounted ChatSurface has no runtime errors');
      results.push({name, status:'passed'}); console.log('PASS ' + name);
    } catch (error) {
      failures++; results.push({name, status:'failed', error:error.message});
      console.error('FAIL ' + name + ': ' + error.message);
    } finally { await page.close(); }
  }
  const start = page => page.getByRole('button', {name:'+ New Chat', exact:true}).click();
  const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const retained = async page => {
    const state = await page.evaluate(() => fixtureSnapshot());
    assert.equal(state.id, 'conv-main'); assert.equal(state.draft, 'Synthetic unsent draft');
    assert.deepEqual(state.messages, ['Synthetic existing message']);
    assert.equal(state.loading, true); assert.equal(state.pause, true); assert.equal(state.epoch, 1);
  };
  try {
    for (const mode of ['panel', 'window']) await run(mode + ' keeps old state until valid creation, then restores its draft on return', async page => {
      await start(page); await retained(page);
      await expect(page.getByRole('button', {name:'Creating…',exact:true})).toBeDisabled();
      await page.evaluate(() => finishCreation());
      await expect(page.locator('[data-selected-fixture]')).toHaveText('conv-created');
      await expect(page.getByRole('button', {name:'Switch conversation',exact:true})).toContainText('Synthetic Created');
      await expect(page.locator('textarea[data-chat-input]')).toHaveValue('');
      await expect(page.getByText('Synthetic existing message',{exact:true})).toHaveCount(0);
      const created = await page.evaluate(() => fixtureSnapshot());
      assert.equal(created.loading, false); assert.equal(created.pause, false);
      await page.evaluate(() => fixtureSelect('conv-main'));
      await expect(page.locator('textarea[data-chat-input]')).toHaveValue('Synthetic unsent draft');
    }, mode);
    for (const kind of ['network','http','json','status','missing','number','blank','same']) await run(kind + ' failure preserves state and permits a confirmed retry', async page => {
      await start(page); await page.evaluate(kind => finishCreation(kind), kind);
      await expect(page.getByRole('alert')).toContainText('Could not confirm a new chat');
      await retained(page);
      if (kind === 'http') {
        await page.setViewportSize({width:320,height:760});
        const box = await page.getByRole('alert').boundingBox();
        assert.ok(box && box.x >= 0 && box.x + box.width <= 321, 'Creation error fits the narrow surface');
        if (output) await page.screenshot({path:path.join(output,'new-chat-error-portrait.png')});
      }
      await start(page); await expect(page.getByRole('alert')).toHaveCount(0);
      await page.evaluate(() => finishCreation());
      await expect(page.locator('[data-selected-fixture]')).toHaveText('conv-created');
    });
    await run('rapid duplicate activation sends one creation', async page => {
      await page.getByRole('button',{name:'+ New Chat',exact:true}).evaluate(button => {button.click();button.click();});
      assert.equal((await page.evaluate(() => fixtureSnapshot())).requests, 1);
      await retained(page);
    });
    for (const path of ['different','aba','same-turn','unmount']) await run('late success cannot take ownership after ' + path, async page => {
      await start(page);
      if (path === 'unmount') await page.evaluate(() => fixtureUnmount());
      else if (path === 'same-turn') await page.evaluate(() => fixtureStartAnotherTurn());
      else {
        await page.evaluate(() => fixtureSelect('conv-other'));
        await expect(page.locator('[data-selected-fixture]')).toHaveText('conv-other');
        if (path === 'aba') {
          await page.evaluate(() => fixtureSelect('conv-main'));
          await expect(page.locator('[data-selected-fixture]')).toHaveText('conv-main');
        }
      }
      const before = await page.evaluate(() => fixtureSnapshot());
      await page.evaluate(() => finishCreation()); await settle(page);
      const after = await page.evaluate(() => fixtureSnapshot());
      for (const key of ['id','messages','draft','loading','pause','opens','epoch']) assert.deepEqual(after[key], before[key], key);
      if (path === 'unmount') assert.equal(after.refreshes, before.refreshes, 'Unmounted surface cannot update its owner');
    });
    await run('late failure cannot attach an error to a different conversation', async page => {
      await start(page); await page.evaluate(() => fixtureSelect('conv-other'));
      await expect(page.locator('[data-selected-fixture]')).toHaveText('conv-other');
      await page.evaluate(() => finishCreation('http')); await settle(page);
      await expect(page.getByRole('alert')).toHaveCount(0);
      await expect(page.getByRole('button',{name:'+ New Chat',exact:true})).toBeEnabled();
    });
  } finally {
    if (output) fs.writeFileSync(path.join(output,'new-chat-results.json'), JSON.stringify({baseline:baseline || null, results},null,2));
    await browser.close();
  }
  assert.equal(failures, 0, failures + ' creation regressions failed');
})().catch(error => { console.error(error); process.exitCode = 1; });
