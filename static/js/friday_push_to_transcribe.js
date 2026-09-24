/* Push-to-transcribe inside the Friday tab.
 *
 * The tray runs the system-wide version, and while that is on it SUPPRESSES
 * the hotkey before any window sees it — so if this handler is reached at all,
 * the system-wide one is off or not running. That is the whole coordination
 * between them: no flag to keep in step, no double-recording, and the shortcut
 * behaves the same either way.
 *
 * Kept in one file rather than copied into index.html and ui_parts/app.html,
 * because two hand-maintained copies of two hundred lines is a drift waiting
 * to happen.
 *
 * The audio goes to /api/voice/transcribe, which has no cloud path. Nothing
 * spoken here leaves the machine.
 */
(function () {
  'use strict';
  if (window.fridayPushToTranscribe) return;

  var RATE = 16000;
  var DEFAULTS = { on: true, hotkey: 'alt+t', holdMs: 150 };
  var MODS = { alt: 'altKey', ctrl: 'ctrlKey', control: 'ctrlKey',
               shift: 'shiftKey', win: 'metaKey', cmd: 'metaKey' };

  function parseHotkey(spec) {
    var parts = String(spec || '').toLowerCase().split('+').filter(Boolean);
    if (!parts.length) return null;
    var key = parts.pop();
    var mods = [];
    for (var i = 0; i < parts.length; i++) {
      if (!MODS[parts[i]]) return null;
      mods.push(MODS[parts[i]]);
    }
    if (key === 'space') key = ' ';
    if (key === 'grave') key = '`';
    return { mods: mods, key: key };
  }

  function matches(ev, hk) {
    if (!hk) return false;
    var k = (ev.key || '').toLowerCase();
    if (k !== hk.key && (ev.code || '').toLowerCase() !== 'key' + hk.key) return false;
    for (var i = 0; i < hk.mods.length; i++) if (!ev[hk.mods[i]]) return false;
    // A chord is the listed modifiers and no others: Ctrl+Alt+T must not fire
    // an Alt+T binding.
    var all = ['altKey', 'ctrlKey', 'shiftKey', 'metaKey'];
    for (var j = 0; j < all.length; j++) {
      if (hk.mods.indexOf(all[j]) === -1 && ev[all[j]]) return false;
    }
    return true;
  }

  // ── where the words go ────────────────────────────────────────────────
  function visible(el) {
    // The app keeps several chat boxes in the DOM and shows one. Picking a
    // hidden one drops the sentence somewhere nobody is looking.
    //
    // Measured rather than inferred from offsetParent, which is null for
    // anything inside a position:fixed ancestor and would have called every
    // chat box in this app hidden.
    if (!el || el.disabled || el.readOnly) return false;
    if (typeof el.checkVisibility === 'function') return el.checkVisibility();
    return el.getClientRects().length > 0;
  }

  function firstVisible(sel) {
    var all = document.querySelectorAll(sel);
    for (var i = 0; i < all.length; i++) if (visible(all[i])) return all[i];
    return null;
  }

  function editableTarget() {
    var el = document.activeElement;
    if (el && (el.tagName === 'TEXTAREA' ||
               (el.tagName === 'INPUT' && /^(text|search|url|email|tel|)$/i
                 .test(el.type || '')) ||
               el.isContentEditable)) return el;
    // Nothing focused that can take text: the chat box is what someone
    // dictating into Friday almost always means. `data-chat-input` is the
    // attribute FridayChatInput actually carries — checked against the
    // running app, where the invented selector this used to try matched
    // nothing at all and the fallback landed on a hidden textarea.
    return firstVisible('[data-chat-input]') ||
           firstVisible('textarea') ||
           document.querySelector('[data-chat-input]');
  }

  function insertInto(el, text) {
    if (!el) return false;
    try { el.focus(); } catch (e) {}
    if (el.isContentEditable) {
      if (document.execCommand && document.execCommand('insertText', false, text)) return true;
      el.textContent += text;
      return true;
    }
    var start = el.selectionStart, end = el.selectionEnd;
    if (typeof start === 'number') {
      // execCommand keeps the field's own undo history intact, which setting
      // .value directly would throw away.
      if (document.execCommand && document.execCommand('insertText', false, text)) {
        return true;
      }
      var v = el.value || '';
      el.value = v.slice(0, start) + text + v.slice(end);
      var at = start + text.length;
      el.setSelectionRange(at, at);
    } else {
      el.value = (el.value || '') + text;
    }
    // React listens for input events, not assignments.
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }

  // ── the little card ───────────────────────────────────────────────────
  var card, cardText, cardMeter, hideTimer;

  function showCard(state, message) {
    if (!card) {
      card = document.createElement('div');
      card.setAttribute('data-friday-ptt-card', '');
      card.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);' +
        'z-index:2147483000;background:#0b1020;color:#e8eefc;border:1px solid #2a3550;' +
        'border-radius:10px;padding:10px 14px;font:12px/1.4 "Segoe UI",system-ui,sans-serif;' +
        'box-shadow:0 8px 28px rgba(0,0,0,.5);pointer-events:none;min-width:240px';
      cardText = document.createElement('div');
      cardMeter = document.createElement('div');
      cardMeter.style.cssText = 'height:4px;background:#18203a;border-radius:2px;margin-top:8px;overflow:hidden';
      var bar = document.createElement('div');
      bar.style.cssText = 'height:100%;width:0;background:#ef4444;transition:width .08s linear';
      cardMeter.appendChild(bar);
      cardMeter._bar = bar;
      card.appendChild(cardText);
      card.appendChild(cardMeter);
      document.body.appendChild(card);
    }
    var colour = { recording: '#ef4444', arming: '#94a3b8', thinking: '#e0a030',
                   error: '#ef4444', done: '#22c55e' }[state] || '#94a3b8';
    cardText.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;' +
      'background:' + colour + ';margin-right:8px"></span>' +
      String(message || '').replace(/[<>&]/g, function (c) {
        return { '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c];
      });
    cardMeter.style.display = state === 'recording' ? 'block' : 'none';
    card.style.display = 'block';
    clearTimeout(hideTimer);
    if (state === 'done' || state === 'error' || state === 'idle') {
      hideTimer = setTimeout(function () { if (card) card.style.display = 'none'; },
                             state === 'error' ? 5000 : 1400);
    }
  }

  function level(v) {
    if (cardMeter && cardMeter._bar) {
      cardMeter._bar.style.width = Math.min(100, Math.round(v * 100)) + '%';
    }
  }

  // ── recording ─────────────────────────────────────────────────────────
  var cfg = Object.assign({}, DEFAULTS), hk = parseHotkey(DEFAULTS.hotkey);
  var state = 'idle', timer = null, chunks = [], ctx = null, stream = null,
      node = null, src = null, heard = false;

  function loadSettings() {
    return fetch('/api/settings').then(function (r) { return r.json(); })
      .then(function (body) {
        var s = (body && body.settings) || body || {};
        cfg.on = s.push_to_transcribe !== false;
        cfg.hotkey = s.push_to_transcribe_hotkey || DEFAULTS.hotkey;
        cfg.holdMs = s.push_to_transcribe_hold_ms || DEFAULTS.holdMs;
        hk = parseHotkey(cfg.hotkey) || parseHotkey(DEFAULTS.hotkey);
      }).catch(function () {});
  }

  function startCapture() {
    heard = false;
    chunks = [];
    return navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
    }).then(function (st) {
      stream = st;
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      src = ctx.createMediaStreamSource(st);
      node = ctx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = function (ev) {
        if (state !== 'recording' && state !== 'pending') return;
        var input = ev.inputBuffer.getChannelData(0);
        var ratio = ctx.sampleRate / RATE;
        var n = Math.floor(input.length / ratio);
        var out = new Int16Array(n), peak = 0;
        for (var i = 0; i < n; i++) {
          var s0 = input[Math.floor(i * ratio)];
          if (s0 > peak) peak = s0; else if (-s0 > peak) peak = -s0;
          var c = Math.max(-1, Math.min(1, s0));
          out[i] = c < 0 ? c * 0x8000 : c * 0x7FFF;
        }
        chunks.push(out);
        if (!heard) {
          heard = true;
          if (state === 'recording') {
            showCard('recording', 'Listening… release to transcribe, Esc to cancel');
          }
        }
        level(Math.min(1, peak * 2.2));
      };
      src.connect(node);
      node.connect(ctx.destination);
    });
  }

  function stopCapture() {
    try { node && (node.onaudioprocess = null, node.disconnect()); } catch (e) {}
    try { src && src.disconnect(); } catch (e) {}
    try { stream && stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
    try { ctx && ctx.close(); } catch (e) {}
    node = src = stream = ctx = null;
    var total = 0, i;
    for (i = 0; i < chunks.length; i++) total += chunks[i].length;
    var all = new Int16Array(total), at = 0;
    for (i = 0; i < chunks.length; i++) { all.set(chunks[i], at); at += chunks[i].length; }
    chunks = [];
    return all;
  }

  function toB64(i16) {
    var bytes = new Uint8Array(i16.buffer, i16.byteOffset, i16.byteLength);
    var bin = '', CH = 0x8000;
    for (var i = 0; i < bytes.length; i += CH) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    }
    return btoa(bin);
  }

  function finish() {
    var pcm = stopCapture();
    if (pcm.length < RATE * 0.3) {
      showCard('idle', 'Too short — hold the key while you speak');
      return;
    }
    var target = editableTarget();
    showCard('thinking', 'Transcribing…');
    fetch('/api/voice/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio_b64: toB64(pcm), rate: RATE })
    }).then(function (r) {
      return r.json().then(function (b) { return { ok: r.ok, body: b }; });
    }).then(function (res) {
      if (!res.ok) {
        showCard('error', (res.body && res.body.error) || 'Transcription failed');
        return;
      }
      var text = (res.body.text || '').trim();
      if (!text) { showCard('idle', 'Nothing was said'); return; }
      if (insertInto(target, text)) showCard('done', text);
      else showCard('error', 'Nowhere to put the text');
    }).catch(function (e) {
      showCard('error', String(e && e.message || e));
    });
  }

  function cancel(why) {
    state = 'idle';
    clearTimeout(timer);
    stopCapture();
    showCard('idle', why);
  }

  window.addEventListener('keydown', function (ev) {
    if (!cfg.on) return;
    if (ev.key === 'Escape' && state === 'recording') {
      ev.preventDefault();
      cancel('Cancelled');
      return;
    }
    if (!matches(ev, hk)) return;
    ev.preventDefault();
    if (state !== 'idle') return;             // auto-repeat while held
    state = 'pending';
    // The microphone takes a moment to open, and people start speaking as
    // they press, so it opens now. A tap throws the audio away.
    startCapture().catch(function (e) {
      state = 'idle';
      showCard('error', 'Could not start the microphone: ' + (e && e.message || e));
    });
    timer = setTimeout(function () {
      if (state !== 'pending') return;
      state = 'recording';
      showCard(heard ? 'recording' : 'arming',
               heard ? 'Listening… release to transcribe, Esc to cancel'
                     : 'Opening the microphone…');
    }, cfg.holdMs);
  }, true);

  window.addEventListener('keyup', function (ev) {
    if (!cfg.on || !hk) return;
    var k = (ev.key || '').toLowerCase();
    if (k !== hk.key && (ev.code || '').toLowerCase() !== 'key' + hk.key) return;
    if (state === 'pending') {
      // A tap: never ours. Drop the audio unheard.
      ev.preventDefault();
      state = 'idle';
      clearTimeout(timer);
      stopCapture();
      return;
    }
    if (state === 'recording') {
      ev.preventDefault();
      state = 'idle';
      finish();
    }
  }, true);

  loadSettings();
  window.addEventListener('focus', loadSettings);

  window.fridayPushToTranscribe = {
    reloadSettings: loadSettings,
    config: function () { return Object.assign({}, cfg); },
    parseHotkey: parseHotkey,
    matches: matches
  };
})();
