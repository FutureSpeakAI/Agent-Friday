const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.FRIDAY_UI_SOURCE || path.resolve(__dirname, '../../index.html'), 'utf8');
const begin = source.indexOf('async function careerRequest(');
const career = source.slice(begin < 0 ? source.indexOf('function CareerWS(') : begin, source.indexOf('function OutreachPanel('));
const workflows = source.slice(source.indexOf('function WorkflowsWS('), source.indexOf('function SitesWS('));
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = (data, ok = true) => ({ok, status:ok ? 200 : 503, json:async()=>data});
const deferred = () => {let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve}};

function mount({ready=true,failStatus=false,request,workflow=false}={}) {
  const states=[], refs=[], effects=[], calls=[], toasts=[];
  let cursor=0, refCursor=0, mounted=false;
  const element=(type,props,...children)=>({type,props:props||{},children});
  const fetch=async(url,options)=>{
    calls.push({url,body:options&&options.body?JSON.parse(options.body):undefined});
    const custom=request&&request(url,options);if(custom)return await custom;
    const values={
      '/api/career-ops/status':{ready,configured:false,path:'/career',items:[],summary:ready?'Ready':'Add your profile'},
      '/api/career-ops/tracker':{entries:[]},'/api/career-ops/reports':{reports:[]},'/api/career-ops/pipeline':{content:''},'/api/jobs':{content:''},'/api/pipeline/jobs':{jobs:[]},
      '/api/conversations':{status:'ok',conversation:{id:'career-test'}},'/api/chat':{response:'Evidence-backed evaluation'},
      '/api/workflows/templates/career-search/add':{status:'ok',slug:'career-search',created:true,schedule_id:null},
      '/api/workflows/overview':{status:'ok',workflows:[],routines:[],templates:[{id:'career-search',slug:'career-search',name:'Career search',description:'Find and evaluate roles.',step_count:3,installed:false}]},
      '/api/tasks':{tasks:[]}
    };
    if(failStatus&&url==='/api/career-ops/status')return response({error:'Setup unavailable'},false);
    if(!values[url])throw new Error('Unexpected request '+url);
    return response(values[url]);
  };
  const context=vm.createContext({
    React:{createElement:element},useState:initial=>{const n=cursor++;if(!(n in states))states[n]=initial;return [states[n],v=>{states[n]=typeof v==='function'?v(states[n]):v}]},
    useRef:initial=>{const n=refCursor++;return refs[n]||(refs[n]={current:initial})},useEffect:fn=>{if(!mounted)effects.push(fn)},
    fetch,apiFetch:fetch,URL,Promise,fridayName:()=> 'Friday',fridayToast:s=>toasts.push(s),
    FridaySays:'FridaySays',FridayDoc:'FridayDoc',SendTo:'SendTo',OutreachPanel:'OutreachPanel',
    WF_CSS:'',WF_EXAMPLES:[],WfCard:'WfCard',WfEditor:'WfEditor',WfSwitch:'WfSwitch',wfOpenApprovals:()=>{},
    setInterval:()=>1,clearInterval:()=>{},setTimeout:()=>1,window:{},document:{},
  });
  vm.runInContext(career+workflows,context);
  const render=()=>{cursor=0;refCursor=0;return workflow?context.WorkflowsWS():context.CareerWS()};
  const nodes=t=>[t,...(t.children||[]).flat(Infinity).filter(x=>x&&typeof x==='object').flatMap(nodes)];
  const all=()=>nodes(render());
  const button=label=>{const b=all().find(n=>n.type==='button'&&n.children.flat(Infinity).includes(label));assert.ok(b,'button '+label+' exists');return b};
  render();mounted=true;effects.forEach(fn=>fn());
  return {all,button,calls,toasts,render};
}

test('missing or unreadable career setup never claims readiness and blocks tasks',async()=>{
  for(const options of [{ready:false},{failStatus:true}]){
    const ui=mount(options);await flush();
    const text=JSON.stringify(ui.render());
    assert.doesNotMatch(text,/Career-ops ready\./);
    assert.equal(ui.button('Scan opportunities').props.disabled,true);
    assert.match(text,options.failStatus?/Readiness unavailable/:/Setup needed/);
    assert.ok(ui.button('Refresh'));
  }
});

test('evaluation uses a dedicated native chat, awaits it and rejects duplicate activation',async()=>{
  const chat=deferred();const ui=mount({request:url=>url==='/api/chat'?chat.promise:null});await flush();
  ui.all().find(n=>n.props.id==='career-job-url').props.onChange({target:{value:'https://employer.example/jobs/1'}});
  const evaluate=ui.button('Evaluate job');const action=evaluate.props.onClick();evaluate.props.onClick();await flush();
  assert.equal(ui.calls.filter(c=>c.url==='/api/conversations').length,1);
  const requests=ui.calls.filter(c=>c.url==='/api/chat');assert.equal(requests.length,1);
  assert.equal(requests[0].body.conversation_id,'career-test');assert.equal(requests[0].body.workspace,'career');
  assert.match(requests[0].body.message,/career_evaluate/);assert.equal(ui.calls.some(c=>c.url.includes('vibe-code')),false);
  assert.equal(ui.button('Evaluate job').props.disabled,true);
  assert.ok(ui.all().find(n=>n.type==='a'&&n.props.href.includes('conversation=career-test')));
  assert.doesNotMatch(JSON.stringify(ui.render()),/Review Friday’s response/);
  chat.resolve(response({response:'Evidence-backed evaluation'}));await action;await flush();
  assert.match(JSON.stringify(ui.render()),/Evidence-backed evaluation/);
  assert.equal(ui.button('Evaluate job').props.disabled,false);
  assert.equal(ui.all().find(n=>n.props.id==='career-job-url').props.value,'https://employer.example/jobs/1');
});

test('rejected chat remains a visible error with a recovery conversation link',async()=>{
  const ui=mount({request:url=>url==='/api/chat'?response({error:'Seat unavailable'},false):null});await flush();
  await ui.button('Scan opportunities').props.onClick();await flush();
  assert.ok(ui.all().find(n=>n.props.role==='alert'&&n.children.includes('Seat unavailable')));
  assert.ok(ui.all().find(n=>n.type==='a'&&n.props.href.includes('conversation=career-test')));
  assert.equal(ui.button('Scan opportunities').props.disabled,false);
  assert.doesNotMatch(JSON.stringify(ui.render()),/queued|Scan finished/);
});

test('invalid job URL is refused without creating a chat',async()=>{
  const ui=mount();await flush();
  ui.all().find(n=>n.props.id==='career-job-url').props.onChange({target:{value:'not a URL'}});
  await ui.button('Tailor CV').props.onClick();await flush();
  assert.match(JSON.stringify(ui.render()),/Enter a full job URL/);
  assert.equal(ui.calls.some(c=>c.url==='/api/conversations'),false);
});

test('Career starter awaits save, guards duplicates and neither runs nor schedules it',async()=>{
  const save=deferred();const ui=mount({request:url=>url.includes('/templates/')?save.promise:null});await flush();
  const button=ui.button('Add Career search workflow');const action=button.props.onClick();button.props.onClick();await flush();
  assert.equal(ui.calls.filter(c=>c.url.includes('/templates/')).length,1);
  assert.equal(ui.button('Add Career search workflow').props.disabled,true);
  assert.doesNotMatch(JSON.stringify(ui.render()),/Career search added to Workflows/);
  save.resolve(response({status:'ok',slug:'career-search',created:true}));await action;await flush();
  assert.match(JSON.stringify(ui.render()),/Career search added to Workflows/);
  assert.equal(ui.calls.some(c=>c.url.includes('/run')||c.url.includes('/schedules')),false);
});

test('Workflow Manager offers the manual Career starter and displays add errors',async()=>{
  const ui=mount({workflow:true,request:url=>url.includes('/templates/')?response({error:'Could not save starter'},false):null});await flush();
  await ui.button('Add Career search workflow').props.onClick();await flush();
  assert.match(JSON.stringify(ui.render()),/Could not save starter/);
  assert.equal(ui.button('Add Career search workflow').props.disabled,false);
  assert.equal(ui.calls.some(c=>c.url.includes('/run')||c.url.includes('/schedules')),false);
});

test('an invalid existing Career starter is explained and cannot be overwritten',async()=>{
  const ui=mount({workflow:true,request:url=>url==='/api/workflows/overview'?response({status:'ok',workflows:[],routines:[],templates:[{id:'career-search',slug:'career-search',name:'Career search',step_count:3,installed:false,problem:'Repair the existing workflow before adding this starter.'}]}):null});await flush();
  assert.equal(ui.button('Add Career search workflow').props.disabled,true);
  assert.match(JSON.stringify(ui.render()),/Repair the existing workflow/);
  assert.equal(ui.calls.some(c=>c.url.includes('/templates/')),false);
});

test('an empty career funnel has no progress, and populated stages use their actual denominator',async()=>{
  const empty=mount();await flush();
  const fills=ui=>ui.all().filter(n=>n.props.className==='progress-fill').map(n=>n.props.style.width);
  assert.deepEqual(fills(empty),['0%','0%','0%','0%']);
  const rows=[{company:'Example A',status:'Applied'},{company:'Example B',status:'Interview'}];
  const populated=mount({request:url=>url==='/api/career-ops/tracker'?response({entries:rows}):null});await flush();
  assert.deepEqual(fills(populated),['100%','50%','50%','0%']);
});
