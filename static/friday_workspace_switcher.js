/* A shared workspace switcher for the desktop, workspace tabs and chat tabs.
 * The host owns navigation; this surface selects only registered workspaces. */
(function () {
  'use strict';
  if (window.FridayWorkspaceSwitcher) return;
  const h = React.createElement;
  const { useState, useEffect, useRef } = React;
  let nextId = 0;

  function FridayWorkspaceSwitcher({ workspaces, currentWorkspace, onSelect, onOpenTab, onHome }) {
    const id = useRef(null);
    if (!id.current) id.current = 'fx-workspace-switcher-' + (++nextId);
    const rootRef = useRef(null);
    const triggerRef = useRef(null);
    const popupRef = useRef(null);
    const searchRef = useRef(null);
    const optionsRef = useRef([]);
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [active, setActive] = useState(-1);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [position, setPosition] = useState({ top: 48, left: 12, width: 340, maxHeight: 480 });
    const mounted = useRef(true);
    const registry = window.FRIDAY_WORKSPACE_REGISTRY && window.FRIDAY_WORKSPACE_REGISTRY.workspaces || [];
    const entries = (Array.isArray(workspaces) ? workspaces : []).filter(w => w && typeof w.id === 'string' && w.id).map(w => Object.assign({}, registry.find(r => r.id === w.id) || {}, w));
    const currentId = typeof currentWorkspace === 'string' ? currentWorkspace : currentWorkspace && currentWorkspace.id;
    const current = entries.find(w => w.id === currentId);
    const search = query.trim().toLocaleLowerCase();
    const filtered = entries.filter(w => !search || [w.label, w.id, w.blurb].concat(w.aliases || w.aka || []).filter(Boolean).join(' ').toLocaleLowerCase().includes(search));

    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    function close(returnFocus) {
      setOpen(false); setActive(-1); setBusy(false);
      if (returnFocus) requestAnimationFrame(() => { if (triggerRef.current && triggerRef.current.isConnected) triggerRef.current.focus(); });
    }
    function place() {
      if (!triggerRef.current) return;
      const rect = triggerRef.current.getBoundingClientRect();
      const width = Math.max(160, Math.min(360, window.innerWidth - 24));
      const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
      const top = Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - 160));
      setPosition({ top, left, width, maxHeight: Math.max(140, Math.min(560, window.innerHeight - top - 12)) });
    }
    useEffect(() => {
      if (!open) return undefined;
      place();
      const frame = requestAnimationFrame(() => { if (searchRef.current) searchRef.current.focus(); });
      const inside = node => (rootRef.current && rootRef.current.contains(node)) || (popupRef.current && popupRef.current.contains(node));
      const outside = event => { if (!inside(event.target)) close(false); };
      const escape = event => {
        if (event.key !== 'Escape') return;
        event.preventDefault(); event.stopPropagation(); close(true);
      };
      const focus = event => { if (!inside(event.target)) close(false); };
      document.addEventListener('pointerdown', outside, true);
      document.addEventListener('keydown', escape, true);
      document.addEventListener('focusin', focus);
      window.addEventListener('resize', place);
      window.addEventListener('scroll', place, true);
      return () => {
        cancelAnimationFrame(frame);
        document.removeEventListener('pointerdown', outside, true);
        document.removeEventListener('keydown', escape, true);
        document.removeEventListener('focusin', focus);
        window.removeEventListener('resize', place);
        window.removeEventListener('scroll', place, true);
      };
    }, [open]);
    useEffect(() => { setActive(-1); }, [query]);
    function focusOption(index) {
      if (!filtered.length) return;
      const next = Math.max(0, Math.min(index, filtered.length - 1));
      setActive(next);
      const button = optionsRef.current[next];
      if (button) { button.focus(); button.scrollIntoView({ block: 'nearest' }); }
    }
    function listKeys(event, index) {
      if (event.key === 'ArrowDown') { event.preventDefault(); focusOption((index + 1) % filtered.length); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); focusOption(index <= 0 ? filtered.length - 1 : index - 1); }
      else if (event.key === 'Home') { event.preventDefault(); focusOption(0); }
      else if (event.key === 'End') { event.preventDefault(); focusOption(filtered.length - 1); }
      else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key !== ' ') {
        event.preventDefault(); setQuery(query + event.key); if (searchRef.current) searchRef.current.focus();
      }
    }
    async function choose(workspace, event) {
      if (busy || !entries.some(w => w.id === workspace.id)) return;
      setError('');
      const separate = !!(event && (event.ctrlKey || event.metaKey || event.button === 1));
      if (separate && workspace.tab === false) {
        setError(workspace.label + ' opens inside the current Friday window. Select it without the new-tab modifier.');
        return;
      }
      const handler = separate ? onOpenTab : onSelect;
      if (!handler) { setError('This workspace cannot be opened from here yet.'); return; }
      setBusy(true);
      try {
        const result = await handler(workspace.id);
        if (!mounted.current) return;
        if (result === false) { setError(separate ? 'The new tab could not open. Allow pop-ups for this address and try again.' : 'The workspace could not open. Try again.'); setBusy(false); return; }
        close(true);
      } catch (e) {
        if (mounted.current) { setError(e.message || 'The workspace could not open.'); setBusy(false); }
      }
    }
    const toggle = () => {
      if (open) close(true);
      else { setQuery(''); setError(''); setActive(-1); setOpen(true); }
    };
    async function home() {
      if (!onHome || busy) return;
      setError(''); setBusy(true);
      try { await onHome(); if (mounted.current) close(true); }
      catch (e) { if (mounted.current) { setError(e.message || 'The desktop could not open.'); setBusy(false); } }
    }
    const popover = open && h('div', {
      ref: popupRef, id: id.current, className: 'fx-switcher-popover', role: 'dialog', 'aria-label': 'Switch workspace',
      onKeyDown: event => {
        // Arrow keys belong to this dialog; typing in its search keeps native
        // caret movement without stepping the desktop scene behind it.
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') event.stopPropagation();
      },
      style: { position: 'fixed', top: position.top, left: position.left, width: position.width, maxHeight: position.maxHeight, zIndex: 10000, overflow: 'auto' }
    }, h('div', { className: 'fx-switcher-heading' }, h('strong', null, 'Switch workspace'),
      h('button', { type: 'button', className: 'fx-switcher-close', onClick: () => close(true), 'aria-label': 'Close workspace switcher', title: 'Close (Escape)' }, '×')),
    h('input', { ref: searchRef, className: 'fx-switcher-search', type: 'search', value: query, placeholder: 'Find a workspace…', 'aria-label': 'Find a workspace', autoComplete: 'off', spellCheck: false,
      onChange: e => setQuery(e.target.value), onFocus: () => setActive(-1), onKeyDown: e => {
        if (e.key === 'ArrowDown') { e.preventDefault(); focusOption(0); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); focusOption(filtered.length - 1); }
        else if (e.key === 'Enter' && filtered.length) { e.preventDefault(); choose(filtered[active >= 0 ? active : 0], e); }
      } }),
    error && h('p', { className: 'fx-switcher-error', role: 'alert' }, error),
    onHome && h('button', { type: 'button', className: 'fx-switcher-home', onClick: home, disabled: busy }, '← Back to desktop'),
    h('div', { className: 'fx-switcher-list', 'aria-label': 'Workspaces' }, filtered.length ? filtered.map((w, index) => {
      const imageName = /^[a-z0-9_-]+$/i.test(w.icon || '') ? w.icon : 'code';
      return h('button', { type: 'button', key: w.id, ref: el => { optionsRef.current[index] = el; }, className: 'fx-switcher-option',
        'aria-current': currentId === w.id ? 'page' : undefined, 'data-active': active === index ? 'true' : 'false', disabled: busy,
        onFocus: () => setActive(index), onKeyDown: e => listKeys(e, index), onClick: e => choose(w, e),
        onAuxClick: e => { if (e.button === 1) { e.preventDefault(); choose(w, e); } }, onMouseDown: e => { if (e.button === 1) e.preventDefault(); }
      }, h('img', { className: 'fx-switcher-icon', src: '/assets/icons/' + imageName + '.svg', alt: '', width: 28, height: 28 }),
      h('span', { className: 'fx-switcher-copy' }, h('strong', null, w.label || w.id), w.blurb && h('small', null, w.blurb)),
      currentId === w.id && h('span', { className: 'fx-switcher-current' }, 'Current'));
    }) : h('p', { className: 'fx-switcher-empty', role: 'status' }, 'No workspace matches “' + query + '”.')),
    h('p', { className: 'fx-switcher-hint' }, 'Enter opens here · Ctrl/⌘-click opens a new tab'));
    return h('div', { className: 'fx-switcher', ref: rootRef },
      h('button', { type: 'button', className: 'fx-switcher-trigger', ref: triggerRef, onClick: toggle, title: 'Switch workspace', 'aria-label': 'Switch workspace' + (current ? ', current: ' + current.label : ''), 'aria-expanded': open, 'aria-haspopup': 'dialog', 'aria-controls': open ? id.current : undefined },
        h('span', null, current ? current.label : 'Workspaces'), h('svg', { width: 12, height: 12, viewBox: '0 0 12 12', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, 'aria-hidden': true }, h('path', { d: 'm3 4.5 3 3 3-3' }))),
      popover && ReactDOM.createPortal(popover, document.body));
  }
  window.FridayWorkspaceSwitcher = FridayWorkspaceSwitcher;
})();
