/* Actual native callbacks and surfaces under controlled DOM-only layout moves.
 * No server, model, camera or microphone is used. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawnSync}=require('node:child_process');
const {chromium,expect}=require('@playwright/test');
const root=path.resolve(__dirname,'..'),baseline=process.env.FRIDAY_BASELINE_REV,output=process.env.FRIDAY_NATIVE_SPATIAL_PROOFS;
function read(file){
  if(!baseline)return fs.readFileSync(path.join(root,file),'utf8');
  const result=spawnSync('git',['-c','safe.directory='+root,'show',baseline+':'+file],{cwd:root,encoding:'utf8',windowsHide:true,maxBuffer:12e6,timeout:15000});
  assert.equal(result.status,0,result.stderr||result.error?.message);return result.stdout;
}
const html=read('index.html');
function region(first,last){const a=html.indexOf(first),b=html.indexOf(last,a+first.length);assert.ok(a>=0&&b>a,'Actual source region: '+first);return html.slice(a,b);}
function arrow(source,marker){
  const first=source.indexOf(marker),start=first+marker.length,brace=source.indexOf('{',start);
  assert.ok(first>=0&&brace>start,'Actual callback is present: '+marker);
  let depth=0,quote=null,line=false,block=false;
  for(let i=brace;i<source.length;i++){
    const c=source[i],n=source[i+1];
    if(line){if(c==='\n')line=false;continue;}if(block){if(c==='*'&&n==='/'){block=false;i++;}continue;}
    if(quote){if(c==='\\')i++;else if(c===quote)quote=null;continue;}
    if(c==='/'&&n==='/'){line=true;i++;continue;}if(c==='/'&&n==='*'){block=true;i++;continue;}
    if(c==='"'||c==="'"||c==='`'){quote=c;continue;}if(c==='{')depth++;else if(c==='}'&&--depth===0)return source.slice(start,i+1);
  }
  throw new Error('Unclosed actual callback: '+marker);
}
const fwin=region('function FWin(','function WorkspaceChat('),conversation=region('function ConversationWindow(','async function fridayAnalyzeFile(');
const callbacks=[['FWin',fwin,'className: "fwin-bar"'],['ConversationWindow',conversation,'className: "chat-win-bar"']].map(([name,source,title])=>({name,
  resize:arrow(source,'const startResize = '),drag:arrow(source.slice(source.indexOf(title)),'onMouseDown: ')}));
const rendered=html.includes('function fridayRenderedBox(')?region('function fridayRenderedBox(','function useFridaySpatialSurface('):'';
const hook=html.includes('function useFridaySpatialSurface(')?region('function useFridaySpatialSurface(','function FWin('):'function useFridaySpatialSurface(){}';
const snap=region('function SnapPreview(','const FRIDAY_SNAP_LAYOUTS =');
const snapBox=region('function fridaySnapBox(','// Where a dragged window would snap:');
const snapArea=region('function fridaySnapArea(','// A slot\'s exact box in the area.');
const desktopArea=region('function fridayDesktopArea(','// One window\'s snap state.');
const studio=region('function WorkspaceChat(','class Safe extends React.Component');
const toast=region('function fridayToast(','window.fridayToast = fridayToast;');
const css=['static/friday_shared_surfaces.css','static/friday_native_shell.css','static/friday_holographic_workspace.css'].map(read).join('\n');
// Keep the same real geometry controller for old/new integration callbacks.
const geometry=fs.readFileSync(path.join(root,'static/friday_holographic_workspace.js'),'utf8');
const pause=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
function near(actual,expected,label){assert.ok(Math.abs(actual-expected)<1,label+': '+actual+' versus '+expected);}
(async()=>{
  assert.ok(os.freemem()>Math.max(4,Number(process.env.FRIDAY_CHECK_MIN_GIB)||6)*1073741824,'Not enough free memory for mounted integration checks');
  if(output){const relative=path.relative(root,path.resolve(output));assert.ok(relative.startsWith('..'+path.sep)||path.isAbsolute(relative),'Proofs stay outside the repository');fs.mkdirSync(output,{recursive:true});}
  const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
  assert.ok(typeof executablePath==='string'&&path.isAbsolute(executablePath)&&fs.statSync(executablePath).isFile(),'Select an explicit installed dedicated test browser');
  const results=[],browser=await chromium.launch({executablePath,headless:true});
  console.log(JSON.stringify({browser:{executablePath,version:browser.version()}}));
  async function run(name,test,{viewport={width:1200,height:900},reducedMotion='no-preference'}={}){
    const page=await browser.newPage({viewport,reducedMotion}),errors=[];page.on('pageerror',error=>errors.push(error.message));
    try{
      await page.route('**/*',route=>route.abort());
      await page.setContent(`<!doctype html><style>${css}</style><style>
        :root{--fr-surface:#101725;--fr-text:#edf4fc;--fr-label:#b1bfd1;--fr-cyan:#00d4ff;--fr-glass-edge:#334052;--fr-font-body:Arial,sans-serif;--fr-font-display:Arial,sans-serif}
        body{margin:0;background:#080e16;color:#ddd;font:14px Arial,sans-serif}.top-bar{position:fixed;inset:0 0 auto;height:52px}.dock{display:none}
        .snap-preview{position:fixed;box-sizing:border-box;pointer-events:none}.friday-avatar-caption{font:12px Arial,sans-serif}
        #moved{position:fixed;box-sizing:border-box;background:#193044}#moved .title{height:35px}#resize{position:absolute;bottom:0;right:0;width:30px;height:30px}
      </style><body class="friday-experience-enabled friday-experience-desktop"><div class="top-bar"></div><div class="dock"></div><div id="ui-root"></div><div id="state-indicator">READY</div><div id="presence-status">Synthetic fixture</div></body>`);
      await page.addScriptTag({path:path.join(root,'static/vendor/react-18.3.1.production.min.js')});
      await page.addScriptTag({path:path.join(root,'static/vendor/react-dom-18.3.1.production.min.js')});
      await page.evaluate(()=>{
        window.FridayDisplayStyle={get:()=> 'simple'};window.__fridayOffRecord=false;
      });
      await page.addScriptTag({content:snapArea+'\n'+desktopArea});
      await page.addScriptTag({content:geometry});await page.evaluate(()=>dispatchEvent(new Event('resize')));
      await page.addScriptTag({content:`const {useRef,useState,useEffect,useCallback,useContext}=React;const fridayName=()=> 'Friday',fridayTM=value=>value;
        ${rendered}\n${hook}\n${toast}
        const fixtureRoot=ReactDOM.createRoot(document.getElementById('ui-root'));`});
      await test(page);assert.deepEqual(errors,[],'Mounted actual callbacks/surfaces must not throw');results.push({name,status:'passed'});
    }catch(error){results.push({name,status:'failed',message:error.message});}
    finally{await page.close();}
  }
  try{
    for(const callback of callbacks)for(const operation of ['resize','free drag','snapped drag'])await run(callback.name+' '+operation+' starts from displayed geometry',async page=>{
      await page.addScriptTag({content:`
        function Fixture(){
          const fwinRef=useRef(null),surfaceRef=fwinRef,rStart=useRef({}),dOff=useRef({});
          const box={x:20,y:30,w:900,h:700},shownBox=box,size={w:660,h:460},shownW=660;
          const snap={slot:${operation==='snapped drag'?'"full"':'null'},box:${operation==='snapped drag'?'box':'null'}};
          let dragging=false,resizing=null,focused=0;const setDragging=value=>{dragging=value},setResizing=value=>{resizing=value},onFocus=()=>{focused++};
          const startResize=${callback.resize};const dragStart=${callback.drag};
          window.interactionSnapshot=()=>({rStart:rStart.current,dOff:dOff.current,dragging,resizing,focused,size,box});
          return React.createElement('div',{id:'moved',ref:fwinRef,style:{left:box.x,top:box.y,width:box.w,height:box.h}},
            React.createElement('div',{className:'title',onMouseDown:dragStart},'Actual handler title'),React.createElement('button',{id:'resize',onMouseDown:e=>startResize(e,'se')},'↘'));
        }fixtureRoot.render(React.createElement(Fixture));
      `});await expect(page.locator('#moved')).toBeVisible();
      // Simulate a displayed layout frame without a React render. The handler's box stays stale.
      await page.evaluate(()=>Object.assign(document.getElementById('moved').style,{left:'350px',top:'160px',width:'400px',height:'300px'}));
      if(operation==='resize')await page.locator('#resize').click();
      else await page.locator('#moved .title').click({position:{x:80,y:15}});
      const result=await page.evaluate(()=>interactionSnapshot());
      if(operation==='resize'){
        for(const [key,value]of Object.entries({x:350,y:160,w:400,h:300}))near(result.rStart[key],value,'resize '+key);
        assert.equal(result.resizing,'se');
      }else{near(result.dOff.x,operation==='snapped drag'?132:80,'drag offset x');near(result.dOff.y,15,'drag offset y');assert.equal(result.dragging,true);}
      assert.deepEqual(result.size,{w:660,h:460},'Starting interaction must preserve restored size');assert.deepEqual(result.box,{x:20,y:30,w:900,h:700},'Do not rewrite the saved request rectangle');
    });
    await run('actual SnapPreview follows forced layout topology without React rerender',async page=>{
      await page.addScriptTag({content:`const FRIDAY_SNAP_SLOTS={full:[0,1]},FRIDAY_SNAP_NAMES={full:'Full'};${snapBox}\n${snap}
        const stale=fridayDesktopArea();fixtureRoot.render(React.createElement(SnapPreview,{box:stale,slot:'full'}));`});
      await expect(page.getByTestId('snap-preview')).toBeVisible();
      const frame=await page.evaluate(()=>{
        document.documentElement.style.setProperty('--fr-chat-dock','320px');fridayDesktopArea();
        const r=document.querySelector('.snap-preview').getBoundingClientRect(),layout=FridayHolographicWorkspace.layoutRect;
        return {r:{x:r.x,y:r.y,w:r.width,h:r.height},layout};
      });
      const c=frame.layout.content,s=frame.layout.stageFrame||frame.layout.stage,r=frame.r;
      assert.ok(r.x>=c.x-.5&&r.y>=c.y-.5&&r.x+r.w<=c.x+c.w+.5&&r.y+r.h<=c.y+c.h+.5,'Snap preview belongs to the current content frame');
      assert.ok(r.x+r.w<=s.x+.5||s.x+s.w<=r.x+.5||r.y+r.h<=s.y+.5||s.y+s.h<=r.y+.5,'Preview never covers the relocated stage');
    });
    for(const viewport of [{width:568,height:320},{width:320,height:320}])await run('short Salon confirmation and composer remain reachable at '+viewport.width+'x'+viewport.height,async page=>{
      await page.addScriptTag({content:`const WsCustomCtx=React.createContext({setCust:()=>{}}),FridayDoc=({text})=>React.createElement('span',null,text);
        const useFridayToast=()=>useCallback(fridayToast,[]),fridayEnterSends=()=>false;
        window.fetch=async(url,options)=>{if(options?.method)throw new Error('No mutation permitted in scroll fixture');return{ok:true,json:async()=>({status:'ok',chat:[],customization:{},versions:[]})}};
        ${studio}
        fixtureRoot.render(React.createElement(WorkspaceChat,{wsId:'library',label:'Synthetic Library',onClose:()=>{}}));`});
      const dialog=page.getByRole('dialog',{name:'Synthetic Library customization',exact:true}),input=dialog.getByRole('textbox');
      await expect(dialog).toBeVisible();await input.fill('Unsent synthetic draft');await dialog.getByRole('button',{name:'Close',exact:true}).click();
      const bounds=await dialog.evaluate(node=>({overflow:getComputedStyle(node).overflowY,client:node.clientHeight,scroll:node.scrollHeight,log:node.querySelector('.friday-studio-log').getBoundingClientRect().height}));
      assert.ok(['auto','scroll'].includes(bounds.overflow),'Inline hidden must not prevent outer scrolling');assert.ok(bounds.scroll>bounds.client,'Confirmation produces a real bounded scroll range');assert.ok(bounds.log>=79,'Transcript retains a usable scroll region');
      await dialog.getByRole('button',{name:'Keep editing',exact:true}).click();await input.scrollIntoViewIfNeeded();
      assert.equal(await input.evaluate(node=>{const r=node.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===node;}),true,'Composer is reachable by scrolling the protected surface');
      if(output)await page.screenshot({path:path.join(output,'salon-scroll-'+viewport.width+'x'+viewport.height+'.png')});
    },{viewport});
    for(const reducedMotion of ['reduce','no-preference'])await run('actual notification respects '+reducedMotion,async page=>{
      await page.evaluate(()=>fridayToast('Synthetic notification'));await pause(page);
      const node=page.locator('#friday-toast-host > [role="status"]');await expect(node).toBeVisible();
      const result=await node.evaluate(element=>({transform:getComputedStyle(element).transform,duration:getComputedStyle(element).transitionDuration}));
      if(reducedMotion==='reduce'){assert.equal(result.transform,'none');assert.ok(result.duration.split(',').every(value=>parseFloat(value)===0));}
      else assert.ok(result.duration.split(',').some(value=>parseFloat(value)>0),'Normal notification keeps its existing gentle transition');
      await node.getByRole('button',{name:'Dismiss notification',exact:true}).click();await expect(node).toHaveCount(0);
    },{reducedMotion});
  }finally{if(output)fs.writeFileSync(path.join(output,'native-spatial-integration-results.json'),JSON.stringify({baseline:baseline||null,results},null,2));await browser.close();}
  process.stdout.write(JSON.stringify({baseline:baseline||null,results})+'\n');if(results.some(result=>result.status==='failed'))process.exitCode=1;
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
