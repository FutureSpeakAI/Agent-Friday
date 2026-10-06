/* Browser-level hand interactions against a small synthetic document.
 * No Friday server, model, webcam or microphone is opened. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium} = require('@playwright/test');
const root = path.resolve(__dirname,'..');
const baseline = process.env.FRIDAY_BASELINE_REV;
function source(name){
  if(!baseline)return fs.readFileSync(path.join(root,'static',name),'utf8');
  const result=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':static/'+name],{cwd:root,encoding:'utf8',windowsHide:true});
  assert.equal(result.status,0,result.stderr);return result.stdout;
}
(async()=>{
  assert.ok(os.freemem()>6*1073741824,'At least 6 GB free is required');
  const browser=await chromium.launch({channel:'chrome',headless:true});
  let failures=0;
  const cases=[
    ['voice next initializes before any tracking frame',()=>{
      const next=FridayHandCursor.next(1), selected=FridayHandCursor.select();
      return {next:next.ok,selected:selected.ok,clicks:window.clicks};
    },{next:true,selected:true,clicks:1}],
    ['voice previous starts at the last target',()=>{
      return {moved:FridayHandCursor.next(-1).ok,target:FridayHandCursor.locked?.el.id};
    },{moved:true,target:'target'}],
    ['newly covered snap target is released',()=>{
      FridayHandCursor.frame({x:130,y:120,pinching:false,visible:true,t:0});
      const was=FridayHandCursor.locked?.el.id;
      document.querySelector('#cover').hidden=false;
      FridayHandCursor.frame({x:130,y:120,pinching:false,visible:true,t:33});
      return {was,locked:!!FridayHandCursor.locked,selected:FridayHandCursor.select().ok,clicks:window.clicks};
    },{was:'target',locked:false,selected:false,clicks:0}],
    ...['disabled','aria-disabled','inert','covered','removed'].map(kind=>[
      'voice select rejects '+kind+' stale target',new Function(`
        FridayHandCursor.next(1);
        const b=document.querySelector('#target');
        const kind=${JSON.stringify(kind)};
        if(kind==='disabled')b.disabled=true;
        if(kind==='aria-disabled')b.setAttribute('aria-disabled','true');
        if(kind==='inert')document.querySelector('#holder').inert=true;
        if(kind==='covered')document.querySelector('#cover').hidden=false;
        if(kind==='removed')b.remove();
        return {selected:FridayHandCursor.select().ok,clicks:window.clicks};
      `),{selected:false,clicks:0}]),
    ['newly guarded voice target requires hold',()=>{
      FridayHandCursor.next(1);document.querySelector('#target').textContent='Delete item';
      const result=FridayHandCursor.select();return {selected:result.ok,code:result.code,clicks:window.clicks};
    },{selected:false,code:'CURSOR_GUARDED',clicks:0}],
    ['snap off still requires guarded hold',()=>{
      FridayTracking.cfg.snap=false;document.querySelector('#target').textContent='Delete item';
      FridayHandCursor.refresh();
      FridayHandCursor.frame({x:130,y:120,pinching:true,visible:true,t:0});
      FridayHandCursor.frame({x:130,y:120,pinching:false,visible:true,t:100});
      const short=window.clicks;
      FridayHandCursor.frame({x:130,y:120,pinching:true,visible:true,t:200});
      FridayHandCursor.frame({x:130,y:120,pinching:true,visible:true,t:950});
      FridayHandCursor.frame({x:130,y:120,pinching:false,visible:true,t:980});
      return {short,held:window.clicks};
    },{short:0,held:1}],
    ['the frozen onset target survives pinch drift with snap off',()=>{
      FridayTracking.cfg.snap=false;
      const b=document.querySelector('#target');b.style.width='24px';b.style.height='24px';
      FridayHandCursor.refresh();
      FridayHandCursor.frame({x:110,y:110,pinching:true,visible:true,t:0});
      FridayHandCursor.frame({x:130,y:115,pinching:true,visible:true,t:50});
      FridayHandCursor.frame({x:145,y:115,pinching:false,visible:true,t:100});
      return window.clicks;
    },1],
    ['an overlay arriving during a pinch cancels that click',()=>{
      FridayHandCursor.frame({x:130,y:120,pinching:true,visible:true,t:0});
      document.querySelector('#cover').hidden=false;
      FridayHandCursor.frame({x:130,y:120,pinching:false,visible:true,t:100});
      return window.clicks;
    },0],
    ['guard changes during a pinch cannot bypass hold',()=>{
      FridayHandCursor.frame({x:130,y:120,pinching:true,visible:true,t:0});
      document.querySelector('#target').textContent='Delete item';
      FridayHandCursor.frame({x:130,y:120,pinching:false,visible:true,t:100});
      return window.clicks;
    },0],
    ['a pointer handler disabling the target cancels the final click',()=>{
      const button=document.querySelector('#target');
      button.addEventListener('pointerdown',()=>{button.disabled=true});
      FridayHandCursor.next(1);
      return {selected:FridayHandCursor.select().ok,clicks:window.clicks};
    },{selected:false,clicks:0}],
    ['blank content still scrolls by pinch dragging',()=>{
      document.querySelector('#target').remove();
      const sc=document.querySelector('#scroll');sc.hidden=false;sc.scrollTop=100;
      FridayTracking.cfg.snap=false;FridayHandCursor.refresh();
      FridayHandCursor.frame({x:140,y:170,pinching:true,visible:true,t:0});
      FridayHandCursor.frame({x:140,y:120,pinching:true,visible:true,t:100});
      FridayHandCursor.frame({x:140,y:120,pinching:false,visible:true,t:130});
      return {scrolled:sc.scrollTop>100,clicks:window.clicks};
    },{scrolled:true,clicks:0}],
    ['horizontal range drag respects min max step and commits once',()=>{
      const range=mountRange({min:10,max:90,step:5,value:50});
      FridayHandCursor.frame({x:200,y:120,pinching:true,visible:true,t:0});
      FridayHandCursor.frame({x:245,y:120,pinching:true,visible:true,t:100});
      const stepped=range.value;
      FridayHandCursor.frame({x:550,y:120,pinching:true,visible:true,t:140});
      const maximum=range.value;
      FridayHandCursor.frame({x:0,y:120,pinching:true,visible:true,t:180});
      const minimum=range.value;
      FridayHandCursor.frame({x:0,y:120,pinching:false,visible:true,t:210});
      return {stepped,maximum,minimum,inputs:rangeInputs,changes:rangeChanges};
    },{stepped:'70',maximum:'90',minimum:'10',inputs:3,changes:1}],
    ['range drag supports decimal steps',()=>{
      const range=mountRange({min:-.5,max:.5,step:.05,value:0});
      FridayHandCursor.frame({x:200,y:120,pinching:true,visible:true,t:0});
      FridayHandCursor.frame({x:249,y:120,pinching:true,visible:true,t:100});
      FridayHandCursor.frame({x:249,y:120,pinching:false,visible:true,t:130});
      return {value:range.value,changes:rangeChanges};
    },{value:'0.25',changes:1}],
    ['range drag supports RTL direction',()=>{
      const range=mountRange({min:0,max:100,step:1,value:50});range.style.direction='rtl';
      FridayHandCursor.frame({x:200,y:120,pinching:true,visible:true,t:0});
      FridayHandCursor.frame({x:250,y:120,pinching:true,visible:true,t:100});
      FridayHandCursor.frame({x:250,y:120,pinching:false,visible:true,t:130});
      return {value:range.value,changes:rangeChanges};
    },{value:'25',changes:1}],
    ...['disabled','aria-disabled','inert','covered'].map(kind=>[
      'range drag cancels when '+kind,new Function(`
        const range=mountRange({min:0,max:100,step:1,value:50}),kind=${JSON.stringify(kind)};
        FridayHandCursor.frame({x:200,y:120,pinching:true,visible:true,t:0});
        FridayHandCursor.frame({x:250,y:120,pinching:true,visible:true,t:100});
        if(kind==='disabled')range.disabled=true;
        if(kind==='aria-disabled')range.setAttribute('aria-disabled','true');
        if(kind==='inert')document.querySelector('#holder').inert=true;
        if(kind==='covered'){const cover=document.querySelector('#cover');cover.style.width='300px';cover.hidden=false}
        FridayHandCursor.frame({x:290,y:120,pinching:true,visible:true,t:140});
        range.disabled=false;range.removeAttribute('aria-disabled');document.querySelector('#holder').inert=false;document.querySelector('#cover').hidden=true;
        FridayHandCursor.frame({x:290,y:120,pinching:false,visible:true,t:170});
        return {value:range.value,inputs:rangeInputs,changes:rangeChanges};
      `),{value:'75',inputs:1,changes:0}]),
    ['range drag preserves React controlled input state',()=>{
      document.querySelector('#target').remove();
      let renderCount=0;
      function Control(){
        const [value,setValue]=React.useState(.5);renderCount++;
        return React.createElement('input',{id:'range',type:'range',min:0,max:1,step:.05,value,
          style:{position:'absolute',left:100,top:100,width:200,height:40,margin:0},
          onChange:event=>{window.controlledValue=Number(event.target.value);setValue(Number(event.target.value))}});
      }
      ReactDOM.flushSync(()=>ReactDOM.createRoot(document.querySelector('#holder')).render(React.createElement(Control)));
      window.controlledValue=.5;FridayTracking.cfg.snap=false;FridayHandCursor.refresh();
      FridayHandCursor.frame({x:200,y:120,pinching:true,visible:true,t:0});
      ReactDOM.flushSync(()=>FridayHandCursor.frame({x:250,y:120,pinching:true,visible:true,t:100}));
      FridayHandCursor.frame({x:250,y:120,pinching:false,visible:true,t:130});
      return {value:document.querySelector('#range').value,state:controlledValue,renderedAgain:renderCount>1};
    },{value:'0.75',state:.75,renderedAgain:true}],
  ];
  try{
    for(const [name,check,expected] of cases){
      const page=await browser.newPage({viewport:{width:800,height:600}});
      try{
        await page.setContent('<style>body{margin:0}#target{position:absolute;left:100px;top:100px;width:120px;height:40px}#cover{position:fixed;left:90px;top:90px;width:150px;height:70px;z-index:100;background:#222}#hand-cursor,.fr-snap-box{position:fixed;pointer-events:none;z-index:999}#scroll{position:absolute;left:100px;top:100px;width:200px;height:200px;overflow:auto}</style><div id="holder"><button id="target">Open notes</button></div><div id="cover" hidden></div><div id="scroll" hidden><div style="height:1000px">Long content</div></div><div id="hand-cursor"></div>');
        await page.evaluate(()=>{window.clicks=0;window.FridayTracking={cfg:{snap:true}};document.querySelector('#target').addEventListener('click',()=>window.clicks++);});
        if(name.includes('React controlled')){
          await page.addScriptTag({path:path.join(root,'static/vendor/react-18.3.1.production.min.js')});
          await page.addScriptTag({path:path.join(root,'static/vendor/react-dom-18.3.1.production.min.js')});
        }
        await page.addScriptTag({content:source('hand_cursor_core.js')});
        await page.addScriptTag({content:source('hand_cursor.js')});
        await page.evaluate(()=>{window.mountRange=options=>{
          const holder=document.querySelector('#holder');holder.innerHTML='';
          const range=document.createElement('input');range.type='range';range.id='range';
          Object.entries(options).forEach(([key,value])=>{range[key]=String(value)});
          range.style.cssText='position:absolute;left:100px;top:100px;width:200px;height:40px;margin:0';
          window.rangeInputs=0;window.rangeChanges=0;
          range.addEventListener('input',()=>window.rangeInputs++);range.addEventListener('change',()=>window.rangeChanges++);
          holder.appendChild(range);FridayTracking.cfg.snap=false;FridayHandCursor.refresh();return range;
        }});
        assert.deepEqual(await page.evaluate(check),expected);console.log('PASS '+name);
      }catch(error){failures++;console.error('FAIL '+name+': '+error.message);}
      finally{await page.close();}
    }
  }finally{await browser.close();}
  assert.equal(failures,0,failures+' hand interaction regressions');
})().catch(error=>{console.error(error);process.exitCode=1;});
