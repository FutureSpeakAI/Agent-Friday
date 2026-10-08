const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'static/friday_agent_workspaces.js'), 'utf8');
const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
const state = (patch = {}) => ({surface_id:'surface-one',generation:1,page_generation:2,frame_sequence:3,
  actor_id:'crew-example',display_name:'Mira',task_id:'task-one',conversation_id:'chat-one',room_revision:4,
  project_id:'project-one',mode:'agent',viewport:{width:1000,height:600},cursor:{x:100,y:100,sequence:1},...patch});
const response = data => ({ok:true,status:200,json:async()=>data});
const flush = () => new Promise(resolve=>setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done=>{resolve=done;}); return {promise,resolve}; }
function find(tree, predicate) {
  if (!tree || typeof tree !== 'object') return null;
  if (predicate(tree)) return tree;
  for (const child of tree.children || []) {
    for (const item of Array.isArray(child)?child:[child]) { const found = find(item,predicate); if(found)return found; }
  }
  return null;
}
function environment() {
  const stores = new Map(), cleanups = new Map(), effects = [], timers = new Map();
  const listeners = new Map(); let component, cursor = 0, timerId = 0;
  const document = {hidden:false,addEventListener(){},removeEventListener(){}};
  const window = {crypto,document,addEventListener(name,fn){listeners.set(name,fn);},removeEventListener(name){listeners.delete(name);},
    dispatchEvent(event){listeners.get(event.type)?.(event);},setTimeout(fn){const id=++timerId;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);}};
  function slot(initial) { const state=stores.get(component), index=cursor++; if(!(index in state))state[index]=typeof initial==='function'?initial():initial; return [state,index]; }
  window.React = {createElement:(type,props,...children)=>({type,props:props||{},children}),Fragment:'fragment',
    useState(initial){const [state,index]=slot(initial);return[state[index],value=>{state[index]=typeof value==='function'?value(state[index]):value;}];},
    useRef(initial){const [state,index]=slot(()=>({current:initial}));return state[index];},
    useEffect(fn,deps){const [state,index]=slot(undefined);const prior=state[index];
      if(!prior||!deps||deps.some((value,i)=>!Object.is(value,prior[i]))){const key=component+':'+index;effects.push(()=>{cleanups.get(key)?.();cleanups.set(key,fn());});state[index]=deps;}}
  };
  vm.runInNewContext(source,{window,document,AbortController,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},JSON,Map,Set,Number,Math,Error});
  function render(fn,props,key=fn.name){component=key;cursor=0;if(!stores.has(key))stores.set(key,[]);return fn(props);}
  function commit(){while(effects.length)effects.shift()();}
  function unmount(){for(const cleanup of cleanups.values())cleanup?.();cleanups.clear();}
  return {window,document,timers,commit,unmount,render,
    workspaces:props=>render(window.FridayAgentWorkspaces,props),
    surface:node=>render(node.type,node.props,node.props.initial.surface_id)};
}
const button = (tree,label) => find(tree,node=>node.type==='button'&&node.children.includes(label));
function loadImage(tree) {
  const image=find(tree,node=>node.type==='img');assert.ok(image);
  const events=new Map();
  image.props.ref.current={getBoundingClientRect:()=>({left:10,top:20,right:510,bottom:320,width:500,height:300}),
    addEventListener(name,fn,options){events.set(name,{fn,options});},removeEventListener(name){events.delete(name);}};
  image.props.onLoad();return {image,events};
}
async function mountedSurface(apiFetch, initial=state()) {
  const env=environment(),props={apiFetch,active:true};
  env.workspaces(props);env.commit();await flush();
  const node=find(env.workspaces(props),item=>typeof item.type==='function'&&item.props.initial);
  assert.ok(node);node.props.initial=initial;
  env.surface(node);env.commit();
  return {env,node,tree:()=>env.surface(node)};
}
test('hidden workspaces do not fetch and changed chat ignores old list replies',async()=>{
  const env=environment(),old=deferred(),requests=[];
  const apiFetch=async url=>{requests.push(url);return url.includes('chat-one')?old.promise:response({workspaces:[]});};
  env.workspaces({apiFetch,active:false});env.commit();assert.equal(requests.length,0);
  env.workspaces({apiFetch,active:true,conversationId:'chat-one'});env.commit();
  env.workspaces({apiFetch,active:true,conversationId:'chat-two'});env.commit();await flush();
  old.resolve(response({workspaces:[state()]}));await flush();
  const tree=env.workspaces({apiFetch,active:true,conversationId:'chat-two'});
  assert.equal(find(tree,item=>item.props.initial?.surface_id==='surface-one'),null);
  env.unmount();
});
test('duplicate surface ownership fails closed before rendering a browser',async()=>{
  const env=environment(),apiFetch=async()=>response({workspaces:[state(),state()]});
  env.workspaces({apiFetch});env.commit();await flush();
  const tree=env.workspaces({apiFetch});assert.ok(find(tree,node=>node.props.role==='alert'));
  assert.equal(find(tree,node=>node.props.initial),null);env.unmount();
});
test('manual click uses the displayed frame coordinates and authority generations',async()=>{
  const calls=[],initial=state({mode:'human'});
  const apiFetch=async(url,options)=>{
    calls.push({url,options});
    if(url.includes('/frame?'))return response({frame:{...initial,image:png}});
    if(url.endsWith('/input'))return response({workspace:initial});
    return response({workspaces:[initial]});
  };
  const {env,node,tree}=await mountedSurface(apiFetch,initial);
  button(tree(),'Watch work').props.onClick();tree();env.commit();await flush();
  const {image}=loadImage(tree());
  image.props.onClick({clientX:260,clientY:170});await flush();
  const call=calls.find(item=>item.url.endsWith('/input'));assert.ok(call);
  assert.deepEqual(JSON.parse(call.options.body),{generation:1,event:{type:'click',x:500,y:300,page_generation:2,frame_sequence:3}});
  env.unmount();
});
test('manual input waits for the exact image load and refuses stale image loads',async()=>{
  const calls=[],initial=state({mode:'human'}),later=state({mode:'human',frame_sequence:4});let count=0;
  const apiFetch=async(url,options)=>{
    calls.push({url,options});
    if(url.includes('/frame?'))return response({frame:{...(count++?later:initial),image:png}});
    if(url.endsWith('/input'))return response({workspace:later});
    return response({workspaces:[initial]});
  };
  const {env,tree}=await mountedSurface(apiFetch,initial);
  button(tree(),'Watch work').props.onClick();tree();env.commit();await flush();
  const oldImage=find(tree(),n=>n.type==='img');assert.ok(oldImage);
  oldImage.props.ref.current={getBoundingClientRect:()=>({left:0,top:0,right:100,bottom:100,width:100,height:100}),addEventListener(){},removeEventListener(){}};
  oldImage.props.onClick({clientX:50,clientY:50});await flush();
  assert.equal(calls.filter(item=>item.url.endsWith('/input')).length,0);
  // Select the last timer: the frame poll follows the outer list poll.
  [...env.timers.values()].at(-1)();await flush();
  const nextImage=find(tree(),n=>n.type==='img');assert.notEqual(nextImage.props.key,oldImage.props.key);
  oldImage.props.onLoad();nextImage.props.onClick({clientX:50,clientY:50});await flush();
  assert.equal(calls.filter(item=>item.url.endsWith('/input')).length,0);
  nextImage.props.onLoad();nextImage.props.onClick({clientX:50,clientY:50});await flush();
  const write=calls.find(item=>item.url.endsWith('/input'));assert.ok(write);
  assert.equal(JSON.parse(write.options.body).event.frame_sequence,4);env.unmount();
});
test('a not-yet-started browser shows waiting without a malformed-frame error',async()=>{
  const apiFetch=async url=>url.includes('/frame?')?response({frame:{...state(),image:null}}):response({workspaces:[state()]});
  const {env,tree}=await mountedSurface(apiFetch);
  button(tree(),'Watch work').props.onClick();tree();env.commit();await flush();
  const result=tree();assert.equal(find(result,node=>node.type==='img'),null);
  assert.equal(find(result,node=>node.props.role==='alert'),null);
  assert.ok(find(result,node=>node.props.role==='status'));env.unmount();
});
test('remote scrolling carries cursor coordinates and suppresses local scroll',async()=>{
  const calls=[],initial=state({mode:'human'});
  const apiFetch=async(url,options)=>{calls.push({url,options});return response(url.includes('/frame?')
    ?{frame:{...initial,image:png}}:url.endsWith('/input')?{workspace:initial}:{workspaces:[initial]});};
  const {env,tree}=await mountedSurface(apiFetch,initial);
  button(tree(),'Watch work').props.onClick();tree();env.commit();await flush();
  const {events}=loadImage(tree());env.commit();
  let prevented=false,stopped=false;const listener=events.get('wheel');assert.ok(listener);
  assert.equal(listener.options.passive,false);
  listener.fn({clientX:260,clientY:170,deltaY:200,preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});
  await flush();const write=calls.find(item=>item.url.endsWith('/input'));assert.ok(write);
  assert.deepEqual(JSON.parse(write.options.body).event,{type:'wheel',x:500,y:300,delta_y:200,page_generation:2,frame_sequence:3});
  assert.equal(prevented,true);assert.equal(stopped,true);env.unmount();
});
test('a paused surface never displays a late frame from the old controller',async()=>{
  const late=deferred();
  const apiFetch=async(url,options)=>{
    if(url.includes('/frame?'))return late.promise;
    if(url.endsWith('/control'))return response({workspace:state({generation:2,mode:'paused'})});
    return response({workspaces:[state()]});
  };
  const {env,tree}=await mountedSurface(apiFetch);
  button(tree(),'Watch work').props.onClick();tree();env.commit();
  await button(tree(),'Pause').props.onClick();
  late.resolve(response({frame:{...state(),image:png}}));await flush();
  assert.equal(find(tree(),node=>node.type==='img'),null);
  assert.ok(button(tree(),'Let agent continue'));env.unmount();
});
test('closing accepts a minimal stop receipt without exposing stale pixels',async()=>{
  const requests=[];
  const apiFetch=async(url,options)=>{requests.push({url,options});return url.endsWith('/control')
    ?response({workspace:{surface_id:'surface-one',generation:2,mode:'closed'}}):response({workspaces:[state()]});};
  const {env,tree}=await mountedSurface(apiFetch);
  await button(tree(),'Close workspace').props.onClick();
  assert.equal(find(tree(),node=>node.props.role==='alert'),null);
  assert.equal(button(tree(),'Take control'),null);
  assert.deepEqual(JSON.parse(requests.find(item=>item.url.endsWith('/control')).options.body),{operation:'close',generation:1});
  env.unmount();
});
test('permission changes include the displayed consent revision',async()=>{
  const env=environment(),writes=[];
  const apiFetch=async(url,options)=>{if(options?.method==='PUT'){writes.push(JSON.parse(options.body));return response({permission:{enabled:true,generation:9}});}return response({permission:{enabled:false,generation:8}});};
  const render=()=>env.render(env.window.FridayAgentWorkspacePermission,{apiFetch});
  render();env.commit();await flush();await button(render(),'Allow agent workspaces').props.onClick();
  assert.deepEqual(writes,[{enabled:true,generation:8}]);env.unmount();
});
test('stop all remains reachable and suppresses an older list response',async()=>{
  const env=environment(),late=deferred();
  const apiFetch=async url=>url.endsWith('/stop-all')?response({stopped:true}):late.promise;
  const props={apiFetch};env.workspaces(props);env.commit();
  const stop=button(env.workspaces(props),'Stop all workspaces');assert.ok(stop);
  await stop.props.onClick();late.resolve(response({workspaces:[state()]}));await flush();
  const tree=env.workspaces(props);assert.equal(find(tree,node=>node.props.initial),null);env.unmount();
});
test('a late steering receipt cannot acknowledge a replacement instruction',async()=>{
  const late=deferred(),tickets=[];
  const apiFetch=async(url,options)=>{
    if(url.endsWith('/steer')){
      if(options?.method==='POST'){tickets.push(JSON.parse(options.body).request_id);return response({status:'queued'});}
      return late.promise;
    }
    return response({workspaces:[state()]});
  };
  const {env,tree}=await mountedSurface(apiFetch);
  find(tree(),node=>node.type==='textarea').props.onChange({target:{value:'First instruction'}});
  await button(tree(),'Guide task').props.onClick();tree();env.commit();
  find(tree(),node=>node.type==='textarea').props.onChange({target:{value:'Second instruction'}});
  await button(tree(),'Guide task').props.onClick();
  assert.equal(tickets.length,2);assert.notEqual(tickets[0],tickets[1]);
  // Deliver the old response before React cleans up its old effect.
  late.resolve(response({steers:[{request_id:tickets[0],status:'consumed'}],accepting:true}));await flush();
  assert.equal(find(tree(),node=>node.children?.includes('The agent received your guidance.')),null);
  assert.ok(find(tree(),node=>node.children?.includes('Guidance queued. The agent will receive it at its next checkpoint.')));
  env.unmount();
});
test('served UI and source mirror include setup permission and real workspace assets',()=>{
  for(const file of ['index.html','ui_parts/app.html']){
    const text=fs.readFileSync(path.join(root,file),'utf8');
    const finish=text.slice(text.indexOf('function SetupFinishCard'),text.indexOf('function SetupChat'));
    assert.match(finish,/FridayAgentWorkspacePermission/);
    assert.match(text,/Agent workspaces/);
  }
  for(const file of ['index.html','ui_parts/styles_and_scene.html']){
    const text=fs.readFileSync(path.join(root,file),'utf8');
    assert.match(text,/static\/friday_agent_workspaces\.js/);
  }
  const crew=fs.readFileSync(path.join(root,'static/friday_crew.js'),'utf8');
  assert.match(crew,/FridayAgentWorkspaces/);assert.match(crew,/Task project/);assert.match(crew,/crew_dialogue/);
});
