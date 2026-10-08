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

  function homeStamp(value, clock) {
    if (!Number.isFinite(Number(value)) || Number(value) <= 0) return 'Not checked yet';
    const date = new Date(Number(value) * 1000), minutes = Math.max(0, Math.floor((clock - date.getTime()) / 60000));
    return minutes < 1 ? 'Just now' : minutes < 60 ? minutes + ' min ago' : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }
  function homePayload(card) {
    return { id: card.id, title: card.title, body: card.body || '', priority: Number.isInteger(card.priority) ? card.priority : 50, actions: card.actions || [] };
  }
  function homeContent(card) {
    const source = card.source || {};
    return JSON.stringify({ ...homePayload(card), source: { label: source.label, status: source.status, detail: source.detail, updated_at: source.updated_at }, tracking: card.tracking });
  }
  function validatedHomeBoard(data) {
    const object = value => value && typeof value === 'object' && !Array.isArray(value);
    const text = (value, limit, empty = true) => typeof value === 'string' && value.length <= limit && (empty || !!value.trim());
    const stamp = value => value == null || typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 8640000000000;
    const optionalText = (value, limit) => value == null || text(value, limit);
    const source = value => object(value) && text(value.kind, 40, false) && optionalText(value.id, 500) && optionalText(value.label, 256) && optionalText(value.status, 64) && optionalText(value.detail, 4000) && stamp(value.checked_at) && stamp(value.updated_at);
    const action = value => object(value) && text(value.label, 60, false) && (value.workspace == null ? ['projects', 'activity'].includes(value.view) : value.view == null && text(value.workspace, 64, false));
    const card = value => object(value) && text(value.id, 64, false) && text(value.title, 120, false) && text(value.body, 2000) && Number.isInteger(value.priority) && value.priority >= 0 && value.priority <= 100 &&
      ['saved', 'automatic', 'tracked'].includes(value.origin) && Array.isArray(value.actions) && value.actions.length <= 3 && value.actions.every(action) &&
      (value.source == null || source(value.source)) && ['pinned', 'dismissed', 'expired'].every(key => value[key] == null || typeof value[key] === 'boolean') &&
      (value.order == null || Number.isSafeInteger(value.order) && value.order >= 0) && ['created_at', 'updated_at', 'snoozed_until', 'expires_at'].every(key => stamp(value[key])) &&
      (value.tracking == null || object(value.tracking) && typeof value.tracking.enabled === 'boolean' && object(value.tracking.scope) && text(value.tracking.scope.kind, 40, false) && text(value.tracking.scope.id, 500, false) && stamp(value.tracking.stopped_at));
    const next = object(data) && (data.board || data);
    let valid = object(next) && next.status === 'ok' && Number.isSafeInteger(next.revision) && next.revision >= 0 && typeof next.generated_at === 'number' && stamp(next.generated_at) &&
      // Legacy stores can retain 256 preferences, 256 ordered IDs and 24 durable cards.
      Array.isArray(next.cards) && Array.isArray(next.hidden_cards) && next.cards.length + next.hidden_cards.length <= 536 && next.cards.every(card) && next.hidden_cards.every(card) &&
      Array.isArray(next.sources) && next.sources.length <= 12 && next.sources.every(item => source(item) && text(item.label, 256, false) && Array.isArray(item.options) && item.options.length <= 200 && item.options.every(option => object(option) && text(option.id, 500, false) && text(option.label, 256, false) && optionalText(option.status, 64) && (option.available == null || typeof option.available === 'boolean')) && new Set(item.options.map(option => option.id)).size === item.options.length) &&
      (next.summary == null || object(next.summary) && (next.summary.private == null || typeof next.summary.private === 'boolean'));
    if (valid) valid = new Set(next.cards.concat(next.hidden_cards).map(item => item.id)).size === next.cards.length + next.hidden_cards.length && new Set(next.sources.map(item => item.kind)).size === next.sources.length;
    if (!valid) throw new Error('Home returned an incomplete board. Your previous cards and drafts are kept.');
    return next;
  }
  function homePresentation(board) {
    return JSON.stringify({ cards: board.cards.concat(board.hidden_cards).map(card => [card.id, homeContent(card), card.pinned, card.order, card.dismissed, card.snoozed_until, card.hidden_reason]), visible: board.cards.map(card => card.id),
      sources: board.sources.map(source => [source.kind, source.label, source.status, source.detail, source.options]) });
  }
  function privateHomeBoard(board) {
    return { ...board, cards: board.cards.filter(card => card.origin === 'saved'), hidden_cards: board.hidden_cards.filter(card => card.origin === 'saved'),
      sources: (board.sources || []).map(source => ({ kind: source.kind, label: source.label, status: 'paused', options: [], detail: 'Personal activity is hidden while off the record.' })), summary: { ...board.summary, private: true } };
  }
  function HomeCards({ api, active, onNavigate, onView, workspaces, clock, draftCache, privacyContext, mutationContext }) {
    const [board, setBoard] = useState(null), [failure, setFailure] = useState(''), [actionFailure, setActionFailure] = useState('');
    const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
    const [revision, setRevision] = useState(0), [tab, setTab] = useState('today');
    const [page, setPage] = useState(0), [perPage, setPerPage] = useState(2), [reading, setReading] = useState(null);
    const [editor, setEditorState] = useState(() => draftCache.current), [notice, setNotice] = useState('');
    const setEditor = value => { const next = typeof value === 'function' ? value(draftCache.current) : value; draftCache.current = next; setEditorState(next); };
    const [discardEditor, setDiscardEditor] = useState(false), [updatesReady, setUpdatesReady] = useState(false);
    const [undo, setUndo] = useState(null), [showSources, setShowSources] = useState(false);
    const [removeCandidate, setRemoveCandidate] = useState(null);
    const [resetSuggestions, setResetSuggestions] = useState(false);
    const [privacyOn, setPrivacyOn] = useState(!!window.__fridayOffRecord);
    const [needsConfirmation, setNeedsConfirmation] = useState(mutationContext.current.uncertain);
    const operation = useRef(false), generation = useRef(0), currentBoard = useRef(null);
    const mutationController = useRef(null);
    const displayedBoard = useRef(null), pendingBoard = useRef(null), pendingReadError = useRef(''), readFailure = useRef(''), pointerHeld = useRef(false);
    const live = useRef(true), dragged = useRef(null), heading = useRef(null), opener = useRef(null), boardElement = useRef(null);
    const host = useRef({ api }); host.current = { api };
    const interactionHeld = () => {
      const root = boardElement.current, target = window.FridayHandCursor?.locked?.el;
      return !!(dragged.current || pointerHeld.current || root?.querySelector('.fx-board-card-menu[open]') ||
        root?.contains(document.activeElement) && document.activeElement?.closest('[data-card-id], .fx-board-reader') ||
        window.FridayTracking?.hand?.seen && target && root?.contains(target));
    };
    const showReadFailure = message => { readFailure.current = message; setFailure(message); };
    const display = next => {
      displayedBoard.current = next; pendingBoard.current = null; setBoard(next); setUpdatesReady(false); showReadFailure('');
      if (!mutationContext.current.uncertain) setNeedsConfirmation(false);
    };
    const applyPending = explicit => {
      if ((!pendingBoard.current && !pendingReadError.current) || operation.current || !explicit && interactionHeld()) return;
      if (explicit) boardElement.current?.querySelectorAll('.fx-board-card-menu[open]').forEach(menu => { menu.open = false; });
      if (pendingBoard.current) display(pendingBoard.current);
      if (pendingReadError.current) { showReadFailure(pendingReadError.current); pendingReadError.current = ''; }
      setUpdatesReady(false);
    };
    const settleInteraction = () => requestAnimationFrame(() => { if (live.current) applyPending(false); });
    const accept = (data, explicit = false, confirmedRead = false) => {
      let next = validatedHomeBoard(data);
      const recoveredWrite = confirmedRead && mutationContext.current.uncertain;
      if (confirmedRead) mutationContext.current.uncertain = false;
      pendingReadError.current = '';
      const privateMode = window.__fridayOffRecord || next.summary?.private;
      if (privateMode) { next = privateHomeBoard(next); setReading(current => current?.origin === 'saved' ? current : null); }
      currentBoard.current = next;
      if (!explicit && !privateMode && displayedBoard.current && interactionHeld() && (pendingBoard.current || recoveredWrite || readFailure.current || homePresentation(next) !== homePresentation(displayedBoard.current))) { pendingBoard.current = next; setUpdatesReady(true); }
      else display(next);
      setLoading(false);
    };
    useEffect(() => { live.current = true; return () => { live.current = false; generation.current += 1; mutationController.current?.abort(); }; }, []);
    useEffect(() => {
      const release = () => { pointerHeld.current = false; settleInteraction(); };
      const blur = () => { pointerHeld.current = false; dragged.current = null; settleInteraction(); };
      window.addEventListener('pointerup', release); window.addEventListener('pointercancel', release); window.addEventListener('mouseup', release); window.addEventListener('blur', blur);
      return () => { window.removeEventListener('pointerup', release); window.removeEventListener('pointercancel', release); window.removeEventListener('mouseup', release); window.removeEventListener('blur', blur); };
    }, []);
    useEffect(() => { if (!updatesReady) return undefined; const timer = setInterval(() => applyPending(false), 150); return () => clearInterval(timer); }, [updatesReady]);
    useEffect(() => {
      if (!boardElement.current || typeof ResizeObserver !== 'function') return undefined;
      const measure = () => { const size = boardElement.current?.getBoundingClientRect(); if (size) setPerPage(size.width >= 620 ? (size.height >= 640 ? 4 : 2) : 1); };
      const observer = new ResizeObserver(measure); observer.observe(boardElement.current); measure();
      return () => observer.disconnect();
    }, []);
    useEffect(() => {
      if (!active) return undefined;
      let stopped = false, pending = false, queued = false;
      const controller = new AbortController();
      const refresh = async () => {
        if (stopped || document.hidden) return;
        if (pending || mutationContext.current.pending) { queued = true; return; }
        pending = true;
        const started = generation.current;
        try {
          const response = await host.current.api('/api/desktop/board', { cache: 'no-store', signal: controller.signal });
          const data = await response.json();
          if (!response.ok || data.status !== 'ok') throw new Error(data.message || data.error || (response.status === 401 || response.status === 403 ? 'Sign in again to read your Home board.' : 'Home could not refresh.'));
          if (!stopped && started === generation.current && !mutationContext.current.pending) {
            accept(data, false, true);
          }
        } catch (error) {
          if (!stopped && started === generation.current) {
            const message = error.message || 'Home could not refresh.';
            if (displayedBoard.current && interactionHeld()) { pendingReadError.current = message; setUpdatesReady(true); }
            else showReadFailure(message);
            setLoading(false);
          }
        } finally {
          pending = false;
          if (queued && !stopped && !mutationContext.current.pending) { queued = false; refresh(); }
        }
      };
      refresh();
      const timer = setInterval(refresh, 30000);
      const events = ['friday:desktop-cards-changed', 'friday:home-refresh', 'focus', 'online'];
      const privacy = event => {
        generation.current += 1; setPrivacyOn(!!event.detail?.on);
        pendingReadError.current = ''; mutationController.current?.abort();
        if (mutationContext.current.uncertain) setNeedsConfirmation(true);
        if (event.detail?.on) {
          if (currentBoard.current) { currentBoard.current = privateHomeBoard(currentBoard.current); display(currentBoard.current); }
          setReading(current => current?.origin === 'saved' ? current : null);
        }
        refresh();
      };
      events.forEach(name => window.addEventListener(name, refresh));
      window.addEventListener('friday:off-record', privacy);
      document.addEventListener('visibilitychange', refresh);
      return () => { stopped = true; controller.abort(); clearInterval(timer); events.forEach(name => window.removeEventListener(name, refresh)); window.removeEventListener('friday:off-record', privacy); document.removeEventListener('visibilitychange', refresh); };
    }, [active, revision, Math.floor(clock / 60000)]);
    useEffect(() => { if (reading || editor) heading.current?.focus(); }, [reading && reading.id, !!editor]);
    const cards = board?.cards || [], hidden = board?.hidden_cards || [];
    const later = hidden.filter(card => !card.dismissed && (card.hidden_reason === 'snoozed' || !card.hidden_reason && Number(card.snoozed_until) > 0));
    const hiddenRows = hidden.filter(card => !later.includes(card));
    const rows = tab === 'later' ? later : tab === 'hidden' ? hiddenRows : cards;
    const lastPage = Math.max(0, Math.ceil(rows.length / perPage) - 1), currentPage = Math.min(page, lastPage);
    const shown = rows.slice(currentPage * perPage, (currentPage + 1) * perPage);
    const sources = Array.isArray(board?.sources) ? board.sources : [];
    const confirmed = currentBoard.current || board;
    const latest = reading && [...(confirmed?.cards || []), ...(confirmed?.hidden_cards || [])].find(card => card.id === reading.id);
    const visibleReadingCard = reading && [...cards, ...hidden].find(card => card.id === reading.id);
    const readerChanged = reading && latest && homeContent(latest) !== homeContent(reading);
    const paused = privacyOn || !!board?.summary?.private;
    async function mutate(op, values, after, expected) {
      if (operation.current || mutationContext.current.pending || !currentBoard.current) return false;
      if (mutationContext.current.uncertain) { setNeedsConfirmation(true); return false; }
      if (window.__fridayOffRecord || currentBoard.current.summary?.private) { setActionFailure('Off the record is on. Home changes are paused because cards are saved to disk.'); return false; }
      const ticket = ++generation.current, privacyEpoch = privacyContext.current.epoch;
      const controller = new AbortController(); mutationController.current = controller;
      let timer, timedOut = false, confirmedRefusal = false;
      mutationContext.current.pending = true; mutationContext.current.uncertain = true;
      operation.current = true; setBusy(true); setActionFailure(''); setNotice('');
      try {
        const deadline = new Promise((_, reject) => {
          controller.signal.addEventListener('abort', () => reject(new Error(timedOut ? 'Home did not confirm the change within 30 seconds.' : 'The change was interrupted before confirmation.')), { once: true });
          timer = setTimeout(() => { timedOut = true; controller.abort(); }, 30000);
        });
        const request = (async () => {
          const response = await host.current.api('/api/desktop/board', { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ op, expected_revision: expected == null ? currentBoard.current.revision : expected, ...values }) });
          return { response, data: await response.json() };
        })();
        const { response, data } = await Promise.race([request, deadline]);
        if (!response.ok || data?.status !== 'ok') {
          confirmedRefusal = [400, 401, 403, 404, 409, 422].includes(response.status) && data?.status === 'error';
          throw new Error(data?.message || data?.error || (response.status === 409 ? 'Home changed while you were working. Review the latest cards and try again; your draft is kept.' : 'The change was not confirmed. Your cards are kept.'));
        }
        if (!live.current || ticket !== generation.current || privacyEpoch !== privacyContext.current.epoch || window.__fridayOffRecord) return false;
        accept(data, true); mutationContext.current.uncertain = false; setNeedsConfirmation(false);
        if (after) after(); return true;
      } catch (error) {
        if (confirmedRefusal) mutationContext.current.uncertain = false;
        if (live.current && ticket === generation.current && privacyEpoch === privacyContext.current.epoch) {
          setNeedsConfirmation(mutationContext.current.uncertain); setActionFailure(error.message || 'The change was not confirmed.');
        }
        return false;
      } finally {
        clearTimeout(timer); mutationController.current = null;
        operation.current = false; mutationContext.current.pending = false;
        if (live.current) { setBusy(false); setRevision(n => n + 1); }
        else window.dispatchEvent(new Event('friday:home-refresh'));
      }
    }
    function changeTab(value) { setTab(value); setPage(0); }
    const editorDirty = editor && (editor.original ? JSON.stringify(homePayload(editor.card)) !== editor.original : !!(editor.card.title || editor.card.body || editor.sourceKind || editor.sourceId));
    function closePanel(discard = false) {
      if (discard !== true && editorDirty) { setDiscardEditor(true); return; }
      setReading(null); setEditor(null); setDiscardEditor(false); setRemoveCandidate(null); requestAnimationFrame(() => { if (!live.current) return; (opener.current?.isConnected ? opener.current : boardElement.current)?.focus(); applyPending(false); });
    }
    function read(card, event) { opener.current = event.currentTarget; setReading({ ...card }); setEditor(null); }
    function edit(card, event) {
      if (card) {
        const current = [...(currentBoard.current?.cards || []), ...(currentBoard.current?.hidden_cards || [])].find(item => item.id === card.id);
        if (!current || current.origin !== 'saved' || current.tracking) { setActionFailure('This card is no longer available to edit. Apply the latest board updates.'); return; }
        card = current;
      }
      opener.current = event?.currentTarget || null;
      setEditor(card ? { mode: 'note', card: homePayload(card), revision: board.revision, original: JSON.stringify(homePayload(card)), privacyEpoch: privacyContext.current.epoch }
        : { mode: 'note', card: { id: 'note-' + (window.crypto?.randomUUID?.() || Date.now().toString(36)), title: '', body: '', priority: 50, actions: [] }, revision: board.revision, privacyEpoch: privacyContext.current.epoch });
      setDiscardEditor(false);
      setReading(null);
    }
    function reorder(id, to) {
      const from = cards.findIndex(card => card.id === id);
      if (from < 0 || to < 0 || to >= cards.length || from === to) return;
      if (!!cards[from].pinned !== !!cards[to].pinned) { setNotice('Pinned cards stay first. Pin or unpin the card to change its group.'); return; }
      const ids = cards.map(card => card.id); ids.splice(to, 0, ids.splice(from, 1)[0]);
      void mutate('reorder', { ids }, () => {
        setNotice('Card order saved.');
        const position = currentBoard.current.cards.findIndex(card => card.id === id);
        if (position >= 0) setPage(Math.floor(position / perPage));
        requestAnimationFrame(() => Array.from(boardElement.current?.querySelectorAll('[data-card-id]') || []).find(element => element.dataset.cardId === id)?.querySelector('.fx-board-drag')?.focus());
      });
    }
    function dismiss(card) { void mutate('dismiss', { id: card.id }, () => { setUndo({ id: card.id, label: 'Undo dismiss' }); setNotice('Card dismissed. Its source is unchanged.'); if (reading?.id === card.id) closePanel(); }); }
    function snooze(card, tomorrow) {
      const until = new Date(clock);
      if (tomorrow) { until.setDate(until.getDate() + 1); until.setHours(9, 0, 0, 0); } else until.setHours(until.getHours() + 1);
      void mutate('snooze', { id: card.id, until: Math.floor(until.getTime() / 1000) }, () => { setUndo({ id: card.id, label: 'Undo snooze' }); setNotice(tomorrow ? 'Card set aside until tomorrow at 9:00.' : 'Card set aside for one hour.'); if (reading?.id === card.id) closePanel(); });
    }
    function sourceLine(card) {
      const source = card.source || {}, tracked = card.tracking, sourceCheck = source.kind && source.kind !== 'saved';
      const status = tracked && !tracked.enabled ? 'Tracking stopped · saved snapshot' : source.status && !['ok', 'ready', 'available', 'saved'].includes(source.status) ? String(source.status).replace(/_/g, ' ') : '';
      return h('div', { className: 'fx-board-provenance' },
        h('span', { className: 'fx-board-source' }, source.label || (card.origin === 'saved' ? 'Your card' : 'Local activity')),
        status && h('span', { className: 'fx-board-source-state' }, status),
        h('span', { title: sourceCheck ? 'When the source was last checked' : 'When this card was saved' }, (sourceCheck ? 'Checked ' : 'Saved ') + homeStamp(sourceCheck ? source.checked_at : card.updated_at, clock)),
        sourceCheck && source.updated_at ? h('span', { title: 'When the source content last changed' }, 'Updated ' + homeStamp(source.updated_at, clock)) : null);
    }
    function actionsFor(card) {
      return (card.actions || []).map((item, i) => {
        const nativeView = ['projects', 'activity'].includes(item.view), available = nativeView || workspaces.some(ws => ws.id === item.workspace);
        return action(item.label, () => nativeView ? onView(item.view) : onNavigate(item.workspace), { key: i, className: 'fx-action-secondary', disabled: !available, title: available ? undefined : 'This workspace is unavailable' });
      });
    }
    function controls(card, index, expanded) {
      const isHidden = hidden.some(row => row.id === card.id), tracking = card.tracking, removable = card.origin === 'saved' && !tracking || tracking && !tracking.enabled;
      return h('div', { className: 'fx-board-card-controls' },
        isHidden ? action('Show again', () => void mutate('restore', { id: card.id }, () => setNotice('Card returned to Today.')), { className: 'fx-action-secondary', disabled: busy })
          : action(card.pinned ? 'Unpin' : 'Pin', () => void mutate('pin', { id: card.id, pinned: !card.pinned }), { className: 'fx-action-secondary', disabled: busy, 'aria-pressed': !!card.pinned, 'aria-label': (card.pinned ? 'Unpin ' : 'Pin ') + card.title }),
        h('details', { className: 'fx-board-card-menu', onToggle: settleInteraction }, h('summary', { 'aria-label': 'Options for ' + card.title }, 'More'),
          h('div', null,
            !isHidden && h(React.Fragment, null,
              action('Move earlier', () => reorder(card.id, index - 1), { className: 'fx-action-secondary', disabled: busy || index <= 0 || !!cards[index - 1]?.pinned !== !!card.pinned }),
              action('Move later', () => reorder(card.id, index + 1), { className: 'fx-action-secondary', disabled: busy || index < 0 || index === cards.length - 1 || !!cards[index + 1]?.pinned !== !!card.pinned }),
              action('Snooze for 1 hour', () => snooze(card, false), { className: 'fx-action-secondary', disabled: busy }),
              action('Snooze until tomorrow', () => snooze(card, true), { className: 'fx-action-secondary', disabled: busy }),
              action('Dismiss card', () => dismiss(card), { className: 'fx-action-secondary', disabled: busy })),
            card.origin === 'saved' && !tracking && action('Edit card', event => edit(card, event), { className: 'fx-action-secondary', disabled: busy }),
            tracking?.enabled && action('Stop tracking', () => void mutate('stop_tracking', { id: card.id }, () => { setNotice('Tracking stopped. The last snapshot is kept; the task or routine continues.'); setReading(null); }), { className: 'fx-action-secondary', disabled: busy }),
            tracking && !tracking.enabled && action('Resume tracking', () => void mutate('track', { source: tracking.scope }, () => { setNotice('Tracking resumed from local activity.'); setReading(null); }), { className: 'fx-action-secondary', disabled: busy }),
            removable && (removeCandidate === card.id ? h('div', { className: 'fx-board-delete', role: 'group', 'aria-label': 'Confirm card deletion' },
              h('p', { className: 'fx-meta' }, 'Delete this ' + (tracking ? 'snapshot' : 'saved card') + ' permanently? It cannot be restored. The source is unchanged.'),
              action('Delete permanently', () => void mutate('remove', { id: card.id }, () => { setRemoveCandidate(null); setUndo(null); setNotice('Card deleted. Its source is unchanged.'); if (reading?.id === card.id) closePanel(); }), { className: 'fx-action-secondary', disabled: busy }),
              action('Keep card', () => setRemoveCandidate(null), { className: 'fx-action-secondary', disabled: busy }))
              : action(tracking ? 'Delete stopped snapshot' : 'Delete saved card', () => setRemoveCandidate(card.id), { className: 'fx-action-secondary', disabled: busy })),
            tracking && h('p', { className: 'fx-meta' }, 'Tracking updates this card while Home is open. Stopping keeps its last snapshot and leaves the source running.'))),
        !expanded && action('Read card', event => read(card, event), { className: 'fx-action-secondary', 'aria-label': 'Read card: ' + card.title }));
    }
    async function saveEditor(event) {
      event.preventDefault(); if (!editor || busy || discardEditor || editor.privacyEpoch !== privacyContext.current.epoch) return;
      const submitted = editor;
      if (editor.mode === 'track') {
        if (!editor.sourceKind || !editor.sourceId || !selectedOption || selectedOption.available === false) return;
        await mutate('track', { source: { kind: editor.sourceKind, id: editor.sourceId } }, () => { setEditor(current => current === submitted ? null : current); setNotice('Added to Home. Updates come from local activity while Home is open.'); });
      } else {
        const current = [...currentBoard.current.cards, ...currentBoard.current.hidden_cards].find(card => card.id === editor.card.id);
        if (editor.original && (!current || JSON.stringify(homePayload(current)) !== editor.original)) {
          setActionFailure('This card changed elsewhere. Your draft is kept. Close and reopen it to review the newer version.'); return;
        }
        await mutate('save', { card: editor.card }, () => { setEditor(current => current === submitted ? null : current); setNotice('Card saved.'); });
      }
    }
    const selectedSource = sources.find(source => source.kind === editor?.sourceKind);
    const selectedOption = selectedSource?.options?.find(option => option.id === editor?.sourceId);
    const editCurrent = editor && [...(confirmed?.cards || []), ...(confirmed?.hidden_cards || [])].find(card => card.id === editor.card?.id);
    const editChanged = editor?.original && (!editCurrent || JSON.stringify(homePayload(editCurrent)) !== editor.original);
    const editPrivacyChanged = editor && editor.privacyEpoch !== privacyContext.current.epoch;
    return h('section', { ref: boardElement, tabIndex: -1, className: 'fx-working-board', 'aria-label': 'Home cards', 'aria-busy': loading || busy,
      onPointerDownCapture: () => { pointerHeld.current = true; }, onMouseDownCapture: () => { pointerHeld.current = true; }, onBlurCapture: settleInteraction,
      onKeyDown: event => { if ((reading || editor || showSources) && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) { if (discardEditor) setDiscardEditor(false); else if (showSources) setShowSources(false); else closePanel(); } } } },
      h('div', { className: 'fx-board-heading' }, h('div', null, h('h2', null, 'Your working day'), h('p', { className: 'fx-meta' }, 'Local activity, things to follow, and space for your next idea.')),
        h('div', { className: 'fx-board-tools' }, action('Add card', event => { setShowSources(false); edit(null, event); }, { disabled: busy || paused || !board || !!editor }), action('Refresh', () => setRevision(n => n + 1), { className: 'fx-action-secondary', disabled: busy }), action(showSources ? 'Close sources' : 'Sources', () => setShowSources(value => !value), { className: 'fx-action-secondary', 'aria-expanded': showSources }),
          action('Updates ready · Apply', () => applyPending(true), { className: 'fx-action-secondary fx-board-pending', disabled: busy || !updatesReady, 'aria-hidden': !updatesReady, tabIndex: updatesReady ? 0 : -1, style: { visibility: updatesReady ? 'visible' : 'hidden' } }))),
      paused && h('p', { className: 'fx-board-notice', role: 'status' }, 'Off the record: personal activity is hidden and saved-card changes are paused.'),
      needsConfirmation && h('p', { className: 'fx-board-notice', role: 'status' }, 'The last change may have saved. Refresh Home to confirm its state before making another change. Your draft is kept.'),
      (failure || actionFailure) && h('div', { className: 'fx-board-alert', role: 'alert' }, h('span', null, actionFailure || failure, board ? ' Showing the last confirmed board.' : ' Your board has not loaded yet.'), action('Refresh cards', () => setRevision(n => n + 1), { className: 'fx-action-secondary', disabled: busy }), actionFailure && action('Dismiss message', () => setActionFailure(''), { className: 'fx-action-secondary' })),
      notice && h('div', { className: 'fx-board-notice', role: 'status' }, notice,
        undo && action(undo.label, () => void mutate('restore', { id: undo.id }, () => { setUndo(null); setNotice('Card restored.'); }), { className: 'fx-action-secondary', disabled: busy })),
      showSources ? h('div', { className: 'fx-board-sources', 'aria-label': 'Home sources', tabIndex: 0 }, h('p', { className: 'fx-meta' }, 'Home reads the information already available to Friday. It does not start a background monitor or refresh a remote account.'),
        board?.summary?.suggestion_capacity_full === true && h('p', { className: 'fx-board-notice' }, 'Home has reached its saved-choice limit. Reset automatic suggestion choices below, or delete a saved card or stopped snapshot, to make room. Saved, tracked and pinned cards are kept when you reset.'),
        sources.length ? sources.map(source => h('div', { className: 'fx-board-source-row', key: source.kind }, h('strong', null, source.label), h('span', null, String(source.status || 'unknown').replace(/_/g, ' ')), h('p', null, source.detail || 'No source details available.'), h('small', null, 'Checked ' + homeStamp(source.checked_at, clock)))) : h('p', null, loading ? 'Reading sources…' : 'Source details are unavailable.'),
        h('div', { className: 'fx-board-source-reset' }, resetSuggestions ? h(React.Fragment, null,
          h('p', { className: 'fx-meta' }, 'Dismissed automatic suggestions may return. Saved, tracked and pinned cards are kept.'),
          action('Reset automatic suggestions', () => void mutate('reset_suggestions', {}, () => { setResetSuggestions(false); setNotice('Automatic suggestion choices reset. Saved, tracked and pinned cards are kept.'); }), { className: 'fx-action-secondary', disabled: busy || paused }),
          action('Keep my choices', () => setResetSuggestions(false), { className: 'fx-action-secondary', disabled: busy }))
          : action('Reset suggestion choices…', () => setResetSuggestions(true), { className: 'fx-action-secondary', disabled: busy || paused || !board })))
      : editor ? h('form', { className: 'fx-board-editor', onSubmit: saveEditor, 'aria-label': editor.original ? 'Edit Home card' : 'Add to Home' },
        h('div', { className: 'fx-board-panel-heading' }, h('h3', { ref: heading, tabIndex: -1 }, editor.original ? 'Edit your card' : 'Add to Home'), action('Cancel', closePanel, { className: 'fx-action-secondary', disabled: busy })),
        discardEditor && h('div', { className: 'fx-board-notice', role: 'alert' }, 'Discard this unsaved card draft?',
          action('Keep editing', () => setDiscardEditor(false), { className: 'fx-action-secondary' }), action('Discard draft', () => closePanel(true), { className: 'fx-action-secondary' })),
        editPrivacyChanged && h('div', { className: 'fx-board-notice' }, 'The privacy session changed. Your draft is kept; review it before saving in this session.',
          action('Review draft for this session', () => setEditor(current => current ? { ...current, privacyEpoch: privacyContext.current.epoch } : current), { className: 'fx-action-secondary', disabled: busy || paused || discardEditor })),
        !editor.original && h('div', { className: 'fx-board-tabs', 'aria-label': 'Card type' }, [['note', 'Write a card'], ['track', 'Track something']].map(([value, label]) => action(label, () => setEditor(current => ({ ...current, mode: value })), { key: value, className: 'fx-action-secondary', 'aria-pressed': editor.mode === value, disabled: busy }))),
        editor.mode === 'track' ? h(React.Fragment, null,
          h('label', null, 'Source', h('select', { value: editor.sourceKind || '', required: true, disabled: busy, onChange: event => setEditor(current => ({ ...current, sourceKind: event.target.value, sourceId: '' })) }, h('option', { value: '' }, 'Choose a source'), sources.map(source => h('option', { key: source.kind, value: source.kind }, source.label)))),
          selectedSource && h('p', { className: 'fx-meta' }, selectedSource.detail || String(selectedSource.status || '').replace(/_/g, ' ')),
          h('label', null, 'What to follow', h('select', { value: editor.sourceId || '', required: true, disabled: busy || !selectedSource?.options?.length, onChange: event => setEditor(current => ({ ...current, sourceId: event.target.value })) }, h('option', { value: '' }, selectedSource?.options?.length ? 'Choose an item' : 'No available items'), (selectedSource?.options || []).map(option => h('option', { key: option.id, value: option.id, disabled: option.available === false }, option.label + (option.available === false ? ' (not available)' : ''))))),
          selectedSource?.options?.some(option => option.available === false) && h('p', { className: 'fx-meta' }, 'Items marked not available cannot be followed yet. Choose an available item to start tracking.'),
          h('p', { className: 'fx-meta' }, 'The card updates from local activity while Home is open. Snooze hides it temporarily; dismiss hides the card; stop tracking keeps a snapshot without changing the source.'))
          : h(React.Fragment, null,
            h('label', null, 'Title', h('input', { value: editor.card.title, maxLength: 120, required: true, disabled: busy, onChange: event => setEditor(current => ({ ...current, card: { ...current.card, title: event.target.value } })) })),
            h('label', null, 'Details', h('textarea', { value: editor.card.body, maxLength: 2000, rows: 5, disabled: busy, onChange: event => setEditor(current => ({ ...current, card: { ...current.card, body: event.target.value } })) })),
            editChanged && h('p', { className: 'fx-board-alert' }, 'This card changed elsewhere. Your draft is kept. Close and reopen it to review the newer version.')),
        h('button', { className: 'fx-action', type: 'submit', disabled: busy || paused || needsConfirmation || discardEditor || editPrivacyChanged || !!editChanged || (editor.mode === 'track' ? !selectedOption || selectedOption.available === false : !editor.card.title.trim()) }, busy ? 'Saving…' : editor.mode === 'track' ? 'Start tracking' : 'Save card'))
      : reading ? h('article', { className: 'fx-board-reader', 'aria-label': 'Reading Home card' },
        h('div', { className: 'fx-board-panel-heading' }, h('h3', { ref: heading, tabIndex: -1 }, reading.title), action('Back to cards', closePanel, { className: 'fx-action-secondary' })),
        sourceLine(reading),
        h('div', { className: 'fx-board-notice', 'aria-hidden': !readerChanged, style: { visibility: readerChanged ? 'visible' : 'hidden' } }, 'A newer version is available. Your reading position is kept.', action('Read latest version', () => { const fresh = currentBoard.current?.cards.concat(currentBoard.current.hidden_cards).find(card => card.id === reading.id); if (fresh) setReading({ ...fresh }); }, { className: 'fx-action-secondary', disabled: !readerChanged, tabIndex: readerChanged ? 0 : -1 })),
        !visibleReadingCard && h('p', { className: 'fx-board-notice' }, 'This card is no longer on the board. This reading copy is kept until you close it.'),
        h('div', { className: 'fx-board-reader-body', tabIndex: 0 }, reading.body || 'No additional details.'),
        reading.source?.detail && h('p', { className: 'fx-meta' }, reading.source.detail),
        h('div', { className: 'fx-board-card-actions' }, actionsFor(reading)), visibleReadingCard && controls(visibleReadingCard, cards.findIndex(card => card.id === visibleReadingCard.id), true))
      : h(React.Fragment, null,
        h('div', { className: 'fx-board-tabs', 'aria-label': 'Home card views' }, [['today', 'Today', cards.length], ['later', 'Later', later.length], ['hidden', 'Hidden', hiddenRows.length]].map(([value, label, count]) => action(label + ' · ' + count, () => changeTab(value), { key: value, className: 'fx-action-secondary', 'aria-pressed': tab === value }))),
        loading && !board ? h('div', { className: 'fx-board-empty', role: 'status' }, 'Gathering your working day…')
          : !shown.length ? h('div', { className: 'fx-board-empty' }, h('h3', null, failure && !board ? 'Home is unavailable' : tab === 'later' ? 'Nothing set aside' : tab === 'hidden' ? 'No hidden cards' : 'A little room for what comes next'),
            h('p', null, tab === 'later' ? 'Snoozed cards return at the time you choose.' : tab === 'hidden' ? 'Dismissed and expired cards stay here until you restore them.' : 'Add a note or choose a local task, project or routine to follow. Available activity appears here as it arrives.'), board && tab === 'today' && action('Add your first card', event => edit(null, event)))
          : h('div', { className: 'fx-board-grid', tabIndex: 0, 'aria-label': 'Cards. Scroll within this panel for details.' }, shown.map(card => {
            const index = cards.findIndex(row => row.id === card.id);
            return h('article', { key: card.id, className: 'fx-board-card', 'data-card-id': card.id, 'data-pinned': !!card.pinned,
              onDragOver: event => { if (dragged.current && tab === 'today') event.preventDefault(); },
              onDrop: event => { const id = dragged.current; dragged.current = null; if (!id || tab !== 'today') return; event.preventDefault(); event.stopPropagation(); reorder(id, index); } },
              h('div', { className: 'fx-board-card-top' }, h('span', { className: 'fx-eyebrow' }, card.pinned ? 'Pinned' : card.tracking?.enabled ? 'Following' : card.origin === 'saved' ? 'Your card' : 'From your day'),
                tab === 'today' && h('button', { type: 'button', className: 'fx-board-drag', draggable: !busy, disabled: busy, 'aria-label': 'Move ' + card.title, title: 'Drag to reorder, or use Alt + Up / Down',
                  onDragStart: event => { dragged.current = card.id; event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', card.id); }, onDragEnd: () => { dragged.current = null; pointerHeld.current = false; settleInteraction(); },
                  onKeyDown: event => { if (event.altKey && ['ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); event.stopPropagation(); reorder(card.id, index + (event.key === 'ArrowUp' ? -1 : 1)); } } }, '↕')),
              h('h3', null, card.title), h('p', { className: 'fx-board-card-body' }, card.body), sourceLine(card),
              tab === 'later' && h('p', { className: 'fx-meta' }, 'Returns ' + new Date(card.snoozed_until * 1000).toLocaleString()),
              h('div', { className: 'fx-board-card-actions' }, actionsFor(card)), controls(card, index, false));
          })),
        rows.length > perPage && h('nav', { className: 'fx-board-pages', 'aria-label': 'Home card pages' }, action('Previous cards', () => setPage(currentPage - 1), { className: 'fx-action-secondary', disabled: currentPage === 0 }), h('span', null, (currentPage * perPage + 1) + '–' + Math.min(rows.length, (currentPage + 1) * perPage) + ' of ' + rows.length), action('More cards', () => setPage(currentPage + 1), { className: 'fx-action-secondary', disabled: currentPage === lastPage }))),
      board && h('p', { className: 'fx-board-check' }, (failure ? 'Last confirmed board · ' : 'Board checked · ') + homeStamp(board.generated_at, clock), ' · Updates from local activity while Home is open.'));
  }

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
    const [homeClock, setHomeClock] = useState(() => Date.now());
    const [files, setFiles] = useState({ loading: false, error: '', items: [] });
    const [draft, setDraft] = useState('');
    const [homePrompt, setHomePrompt] = useState('');
    const [preparing, setPreparing] = useState(false);
    const homeCardEditor = useRef(null), homeCardPrivacy = useRef({ epoch: 0 }), homeCardMutation = useRef({ pending: false, uncertain: false });
    useEffect(() => {
      const changed = () => { homeCardPrivacy.current.epoch += 1; };
      window.addEventListener('friday:off-record', changed);
      return () => window.removeEventListener('friday:off-record', changed);
    }, []);
    const homeDrafts = useRef({});
    const homeDraftContext = useRef(selectedProject || 'session');
    useEffect(() => { const key=selectedProject || 'session'; if(key!==homeDraftContext.current){homeDrafts.current[homeDraftContext.current]=homePrompt;homeDraftContext.current=key;setHomePrompt(homeDrafts.current[key]||'');} }, [selectedProject]);
    const homeDraftState = useRef(null);
    homeDraftState.current = { text: homePrompt, projectId: homeDraftContext.current === 'session' ? null : homeDraftContext.current, selectedProject: selectedProject || null, preparing };
    useEffect(() => {
      if (!p.onRegisterHomeDraft) return undefined;
      const getDraft = () => {
        const draft = homeDraftState.current;
        if (!draft || draft.preparing || !draft.text.trim() || draft.projectId !== draft.selectedProject || (draft.projectId || 'session') !== homeDraftContext.current) return null;
        return { text: draft.text, projectId: draft.projectId, personal: !draft.projectId, consume: () => {
          const current = homeDraftState.current;
          const key = draft.projectId || 'session';
          if (!current || current.selectedProject !== draft.projectId || current.projectId !== draft.projectId || current.text !== draft.text || homeDraftContext.current !== key) return false;
          homeDraftState.current = { ...current, text: '' };
          setHomePrompt(value => homeDraftContext.current === key && value === draft.text ? '' : value);
          if (homeDrafts.current[key] === draft.text) delete homeDrafts.current[key];
          return true;
        } };
      };
      return p.onRegisterHomeDraft(getDraft);
    }, [p.onRegisterHomeDraft]);
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
      if (view !== 'day' || !visible || p.enabled === false) return undefined;
      const tick = () => { if (!document.hidden) setHomeClock(Date.now()); };
      tick(); const timer = setInterval(tick, 30000);
      window.addEventListener('focus', tick); document.addEventListener('visibilitychange', tick);
      return () => { clearInterval(timer); window.removeEventListener('focus', tick); document.removeEventListener('visibilitychange', tick); };
    }, [view, visible, p.enabled]);
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
      const date = new Date(homeClock), hour = date.getHours();
      const greeting = hour < 5 ? 'A quiet moment for what matters.' : hour < 12 ? 'Good morning. What comes first?' : hour < 17 ? 'Good afternoon. Keep things moving.' : 'Good evening. Make room for tomorrow.';
      async function prepare(e) {
        e.preventDefault(); if(preparing || !homePrompt.trim())return;
        const submitted=homePrompt,context=selectedProject||'session';setPreparing(true);setError('');
        try { await discuss(submitted,{personal:!materialProject});if(homeDraftContext.current===context)setHomePrompt(value=>homeDraftContext.current===context&&value===submitted?'':value);if(homeDrafts.current[context]===submitted)delete homeDrafts.current[context]; }
        catch(_) {} finally { if(alive.current)setPreparing(false); }
      }
      return h(React.Fragment,null,
        h('header', { className: 'fx-day-heading' }, h('div', null,
          h('div', { className: 'fx-eyebrow' }, date.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })),
          h('h1', null, greeting), h('p', { className: 'fx-meta' }, 'Your space. Your work. Your ' + agentName + '.')),
          h('div', { className: 'fx-day-links' }, action('Calendar', () => openWorkspace('calendar'), { className: 'fx-action-secondary' }), action('Avatars & depth', () => window.FridayHolographicWorkspace?.open(), { className: 'fx-action-secondary' }),
            approvals.length > 0 && action(approvals.length + ' decision' + (approvals.length === 1 ? '' : 's') + ' to review', () => { go('activity'); setActivityTab('needs'); }, { className: 'fx-attention-action' }))),
        h(HomeCards, { api, active: visible && p.enabled !== false, onNavigate: openWorkspace, onView: go, workspaces, clock: homeClock, draftCache: homeCardEditor, privacyContext: homeCardPrivacy, mutationContext: homeCardMutation }),
        !p.chatOpen && h('form',{className:'fx-day-composer',onSubmit:prepare},
          h('textarea',{value:homePrompt,onChange:e=>setHomePrompt(e.target.value),placeholder:'Ask, make, or pick up where you left off…','aria-label':'Ask '+agentName,rows:3,disabled:preparing}),
          h('div',{className:'fx-day-composer-footer'},h('div',{className:'fx-day-context'},
            h('select',{'aria-label':'Conversation project',value:materialProject?.id||'',disabled:preparing,onChange:e=>setSelectedProject(e.target.value)},h('option',{value:''},'Personal context'),projects.map(x=>h('option',{value:x.id,key:x.id},x.name))),
            action('+ Materials',()=>go('explore'),{className:'fx-action-secondary'})),
            h('button',{className:'fx-action',type:'submit',disabled:preparing||!homePrompt.trim()},preparing?'Preparing…':'Continue →')),
          h('small',{className:'fx-meta'},'Opens your draft in the conversation, ready to review.')),
        h('footer', { className: 'fx-day-resume' }, resume && action('Continue ' + (resume.title || 'your conversation'), () => openChat(resume), { className: 'fx-action-secondary' }),
          project ? action('Open ' + project.name, () => chooseProject(project.id), { className: 'fx-action-secondary' }) : action('Start a project', () => { go('projects'); beginProjectForm(null); }, { className: 'fx-action-secondary' }),
          action('All workspaces', () => go('workspaces'), { className: 'fx-action-secondary' })));
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
          h('div', { className: 'fx-project-tools' }, action('Project workflows', () => openWorkspace({workspace:'workflows',project_id:project.id}), { className: 'fx-action-secondary' }), action('Open Code', () => openWorkspace('code'), { className: 'fx-action-secondary' }), action('Open Media', () => openWorkspace('media'), { className: 'fx-action-secondary' }), action('Remove project…', () => setRemoveProject(true), { className: 'fx-action-secondary fx-danger', disabled: busy })),
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
      visible && h('main', { className: 'fx-main', 'data-view': view, 'data-home-board': view === 'day' ? 'true' : undefined, 'data-density': visibleShape.density, 'data-arrangement': visibleShape.arrangement, 'data-layout-preview': shapeDraft && shapeDraft.view === view ? 'true' : 'false', 'aria-label': VIEWS.find(v => v[0] === view)[1] },
        error && h('div', { className: 'fx-error', role: 'alert' }, error), notice && h('div', { className: 'fx-notice', role: 'status' }, notice),
        view === 'day' ? dayView() : view === 'projects' ? projectsView() : view === 'explore' ? exploreView() : view === 'activity' ? activityView() : workspacesView(),
        draft && h('div', { className: 'fx-draft' }, h('label', null, 'Prepared discussion', h('textarea', { readOnly: true, value: draft, rows: 6 })), action('Copy discussion', () => navigator.clipboard.writeText(draft).then(() => setNotice('Discussion copied.')).catch(() => setError('Copy was unavailable. Select the text and copy it.'))), action('Open chat', p.onShowChat, { className: 'fx-action-secondary' }), action('Dismiss', () => setDraft(''), { className: 'fx-action-secondary' }))));
  }
  window.FridayExperience = FridayExperience;
})();
