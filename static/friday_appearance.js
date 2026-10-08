/* A native, reviewed appearance change. Preview never writes or runs a model. */
(function () {
  'use strict';
  if (window.FridayWorkspaceAppearance || !window.React || !window.ReactDOM) return;
  const h = React.createElement, {useState, useRef, useEffect} = React;
  let active = null;
  const editable = state => ({note: state.note || '', accent: /^#[\da-f]{6}$/i.test(state.accent || '') ? state.accent : '',
    density: state.density || '', actions: (state.actions || []).map(a => ({label:a.label, prompt:a.prompt}))});
  const patchOf = (draft, saved) => {
    const patch = {};
    for (const key of ['note', 'accent', 'density', 'actions']) {
      if (JSON.stringify(draft[key]) !== JSON.stringify(saved[key])) patch[key] = draft[key] === '' ? null : draft[key];
    }
    return patch;
  };
  function validateState(data, workspace, preview = false) {
    const object = value => value && typeof value === 'object' && !Array.isArray(value);
    const validAppearance = value => object(value) &&
      (value.note == null || typeof value.note === 'string') &&
      (value.accent == null || typeof value.accent === 'string') &&
      (value.density == null || ['compact','comfortable'].includes(value.density)) &&
      (value.actions == null || Array.isArray(value.actions) && value.actions.every(a => object(a) && typeof a.label === 'string' && typeof a.prompt === 'string'));
    if (data.workspace !== workspace || !/^(new|[a-f0-9]{64})$/.test(data.revision || '') ||
        !validAppearance(data.customization) || !Array.isArray(data.versions) ||
        preview && (!validAppearance(data.preview) || !Array.isArray(data.changed) ||
          !data.changed.every(key => ['note','accent','density','actions'].includes(key)))) {
      throw new Error('The workspace returned an incomplete appearance. Reload current appearance; your draft is kept.');
    }
    return data;
  }
  async function request(url, options) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await window.apiFetch(url, {...options, signal:controller.signal});
      const data = await response.json();
      if (!response.ok || data.status !== 'ok') throw new Error(data.message || 'The workspace request was not confirmed.');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The request timed out. Reload current appearance before trying to apply again; your draft is kept.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function Editor({workspace, title, finish, opener}) {
    const dialog = useRef(null), alive = useRef(true), serial = useRef(0), flight = useRef(false), dirtyRef = useRef(false);
    const privateRef = useRef(!!window.__fridayOffRecord), mustReload = useRef(!!window.__fridayOffRecord);
    const [saved, setSaved] = useState(null), [draft, setDraft] = useState(null), [preview, setPreview] = useState(null);
    const [busy, setBusy] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
    const [discard, setDiscard] = useState(false), [reload, setReload] = useState(false);
    const [isPrivate, setPrivate] = useState(!!window.__fridayOffRecord);
    const patch = draft && saved ? patchOf(draft, editable(saved.customization)) : {};
    const dirty = Object.keys(patch).length > 0;
    dirtyRef.current = dirty;
    const close = () => { if (flight.current) return; if (dirtyRef.current) setDiscard(true); else finish(); };
    const privacyChanged = () => {
      privateRef.current = !!window.__fridayOffRecord;
      serial.current++; mustReload.current = true;
      setPrivate(privateRef.current); setPreview(null); setReload(true); setError('');
      setNotice(flight.current
        ? 'Privacy changed while a request was running. Its result is unconfirmed here; reload current appearance before continuing. Your public draft is kept.'
        : 'Privacy changed. Reload current appearance and review before applying. Your public draft is kept.');
    };
    const publicNow = () => {
      // Native events can arrive before React renders the disabled controls.
      if (window.__fridayOffRecord || privateRef.current) {
        if (!privateRef.current) privacyChanged();
        return false;
      }
      return true;
    };
    const current = ticket => alive.current && publicNow() && ticket === serial.current;
    const load = async preserve => {
      if (flight.current) return;
      if (!publicNow()) { setBusy(false); return; }
      flight.current = true; setBusy(true); setError('');
      const ticket = ++serial.current;
      try {
        const response = await request('/api/workspace/' + encodeURIComponent(workspace) + '/appearance');
        if (!current(ticket)) return;
        const data = validateState(response, workspace);
        setSaved(data); if (!preserve) setDraft(editable(data.customization));
        mustReload.current = false; setPreview(null); setReload(false);
        setNotice(preserve ? 'Current appearance loaded. Your draft is kept; review its differences before applying.' : '');
      } catch (e) { if (current(ticket)) setError(e.message); }
      finally { flight.current = false; if (alive.current) setBusy(false); }
    };
    useEffect(() => {
      alive.current = true;
      window.addEventListener('friday:off-record', privacyChanged);
      load(false);
      const node = dialog.current;
      const requested = () => {
        const area = window.FridayHolographicWorkspace?.layoutRect?.content || {x:12,y:56,w:innerWidth-24,h:innerHeight-80};
        const w = Math.min(600, Math.max(1, area.w-16)), ht = Math.min(720, Math.max(1, area.h-16));
        return {x:area.x+(area.w-w)/2, y:area.y+(area.h-ht)/2, w, h:ht};
      };
      const place = () => {
        const r = requested();
        Object.assign(node.style, {left:r.x+'px',top:r.y+'px',width:r.w+'px',height:r.h+'px'});
      };
      place(); node.showModal();
      const unbind = window.FridayHolographicWorkspace?.registerSurface?.(node, {kind:'overlay',getRect:requested});
      window.addEventListener('resize', place); window.addEventListener('friday:spatial-layout', place);
      node.querySelector('[data-appearance-close]')?.focus();
      return () => {
        alive.current = false; serial.current++; unbind?.();
        window.removeEventListener('friday:off-record',privacyChanged);
        window.removeEventListener('resize',place); window.removeEventListener('friday:spatial-layout',place);
        if (node.open) node.close();
        opener?.isConnected && opener.focus({preventScroll:true});
      };
    }, [workspace]);
    const edit = (key, value) => {
      if (!publicNow() || flight.current || discard) return;
      setDraft(d => ({...d,[key]:value})); setPreview(null); if (!mustReload.current) setNotice('');
    };
    const review = async apply => {
      if (!publicNow() || mustReload.current || flight.current || discard || !saved || !dirty || apply && !preview) return;
      const chosen = apply ? preview.patch : patch;
      flight.current = true; setBusy(true); setError('');
      const ticket = ++serial.current;
      try {
        const response = await request('/api/workspace/' + encodeURIComponent(workspace) + '/appearance', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({patch:chosen, expected_revision: apply ? preview.revision : saved.revision, apply})
        });
        if (!current(ticket)) return;
        const data = validateState(response, workspace, !apply);
        if (typeof data.applied !== 'boolean') throw new Error('The workspace did not confirm whether this appearance was saved. Reload current appearance; your draft is kept.');
        if (apply) {
          setSaved(data); setDraft(editable(data.customization)); setPreview(null);
          setNotice(data.applied ? 'Appearance saved. Earlier versions are available in workspace history.' : 'This appearance is already saved.');
          window.dispatchEvent(new CustomEvent('friday:appearance-saved', {detail:{workspace, customization:data.customization}}));
        } else setPreview({...data,patch:chosen});
      } catch (e) {
        if (current(ticket)) { setError(e.message); setPreview(null); mustReload.current = true; setReload(true); }
      } finally { flight.current = false; if (alive.current) setBusy(false); }
    };
    const field = (label, control) => h('label',{className:'fr-appearance-field'},h('span',null,label),control);
    return h('dialog',{ref:dialog,className:'fr-appearance','data-friday-overlay':'dialog','aria-label':'Appearance of '+title,
      onCancel:e=>{e.preventDefault();close();}},
      h('header',null,h('div',null,h('small',null,'VIBE CODING SALON'),h('h2',null,title),h('p',null,'Workspace appearance · saved across Simple and Classic')),
        h('button',{type:'button','aria-label':'Close appearance editor','data-appearance-close':'',disabled:busy,onClick:close},'×')),
      h('div',{className:'fr-appearance-body','aria-busy':busy},
        error&&h('p',{role:'alert',className:'fr-appearance-error'},error),
        isPrivate&&h('p',{role:'status',className:'fr-appearance-privacy'},'Appearance editing is paused while Off the Record is on. Your public draft is kept. Turn it off, then reload current appearance to continue.'),
        notice&&h('p',{role:'status'},notice),
        !saved&&!isPrivate&&h('p',{role:'status'},busy?'Loading current appearance…':'Current appearance is unavailable.'),
        (!saved||reload)&&!busy&&h('button',{type:'button',disabled:isPrivate,onClick:()=>load(!!draft)},'Reload current appearance'),
        draft&&h('fieldset',{disabled:busy||discard||isPrivate},
          field('Spacing',h('select',{value:draft.density,onChange:e=>edit('density',e.target.value)},
            h('option',{value:''},'Workspace default'),h('option',{value:'comfortable'},'Comfortable'),h('option',{value:'compact'},'Compact'))),
          field('Accent',h('div',{className:'fr-appearance-accent'},
            h('input',{type:'color',value:draft.accent||'#00d4ff','aria-label':'Choose accent color',onChange:e=>edit('accent',e.target.value)}),
            h('span',null,draft.accent||'Original workspace accent'),h('button',{type:'button',onClick:()=>edit('accent','')},'Use original'))),
          field('Pinned workspace note',h('textarea',{value:draft.note,maxLength:1500,rows:3,onChange:e=>edit('note',e.target.value)})),
          h('div',{className:'fr-appearance-actions'},h('h3',null,'Quick actions'),h('p',null,'Each button opens this workspace’s conversation with its prompt.'),
            draft.actions.map((action,index)=>h('div',{className:'fr-appearance-action',key:index},
              field('Button label '+(index+1),h('input',{value:action.label,maxLength:40,onChange:e=>edit('actions',draft.actions.map((a,i)=>i===index?{...a,label:e.target.value}:a))})),
              field('Prompt '+(index+1),h('textarea',{value:action.prompt,maxLength:400,rows:2,onChange:e=>edit('actions',draft.actions.map((a,i)=>i===index?{...a,prompt:e.target.value}:a))})),
              h('button',{type:'button','aria-label':'Remove quick action '+(index+1),onClick:()=>edit('actions',draft.actions.filter((_,i)=>i!==index))},'Remove'))),
            h('button',{type:'button',disabled:draft.actions.length>=8,onClick:()=>edit('actions',[...draft.actions,{label:'',prompt:''}])},'Add quick action'))),
        preview&&h('section',{className:'fr-appearance-preview','aria-label':'Appearance preview',style:{'--appearance-accent':preview.preview.accent||'var(--fr-cyan)'}},
          h('small',null,'PREVIEW · NOT SAVED'),h('h3',null,'Changes to '+title),
          h('p',null,'Changed: '+preview.changed.join(', ')+'. Existing code and other appearance settings are kept.'),
          h('div',{className:'fr-appearance-sample','data-density':preview.preview.density||'comfortable'},
            h('strong',null,'Workspace sample'),preview.preview.note&&h('p',null,preview.preview.note),
            h('div',null,(preview.preview.actions||[]).map((a,i)=>h('span',{key:i},a.label))))),
        discard&&h('div',{className:'fr-appearance-discard',role:'alert'},h('p',null,'Discard this unsaved appearance draft?'),
          h('button',{type:'button',disabled:busy,onClick:()=>setDiscard(false)},'Keep editing'),h('button',{type:'button',disabled:busy,onClick:()=>{if(!flight.current)finish();}},'Discard draft'))),
      h('footer',null,h('span',{role:'status'},isPrivate?'Appearance editing paused':busy?'Working…':dirty?'Unsaved appearance draft':'Current appearance'),
        h('button',{type:'button',disabled:busy||discard||isPrivate||!dirty||reload,onClick:()=>review(false)},'Preview changes'),
        h('button',{type:'button',className:'fr-appearance-apply',disabled:busy||discard||isPrivate||!preview||!dirty||reload,onClick:()=>review(true)},'Apply appearance')));
  }
  window.FridayWorkspaceAppearance = {open({id,title,opener:trigger}) {
    if (!/^[a-z0-9_-]+$/i.test(id || '') || !window.apiFetch) return false;
    if (active) { active.node.querySelector('dialog')?.focus(); return false; }
    const node = document.createElement('div'), opener = trigger || document.activeElement;
    document.body.appendChild(node);
    const root = ReactDOM.createRoot(node);
    const finish = () => { root.unmount(); node.remove(); if(active?.node===node)active=null; };
    active={node};root.render(h(Editor,{workspace:id,title:title||id,finish,opener}));return true;
  }, patchOf, editable};
})();
