/* Desktop orientation over the real scene. Domain records stay in their existing
 * stores; this component owns only the current view and browser-session choices. */
(function () {
  'use strict';
  if (window.FridayExperience) return;
  const h = React.createElement;
  const { useState, useEffect, useRef } = React;
  const SESSION_KEY = 'friday.experience.v1';
  const VIEWS = [['day', 'Day'], ['projects', 'Projects'], ['explore', 'Explore'], ['activity', 'Activity'], ['workspaces', 'Workspaces']];
  const ICONS = {
    day: 'M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
    projects: 'M3 7h7l2-3h8a1 1 0 0 1 1 1v14H3V7Zm0 0V4h6l3 3',
    explore: 'm15.5 8.5-2 5-5 2 2-5 5-2ZM21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
    activity: 'M3 12h4l3-7 4 14 3-7h4',
    workspaces: 'M3 3h7v7H3V3Zm11 0h7v7h-7V3ZM3 14h7v7H3v-7Zm11 0h7v7h-7v-7Z',
    search: 'm16 16 5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    chat: 'M4 4h16v12H9l-5 4V4Z',
    settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2',
    arrow: 'M5 12h14m-5-5 5 5-5 5',
    plus: 'M12 5v14M5 12h14'
  };
  function icon(name) {
    return h('svg', { viewBox: '0 0 24 24', width: 20, height: 20, fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }, h('path', { d: ICONS[name] || ICONS.workspaces }));
  }
  function readSession() {
    try { const d = JSON.parse(sessionStorage.getItem(SESSION_KEY) || '{}'); return d && typeof d === 'object' ? d : {}; } catch (_) { return {}; }
  }
  function defaultShape(view) { return { density: 'comfortable', arrangement: view === 'workspaces' || view === 'explore' ? 'grid' : 'list' }; }
  function readShapes(raw) {
    const result = {};
    if (!raw || typeof raw !== 'object') return result;
    VIEWS.forEach(([key]) => {
      const value = raw[key];
      if (!value || typeof value !== 'object') return;
      result[key] = {
        density: value.density === 'compact' ? 'compact' : 'comfortable',
        arrangement: key === 'workspaces' || key === 'explore' ? (value.arrangement === 'list' ? 'list' : 'grid') : 'list'
      };
    });
    return result;
  }
  function when(value) {
    if (!value) return '';
    const d = new Date(typeof value === 'number' ? value * 1000 : value);
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function eventTime(event) {
    if (event.all_day) return 'All day';
    const raw = event.start_time || (typeof event.start === 'string' ? event.start : event.start && (event.start.dateTime || event.start.date));
    if (!raw) return 'Open calendar';
    const d = new Date(raw);
    return isNaN(d.getTime()) ? String(raw) : d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  function taskState(t) { return String(t.display_status || t.status || 'unknown'); }
  function isCurrent(t) { return ['running', 'queued', 'interrupted', 'stalled', 'waiting', 'waiting_approval', 'paused'].includes(taskState(t)); }
  function stateLabel(t) {
    const s = taskState(t);
    const words = { complete: 'Complete', completed: 'Complete', completed_unverified: 'Finished · unverified', running: 'Working', queued: 'Queued', interrupted: 'Interrupted', stalled: 'Stalled', failed: 'Failed', error: 'Failed', timeout: 'Timed out', cancelled: 'Cancelled', waiting_approval: 'Needs you', waiting: 'Waiting', paused: 'Paused', unknown: 'Status unavailable' };
    return words[s] || s.replace(/_/g, ' ');
  }
  const action = (label, onClick, extra) => h('button', Object.assign({ type: 'button', className: 'fx-action', onClick }, extra || {}), label);
  function empty(title, text, button) { return h('div', { className: 'fx-empty' }, h('strong', null, title), text && h('p', null, text), button); }
  function section(title, content, more) { return h('section', { className: 'fx-section' }, h('div', { className: 'fx-section-heading' }, h('h2', null, title), more), content); }

  function FridayExperience(props) {
    const p = props;
    useEffect(() => {
      const dock = document.querySelector('.dock');
      if (!dock) return;
      const measure = () => {
        const rect = dock.getBoundingClientRect();
        const depth = dock.classList.contains('hidden') || !rect.height ? 0 : Math.max(0, innerHeight - rect.top);
        document.body.style.setProperty('--fx-dock-height', Math.ceil(depth) + 'px');
      };
      const size = new ResizeObserver(measure);
      const visibility = new MutationObserver(measure);
      size.observe(dock); visibility.observe(dock, {attributes: true, attributeFilter: ['class']});
      window.addEventListener('resize', measure); measure();
      return () => {
        size.disconnect(); visibility.disconnect(); window.removeEventListener('resize', measure);
        document.body.style.removeProperty('--fx-dock-height');
      };
    }, []);
    const initial = useRef(readSession());
    const [view, setView] = useState(() => { const requested = new URLSearchParams(location.search).get('view'); return VIEWS.some(v => v[0] === requested) ? requested : VIEWS.some(v => v[0] === initial.current.view) ? initial.current.view : 'day'; });
    const [selectedProject, setSelectedProject] = useState(initial.current.project || '');
    const [favorites, setFavorites] = useState(Array.isArray(initial.current.favorites) ? initial.current.favorites : ['library', 'media', 'code', 'knowledge']);
    const [lastWorkspace, setLastWorkspace] = useState(initial.current.lastWorkspace || '');
    const [shapes, setShapes] = useState(() => readShapes(initial.current.shapes));
    const [previousShapes, setPreviousShapes] = useState(() => readShapes(initial.current.previousShapes));
    const [shapeDraft, setShapeDraft] = useState(null);
    const [sessionStored, setSessionStored] = useState(true);
    const [activityTab, setActivityTab] = useState('now');
    const [query, setQuery] = useState('');
    const [projectForm, setProjectForm] = useState(null);
    const [removeProject, setRemoveProject] = useState(false);
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState('');
    const [error, setError] = useState('');
    const [approvals, setApprovals] = useState([]);
    const [day, setDay] = useState({ loading: true, error: '', events: [] });
    const [dayRevision, setDayRevision] = useState(0);
    const [files, setFiles] = useState({ loading: false, error: '', items: [] });
    const [draft, setDraft] = useState('');
    const [homePrompt, setHomePrompt] = useState('');
    const [preparing, setPreparing] = useState(false);
    const homeDrafts = useRef({});
    const homeDraftContext = useRef(selectedProject || 'session');
    useEffect(() => { const key=selectedProject || 'session'; if(key!==homeDraftContext.current){homeDrafts.current[homeDraftContext.current]=homePrompt;homeDraftContext.current=key;setHomePrompt(homeDrafts.current[key]||'');} }, [selectedProject]);
    useEffect(() => {
      const navigate = e => { const target=e.detail && e.detail.view; if(VIEWS.some(v=>v[0]===target))go(target); };
      window.addEventListener('friday:desktop-view',navigate);
      return () => window.removeEventListener('friday:desktop-view',navigate);
    }, []);
    const projectDrafts = useRef({});
    const navigationRevision = useRef(0);
    const discussionPending = useRef(false);
    const projects = Array.isArray(p.projects) ? p.projects.filter(x => !x.archived) : [];
    const conversations = Array.isArray(p.conversations) ? p.conversations : [];
    const tasks = Array.isArray(p.tasks) ? p.tasks : [];
    const registry = window.FRIDAY_WORKSPACE_REGISTRY && window.FRIDAY_WORKSPACE_REGISTRY.workspaces || [];
    const workspaces = Array.isArray(p.workspaces) ? p.workspaces.map(w => Object.assign({}, registry.find(x => x.id === w.id) || {}, w)) : [];
    const project = projects.find(x => x.id === selectedProject) || projects[0] || null;
    const materialProject = projects.find(x => x.id === selectedProject) || null;
    const projectChats = project ? conversations.filter(c => c.project === project.id && c.status !== 'archived') : [];
    const recentChats = conversations.filter(c => c.status !== 'archived').slice().sort((a, b) => Number(b.last_active_at || 0) - Number(a.last_active_at || 0));
    const currentTasks = tasks.filter(isCurrent);
    const historyTasks = tasks.filter(t => !isCurrent(t));
    const visible = p.desktopVisible !== false;
    const savedShape = shapes[view] || defaultShape(view);
    const visibleShape = shapeDraft && shapeDraft.view === view ? shapeDraft.value : savedShape;
    const agentName = window.fridayName ? window.fridayName() : 'your assistant';
    const api = p.apiFetch || window.fetch.bind(window);
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    useEffect(() => {
      try {
        sessionStorage.setItem(SESSION_KEY, JSON.stringify({ view, project: selectedProject, favorites, lastWorkspace, shapes, previousShapes }));
        setSessionStored(true);
      } catch (_) { setSessionStored(false); }
    }, [view, selectedProject, favorites, lastWorkspace, shapes, previousShapes]);
    useEffect(() => {
      if (!window.fridayApprovalFeed) return undefined;
      return window.fridayApprovalFeed.subscribe(setApprovals);
    }, []);
    useEffect(() => {
      if (view !== 'day' || !visible) return undefined;
      let stopped = false;
      setDay(d => Object.assign({}, d, { loading: true, error: '' }));
      api('/api/calendar/today').then(async r => {
        const d = await r.json();
        if (!r.ok || d.status !== 'ok') throw new Error(d.message || d.error || 'Calendar could not be read.');
        if (!stopped) setDay({ loading: false, error: '', events: Array.isArray(d.events) ? d.events : [], annotation: d.annotation || '', date: d.date, connected: d.google_connected });
      }).catch(e => { if (!stopped) setDay({ loading: false, error: e.message || 'Calendar could not be read.', events: [] }); });
      return () => { stopped = true; };
    }, [view, visible, dayRevision]);
    useEffect(() => {
      if (view !== 'projects' || !project || !visible) return undefined;
      let stopped = false;
      setFiles({ loading: true, error: '', items: [], projectId: project.id });
      api('/api/projects/' + encodeURIComponent(project.id) + '/files').then(async r => {
        const d = await r.json();
        if (!r.ok || d.status !== 'ok') throw new Error(d.message || d.error || 'Project files could not be read.');
        if (!stopped) setFiles({ loading: false, error: '', items: Array.isArray(d.files) ? d.files : [], projectId: project.id });
      }).catch(e => { if (!stopped) setFiles({ loading: false, error: e.message, items: [], projectId: project.id }); });
      return () => { stopped = true; };
    }, [view, project && project.id, visible]);
    function go(next) {
      navigationRevision.current += 1;
      setShapeDraft(null);
      setView(next); setQuery(''); setError(''); setNotice(''); setRemoveProject(false);
      if (p.onShowDesktop) p.onShowDesktop();
    }
    function openWorkspace(target) {
      const t = typeof target === 'string' ? { workspace: target } : target;
      if (!t || !t.workspace) return;
      // Opening a collection from a visible project is an explicit context
      // choice. A collection opened elsewhere never picks the first project.
      if (view === 'projects' && project && selectedProject !== project.id) setSelectedProject(project.id);
      setLastWorkspace(t.workspace);
      if (p.onOpenWorkspace) p.onOpenWorkspace(t);
    }
    function openChat(c) { if (c && p.onOpenConversation) p.onOpenConversation(c.id); }
    function keepFormDraft() {
      if (projectForm) projectDrafts.current[projectForm.id || 'new'] = projectForm;
    }
    function beginProjectForm(record) {
      keepFormDraft();
      const key = record && record.id || 'new';
      setProjectForm(projectDrafts.current[key] || { id: record && record.id, name: record && record.name || '', instructions: record && record.instructions || '' });
      setError('');
    }
    function cancelProjectForm() {
      if (projectForm) delete projectDrafts.current[projectForm.id || 'new'];
      setProjectForm(null);
    }
    function chooseProject(id) { keepFormDraft(); setSelectedProject(id); setProjectForm(null); setRemoveProject(false); go('projects'); }
    async function request(url, method, body) {
      const r = await api(url, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok || d.status !== 'ok') throw new Error(d.message || d.error || 'The change could not be saved.');
      return d;
    }
    async function refresh() {
      if (p.onRefreshProjects) {
        const d = await p.onRefreshProjects();
        if (d === null) throw new Error('The change was saved, but the project list could not refresh. Try Refresh.');
      }
    }
    async function saveProject(e) {
      e.preventDefault();
      if (busy || !projectForm || !projectForm.name.trim()) return;
      const submitted = projectForm;
      const revision = navigationRevision.current;
      setBusy(true); setError(''); setNotice('');
      try {
        const d = await request('/api/projects' + (projectForm.id ? '/' + encodeURIComponent(projectForm.id) : ''), projectForm.id ? 'PATCH' : 'POST', { name: projectForm.name.trim(), instructions: projectForm.instructions });
        if (!alive.current) return;
        delete projectDrafts.current[submitted.id || 'new'];
        if (navigationRevision.current === revision) setSelectedProject(d.project.id);
        setProjectForm(current => current === submitted ? null : current); setNotice(d.note || 'Project saved.');
        await refresh();
      } catch (e2) { if (alive.current) setError(e2.message); }
      finally { if (alive.current) setBusy(false); }
    }
    async function newChat(pid) {
      if (busy) return;
      setBusy(true); setError('');
      try {
        const d = await request('/api/conversations', 'POST', { title: 'New chat', project: pid || null });
        if (!d.conversation || !d.conversation.id) throw new Error('The server did not return the new conversation. Refresh before trying again.');
        if (pid && d.conversation.project !== pid) {
          await refresh();
          throw new Error('That project is no longer available. The new conversation was kept outside the project and was not opened.');
        }
        if (alive.current) openChat(d.conversation);
        await refresh();
      } catch (e) { if (alive.current) setError(e.message); }
      finally { if (alive.current) setBusy(false); }
    }
    async function deleteProject() {
      if (!project || busy) return;
      setBusy(true); setError('');
      try {
        const d = await request('/api/projects/' + encodeURIComponent(project.id), 'DELETE');
        if (alive.current) { setSelectedProject(''); setRemoveProject(false); setNotice(d.note || 'Project removed. Its chats were kept.'); }
        await refresh();
      } catch (e) { if (alive.current) setError(e.message); }
      finally { if (alive.current) setBusy(false); }
    }
    async function discuss(text, options) {
      if (discussionPending.current) throw new Error('A discussion is already being prepared.');
      setError('');
      if (!p.onDraftChat) { setDraft(text); setNotice('Discussion prepared below. Copy it into the conversation you choose.'); return; }
      discussionPending.current = true;
      try {
        await p.onDraftChat(text, project && view === 'projects' ? project.id : materialProject && materialProject.id || null, options);
        if (alive.current) setNotice('Discussion prepared in chat. Review it before sending.');
      } catch (e) {
        const failure = e instanceof Error ? e : new Error('The discussion could not be prepared in chat.');
        if (alive.current) { setDraft(text); setError(failure.message + ' Your draft is kept below.'); }
        throw failure;
      } finally { discussionPending.current = false; }
    }
    function materialTray() {
      const context = view === 'projects' ? project : materialProject;
      return window.FridayMaterials && window.FridayMaterials.Tray ? h(window.FridayMaterials.Tray, { key: context ? context.id : 'session', projectId: context ? context.id : 'session', onNavigate: openWorkspace, onDiscuss: discuss, className: 'fx-materials' }) : null;
    }
    function workspaceCard(w, compact) {
      return h('div', { className: compact ? 'fx-workspace-card fx-workspace-card-compact' : 'fx-workspace-card', key: w.id },
        h('button', { type: 'button', className: 'fx-workspace-open', onClick: () => openWorkspace(w.id) },
          h('img', { className: 'fx-workspace-icon', src: '/assets/icons/' + (w.icon || 'code') + '.svg', alt: '', width: 32, height: 32 }),
          h('span', null, h('strong', null, w.label), !compact && h('span', { className: 'fx-meta' }, w.blurb || 'Open workspace')),
          p.openWorkspaceIds && p.openWorkspaceIds.has(w.id) && h('span', { className: 'fx-open-dot', title: 'Open', 'aria-label': 'Open' })),
        !compact && h('div', { className: 'fx-workspace-actions' },
          action(favorites.includes(w.id) ? 'Unpin' : 'Pin', () => setFavorites(xs => xs.includes(w.id) ? xs.filter(x => x !== w.id) : xs.concat(w.id)), { className: 'fx-action-secondary', 'aria-pressed': favorites.includes(w.id), 'aria-label': (favorites.includes(w.id) ? 'Unpin ' : 'Pin ') + w.label }),
          p.onCustomize && action('Customize', () => p.onCustomize(w.id), { className: 'fx-action-secondary', 'aria-label': 'Customize ' + w.label })));
    }
    function chatRow(c) {
      return h('button', { className: 'fx-row-button', type: 'button', key: c.id, onClick: () => openChat(c), 'aria-current': p.activeConversationId === c.id ? 'true' : undefined }, icon('chat'),
        h('span', { className: 'fx-row-copy' }, h('strong', null, c.title || 'Untitled chat'), h('span', { className: 'fx-meta' }, [when(c.last_active_at), c.effective_seat && c.effective_seat.model].filter(Boolean).join(' · '))), icon('arrow'));
    }
    function taskRow(t) {
      return h('button', { className: 'fx-task-row', type: 'button', key: t.task_id, onClick: () => p.onOpenTask && p.onOpenTask(t.task_id) },
        h('span', { className: 'fx-row-copy' }, h('strong', null, t.name || 'Task'), h('span', { className: 'fx-meta' }, t.now || t.description || (t.process ? 'Background process' : 'Open task details'))),
        h('span', { className: 'fx-chip', 'data-state': taskState(t) }, stateLabel(t)));
    }
    function openShape() {
      setShapeDraft({ view, value: Object.assign({}, savedShape) });
      setNotice('');
    }
    function previewShape(key, value) {
      setShapeDraft(d => d && d.view === view ? { view, value: Object.assign({}, d.value, { [key]: value }) } : d);
    }
    function applyShape() {
      if (!shapeDraft || shapeDraft.view !== view) return;
      const next = shapeDraft.value;
      if (next.density !== savedShape.density || next.arrangement !== savedShape.arrangement) {
        setPreviousShapes(old => Object.assign({}, old, { [view]: savedShape }));
        setShapes(old => Object.assign({}, old, { [view]: Object.assign({}, next) }));
        setNotice('View updated. You can restore the previous layout from Shape this view.');
      }
      setShapeDraft(null);
    }
    function restoreShape() {
      if (!previousShapes[view]) return;
      const prior = previousShapes[view];
      setPreviousShapes(old => Object.assign({}, old, { [view]: savedShape }));
      setShapes(old => Object.assign({}, old, { [view]: prior }));
      setShapeDraft(null); setNotice('Previous layout restored.');
    }
    function shapeControls() {
      if (!shapeDraft || shapeDraft.view !== view) return null;
      const options = (key, labels) => labels.map(([value, label]) => action(label, () => previewShape(key, value), { key: value, className: 'fx-action-secondary', 'aria-pressed': shapeDraft.value[key] === value }));
      return h('section', { className: 'fx-view-controls', 'aria-label': 'Shape this view' },
        h('div', { className: 'fx-shape-heading' }, h('h2', null, 'Shape this view'), h('p', { className: 'fx-meta' }, 'Preview the change here, then apply it when it feels right.')),
        (view === 'explore' || view === 'workspaces') && h('fieldset', { className: 'fx-arrangement' }, h('legend', null, 'Arrangement'), options('arrangement', [['grid', 'Grid'], ['list', 'List']])),
        h('fieldset', { className: 'fx-density' }, h('legend', null, 'Spacing'), options('density', [['comfortable', 'Comfortable'], ['compact', 'Compact']])),
        h('p', { className: 'fx-shape-note', role: 'status' }, 'Preview · ' + (visibleShape.density === 'compact' ? 'compact spacing' : 'comfortable spacing') + ((view === 'explore' || view === 'workspaces') ? ' · ' + visibleShape.arrangement + ' arrangement' : '') + '. Not applied yet.'),
        h('div', { className: 'fx-view-history' }, action('Apply', applyShape), action('Cancel', () => setShapeDraft(null), { className: 'fx-action-secondary' }), action('Restore previous layout', restoreShape, { className: 'fx-action-secondary', disabled: !previousShapes[view] })),
        h('p', { className: 'fx-shape-note' }, sessionStored ? 'Applied layouts and one previous version are remembered in this browser session.' : 'Browser session storage is unavailable. Layouts last only while this window stays open.'));
    }
    function title(eyebrow, heading, subtitle, actions) {
      return h(React.Fragment, null, h('header', { className: 'fx-main-header' }, h('div', { className: 'fx-eyebrow' }, eyebrow), h('div', { className: 'fx-title-row' }, h('h1', null, heading), h('div', { className: 'fx-header-actions' }, actions, action('Shape this view', openShape, { className: 'fx-action-secondary', 'aria-expanded': !!(shapeDraft && shapeDraft.view === view) }))), subtitle && h('p', { className: 'fx-subtitle' }, subtitle)), shapeControls());
    }
    function dayView() {
      const resume=recentChats[0];
      async function prepare(e) {
        e.preventDefault(); if(preparing || !homePrompt.trim())return;
        const submitted=homePrompt,context=selectedProject||'session';setPreparing(true);setError('');
        try { await discuss(submitted,{personal:!materialProject});if(homeDraftContext.current===context)setHomePrompt(value=>value===submitted?'':value);delete homeDrafts.current[context]; }
        catch(_) {} finally { if(alive.current)setPreparing(false); }
      }
      return h(React.Fragment,null,
        h('div',{className:'fx-home-top'},h('section',{className:'fx-home-greeting'},
          h('div',{className:'fx-eyebrow'},'Your space. Your work. Your '+agentName+'.'),
          h('h1',null,'What shall we make today?'),
          h('p',{className:'fx-subtitle'},'A clear desktop, with everything ready when you need it.'),
          project ? action('Resume '+project.name+' →',()=>chooseProject(project.id)) : action('Start a project →',()=>{go('projects');beginProjectForm(null);})),
        h('aside',{className:'fx-home-day'},h('div',{className:'fx-section-heading'},h('span',{className:'fx-eyebrow'},'My day'),h('span',{className:'fx-meta'},new Date().toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'}))),
          day.loading ? h('p',{className:'fx-meta',role:'status'},'Reading your calendar…') : day.error ? h('div',{className:'fx-error'},h('p',null,day.error),action('Try again',()=>setDayRevision(n=>n+1))) : day.events.length ? day.events.slice(0,2).map((e,i)=>h('button',{className:'fx-row-button',type:'button',key:e.id||i,onClick:()=>openWorkspace({workspace:'calendar',date:day.date})},h('span',{className:'fx-row-copy'},h('strong',null,e.summary||e.title||'Calendar event'),h('span',{className:'fx-meta'},eventTime(e))))) : h('p',{className:'fx-meta'},day.connected===false?'No local calendar events. Connect a calendar to bring your day here.':'Your calendar has room today.'),
          approvals.length ? action(approvals.length+' decision'+(approvals.length===1?'':'s')+' need'+(approvals.length===1?'s':'')+' you →',()=>{go('activity');setActivityTab('needs');},{className:'fx-attention-action'}) : action('Open calendar →',()=>openWorkspace('calendar'),{className:'fx-action-secondary'}))),
        h('div',{className:'fx-home-scene-tools'},h('span',{className:'fx-eyebrow'},'A living desktop'),h('span',{className:'fx-meta'},'Your holographic companion, always here.'),action('Explore avatars & depth ↗',()=>window.FridayHolographicWorkspace?.open(),{className:'fx-action-secondary'})),
        h('div',{className:'fx-home-bottom'},h('form',{className:'fx-home-composer',onSubmit:prepare},
          h('textarea',{value:homePrompt,onChange:e=>setHomePrompt(e.target.value),placeholder:'Ask, make, or pick up where you left off…','aria-label':'Ask '+agentName,rows:3,disabled:preparing}),
          h('div',{className:'fx-home-composer-footer'},h('div',{className:'fx-home-context'},
            h('select',{'aria-label':'Conversation project',value:materialProject?.id||'',disabled:preparing,onChange:e=>setSelectedProject(e.target.value)},h('option',{value:''},'Personal context'),projects.map(x=>h('option',{value:x.id,key:x.id},x.name))),
            action('+ Materials',()=>go('explore'),{className:'fx-action-secondary'})),
            h('button',{className:'fx-action',type:'submit',disabled:preparing||!homePrompt.trim()},preparing?'Preparing…':'Continue →')),
          h('small',{className:'fx-meta'},'Opens your draft in the conversation, ready to review.')),
        h('section',{className:'fx-home-resume'},h('div',{className:'fx-section-heading'},h('span',{className:'fx-eyebrow'},'Continue working'),action('All projects',()=>go('projects'),{className:'fx-action-secondary'})),
          resume?chatRow(resume):h('p',{className:'fx-meta'},'Your conversations will appear here.'),
          project&&action(project.name+' →',()=>chooseProject(project.id),{className:'fx-row-button'}),
          currentTasks.length>0&&h('div',{className:'fx-home-current'},taskRow(currentTasks[0])))));
    }
    function formView() {
      return h('form', { className: 'fx-form', onSubmit: saveProject }, h('h2', null, projectForm.id ? 'Edit project' : 'A place for your next idea'),
        h('label', null, 'Project name', h('input', { value: projectForm.name, onChange: e => setProjectForm(f => Object.assign({}, f, { name: e.target.value })), maxLength: 120, required: true, autoFocus: true, disabled: busy, placeholder: 'Give it a name' })),
        h('label', null, 'Standing instructions', h('textarea', { value: projectForm.instructions, onChange: e => setProjectForm(f => Object.assign({}, f, { instructions: e.target.value })), maxLength: 4000, rows: 5, disabled: busy, placeholder: 'What should every conversation in this project keep in mind?' })),
        h('p', { className: 'fx-meta' }, 'Chats in this project inherit these instructions. Knowledge and memory remain shared across projects.'),
        h('div', { className: 'fx-form-actions' }, h('button', { type: 'submit', className: 'fx-action', disabled: busy || !projectForm.name.trim() }, busy ? 'Saving…' : 'Save project'), action('Cancel', cancelProjectForm, { className: 'fx-action-secondary', disabled: busy })));
    }
    function projectsView() {
      const filtered = projects.filter(x => (x.name || '').toLowerCase().includes(query.toLowerCase()));
      return h(React.Fragment, null, title('A thread for every ambition', 'Projects', 'Conversations, instructions and materials, together.', action(projectDrafts.current.new ? 'Resume new project' : 'New project', () => beginProjectForm(null), { disabled: busy })),
        h('div', { className: 'fx-project-layout' }, h('aside', { className: 'fx-project-list', 'aria-label': 'Projects' }, h('input', { className: 'fx-search-input', type: 'search', value: query, onChange: e => setQuery(e.target.value), placeholder: 'Find a project', 'aria-label': 'Find a project' }),
          p.projectsStatus && p.projectsStatus.loading && h('p', { className: 'fx-meta', role: 'status' }, 'Loading projects…'),
          p.projectsStatus && p.projectsStatus.error && h('p', { className: 'fx-error' }, p.projectsStatus.error),
          filtered.map(x => h('button', { className: 'fx-project-button', type: 'button', key: x.id, disabled: busy, onClick: () => chooseProject(x.id), 'aria-current': project && project.id === x.id ? 'true' : undefined }, h('strong', null, x.name), h('span', { className: 'fx-meta' }, (x.conversations || 0) + ' conversations · ' + (x.files || 0) + ' files'))),
          !filtered.length && !(p.projectsStatus && (p.projectsStatus.loading || p.projectsStatus.error)) && h('p', { className: 'fx-meta' }, query ? 'No matching projects.' : 'No projects to show.'),
          action('Refresh', () => { setError(''); Promise.resolve(p.onRefreshProjects && p.onRefreshProjects()).then(d => { if (d === null) setError('Projects could not be refreshed.'); }).catch(e => setError(e.message)); }, { className: 'fx-action-secondary' })),
        h('section', { className: 'fx-project-detail', 'aria-label': 'Project detail' }, projectForm ? formView() : project ? h(React.Fragment, null,
          h('div', { className: 'fx-project-heading' }, h('div', null, h('span', { className: 'fx-eyebrow' }, project.type === 'creative' ? 'Creative project' : 'Project'), h('h2', null, project.name)), action(projectDrafts.current[project.id] ? 'Resume editing' : 'Edit', () => beginProjectForm(project), { className: 'fx-action-secondary', disabled: busy })),
          project.instructions && h('p', { className: 'fx-project-instructions' }, project.instructions),
          h('div', { className: 'fx-project-facts' }, h('span', { className: 'fx-chip' }, (project.codebases || []).length + ' connected codebases'), project.seat && project.seat.model && h('span', { className: 'fx-chip' }, project.seat.model)),
          section('Conversations', projectChats.length ? h('div', { className: 'fx-conversation-list' }, projectChats.map(chatRow)) : empty('Start the first conversation', 'It will carry this project’s instructions and model preference.'), action('New chat', () => newChat(project.id), { disabled: busy })),
          section('Project files', files.loading || files.projectId !== project.id ? h('p', { className: 'fx-meta', role: 'status' }, 'Reading project files…') : files.error ? h('p', { className: 'fx-error' }, files.error) : files.items.length ? h('ul', { className: 'fx-file-list' }, files.items.map(f => h('li', { key: f.name }, h('a', { href: '/api/projects/' + encodeURIComponent(project.id) + '/files/' + encodeURIComponent(f.name), target: '_blank', rel: 'noopener noreferrer' }, f.name), h('span', { className: 'fx-meta' }, f.bytes == null ? '' : (f.bytes < 1024 ? f.bytes + ' B' : Math.round(f.bytes / 1024) + ' KB'))))) : h('p', { className: 'fx-meta' }, 'No files have been added to this project.'), action('Browse Library', () => openWorkspace('library'), { className: 'fx-action-secondary' })),
          materialTray(),
          h('div', { className: 'fx-project-tools' }, action('Open Code', () => openWorkspace('code'), { className: 'fx-action-secondary' }), action('Open Media', () => openWorkspace('media'), { className: 'fx-action-secondary' }), action('Remove project…', () => setRemoveProject(true), { className: 'fx-action-secondary fx-danger', disabled: busy })),
          removeProject && h('div', { className: 'fx-confirm', role: 'group', 'aria-label': 'Remove project' }, h('strong', null, 'Remove “' + project.name + '”?'), h('p', null, 'Its conversations will be kept outside the project. The project and its saved files will be removed.'), action('Remove project', deleteProject, { className: 'fx-action fx-danger', disabled: busy }), action('Keep project', () => setRemoveProject(false), { className: 'fx-action-secondary', disabled: busy }))) : p.projectsStatus && (p.projectsStatus.loading || p.projectsStatus.error) ? empty(p.projectsStatus.loading ? 'Loading your projects…' : 'Projects are unavailable', p.projectsStatus.error || '') : empty('Begin with a project', 'Give a piece of work a home, then bring its conversations and materials together.', action('Create project', () => beginProjectForm(null))))));
    }
    function exploreView() {
      const sources = [['library', 'Read and collect', 'Documents, citations and your spatial shelves.'], ['media', 'Make and revisit', 'Images, writing, audio and everything you create.'], ['knowledge', 'Follow a connection', 'Wiki pages and the graph that brings them together.'], ['code', 'Build something', 'Codebases, files, changes and running projects.']];
      return h(React.Fragment, null, title('Find a different way in', 'Explore', 'Move from a collection to an object, then into a conversation.'),
        h('div', { className: 'fx-explore-grid' }, sources.filter(s => workspaces.some(w => w.id === s[0])).map(s => h('button', { className: 'fx-explore-card', type: 'button', key: s[0], onClick: () => openWorkspace(s[0]) }, h('span', { className: 'fx-eyebrow' }, workspaces.find(w => w.id === s[0]).label), h('h2', null, s[1]), h('p', null, s[2]), h('span', { className: 'fx-explore-open' }, 'Open collection ', icon('arrow'))))),
        section('Bring your materials together', h('div', null, h('p', { className: 'fx-meta' }, 'Select an item in Library, Media or a spatial view. The materials bar stays with you while you browse: gather a reference, then review the collection to compare or discuss it.'), h('p', { className: 'fx-material-context' }, materialProject ? 'Gathering for ' + materialProject.name : 'Gathering on this page'))), materialTray(),
        section('A workspace that fits your work', h('div', { className: 'fx-card' }, h('p', null, 'Open a workspace, then choose Customize to adjust its layout with ' + agentName + '. Earlier versions remain in its history.'), action('Choose a workspace', () => go('workspaces'), { className: 'fx-action-secondary' }))));
    }
    function activityView() {
      const rows = activityTab === 'now' ? currentTasks : historyTasks;
      const tabs = [['now', 'Now', currentTasks.length], ['needs', 'Needs you', approvals.length], ['history', 'Recent', historyTasks.length]];
      function tabKey(e, index) {
        let next;
        if (e.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (e.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        else if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = tabs.length - 1;
        else return;
        e.preventDefault(); e.stopPropagation();
        setActivityTab(tabs[next][0]);
        const button = e.currentTarget.parentNode.querySelector('#fx-tab-' + tabs[next][0]);
        if (button) button.focus();
      }
      return h(React.Fragment, null, title('See the work behind the moment', 'Activity', 'Real tasks, decisions and the outcomes they produced.'),
        h('div', { className: 'fx-tabs', role: 'tablist', 'aria-label': 'Activity' }, tabs.map((t, index) => h('button', { type: 'button', role: 'tab', id: 'fx-tab-' + t[0], 'aria-controls': 'fx-activity-panel', 'aria-selected': activityTab === t[0], tabIndex: activityTab === t[0] ? 0 : -1, key: t[0], onClick: () => setActivityTab(t[0]), onKeyDown: e => tabKey(e, index) }, t[1], h('span', { className: 'fx-count' }, t[2])))),
        h('div', { id: 'fx-activity-panel', role: 'tabpanel', 'aria-labelledby': 'fx-tab-' + activityTab }, activityTab === 'needs' ? approvals.length ? (p.renderApprovals ? p.renderApprovals(approvals) : h('div', { className: 'fx-task-list' }, approvals.map(a => h('button', { className: 'fx-task-row', type: 'button', key: a.approval_id, onClick: () => openWorkspace({ workspace: 'system', tab: 'approvals' }) }, h('span', null, h('strong', null, a.title || 'Review decision'), h('span', { className: 'fx-meta' }, a.action_description || 'Open the complete approval card')), h('span', { className: 'fx-chip', 'data-state': 'waiting_approval' }, 'Review'))))) : empty('Nothing waiting on you', 'Decisions appear here when they are ready to review.') : p.tasksStatus && p.tasksStatus.loading ? h('p', { className: 'fx-meta', role: 'status' }, 'Reading activity…') : p.tasksStatus && p.tasksStatus.error ? h('p', { className: 'fx-error' }, p.tasksStatus.error) : rows.length ? h('div', { className: 'fx-task-list' }, rows.map(taskRow)) : empty(activityTab === 'now' ? 'Room for the next thing' : 'No recent task cards', activityTab === 'now' ? 'No active task cards are available.' : 'Older records and receipts remain in System.')),
        h('div', { className: 'fx-footer-actions' }, action('Receipts and system details', () => openWorkspace('system'), { className: 'fx-action-secondary' }), action('Routines', () => openWorkspace('workflows'), { className: 'fx-action-secondary' })));
    }
    function workspacesView() {
      const shown = workspaces.filter(w => (w.label + ' ' + (w.blurb || '')).toLowerCase().includes(query.toLowerCase()));
      const pinned = shown.filter(w => favorites.includes(w.id));
      return h(React.Fragment, null, title('Your tools, one place', 'Workspaces', 'Open the right surface. Shape it around the way you work.'),
        h('input', { className: 'fx-search-input', type: 'search', value: query, onChange: e => setQuery(e.target.value), placeholder: 'Find a workspace', 'aria-label': 'Find a workspace' }),
        pinned.length > 0 && section('Pinned for this session', h('div', { className: 'fx-grid' }, pinned.map(w => workspaceCard(w, true)))),
        section('All workspaces', shown.length ? h('div', { className: 'fx-grid' }, shown.map(w => workspaceCard(w, false))) : empty('No matching workspaces', 'Try a different name.')),
        h('p', { className: 'fx-session-note' }, (sessionStored ? 'Pins, layouts and your last view are remembered in this browser session. ' : 'Session storage is unavailable; pins and layouts last while this window stays open. ') + 'Workspace content and project changes are saved by ' + agentName + '.'));
    }
    const count = approvals.length;
    return h('div', { className: 'fx-shell', hidden: p.enabled === false, 'data-view': view, 'data-desktop-visible': visible ? 'true' : 'false' },
      visible && view!=='day' && h('nav',{className:'fx-desktop-views','aria-label':'Desktop views'},VIEWS.map(v=>h('button',{type:'button',key:v[0],'aria-current':view===v[0]?'page':undefined,onClick:()=>go(v[0])},v[1],v[0]==='activity'&&count>0?h('span',{className:'fx-count'},count):null))),
      p.enabled !== false && !visible && window.FridayMaterials && window.FridayMaterials.SelectionBar && ReactDOM.createPortal(h(window.FridayMaterials.SelectionBar, {
        key: materialProject ? materialProject.id : 'session',
        projectId: materialProject ? materialProject.id : 'session',
        projectName: materialProject ? materialProject.name : '',
        className: 'fx-selection-bar',
        onReview: () => materialProject ? chooseProject(materialProject.id) : go('explore')
      }), document.body),
      visible && h('main', { className: 'fx-main', 'data-view': view, 'data-density': visibleShape.density, 'data-arrangement': visibleShape.arrangement, 'data-layout-preview': shapeDraft && shapeDraft.view === view ? 'true' : 'false', 'aria-label': VIEWS.find(v => v[0] === view)[1] },
        error && h('div', { className: 'fx-error', role: 'alert' }, error), notice && h('div', { className: 'fx-notice', role: 'status' }, notice),
        view === 'day' ? dayView() : view === 'projects' ? projectsView() : view === 'explore' ? exploreView() : view === 'activity' ? activityView() : workspacesView(),
        draft && h('div', { className: 'fx-draft' }, h('label', null, 'Prepared discussion', h('textarea', { readOnly: true, value: draft, rows: 6 })), action('Copy discussion', () => navigator.clipboard.writeText(draft).then(() => setNotice('Discussion copied.')).catch(() => setError('Copy was unavailable. Select the text and copy it.'))), action('Open chat', p.onShowChat, { className: 'fx-action-secondary' }), action('Dismiss', () => setDraft(''), { className: 'fx-action-secondary' }))));
  }
  window.FridayExperience = FridayExperience;
})();
