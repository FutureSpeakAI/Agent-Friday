/* Shared prototype helpers. Not product code.
   - renderHolo(el, seed, state): draws the Genesis Lattice as a flat 3x3 mark with a
     seeded dropout and a seeded sigil (arm count 3..8, tilt), the way the genome spec
     describes the birth mark. Same seed, same mark, on every screen.
   - protoNav(current): the prototype navigation strip.
   - say(text): updates the "Friday says" line and the aria-live region.
*/
(function () {
  function hash(str) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return h >>> 0;
  }
  function rng(seed) {
    let s = hash(String(seed)) || 1;
    return function () { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return (s >>> 0) / 4294967296; };
  }
  window.fridaySeed = window.fridaySeed || (function () {
    try { return localStorage.getItem('friday.proto.seed') || 'friday-birth-seed'; } catch (e) { return 'friday-birth-seed'; }
  })();
  window.setFridaySeed = function (seed) {
    window.fridaySeed = seed;
    try { localStorage.setItem('friday.proto.seed', seed); } catch (e) { /* private window */ }
    document.querySelectorAll('.holo').forEach(function (el) { renderHolo(el, seed, el.dataset.state); });
  };
  window.renderHolo = function renderHolo(el, seed, state) {
    const r = rng(seed || window.fridaySeed);
    const cell = 30, gap = 6, size = 3 * cell + 2 * gap;
    let cubes = '';
    for (let y = 0; y < 3; y++) for (let x = 0; x < 3; x++) {
      const off = r() < 0.15 && !(x === 1 && y === 1);
      const px = x * (cell + gap), py = y * (cell + gap);
      cubes += '<rect class="cube' + (off ? ' off' : '') + '" x="' + px + '" y="' + py + '" width="' + cell + '" height="' + cell + '" rx="3"/>';
    }
    const arms = 3 + Math.floor(r() * 6); // 3..8
    const tilt = r() * 360;
    const cx = size / 2, cy = size / 2, rad = 9;
    let sigil = '';
    for (let a = 0; a < arms; a++) {
      const ang = (tilt + a * 360 / arms) * Math.PI / 180;
      sigil += '<line x1="' + cx + '" y1="' + cy + '" x2="' + (cx + Math.cos(ang) * rad).toFixed(1) + '" y2="' + (cy + Math.sin(ang) * rad).toFixed(1) + '"/>';
    }
    const accent = Math.floor(r() * arms);
    const ang = (tilt + accent * 360 / arms) * Math.PI / 180;
    sigil += '<circle cx="' + (cx + Math.cos(ang) * rad).toFixed(1) + '" cy="' + (cy + Math.sin(ang) * rad).toFixed(1) + '" r="2"/>';
    el.innerHTML = '<svg viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="Friday\'s hologram mark, ' + arms + ' arms">' +
      '<g>' + cubes + '</g><g class="sigil">' + sigil + '</g></svg>';
    el.classList.remove('busy', 'speaking');
    if (state) el.classList.add(state);
  };
  window.protoNav = function protoNav(current) {
    const pages = [
      ['index.html', 'Map'],
      ['installer.html', '1 Installer'],
      ['birth.html', '2 Naming and birth'],
      ['connect.html', '3 Connect your life'],
      ['knowledge.html', '4 Knowledge growing'],
      ['review.html', '5 Review queue'],
      ['control-room.html', '6 Control room'],
      ['privacy-map.html', '7 Privacy map']
    ];
    const bar = document.createElement('div');
    bar.className = 'proto-bar';
    bar.innerHTML = '<span class="tag">Prototype</span><span>First run and onboarding · design prototype, not product code</span>' +
      '<nav aria-label="Prototype screens">' + pages.map(function (p) {
        return '<a href="' + p[0] + '"' + (p[0] === current ? ' aria-current="page"' : '') + '>' + p[1] + '</a>';
      }).join('') + '</nav>';
    document.body.prepend(bar);
    const skip = document.createElement('a');
    skip.className = 'skip'; skip.href = '#main'; skip.textContent = 'Skip to content';
    document.body.prepend(skip);
  };
  window.say = function say(text, state) {
    document.querySelectorAll('[data-friday-line]').forEach(function (p) { p.textContent = text; });
    let live = document.getElementById('live');
    if (!live) { live = document.createElement('div'); live.id = 'live'; live.className = 'sr-only'; live.setAttribute('aria-live', 'polite'); document.body.appendChild(live); }
    live.textContent = text;
    document.querySelectorAll('.holo[data-voice]').forEach(function (el) { renderHolo(el, window.fridaySeed, state || 'speaking'); });
    document.querySelectorAll('[data-holo-state]').forEach(function (el) { el.textContent = state === 'busy' ? 'Working' : 'Speaking'; });
    clearTimeout(window.__sayT);
    window.__sayT = setTimeout(function () {
      document.querySelectorAll('.holo[data-voice]').forEach(function (el) { renderHolo(el, window.fridaySeed, null); });
      document.querySelectorAll('[data-holo-state]').forEach(function (el) { el.textContent = 'Listening'; });
    }, 2600);
  };
  window.toast = function toast(text) {
    let t = document.querySelector('.toast');
    if (!t) { t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = text; t.classList.add('show');
    clearTimeout(window.__toastT);
    window.__toastT = setTimeout(function () { t.classList.remove('show'); }, 2800);
  };
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.holo').forEach(function (el) { renderHolo(el, window.fridaySeed, el.dataset.state); });
  });
})();
