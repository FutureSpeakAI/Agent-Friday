/* Synthetic Home board interactions, race handling and responsive containment.
 * Run only against the isolated preview. Domain collection/persistence belongs
 * to the board route tests; this fixture verifies the frontend contract. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const base = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3194/';
const output = process.env.FRIDAY_CARD_PROOFS;
const clone = value => JSON.parse(JSON.stringify(value));
const stamp = () => Math.floor(Date.now() / 1000);
const note = (id, title, body = 'A useful note for today.') => ({id, title, body, priority:50, actions:[{label:'Open Library',workspace:'library'}], origin:'saved', pinned:false, created_at:stamp(), updated_at:stamp()});
const followed = () => ({...note('track-task-one','Follow the local task','The latest confirmed local task state.'), origin:'tracked', source:{kind:'task',id:'task-one',label:'Local task',status:'local_only',checked_at:stamp(),updated_at:stamp()}, tracking:{enabled:true,scope:{kind:'task',id:'task-one'},stopped_at:null}, actions:[{label:'Open activity',view:'activity'}]});
const board = page => page.getByRole('region',{name:'Home cards',exact:true});
const card = (page,id) => board(page).locator('[data-card-id="'+id+'"]');
async function options(page,id) {
  const menu=card(page,id).locator('details');
  if(!await menu.evaluate(el=>el.open))await menu.locator('summary').click();
  return menu;
}
async function refresh(page) {
  await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-cards-changed')));
}
async function findCard(page,id) {
  const previous=board(page).getByRole('button',{name:'Previous cards',exact:true});
  while(await previous.count() && await previous.isEnabled())await previous.click();
  for(let i=0;i<30;i++){
    if(await card(page,id).count())return card(page,id);
    const next=board(page).getByRole('button',{name:'More cards',exact:true});
    assert.ok(await next.count() && await next.isEnabled(),'Missing card '+id);
    await next.click();
  }
  throw new Error('Too many card pages');
}
async function proof(page,name) {
  const result=await page.evaluate(()=>{
    const main=document.querySelector('.fx-main[data-view="day"]'), panel=document.querySelector('.fx-working-board');
    const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
    return {main:rect(main),board:rect(panel),client:[main.clientWidth,main.clientHeight],scroll:[main.scrollWidth,main.scrollHeight]};
  });
  assert.ok(result.scroll[0]<=result.client[0]+1 && result.scroll[1]<=result.client[1]+1,name+': desktop overflow '+JSON.stringify(result));
  assert.ok(result.board.w>100 && result.board.h>70,name+': board has usable space');
  assert.ok(result.board.x>=result.main.x-1 && result.board.y>=result.main.y-1 && result.board.x+result.board.w<=result.main.x+result.main.w+1 && result.board.y+result.board.h<=result.main.y+result.main.h+1,name+': board leaves Home');
  if(output)await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});
}
async function draftContract(browser) {
  // A separate component mount makes registration/consumption observable without
  // poking React internals or assuming any hidden assistant/conversation state.
  const page=await browser.newPage();
  try {
    await page.setContent('<div id="fixture"></div>');
    for(const asset of ['vendor/react-18.3.1.production.min.js','vendor/react-dom-18.3.1.production.min.js','friday_experience.js'])await page.addScriptTag({path:path.join(root,'static',asset)});
    await page.evaluate(()=>{
      const response={status:'ok',revision:1,generated_at:1,cards:[],hidden_cards:[],sources:[],files:[]};
      window.draftProps={enabled:true,desktopVisible:true,chatOpen:false,projects:[{id:'project-a',name:'Project A'},{id:'project-b',name:'Project B'}],workspaces:[],apiFetch:async()=>({ok:true,json:async()=>response}),onRegisterHomeDraft:getter=>{window.homeGetter=getter;return()=>{if(window.homeGetter===getter)window.homeGetter=null;};}};
      window.draftRoot=ReactDOM.createRoot(document.getElementById('fixture'));
      window.renderDraft=patch=>{Object.assign(window.draftProps,patch);ReactDOM.flushSync(()=>window.draftRoot.render(React.createElement(window.FridayExperience,window.draftProps)));};
      window.renderDraft({});
    });
    const composer=page.locator('.fx-day-composer textarea');
    await composer.fill('A personal draft');
    assert.deepEqual(await page.evaluate(()=>{const d=window.homeGetter();return {text:d.text,projectId:d.projectId,personal:d.personal};}),{text:'A personal draft',projectId:null,personal:true});
    await page.getByLabel('Conversation project',{exact:true}).selectOption('project-a');
    await expect(composer).toHaveValue('');
    await composer.fill('A project draft');
    await page.evaluate(()=>{window.olderDraft=window.homeGetter();});
    await page.getByLabel('Conversation project',{exact:true}).selectOption('project-b');
    await composer.fill('A different project draft');
    assert.equal(await page.evaluate(()=>window.olderDraft.consume()),false,'A descriptor cannot consume a different project draft');
    await expect(composer).toHaveValue('A different project draft');
    await page.getByLabel('Conversation project',{exact:true}).selectOption('project-a');
    await expect(composer).toHaveValue('A project draft');
    await page.evaluate(()=>window.renderDraft({chatOpen:true}));
    await expect(composer).toHaveCount(0);
    assert.equal(await page.evaluate(()=>window.homeGetter().text),'A project draft','Opening chat alone does not move or erase a Home draft');
    const consumed=await page.evaluate(()=>{const d=window.homeGetter();return [d.consume(),d.consume(),window.homeGetter()];});
    assert.deepEqual(consumed,[true,false,null],'Consumption is once-only before a render');
    await page.evaluate(()=>window.renderDraft({chatOpen:false}));
    await expect(composer).toHaveValue('');
    await page.getByLabel('Conversation project',{exact:true}).selectOption('');
    await expect(composer).toHaveValue('A personal draft');
    // Date and greeting change together after focus/day rollover.
    await page.evaluate(()=>{
      const NativeDate=Date, next=new NativeDate();next.setDate(next.getDate()+1);next.setHours(8,0,0,0);
      window.Date=class extends NativeDate {constructor(...args){super(...(args.length?args:[next.getTime()]));}static now(){return next.getTime();}};
      window.expectedDay=next.toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'});
      window.dispatchEvent(new Event('focus'));
    });
    await expect(page.locator('.fx-day-heading h1')).toHaveText('Good morning. What comes first?');
    await expect(page.locator('.fx-day-heading .fx-eyebrow')).toHaveText(await page.evaluate(()=>window.expectedDay));
    await page.evaluate(()=>window.draftRoot.unmount());
    assert.equal(await page.evaluate(()=>window.homeGetter),null,'Registration cleans up its own getter');
  } finally {await page.close();}
}
async function mutationContract(browser) {
  // Hold the real component's API boundary, including an adapter that ignores
  // abort, so a late acknowledgement cannot consume an owned draft.
  const page=await browser.newPage();
  try {
    await page.setContent('<div id="fixture"></div>');
    for(const asset of ['vendor/react-18.3.1.production.min.js','vendor/react-dom-18.3.1.production.min.js','friday_experience.js'])await page.addScriptTag({path:path.join(root,'static',asset)});
    await page.evaluate(()=>{
      const nativeTimeout=window.setTimeout;
      window.setTimeout=(fn,delay,...args)=>delay===30000?(window.homeDeadline=()=>fn(...args),-1):nativeTimeout(fn,delay,...args);
      window.fixtureBoard={status:'ok',revision:1,generated_at:1,cards:[],hidden_cards:[],sources:[],summary:{private:false}};
      window.readMode='ok';window.posts=0;
      const api=async(url,options={})=>{
        if(options.method==='POST'){
          window.posts++;window.postSignal=options.signal;
          return new Promise(resolve=>{window.finishPost=data=>resolve({ok:true,status:200,json:async()=>data});});
        }
        return {ok:window.readMode!=='offline',status:window.readMode==='offline'?503:200,json:async()=>window.readMode==='offline'?{status:'error',message:'Home is offline.'}:window.readMode==='malformed'?{...window.fixtureBoard,cards:[null]}:window.fixtureBoard};
      };
      window.fixtureRoot=ReactDOM.createRoot(document.getElementById('fixture'));
      ReactDOM.flushSync(()=>window.fixtureRoot.render(React.createElement(window.FridayExperience,{enabled:true,desktopVisible:true,projects:[],workspaces:[],apiFetch:api})));
    });
    await board(page).getByRole('button',{name:'Add card',exact:true}).click();
    await board(page).getByLabel('Title',{exact:true}).fill('Keep this uncertain draft');
    await page.evaluate(()=>{window.readMode='offline';});
    await board(page).getByRole('button',{name:'Save card',exact:true}).click();
    await expect(board(page).getByRole('button',{name:'Saving…',exact:true})).toBeVisible();
    await page.evaluate(()=>{window.latePost=window.finishPost;window.homeDeadline();});
    await expect(board(page)).toContainText('The last change may have saved.');
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeDisabled();
    assert.equal(await page.evaluate(()=>window.postSignal.aborted),true,'The deadline aborts the transport');
    await board(page).locator('form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    assert.equal(await page.evaluate(()=>window.posts),1,'An uncertain mutation cannot be blindly retried');
    await page.evaluate(()=>{window.readMode='malformed';window.dispatchEvent(new Event('friday:home-refresh'));});
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeDisabled();
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-view',{detail:{view:'activity'}})));
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-view',{detail:{view:'day'}})));
    await expect(board(page).getByLabel('Title',{exact:true})).toHaveValue('Keep this uncertain draft');
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeDisabled();
    await page.evaluate(()=>{window.readMode='ok';window.fixtureBoard.revision=2;window.dispatchEvent(new Event('friday:home-refresh'));});
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeEnabled();
    await page.evaluate(()=>window.latePost({...window.fixtureBoard,revision:1}));
    await expect(board(page).getByLabel('Title',{exact:true})).toHaveValue('Keep this uncertain draft');
    await expect(board(page)).not.toContainText('Card saved.');
    await board(page).getByRole('button',{name:'Save card',exact:true}).click();
    await expect(board(page).getByRole('button',{name:'Saving…',exact:true})).toBeVisible();
    await page.evaluate(()=>{
      window.privatePost=window.finishPost;window.readMode='offline';
      window.__fridayOffRecord=true;window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on:true}}));
      window.__fridayOffRecord=false;window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on:false}}));
      window.privatePost(window.fixtureBoard);
    });
    await expect(board(page).getByLabel('Title',{exact:true})).toHaveValue('Keep this uncertain draft');
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeDisabled();
    await expect(board(page)).not.toContainText('Card saved.');
    await page.evaluate(()=>{window.readMode='ok';window.dispatchEvent(new Event('friday:home-refresh'));});
    await expect(board(page).getByRole('button',{name:'Review draft for this session',exact:true})).toBeEnabled();
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeDisabled();
    await board(page).getByRole('button',{name:'Review draft for this session',exact:true}).click();
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeEnabled();
  } finally {await page.close();}
}
(async()=>{
  assert.ok(os.freemem()>6*1073741824,'At least 6 GB free is required');
  assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname) && new URL(base).port!=='3000','Use the isolated preview, never live Friday');
  if(output){const rel=path.relative(root,path.resolve(output));assert.ok(rel.startsWith('..'+path.sep)||path.isAbsolute(rel),'Proof output must be outside the repository');fs.mkdirSync(output,{recursive:true});}
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1600,height:1000},reducedMotion:'reduce'}), errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    let cards=[note('alpha','Review your notes'),note('beta','Plan the afternoon')], hidden=[], revision=1;
    let failRead=403, failWrite=false, conflictWrite=false, malformed=null, holdRead=false, releaseRead, reads=0;
    const operations=[];
    const sources=[{kind:'task',label:'Local tasks',status:'local_only',detail:'Uses the latest task state stored on this device.',checked_at:stamp(),options:[{id:'task-one',label:'The local task',available:true}]},{kind:'calendar',label:'Calendar',status:'missing',detail:'No local calendar snapshot is available.',checked_at:stamp(),options:[]},{kind:'routine',label:'Routines',status:'local_only',detail:'Local routine availability.',checked_at:stamp(),options:[{id:'not-implemented',label:'Unavailable routine',available:false,status:'unimplemented'}]}];
    const snapshot=()=>({status:'ok',revision,generated_at:stamp(),cards:clone(cards),hidden_cards:clone(hidden),sources:clone(sources),summary:{visible:cards.length,retained:cards.length+hidden.length,retained_limit:256,suggestion_capacity_full:cards.length+hidden.length>=256}});
    await page.route('**/api/desktop/board**',async route=>{
      let status=200,data;
      if(route.request().method()==='GET'){
        reads++; data=failRead?{status:'error',message:failRead===403?'Permission to read Home is required.':'Home is temporarily offline.'}:malformed||snapshot();status=failRead||200;
        if(holdRead){holdRead=false;await new Promise(resolve=>{releaseRead=resolve;});}
      }else{
        const request=route.request().postDataJSON();operations.push(request);
        if(failWrite){status=503;data={status:'error',message:'The change was not confirmed.'};}
        else if(conflictWrite){conflictWrite=false;revision++;status=409;data={status:'error',message:'Home changed while you were working. Your draft is kept.'};}
        else if(request.expected_revision!==revision){status=409;data={status:'error',message:'Home changed while you were working. Your draft is kept.'};}
        else{
          const all=cards.concat(hidden), row=all.find(item=>item.id===request.id);
          if(request.op==='save'){const index=cards.findIndex(item=>item.id===request.card.id);const saved={...note(request.card.id,request.card.title),...request.card};if(index<0)cards.push(saved);else cards[index]={...cards[index],...saved};}
          else if(request.op==='pin')row.pinned=request.pinned;
          else if(request.op==='reorder'){assert.deepEqual(request.ids.slice().sort(),cards.map(item=>item.id).sort(),'Reorder carries the complete visible set');cards=request.ids.map(id=>cards.find(item=>item.id===id));}
          else if(request.op==='dismiss'||request.op==='snooze'){cards=cards.filter(item=>item.id!==row.id);hidden=hidden.filter(item=>item.id!==row.id);hidden.push({...row,dismissed:request.op==='dismiss',snoozed_until:request.op==='snooze'?request.until:null});}
          else if(request.op==='restore'){hidden=hidden.filter(item=>item.id!==row.id);cards.push({...row,dismissed:false,snoozed_until:null});}
          else if(request.op==='track'){const tracked=followed();cards=cards.filter(item=>item.id!==tracked.id);hidden=hidden.filter(item=>item.id!==tracked.id);cards.push(tracked);}
          else if(request.op==='stop_tracking')row.tracking={...row.tracking,enabled:false,stopped_at:stamp()};
          else if(request.op==='remove'){assert.ok(row.origin==='saved'&&!row.tracking||row.tracking&&!row.tracking.enabled,'Only saved cards or stopped snapshots can be deleted');cards=cards.filter(item=>item.id!==row.id);hidden=hidden.filter(item=>item.id!==row.id);}
          else if(request.op==='reset_suggestions'){cards=cards.filter(item=>item.origin!=='automatic'||item.pinned);hidden=hidden.filter(item=>item.origin!=='automatic'||item.pinned);}
          else throw new Error('Unexpected mutation '+request.op);
          revision++;data=snapshot();
        }
      }
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto(base,{waitUntil:'domcontentloaded'});
    await expect(board(page).getByRole('alert')).toContainText('Permission');
    await expect(board(page).getByRole('button',{name:'Add card',exact:true})).toBeDisabled();
    failRead=0;await board(page).getByRole('button',{name:'Refresh cards',exact:true}).click();
    await expect(card(page,'alpha')).toBeVisible();
    await proof(page,'working-board-wide');

    holdRead=true;await refresh(page);await expect.poll(()=>typeof releaseRead).toBe('function');
    const before=reads;cards[0]={...cards[0],body:'Updated while an older read was pending.'};revision++;
    await refresh(page);releaseRead();
    await expect(card(page,'alpha').locator('.fx-board-card-body')).toHaveText(cards[0].body);
    assert.ok(reads>before,'A notification during a read queues a fresh read');

    // A stream refresh cannot move an open menu's card. Its action still uses
    // the latest confirmed revision; a still-newer writer can refuse it.
    await options(page,'alpha');
    const menuOrder=await board(page).locator('[data-card-id]').evaluateAll(nodes=>nodes.map(node=>node.dataset.cardId));
    cards.reverse();revision++;const menuRevision=revision;await refresh(page);
    await expect(board(page).getByRole('button',{name:'Updates ready · Apply',exact:true})).toBeVisible();
    assert.deepEqual(await board(page).locator('[data-card-id]').evaluateAll(nodes=>nodes.map(node=>node.dataset.cardId)),menuOrder);
    conflictWrite=true;await card(page,'alpha').getByRole('button',{name:'Pin Review your notes',exact:true}).click();
    await expect(board(page).getByRole('alert')).toContainText('Home changed');
    assert.equal(operations.at(-1).expected_revision,menuRevision,'Held display uses the latest confirmed revision');
    await card(page,'alpha').locator('summary').click();
    await board(page).getByRole('button',{name:'Updates ready · Apply',exact:true}).click();
    await board(page).getByRole('button',{name:'Dismiss message',exact:true}).click();
    await expect(board(page).locator('[data-card-id]').first()).toHaveAttribute('data-card-id','beta');

    // Exercise the native hand controller's real locked element. The seen
    // flag is synthetic; this does not claim a camera/physical proof.
    await board(page).getByRole('button',{name:'Refresh',exact:true}).focus();
    const handLocked=await page.evaluate(()=>{
      const button=document.querySelector('[data-card-id="beta"] button'),r=button.getBoundingClientRect();
      window.FridayTracking.hand.seen=true;
      window.FridayHandCursor.frame({x:r.x+r.width/2,y:r.y+r.height/2,visible:true,pinching:false,t:performance.now()});
      return !!window.FridayHandCursor.locked?.el && document.querySelector('.fx-working-board').contains(window.FridayHandCursor.locked.el);
    });
    assert.equal(handLocked,true,'The native hand cursor actually locked inside Home');
    cards.reverse();revision++;await refresh(page);
    await expect(board(page).getByRole('button',{name:'Updates ready · Apply',exact:true})).toBeVisible();
    await expect(board(page).locator('[data-card-id]').first()).toHaveAttribute('data-card-id','beta');
    await page.evaluate(()=>{window.FridayHandCursor.frame({visible:false,x:0,y:0,pinching:false,t:performance.now()});window.FridayTracking.hand.seen=false;});
    await expect(board(page).locator('[data-card-id]').first()).toHaveAttribute('data-card-id','alpha');

    const beforeDrag=await card(page,'alpha').boundingBox();
    await card(page,'alpha').getByRole('button',{name:'Move Review your notes',exact:true}).evaluate(button=>button.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:new DataTransfer()})));
    cards.reverse();revision++;await refresh(page);
    await expect(board(page).getByRole('button',{name:'Updates ready · Apply',exact:true})).toBeVisible();
    const duringDrag=await card(page,'alpha').boundingBox();
    assert.equal(duringDrag.x,beforeDrag.x);assert.equal(duringDrag.y,beforeDrag.y,'The update affordance does not move the drag target');
    await card(page,'alpha').getByRole('button',{name:'Move Review your notes',exact:true}).evaluate(button=>button.dispatchEvent(new DragEvent('dragend',{bubbles:true})));
    await expect(board(page).locator('[data-card-id]').first()).toHaveAttribute('data-card-id','beta');
    cards.sort((a,b)=>a.id.localeCompare(b.id));revision++;await refresh(page);
    await expect(board(page).locator('[data-card-id]').first()).toHaveAttribute('data-card-id','alpha');

    await card(page,'alpha').getByRole('button',{name:'Read card: Review your notes',exact:true}).click();
    await expect(board(page).getByRole('heading',{name:'Review your notes',exact:true})).toBeFocused();
    const oldBody=cards[0].body;
    cards[0]={...cards[0],body:'The reader chooses when to replace this text.'};revision++;await refresh(page);
    await expect(board(page).getByRole('button',{name:'Read latest version',exact:true})).toBeVisible();
    await expect(board(page).locator('.fx-board-reader-body')).toHaveText(oldBody);
    await board(page).getByRole('button',{name:'Read latest version',exact:true}).click();
    await expect(board(page).locator('.fx-board-reader-body')).toHaveText(cards[0].body);
    await board(page).getByRole('button',{name:'Back to cards',exact:true}).click();

    const alphaMenu=await options(page,'alpha');
    await alphaMenu.getByRole('button',{name:'Edit card',exact:true}).click();
    await board(page).getByLabel('Details',{exact:true}).fill('My unfinished edit stays here.');
    cards[0]={...cards[0],body:'Someone else updated this card.'};revision++;await refresh(page);
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeDisabled();
    await expect(board(page).getByLabel('Details',{exact:true})).toHaveValue('My unfinished edit stays here.');
    await board(page).getByRole('button',{name:'Cancel',exact:true}).click();
    await expect(board(page).getByRole('alert')).toContainText('Discard this unsaved card draft?');
    await board(page).getByRole('button',{name:'Keep editing',exact:true}).click();
    await expect(board(page).getByLabel('Details',{exact:true})).toHaveValue('My unfinished edit stays here.');
    await board(page).getByLabel('Details',{exact:true}).press('Escape');
    await board(page).getByRole('button',{name:'Discard draft',exact:true}).click();
    await board(page).getByRole('button',{name:'Add card',exact:true}).click();
    const literal='<img src=x onerror=alert(1)>';
    await board(page).getByLabel('Title',{exact:true}).fill(literal);
    await board(page).getByLabel('Details',{exact:true}).fill(('Long plain text stays readable.\n').repeat(55));
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-view',{detail:{view:'activity'}})));
    await expect(board(page)).toHaveCount(0);
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-view',{detail:{view:'day'}})));
    await expect(board(page).getByLabel('Title',{exact:true})).toHaveValue(literal);
    const valid=snapshot();
    for(const invalid of [{...valid,cards:[null]},{...valid,cards:[{...valid.cards[0],title:{bad:true}}]},{...valid,cards:[{...valid.cards[0],actions:[null]}]},{...valid,sources:[{...sources[0],options:[null]}]},{...valid,revision:-1},{...valid,cards:Array.from({length:537},(_,i)=>note('oversized-'+i,'Too many records')),hidden_cards:[]}]){
      malformed=invalid;await refresh(page);
      await expect(board(page).getByRole('alert')).toContainText('incomplete board');
      await expect(board(page).getByLabel('Title',{exact:true})).toHaveValue(literal);
      malformed=null;await board(page).getByRole('button',{name:'Refresh',exact:true}).click();
      await expect(board(page).getByRole('alert')).toHaveCount(0);
    }
    await page.evaluate(()=>{window.__fridayOffRecord=true;window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on:true}}));});
    await board(page).getByLabel('Details',{exact:true}).fill('A draft edited while private.');
    await page.evaluate(()=>{window.__fridayOffRecord=false;window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on:false}}));});
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeDisabled();
    await expect(board(page).getByLabel('Details',{exact:true})).toHaveValue('A draft edited while private.');
    await board(page).getByRole('button',{name:'Review draft for this session',exact:true}).click();
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeEnabled();
    await board(page).getByLabel('Details',{exact:true}).fill(('Long plain text stays readable.\n').repeat(55));
    failWrite=true;await board(page).getByRole('button',{name:'Save card',exact:true}).click();
    await expect(board(page).getByRole('alert')).toContainText('not confirmed');
    await expect(board(page).getByLabel('Title',{exact:true})).toHaveValue(literal);
    failWrite=false;conflictWrite=true;await board(page).getByRole('button',{name:'Save card',exact:true}).click();
    await expect(board(page).getByRole('alert')).toContainText('Home changed');
    await expect(board(page).getByLabel('Title',{exact:true})).toHaveValue(literal);
    // The failed request refreshes the board; explicitly retry the retained draft.
    const refreshed=page.waitForResponse(response=>response.url().includes('/api/desktop/board')&&response.request().method()==='GET'&&response.status()===200);
    await board(page).getByRole('button',{name:'Refresh',exact:true}).click();
    await refreshed;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await expect(board(page).getByRole('button',{name:'Save card',exact:true})).toBeEnabled();
    await board(page).getByRole('button',{name:'Save card',exact:true}).click();
    await expect.poll(()=>cards.some(item=>item.title===literal)).toBe(true);
    const literalId=cards.find(item=>item.title===literal).id;
    await findCard(page,literalId);assert.equal(await board(page).locator('img').count(),0,'Cards render text without HTML');

    await findCard(page,'alpha');await card(page,'alpha').getByRole('button',{name:'Pin Review your notes',exact:true}).click();
    await expect(card(page,'alpha')).toHaveAttribute('data-pinned','true');
    await card(page,'alpha').getByRole('button',{name:'Unpin Review your notes',exact:true}).click();
    await expect(card(page,'alpha')).toHaveAttribute('data-pinned','false');
    await findCard(page,'beta');await card(page,'beta').getByRole('button',{name:'Move Plan the afternoon',exact:true}).press('Alt+ArrowUp');
    await expect.poll(()=>operations.filter(item=>item.op==='reorder').length).toBe(1);
    assert.equal(cards[0].id,'beta','Keyboard reorder changes the board');
    await findCard(page,'alpha');await card(page,'alpha').getByRole('button',{name:'Move Review your notes',exact:true}).dragTo(card(page,'beta'));
    await expect.poll(()=>operations.filter(item=>item.op==='reorder').length).toBe(2);
    const beforeFailedOrder=cards.map(item=>item.id);
    failWrite=true;await card(page,'alpha').getByRole('button',{name:'Move Review your notes',exact:true}).press('Alt+ArrowDown');
    await expect(board(page).getByRole('alert')).toContainText('not confirmed');
    assert.deepEqual(cards.map(item=>item.id),beforeFailedOrder,'Failed reorder keeps the canonical card order');
    failWrite=false;await board(page).getByRole('button',{name:'Dismiss message',exact:true}).click();

    await findCard(page,'alpha');await (await options(page,'alpha')).getByRole('button',{name:'Snooze for 1 hour',exact:true}).click();
    await expect(board(page).getByRole('button',{name:'Later · 1',exact:true})).toBeVisible();
    await board(page).getByRole('button',{name:'Later · 1',exact:true}).click();
    await expect(card(page,'alpha')).toBeVisible();
    await card(page,'alpha').getByRole('button',{name:'Show again',exact:true}).click();
    await board(page).getByRole('button',{name:/^Today ·/}).click();await findCard(page,'alpha');
    await (await options(page,'alpha')).getByRole('button',{name:'Dismiss card',exact:true}).click();
    await expect(board(page).getByRole('button',{name:'Hidden · 1',exact:true})).toBeVisible();
    await board(page).getByRole('button',{name:'Undo dismiss',exact:true}).click();
    await expect.poll(()=>hidden.length).toBe(0);

    await board(page).getByRole('button',{name:'Add card',exact:true}).click();
    await board(page).getByRole('button',{name:'Track something',exact:true}).click();
    await board(page).getByLabel('Source',{exact:true}).selectOption('routine');
    await expect(board(page).getByRole('option',{name:'Unavailable routine (not available)',exact:true})).toBeDisabled();
    await expect(board(page)).toContainText('Items marked not available cannot be followed yet.');
    await board(page).getByLabel('Source',{exact:true}).selectOption('calendar');
    await expect(board(page).getByLabel('What to follow',{exact:true})).toBeDisabled();
    await expect(board(page)).toContainText('No local calendar snapshot');
    await board(page).getByLabel('Source',{exact:true}).selectOption('task');
    await board(page).getByLabel('What to follow',{exact:true}).selectOption('task-one');
    await board(page).getByRole('button',{name:'Start tracking',exact:true}).click();
    await expect.poll(()=>cards.some(item=>item.id==='track-task-one')).toBe(true);
    await findCard(page,'track-task-one');await card(page,'track-task-one').getByRole('button',{name:'Read card: Follow the local task',exact:true}).click();
    const beforeChecked=reads;
    cards.find(item=>item.id==='track-task-one').source.checked_at=stamp()+60;await refresh(page);
    await expect.poll(()=>reads).toBeGreaterThan(beforeChecked);
    await expect(board(page).getByRole('button',{name:'Read latest version',exact:true})).toHaveCount(0);
    await board(page).getByRole('button',{name:'Back to cards',exact:true}).click();
    await findCard(page,'track-task-one');
    await options(page,'track-task-one');
    await expect(card(page,'track-task-one').getByRole('button',{name:'Edit card',exact:true})).toHaveCount(0);
    await findCard(page,'track-task-one');await (await options(page,'track-task-one')).getByRole('button',{name:'Stop tracking',exact:true}).click();
    await expect(card(page,'track-task-one')).toContainText('Tracking stopped');
    assert.equal(cards.find(item=>item.id==='track-task-one').tracking.enabled,false);
    await (await options(page,'track-task-one')).getByRole('button',{name:'Dismiss card',exact:true}).click();
    await board(page).getByRole('button',{name:'Hidden · 1',exact:true}).click();
    await card(page,'track-task-one').getByRole('button',{name:'Show again',exact:true}).click();
    assert.equal(cards.find(item=>item.id==='track-task-one').tracking.enabled,false,'Restore does not resume tracking');
    await board(page).getByRole('button',{name:/^Today ·/}).click();await findCard(page,'track-task-one');
    await (await options(page,'track-task-one')).getByRole('button',{name:'Resume tracking',exact:true}).click();
    await expect.poll(()=>cards.find(item=>item.id==='track-task-one').tracking.enabled).toBe(true);
    await findCard(page,'track-task-one');await card(page,'track-task-one').getByRole('button',{name:'Read card: Follow the local task',exact:true}).click();
    failRead=503;
    await page.evaluate(()=>{window.__fridayOffRecord=true;window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on:true}}));});
    await expect(board(page).locator('.fx-board-reader')).toHaveCount(0);
    await expect(board(page).getByRole('button',{name:'Add card',exact:true})).toBeDisabled();
    await expect(board(page)).not.toContainText('The latest confirmed local task state.');
    await expect(board(page)).toContainText('personal activity is hidden');
    failRead=0;
    await page.evaluate(()=>{window.__fridayOffRecord=false;window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on:false}}));});
    await expect(board(page).getByRole('button',{name:'Add card',exact:true})).toBeEnabled();
    await findCard(page,'track-task-one');await card(page,'track-task-one').getByRole('button',{name:'Open activity',exact:true}).click();
    await expect(page.locator('.fx-main')).toHaveAttribute('data-view','activity');
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:desktop-view',{detail:{view:'day'}})));
    await expect(board(page)).toBeVisible();
    await findCard(page,'alpha');await (await options(page,'alpha')).getByRole('button',{name:'Delete saved card',exact:true}).click();
    await expect(card(page,'alpha').getByRole('group',{name:'Confirm card deletion',exact:true})).toContainText('cannot be restored');
    assert.ok(cards.some(item=>item.id==='alpha'),'Choosing delete does not delete until confirmed');
    await card(page,'alpha').getByRole('button',{name:'Delete permanently',exact:true}).click();
    await expect.poll(()=>cards.some(item=>item.id==='alpha')).toBe(false);
    await findCard(page,'track-task-one');await (await options(page,'track-task-one')).getByRole('button',{name:'Stop tracking',exact:true}).click();
    await expect(card(page,'track-task-one')).toContainText('Tracking stopped');
    await (await options(page,'track-task-one')).getByRole('button',{name:'Delete stopped snapshot',exact:true}).click();
    await card(page,'track-task-one').getByRole('button',{name:'Delete permanently',exact:true}).click();
    await expect.poll(()=>cards.some(item=>item.id==='track-task-one')).toBe(false);

    failRead=503;await refresh(page);
    await expect(board(page).getByRole('alert')).toContainText('offline');
    await expect(board(page).locator('.fx-board-card').first()).toBeVisible();
    failRead=0;await board(page).getByRole('button',{name:'Refresh cards',exact:true}).click();
    await expect(board(page).getByRole('alert')).toHaveCount(0);
    await board(page).getByRole('button',{name:'Sources',exact:true}).click();
    await expect(board(page).getByLabel('Home sources',{exact:true})).toContainText('No local calendar snapshot');
    await board(page).getByRole('button',{name:'Reset suggestion choices…',exact:true}).click();
    await expect(board(page)).toContainText('Dismissed automatic suggestions may return. Saved, tracked and pinned cards are kept.');
    await board(page).getByRole('button',{name:'Keep my choices',exact:true}).click();
    assert.equal(operations.filter(item=>item.op==='reset_suggestions').length,0);
    await board(page).getByRole('button',{name:'Reset suggestion choices…',exact:true}).click();
    await board(page).getByRole('button',{name:'Reset automatic suggestions',exact:true}).click();
    await expect.poll(()=>operations.filter(item=>item.op==='reset_suggestions').length).toBe(1);
    await board(page).getByRole('button',{name:'Close sources',exact:true}).click();
    for(const [width,height] of [[1280,720],[768,1024],[390,844],[320,568],[568,320]]){
      await page.setViewportSize({width,height});await page.waitForTimeout(400);
      await proof(page,'working-board-'+width+'x'+height);
      await findCard(page,literalId);await card(page,literalId).getByRole('button',{name:'Read card: '+literal,exact:true}).click();
      await proof(page,'working-board-reader-'+width+'x'+height);
      await board(page).getByRole('button',{name:'Back to cards',exact:true}).click();
    }
    // Separate bounded legacy collections can total 536 retained identities.
    // They must remain readable so Sources can recover unpinned history.
    cards=[note('legacy-saved-0','Keep the saved choice'),{...note('legacy-pin','Keep the pinned choice'),origin:'automatic',pinned:true}];
    hidden=Array.from({length:22},(_,i)=>({...note('legacy-saved-'+(i+1),'Saved choice '+i),dismissed:true,hidden_reason:'dismissed'}))
      .concat(Array.from({length:512},(_,i)=>({...note('legacy-auto-'+i,'Dismissed suggestion '+i),origin:'automatic',dismissed:true,hidden_reason:'dismissed'})));
    revision++;await refresh(page);
    await board(page).getByRole('button',{name:'Sources',exact:true}).click();
    await expect(board(page).getByLabel('Home sources',{exact:true})).toContainText('saved-choice limit');
    await board(page).getByRole('button',{name:'Reset suggestion choices…',exact:true}).click();
    await board(page).getByRole('button',{name:'Reset automatic suggestions',exact:true}).click();
    await expect.poll(()=>cards.length+hidden.length).toBe(24);
    assert.equal(cards.some(item=>item.id==='legacy-pin'&&item.pinned),true,'Recovery keeps pinned automatic cards');
    assert.equal(cards.concat(hidden).filter(item=>item.origin==='saved').length,23,'Recovery keeps saved cards');
    await board(page).getByRole('button',{name:'Close sources',exact:true}).click();
    await board(page).getByRole('button',{name:'Refresh',exact:true}).focus();
    cards=[];hidden=[];revision++;await refresh(page);
    await expect(board(page).getByRole('heading',{name:'A little room for what comes next',exact:true})).toBeVisible();
    assert.deepEqual(errors,[]);
    await draftContract(browser);
    await mutationContract(browser);
    console.log('PASS Home board refresh races, revisions, stable reading/editing, plain text, pin/reorder, snooze/dismiss/restore, explicit tracking, source truth, responsive bounds and draft registration');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
