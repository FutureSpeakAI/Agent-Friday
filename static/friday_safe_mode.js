/* Safe mode banner (services/safe_mode.py). When this run is in safe mode,
 * a line across the top says so, says what is off, and links to the Doctor
 * page, where the owner leaves safe mode for the next start. Nothing is drawn
 * on a normal start. */
(function () {
  'use strict';
  if (window.__fridaySafeModeBanner) return;
  window.__fridaySafeModeBanner = true;
  function draw(d) {
    if (!d || !d.active || document.getElementById('friday-safe-mode-banner')) return;
    const bar = document.createElement('div');
    bar.id = 'friday-safe-mode-banner';
    bar.setAttribute('role', 'status');
    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#3a2a05;color:#fde68a;' +
      'font:13px/1.4 system-ui,sans-serif;padding:6px 14px;border-bottom:1px solid #fbbf24;text-align:center';
    bar.textContent = 'Safe mode (' + (d.why || 'chosen') + '): extensions and the cloud are off, schedules are paused, outward actions are held. ';
    const a = document.createElement('a');
    a.href = '/doctor'; a.textContent = 'Open Doctor'; a.style.color = '#fbbf24';
    bar.appendChild(a);
    document.body.appendChild(bar);
  }
  function check() {
    fetch('/api/safe-mode').then(r => r.json()).then(draw).catch(() => {});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', check);
  else check();
})();
