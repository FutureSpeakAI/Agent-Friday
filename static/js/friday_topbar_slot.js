/* The top-bar plugin slot: one documented place where an add-on or a local
   override puts a control in Friday's top bar (field install report, Part 2
   #12), instead of patching index.html, which the next update replaces.

   Usage, from any script the page loads (static/, an override, a bundle):

     window.fridayTopBarSlot.register('my-addon', function MyAddon(props) {
       return React.createElement('button', {type: 'button',
         'aria-label': 'My add-on', onClick: ...}, 'Mine');
     });
     // ...and later, if needed:
     window.fridayTopBarSlot.unregister('my-addon');

   - `id`: [a-z0-9_-], 1-40 characters; registering the same id again
     replaces the earlier control.
   - The second argument is a React component. It renders inside
     #friday-topbar-slot (data-friday-slot="topbar"), after the built-in
     controls of the top bar's left group, and travels with them into the
     "More Friday controls" pull-out on a narrow window.
   - Each control renders inside its own error boundary, so one broken add-on
     cannot take the desktop down with it.
   - Register before the desktop mounts (any script in <head> or <body>) or
     at any time after: the slot listens for the `friday:topbar-slot` event.

   The registry is plain data and has no React dependency; the slot component
   itself lives in index.html (FridayTopBarPlugins). */
(function (root) {
  'use strict';
  if (!root) return;
  var EVENT = 'friday:topbar-slot';
  var ID = /^[a-z0-9_-]{1,40}$/;
  var items = [];

  function changed() {
    try { root.dispatchEvent(new root.CustomEvent(EVENT, { detail: { ids: items.map(function (i) { return i.id; }) } })); }
    catch (e) { /* no event support: the next top-bar render picks it up */ }
  }

  function unregister(id) {
    var before = items.length;
    items = items.filter(function (i) { return i.id !== id; });
    if (items.length !== before) changed();
    return items.length !== before;
  }

  function register(id, component) {
    if (typeof id !== 'string' || !ID.test(id)) {
      throw new Error('fridayTopBarSlot.register: id must match [a-z0-9_-]{1,40}');
    }
    if (typeof component !== 'function') {
      throw new Error('fridayTopBarSlot.register: the second argument must be a React component');
    }
    items = items.filter(function (i) { return i.id !== id; }).concat([{ id: id, component: component }]);
    changed();
    return function () { return unregister(id); };
  }

  root.fridayTopBarSlot = {
    EVENT: EVENT,
    register: register,
    unregister: unregister,
    list: function () { return items.slice(); },
    size: function () { return items.length; }
  };
})(typeof window === 'undefined' ? null : window);
