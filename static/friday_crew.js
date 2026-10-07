/* Crew configuration belongs to profiles and conversations, never global settings. */
(function (window) {
  'use strict';
  const React = window.React;
  if (!React) return;
  const h = React.createElement, {useState,useEffect,useRef} = React;
  const cache = {drafts:{},rooms:{},speech:null,proposal:null};
  const copy = value => JSON.parse(JSON.stringify(value));
  const voice = (action, conversationId) => window.dispatchEvent(new CustomEvent('friday:crew-voice',{detail:{action,conversationId}}));
  const blank = () => ({name:'',role:'',persona:'',provider:'',model:'',voice:{provider:'',model:'',voice_id:''},caption:{label:''},project_ids:[],grants:[],skills:[],allowed_tools:[],memory:{notes:'',read:false,write:false},max_steps:12,time_budget_s:180,status:'active'});
  const bridge = window.FridayCrew = {
    enabled: id => !!cache.rooms[id]?.enabled,
    state: detail => { cache.speech = detail; window.dispatchEvent(new CustomEvent('friday:crew-speech',{detail})); },
    label: meta => meta?.kind === 'crew_result' ? meta.speaker_name || 'Crew agent' : null,
    messages: messages => {
      const receipts=new Map();
      for(const message of messages)if(message.meta?.kind==='crew_playback'&&message.meta.task_id)receipts.set(message.meta.task_id,message.meta);
      return messages.filter(m=>m.meta?.kind!=='crew_playback'&&!(m.meta?.kind==='crew_speech'&&!m.text)).map(m=>{
        const receipt=m.meta?.kind==='crew_result'&&receipts.get(m.meta.task_id);
        return receipt?{...m,meta:{...m.meta,audio_state:receipt.audio_state,played_samples:receipt.played_samples}}:m;
      });
    },
    propose: draft => {
      if (!draft || typeof draft !== 'object' || Array.isArray(draft)) return;
      const proposal = blank();
      for (const key of Object.keys(proposal)) if (key !== 'status' && Object.prototype.hasOwnProperty.call(draft,key)) proposal[key] = copy(draft[key]);
      proposal.status = 'active';
      proposal.voice = {...blank().voice,...proposal.voice}; proposal.memory = {...blank().memory,...proposal.memory};
      cache.proposal = proposal; window.dispatchEvent(new CustomEvent('friday:crew-proposal'));
    }
  };
  window.FridayCrewByline = function ({meta}) {
    if (!['crew_result','crew_speech'].includes(meta?.kind)) return null;
    const outcome = {complete:'Complete',completed:'Complete',completed_unverified:'Reply ready',failed:'Could not finish'}[meta.status] || '';
    return h('span',{className:'fr-crew-byline',title:['Agent: '+meta.speaker_id,'Task: '+meta.task_id,'Provider: '+meta.provider,'Model: '+meta.model].join('\n')},
      h('strong',null,meta.speaker_name || meta.speaker_label || (meta.speaker_id==='friday'?'Friday':'Crew agent')),' · ',[meta.provider,meta.model].filter(Boolean).join(' / '),outcome?' · '+outcome:'',meta.audio_state&&meta.audio_state!=='finished'?' · speech '+meta.audio_state+'; full reply shown':'');
  };
  function area() {
    const content = window.FridayHolographicWorkspace?.state?.spatial?.content;
    if (content && content.w > 0 && content.h > 0) return {left:content.x+4,top:content.y+4,width:Math.max(1,content.w-8),height:Math.max(1,content.h-8)};
    const top = document.querySelector('.top-bar')?.getBoundingClientRect().bottom || 60;
    return {left:12,top:top+12,width:Math.max(1,window.innerWidth-24),height:Math.max(1,window.innerHeight-top-116)};
  }
  window.FridayCrewEntry = function CrewEntry({conversationId,apiFetch,onMessages,profileOnly=false,editorRequest=null,onEditorRequestHandled}) {
    const [open,setOpen] = useState(false), [agents,setAgents] = useState([]), [caps,setCaps] = useState(null), [selected,setSelected] = useState('new');
    const [draft,setDraft] = useState(()=>cache.drafts.new || blank()), [room,setRoom] = useState(null), [members,setMembers] = useState([]);
    const [error,setError] = useState(''), [notice,setNotice] = useState(''), [busy,setBusy] = useState(false), [conflict,setConflict] = useState(null);
    const [speaker,setSpeaker] = useState(cache.speech), [bounds,setBounds] = useState(area), [request,setRequest] = useState(''), [target,setTarget] = useState(''), [pending,setPending] = useState([]);
    const buttonRef = useRef(null), dialogRef = useRef(null), generation = useRef(0), requestRef = useRef(null), latest = useRef({conversationId,onMessages});
    latest.current = {conversationId,onMessages};
    async function call(path, options) {
      const response = await apiFetch(path,options && {...options,headers:{'Content-Type':'application/json'},body:JSON.stringify(options.body)});
      const data = await response.json();
      if (!response.ok || data.status === 'error') { const err = new Error(data.message || data.error || 'Crew request failed.'); err.current = data.current; throw err; }
      return data;
    }
    const base = '/api/crew/rooms/'+encodeURIComponent(conversationId || '');
    function edit(next) { setDraft(next); cache.drafts[selected] = next; setNotice(''); }
    function field(key,value) { edit({...draft,[key]:value}); }
    function select(id, list = agents) { if(busy)return;setSelected(id); setConflict(null); setNotice(''); setDraft(cache.drafts[id] || copy(list.find(a=>a.id===id) || blank())); }
    async function refreshProfiles() {
      const [profiles,capabilities] = await Promise.all([call('/api/crew/agents?include_retired=true'),call('/api/crew/capabilities')]);
      setAgents(profiles.agents || []); setCaps(capabilities);
      return profiles.agents || [];
    }
    useEffect(()=>{
      if(!editorRequest||busy)return;
      const profile=editorRequest.profile;
      select(profile?.id || 'new',profile?[profile]:[]);setOpen(true);
      if(profileOnly)buttonRef.current=editorRequest.opener||null;
      onEditorRequestHandled?.();
    },[editorRequest,busy]);
    useEffect(()=>{
      const id = conversationId, epoch = ++generation.current;
      setRoom(cache.rooms[id] || null); setMembers(cache.rooms[id]?.member_ids || []); setPending([]); setRequest(''); requestRef.current = null;
      if (!id) return;
      call('/api/crew/rooms/'+encodeURIComponent(id)).then(data=>{
        if (generation.current !== epoch) return;
        cache.rooms[id] = data.room; setRoom(data.room); setMembers(data.room.member_ids || []);
      }).catch(e=>{if(generation.current === epoch && open)setError(e.message);});
      return ()=>{generation.current++;};
    },[conversationId]);
    useEffect(()=>{
      if (!open) return;
      let live = true;
      refreshProfiles().catch(e=>{if(live)setError(e.message);});
      const layout=()=>setBounds(area()); layout();
      window.addEventListener('resize',layout); window.addEventListener('friday:spatial-layout',layout);
      const raf=window.requestAnimationFrame(()=>dialogRef.current?.querySelector('button')?.focus());
      return ()=>{live=false;window.cancelAnimationFrame(raf);window.removeEventListener('resize',layout);window.removeEventListener('friday:spatial-layout',layout);};
    },[open]);
    useEffect(()=>{
      const update=e=>setSpeaker(e.detail);
      const proposal=()=>{
        if (!cache.proposal) return;
        const key='proposal-'+Date.now();
        // A proposal never replaces an existing unsaved profile.
        cache.drafts[key]=cache.proposal;
        cache.proposal=null;setSelected(key);setDraft(cache.drafts[key]);setOpen(true);
        setNotice('Friday suggested a new profile. Review its resources and save when ready.');
      };
      window.addEventListener('friday:crew-speech',update);
      window.addEventListener('friday:crew-proposal',proposal);proposal();
      return ()=>{window.removeEventListener('friday:crew-speech',update);window.removeEventListener('friday:crew-proposal',proposal);};
    },[]);
    useEffect(()=>{
      if (!conversationId || !(open || room?.enabled)) return;
      let cancelled=false, timer;
      const poll=async()=>{
        try {
          const data=await call(base+'/turns');
          if(cancelled || latest.current.conversationId !== conversationId)return;
          setPending((data.tasks || []).filter(t=>['queued','running'].includes(t.status)));
          const messages=(data.messages || []).filter(m=>['crew_request','crew_result','crew_speech','crew_playback'].includes(m.meta?.kind));
          if(messages.length)latest.current.onMessages?.(previous=>{
            const key=m=>m.id || (m.meta?.task_id ? m.meta.kind+':'+m.meta.task_id : null);
            const seen=new Set(previous.map(key).filter(Boolean));
            const fresh=messages.filter(m=>key(m)&&!seen.has(key(m)));
            return fresh.length ? previous.concat(fresh.map(m=>({...m,role:m.role==='user'?'user':'friday',time:m.ts?new Date(m.ts*1000).toLocaleTimeString():''}))) : previous;
          });
        } catch(e) { if(!cancelled && open)setError(e.message); }
        if(!cancelled)timer=window.setTimeout(poll,3000);
      }; poll(); return ()=>{cancelled=true;window.clearTimeout(timer);};
    },[conversationId,open,room?.enabled]);
    const run=async action=>{setBusy(true);setError('');setNotice('');try{await action();}catch(e){setError(e.message);if(e.current?.id)setConflict(e.current);else if(e.current?.conversation_id){cache.rooms[e.current.conversation_id]=e.current;if(e.current.conversation_id===latest.current.conversationId)setRoom(e.current);}}finally{setBusy(false);}};
    const save=()=>run(async()=>{
      const body={};
      for(const key of [...Object.keys(blank()),'offline','revision'])if(draft[key]!==undefined)body[key]=copy(draft[key]);
      if(!body.caption.label.trim())body.caption.label=body.name;
      const creating=!draft.id;
      const result=await call(creating?'/api/crew/agents':'/api/crew/agents/'+encodeURIComponent(selected),{method:creating?'POST':'PATCH',body});
      delete cache.drafts[selected]; const agent=result.agent;
      setAgents(list=>[...list.filter(a=>a.id!==agent.id),agent]); setSelected(agent.id); setDraft(copy(agent)); setConflict(null); setNotice('Agent saved.');
      window.dispatchEvent(new CustomEvent('friday:crew-profiles-changed'));
    });
    const lifecycle=status=>run(async()=>{
      if(cache.drafts[selected])throw new Error('Save this profile’s draft before changing its status.');
      const endpoint='/api/crew/agents/'+encodeURIComponent(selected)+(status==='retired'?'/retire':'');
      const result=await call(endpoint,{method:status==='retired'?'POST':'PATCH',body:status==='retired'?{revision:draft.revision}:{revision:draft.revision,status}});
      delete cache.drafts[selected];setAgents(list=>list.map(a=>a.id===selected?result.agent:a));setDraft(copy(result.agent));setNotice(status==='retired'?'Agent retired. Its history is kept.':'Agent '+status+'.');
      window.dispatchEvent(new CustomEvent('friday:crew-profiles-changed'));
    });
    const saveRoom=enabled=>run(async()=>{
      const result=await call(base,{method:'PUT',body:{revision:room?.revision || 0,member_ids:members,enabled}});
      cache.rooms[conversationId]=result.room;
      if(latest.current.conversationId!==conversationId)return;
      setRoom(result.room);setMembers(result.room.member_ids);
      if(!enabled)voice('stop',conversationId);
      setNotice(enabled?'Crew room ready. Choose an agent or start voice.':'Room left. Existing work can finish.');
    });
    const ask=()=>run(async()=>{
      const text=request.trim();if(!text||!target)return;
      const signature=JSON.stringify([conversationId,target,text,room.revision]);
      if(requestRef.current?.signature!==signature)requestRef.current={signature,id:window.crypto?.randomUUID?.() || 'crew-'+Date.now()+'-'+Math.random().toString(36).slice(2)};
      const result=await call(base+'/turns',{method:'POST',body:{room_revision:room.revision,agent_id:target,text,request_id:requestRef.current.id}});
      if(latest.current.conversationId!==conversationId)return;
      setRequest('');requestRef.current=null;setPending(list=>[...list,{task_id:result.task_id,agent_id:target,status:'running'}]);setNotice('Task handed to '+(agents.find(a=>a.id===target)?.name || 'the agent')+'.');
    });
    function close() { setOpen(false);window.requestAnimationFrame(()=>(editorRequest?.opener || buttonRef.current)?.focus()); }
    function keys(e) {
      if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}
      if(e.key==='Tab'){
        const items=[...dialogRef.current.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(el=>el.getClientRects().length);
        const first=items[0],last=items[items.length-1];
        if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
      }
    }
    const frozen=draft.status==='retired'||busy, provider=(caps?.providers||[]).find(p=>p.id===draft.provider), speechProvider=(caps?.voice_providers||[]).find(p=>p.id===draft.voice.provider);
    const options=(list,value)=>[h('option',{key:'',value:''},'Choose…'),...(list||[]).map(o=>h('option',{key:o.id,value:o.id,disabled:o.available===false},o.label||o.name||o.id)),...((value && !(list||[]).some(o=>o.id===value))?[h('option',{key:value,value},value+' (saved)')]:[])];
    const input=(label,value,onChange,extra={})=>h('label',{className:'fr-crew-field'},h('span',null,label),h('input',{'aria-label':label,value:value??'',onChange:e=>onChange(e.target.value),disabled:frozen,...extra}));
    const text=(label,value,onChange,maxLength)=>h('label',{className:'fr-crew-field'},h('span',null,label),h('textarea',{'aria-label':label,value:value||'',onChange:e=>onChange(e.target.value),disabled:frozen,maxLength,rows:3}));
    const choose=(label,value,onChange,list)=>h('label',{className:'fr-crew-field'},h('span',null,label),h('select',{'aria-label':label,value:value||'',onChange:e=>onChange(e.target.value),disabled:frozen},options(list,value)));
    const toggle=(label,value,onChange,disabled=frozen)=>h('label',{className:'fr-crew-check'},h('input',{'aria-label':label,type:'checkbox',checked:!!value,onChange:e=>onChange(e.target.checked),disabled}),h('span',null,label));
    const multi=(key,list,label)=>h('fieldset',null,h('legend',null,label),list.length?list.map(item=>{const id=typeof item==='string'?item:item.id;return h('div',{key:id},toggle(typeof item==='string'?item:item.name||item.label||id,(draft[key]||[]).includes(id),checked=>field(key,checked?[...(draft[key]||[]),id]:(draft[key]||[]).filter(x=>x!==id))));}):h('p',{className:'fr-crew-muted'},'None available.'));
    const roomMembers=agents.filter(a=>(room?.member_ids||[]).includes(a.id)&&a.status==='active');
    const activeSpeech=speaker?.conversation_id===conversationId ? speaker : null;
    return h(React.Fragment,null,!profileOnly&&h('button',{ref:buttonRef,type:'button',className:'fr-crew-entry','aria-expanded':open,onClick:()=>setOpen(true),title:'Crew agents and room'},'Crew',room?.enabled?' · on':''),
      open && window.ReactDOM.createPortal(h('section',{ref:dialogRef,className:'fr-crew-dialog',role:'dialog','aria-modal':'true','aria-label':profileOnly?'Crew agent profile':'Friday Crew',style:bounds,onKeyDown:keys},
        h('header',{className:'fr-crew-head'},h('div',null,h('h2',null,profileOnly?'Agent profile':'Friday Crew'),h('p',null,profileOnly?'Identity, model, voice and permissions.':'Distinct agents, one conversation.')),h('button',{onClick:close,'aria-label':'Close Crew'},'Close')),
        h('div',{className:'fr-crew-notices','aria-live':'polite'},error&&h('p',{role:'alert',className:'fr-crew-error'},error),notice&&h('p',null,notice),conflict&&h('div',null,'A newer revision exists. Your draft is kept. ',h('button',{onClick:()=>{setDraft(copy(conflict));delete cache.drafts[selected];setConflict(null);setError('');}},'Load saved revision'))),
        h('div',{className:'fr-crew-scroll'},!profileOnly&&h('section',{className:'fr-crew-room'},h('h3',null,'This chat’s room'),
          !conversationId?h('p',null,'Open a chat to assemble its Crew.'):h(React.Fragment,null,
            h('p',{className:'fr-crew-muted'},'Invited members can use requests and replies shared in this Crew conversation. Their private files and memories follow their own permissions. Earlier chat history is not automatically shared.'),
            agents.filter(a=>a.status==='active').length===0&&h('p',null,'Create an agent below, then invite it to this chat.'),
            h('div',{className:'fr-crew-members'},agents.filter(a=>a.status==='active').map(a=>h('div',{key:a.id},toggle(a.name,members.includes(a.id),checked=>setMembers(checked?[...members,a.id]:members.filter(id=>id!==a.id)),busy)))),
            h('div',{className:'fr-crew-actions'},h('button',{disabled:busy||!members.length,onClick:()=>saveRoom(true)},room?.enabled?'Save room':'Assemble room'),room?.enabled&&h('button',{disabled:busy,onClick:()=>saveRoom(false)},'Leave room'),
              h('button',{disabled:!room?.enabled||busy,onClick:()=>voice('start',conversationId)},'Start voice room'),h('button',{disabled:!activeSpeech||activeSpeech.status==='disconnected',onClick:()=>voice('quiet',conversationId)},'Quiet'),h('button',{disabled:!activeSpeech||activeSpeech.status==='disconnected',onClick:()=>voice('stop',conversationId)},'Stop voice')),
            h('p',{className:'fr-crew-muted'},'Cloud voice uses the configured Gemini host and each agent’s saved voice. Quiet stops speech; background tasks continue.'),
            activeSpeech&&h('div',{className:'fr-crew-caption','aria-live':'polite'},h('strong',null,activeSpeech.label||'Friday'),': ',activeSpeech.status,activeSpeech.provider?' · '+activeSpeech.provider:'',activeSpeech.message&&h('p',null,activeSpeech.message),activeSpeech.text&&h('p',null,activeSpeech.text)),
            h('div',{className:'fr-crew-handoff'},h('label',{className:'fr-crew-field'},h('span',null,'Hand off to'),h('select',{'aria-label':'Hand off to',value:target,onChange:e=>setTarget(e.target.value),disabled:!room?.enabled},options(roomMembers,target))),
              h('label',{className:'fr-crew-field'},h('span',null,'Request'),h('textarea',{'aria-label':'Request',value:request,rows:2,maxLength:8000,onChange:e=>{setRequest(e.target.value);requestRef.current=null;},disabled:!room?.enabled,placeholder:'What should this agent work on?'})),h('button',{disabled:busy||!room?.enabled||!roomMembers.some(a=>a.id===target)||!request.trim(),onClick:ask},'Hand off')),
            pending.length>0&&h('ul',{className:'fr-crew-pending'},pending.map((p,i)=>h('li',{key:p.task_id||i},(agents.find(a=>a.id===p.agent_id)?.name||'Crew agent')+' · '+(p.status||'working')))))),
          h('section',{className:'fr-crew-profiles'},h('aside',{className:'fr-crew-roster','aria-label':'Agents'},h('h3',null,'Agents'),h('button',{onClick:()=>select('new'),'aria-pressed':selected==='new'},'New agent'),Object.keys(cache.drafts).filter(k=>k.startsWith('proposal-')).map(k=>h('button',{key:k,onClick:()=>select(k),'aria-pressed':selected===k},cache.drafts[k].name||'Suggested agent',' · draft')),agents.map(a=>h('button',{key:a.id,onClick:()=>select(a.id),'aria-pressed':selected===a.id},h('strong',null,a.name),h('small',null,a.status+(cache.drafts[a.id]?' · draft':''))))),
            h('form',{className:'fr-crew-editor',onSubmit:e=>{e.preventDefault();save();}},h('h3',null,!draft.id?'Create agent':draft.name||'Agent profile'),
              h('div',{className:'fr-crew-grid'},input('Name',draft.name,v=>field('name',v),{required:true,maxLength:80}),input('Caption label',draft.caption?.label,v=>field('caption',{label:v}),{maxLength:80})),
              text('Role',draft.role,v=>field('role',v),caps?.limits?.role||1000),text('Personality and instructions',draft.persona,v=>field('persona',v),caps?.limits?.persona||8000),
              h('fieldset',null,h('legend',null,'Reasoning model'),h('div',{className:'fr-crew-grid'},choose('Provider',draft.provider,v=>edit({...draft,provider:v,model:''}),caps?.providers),choose('Model',draft.model,v=>field('model',v),provider?.models || (caps?.models||[]).filter(m=>m.provider===draft.provider))),provider?.available===false&&h('p',null,'This provider is not configured.'),caps&&!(caps.providers||[]).some(p=>p.available!==false)&&h('p',null,'Connect a cloud provider in Settings to choose a reasoning model.')),
              h('fieldset',null,h('legend',null,'Voice'),h('div',{className:'fr-crew-grid'},choose('Voice provider',draft.voice.provider,v=>field('voice',{provider:v,model:'',voice_id:''}),caps?.voice_providers),choose('Voice model',draft.voice.model,v=>field('voice',{...draft.voice,model:v}),speechProvider?.models),
                input('Voice ID',draft.voice.voice_id,v=>field('voice',{...draft.voice,voice_id:v}),{list:'crew-voice-options',maxLength:128}),h('datalist',{id:'crew-voice-options'},(speechProvider?.voices||[]).map(v=>h('option',{key:v.id,value:v.id},v.label||v.id)))),speechProvider?.reason&&h('p',{className:'fr-crew-muted'},speechProvider.reason)),
              multi('project_ids',caps?.projects||[],'Assigned projects'),
              h('fieldset',null,h('legend',null,'Files and folders'),h('p',{className:'fr-crew-muted'},'Add only paths this agent may use. Project files also require project membership. Normal approvals still apply.'),
                (draft.grants||[]).map((grant,i)=>h('div',{key:i,className:'fr-crew-grant'},input('Path',grant.path,v=>field('grants',draft.grants.map((g,n)=>n===i?{...g,path:v}:g)),{required:true}),h('label',{className:'fr-crew-field'},h('span',null,'Access'),h('select',{value:grant.access,disabled:frozen,onChange:e=>field('grants',draft.grants.map((g,n)=>n===i?{...g,access:e.target.value}:g))},h('option',{value:'read'},'Read'),h('option',{value:'write'},'Read and write'))),h('button',{type:'button',disabled:frozen,onClick:()=>field('grants',draft.grants.filter((_,n)=>n!==i))},'Remove'))),h('button',{type:'button',disabled:frozen,onClick:()=>field('grants',[...(draft.grants||[]),{path:'',access:'read'}])},'Add path')),
              multi('skills',caps?.skills||[],'Skills'),multi('allowed_tools',caps?.supported_tools||[],'Allowed tools'),
              h('fieldset',null,h('legend',null,'This agent’s memory'),text('Notes',draft.memory.notes,v=>field('memory',{...draft.memory,notes:v}),caps?.limits?.memory_notes||8000),toggle('Read its own saved memory',draft.memory.read,v=>field('memory',{...draft.memory,read:v})),toggle('Save to its own memory',draft.memory.write,v=>field('memory',{...draft.memory,write:v}))),
              h('div',{className:'fr-crew-grid'},input('Maximum steps',draft.max_steps,v=>field('max_steps',Number(v)),{type:'number',min:1,max:caps?.limits?.max_steps||200}),input('Time budget (seconds)',draft.time_budget_s,v=>field('time_budget_s',Number(v)),{type:'number',min:1,max:caps?.limits?.time_budget_s||3600})),
              h('details',null,h('summary',null,'Offline bindings (experimental)'),h('p',{className:'fr-crew-muted'},caps?.offline?.message || 'Offline Crew is not operational. These optional bindings are stored for future setup.'),
                input('Offline provider',draft.offline?.provider,v=>field('offline',{...(draft.offline||{}),provider:v})),input('Offline model',draft.offline?.model,v=>field('offline',{...(draft.offline||{}),model:v})),input('Offline voice provider',draft.offline?.voice?.provider,v=>field('offline',{...(draft.offline||{}),voice:{...draft.offline?.voice,provider:v}})),input('Offline voice model',draft.offline?.voice?.model,v=>field('offline',{...(draft.offline||{}),voice:{...draft.offline?.voice,model:v}})),input('Offline voice ID',draft.offline?.voice?.voice_id,v=>field('offline',{...(draft.offline||{}),voice:{...draft.offline?.voice,voice_id:v}}))),
              h('div',{className:'fr-crew-actions'},h('button',{type:'submit',disabled:busy||frozen||!caps},busy?'Saving…':'Save agent'),draft.id&&!frozen&&h('button',{type:'button',disabled:busy,onClick:()=>lifecycle(draft.status==='suspended'?'active':'suspended')},draft.status==='suspended'?'Reactivate':'Suspend'),draft.id&&!frozen&&h('button',{type:'button',disabled:busy,onClick:()=>{if(window.confirm('Retire this agent? Its history is kept, but this profile cannot be reactivated.'))lifecycle('retired');}},'Retire')),
              draft.status==='retired'&&h('p',null,'Retired profiles remain available for their history. Create a new agent to work again.'))))),document.body));
  };
  window.FridayCrewWorkspace = function CrewWorkspace({apiFetch,active=true}) {
    const [agents,setAgents]=useState([]),[projects,setProjects]=useState([]),[tasks,setTasks]=useState([]);
    const [loading,setLoading]=useState(true),[errors,setErrors]=useState({}),[filter,setFilter]=useState('all'),[editor,setEditor]=useState(null),[refreshKey,setRefreshKey]=useState(0),[tasksLoading,setTasksLoading]=useState(true);
    const [pageVisible,setPageVisible]=useState(!document.hidden),sequence=useRef(0);
    useEffect(()=>{
      const visible=()=>setPageVisible(!document.hidden);
      document.addEventListener('visibilitychange',visible);
      return()=>document.removeEventListener('visibilitychange',visible);
    },[]);
    useEffect(()=>{
      if(!active)return;
      const refresh=()=>setRefreshKey(n=>n+1);
      window.addEventListener('friday:crew-profiles-changed',refresh);
      return()=>window.removeEventListener('friday:crew-profiles-changed',refresh);
    },[active]);
    useEffect(()=>{
      if(!active||!pageVisible)return;
      let cancelled=false;const abort=new AbortController();setLoading(true);
      async function read(url){const r=await apiFetch(url,{signal:abort.signal});const d=await r.json();if(!r.ok||d.status==='error')throw Error(d.message||d.error||'Could not load Crew.');return d;}
      async function load(){
        const results=await Promise.allSettled([read('/api/crew/agents?include_retired=true'),read('/api/crew/capabilities')]);
        if(cancelled)return;
        const nextErrors={agents:undefined,projects:undefined};
        if(results[0].status==='fulfilled')setAgents(results[0].value.agents||[]);else nextErrors.agents=results[0].reason.message;
        if(results[1].status==='fulfilled')setProjects(results[1].value.projects||[]);else nextErrors.projects=results[1].reason.message;
        setErrors(previous=>({...previous,...nextErrors}));setLoading(false);
      }
      load();return()=>{cancelled=true;abort.abort();};
    },[active,pageVisible,refreshKey]);
    useEffect(()=>{
      if(!active||!pageVisible)return;
      let cancelled=false,timer;const abort=new AbortController();setTasksLoading(true);
      async function load(){
        try{
          const response=await apiFetch('/api/crew/tasks',{signal:abort.signal}),data=await response.json();
          if(!response.ok||data.status==='error')throw Error(data.message||data.error||'Could not load current work.');
          if(cancelled)return;setTasks(data.tasks||[]);setErrors(previous=>({...previous,tasks:undefined}));
        }catch(error){if(cancelled)return;setErrors(previous=>({...previous,tasks:error.message}));}
        if(cancelled)return;setTasksLoading(false);timer=window.setTimeout(load,5000);
      }
      load();return()=>{cancelled=true;abort.abort();window.clearTimeout(timer);};
    },[active,pageVisible,refreshKey]);
    const projectName=id=>projects.find(p=>p.id===id)?.name||id;
    const stateLabel=status=>({active:'Active',suspended:'Suspended',retired:'Retired',queued:'Queued',running:'Working',complete:'Complete',completed:'Complete',completed_unverified:'Reply ready',failed:'Could not finish',cancelled:'Cancelled'}[status]||status||'Unknown');
    const editProfile=(profile,event)=>setEditor({profile,sequence:++sequence.current,opener:event.currentTarget});
    const shown=agents.filter(a=>filter==='all'||a.status===filter);
    return h('div',{className:'fr-crew-workspace','data-testid':'crew-workspace'},
      h('header',{className:'fr-crew-hub-head'},h('div',null,h('h2',null,'Your Crew'),h('p',null,'Specialist agents with their own roles, models, voices and permissions.')),
        h('div',{className:'fr-crew-actions'},h('button',{onClick:e=>editProfile(null,e)},'Create agent'),h('button',{onClick:()=>setRefreshKey(n=>n+1)},'Refresh'))),
      h('p',{className:'fr-crew-muted'},'Manage agents here, then invite them from Crew in any chat. Room requests and replies are shared with its invited members; files and private memory follow each agent’s permissions.'),
      h('section',{'aria-labelledby':'crew-roster-title'},h('div',{className:'fr-crew-hub-heading'},h('h3',{id:'crew-roster-title'},'Agent roster'),h('label',null,'Show ',h('select',{'aria-label':'Agent status',value:filter,onChange:e=>setFilter(e.target.value)},['all','active','suspended','retired'].map(v=>h('option',{key:v,value:v},v==='all'?'All agents':stateLabel(v)))))),
        loading&&h('p',{role:'status'},'Loading agents…'),
        errors.agents&&h('p',{role:'alert'},'Could not refresh agents. ',errors.agents,agents.length?' Previously loaded profiles are shown.':''),
        errors.projects&&h('p',{role:'alert'},'Could not refresh project names. Saved IDs or previously loaded names are shown.'),
        !loading&&!errors.agents&&!shown.length&&h('div',{className:'fr-crew-empty'},h('h4',null,agents.length?'No agents with this status':'Build your first Crew agent'),h('p',null,agents.length?'Choose another status to see your roster.':'Give an agent a role, choose its model and voice, and decide what it may access.')),
        h('div',{className:'fr-crew-agent-grid'},shown.map(agent=>h('article',{key:agent.id,className:'fr-crew-agent-card','data-agent-id':agent.id},
          h('div',{className:'fr-crew-hub-heading'},h('h4',null,agent.name),h('span',{className:'fr-crew-agent-status'},stateLabel(agent.status))),
          h('p',{className:'fr-crew-agent-role'},agent.role||'No role specified'),
          h('dl',null,h('dt',null,'Model'),h('dd',null,[agent.provider,agent.model].filter(Boolean).join(' / ')||'Not selected'),h('dt',null,'Voice'),h('dd',null,[agent.voice?.provider,agent.voice?.model,agent.voice?.voice_id].filter(Boolean).join(' / ')||'Not selected'),h('dt',null,'Projects'),h('dd',null,(agent.project_ids||[]).map(projectName).join(', ')||'No projects assigned')),
          h('button',{'aria-label':(agent.status==='retired'?'View ':'Edit ')+agent.name,onClick:e=>editProfile(agent,e)},agent.status==='retired'?'View profile':'Edit profile'))))),
      h('section',{'aria-labelledby':'crew-work-title',className:'fr-crew-work-list'},h('h3',{id:'crew-work-title'},'Current work'),
        tasksLoading&&h('p',{role:'status'},'Loading current work…'),errors.tasks&&h('p',{role:'alert'},'Could not refresh current work. ',errors.tasks,tasks.length?' Previously loaded tasks are shown.':''),
        !tasksLoading&&!errors.tasks&&!tasks.length&&h('p',{className:'fr-crew-empty'},'No Crew work to show. Open a chat, assemble a room and hand a request to an agent.'),
        tasks.length>0&&h('ul',null,tasks.map(task=>h('li',{key:task.task_id},h('div',null,h('strong',null,task.speaker_name||agents.find(a=>a.id===task.agent_id)?.name||'Crew agent'),h('span',null,' · '+stateLabel(task.status)),h('small',null,task.project_id?projectName(task.project_id):'No project')),task.conversation_id&&h('a',{href:'/?chrome=chat&conversation='+encodeURIComponent(task.conversation_id),target:'_blank',rel:'noopener'},'Open chat'))))),
      active&&h(window.FridayCrewEntry,{apiFetch,profileOnly:true,editorRequest:editor,onEditorRequestHandled:()=>setEditor(null)}));
  };
})(window);
