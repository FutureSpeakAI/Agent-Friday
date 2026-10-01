// Drives the holographic scene the way a real session does, from the page's
// own clock (so under Playwright's fake clock it is exact and repeatable).
// Evaluated in the page's global scope after the scene has loaded:
//   window.__voice = 1  Friday speaking: _fridayHoloAmplitude follows a
//                       syllable-like envelope (about 4.5 syllables a second,
//                       with phrasing);
//   window.__room  = 1  the microphone hears speech: a stand-in analyser
//                       replaces the microphone's, so audioData (low, mid,
//                       high) moves as it does live. 0 is a quiet room, which
//                       still has a little noise;
//   window.__mic   = 1  the user's mic level, for the listening ripple.
(function () {
  const w = window;
  w.__voice = 0; w.__room = 0; w.__mic = 0;
  const syl = t => Math.pow(Math.max(0, Math.sin(2 * Math.PI * 4.5 * t)), 0.6) * (0.6 + 0.4 * Math.sin(2 * Math.PI * 0.7 * t));
  const noise = n => { n = Math.imul((n ^ 0x9e3779b9) >>> 0, 0x85ebca6b) >>> 0; return ((n ^ (n >>> 13)) >>> 0) / 4294967296; };
  const drive = () => {
    requestAnimationFrame(drive);
    const t = performance.now() / 1000;
    w._fridayHoloAmplitude = w.__voice ? 0.75 * syl(t) : 0;
    w._fridayMicLevel = w.__mic ? 0.7 * Math.abs(Math.sin(2 * Math.PI * 3.1 * t)) : 0;
  };
  drive();
  // The page's own microphone globals (index.html: analyser, dataArray, micActive).
  analyser = {
    getByteFrequencyData(arr) {
      const t = performance.now() / 1000, frame = Math.floor(t * 60);
      const e = w.__room ? syl(t * 1.07 + 0.3) : 0;
      for (let i = 0; i < arr.length; i++) {
        const band = i < 8 ? 210 : i < 40 ? 160 : 120;
        arr[i] = Math.max(0, Math.min(255, Math.round(band * e + 12 * noise(frame * 131 + i))));
      }
    },
  };
  dataArray = new Uint8Array(128);
  micActive = true;
})();
