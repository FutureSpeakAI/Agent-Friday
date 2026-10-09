/* Agent workspaces display real browser frames; input stays inside the owned page. */
(function (window) {
  'use strict';
  const React = window.React;
  if (!React) return;
  const h = React.createElement;
  const {useState, useEffect, useRef} = React;
  const base = '/api/browser/workspaces';
  const modes = {agent:'Agent working',paused:'Paused',human:'You have control',closed:'Closed',revoked:'Permission revoked'};
  const validId = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const requestId = () => window.crypto.randomUUID();
  const changed = () => window.dispatchEvent(new CustomEvent('friday:agent-workspaces-changed'));
  async function read(apiFetch, path, options) {
    const response = await apiFetch(path, options);
    const data = await response.json();
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('The workspace returned an unreadable response.');
    if (!response.ok || data.ok === false || data.status === 'error') {
      const error = new Error(data.message || data.error || 'The workspace request could not finish.');
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function post(apiFetch, path, body, method = 'POST') {
    return read(apiFetch, path, {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  }
  function useVisible(active) {
    const [visible,setVisible] = useState(!document.hidden);
    useEffect(() => {
      const update = () => setVisible(!document.hidden);
      document.addEventListener('visibilitychange',update);
      return () => document.removeEventListener('visibilitychange',update);
    },[]);
    return active && visible;
  }
  function statusValue(value) {
    if (!value || !validId(value.surface_id) || !integer(value.generation)
        || !integer(value.page_generation) || !Object.prototype.hasOwnProperty.call(modes,value.mode)
        || !value.viewport || !Number.isFinite(value.viewport.width) || value.viewport.width <= 0
        || !Number.isFinite(value.viewport.height) || value.viewport.height <= 0) {
      throw new Error('The workspace returned an unreadable view. Refresh to continue.');
    }
    return value;
  }
  function permissionValue(value) {
    if (!value || typeof value.enabled !== 'boolean' || !integer(value.generation) || value.generation < 1) {
      throw new Error('Workspace permission could not be read. Refresh to continue.');
    }
    return value;
  }
  window.FridayAgentWorkspacePermission = function Permission({apiFetch,disabled=false}) {
    const [permission,setPermission] = useState(null), [error,setError] = useState('');
    const [busy,setBusy] = useState(false), live = useRef(true), pending = useRef(false);
    const [refresh,setRefresh] = useState(0);
    const refreshNow = () => setRefresh(value=>value+1);
    useEffect(() => {
      live.current = true;
      window.addEventListener('friday:agent-workspaces-changed',refreshNow);
      return () => { live.current = false; window.removeEventListener('friday:agent-workspaces-changed',refreshNow); };
    },[]);
    useEffect(() => {
      let cancelled = false;
      const abort = new AbortController();
      read(apiFetch,base+'/permission',{signal:abort.signal}).then(data => {
        const next = permissionValue(data.permission);
        if (!cancelled) setPermission(previous=>!previous || next.generation >= previous.generation ? next : previous);
      }).catch(error => { if (!cancelled && error.name !== 'AbortError') setError(error.message); });
      return () => { cancelled = true; abort.abort(); };
    },[apiFetch,refresh]);
    const toggle = async () => {
      if (pending.current || !permission || disabled) return;
      pending.current = true; setBusy(true); setError('');
      try {
        const result = await post(apiFetch,base+'/permission',{enabled:!permission.enabled,generation:permission.generation},'PUT');
        const next = permissionValue(result.permission);
        if (live.current) setPermission(previous=>!previous || next.generation >= previous.generation ? next : previous);
        changed();
      } catch (error) { if (live.current) { setError(error.message); refreshNow(); } }
      finally { pending.current = false; if (live.current) setBusy(false); }
    };
    return h('section',{className:'fr-aw-permission','aria-label':'Agent workspace permission'},
      h('div',null,h('strong',null,'Independent agent workspaces'),
        h('p',null,'Let agents work on websites in their own browser sessions, with their own cursors. Your mouse and keyboard stay yours. Agent assignments and action approvals still apply.')),
      h('button',{type:'button',role:'switch','aria-checked':!!permission?.enabled,disabled:disabled||busy||!permission,onClick:toggle},
        busy?'Saving…':permission?.enabled?'Enabled — turn off':'Allow agent workspaces'),
      permission?.enabled && h('p',{className:'fr-aw-muted'},'Turning this off stops workspace input and removes the live views.'),
      error && h('p',{role:'alert'},error),
      error && h('button',{type:'button',disabled:busy,onClick:()=>{setError('');refreshNow();}},'Refresh permission'));
  };
  function Surface({initial,apiFetch,active,onRefresh}) {
    const [surface,setSurface] = useState(initial), [frame,setFrame] = useState(null);
    const [watch,setWatch] = useState(false), [expanded,setExpanded] = useState(false);
    const [error,setError] = useState(''), [busy,setBusy] = useState(false), [notice,setNotice] = useState('');
    const [typed,setTyped] = useState(''), [guidance,setGuidance] = useState('');
    const [steering,setSteering] = useState(null), [tick,setTick] = useState(0);
    const current = useRef(initial), currentFrame = useRef(null), renderedFrame = useRef(null), imageRef = useRef(null);
    const live = useRef(true), pending = useRef(false), epoch = useRef(0), guidanceRequest = useRef(null), steeringTicket = useRef(null);
    const visible = useVisible(active), surfacePath = base+'/'+encodeURIComponent(initial.surface_id);
    useEffect(() => { live.current = true; return () => { live.current = false; epoch.current++; }; },[]);
    function clearFrame() { currentFrame.current = null; renderedFrame.current = null; setFrame(null); }
    function acceptStatus(next) {
      statusValue(next);
      if (next.surface_id !== initial.surface_id || next.generation < current.current.generation
          || (next.generation === current.current.generation && next.page_generation < current.current.page_generation)) return false;
      const invalidates = next.generation !== current.current.generation || next.page_generation !== current.current.page_generation;
      current.current = next; setSurface(next);
      if (invalidates || ['revoked','closed'].includes(next.mode)) {
        clearFrame(); setTyped(''); epoch.current++;
      }
      if (['revoked','closed'].includes(next.mode)) { setWatch(false);setGuidance('');setSteering(null);steeringTicket.current=null;guidanceRequest.current=null; }
      return true;
    }
    useEffect(() => { acceptStatus(initial); },[initial]);
    useEffect(() => {
      if (!watch || !visible || ['closed','revoked'].includes(surface.mode)) {
        clearFrame(); setTyped(''); return;
      }
      let cancelled = false, timer;
      const abort = new AbortController();
      const poll = async () => {
        const generation = current.current.generation, requestEpoch = epoch.current;
        try {
          if (pending.current) return;
          const data = await read(apiFetch,surfacePath+'/frame?generation='+generation,{signal:abort.signal});
          if (cancelled || epoch.current !== requestEpoch) return;
          const next = statusValue(data.frame);
          if (next.surface_id !== initial.surface_id || next.generation !== current.current.generation
              || !integer(next.frame_sequence)) throw new Error('The live frame is unavailable. Refresh the view.');
          if (next.image === null) { if (acceptStatus(next)) clearFrame(); setError(''); return; }
          if (typeof next.image !== 'string'
              || !/^data:image\/(jpeg|png);base64,[A-Za-z0-9+/=]+$/.test(next.image)
              || next.image.length > 6000000) throw new Error('The live frame is unavailable. Refresh the view.');
          if (renderedFrame.current && next.frame_sequence < renderedFrame.current.frame_sequence) return;
          if (!acceptStatus(next)) return;
          // Do not admit input against new metadata until those exact pixels load.
          if (renderedFrame.current?.frame_sequence === next.frame_sequence && renderedFrame.current.image === next.image) return;
          currentFrame.current = null; renderedFrame.current = next;
          setFrame(next); setError('');
        } catch (error) {
          if (!cancelled && epoch.current === requestEpoch && error.name !== 'AbortError') {
            clearFrame(); setError(error.message);
            if ([403,404,409].includes(error.status)) onRefresh();
          }
        } finally {
          if (!cancelled) timer = window.setTimeout(poll,expanded?400:1000);
        }
      };
      poll();
      return () => { cancelled = true; abort.abort(); window.clearTimeout(timer); };
    },[watch,visible,surface.mode,surface.generation,expanded,tick,apiFetch]);
    const run = async action => {
      if (pending.current) return;
      pending.current = true; setBusy(true); setError(''); setNotice('');
      epoch.current++;
      try { await action(); }
      catch (error) { if (live.current) setError(error.message); }
      finally { pending.current = false; if (live.current) { setBusy(false); setTick(n=>n+1); } }
    };
    const control = operation => run(async () => {
      const generation = current.current.generation;
      const requestEpoch = epoch.current;
      clearFrame(); setTyped('');
      const result = await post(apiFetch,surfacePath+'/control',{operation,generation});
      if (!live.current || epoch.current !== requestEpoch) return;
      // Stop replies intentionally omit old page metadata after authority expires.
      if (['close','revoke'].includes(operation) && (!result.workspace || result.workspace.surface_id !== initial.surface_id
          || !integer(result.workspace.generation) || !['closed','revoked'].includes(result.workspace.mode))) {
        throw new Error('The workspace stop could not be confirmed. Refresh to continue.');
      }
      const next = ['close','revoke'].includes(operation) ? {...current.current,...result.workspace,url:'',title:''} : result.workspace;
      if (!acceptStatus(next)) return;
      if (operation === 'takeover') setWatch(true);
      setNotice(operation === 'takeover'?'You control this browser. Click its page, then type into the selected field.':'');
      onRefresh();
    });
    const input = event => run(async () => {
      const frame = currentFrame.current, state = current.current;
      if (state.mode !== 'human' || !frame) throw new Error('Wait for a fresh view before interacting.');
      const generation = state.generation;
      const requestEpoch = epoch.current;
      clearFrame(); setTyped('');
      const result = await post(apiFetch,surfacePath+'/input',{
        generation,event:{...event,page_generation:frame.page_generation,frame_sequence:frame.frame_sequence}
      });
      if (!live.current || epoch.current !== requestEpoch) return;
      acceptStatus(result.workspace); setTyped('');
    });
    const coordinates = event => {
      const rect = imageRef.current?.getBoundingClientRect();
      const viewport = currentFrame.current?.viewport;
      if (!rect || !rect.width || !rect.height || !viewport
          || event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) return null;
      return {x:Math.max(0,Math.min(viewport.width-1,(event.clientX-rect.left)*viewport.width/rect.width)),
        y:Math.max(0,Math.min(viewport.height-1,(event.clientY-rect.top)*viewport.height/rect.height))};
    };
    const clickPage = event => {
      if (surface.mode !== 'human' || pending.current) return;
      const point = coordinates(event); if (point) input({type:'click',...point});
    };
    const wheelPage = event => {
      if (surface.mode !== 'human' || pending.current || !currentFrame.current) return;
      const point = coordinates(event); if (!point) return;
      event.preventDefault(); event.stopPropagation();
      input({type:'wheel',...point,delta_y:Math.max(-1000,Math.min(1000,event.deltaY))});
    };
    // React delegates wheel events passively; this page owns its scroll gesture.
    useEffect(() => {
      const image = imageRef.current;
      if (!watch || !frame || !image) return;
      image.addEventListener('wheel',wheelPage,{passive:false});
      return () => image.removeEventListener('wheel',wheelPage);
    });
    const guide = (intent = 'steer') => run(async () => {
      const message = guidance.trim();
      if (!message || !surface.task_id || !surface.conversation_id) return;
      const signature = JSON.stringify([surface.task_id,intent,message]);
      if (guidanceRequest.current?.signature !== signature) guidanceRequest.current = {signature,id:requestId()};
      const path = '/api/crew/rooms/'+encodeURIComponent(surface.conversation_id)+'/tasks/'+encodeURIComponent(surface.task_id)+'/'+intent;
      const requestEpoch = epoch.current;
      const result = await post(apiFetch,path,{message,request_id:guidanceRequest.current.id,room_revision:surface.room_revision});
      if (!live.current || epoch.current !== requestEpoch) return;
      if (intent === 'steer') {
        const ticket = {path,request_id:guidanceRequest.current.id,status:result.status === 'consumed'?'consumed':'queued'};
        steeringTicket.current = ticket; setSteering(ticket);
      }
      setGuidance(''); guidanceRequest.current = null;
      setNotice(intent === 'talk' ? 'The agent is replying in your chat while its work continues.' : 'Guidance queued. The agent will receive it at its next checkpoint.');
    });
    useEffect(() => {
      if (!steering || steering.status === 'consumed' || !visible) return;
      let cancelled = false, timer;
      const abort = new AbortController();
      const ownsReceipt = () => !cancelled && live.current && steeringTicket.current?.request_id === steering.request_id
        && steeringTicket.current?.path === steering.path;
      const poll = async () => {
        try {
          const result = await read(apiFetch,steering.path,{signal:abort.signal});
          if (!ownsReceipt()) return;
          const match = result.steers?.find(row=>row.request_id===steering.request_id);
          if (match?.status === 'consumed') {
            setSteering(previous=>previous?.request_id===steering.request_id && previous?.path===steering.path
              ? {...previous,status:'consumed'} : previous);
            setNotice('The agent received your guidance.'); return;
          }
          if (result.accepting === false) { setNotice('The task ended before guidance was confirmed as received.'); return; }
        } catch (error) { if (ownsReceipt()) { setNotice('Guidance receipt could not be refreshed.'); return; } }
        if (!cancelled) timer = window.setTimeout(poll,1800);
      };
      poll(); return () => { cancelled = true; abort.abort(); window.clearTimeout(timer); };
    },[steering,visible,apiFetch]);
    const cursor = frame?.cursor, state = modes[surface.mode], name = surface.display_name || 'Agent';
    const isCrew = surface.actor_id?.startsWith('crew-');
    const cursorVisible = cursor && Number.isFinite(cursor.x) && Number.isFinite(cursor.y);
    return h('article',{className:'fr-aw-surface'+(expanded?' fr-aw-expanded':''),'data-surface-id':surface.surface_id},
      h('header',{className:'fr-aw-heading'},h('div',null,h('h4',null,name),h('span',{className:'fr-aw-muted'},state)),
        h('div',{className:'fr-aw-actions'},h('button',{type:'button',onClick:()=>setWatch(value=>!value),'aria-expanded':watch},watch?'Hide view':'Watch work'),
          watch && h('button',{type:'button',onClick:()=>setExpanded(value=>!value),'aria-pressed':expanded},expanded?'Fit alongside':'Enlarge'))),
      h('p',{className:'fr-aw-location',title:surface.url||''},surface.title || surface.url || 'Browser ready'),
      watch && h('div',{className:'fr-aw-frame'},frame ? h(React.Fragment,null,
        h('img',{key:frame.generation+':'+frame.page_generation+':'+frame.frame_sequence,ref:imageRef,src:frame.image,alt:name+' browser view',draggable:false,onClick:clickPage,
          onLoad:()=>{if(live.current && !pending.current && visible && watch && renderedFrame.current===frame
              && current.current.generation===frame.generation && current.current.page_generation===frame.page_generation) currentFrame.current=frame;},
          onError:()=>{if(renderedFrame.current===frame){clearFrame();setError('The live frame could not be displayed.');}}}),
        cursorVisible && h('div',{className:'fr-aw-cursor','aria-hidden':'true',style:{left:(cursor.x/frame.viewport.width*100)+'%',top:(cursor.y/frame.viewport.height*100)+'%'}},
          h('svg',{width:20,height:25,viewBox:'0 0 20 25'},h('path',{d:'M2 1 L2 20 L7 15 L11 23 L15 21 L11 13 L18 13 Z'})),h('span',null,name)),
        h('span',{className:'fr-aw-frame-label'},surface.mode==='human'?'Your browser controls':'Live agent view')) : h('p',{role:'status'},error?'Live view unavailable':'Loading the live page…')),
      h('div',{className:'fr-aw-actions'},surface.mode==='agent' && h('button',{type:'button',disabled:busy,onClick:()=>control('pause')},'Pause'),
        ['paused','human'].includes(surface.mode) && h('button',{type:'button',disabled:busy,onClick:()=>control('resume')},'Let agent continue'),
        ['agent','paused'].includes(surface.mode) && h('button',{type:'button',disabled:busy,onClick:()=>control('takeover')},'Take control'),
        !['closed','revoked'].includes(surface.mode) && h('button',{type:'button',disabled:busy,onClick:()=>control('close')},'Close workspace'),
        isCrew && surface.conversation_id && h('button',{type:'button',onClick:()=>window.dispatchEvent(new CustomEvent('friday:crew-voice',{detail:{action:'start',conversationId:surface.conversation_id}}))},'Voice room')),
      surface.mode==='human' && watch && h('form',{className:'fr-aw-input',onSubmit:event=>{event.preventDefault();if(typed)input({type:'text',text:typed});}},
        h('label',null,'Type into the selected field',h('input',{type:'password',value:typed,autoComplete:'off',maxLength:2000,disabled:busy,onChange:event=>setTyped(event.target.value),'aria-label':'Text for selected browser field'})),
        h('button',{type:'submit',disabled:busy||!typed},'Type'),
        h('div',{className:'fr-aw-actions'},['Tab','Enter','Backspace','Escape','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].map(key=>h('button',{key,type:'button',disabled:busy,onClick:()=>input({type:'key',key})},key))),
        h('p',{className:'fr-aw-muted'},'Text is hidden here and cleared after use. These controls affect only this browser.')),
      isCrew && surface.task_id && !['closed','revoked'].includes(surface.mode) && h('form',{className:'fr-aw-guide',onSubmit:event=>{event.preventDefault();guide('talk');}},
        h('label',null,'Talk to '+name,h('textarea',{rows:2,value:guidance,maxLength:4000,disabled:busy,onChange:event=>setGuidance(event.target.value),placeholder:'Ask a question or change the task…'})),
        h('div',{className:'fr-aw-actions'},h('button',{type:'submit',disabled:busy||!guidance.trim()},'Ask agent'),
          h('button',{type:'button',disabled:busy||!guidance.trim(),onClick:()=>guide('steer')},'Guide task')),
        h('p',{className:'fr-aw-muted'},'Ask a question while work continues, or send a change to the task. Replies appear in the task’s chat.')),
      error && h('p',{role:'alert',className:'fr-aw-error'},error),notice && h('p',{role:'status'},notice));
  }
  window.FridayAgentWorkspaces = function Workspaces({apiFetch,conversationId=null,active=true}) {
    const [surfaces,setSurfaces] = useState([]), [error,setError] = useState(''), [loaded,setLoaded] = useState(false);
    const [refresh,setRefresh] = useState(0), [busy,setBusy] = useState(false);
    const visible = useVisible(active), lock = useRef(false), live = useRef(true), epoch = useRef(0);
    const refreshNow = () => { epoch.current++; setRefresh(value=>value+1); };
    useEffect(() => { live.current=true;return()=>{live.current=false;epoch.current++;}; },[]);
    useEffect(() => { epoch.current++;setSurfaces([]);setLoaded(false);setError(''); },[visible,conversationId]);
    useEffect(() => {
      const invalidate = () => { setSurfaces([]);setLoaded(false);refreshNow(); };
      window.addEventListener('friday:agent-workspaces-changed',invalidate);
      window.addEventListener('friday:off-record',invalidate);
      return () => {window.removeEventListener('friday:agent-workspaces-changed',invalidate);window.removeEventListener('friday:off-record',invalidate);};
    },[]);
    useEffect(() => {
      if (!visible) return;
      let cancelled = false, timer;
      const abort = new AbortController();
      const poll = async () => {
        const requestEpoch = epoch.current;
        try {
          if (lock.current) return;
          const query = conversationId?'?conversation_id='+encodeURIComponent(conversationId):'';
          const result = await read(apiFetch,base+query,{signal:abort.signal});
          if (cancelled || epoch.current !== requestEpoch) return;
          if (!Array.isArray(result.workspaces) || result.workspaces.length > 16) throw new Error('Workspace status is unreadable.');
          const next = result.workspaces.map(statusValue);
          if (new Set(next.map(row=>row.surface_id)).size !== next.length) throw new Error('Workspace ownership could not be verified.');
          setSurfaces(next); setError(''); setLoaded(true);
        } catch (error) { if (!cancelled && epoch.current === requestEpoch) { setSurfaces([]); setError(error.message); setLoaded(true); } }
        finally { if (!cancelled) timer = window.setTimeout(poll,2500); }
      };
      poll(); return () => { cancelled = true; abort.abort(); window.clearTimeout(timer); };
    },[visible,conversationId,refresh,apiFetch]);
    const stopAll = async () => {
      if (lock.current) return;
      lock.current = true; epoch.current++;setBusy(true);setError('');setSurfaces([]);
      try { await post(apiFetch,base+'/stop-all',{}); changed(); }
      catch (error) { if(live.current) setError(error.message); }
      finally { lock.current = false;if(live.current){setBusy(false);refreshNow();} }
    };
    return h('section',{className:'fr-agent-workspaces','aria-label':'Agent browser workspaces'},
      h('header',{className:'fr-aw-heading'},h('div',null,h('h3',null,'Agent workspaces'),h('p',null,'Watch each agent’s browser while you keep working.')),
        h('div',{className:'fr-aw-actions'},h('button',{type:'button',onClick:refreshNow},'Refresh views'),
          h('button',{type:'button',disabled:busy,onClick:stopAll},'Stop all workspaces'))),
      !loaded && h('p',{role:'status'},'Loading workspaces…'),error && h('p',{role:'alert'},error),
      loaded && !error && !surfaces.length && h('p',{className:'fr-aw-empty'},'No agent browsers are open. Enable independent workspaces in setup or Privacy & Data, then ask an agent with browser access to work on a website.'),
      h('div',{className:'fr-aw-grid'},surfaces.map(surface=>h(Surface,{key:surface.surface_id,initial:surface,apiFetch,active:visible,onRefresh:refreshNow}))));
  };
})(window);
