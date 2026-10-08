const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../..');
const read=file=>fs.readFileSync(path.join(root,file),'utf8');
const registry=JSON.parse(read('static/workspace_registry.js').split('/*BEGIN JSON*/')[1].split('/*END JSON*/')[0]);
test('Crew is one native Work destination with unambiguous menu and voice aliases',()=>{
  const crew=registry.workspaces.find(w=>w.id==='crew');assert.ok(crew);assert.equal(crew.label,'Crew');assert.equal(crew.group,'work');assert.equal(crew.core,true);assert.notEqual(crew.tab,false);assert.equal(crew.held,undefined);
  assert.deepEqual(crew.boundary.components,['CrewWS']);assert.ok(crew.boundary.scripts.includes('static/friday_crew.js'));
  for(const alias of ['agents','subagents','crew hub']){
    const matches=registry.workspaces.filter(w=>w.id===alias||w.label.toLowerCase()===alias||(w.aliases||[]).includes(alias));
    assert.deepEqual(matches.map(w=>w.id),['crew']);
  }
  assert.ok(fs.existsSync(path.join(root,'assets/icons',crew.icon+'.svg')));
});
function environment(){
  const effects=[],layoutEffects=[],stores=new Map(),timers=new Map(),window={innerWidth:1600,innerHeight:1000,addEventListener(){},removeEventListener(){},dispatchEvent(){},requestAnimationFrame:fn=>fn(),ReactDOM:{createPortal:tree=>tree},setTimeout:fn=>{const id=timers.size+1;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)};let cursor=0,component;
  const slot=initial=>{const state=stores.get(component),i=cursor++;if(!(i in state))state[i]=typeof initial==='function'?initial():initial;return[state,i];};
  window.React={createElement:(type,props,...children)=>({type,props:props||{},children}),useState:initial=>{const[state,i]=slot(initial);return[state[i],value=>{state[i]=typeof value==='function'?value(state[i]):value;}];},useRef:initial=>{const[state,i]=slot(()=>({current:initial}));return state[i];},useEffect:fn=>effects.push(fn)};
  // Commit layout effects separately from the explicitly scheduled passive effects.
  window.React.useLayoutEffect=(fn,deps)=>{
    const[state,i]=slot(()=>({layoutEffect:true,deps:undefined,cleanup:undefined})),effect=state[i];
    if(!deps||!effect.deps||deps.length!==effect.deps.length||deps.some((value,index)=>!Object.is(value,effect.deps[index])))layoutEffects.push(()=>{
      if(typeof effect.cleanup==='function')effect.cleanup();
      effect.deps=deps?.slice();effect.cleanup=fn();
    });
  };
  vm.runInNewContext(read('static/friday_crew.js'),{window,document:{hidden:false,body:{},querySelector:()=>null,addEventListener(){},removeEventListener(){}},JSON,Map,AbortController,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}}});
  const render=(fn,props)=>{component=fn;cursor=0;if(!stores.has(fn))stores.set(fn,[]);const tree=fn(props);for(const effect of layoutEffects.splice(0))effect();return tree;};
  return{window,effects,timers,render:props=>render(window.FridayCrewWorkspace,props),renderEditor:props=>render(window.FridayCrewEntry,props),unmountEditor:()=>{for(const effect of stores.get(window.FridayCrewEntry)||[])if(effect?.layoutEffect&&typeof effect.cleanup==='function')effect.cleanup();stores.delete(window.FridayCrewEntry);}};
}
function find(tree,predicate){if(!tree||typeof tree!=='object')return null;if(predicate(tree))return tree;for(const child of tree.children||[]){for(const value of Array.isArray(child)?child:[child]){const found=find(value,predicate);if(found)return found;}}return null;}
test('hub creates profiles through the existing editor with no conversation dependency',()=>{
  const r=environment(),apiFetch=()=>{throw Error('render must not request a room');};let tree=r.render({apiFetch,active:true});
  const create=find(tree,n=>n.type==='button'&&n.children.includes('Create agent'));assert.ok(create);
  create.props.onClick({currentTarget:{focus(){}}});tree=r.render({apiFetch,active:true});
  const editor=find(tree,n=>n.type===r.window.FridayCrewEntry);assert.ok(editor);assert.equal(editor.props.profileOnly,true);assert.equal(editor.props.conversationId,undefined);assert.equal(editor.props.editorRequest.profile,null);
  tree=r.render({apiFetch,active:false});assert.equal(find(tree,n=>n.type===r.window.FridayCrewEntry),null,'hidden workspace retains drafts without an active editor');
});
test('served and mirrored workspace maps route Crew into the shared hub component',()=>{
  for(const file of ['index.html','ui_parts/app.html']){
    const source=read(file);assert.match(source,/function CrewWS\(\{active=true\}\)/);assert.match(source,/window\.FridayCrewWorkspace,\{apiFetch,active\}/);
    assert.match(source,/crew:\s*(?:React\.createElement\(CrewWS|<CrewWS)/);assert.match(source,/STANDALONE_WS==='crew'/);
  }
});
test('closing a profile then returning to the hub does not reopen it, and an explicit reopen keeps its draft',()=>{
  const r=environment(),props={apiFetch:()=>{throw Error('lifecycle test must not request data');},active:true};
  const editorFrom=tree=>find(tree,n=>n.type===r.window.FridayCrewEntry);
  function requestEditor(){const tree=r.render(props);find(tree,n=>n.type==='button'&&n.children.includes('Create agent')).props.onClick({currentTarget:{focus(){}}});}
  function mountEditor(){const editor=editorFrom(r.render(props)),effectIndex=r.effects.length;r.renderEditor(editor.props);r.effects[effectIndex]();return r.renderEditor(editorFrom(r.render(props)).props);}
  requestEditor();let dialog=mountEditor();
  find(dialog,n=>n.props['aria-label']==='Name').props.onChange({target:{value:'Unfinished agent'}});
  dialog=r.renderEditor(editorFrom(r.render(props)).props);find(dialog,n=>n.props['aria-label']==='Close Crew').props.onClick();
  r.render({...props,active:false});r.unmountEditor();dialog=mountEditor();
  assert.equal(find(dialog,n=>n.props.role==='dialog'),null,'returning to Crew must not replay a closed editor request');
  requestEditor();dialog=mountEditor();
  assert.equal(find(dialog,n=>n.props['aria-label']==='Name').props.value,'Unfinished agent','consuming an open request must preserve the separately cached draft');
});
test('current-work chat links open separately without unloading cached profile drafts',async()=>{
  const r=environment(),props={active:true,apiFetch:async()=>({ok:true,json:async()=>({tasks:[{task_id:'task-one',speaker_name:'Mira',status:'running',conversation_id:'chat-one'}]})})};
  r.render(props);const stop=r.effects[3]();await new Promise(resolve=>setImmediate(resolve));
  const link=find(r.render(props),n=>n.type==='a'&&n.children.includes('Open chat'));
  assert.ok(link);assert.equal(link.props.href,'/?chrome=chat&conversation=chat-one');assert.equal(link.props.target,'_blank');assert.ok(link.props.rel.split(/\s+/).includes('noopener'));stop();
});
test('leaving an open editor cannot replay its original request after either drafting or saving a new agent',async()=>{
  for(const saved of [false,true]){
    const r=environment();let creates=0;
    const props={active:true,apiFetch:async(url,options)=>{assert.equal(url,'/api/crew/agents');assert.equal(options.method,'POST');creates++;return{ok:true,json:async()=>({status:'ok',agent:{...JSON.parse(options.body),id:'crew-sample-agent',revision:1}})};}};
    const editorFrom=()=>find(r.render(props),n=>n.type===r.window.FridayCrewEntry);
    const create=find(r.render(props),n=>n.type==='button'&&n.children.includes('Create agent'));create.props.onClick({currentTarget:{focus(){}}});
    const opening=editorFrom();let effectIndex=r.effects.length;r.renderEditor(opening.props);r.effects[effectIndex]();
    let dialog=r.renderEditor(editorFrom().props);find(dialog,n=>n.props['aria-label']==='Name').props.onChange({target:{value:'Mira'}});
    if(saved){dialog=r.renderEditor(editorFrom().props);find(dialog,n=>n.type==='form').props.onSubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));assert.equal(creates,1);}
    r.render({...props,active:false});r.unmountEditor();
    const request=editorFrom();effectIndex=r.effects.length;r.renderEditor(request.props);r.effects[effectIndex]();dialog=r.renderEditor(editorFrom().props);
    assert.equal(find(dialog,n=>n.props.role==='dialog'),null,saved?'saved creation must not reopen as a blank new profile':'open draft must not reopen automatically');
  }
});
test('only current work polls; roster and capabilities refresh once per activation',async()=>{
  const r=environment(),requests=[];
  const apiFetch=async url=>{requests.push(url);return{ok:true,json:async()=>({status:'ok',agents:[],projects:[],tasks:[]})};};
  r.render({apiFetch,active:true});const stopProfiles=r.effects[2](),stopTasks=r.effects[3]();
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(requests.sort(),['/api/crew/agents?include_retired=true','/api/crew/capabilities','/api/crew/tasks']);
  requests.length=0;const next=[...r.timers.values()][0];r.timers.clear();await next();
  assert.deepEqual(requests,['/api/crew/tasks']);stopProfiles();stopTasks();assert.equal(r.timers.size,0);
  requests.length=0;r.render({apiFetch,active:false});r.effects.at(-2)();r.effects.at(-1)();assert.deepEqual(requests,[]);
});
