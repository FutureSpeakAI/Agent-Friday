/* One selected codebase owns the source, map and conversation in Code.
 * Discovered repositories remain originals until an explicit study-copy action.
 */
(function () {
  'use strict';
  const { createElement: h, useState, useEffect, useRef, useCallback } = React;
  const VIEWS = ['understand', 'files', 'changes', 'preview', 'terminal'];
  const viewFor = value => ({ run: 'terminal', vibe: 'understand' }[value] || (VIEWS.includes(value) ? value : 'understand'));
  const repoKey = repo => 'repo:' + repo.path;
  const codeKey = codebase => 'codebase:' + codebase.id;
  const label = value => String(value || '').toLowerCase();

  async function request(url, options) {
    const opts = Object.assign({}, options);
    opts.headers = Object.assign({}, opts.headers);
    if (window.__FRIDAY_API_TOKEN) opts.headers['X-Friday-Token'] = window.__FRIDAY_API_TOKEN;
    const response = await fetch(url, opts);
    const body = await response.json();
    if (!response.ok || body.error || ['error', 'refused', 'failed'].includes(body.status)) throw new Error(body.error || body.message || 'This request could not be completed.');
    return body;
  }

  function CodeStudio({ chat = {}, tools = {}, navTarget, onNavState }) {
    const [codebases, setCodebases] = useState([]);
    const [repos, setRepos] = useState([]);
    const [loading, setLoading] = useState(true);
    const [repoLoading, setRepoLoading] = useState(true);
    const [error, setError] = useState('');
    const [repoError, setRepoError] = useState('');
    const [selected, setSelected] = useState(null);
    const [query, setQuery] = useState('');
    const [view, setView] = useState('understand');
    const [activity, setActivity] = useState('');
    const [railOpen, setRailOpen] = useState(true);
    const [chatOpen, setChatOpen] = useState(true);
    const [form, setForm] = useState(null);
    const [title, setTitle] = useState('');
    const [path, setPath] = useState('');
    const [template, setTemplate] = useState('static');
    const [creating, setCreating] = useState(false);
    const [notice, setNotice] = useState('');
    const [refreshKey, setRefreshKey] = useState(0);
    const [dirty, setDirty] = useState(false);
    const [binding, setBinding] = useState(null);
    const [catalogRevision, setCatalogRevision] = useState(0);
    const life = useRef(true);
    const requestVersion = useRef(0);
    const createPending = useRef(false);
    const owner = useRef(null);
    const dirtyRef = useRef(false);
    const chatRef = useRef(chat);
    const railRef = useRef(null);
    const chatRefElement = useRef(null);
    const bindingRef = useRef(null);
    const consumedNavigation = useRef(null);
    const codebasesRef = useRef(codebases);
    owner.current = selected;
    chatRef.current = chat;
    bindingRef.current = binding;
    codebasesRef.current = codebases;
    const codebase = codebases.find(c => codeKey(c) === selected) || null;
    const repo = repos.find(r => repoKey(r) === selected) || null;
    const convId = codebase && codebase.conversation_id;
    const chatReady = chat.active !== false && !!convId && chat.conversationId === convId && binding && binding.key === selected && binding.ok;
    const wantView = React.useMemo(() => ({ view }), [view]);

    const refresh = () => {
      const generation = ++requestVersion.current;
      setLoading(true); setRepoLoading(true); setError(''); setRepoError('');
      request('/api/codebases').then(d => {
        if (!life.current || generation !== requestVersion.current) return;
        let next = d.codebases || [];
        const editing = dirtyRef.current && codebasesRef.current.find(c => codeKey(c) === owner.current);
        if (editing) {
          const updated = next.find(c => c.id === editing.id);
          if (!updated || updated.conversation_id !== editing.conversation_id) setNotice('This codebase changed elsewhere. Your file draft is kept here; save or copy it before switching.');
          next = [editing, ...next.filter(c => c.id !== editing.id)];
        }
        setCodebases(next); setBinding(null); setCatalogRevision(r => r + 1);
      }).catch(e => { if (life.current && generation === requestVersion.current) setError(e.message); })
        .finally(() => { if (life.current && generation === requestVersion.current) setLoading(false); });
      request('/api/repos/scan').then(d => {
        if (life.current && generation === requestVersion.current) setRepos(d.repos || []);
      }).catch(e => { if (life.current && generation === requestVersion.current) setRepoError(e.message); })
        .finally(() => { if (life.current && generation === requestVersion.current) setRepoLoading(false); });
    };
    useEffect(() => { life.current = true; refresh(); return () => { life.current = false; ++requestVersion.current; }; }, []);
    const setFileDirty = useCallback(value => { dirtyRef.current = value; setDirty(value); }, []);
    const setPanelView = useCallback(value => { if (VIEWS.includes(value)) setView(value); }, []);
    const mayLeave = () => {
      if (dirtyRef.current) { setNotice('Save or cancel the file edit before switching codebases.'); return false; }
      if (createPending.current) return false;
      return true;
    };
    const choose = (key, nextView) => {
      if (key !== owner.current && !mayLeave()) return;
      setSelected(key); owner.current = key; setNotice(''); setActivity(''); setForm(null);
      if (nextView) setView(viewFor(nextView));
      if (window.matchMedia && window.matchMedia('(max-width: 760px)').matches) setRailOpen(false);
    };
    useEffect(() => {
      setBinding(null);
      if (!convId || !codebase || chat.active === false) return;
      let alive = true;
      const key = selected, codebaseId = codebase.id;
      request('/api/conversations/' + encodeURIComponent(convId)).then(async data => {
        if (!alive || owner.current !== key) return;
        if (!data.conversation || data.conversation.codebase !== codebaseId) throw new Error('This conversation is no longer connected to this codebase. Reconnect it from the salon before asking Friday.');
        if (chatRef.current.openConversation) await chatRef.current.openConversation(convId);
        if (alive && owner.current === key) setBinding({ key, ok: true });
      }).catch(e => { if (alive && owner.current === key) setBinding({ key, ok: false, error: e.message }); });
      return () => { alive = false; };
    }, [selected, convId, chat.active, catalogRevision]);
    useEffect(() => {
      if (!navTarget || consumedNavigation.current === navTarget) return;
      if (navTarget.path && repoLoading) return;
      consumedNavigation.current = navTarget;
      if (!mayLeave()) return;
      if (navTarget.codebase_id) choose('codebase:' + navTarget.codebase_id, navTarget.tab);
      else if (navTarget.path) {
        const found = repos.find(r => r.path === navTarget.path);
        choose(found ? repoKey(found) : 'repo:' + navTarget.path);
      }
      if (navTarget.tab === 'procs' || navTarget.tab === 'logs') setActivity(navTarget.tab);
      else if (navTarget.tab === 'repos') setRailOpen(true);
      else if (navTarget.tab === 'git') setActivity('git');
      else if (navTarget.tab) setView(viewFor(navTarget.tab));
    }, [navTarget, repos, repoLoading]);
    useEffect(() => {
      onNavState && onNavState({ tab: activity || view, ...(codebase ? { codebase_id: codebase.id } : {}), ...(repo ? { path: repo.path } : {}) });
    }, [selected, view, activity, codebase && codebase.id, repo && repo.path]);
    useEffect(() => {
      if (!codebase || !window.fridayBusSubscribe) return;
      return window.fridayBusSubscribe(event => {
        if (event && event.codebase_id === codebase.id && ['codebase_step', 'codebase_run', 'codebase_header'].includes(event.type)) setRefreshKey(k => k + 1);
      });
    }, [codebase && codebase.id]);

    const begin = (kind, source) => {
      if (!mayLeave()) return;
      setForm(kind); setTitle(source ? source.name : ''); setPath(source ? source.path : ''); setTemplate('static'); setNotice('');
    };
    const create = async event => {
      event.preventDefault();
      if (createPending.current || !title.trim() || (form === 'study' && !path.trim())) return;
      createPending.current = true; setCreating(true); setNotice('');
      try {
        const body = form === 'study' ? { title: title.trim(), study_path: path.trim() } : { title: title.trim(), template };
        const data = await request('/api/codebases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        if (!data.codebase || !data.codebase.id) throw new Error('The new codebase could not be confirmed. Refresh the list before trying again.');
        if (!life.current) return;
        ++requestVersion.current;
        setLoading(false); setRepoLoading(false);
        setCodebases(items => [data.codebase, ...items.filter(c => c.id !== data.codebase.id)]);
        const key = codeKey(data.codebase);
        setSelected(key); owner.current = key; setView(form === 'study' ? 'understand' : 'preview'); setActivity(''); setForm(null);
        setNotice(form === 'study' ? 'Study copy ready. The original repository is unchanged.' : 'Your codebase is ready.');
        window.dispatchEvent(new CustomEvent('friday:conversations-changed'));
      } catch (e) { if (life.current) setNotice(e.message + ' Refresh codebases before retrying if the request was interrupted.'); }
      finally { createPending.current = false; if (life.current) setCreating(false); }
    };
    const showChat = () => {
      setChatOpen(true);
      if (chatRef.current.active !== false && bindingRef.current && bindingRef.current.ok && bindingRef.current.key === owner.current && convId && chatRef.current.conversationId !== convId && chatRef.current.openConversation) {
        Promise.resolve(chatRef.current.openConversation(convId)).catch(e => { if (life.current) setNotice(e.message || 'Could not open the conversation.'); });
      }
      setTimeout(() => { if (life.current) chatRefElement.current && chatRefElement.current.focus(); }, 0);
    };
    useEffect(() => {
      const focus = () => {
        if (chatRef.current.active === false) return;
        setChatOpen(true);
        setTimeout(() => { if (life.current && chatRef.current.active !== false) chatRefElement.current && chatRefElement.current.querySelector('textarea')?.focus(); }, 0);
      };
      window.addEventListener('friday:code-chat-focus', focus);
      return () => window.removeEventListener('friday:code-chat-focus', focus);
    }, []);
    const ask = async message => {
      const expected = codebase && codebase.id, conversation = convId;
      if (!expected || chatRef.current.active === false || !bindingRef.current || !bindingRef.current.ok || bindingRef.current.key !== owner.current || owner.current !== 'codebase:' + expected || chatRef.current.conversationId !== conversation || typeof chatRef.current.ask !== 'function') {
        throw new Error('Open this codebase’s conversation before asking.');
      }
      setChatOpen(true);
      return chatRef.current.ask(message, conversation);
    };
    const visibleCodebases = codebases.filter(c => label(c.title).includes(label(query)));
    const visibleRepos = repos.filter(r => label(r.name + ' ' + r.path).includes(label(query)));
    const Panel = window.FridayCodebasePanel;
    const Git = tools.Git, Procs = tools.Procs, Logs = tools.Logs;
    const contextLabel = codebase ? (codebase.source_snapshot ? 'Study copy · original untouched' : codebase.existing ? 'Working folder · ' + (codebase.branch || '') : 'Managed codebase') : repo ? 'Original repository · ' + (repo.branch || 'branch unavailable') : 'Explore · learn · build';

    return h('section', { className: 'fr-comp-root fr-comp-code cs-studio' + (!railOpen ? ' cs-rail-closed' : '') + (!chatOpen ? ' cs-chat-closed' : ''), 'data-code-studio': '', 'data-selection': selected || '', 'aria-label': 'Code workspace' },
      h('header', { className: 'cs-top' },
        h('div', { className: 'cs-heading' }, h('span', { className: 'cs-eyebrow' }, 'CODE · SALON'), h('h2', null, codebase ? codebase.title : repo ? repo.name : 'Your code, in focus'), h('p', null, contextLabel)),
        h('div', { className: 'cs-actions' },
          h('button', { className: 'cs-button', 'aria-expanded': railOpen, 'aria-controls': 'code-studio-catalog', onClick: () => setRailOpen(v => !v) }, 'Projects'),
          codebase && h('button', { className: 'cs-button', 'aria-expanded': chatOpen, 'aria-controls': 'code-studio-chat', onClick: () => setChatOpen(v => !v) }, 'Friday'),
          h('details', { className: 'cs-more' }, h('summary', null, 'Activity'),
            h('div', { className: 'cs-more-items' }, h('button', { onClick: () => setActivity('procs') }, 'All processes'), h('button', { onClick: () => setActivity('logs') }, 'System logs'))))),
      notice && h('div', { className: 'cs-notice', role: 'status' }, notice),
      h('div', { className: 'cs-layout' },
        h('aside', { className: 'cs-catalog', id: 'code-studio-catalog', ref: railRef, 'aria-label': 'Codebases and repositories', hidden: !railOpen },
          h('div', { className: 'cs-catalog-tools' }, h('label', null, h('span', { className: 'cs-sr' }, 'Find a codebase or repository'), h('input', { type: 'search', placeholder: 'Find a project…', value: query, onChange: e => setQuery(e.target.value) })),
            h('button', { className: 'cs-button cs-primary', onClick: () => begin('study'), disabled: creating }, 'Study a repository'),
            h('div', { className: 'cs-small-actions' }, h('button', { className: 'cs-button', onClick: () => begin('new'), disabled: creating }, 'New codebase'), h('button', { className: 'cs-button', onClick: refresh, disabled: loading || repoLoading || creating, 'aria-label': 'Refresh codebases and repositories' }, 'Refresh'))),
          h('div', { className: 'cs-catalog-scroll' },
            h('h3', null, 'Your codebases', h('span', null, codebases.length)),
            loading && h('p', { className: 'cs-muted', role: 'status' }, 'Loading codebases…'),
            error && h('p', { className: 'cs-error', role: 'alert' }, error),
            !loading && !error && !codebases.length && h('p', { className: 'cs-muted' }, 'Study a repository or start a new codebase.'),
            visibleCodebases.map(c => h('button', { className: 'cs-project' + (selected === codeKey(c) ? ' cs-selected' : ''), key: c.id, 'aria-pressed': selected === codeKey(c), disabled: creating, onClick: () => choose(codeKey(c), 'understand') },
              h('span', { className: 'cs-project-glyph', 'aria-hidden': true }, c.source_snapshot ? '◉' : '⌘'), h('span', null, h('strong', null, c.title), h('small', null, c.source_snapshot ? 'Study copy' : c.existing ? 'Working folder' : 'Managed codebase')))),
            h('h3', null, 'Local repositories', h('span', null, repos.length)),
            repoLoading && h('p', { className: 'cs-muted', role: 'status' }, 'Finding repositories…'),
            repoError && h('p', { className: 'cs-error', role: 'alert' }, repoError),
            !repoLoading && !repoError && !repos.length && h('p', { className: 'cs-muted' }, 'No repositories found in Projects. You can study another local folder.'),
            visibleRepos.map(r => h('button', { className: 'cs-project' + (selected === repoKey(r) ? ' cs-selected' : ''), key: r.path, 'aria-pressed': selected === repoKey(r), disabled: creating, onClick: () => choose(repoKey(r)) },
              h('span', { className: 'cs-project-glyph', 'aria-hidden': true }, '⌁'), h('span', null, h('strong', null, r.name), h('small', null, (r.branch || 'Unknown branch') + (r.dirty ? ' · ' + r.dirty + ' changed' : ''))))),
            query && !visibleCodebases.length && !visibleRepos.length && h('p', { className: 'cs-muted' }, 'No matching projects.'))),
        h('main', { className: 'cs-work' },
          form ? h('form', { className: 'cs-intake', onSubmit: create, 'aria-label': form === 'study' ? 'Study a repository' : 'New codebase' },
            h('span', { className: 'cs-eyebrow' }, form === 'study' ? 'START WITH UNDERSTANDING' : 'START WITH AN IDEA'),
            h('h3', null, form === 'study' ? 'Study a repository' : 'Create a codebase'),
            h('p', null, form === 'study' ? 'Bring a local repository into the salon as an editable source copy. Its original files, branch and history stay untouched.' : 'Start a small project with its own conversation, source files and change history.'),
            h('label', null, 'Name', h('input', { value: title, required: true, disabled: creating, onChange: e => setTitle(e.target.value), autoFocus: true, placeholder: 'What are you working on?' })),
            form === 'study' ? h('label', null, 'Local repository folder', h('input', { value: path, required: true, disabled: creating, onChange: e => setPath(e.target.value), placeholder: 'Full path to the repository' }))
              : h('label', null, 'Starting point', h('select', { value: template, disabled: creating, onChange: e => setTemplate(e.target.value) }, h('option', { value: 'static' }, 'Web page · HTML, CSS and JavaScript'), h('option', { value: 'react' }, 'React app'))),
            form === 'study' && h('p', { className: 'cs-muted' }, 'Source text only. Dependencies, binaries, generated files and credential material are omitted. A study copy is not a runnable clone or a live sync.'),
            h('div', { className: 'cs-actions' }, h('button', { className: 'cs-button cs-primary', type: 'submit', disabled: creating }, creating ? 'Preparing…' : form === 'study' ? 'Create study copy' : 'Create codebase'), h('button', { className: 'cs-button', type: 'button', disabled: creating, onClick: () => setForm(null) }, 'Cancel')))
          : codebase ? h('div', { className: 'cs-project-work', 'data-active-codebase': codebase.id },
            dirty && h('div', { className: 'cs-dirty', role: 'status' }, 'Unsaved file edit'),
            Panel ? h(Panel, { key: codebase.id + ':' + convId, codebase, convId, tab: true, workspace: true, refreshKey, wantView, onViewChange: setPanelView, onCodebaseAsk: ask, chatBusy: !chatReady || !!chat.busy, onCollapse: showChat, onDirtyChange: setFileDirty }) : h('p', { role: 'alert' }, 'The code tools could not be loaded. Refresh Friday.'))
          : repo ? h('div', { className: 'cs-repo-overview' },
            h('span', { className: 'cs-eyebrow' }, 'LOCAL REPOSITORY'), h('h3', null, 'Get to know ' + repo.name), h('p', null, 'Make a study copy to explore its structure, read source, learn its patterns and plan an adaptation with Friday.'),
            h('dl', { className: 'cs-repo-facts' }, h('dt', null, 'Branch'), h('dd', null, repo.branch || 'Unavailable'), h('dt', null, 'Working files'), h('dd', null, repo.error || (repo.dirty ? repo.dirty + ' changed' : 'Clean')), h('dt', null, 'Folder'), h('dd', null, repo.path)),
            repo.last_commit && h('p', { className: 'cs-muted' }, repo.last_commit),
            h('div', { className: 'cs-actions' }, h('button', { className: 'cs-button cs-primary', onClick: () => begin('study', repo) }, 'Study a copy'), h('button', { className: 'cs-button', onClick: () => setActivity('git') }, 'Repository Git tools')),
            h('p', { className: 'cs-muted' }, 'Git tools act on this original folder. Study actions act on a separate copy.'))
          : selected && !loading ? h('div', { className: 'cs-welcome' }, h('h3', null, 'This codebase is unavailable'), h('p', null, 'Choose a project from the list, or refresh to check again.'))
          : h('div', { className: 'cs-welcome' }, h('div', { className: 'cs-welcome-orbit', 'aria-hidden': true }, '⌘'), h('span', { className: 'cs-eyebrow' }, 'FROM CURIOSITY TO CODE'), h('h3', null, 'Understand it. Make it yours.'), h('p', null, 'Choose a codebase to explore its map and source, review changes and work with Friday in one place.'), h('div', { className: 'cs-actions' }, h('button', { className: 'cs-button cs-primary', onClick: () => begin('study') }, 'Study a repository'), h('button', { className: 'cs-button', onClick: () => begin('new') }, 'Start something new')),
              h('div', { className: 'cs-journey' }, h('span', null, h('b', null, '01'), ' Understand the structure'), h('span', null, h('b', null, '02'), ' Follow the source'), h('span', null, h('b', null, '03'), ' Shape the next change'))),
          activity && h('section', { className: 'cs-activity', 'aria-label': activity === 'git' ? 'Original repository Git tools' : 'Global activity' },
            h('header', null, h('strong', null, activity === 'git' ? 'Git · ' + (repo ? repo.name : 'choose a local repository') : activity === 'procs' ? 'All processes · across Friday' : 'System logs · across Friday'), h('button', { className: 'cs-button', onClick: () => setActivity(''), 'aria-label': 'Close activity' }, 'Close')),
            h('div', { className: 'cs-activity-body' }, activity === 'git' ? (repo && Git ? h(Git, { key: repo.path, repos: [repo], selectedRepo: repo.name, onRefresh: refresh, onLog: () => {} }) : h('p', null, 'Choose an original repository from the list to use Git tools.')) : activity === 'procs' && Procs ? h(Procs, { onLog: () => {} }) : Logs ? h(Logs) : h('p', null, 'Activity could not be loaded.')))),
        codebase && chatOpen && h('aside', { className: 'cs-conversation', id: 'code-studio-chat', ref: chatRefElement, tabIndex: -1, 'aria-label': 'Friday for ' + codebase.title },
          h('div', { className: 'cs-conversation-heading' }, h('strong', null, 'Friday alongside'), h('span', null, codebase.title)),
          !convId ? h('div', { className: 'cs-chat-placeholder' }, 'This codebase has no linked conversation. Open it from the salon to connect a conversation.')
          : binding && binding.key === selected && binding.error ? h('div', { className: 'cs-chat-placeholder', role: 'alert' }, binding.error)
          : !chatReady ? h('div', { className: 'cs-chat-placeholder', role: 'status' }, h('p', null, binding && binding.ok ? 'Open this codebase’s conversation to work with Friday.' : 'Connecting this codebase’s conversation…'), binding && binding.ok && h('button', { className: 'cs-button', onClick: showChat }, 'Open conversation'))
          : chat.surface || h('div', { className: 'cs-chat-placeholder' }, 'Friday’s conversation is loading…'))));
  }
  window.FridayCodeStudio = CodeStudio;
  window.FridayCodeStudioData = { viewFor, repoKey, codeKey };
}());
