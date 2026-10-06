/* Native controls retain their React identity when they move into the pullout. */
(function (scope) {
  'use strict';

  function packTopbar(available, items, triggerWidth, gap) {
    const present = items.filter(item => item.width > 0);
    const total = present.reduce((sum, item) => sum + item.width, 0) + Math.max(0, present.length - 1) * gap;
    if (total <= available) return {visible: present.map(item => item.id), overflow: []};
    const brand = present.find(item => item.brand);
    const visible = new Set(brand ? [brand.id] : []);
    let remaining = Math.max(0, available - triggerWidth - (brand ? brand.width + gap : 0));
    present.filter(item => !item.brand).sort((a, b) => b.priority - a.priority || a.order - b.order).forEach(item => {
      if (item.width + gap <= remaining) { visible.add(item.id); remaining -= item.width + gap; }
    });
    return {visible: present.filter(item => visible.has(item.id)).map(item => item.id), overflow: present.filter(item => !visible.has(item.id)).map(item => item.id)};
  }

  if (typeof module === 'object' && module.exports) module.exports = {packTopbar};
  if (!scope || !scope.React || !scope.ReactDOM) return;
  const React = scope.React, h = React.createElement;
  const {useState, useRef, useLayoutEffect, useEffect} = React;
  const focusable = 'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]';

  function metadata(node, group, index) {
    const props = node.props || {}, name = typeof node.type === 'function' ? node.type.name : '';
    const text = typeof props.children === 'string' ? props.children : '';
    const label = props['aria-label'] || props.title || text;
    if (!group && !index) return {brand:true, group:'navigation', priority:1000};
    if (node.type === scope.FridayWorkspaceSwitcher) return {group:'navigation', priority:100, kind:'workspaces'};
    if (name === 'ShellTabName' || name === 'ShellChatName') return {group:'navigation', priority:85, kind:'context'};
    if (name === 'StandaloneApprovals') return {group:'controls', priority:95, kind:'approvals'};
    if (name === 'ArbiterStrip' || props.className === 'top-bar-clock' || node.type === 'span') return {group:'system', priority:props.className === 'cc-indicator' ? 99 : 5};
    if (!group) {
      const nestedControl = React.Children.toArray(props.children).find(child => React.isValidElement(child) && child.type === 'button');
      return {group:'navigation', priority:text === 'Projects' ? 75 : text === 'Activity' ? 40 : String(props.className || '').includes('depth') ? 65 : 50,
        kind:nestedControl?.props['aria-label'] === 'Scene selection' ? 'scene' : nestedControl?.props['aria-label'] === 'Model selection' ? 'model' : undefined};
    }
    return {group:'controls', priority:/chat/i.test(label) && !/fullscreen/i.test(label) ? 90 : /notification|tasks running/i.test(label) ? 80 : /settings/i.test(label) ? 35 : 25, label};
  }

  function FridayResponsiveTopBar(props) {
    const {children, className, onDismissMenus, ...hostProps} = props;
    const host = useRef(null), row = useRef(null), trigger = useRef(null), panel = useRef(null);
    const groups = useRef({}), slots = useRef(new Map()), frame = useRef(0), mounted = useRef(true);
    const requestMeasure = useRef(() => {}), dismissNative = useRef(onDismissMenus), openRef = useRef(false);
    const [open, setOpen] = useState(false), [overflowCount, setOverflowCount] = useState(0);
    dismissNative.current = onDismissMenus; openRef.current = open;
    const descriptors = [];
    React.Children.forEach(children, (group, groupIndex) => {
      if (!React.isValidElement(group)) return;
      React.Children.forEach(group.props.children, (node, index) => {
        if (!React.isValidElement(node) || (node.type === 'span' && node.props.children === '|')) return;
        const id = groupIndex + ':' + index;
        const item = Object.assign({id, node, order:descriptors.length}, metadata(node, groupIndex, index));
        if (!slots.current.has(id)) {
          const element = document.createElement('div');
          element.className = 'friday-topbar-cell'; element.dataset.topbarItem = id;
          slots.current.set(id, element);
        }
        item.element = slots.current.get(id);
        item.element.dataset.group = item.group;
        item.element.classList.toggle('friday-topbar-brand', !!item.brand);
        if (item.kind) item.element.dataset.kind = item.kind;
        descriptors.push(item);
      });
    });
    const current = useRef(descriptors); current.current = descriptors;

    function close(restoreFocus) {
      if (!openRef.current) return;
      ownedPopups().reverse().forEach(dismissPopup);
      setOpen(false); openRef.current = false;
      if (dismissNative.current) dismissNative.current();
      if (restoreFocus) trigger.current?.focus();
    }
    const closeRef = useRef(close); closeRef.current = close;

    function ownedPopups() {
      const owner = panel.current;
      if (!owner) return [];
      const visible = element => element && element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden' && !element.closest('[hidden],[inert]');
      const result = [], seen = new Set();
      owner.querySelectorAll('[aria-controls]').forEach(opener => {
        if (opener.getAttribute('aria-expanded') === 'false') return;
        String(opener.getAttribute('aria-controls') || '').split(/\s+/).forEach(id => {
          const popup = document.getElementById(id);
          if (!visible(popup) || seen.has(popup)) return;
          seen.add(popup); result.push({popup, opener});
        });
      });
      owner.querySelectorAll('.friday-shell-menu,[role="dialog"],[role="menu"],.ws-tools-menu').forEach(popup => {
        if (!visible(popup) || seen.has(popup)) return;
        const cell = popup.closest('.friday-topbar-cell');
        const opener = cell && Array.from(cell.querySelectorAll('button')).find(button => !popup.contains(button));
        seen.add(popup); result.push({popup, opener});
      });
      return result;
    }
    function dismissPopup(entry) {
      const dismiss = Array.from(entry.popup.querySelectorAll('button')).find(button => /^close\b/i.test(button.getAttribute('aria-label') || button.title || ''));
      if (!dismiss && !entry.opener) return false;
      (dismiss || entry.opener).click(); return true;
    }
    function returnToOpener(opener) {
      requestAnimationFrame(() => {
        const hidden = !opener?.isConnected || !opener.getClientRects().length || opener.closest('[hidden],[inert]') || getComputedStyle(opener).visibility === 'hidden';
        (hidden ? trigger.current : opener)?.focus();
      });
    }

    useLayoutEffect(() => {
      mounted.current = true;
      const measure = () => {
        frame.current = 0;
        const bar = host.current, inline = row.current, more = trigger.current;
        if (!bar || !inline || !more) return;
        const items = current.current;
        const wanted = new Set(items.map(item => item.id));
        slots.current.forEach((element, id) => { if (!wanted.has(id)) { element.remove(); slots.current.delete(id); } });
        items.forEach(item => {
          if (!item.element.isConnected) inline.appendChild(item.element);
          item.element.hidden = false;
          item.element.classList.add('friday-topbar-measuring');
        });
        const style = getComputedStyle(bar), gap = parseFloat(style.columnGap) || 8;
        const available = bar.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        const measured = items.map(item => {
          const cellStyle = getComputedStyle(item.element);
          return Object.assign({}, item, {width:Math.ceil(item.element.getBoundingClientRect().width + (parseFloat(cellStyle.marginLeft) || 0) + (parseFloat(cellStyle.marginRight) || 0))});
        });
        const packed = packTopbar(available, measured, more.getBoundingClientRect().width || 38, gap);
        const overflow = new Set(packed.overflow);
        const destinations = {inline:[], navigation:[], controls:[], system:[]};
        items.forEach(item => {
          item.element.classList.remove('friday-topbar-measuring');
          const tucked = overflow.has(item.id);
          item.element.dataset.overflow = tucked ? 'true' : 'false';
          const empty = !measured.find(entry => entry.id === item.id).width;
          const floating = empty && item.element.querySelector('.friday-shell-menu,[role="dialog"],[role="menu"]');
          item.element.dataset.floatingOnly = floating ? 'true' : 'false';
          item.element.hidden = empty && !floating;
          destinations[tucked ? item.group : 'inline'].push(item.element);
        });
        Object.entries(destinations).forEach(([name, elements]) => {
          const destination = name === 'inline' ? inline : groups.current[name];
          if (!destination) return;
          elements.forEach((element, index) => {
            if (destination.children[index] !== element) {
              if (!openRef.current && element.contains(document.activeElement) && name !== 'inline') more.focus();
              destination.insertBefore(element, destination.children[index] || null);
            }
          });
          if (name !== 'inline') destination.parentElement.hidden = !elements.some(element => !element.hidden);
        });
        const changed = bar.dataset.overflowCount !== String(packed.overflow.length);
        bar.dataset.overflowCount = String(packed.overflow.length);
        bar.dataset.ready = 'true';
        more.hidden = !packed.overflow.length;
        if (changed && mounted.current) setOverflowCount(packed.overflow.length);
        if (!packed.overflow.length) closeRef.current(false);
        const height = Math.ceil(bar.getBoundingClientRect().bottom);
        document.documentElement.style.setProperty('--fr-topbar-h', height + 'px');
      };
      requestMeasure.current = () => { if (!frame.current) frame.current = requestAnimationFrame(measure); };
      const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(requestMeasure.current) : null;
      const mutation = new MutationObserver(requestMeasure.current);
      observer?.observe(host.current);
      mutation.observe(host.current, {subtree:true,childList:true,characterData:true});
      mutation.observe(panel.current, {subtree:true,childList:true,characterData:true});
      window.addEventListener('resize', requestMeasure.current);
      document.fonts?.addEventListener('loadingdone', requestMeasure.current);
      measure();
      return () => {
        mounted.current = false; observer?.disconnect(); mutation.disconnect();
        window.removeEventListener('resize', requestMeasure.current);
        document.fonts?.removeEventListener('loadingdone', requestMeasure.current);
        if (frame.current) cancelAnimationFrame(frame.current);
      };
    }, []);
    useLayoutEffect(() => { requestMeasure.current(); });

    useEffect(() => {
      if (!open) return;
      const first = () => Array.from(panel.current.querySelectorAll(focusable)).find(element => element.getClientRects().length);
      first()?.focus();
      const outside = event => {
        if (!panel.current?.contains(event.target) && !trigger.current?.contains(event.target) && !ownedPopups().some(entry => entry.popup.contains(event.target))) closeRef.current(false);
      };
      const key = event => {
        const popups = ownedPopups();
        if (event.key === 'Escape') {
          const nested = popups.find(entry => entry.popup.contains(event.target)) || popups[popups.length - 1];
          if (nested) {
            // The workspace switcher owns its body portal and its Escape handler.
            if (nested.popup.classList.contains('fx-switcher-popover')) return;
            if (nested.opener || Array.from(nested.popup.querySelectorAll('button')).some(button => /^close\b/i.test(button.getAttribute('aria-label') || button.title || ''))) {
              event.preventDefault(); event.stopImmediatePropagation(); dismissPopup(nested); returnToOpener(nested.opener);
            }
            return;
          }
          event.preventDefault(); event.stopImmediatePropagation(); closeRef.current(true);
        } else if (event.key === 'Tab' && !popups.length && panel.current?.contains(event.target)) {
          const targets = Array.from(panel.current.querySelectorAll(focusable)).filter(element => element.getClientRects().length);
          if ((event.shiftKey && event.target === targets[0]) || (!event.shiftKey && event.target === targets[targets.length - 1])) {
            closeRef.current(true); if (event.shiftKey) event.preventDefault();
          }
        }
      };
      const contextAction = event => {
        const control = event.target.closest?.('button,a[href]');
        const cell = control?.closest('.friday-topbar-cell[data-kind="context"][data-overflow="true"]');
        if (!cell || control.disabled || control.hasAttribute('aria-haspopup') || control.hasAttribute('aria-controls') || control.closest('.friday-shell-menu,[role="menu"]')) return;
        // Native workspace tools stop propagation. Capture observes the choice,
        // then yields until their real handler has opened its destination.
        queueMicrotask(() => {
          if (!openRef.current || ownedPopups().some(entry => entry.opener === control)) return;
          closeRef.current(cell.contains(document.activeElement));
        });
      };
      const workspaceAction = event => {
        const workspace = event.detail?.workspace;
        if (!workspace || !Array.from(panel.current.querySelectorAll('.ws-tools[data-workspace]')).some(tools => tools.dataset.workspace === workspace)) return;
        // A workspace tool's popup is a body portal. Its native action owns the
        // new destination; dismiss the enclosing overflow without stealing focus.
        queueMicrotask(() => { if (openRef.current) closeRef.current(false); });
      };
      document.addEventListener('pointerdown', outside, true);
      document.addEventListener('keydown', key, true);
      window.addEventListener('friday:workspace-tool-action', workspaceAction);
      panel.current.addEventListener('click', contextAction, true);
      const currentPanel = panel.current;
      return () => { document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', key, true); window.removeEventListener('friday:workspace-tool-action', workspaceAction); currentPanel.removeEventListener('click', contextAction, true); };
    }, [open]);

    const nativeCell = item => {
      let node = item.node;
      if (node.type === scope.FridayWorkspaceSwitcher) {
        const navigate = {};
        ['onSelect','onOpenTab','onHome'].forEach(name => {
          if (typeof node.props[name] !== 'function') return;
          const handler = node.props[name];
          navigate[name] = async (...args) => {
            const result = await handler(...args);
            if (result !== false && item.element.dataset.overflow === 'true') closeRef.current(true);
            return result;
          };
        });
        node = React.cloneElement(node, navigate);
      }
      if (node.type === 'button' && item.label) node = React.cloneElement(node, {}, node.props.children, h('span', {className:'friday-topbar-action-label'}, item.label));
      if (node.type === 'button') node = React.cloneElement(node, {onClick:event => {
        if (item.node.props.onClick) item.node.props.onClick(event);
        if (item.element.dataset.overflow === 'true') closeRef.current(item.element.contains(document.activeElement));
      }});
      return scope.ReactDOM.createPortal(h('div', {className:'friday-topbar-native'}, node), item.element, item.id);
    };
    const panelId = 'friday-topbar-overflow';
    return h(React.Fragment, null,
      h('div', Object.assign({}, hostProps, {ref:host, className:className + ' friday-responsive-topbar'}),
        h('div', {ref:row, className:'friday-topbar-row'}),
        h('button', {ref:trigger, type:'button', className:'friday-topbar-more', 'aria-label':'More Friday controls', 'aria-haspopup':'dialog', 'aria-expanded':open, 'aria-controls':panelId,
          onClick:() => open ? close(true) : setOpen(true), onKeyDown:event => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); } }},
          h('svg', {viewBox:'0 0 24 24', width:20, height:20, fill:'currentColor', 'aria-hidden':true}, [5,12,19].map(cx => h('circle', {key:cx,cx,cy:12,r:1.8}))))),
      h('div', {id:panelId, ref:panel, className:'friday-topbar-pullout', 'data-open':open ? 'true' : 'false', role:'dialog', 'aria-label':'Friday controls', 'aria-hidden':open ? undefined : 'true', inert:open ? undefined : ''},
        h('div', {className:'friday-topbar-pullout-heading'}, h('div', null, h('strong', null, 'Friday controls'), h('span', null, 'Everything within reach')),
          h('button', {type:'button', 'aria-label':'Close Friday controls', onClick:() => close(true)}, '×')),
        ['navigation','controls','system'].map(name => h('section', {key:name, className:'friday-topbar-pullout-group'}, h('h2', null, {navigation:'Navigate',controls:'Quick controls',system:'System status'}[name]), h('div', {ref:element => {groups.current[name] = element;}})))),
      descriptors.map(nativeCell));
  }
  scope.FridayResponsiveTopBar = FridayResponsiveTopBar;
  scope.FridayTopbarLayout = {packTopbar};
})(typeof window === 'undefined' ? null : window);
