/* Friday frame broker.

   Anything Friday shows that she did not write herself (a Studio creation, a
   published page, a file preview, a document a model produced) runs in an
   iframe with an opaque origin: `sandbox` without `allow-same-origin`. That
   frame has no cookies, no storage, no session token, and the server refuses
   every /api and /ws request it makes. This broker is the one door back in,
   and it is small on purpose.

   A frame asks with postMessage; the page decides. The set of things a frame
   can ask for is closed and listed in CAPABILITIES below. There is no
   capability that reads the vault, touches credentials, reads or writes
   settings, calls an API route, or runs a command, and there is no way to add
   one at run time. A frame that asks for anything else is told no.

   Request  (frame -> page):  { protocol: "friday-frame/1", id, cap, args }
   Reply    (page -> frame):  { protocol: "friday-frame/1", id, ok: true,  result }
                              { protocol: "friday-frame/1", id, ok: false, error }

   Documented for authors in docs/development/frame-broker.md. */
(function (root) {
  'use strict';

  var PROTOCOL = 'friday-frame/1';
  var MAX_ARGS_BYTES = 200000;
  var MAX_CLIPBOARD_CHARS = 100000;
  var MAX_PER_SECOND = 30;

  // Brand tokens a frame may read so it can match the page around it.
  var THEME = {
    primary: '#00d4ff',
    violet: '#7b61ff',
    magenta: '#ff00ff',
    ok: '#00ff80',
    warn: '#f59e0b',
    deny: '#ff0080',
    error: '#ef4444',
    glass: 'rgba(10,14,26,0.75)',
    fonts: { display: 'Orbitron', body: 'Inter', mono: 'JetBrains Mono' }
  };

  // The whole surface. Each entry: a handler (env, frame, args) -> result.
  var CAPABILITIES = {
    'ping': function (env, frame) {
      return { protocol: PROTOCOL, capabilities: Object.keys(CAPABILITIES) };
    },
    'theme.get': function () {
      return JSON.parse(JSON.stringify(THEME));
    },
    // Only a frame the page marked resizable, and only within the window.
    'frame.resize': function (env, frame, args) {
      var h = Math.round(Number(args && args.height));
      if (!(h >= 40 && h <= 4000)) throw new Error('Height must be between 40 and 4000 pixels.');
      if (!frame.el.hasAttribute('data-friday-resizable')) {
        throw new Error('This frame is not resizable.');
      }
      frame.el.style.height = h + 'px';
      return { height: h };
    },
    // A link the person just clicked. Web addresses only, never opener access.
    'link.open': function (env, frame, args) {
      requireGesture(env);
      var u;
      try { u = new URL(String(args && args.url)); } catch (e) { throw new Error('That is not an address.'); }
      if (u.protocol !== 'https:' && u.protocol !== 'http:') {
        throw new Error('Only web addresses can be opened.');
      }
      env.open(u.href, '_blank', 'noopener,noreferrer');
      return { opened: true };
    },
    // Text the person just asked to copy.
    'clipboard.write': function (env, frame, args) {
      requireGesture(env);
      var t = args && args.text;
      if (typeof t !== 'string' || t.length > MAX_CLIPBOARD_CHARS) {
        throw new Error('Text only, up to ' + MAX_CLIPBOARD_CHARS + ' characters.');
      }
      if (!env.clipboardWrite) throw new Error('Copying is not available here.');
      return Promise.resolve(env.clipboardWrite(t)).then(function () { return { copied: true }; });
    }
  };

  function requireGesture(env) {
    if (!env.userActive()) throw new Error('That needs a click or a keypress first.');
  }

  function isSandboxed(el) {
    var sb = el && el.sandbox;
    if (!sb || typeof sb.contains !== 'function') return false;
    if (typeof el.hasAttribute === 'function' && !el.hasAttribute('sandbox')) return false;
    return !sb.contains('allow-same-origin');
  }

  function create(env) {
    var frames = [];
    var windowStart = 0;
    var count = 0;

    function prune() {
      frames = frames.filter(function (f) { return f.el.isConnected !== false; });
    }

    function attach(el) {
      if (!el) return;
      prune();
      if (!isSandboxed(el)) {
        // A frame that could share Friday's origin never gets a door.
        return;
      }
      for (var i = 0; i < frames.length; i++) if (frames[i].el === el) return;
      frames.push({ el: el });
    }

    function frameFor(source) {
      prune();
      for (var i = 0; i < frames.length; i++) {
        var w = frames[i].el.contentWindow;
        if (w && w === source) return frames[i];
      }
      return null;
    }

    function reply(source, id, ok, payload) {
      var msg = { protocol: PROTOCOL, id: id, ok: ok };
      msg[ok ? 'result' : 'error'] = payload;
      try { source.postMessage(msg, '*'); } catch (e) { /* the frame is gone */ }
    }

    function onMessage(ev) {
      var d = ev && ev.data;
      if (!d || typeof d !== 'object' || d.protocol !== PROTOCOL) return;
      var frame = frameFor(ev.source);
      // Only a frame Friday put on screen, and only one with an opaque origin.
      if (!frame || ev.origin !== 'null') return;
      var id = d.id;
      var now = env.now();
      if (now - windowStart >= 1000) { windowStart = now; count = 0; }
      if (++count > MAX_PER_SECOND) return reply(ev.source, id, false, 'Too many requests. Slow down.');
      var cap = d.cap;
      if (typeof cap !== 'string' || !Object.prototype.hasOwnProperty.call(CAPABILITIES, cap)) {
        return reply(ev.source, id, false, "That isn't something a frame can ask me for.");
      }
      try {
        if (JSON.stringify(d.args === undefined ? null : d.args).length > MAX_ARGS_BYTES) {
          throw new Error('That request is too large.');
        }
        Promise.resolve(CAPABILITIES[cap](env, frame, d.args)).then(
          function (r) { reply(ev.source, id, true, r); },
          function (e) { reply(ev.source, id, false, String(e && e.message || 'That did not work.')); }
        );
      } catch (e) {
        reply(ev.source, id, false, String(e && e.message || 'That did not work.'));
      }
    }

    return { attach: attach, onMessage: onMessage, capabilities: Object.keys(CAPABILITIES), protocol: PROTOCOL };
  }

  var api = { create: create, PROTOCOL: PROTOCOL, CAPABILITIES: Object.keys(CAPABILITIES) };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  if (root && typeof root.addEventListener === 'function' && root.document) {
    var broker = create({
      now: function () { return Date.now(); },
      open: function (u, t, f) { return root.open(u, t, f); },
      userActive: function () {
        var ua = root.navigator && root.navigator.userActivation;
        return !!(ua && ua.isActive);
      },
      clipboardWrite: function (t) {
        return root.navigator && root.navigator.clipboard && root.navigator.clipboard.writeText(t);
      }
    });
    root.addEventListener('message', broker.onMessage);
    root.fridayFrameBroker = broker;
    root.fridayAttachFrame = function (el) { broker.attach(el); };
  }
})(typeof window !== 'undefined' ? window : null);
