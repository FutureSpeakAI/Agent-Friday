/* Display style is a browser preference. Changing it preserves the live app. */
(function (scope) {
  'use strict';
  if (!scope || !scope.document || scope.FridayDisplayStyle) return;

  const KEY = 'friday.display-style.v1';
  const valid = value => value === 'simple' || value === 'classic';
  const listeners = new Set();
  let style = 'simple', persisted = true;
  try { const saved = scope.localStorage.getItem(KEY); if (valid(saved)) style = saved; }
  catch (_) { persisted = false; }
  try { if (new URL(scope.location.href).searchParams.get('experience') === 'classic') style = 'classic'; }
  catch (_) { /* The saved preference also works in embedded documents. */ }

  function snapshot() { return {style, persisted}; }
  function publish() {
    const detail = snapshot();
    listeners.forEach(listener => listener(detail));
    scope.dispatchEvent(new scope.CustomEvent('friday:display-style', {detail}));
  }
  function clearLegacyOverride() {
    try {
      const url = new URL(scope.location.href);
      if (url.searchParams.get('experience') === 'classic') {
        url.searchParams.delete('experience');
        scope.history.replaceState(scope.history.state, '', url.href);
      }
    } catch (_) { /* An embedded host may own its navigation history. */ }
  }
  const api = {
    get: () => style,
    getSnapshot: snapshot,
    set(value) {
      if (!valid(value)) return snapshot();
      style = value;
      try { scope.localStorage.setItem(KEY, value); persisted = true; }
      catch (_) { persisted = false; }
      clearLegacyOverride();
      publish();
      return snapshot();
    },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }
  };
  scope.FridayDisplayStyle = api;
  scope.addEventListener('storage', event => {
    if (event.key !== KEY && event.key !== null) return;
    // Ignore sessionStorage events, while tolerating storage that is blocked.
    try { if (event.storageArea && event.storageArea !== scope.localStorage) return; } catch (_) { return; }
    style = valid(event.newValue) ? event.newValue : 'simple';
    persisted = true;
    clearLegacyOverride();
    publish();
  });

  if (!scope.React || !scope.ReactDOM) return;
  const React = scope.React, h = React.createElement;
  const {useState, useRef, useEffect, useLayoutEffect} = React;
  let nextId = 0;
  const choices = [
    {id:'simple', name:'Simple', description:'A focused desktop, thoughtful cards, and room for Friday.'},
    {id:'classic', name:'Classic', description:'Your original desktop and familiar display arrangement.'},
    {id:'advanced', name:'Advanced', description:'A richer workspace experience is taking shape.', disabled:true}
  ];

  function useStyle() {
    const [value, setValue] = useState(api.getSnapshot);
    useEffect(() => {
      const unsubscribe = api.subscribe(setValue);
      setValue(api.getSnapshot());
      return unsubscribe;
    }, []);
    return value;
  }

  function FridayDisplayStyleOptions(props) {
    props = props || {};
    const value = useStyle(), refs = useRef({});
    const choose = (id, commit) => {
      const result = api.set(id);
      if (props.onSelect) props.onSelect(result, commit);
    };
    function key(event, id) {
      const enabled = choices.filter(choice => !choice.disabled).map(choice => choice.id);
      let target;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') target = enabled[(enabled.indexOf(id) + 1) % enabled.length];
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') target = enabled[(enabled.indexOf(id) + enabled.length - 1) % enabled.length];
      if (event.key === 'Home') target = enabled[0];
      if (event.key === 'End') target = enabled[enabled.length - 1];
      if (target) { event.preventDefault(); choose(target, false); refs.current[target]?.focus(); }
    }
    return h('div', {className:'friday-style-options'},
      h('div', {role:'radiogroup', 'aria-label':'Display style', className:'friday-style-choices'},
        choices.map(choice => h('button', {
          key:choice.id, ref:element => { refs.current[choice.id] = element; }, type:'button', role:'radio',
          'aria-label':choice.name, 'aria-description':choice.disabled ? 'In development' : choice.description, 'aria-checked':value.style === choice.id, disabled:choice.disabled,
          tabIndex:value.style === choice.id ? 0 : -1, 'data-display-style':choice.id,
          className:'friday-style-choice', onClick:() => choose(choice.id, true), onKeyDown:event => key(event, choice.id)
        },
        h('span', {className:'friday-style-preview', 'aria-hidden':true}, h('span', {className:'friday-style-preview-work'}), h('span', {className:'friday-style-preview-avatar'})),
        h('span', {className:'friday-style-choice-copy'}, h('span', {className:'friday-style-choice-title'}, choice.name,
          choice.disabled ? h('small', null, 'In development') : h('span', {className:'friday-style-radio', 'aria-hidden':true}, value.style === choice.id ? '✓' : '')),
        h('span', {className:'friday-style-choice-description'}, choice.description))))),
      h('p', {className:'friday-style-storage', role:'status', 'aria-live':'polite', 'data-storage-error':!value.persisted},
        value.persisted ? 'Saved in this browser. Your workspaces and conversations stay open.' : 'Applied for this tab. Browser storage is unavailable, so this choice may reset when you reload.'));
  }

  function workArea() {
    const spatial = scope.FridayHolographicWorkspace?.state?.spatial;
    if (spatial?.stage && spatial.content) return spatial.content;
    const bar = document.querySelector('.top-bar')?.getBoundingClientRect();
    const dock = document.querySelector('.dock')?.getBoundingClientRect();
    const top = Math.max(12, (bar?.bottom || 0) + 12);
    const safe = parseFloat(scope.getComputedStyle(document.documentElement).getPropertyValue('--friday-safe-bottom')) || 0;
    const bottom = dock?.height > 0 && dock.top > top ? Math.min(scope.innerHeight - safe, dock.top) : scope.innerHeight - safe;
    return {x:12, y:top, w:Math.max(1, scope.innerWidth - 24), h:Math.max(1, bottom - top - 12)};
  }

  function FridayDisplayStyleControl() {
    const value = useStyle(), [open, setOpen] = useState(false);
    const trigger = useRef(null), dialog = useRef(null), id = useRef(null), frame = useRef(0);
    if (!id.current) id.current = 'friday-display-style-' + (++nextId);
    const close = () => { dialog.current?.close(); setOpen(false); };
    useLayoutEffect(() => {
      if (!open || !dialog.current) return;
      const node = dialog.current;
      function place() {
        const area = workArea(), gap = Math.min(10, area.w / 20, area.h / 20);
        const width = Math.min(420, Math.max(1, area.w - gap * 2));
        node.style.left = (area.x + (area.w - width) / 2) + 'px';
        node.style.top = (area.y + gap) + 'px';
        node.style.width = width + 'px';
        node.style.maxHeight = Math.max(1, area.h - gap * 2) + 'px';
        node.dataset.compact = String(area.h < 460);
      }
      const layout = () => { cancelAnimationFrame(frame.current); frame.current = requestAnimationFrame(place); };
      place();
      if (!node.open) node.showModal();
      node.querySelector('[role="radio"][aria-checked="true"]')?.focus({preventScroll:true});
      scope.addEventListener('resize', layout);
      scope.addEventListener('friday:spatial-layout', layout);
      scope.addEventListener('friday:display-style', layout);
      return () => {
        cancelAnimationFrame(frame.current);
        scope.removeEventListener('resize', layout);
        scope.removeEventListener('friday:spatial-layout', layout);
        scope.removeEventListener('friday:display-style', layout);
        if (node.open) node.close();
      };
    }, [open]);
    return h(React.Fragment, null,
      h('button', {type:'button', ref:trigger, className:'friday-style-trigger', 'aria-label':'Display style: ' + (value.style === 'classic' ? 'Classic' : 'Simple'),
        'aria-haspopup':'dialog', 'aria-expanded':open, 'aria-controls':id.current, onClick:() => setOpen(true)},
        h('svg', {viewBox:'0 0 20 20', width:16, height:16, fill:'none', stroke:'currentColor', strokeWidth:1.3, 'aria-hidden':true},
          h('rect', {x:2.5, y:3.5, width:15, height:13, rx:2.5}), h('path', {d:'M2.5 7h15M12 7v9.5'})),
        h('span', null, 'Style: ', value.style === 'classic' ? 'Classic' : 'Simple')),
      open && scope.ReactDOM.createPortal(h('dialog', {
        ref:dialog, id:id.current, className:'friday-style-dialog', 'aria-labelledby':id.current + '-title',
        onCancel:event => { event.preventDefault(); close(); },
        onClose:() => {
          setOpen(false);
          const opener = trigger.current;
          if (opener?.isConnected && opener.getClientRects().length) opener.focus({preventScroll:true});
          else document.querySelector('.friday-topbar-more:not([hidden])')?.focus({preventScroll:true});
        },
        onClick:event => { if (event.target === event.currentTarget) {
          const rect = event.currentTarget.getBoundingClientRect();
          if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
        } }
      }, h('header', {className:'friday-style-heading'}, h('div', null,
        h('p', {className:'friday-style-eyebrow'}, 'Make yourself at home'),
        h('h2', {id:id.current + '-title'}, 'Display style')),
        h('button', {type:'button', 'aria-label':'Close display style', onClick:close}, '×')),
      h('p', {className:'friday-style-intro'}, 'The same Friday. A space that feels right to you.'),
      h(FridayDisplayStyleOptions, {onSelect:(result, commit) => { if (commit && result.persisted) {
        close();
        queueMicrotask(() => scope.dispatchEvent(new scope.CustomEvent('friday:display-style-selected')));
      } }})), document.body));
  }
  scope.FridayDisplayStyleControl = FridayDisplayStyleControl;
  scope.FridayDisplayStyleOptions = FridayDisplayStyleOptions;
})(typeof window === 'undefined' ? null : window);
