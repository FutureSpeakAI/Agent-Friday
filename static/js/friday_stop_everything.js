/* Stop everything (services/global_stop.py): a Stop control in the top bar and a
   banner with Resume while everything is stopped.

   Stop halts every lane at once: chat turns and any reply still streaming, tasks
   and website tasks, crews, delegated workers, the scheduler and nightly trials,
   the background queue, live voice calls and computer control. It needs no
   approval. Resume is a press of the banner's button on this page (the server
   refuses a resume from anywhere else); work that was stopped stays stopped.

   Also stops: typing "stop everything" in chat, saying it, or Ctrl+Alt+Shift+S
   anywhere on the PC. The banner is driven by GET /api/stop-everything, polled,
   and by the `everything_stopped` frame a voice call receives. */
(function (root) {
  'use strict';
  if (!root || !root.document) return;
  var doc = root.document;
  var BAR_ID = 'friday-everything-stopped-bar';
  var stopped = false;

  function api(path, opts) {
    return (root.apiFetch || root.fetch)(path, opts);
  }

  function toast(msg, error) {
    try { if (root.fridayToast) root.fridayToast(msg, error ? { error: true } : undefined); } catch (e) {}
  }

  function silenceThePage() {
    // Whatever is already playing in this page stops too: podcasts, read-aloud, voice audio.
    try {
      var media = doc.querySelectorAll('audio,video');
      for (var i = 0; i < media.length; i++) { try { media[i].pause(); } catch (e) {} }
    } catch (e) {}
    try { if (root.speechSynthesis) root.speechSynthesis.cancel(); } catch (e) {}
  }

  function render() {
    var bar = doc.getElementById(BAR_ID);
    if (!stopped) { if (bar) bar.remove(); return; }
    if (!bar) {
      bar = doc.createElement('div');
      bar.id = BAR_ID;
      bar.setAttribute('role', 'alert');
      bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;' +
        'display:flex;align-items:center;gap:12px;padding:8px 14px;' +
        'font-family:JetBrains Mono,monospace;font-size:12px;color:#ffd0d0;' +
        'background:rgba(70,10,14,0.97);border-bottom:1px solid rgba(255,90,90,0.55)';
      doc.body.appendChild(bar);
    }
    bar.innerHTML = '';
    var label = doc.createElement('span');
    label.textContent = '■  Everything is stopped. Nothing runs until you press Resume. ' +
      'What was stopped stays stopped.';
    var btn = doc.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Resume';
    btn.setAttribute('aria-label', 'Resume: let new and queued work run again');
    btn.style.cssText = 'margin-left:auto;cursor:pointer;font-family:inherit;font-size:11px;' +
      'padding:3px 12px;border-radius:5px;color:#fff;background:rgba(255,90,90,0.22);' +
      'border:1px solid rgba(255,90,90,0.65)';
    btn.onclick = function () {
      btn.disabled = true; btn.textContent = 'resuming…';
      api('/api/stop-everything/resume', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (x) {
          if (x.ok && x.d && x.d.stopped === false) {
            stopped = false; render(); toast(x.d.message || 'Resumed.');
          } else {
            btn.disabled = false; btn.textContent = 'Resume';
            toast((x.d && x.d.error) || 'Could not resume.', true);
          }
        })
        .catch(function () { btn.disabled = false; btn.textContent = 'Resume'; toast('Could not resume — try again.', true); });
    };
    bar.appendChild(label);
    bar.appendChild(btn);
  }

  function set(isStopped) {
    var was = stopped;
    stopped = !!isStopped;
    if (stopped && !was) silenceThePage();
    render();
    try { root.dispatchEvent(new root.CustomEvent('friday:everything-stopped', { detail: { stopped: stopped } })); } catch (e) {}
  }

  function stopEverything(source) {
    set(true);   // the page shows it at once; the server is the truth
    return api('/api/stop-everything', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: source || 'button' }) })
      .then(function (r) { return r.json(); })
      .then(function (d) { set(!!(d && d.stopped)); if (d && d.message) toast(d.message); return d; })
      .catch(function () { toast('Stop did not go through — press Ctrl+Alt+Shift+S.', true); poll(); });
  }

  function poll() {
    try {
      api('/api/stop-everything').then(function (r) { return r.json(); })
        .then(function (d) { if (d && typeof d.stopped === 'boolean' && d.stopped !== stopped) set(d.stopped); })
        .catch(function () {});
    } catch (e) {}
  }

  root.fridayStopEverything = { stop: stopEverything, poll: poll, set: set, isStopped: function () { return stopped; } };

  // The top-bar control, through the documented plugin slot.
  try {
    if (root.fridayTopBarSlot) {
      root.fridayTopBarSlot.register('stop-everything', function StopEverything() {
        var R = root.React;
        return R.createElement('button', {
          type: 'button', className: 'friday-stop-everything',
          title: 'Stop everything Friday is doing (Ctrl+Alt+Shift+S from anywhere)',
          'aria-label': 'Stop everything',
          onClick: function () { stopEverything('button'); },
          style: { cursor: 'pointer', fontSize: 11, padding: '2px 9px', borderRadius: 5, color: '#ffd0d0',
                   background: 'rgba(255,90,90,0.16)', border: '1px solid rgba(255,90,90,0.55)', whiteSpace: 'nowrap' }
        }, '■ Stop all');
      });
    }
  } catch (e) { /* the banner and the other ways to stop still work */ }

  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', function () { setTimeout(poll, 800); });
  else setTimeout(poll, 800);
  setInterval(poll, 10000);
})(typeof window !== 'undefined' ? window : null);
