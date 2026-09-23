/* Studio › Files — a 3D file browser with six arrangements that fly into
 * each other: Wall, Carousel, Tree, City, Timeline and Clusters.
 *
 * Scale: every file is one instance of a single InstancedMesh (one draw call
 * for all cards), so thousands of files cost the same as a handful. Real
 * thumbnails live in a fixed pool of texture-atlas slots; only the cards
 * nearest the camera and on screen hold a slot, the rest show a coloured
 * card, and slots are recycled as the camera moves. Thumbnails come from
 * /api/studio-files/thumb (cached on disk server-side, HTTP-cached here).
 *
 * The server decides what is browsable (services/studio_files.py). Nothing
 * here changes a file: delete / move / rename file an approval card and the
 * change happens only after the owner approves it.
 *
 * Look (the "Dazzle" levels Off / Subtle / Full, setting studio_dazzle)
 * follows Friday's holographic theme:
 *   - cyan #00d4ff is the light: rims, selection, grid, focus;
 *   - the cyan -> violet -> magenta shimmer appears only on interaction
 *     and on events, never as wallpaper;
 *   - amber means "waiting for you" (a change held for approval), red
 *     means failure;
 *   - deep ink and navy under the knowledge galaxy's own nebula art
 *     (static/galaxy/), tinted by Friday's live mood colours;
 *   - glow is soft (10-20 % halos, additive light), grain and scanlines
 *     stay at the backdrop's own tiny amounts, and never on a file name;
 *   - motion is fast-out / soft-settle; file actions last <= 600 ms and a
 *     click or key skips them; prefers-reduced-motion stills everything.
 * Every file action animates only what actually happened: a delete
 * dissolves after the approved change reports success, never before.
 *
 * Loaded by index.html as a plain script; defines window.Files3DPanel.
 */
(function () {
  'use strict';
  if (window.Files3DPanel) return;
  const h = React.createElement;
  const { useState, useEffect, useRef, useCallback } = React;

  // ── file kinds ──────────────────────────────────────────────────────────
  const CATS = {
    folder: { color: 0x5b9dff, label: 'Folders', ico: '📁' },
    image: { color: 0xff5fa2, label: 'Images', ico: '🖼️' },
    video: { color: 0xff9f43, label: 'Video', ico: '🎬' },
    audio: { color: 0x2ed3b7, label: 'Audio', ico: '🎵' },
    doc: { color: 0xf5d76e, label: 'Docs', ico: '📄' },
    code: { color: 0x8c7cff, label: 'Code', ico: '💻' },
    data: { color: 0x5fd068, label: 'Data', ico: '📊' },
    design: { color: 0xc77dff, label: '3D & design', ico: '🧊' },
    archive: { color: 0x9aa4b2, label: 'Archives', ico: '🗜️' },
    other: { color: 0x66758c, label: 'Other', ico: '•' }
  };
  const EXT_CAT = {};
  const addExts = (cat, s) => s.split(' ').forEach(x => { EXT_CAT[x] = cat; });
  addExts('image', 'png jpg jpeg webp gif bmp tif tiff ico svg avif heic');
  addExts('video', 'mp4 webm mov mkv avi m4v wmv');
  addExts('audio', 'mp3 wav ogg m4a flac aac opus');
  addExts('doc', 'pdf doc docx txt md markdown rtf odt epub pages ppt pptx odp rst srt vtt log');
  addExts('code', 'py js jsx ts tsx mjs html htm css scss rs go java kt c h cpp hpp cs rb php sh ps1 bat lua swift vue svelte sql');
  addExts('data', 'json jsonl csv tsv xml yaml yml toml ini cfg xlsx xls ods db sqlite parquet');
  addExts('design', 'glb gltf obj fbx blend stl psd ai fig sketch kra xcf');
  addExts('archive', 'zip 7z rar tar gz tgz bz2 xz');
  const SERVER_THUMB = new Set('png jpg jpeg webp gif bmp tif tiff ico mp4 webm mov mkv avi m4v wmv txt md markdown rst log csv tsv json jsonl xml yaml yml toml ini cfg html htm css scss js jsx ts tsx mjs py rs go java kt c h cpp hpp cs rb php sh ps1 bat sql lua swift vue svelte srt vtt'.split(' '));
  const TEXT_PREVIEW = new Set('txt md markdown rst log csv tsv json jsonl xml yaml yml toml ini cfg html htm css scss js jsx ts tsx mjs py rs go java kt c h cpp hpp cs rb php sh ps1 bat sql lua swift vue svelte srt vtt'.split(' '));
  const catOf = (ext, dir) => dir ? 'folder' : (EXT_CAT[ext] || 'other');
  // Record sources (News, Tasks, ...) add their own groups; unknown keys
  // fall back to "other" so a card is never without a colour.
  const EXTRA_CATS = {};
  const catInfo = k => CATS[k] || EXTRA_CATS[k] || CATS.other;
  const registerCats = obj => { Object.keys(obj || {}).forEach(k => { EXTRA_CATS[k] = obj[k]; }); };
  const hex = c => '#' + c.toString(16).padStart(6, '0');
  const BRAND = {
    cyan: 0x00d4ff, violet: 0x7b61ff, magenta: 0xff0080, amber: 0xf59e0b,
    danger: 0xef4444, ink: 0x000103, fog: 0x03050d, nebula: 0x9aa0b8
  };
  const DAZZLE = { off: 0, subtle: 0.55, full: 1 };
  const FX_MS = { delete: 600, fail: 520, copy: 600, move: 600, share: 560, rename: 520, open: 560 };

  const fmtSize = b => {
    if (!b) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
    return (b / Math.pow(1024, i)).toFixed(i ? 1 : 0) + ' ' + u[i];
  };
  const fmtDate = t => {
    try { return new Date(t * 1000).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; }
  };
  const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

  function api(url, opts) {
    const tok = window.__FRIDAY_API_TOKEN || '';
    const headers = Object.assign({}, opts && opts.headers);
    if (tok) headers['X-Friday-Token'] = tok;
    return fetch(url, Object.assign({}, opts, { headers }));
  }
  const postJSON = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json().then(j => ({ ok: r.ok, j })));
  const qs = (root, path) => 'root=' + encodeURIComponent(root) + '&path=' + encodeURIComponent(path);

  // Build the item table from a scan. Index order = scan order (breadth first).
  function buildItems(scan) {
    const base = scan.path || '';
    const E = scan.entries || [];
    const idx = new Map();
    const items = E.map((e, i) => {
      const rel = e[0];
      const name = rel.slice(rel.lastIndexOf('/') + 1);
      const dot = name.lastIndexOf('.');
      const ext = !e[1] && dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
      idx.set(rel, i);
      return { i, rel, name, dir: !!e[1], size: e[2] || 0, mtime: e[3] || 0, ext, cat: catOf(ext, !!e[1]), parent: -1, depth: 0, kids: [] };
    });
    for (const it of items) {
      const cut = it.rel.lastIndexOf('/');
      const pr = cut > 0 ? it.rel.slice(0, cut) : '';
      if (pr && pr !== base && idx.has(pr)) {
        it.parent = idx.get(pr);
        items[it.parent].kids.push(it.i);
      }
    }
    for (const it of items) it.depth = it.parent < 0 ? 1 : items[it.parent].depth + 1;
    return items;
  }

  // Thumbnail slots: which item's tile occupies which atlas slot. A slot
  // belongs to exactly one item and an item holds at most one slot. claim()
  // takes a free slot, else evicts the least recently wanted slot whose item
  // is no longer wanted; it never evicts a wanted item.
  function createSlotPool(slots, cap) {
    const slotOf = new Int32Array(cap).fill(-1), itemIn = new Int32Array(slots).fill(-1), seen = new Float64Array(slots);
    return {
      slotOf, itemIn, seen,
      claim(i, wanted, now) {
        if (slotOf[i] >= 0) return { slot: slotOf[i], evicted: -1 };
        let best = -1, bestT = Infinity;
        for (let s = 0; s < slots; s++) {
          const it = itemIn[s];
          if (it < 0) { best = s; bestT = -Infinity; break; }
          if (!wanted[it] && seen[s] < bestT) { bestT = seen[s]; best = s; }
        }
        if (best < 0) return { slot: -1, evicted: -1 };
        const old = itemIn[best];
        if (old >= 0) slotOf[old] = -1;
        itemIn[best] = i; slotOf[i] = best; seen[best] = now;
        return { slot: best, evicted: old };
      },
      touch(i, now) { if (slotOf[i] >= 0) seen[slotOf[i]] = now; },
      release(i) { const s = slotOf[i]; if (s >= 0) { itemIn[s] = -1; slotOf[i] = -1; } return s; },
      adopt(i, s, now) { if (s >= 0 && s < slots && itemIn[s] < 0 && slotOf[i] < 0) { itemIn[s] = i; slotOf[i] = s; seen[s] = now || 0; return true; } return false; },
      used() { let u = 0; for (let s = 0; s < slots; s++) if (itemIn[s] >= 0) u++; return u; }
    };
  }

  // Where each pile of the Stacks view stands. Up to STACK_ARC piles share
  // one shallow arc that curves toward the viewer, so no pile sits alone on
  // a second row; beyond that, a balanced grid: rows differ by at most one
  // pile and every row is centred.
  const STACK_ARC = 7, STACK_GAP = 5.4, STACK_ROW = 12;
  function stackSlots(n) {
    const out = [];
    if (n <= STACK_ARC) {
      const R = Math.max(24, (n - 1) * STACK_GAP / (Math.PI * 0.55));   // span <= ~100 degrees
      for (let k = 0; k < n; k++) {
        const a = (k - (n - 1) / 2) * STACK_GAP / R;
        out.push({ x: R * Math.sin(a), y: 0, z: R * (1 - Math.cos(a)), yaw: -a, row: 0 });
      }
      return out;
    }
    const rows = Math.ceil(n / STACK_ARC), base = Math.floor(n / rows), extra = n % rows;
    let k = 0;
    for (let r = 0; r < rows; r++) {
      const inRow = base + (r < extra ? 1 : 0);
      for (let c = 0; c < inRow; c++, k++) out.push({ x: (c - (inRow - 1) / 2) * STACK_GAP, y: -r * STACK_ROW, z: 0, yaw: 0, row: r });
    }
    return out;
  }

  // ── engine ──────────────────────────────────────────────────────────────
  function createEngine(mount, cb) {
    const THREE = window.THREE;
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setClearColor(BRAND.ink, 1);
    mount.appendChild(renderer.domElement);
    renderer.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;outline:none;cursor:grab;touch-action:none';
    const scene = new THREE.Scene();
    const FOG = new THREE.Color(BRAND.fog);
    const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 4000);
    scene.add(new THREE.HemisphereLight(0xa9c8ff, 0x0b0f18, 0.95));
    const sun = new THREE.DirectionalLight(0xffffff, 0.75);
    sun.position.set(40, 90, 50);
    scene.add(sun);

    // starfield backdrop
    (() => {
      const N = 1800, pos = new Float32Array(N * 3);
      let sd = 424242;
      const rnd = () => (sd = (sd * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
      for (let i = 0; i < N; i++) {
        const r = 900 + rnd() * 600, th = rnd() * Math.PI * 2, ph = Math.acos(2 * rnd() - 1);
        pos[i * 3] = r * Math.sin(ph) * Math.cos(th); pos[i * 3 + 1] = r * Math.cos(ph); pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      scene.add(new THREE.Points(g, new THREE.PointsMaterial({ color: 0x6f8fbf, size: 1.6, sizeAttenuation: false, transparent: true, opacity: 0.55, fog: false })));
    })();

    // ── atmosphere: nebula, drifting dust, particles, selection glow, floor ──
    const clock0 = performance.now();
    const clock = () => (performance.now() - clock0) / 1000;
    let dz = DAZZLE.full, reduced = false;
    try {
      const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      reduced = mq.matches;
      if (mq.addEventListener) mq.addEventListener('change', e => { reduced = e.matches; applyDazzle(); });
    } catch (_) { /* no media queries: motion stays on */ }
    const loader = new THREE.TextureLoader();
    const TX = {};
    ['nebula_backdrop.jpg', 'star_glow.png', 'shockwave.png', 'spiral_haze.png'].forEach(f => {
      TX[f.split('.')[0]] = loader.load('/static/galaxy/' + f, () => { dirty = true; }, undefined, () => {});
    });
    const sky = new THREE.Mesh(new THREE.SphereGeometry(1400, 48, 24),
      new THREE.MeshBasicMaterial({ map: TX.nebula_backdrop, color: BRAND.nebula, side: THREE.BackSide, fog: false, depthWrite: false, transparent: true, opacity: 0 }));
    sky.renderOrder = -2;
    scene.add(sky);

    const DUST = 900;
    const dustGeo = new THREE.BufferGeometry();
    (() => {
      const pos = new Float32Array(DUST * 3), seed = new Float32Array(DUST);
      let sd = 97;
      const rnd = () => (sd = (sd * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
      for (let i = 0; i < DUST; i++) { pos[i * 3] = rnd() * 2 - 1; pos[i * 3 + 1] = rnd() * 2 - 1; pos[i * 3 + 2] = rnd() * 2 - 1; seed[i] = rnd(); }
      dustGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      dustGeo.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1));
    })();
    const px = renderer.getPixelRatio();
    const dustMat = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 }, uTex: { value: TX.star_glow }, uCenter: { value: new THREE.Vector3() }, uRegion: { value: 60 },
        uOpacity: { value: 0 }, uColA: { value: new THREE.Color(BRAND.cyan) }, uColB: { value: new THREE.Color(BRAND.violet) }, uPx: { value: px }
      },
      vertexShader: [
        'attribute float aSeed; uniform float uTime, uRegion, uPx; uniform vec3 uCenter; varying float vSeed; varying float vFade;',
        'void main(){ vSeed = aSeed;',
        '  vec3 drift = vec3(sin(uTime*0.07+aSeed*40.0), 0.6*sin(uTime*0.05+aSeed*23.0), cos(uTime*0.06+aSeed*31.0))*0.08;',
        '  vec4 mv = modelViewMatrix*vec4(uCenter + (position + drift)*uRegion, 1.0);',
        '  vFade = smoothstep(uRegion*0.05, uRegion*0.3, -mv.z) * (1.0 - smoothstep(uRegion*0.9, uRegion*1.6, -mv.z));',
        '  gl_PointSize = uPx*(1.0 + aSeed*2.5)*clamp(60.0/max(1.0,-mv.z), 0.6, 6.0);',
        '  gl_Position = projectionMatrix*mv; }'
      ].join('\n'),
      fragmentShader: [
        'uniform sampler2D uTex; uniform float uOpacity, uTime; uniform vec3 uColA, uColB; varying float vSeed; varying float vFade;',
        'void main(){ float a = texture2D(uTex, gl_PointCoord).r; float tw = 0.55 + 0.45*sin(uTime*(0.7+vSeed)+vSeed*50.0);',
        '  gl_FragColor = vec4(mix(uColA, uColB, vSeed), a*tw*vFade*uOpacity); }'
      ].join('\n'),
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending
    });
    const dust = new THREE.Points(dustGeo, dustMat);
    dust.frustumCulled = false;
    scene.add(dust);

    // One ring buffer of short-lived light particles: flight trails,
    // materialize sparks, the delete vortex, copy ghosts' wakes.
    const PN = 6000;
    const pGeo = new THREE.BufferGeometry();
    const pPos = new Float32Array(PN * 3), pVel = new Float32Array(PN * 3), pBirth = new Float32Array(PN).fill(-99),
      pLife = new Float32Array(PN).fill(1), pCol = new Float32Array(PN * 3), pSize = new Float32Array(PN);
    [['position', pPos, 3], ['aVel', pVel, 3], ['aBirth', pBirth, 1], ['aLife', pLife, 1], ['aCol', pCol, 3], ['aSize', pSize, 1]]
      .forEach(([k, a, w]) => pGeo.setAttribute(k, new THREE.BufferAttribute(a, w).setUsage(THREE.DynamicDrawUsage)));
    const partMat = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uTex: { value: TX.star_glow }, uPx: { value: px } },
      vertexShader: [
        'attribute vec3 aVel, aCol; attribute float aBirth, aLife, aSize; uniform float uTime, uPx; varying vec3 vCol; varying float vA;',
        'void main(){ float age = uTime - aBirth; float k = age / aLife;',
        '  if (k < 0.0 || k > 1.0) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; vA = 0.0; vCol = aCol; return; }',
        '  vec4 mv = modelViewMatrix*vec4(position + aVel*age, 1.0); vCol = aCol;',
        '  vA = (1.0-k)*(1.0-k)*smoothstep(0.0, 0.15, k + 0.04);',
        '  gl_PointSize = uPx*aSize*clamp(90.0/max(1.0,-mv.z), 1.0, 40.0); gl_Position = projectionMatrix*mv; }'
      ].join('\n'),
      fragmentShader: [
        'uniform sampler2D uTex; varying vec3 vCol; varying float vA;',
        'void main(){ float a = texture2D(uTex, gl_PointCoord).r; gl_FragColor = vec4(vCol, a*vA); }'
      ].join('\n'),
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending
    });
    const parts = new THREE.Points(pGeo, partMat);
    parts.frustumCulled = false;
    scene.add(parts);
    let pk = 0, pDirty = false;
    const pc = new THREE.Color();
    function spawn(x, y, z, vx, vy, vz, life, color, size) {
      if (dz <= 0 || reduced) return;
      const i = pk; pk = (pk + 1) % PN;
      pPos[i * 3] = x; pPos[i * 3 + 1] = y; pPos[i * 3 + 2] = z;
      pVel[i * 3] = vx; pVel[i * 3 + 1] = vy; pVel[i * 3 + 2] = vz;
      pBirth[i] = clock(); pLife[i] = life; pSize[i] = size;
      pc.set(color); pCol[i * 3] = pc.r; pCol[i * 3 + 1] = pc.g; pCol[i * 3 + 2] = pc.b;
      pDirty = true;
    }
    function flushParticles() {
      if (!pDirty) return;
      pDirty = false;
      for (const k of ['position', 'aVel', 'aBirth', 'aLife', 'aCol', 'aSize']) pGeo.attributes[k].needsUpdate = true;
    }

    const glowTex = (() => {
      const c = document.createElement('canvas'); c.width = c.height = 128;
      const x = c.getContext('2d'), g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
      g.addColorStop(0, 'rgba(255,255,255,0.9)'); g.addColorStop(0.35, 'rgba(255,255,255,0.25)'); g.addColorStop(1, 'rgba(255,255,255,0)');
      x.fillStyle = g; x.fillRect(0, 0, 128, 128);
      return new THREE.CanvasTexture(c);
    })();
    const additive = (map, color) => new THREE.SpriteMaterial({ map, color, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, fog: false });
    const selGlow = new THREE.Sprite(additive(glowTex, BRAND.cyan));
    selGlow.visible = false;
    scene.add(selGlow);

    const floorMat = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 }, uOpacity: { value: 0 }, uCenter: { value: new THREE.Vector3() }, uSpan: { value: 50 }, uCell: { value: 2 },
        uColA: { value: new THREE.Color(BRAND.cyan) }, uColB: { value: new THREE.Color(BRAND.violet) }
      },
      vertexShader: 'varying vec3 vW; void main(){ vec4 w = modelMatrix*vec4(position,1.0); vW = w.xyz; gl_Position = projectionMatrix*viewMatrix*w; }',
      fragmentShader: [
        'uniform float uTime, uOpacity, uSpan, uCell; uniform vec3 uCenter, uColA, uColB; varying vec3 vW;',
        'void main(){ vec2 q = (vW.xz - uCenter.xz)/uCell; vec2 g = abs(fract(q - 0.5) - 0.5)/fwidth(q);',
        '  float line = 1.0 - min(min(g.x, g.y), 1.0);',
        '  float d = length(vW.xz - uCenter.xz)/uSpan; float fade = 1.0 - smoothstep(0.2, 1.0, d);',
        '  float pulse = 1.0 - smoothstep(0.0, 0.025, abs(d - fract(uTime*0.05)));',
        '  vec3 col = mix(uColA, uColB, clamp(d*1.3, 0.0, 1.0));',
        '  float a = (line*0.28 + 0.035 + pulse*0.1)*fade*uOpacity;',
        '  gl_FragColor = vec4(col*(0.55 + line*0.7 + pulse*0.5), a); }'
      ].join('\n'),
      transparent: true, depthWrite: false, side: THREE.DoubleSide, extensions: { derivatives: true }
    });
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.visible = false;
    scene.add(floor);
    const fxSprites = [];   // ripples and vortices alive right now

    // ── thumbnail atlases ──
    const TILE = 128, ATLAS = 2048, PER = ATLAS / TILE, SLOTS_PER = PER * PER;
    const NATLAS = Math.max(1, Math.min(8, (renderer.capabilities.maxTextures || 16) - 2));
    const SLOTS = NATLAS * SLOTS_PER;
    // Atlases are created on first use: a small folder needs one, not eight.
    const atlases = [], real = [];
    const makeAtlas = () => {
      const t = new THREE.DataTexture(new Uint8Array(ATLAS * ATLAS * 4), ATLAS, ATLAS, THREE.RGBAFormat);
      t.minFilter = THREE.LinearMipmapLinearFilter; t.magFilter = THREE.LinearFilter;
      t.generateMipmaps = true; t.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
      t.needsUpdate = true;
      renderer.initTexture(t);
      t.image = { data: null, width: ATLAS, height: ATLAS }; // the GPU copy is the only copy
      return t;
    };
    real[0] = makeAtlas();
    for (let a = 0; a < 8; a++) atlases.push(real[0]);
    const ensureAtlas = a => {
      if (!real[a]) { real[a] = makeAtlas(); atlases[a] = real[a]; }
      return real[a];
    };

    const CARD_VS = [
      'attribute vec3 aTile; attribute vec3 aColor; attribute vec4 aState;',
      'uniform float uTime, uReduced;',
      'varying vec2 vUv; varying vec3 vTile; varying vec3 vColor; varying vec4 vState; varying float vDepth; varying float vFacing; varying float vWorldY;',
      'void main(){ vUv=uv; vTile=aTile; vColor=aColor; vState=aState;',
      '  vec4 wp = modelMatrix * instanceMatrix * vec4(position,1.0); vWorldY = wp.y;',
      '  vec4 mv = viewMatrix * wp;',
      '#ifndef REFLECT',
      '  mv.y += aState.z * (0.16 + (1.0 - uReduced)*0.06*sin(uTime*2.2));',   // held: hovering, waiting
      '#endif',
      '  vec3 nrm = normalize(mat3(viewMatrix) * mat3(modelMatrix) * mat3(instanceMatrix) * vec3(0.0, 0.0, 1.0));',
      '  vFacing = abs(nrm.z);',
      '  vDepth = -mv.z; gl_Position = projectionMatrix * mv; }'
    ].join('\n');
    const CARD_FS = [
      'uniform sampler2D uAtlas[8]; uniform float uTileUV; uniform float uPad; uniform vec3 uFog; uniform float uFogDensity;',
      'uniform float uTime, uDazzle, uReduced, uFloorY, uReflect;',
      'uniform vec3 uCyan, uViolet, uMagenta, uAmber, uDanger;',
      'varying vec2 vUv; varying vec3 vTile; varying vec3 vColor; varying vec4 vState; varying float vDepth; varying float vFacing; varying float vWorldY;',
      'float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7)))*43758.5453); }',
      'float vnoise(vec2 p){ vec2 i = floor(p), f = fract(p); vec2 u = f*f*(3.0-2.0*f);',
      '  return mix(mix(hash(i), hash(i+vec2(1.0,0.0)), u.x), mix(hash(i+vec2(0.0,1.0)), hash(i+vec2(1.0,1.0)), u.x), u.y); }',
      'vec4 atl(float i, vec2 uv){',
      '  if(i<0.5) return texture2D(uAtlas[0],uv); if(i<1.5) return texture2D(uAtlas[1],uv);',
      '  if(i<2.5) return texture2D(uAtlas[2],uv); if(i<3.5) return texture2D(uAtlas[3],uv);',
      '  if(i<4.5) return texture2D(uAtlas[4],uv); if(i<5.5) return texture2D(uAtlas[5],uv);',
      '  if(i<6.5) return texture2D(uAtlas[6],uv); return texture2D(uAtlas[7],uv); }',
      'vec3 prism(float x){ float t = 0.5 + 0.5*sin(x); return t < 0.5 ? mix(uCyan, uViolet, t*2.0) : mix(uViolet, uMagenta, t*2.0 - 1.0); }',
      'void main(){',
      '  vec2 uv = vUv; float edge = min(min(uv.x, 1.0-uv.x), min(uv.y, 1.0-uv.y));',
      '  float t = uTime*(1.0 - uReduced); float D = uDazzle;',
      // dissolve (delete) and materialize (folder open) share one threshold
      '  float diss = vState.w, burn = 0.0;',
      '  if (diss > 0.001) { float nz = vnoise(uv*5.5 + vTile.yz*97.0 + vColor.rg*13.0)*0.96 + hash(uv*61.0)*0.04;',
      '    if (nz < diss) discard; burn = 1.0 - smoothstep(0.0, 0.12, nz - diss); }',
      '  vec3 col;',
      '  if (vTile.x < -0.5) { col = mix(vColor*0.12, vColor*0.36, uv.y); col = mix(col, vColor*0.7, (1.0-step(0.2, uv.y))*0.5); }',
      '  else { vec2 tt = vec2(uv.x, 1.0-uv.y)*(1.0-2.0*uPad)+uPad; col = atl(vTile.x, vTile.yz + tt*uTileUV).rgb; }',
      // holographic texture on the picture only; the name strip (bottom 22 %) is left untouched
      '  float img = step(0.22, uv.y);',
      '  col *= 1.0 - D*0.045*(0.5 + 0.5*sin(uv.y*150.0 - t*3.0))*img;',
      '  col += (hash(uv*vec2(413.0, 297.0) + fract(t*3.7)) - 0.5)*0.035*D*img;',
      '  float sweep = fract(t*0.06 + vTile.y*7.0 + vColor.b*3.0)*2.6 - 0.8;',
      '  col += smoothstep(0.1, 0.0, abs(uv.x*0.8 + uv.y*0.5 - sweep))*0.10*D*img;',
      '  col = mix(col, col*vec3(0.93, 1.0, 1.07) + uCyan*0.025, 0.5*D);',
      '  if (!gl_FrontFacing) col = mix(vColor*0.12, uViolet*0.18, 0.5);',
      '  float st = vState.x;',
      '  float bw = st > 1.5 ? 0.06 : 0.035;',
      '  float border = 1.0 - smoothstep(0.0, bw, edge);',
      '  float fres = pow(1.0 - clamp(vFacing, 0.0, 1.0), 2.0);',
      '  vec3 shimmer = prism(t*1.6 + (uv.x - uv.y)*5.0);',
      '  vec3 rim;',
      '  if (st > 2.5) rim = uDanger;',
      '  else if (st > 1.5) rim = mix(vec3(0.6, 0.97, 1.0), shimmer, 0.35*D);',
      '  else if (st > 0.5) rim = mix(vColor*1.6, shimmer, D);',
      '  else rim = mix(vColor, mix(vColor, uCyan, 0.35), D);',
      '  col = mix(col, rim, border*(0.5 + 0.5*min(st, 1.0)));',
      '  col += rim*fres*0.35*D;',
      '  if (st > 0.5 && st < 2.5) col += vec3(0.04, 0.06, 0.09);',
      '  float held = vState.z;',
      '  if (held > 0.0) { float pulse = 0.7 + 0.3*sin(t*4.0); float hb = 1.0 - smoothstep(0.02, 0.1, edge);',
      '    col = mix(col, uAmber*1.35, hb*held*pulse*0.95); col += uAmber*0.09*held*pulse; }',
      '  if (vState.y > 0.5) { float mb = 1.0 - smoothstep(0.02, 0.085, edge);',   // marked: one of several selected
      '    col = mix(col, uMagenta*1.3, mb*0.9); col += uMagenta*0.07; }',
      '  col += mix(uCyan, uMagenta, burn)*burn*1.6;',
      '  float f = 1.0 - exp(-uFogDensity*uFogDensity*vDepth*vDepth);',
      '  col = mix(col, uFog, f);',
      '#ifdef REFLECT',
      '  if (vWorldY > uFloorY) discard;',
      '  gl_FragColor = vec4(col*exp(-(uFloorY - vWorldY)*0.45)*0.32*uReflect, 1.0);',
      '#else',
      '  gl_FragColor = vec4(col, 1.0);',
      '#endif',
      '}'
    ].join('\n');
    const cardUniforms = {
      uAtlas: { value: atlases }, uTileUV: { value: TILE / ATLAS }, uPad: { value: 1.5 / TILE },
      uFog: { value: FOG }, uFogDensity: { value: 0.0055 },
      uTime: { value: 0 }, uDazzle: { value: 1 }, uReduced: { value: 0 }, uFloorY: { value: -1e4 }, uReflect: { value: 0 },
      uCyan: { value: new THREE.Color(BRAND.cyan) }, uViolet: { value: new THREE.Color(BRAND.violet) },
      uMagenta: { value: new THREE.Color(BRAND.magenta) }, uAmber: { value: new THREE.Color(BRAND.amber) }, uDanger: { value: new THREE.Color(BRAND.danger) }
    };
    const cardMat = new THREE.ShaderMaterial({ uniforms: cardUniforms, vertexShader: CARD_VS, fragmentShader: CARD_FS, side: THREE.DoubleSide });
    // The floor reflection is the same cards drawn once more through a
    // mirror matrix, faded with depth below the floor and added as light.
    const reflMat = new THREE.ShaderMaterial({
      uniforms: cardUniforms, vertexShader: CARD_VS, fragmentShader: CARD_FS, defines: { REFLECT: '' },
      side: THREE.DoubleSide, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending
    });
    const boxMat = new THREE.MeshLambertMaterial({ color: 0xffffff });
    const boxGeo = new THREE.BoxGeometry(1, 1, 1); boxGeo.translate(0, 0.5, 0);
    const planeGeo = new THREE.PlaneGeometry(1, 1);
    const lineMat = new THREE.LineBasicMaterial({ color: 0x3d7fd0, transparent: true, opacity: 0, depthWrite: false });

    // ── per-item state ──
    let items = [], n = 0;
    let cards = null, boxes = null, lines = null, lineGeo = null, edges = null, refl = null;
    let heldArr = new Uint8Array(1), markArr = new Uint8Array(1), diss = new Float32Array(1), materialize = null;
    let aTile, aColor, aState;
    let P, Q, S, fP, fQ, fS, tP, tQ, tS, delay;           // cards: current / from / to
    let BP, BS, fBP, fBS, tBP, tBS;                        // boxes (city): position / size
    let visMask = null, hoverIdx = -1, selIdx = -1;
    let pool = null, wanted, failed;
    let view = 'wall', groupBy = 'type', layout = null;
    let flight = null, bbCur = 0, bbFrom = 0, bbTo = 0;
    let decor = null, oldDecor = [];
    let gen = 0;

    const cam = { t: new THREE.Vector3(), theta: 0, phi: Math.PI / 2, r: 40 };
    const goal = { t: new THREE.Vector3(), theta: 0, phi: Math.PI / 2, r: 40 };
    let camMoving = true, dirty = true, disposed = false, visible = true;
    const tmpM = new THREE.Matrix4(), tmpQ = new THREE.Quaternion(), tmpV = new THREE.Vector3(), tmpS = new THREE.Vector3(), tmpE = new THREE.Euler();
    const camQ = new THREE.Quaternion();

    function freeMeshes() {
      skipFx();
      if (refl) scene.remove(refl);
      [cards, boxes, lines].forEach(m => { if (m) { scene.remove(m); if (m.geometry !== planeGeo && m.geometry !== boxGeo) m.geometry.dispose(); } });
      cards = boxes = lines = refl = null;
    }

    function setData(list, fresh) {
      gen++;
      // A refresh of the same folder keeps each surviving file's tile and
      // place, so the rest glide together and only new files materialize.
      const keep = !fresh && n && pool ? new Map() : null;
      if (keep) for (let i = 0; i < n; i++) {
        keep.set(items[i].rel + '|' + items[i].mtime, {
          s: pool.slotOf[i], t: [aTile.getX(i), aTile.getY(i), aTile.getZ(i)],
          p: [P[i * 3], P[i * 3 + 1], P[i * 3 + 2]], q: [Q[i * 4], Q[i * 4 + 1], Q[i * 4 + 2], Q[i * 4 + 3]], sc: [S[i * 2], S[i * 2 + 1]]
        });
      }
      freeMeshes();
      items = list; n = list.length;
      const cap = Math.max(1, n);
      const g = planeGeo.clone();
      aTile = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3).fill(-1), 3);
      aColor = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3), 3);
      aState = new THREE.InstancedBufferAttribute(new Float32Array(cap * 4), 4);
      aTile.setUsage(THREE.DynamicDrawUsage); aState.setUsage(THREE.DynamicDrawUsage);
      g.setAttribute('aTile', aTile); g.setAttribute('aColor', aColor); g.setAttribute('aState', aState);
      cards = new THREE.InstancedMesh(g, cardMat, cap);
      cards.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      cards.frustumCulled = false;
      cards.count = n;
      scene.add(cards);
      refl = new THREE.InstancedMesh(g, reflMat, cap);
      refl.instanceMatrix = cards.instanceMatrix;
      refl.count = n; refl.frustumCulled = false; refl.matrixAutoUpdate = false; refl.visible = false;
      scene.add(refl);
      heldArr = new Uint8Array(cap); markArr = new Uint8Array(cap); diss = new Float32Array(cap);
      boxes = new THREE.InstancedMesh(boxGeo, boxMat, cap);
      boxes.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      boxes.frustumCulled = false; boxes.count = n;
      const c = new THREE.Color();
      for (let i = 0; i < n; i++) {
        c.setHex(items[i].color != null ? items[i].color : catInfo(items[i].cat).color);
        aColor.setXYZ(i, c.r, c.g, c.b);
        if (items[i].dir) c.setHSL(0.6, 0.35, 0.12 + Math.min(0.2, items[i].depth * 0.035));
        else c.multiplyScalar(0.55);
        boxes.setColorAt(i, c);
      }
      scene.add(boxes);
      // tree edges: one segment per item with a parent (root-level items join the root at the origin)
      edges = new Int32Array(n);
      for (let i = 0; i < n; i++) edges[i] = items[i].parent;
      lineGeo = new THREE.BufferGeometry();
      lineGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(cap * 6), 3).setUsage(THREE.DynamicDrawUsage));
      lines = new THREE.LineSegments(lineGeo, lineMat);
      lines.frustumCulled = false;
      scene.add(lines);
      const f = k => new Float32Array(cap * k);
      P = f(3); Q = f(4); S = f(2); fP = f(3); fQ = f(4); fS = f(2); tP = f(3); tQ = f(4); tS = f(2); delay = f(1);
      BP = f(3); BS = f(3); fBP = f(3); fBS = f(3); tBP = f(3); tBS = f(3);
      for (let i = 0; i < n; i++) { Q[i * 4 + 3] = 1; delay[i] = ((i * 2654435761) % 1000) / 1000 * 0.35; }
      visMask = null; hoverIdx = -1; selIdx = -1;
      pool = createSlotPool(SLOTS, cap);
      wanted = new Uint8Array(cap); failed = new Uint8Array(cap);
      loadQueue.length = 0; uploads.length = 0;
      if (!keep) {
        applyLayout(true);
        if (fresh) startMaterialize(); else { materialize = null; diss.fill(0); }
        return;
      }
      const born = [];
      for (let i = 0; i < n; i++) {
        const k = keep.get(items[i].rel + '|' + items[i].mtime);
        if (!k) { born.push(i); continue; }
        if (k.s >= 0 && pool.adopt(i, k.s, performance.now())) aTile.setXYZ(i, k.t[0], k.t[1], k.t[2]);
        P.set(k.p, i * 3); Q.set(k.q, i * 4); S.set(k.sc, i * 2);
      }
      diss.fill(0);
      applyLayout(false, true);
      for (const i of born) {   // new arrivals appear in place, from light
        P.set(tP.subarray(i * 3, i * 3 + 3), i * 3); fP.set(tP.subarray(i * 3, i * 3 + 3), i * 3);
        Q.set(tQ.subarray(i * 4, i * 4 + 4), i * 4); fQ.set(tQ.subarray(i * 4, i * 4 + 4), i * 4);
        S.set(tS.subarray(i * 2, i * 2 + 2), i * 2); fS.set(tS.subarray(i * 2, i * 2 + 2), i * 2);
      }
      materialize = null;
      if (born.length && dz > 0 && !reduced) {
        const delayMs = new Float32Array(n);
        for (let i = 0; i < n; i++) diss[i] = 0;
        born.forEach(i => { diss[i] = 1; delayMs[i] = 250; });
        materialize = { t0: performance.now(), delayMs, dur: 520, only: new Set(born) };
      }
      aTile.needsUpdate = true;
    }

    // Opening a folder: the cards assemble from light, nearest the centre
    // first, while sparks converge on them.
    function startMaterialize() {
      materialize = null;
      if (!n || dz <= 0 || reduced || !layout) { diss.fill(0); return; }
      const o = layout.order, c = layout.cam.t;
      let far = 1;
      const dist = new Float32Array(n);
      for (const i of o) { dist[i] = Math.hypot(P[i * 3] - c[0], P[i * 3 + 1] - c[1], P[i * 3 + 2] - c[2]); if (dist[i] > far) far = dist[i]; }
      const spread = 380 + 320 * dz;
      const delayMs = new Float32Array(n);
      for (let i = 0; i < n; i++) { delayMs[i] = dist[i] / far * spread; diss[i] = 1; }
      materialize = { t0: performance.now(), delayMs, dur: 420 };
      const step = Math.max(1, Math.ceil(o.length / (dz >= 1 ? 420 : 200)));
      for (let k = 0; k < o.length; k += step) {
        const i = o[k], s0 = Math.max(0.6, S[i * 2]), life = 0.45 + delayMs[i] / 1000;
        for (let j = 0; j < 2; j++) {
          const ox = (Math.random() - 0.5) * s0 * 4, oy = (Math.random() - 0.5) * s0 * 4, oz = (Math.random() - 0.5) * s0 * 4;
          spawn(P[i * 3] + ox, P[i * 3 + 1] + oy, P[i * 3 + 2] + oz, -ox / life, -oy / life, -oz / life, life, j ? BRAND.cyan : BRAND.violet, 0.9);
        }
      }
    }
    function stepMaterialize(now) {
      if (!materialize) return false;
      let done = true;
      const m = materialize, arr = aState.array;
      for (let i = 0; i < n; i++) {
        let k = (now - m.t0 - m.delayMs[i]) / m.dur;
        if (k < 1) done = false;
        k = k < 0 ? 0 : k > 1 ? 1 : k;
        if (m.only && !m.only.has(i)) { arr[i * 4 + 3] = diss[i]; continue; }
        diss[i] = fxOf.has(i) ? diss[i] : 1 - k * k * (3 - 2 * k);
        arr[i * 4 + 3] = diss[i];
      }
      aState.needsUpdate = true;
      if (done) materialize = null;
      return true;
    }

    // ── layouts ──
    const GOLD = Math.PI * (3 - Math.sqrt(5));
    const yawQ = (yaw, pitch) => tmpQ.setFromEuler(tmpE.set(pitch || 0, yaw, 0, 'YXZ'));
    const active = () => {
      const a = [];
      for (let i = 0; i < n; i++) if (!visMask || visMask[i]) a.push(i);
      return a;
    };
    const byPath = (a, b) => items[a].rel.localeCompare(items[b].rel, undefined, { numeric: true, sensitivity: 'base' });

    function computeLayout(v) {
      const act = active();
      const L = { P: new Float32Array(n * 3), Q: new Float32Array(n * 4), S: new Float32Array(n * 2), BP: new Float32Array(n * 3), BS: new Float32Array(n * 3), bb: 0, lines: 0, cam: null, labels: [], rings: [], order: act };
      // hidden cards stay where they are and shrink to nothing
      for (let i = 0; i < n; i++) {
        L.P[i * 3] = P[i * 3]; L.P[i * 3 + 1] = P[i * 3 + 1]; L.P[i * 3 + 2] = P[i * 3 + 2];
        L.Q[i * 4] = Q[i * 4]; L.Q[i * 4 + 1] = Q[i * 4 + 1]; L.Q[i * 4 + 2] = Q[i * 4 + 2]; L.Q[i * 4 + 3] = Q[i * 4 + 3];
        L.BP[i * 3] = BP[i * 3]; L.BP[i * 3 + 2] = BP[i * 3 + 2];
      }
      const put = (i, x, y, z, q, w, hgt) => {
        L.P[i * 3] = x; L.P[i * 3 + 1] = y; L.P[i * 3 + 2] = z;
        L.Q[i * 4] = q.x; L.Q[i * 4 + 1] = q.y; L.Q[i * 4 + 2] = q.z; L.Q[i * 4 + 3] = q.w;
        L.S[i * 2] = w; L.S[i * 2 + 1] = hgt == null ? w : hgt;
      };
      const m = act.length;
      const fovT = Math.tan(camera.fov * Math.PI / 360);
      if (v === 'wall') {
        const ord = act.slice().sort(byPath);
        const R = Math.max(1, Math.min(60, Math.round(Math.sqrt(m / 2.2))));
        const cols = Math.max(1, Math.ceil(m / R)), s = 1.18;
        const rad = Math.max(24, cols * s / (Math.PI * 0.85));
        ord.forEach((i, k) => {
          const c = Math.floor(k / R), r = k % R;
          const a = (c - (cols - 1) / 2) * s / rad;
          put(i, rad * Math.sin(a), ((R - 1) / 2 - r) * s, rad * (1 - Math.cos(a)), yawQ(-a), 1);
        });
        L.order = ord;
        L.cam = { t: [0, 0, 0], theta: 0, phi: Math.PI / 2, r: Math.max(8, (R * s / 2 + 1) / fovT * 1.05) };
      } else if (v === 'ring') {
        const ord = act.slice().sort(byPath);
        const per = Math.max(8, Math.min(48, m)), rr = Math.max(6, per * 2.3 / (2 * Math.PI)), pitch = 2.9;
        ord.forEach((i, k) => {
          const a = k * 2 * Math.PI / per;
          put(i, rr * Math.sin(a), -(k / per) * pitch, rr * Math.cos(a), yawQ(a), 1.6);
        });
        L.order = ord; L.ring = { per, rr, pitch, ord };
        L.cam = ringCam(L, selIdx >= 0 ? ord.indexOf(selIdx) : 0);
      } else if (v === 'tree') {
        treeLayout(L, act, put);
      } else if (v === 'city') {
        cityLayout(L, act, put);
      } else if (v === 'time') {
        const ord = act.slice().sort((a, b) => items[b].mtime - items[a].mtime);
        const rt = 7, per = 12;
        let z = -4, lastKey = null;
        ord.forEach((i, k) => {
          const d = new Date(items[i].mtime * 1000), key = d.getFullYear() * 12 + d.getMonth();
          if (key !== lastKey) {
            if (lastKey !== null) z -= 3.5;
            L.rings.push({ z: z + 1.6, text: MONTHS[d.getMonth()] + ' ' + d.getFullYear() });
            lastKey = key;
          }
          const a = k * 2 * Math.PI / per + Math.PI / 2;
          const x = rt * Math.cos(a), y = rt * Math.sin(a);
          tmpV.set(-x, -y, rt * 1.1).normalize();
          tmpM.lookAt(tmpV, ORIGIN, UP);
          tmpQ.setFromRotationMatrix(tmpM);
          put(i, x, y, z, tmpQ, 1.35);
          z -= 0.42;
        });
        L.order = ord; L.time = { ord, minZ: z };
        L.cam = timeCam(L, selIdx >= 0 ? L.P[selIdx * 3 + 2] : 0);
      } else if (v === 'week') {
        weekLayout(L, act, put);
      } else if (v === 'orbit') {
        orbitLayout(L, act, put);
      } else if (v === 'stack') {
        stackLayout(L, act, put);
      } else {
        clusterLayout(L, act, put);
      }
      if (!L.cam) L.cam = { t: [0, 0, 0], theta: 0, phi: 1.2, r: 60 };
      // A floor under everything, for the views that stand on one.
      if (m && (v === 'wall' || v === 'tree' || v === 'city' || v === 'cluster' || v === 'week' || v === 'stack')) {
        let y0 = Infinity, x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
        for (const i of act) {
          const hs = (L.S[i * 2 + 1] || 0) / 2;
          y0 = Math.min(y0, L.P[i * 3 + 1] - hs);
          x0 = Math.min(x0, L.P[i * 3]); x1 = Math.max(x1, L.P[i * 3]); z0 = Math.min(z0, L.P[i * 3 + 2]); z1 = Math.max(z1, L.P[i * 3 + 2]);
        }
        const span = Math.max(x1 - x0, z1 - z0) * 0.75 + 14;
        L.floor = { y: v === 'city' ? -0.03 : y0 - 1.4, cx: (x0 + x1) / 2, cz: (z0 + z1) / 2, span, cell: v === 'city' ? 1 : 2, mirror: v !== 'city' };
      }
      return L;
    }
    // Frame every visible card (and city block) from the view's direction:
    // aim at the centre of their bounds, then back off just far enough that
    // each one projects inside the narrower half of the field of view.
    function fitCam(L, act, theta, phi, margin) {
      if (!act.length) return { t: [0, 0, 0], theta, phi, r: 20 };
      let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
      for (const i of act) {
        const x = L.P[i * 3], y = L.P[i * 3 + 1], z = L.P[i * 3 + 2];
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; if (z < z0) z0 = z; if (z > z1) z1 = z;
      }
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, cz = (z0 + z1) / 2;
      const sp = Math.sin(phi);
      const f = [sp * Math.sin(theta), Math.cos(phi), sp * Math.cos(theta)];      // centre -> camera
      const rt = [Math.cos(theta), 0, -Math.sin(theta)];                          // camera right
      const up = [f[1] * rt[2] - f[2] * rt[1], f[2] * rt[0] - f[0] * rt[2], f[0] * rt[1] - f[1] * rt[0]];
      const tv = Math.tan(camera.fov * Math.PI / 360), th = tv * Math.max(0.3, camera.aspect);
      let r = 6;
      const need = (dx, dy, dz, pad) => {
        const px = Math.abs(dx * rt[0] + dy * rt[1] + dz * rt[2]) + pad;
        const py = Math.abs(dx * up[0] + dy * up[1] + dz * up[2]) + pad;
        const pz = dx * f[0] + dy * f[1] + dz * f[2] + pad;
        r = Math.max(r, pz + px / th, pz + py / tv);
      };
      for (const i of act) need(L.P[i * 3] - cx, L.P[i * 3 + 1] - cy, L.P[i * 3 + 2] - cz, (L.S[i * 2] || 0) / 2);
      for (const l of L.labels) need(l.pos[0] - cx, l.pos[1] - cy, l.pos[2] - cz, (l.size || 1) * 0.8);
      return { t: [cx, cy, cz], theta, phi, r: r * (margin || 1.04) };
    }
    const ORIGIN = new THREE.Vector3(), UP = new THREE.Vector3(0, 1, 0);

    function ringCam(L, k) {
      const R = L.ring;
      k = Math.max(0, k);
      const a = k * 2 * Math.PI / R.per;
      return { t: [R.rr * 0.35 * Math.sin(a), -(k / R.per) * R.pitch - 1.1, R.rr * 0.35 * Math.cos(a)], theta: a, phi: Math.PI / 2, r: R.rr * 0.65 + 6 };
    }
    function timeCam(L, z) {
      return { t: [0, 0, Math.min(-2, z)], theta: 0, phi: Math.PI / 2, r: 10 };
    }

    function treeLayout(L, act, put) {
      // Cone tree: a folder's files form a sunflower disc below it and its
      // sub-folders stand on a ring around that disc, spaced by the width of
      // their own subtrees, so nothing overlaps however deep it goes.
      const inAct = new Uint8Array(n);
      act.forEach(i => { inAct[i] = 1; });
      const kids = new Map(); // parent (-1 = root) -> visible children
      const vparent = i => { let p = items[i].parent; while (p >= 0 && !inAct[p]) p = items[p].parent; return p; };
      const vp = new Int32Array(n).fill(-2);
      act.forEach(i => { const p = vparent(i); vp[i] = p; if (!kids.has(p)) kids.set(p, []); kids.get(p).push(i); });
      const rad = new Map();
      const radius = node => {
        if (rad.has(node)) return rad.get(node);
        const ch = kids.get(node) || [];
        const files = ch.filter(i => !items[i].dir), dirs = ch.filter(i => items[i].dir);
        const rf = files.length ? 0.72 * Math.sqrt(files.length) + 0.6 : 0;
        let r = rf;
        if (dirs.length) {
          const rs = dirs.map(radius), circ = rs.reduce((s, x) => s + 2 * x + 1.4, 0);
          const ringR = Math.max(rf + Math.max.apply(null, rs) + 1.5, circ / (2 * Math.PI), dirs.length > 1 ? 3 : 0);
          r = Math.max(rf, ringR + Math.max.apply(null, rs));
        }
        r = Math.max(r, 0.8);
        rad.set(node, r);
        return r;
      };
      let maxD = 0;
      act.forEach(i => { if (items[i].depth > maxD) maxD = items[i].depth; });
      const Dy = Math.max(7, radius(-1) * 0.9 / Math.max(1, maxD));
      let deepest = 0;
      const place = (node, x, y, z) => {
        const ch = kids.get(node) || [];
        const files = ch.filter(i => !items[i].dir).sort(byPath), dirs = ch.filter(i => items[i].dir).sort(byPath);
        files.forEach((i, j) => {
          const rr = 0.72 * Math.sqrt(j + 0.5), th = j * GOLD;
          put(i, x + rr * Math.cos(th), y - Dy * 0.55, z + rr * Math.sin(th), IDQ, 0.9);
        });
        if (dirs.length) {
          const rs = dirs.map(radius), rf = files.length ? 0.72 * Math.sqrt(files.length) + 0.6 : 0;
          const circ = rs.reduce((s, r) => s + 2 * r + 1.4, 0);
          const ringR = dirs.length === 1 ? 0 : Math.max(rf + Math.max.apply(null, rs) + 1.5, circ / (2 * Math.PI), 3);
          let a = 0;
          dirs.forEach((i, j) => {
            a += (rs[j] + 0.7) / circ * 2 * Math.PI;
            const cx = x + ringR * Math.cos(a), cz = z + ringR * Math.sin(a), cy = y - Dy;
            deepest = Math.min(deepest, cy);
            put(i, cx, cy, cz, IDQ, 1.5);
            place(i, cx, cy, cz);
            a += (rs[j] + 0.7) / circ * 2 * Math.PI;
          });
        }
      };
      place(-1, 0, 0, 0);
      L.bb = 1; L.lines = 1; L.vparent = vp;
      L.labels.push({ text: (currentBase || 'FOLDER').toUpperCase(), pos: [0, 2.2, 0], color: 0x9fd0ff, size: 1.3 });
      L.cam = fitCam(L, act, 0.5, 1.05);
    }
    const IDQ = new THREE.Quaternion();

    function cityLayout(L, act, put) {
      // Squarified treemap: every file owns a plot, folders are raised plates,
      // tower height is the file's size on a log scale.
      const inAct = new Uint8Array(n);
      act.forEach(i => { inAct[i] = 1; });
      const kids = new Map();
      const vparent = i => { let p = items[i].parent; while (p >= 0 && !inAct[p]) p = items[p].parent; return p; };
      act.forEach(i => { const p = vparent(i); if (!kids.has(p)) kids.set(p, []); kids.get(p).push(i); });
      const wt = new Map();
      const weight = node => {
        if (wt.has(node)) return wt.get(node);
        let w = node >= 0 && !items[node].dir ? 1 : 0.6;
        (kids.get(node) || []).forEach(k => { w += weight(k); });
        wt.set(node, w);
        return w;
      };
      const total = weight(-1);
      const side = Math.sqrt(total * 2.4) + 2;
      const worst = (row, w, len) => {
        let s = 0, mx = 0, mn = Infinity;
        row.forEach(r => { s += r.a; mx = Math.max(mx, r.a); mn = Math.min(mn, r.a); });
        const l2 = len * len, s2 = s * s;
        return Math.max(l2 * mx / s2, s2 / (l2 * mn));
      };
      const squarify = (list, x, z, w, d, out) => {
        // list: [{id, a}] with areas summing to w*d
        let rest = list.slice();
        while (rest.length) {
          const len = Math.min(w, d);
          let row = [rest[0]], k = 1;
          while (k < rest.length && worst(row.concat([rest[k]]), 0, len) <= worst(row, 0, len)) { row.push(rest[k]); k++; }
          rest = rest.slice(k);
          const sum = row.reduce((s, r) => s + r.a, 0);
          if (w >= d) {
            const cw = sum / d; let cz = z;
            row.forEach(r => { const rd = r.a / cw; out.push([r.id, x, cz, cw, rd]); cz += rd; });
            x += cw; w -= cw;
          } else {
            const cd = sum / w; let cx = x;
            row.forEach(r => { const rw = r.a / cd; out.push([r.id, cx, z, rw, cd]); cx += rw; });
            z += cd; d -= cd;
          }
        }
      };
      const level = 0.28;
      const order = [];
      const lay = (node, x, z, w, d, depth) => {
        const ch = (kids.get(node) || []).slice().sort((a, b) => weight(b) - weight(a));
        if (!ch.length) return;
        const pad = node >= 0 ? Math.min(0.5, Math.min(w, d) * 0.08) : 0.4;
        x += pad; z += pad; w = Math.max(0.01, w - 2 * pad); d = Math.max(0.01, d - 2 * pad);
        const sum = ch.reduce((s, i) => s + weight(i), 0);
        const rects = [];
        squarify(ch.map(i => ({ id: i, a: weight(i) / sum * w * d })), x, z, w, d, rects);
        rects.forEach(([i, rx, rz, rw, rd]) => {
          const it = items[i], cx = rx + rw / 2 - side / 2, cz = rz + rd / 2 - side / 2, base = depth * level;
          order.push(i);
          if (it.dir) {
            L.BP[i * 3] = cx; L.BP[i * 3 + 1] = base; L.BP[i * 3 + 2] = cz;
            L.BS[i * 3] = Math.max(0.05, rw - 0.12); L.BS[i * 3 + 1] = level; L.BS[i * 3 + 2] = Math.max(0.05, rd - 0.12);
            const cs = Math.min(1.4, rw * 0.3, rd * 0.3);
            put(i, rx - side / 2 + cs / 2 + 0.12, base + level + 0.03, rz - side / 2 + cs / 2 + 0.12, FLATQ, cs);
            if (depth <= 1 && rw * rd > 6) L.labels.push({ area: rw * rd, text: it.name, pos: [cx, base + level + 1.2, rz - side / 2 + 0.4], color: 0x9fd0ff, size: Math.min(2.2, Math.max(0.6, Math.sqrt(rw * rd) * 0.09)) });
            lay(i, rx, rz, rw, rd, depth + 1);
          } else {
            const f = Math.max(0.2, Math.min(2.6, Math.min(rw, rd) * 0.78));
            const hgt = 0.25 + 1.2 * Math.log10(1 + it.size / 1024);
            L.BP[i * 3] = cx; L.BP[i * 3 + 1] = base; L.BP[i * 3 + 2] = cz;
            L.BS[i * 3] = f; L.BS[i * 3 + 1] = hgt; L.BS[i * 3 + 2] = f;
            put(i, cx, base + hgt + 0.02, cz, FLATQ, f * 0.96);
          }
        });
      };
      lay(-1, 0, 0, side, side, 0);
      // name only the biggest districts: every label is a draw call
      L.labels = L.labels.sort((a, b) => b.area - a.area).slice(0, 24);
      L.order = order;
      L.cam = fitCam(L, act, 0, 0.9);
    }
    const FLATQ = new THREE.Quaternion().setFromEuler(new THREE.Euler(-Math.PI / 2, 0, 0));

    // Week: one column per day, time of day running down, each event as
    // tall as it is long; events that overlap step toward the viewer.
    const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
    function weekLayout(L, act, put) {
      const dayKey = t => { const d = new Date(t * 1000); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); };
      const days = Array.from(new Set(act.map(i => dayKey(items[i].mtime)))).sort((a, b) => a - b);
      const col = new Map(days.map((d, k) => [d, k]));
      const W = 4.4, HOUR = 1.05, TOP = 7;           // 07:00 sits at the top
      const byDay = new Map();
      act.forEach(i => { const k = dayKey(items[i].mtime); if (!byDay.has(k)) byDay.set(k, []); byDay.get(k).push(i); });
      const order = [];
      days.forEach((d, c) => {
        const list = byDay.get(d).sort((a, b) => items[a].mtime - items[b].mtime);
        const ends = [];                                 // lane -> end time
        list.forEach(i => {
          const it = items[i], dt = new Date(it.mtime * 1000);
          const hr = dt.getHours() + dt.getMinutes() / 60, dur = Math.max(0.5, (it.dur || 3600) / 3600);
          let lane = ends.findIndex(e => e <= it.mtime);
          if (lane < 0) { lane = ends.length; ends.push(0); }
          ends[lane] = it.mtime + (it.dur || 3600);
          const hgt = Math.min(6, dur * HOUR);
          const x = (c - (days.length - 1) / 2) * W + lane * 0.35, y = -(hr - TOP) * HOUR - hgt / 2, z = lane * 0.6;
          put(i, x, y, z, IDQ, W * 0.84 - lane * 0.2, Math.max(1.0, hgt));
          order.push(i);
        });
        const dd = new Date(d);
        L.labels.push({ text: DAYS[dd.getDay()] + ' ' + dd.getDate() + ' ' + MONTHS[dd.getMonth()], pos: [(c - (days.length - 1) / 2) * W, 1.6, 0], color: 0x9fd0ff, size: 0.8, maxW: W * 0.95 });
      });
      for (let hr = TOP; hr <= 21; hr += 2)
        L.labels.push({ text: String(hr).padStart(2, '0') + ':00', pos: [-(days.length / 2) * W - 1.6, -(hr - TOP) * HOUR, 0], color: 0x6f86a6, size: 0.5 });
      L.order = order;
      L.cam = fitCam(L, act, 0, Math.PI / 2 - 0.1);
    }

    // Orbit: rings around a still centre. Each group is one ring, the most
    // pressing group innermost; heavier items sit nearer the front.
    function orbitLayout(L, act, put) {
      const groups = new Map();
      act.forEach(i => { const k = items[i].cat; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(i); });
      const rank = k => (catInfo(k).rank != null ? catInfo(k).rank : 50);
      const keys = Array.from(groups.keys()).sort((a, b) => rank(a) - rank(b) || groups.get(b).length - groups.get(a).length);
      let r = 5;
      const order = [];
      keys.forEach((k, gi) => {
        const mem = groups.get(k).sort((a, b) => items[b].size - items[a].size || items[a].mtime - items[b].mtime);
        const S0 = 2.1;
        r = Math.max(r, mem.length * S0 * 1.12 / (2 * Math.PI));
        mem.forEach((i, j) => {
          const a = Math.PI / 2 + (j + 0.5) / mem.length * Math.PI * 2;
          put(i, r * Math.cos(a), 0, r * Math.sin(a), IDQ, S0);
          order.push(i);
        });
        const c = catInfo(k);
        // the ring's name sits on its near edge, under the cards
        L.labels.push({ text: c.label + ' · ' + mem.length, pos: [0, -S0 * 0.9, r], color: c.color, size: 0.95 });
        L.rings.push({ z: 0, r, flat: true, color: c.color, y: -S0 * 0.6 });
        r += S0 * 2.4;
      });
      L.labels.push({ text: 'NOW', pos: [0, 0, 0], color: BRAND.cyan, size: 1.2 });
      L.bb = 1; L.order = order;
      L.cam = fitCam(L, act, 0, 1.12);
    }

    // Stack: one fanned pile per group, newest on top and nearest; the
    // piles stand side by side, biggest first.
    function stackLayout(L, act, put) {
      const groups = new Map();
      act.forEach(i => { const k = items[i].cat; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(i); });
      const keys = Array.from(groups.keys()).sort((a, b) => groups.get(b).length - groups.get(a).length);
      const slots = stackSlots(keys.length);
      const order = [];
      keys.forEach((k, gi) => {
        const mem = groups.get(k).sort((a, b) => items[b].mtime - items[a].mtime);
        const sl = slots[gi], cy = Math.cos(sl.yaw), sy = Math.sin(sl.yaw);
        mem.forEach((i, j) => {
          const d = Math.min(j, 24);                         // the pile is shallow after 24
          const jit = (((items[i].mtime * 2654435761) >>> 0) % 1000) / 1000 - 0.5;
          const ox = jit * 0.2, oz = -j * 0.06;              // offsets in the pile's own frame
          tmpQ.setFromEuler(tmpE.set(-0.18, sl.yaw, jit * 0.07, 'YXZ'));
          put(i, sl.x + ox * cy + oz * sy, sl.y - d * 0.34, sl.z - ox * sy + oz * cy, tmpQ, 3.8, 2.4);
          order.push(i);
        });
        const c = catInfo(k);
        let label = c.label || k;
        if (label.length > 26) label = label.slice(0, 25) + '…';
        L.labels.push({ text: label + ' · ' + mem.length, pos: [sl.x, sl.y + 1.9, sl.z], color: c.color || 0x9fd0ff, size: 0.62, maxW: 4.8 });
      });
      L.order = order;
      L.cam = fitCam(L, act, 0, Math.PI / 2 - 0.15);
    }

    function clusterLayout(L, act, put) {
      const key = i => {
        if (groupBy === 'type') return items[i].cat;
        const rel = items[i].rel.slice(currentPath ? currentPath.length + 1 : 0);
        const cut = rel.indexOf('/');
        return cut < 0 ? (items[i].dir ? rel : '(loose files)') : rel.slice(0, cut);
      };
      const groups = new Map();
      act.forEach(i => { const k = key(i); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(i); });
      let keys = Array.from(groups.keys()).sort((a, b) => groups.get(b).length - groups.get(a).length);
      if (keys.length > 40) {
        const extra = keys.slice(39);
        const merged = [].concat.apply([], extra.map(k => groups.get(k)));
        keys = keys.slice(0, 39); groups.set('(everything else)', merged); keys.push('(everything else)');
      }
      const rads = keys.map(k => 1.6 + 0.95 * Math.cbrt(groups.get(k).length) * 1.3);
      // Shelf-pack the balls into rows facing the camera, rows about as wide
      // as the window is wide relative to its height, biggest first.
      const gap = 3.5, area = rads.reduce((s, r) => s + (2 * r + gap) * (2 * r + gap), 0);
      const rowW = Math.max(2 * rads[0] + gap, Math.sqrt(area * Math.max(0.6, camera.aspect)) * 1.05);
      const centres = [];
      let rx = 0, ry = 0, rowH = 0, row = [];
      const flush = () => { const off = rx / 2; row.forEach(c => { c[0] -= off; }); ry -= rowH; rx = 0; rowH = 0; row = []; };
      rads.forEach(r => {
        const d = 2 * r + gap;
        if (rx > 0 && rx + d > rowW) flush();
        const lh = 1.2 + r * 0.12 + Math.max(1.1, r * 0.28);   // label headroom
        const c = [rx + d / 2, ry - lh - d / 2, r];
        centres.push(c); row.push(c);
        rx += d; rowH = Math.max(rowH, d + lh);
      });
      flush();
      const order = [];
      keys.forEach((k, gi) => {
        const cx = centres[gi][0], cy = centres[gi][1], cz = 0;
        const mem = groups.get(k).slice().sort((a, b) => items[b].size - items[a].size);
        mem.forEach((i, j) => {
          const rr = rads[gi] * Math.cbrt((j + 0.5) / mem.length);
          const yy = 1 - 2 * (j + 0.5) / mem.length, th = j * GOLD, sr = Math.sqrt(1 - yy * yy);
          put(i, cx + rr * sr * Math.cos(th), cy + rr * yy, cz + rr * sr * Math.sin(th), IDQ, 1);
          order.push(i);
        });
        const known = groupBy === 'type' && (CATS[k] || EXTRA_CATS[k]);
        const color = known ? catInfo(k).color : 0x9fd0ff;
        let label = known ? catInfo(k).label : k;
        if (label.length > 22) label = label.slice(0, 21) + '…';
        L.labels.push({ text: label + ' · ' + mem.length, pos: [cx, cy + rads[gi] + 1.2 + rads[gi] * 0.12, cz], color, size: Math.max(1.1, rads[gi] * 0.28), maxW: 2 * rads[gi] + gap * 0.8 });
      });
      L.bb = 1; L.order = order;
      L.cam = fitCam(L, act, 0, Math.PI / 2 - 0.12);
    }

    // ── labels / decor ──
    function textSprite(text, color, size) {
      const c = document.createElement('canvas'), fs = 44;
      const x = c.getContext('2d');
      x.font = '700 ' + fs + 'px Orbitron, Inter, sans-serif';
      const w = Math.min(1400, Math.ceil(x.measureText(text).width) + 30);
      c.width = w; c.height = fs + 22;
      x.font = '700 ' + fs + 'px Orbitron, Inter, sans-serif';
      x.textBaseline = 'middle';
      x.shadowColor = 'rgba(0,0,0,0.9)'; x.shadowBlur = 8;
      x.fillStyle = hex(color);
      x.fillText(text, 15, c.height / 2);
      const t = new THREE.CanvasTexture(c);
      t.minFilter = THREE.LinearFilter;
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, opacity: 0, depthWrite: false, fog: false }));
      sp.scale.set(size * c.width / c.height, size, 1);
      return sp;
    }
    function buildDecor(L) {
      const g = new THREE.Group();
      g.userData.mats = [];
      L.labels.forEach(l => {
        const sp = textSprite(l.text, l.color, l.size);
        if (l.maxW && sp.scale.x > l.maxW) sp.scale.multiplyScalar(l.maxW / sp.scale.x);
        sp.position.set(l.pos[0], l.pos[1], l.pos[2]);
        g.add(sp); g.userData.mats.push(sp.material);
      });
      if (L.rings.length) {
        const circle = [];
        for (let k = 0; k <= 64; k++) circle.push(new THREE.Vector3(Math.cos(k / 64 * Math.PI * 2) * 8.4, Math.sin(k / 64 * Math.PI * 2) * 8.4, 0));
        const cg = new THREE.BufferGeometry().setFromPoints(circle);
        L.rings.forEach(r => {
          const m = new THREE.LineBasicMaterial({ color: r.color || 0x3fa9ff, transparent: true, opacity: 0 });
          const loop = new THREE.Line(cg, m);
          if (r.flat) {                    // orbit path: a ring lying in the x-z plane
            loop.rotation.x = Math.PI / 2; loop.scale.setScalar(r.r / 8.4); loop.position.y = r.y || 0;
            g.add(loop); g.userData.mats.push(m);
            return;
          }
          loop.position.z = r.z;
          g.add(loop); g.userData.mats.push(m);
          const sp = textSprite(r.text, 0x8fd3ff, 1.0);
          sp.position.set(0, 9.4, r.z);
          g.add(sp); g.userData.mats.push(sp.material);
        });
      }
      scene.add(g);
      return g;
    }
    function disposeDecor(g) {
      scene.remove(g);
      g.traverse(o => {
        if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); }
      });
    }

    // ── transitions ──
    function applyLayout(instant, keepCam) {
      if (!n) { dirty = true; return; }
      if (reduced) instant = true;
      layout = computeLayout(view);
      tP.set(layout.P); tQ.set(layout.Q); tS.set(layout.S); tBP.set(layout.BP); tBS.set(layout.BS);
      bbTo = layout.bb;
      if (decor) { oldDecor.push(decor); }
      decor = buildDecor(layout);
      if (instant) {
        P.set(tP); Q.set(tQ); S.set(tS); BP.set(tBP); BS.set(tBS); bbCur = bbTo;
        flight = null;
      } else {
        fP.set(P); fQ.set(Q); fS.set(S); fBP.set(BP); fBS.set(BS); bbFrom = bbCur;
        flight = { t0: performance.now(), dur: keepCam ? 700 : 1300 };
      }
      if (!keepCam) setCamGoal(layout.cam, instant);
      dirty = true;
    }
    function setCamGoal(c, instant) {
      goal.t.set(c.t[0], c.t[1], c.t[2]); goal.theta = c.theta; goal.phi = c.phi; goal.r = c.r;
      // unwrap theta so the camera takes the short way round
      while (goal.theta - cam.theta > Math.PI) goal.theta -= 2 * Math.PI;
      while (goal.theta - cam.theta < -Math.PI) goal.theta += 2 * Math.PI;
      if (instant) { cam.t.copy(goal.t); cam.theta = goal.theta; cam.phi = goal.phi; cam.r = goal.r; }
      camMoving = true;
    }
    const ease = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

    function stepFlight(now) {
      if (!flight) return false;
      let done = true;
      const trail = dz > 0 && !reduced && (flight.frame = (flight.frame || 0) + 1) % 2 === 0;
      const tstep = Math.max(1, Math.ceil(n / (dz >= 1 ? 220 : 90)));
      for (let i = 0; i < n; i++) {
        let t = (now - flight.t0 - delay[i] * 1000) / flight.dur;
        if (t < 1) done = false;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        const e = ease(t), i3 = i * 3;
        const dx = tP[i3] - fP[i3], dy = tP[i3 + 1] - fP[i3 + 1], dz = tP[i3 + 2] - fP[i3 + 2];
        const lift = Math.sqrt(dx * dx + dy * dy + dz * dz) * 0.16 * Math.sin(Math.PI * e);
        P[i3] = fP[i3] + dx * e; P[i3 + 1] = fP[i3 + 1] + dy * e + lift; P[i3 + 2] = fP[i3 + 2] + dz * e;
        THREE.Quaternion.slerpFlat(Q, i * 4, fQ, i * 4, tQ, i * 4, e);
        S[i * 2] = fS[i * 2] + (tS[i * 2] - fS[i * 2]) * e; S[i * 2 + 1] = fS[i * 2 + 1] + (tS[i * 2 + 1] - fS[i * 2 + 1]) * e;
        for (let k = 0; k < 3; k++) { BP[i3 + k] = fBP[i3 + k] + (tBP[i3 + k] - fBP[i3 + k]) * e; BS[i3 + k] = fBS[i3 + k] + (tBS[i3 + k] - fBS[i3 + k]) * e; }
        if (trail && i % tstep === 0 && t > 0.05 && t < 0.95 && S[i * 2] > 0.01)
          spawn(P[i3], P[i3 + 1], P[i3 + 2], 0, 0.3, 0, 0.7, i % 3 ? BRAND.cyan : items[i] ? catInfo(items[i].cat).color : BRAND.violet, 0.9 + S[i * 2] * 0.6);
      }
      const gt = Math.min(1, (now - flight.t0) / (flight.dur + 350));
      bbCur = bbFrom + (bbTo - bbFrom) * ease(gt);
      if (done) { flight = null; bbCur = bbTo; }
      return true;
    }

    function writeMatrices() {
      const bb = bbCur, ring = view === 'ring' && layout && layout.ring;
      for (let i = 0; i < n; i++) {
        const i3 = i * 3, i4 = i * 4;
        tmpQ.set(Q[i4], Q[i4 + 1], Q[i4 + 2], Q[i4 + 3]);
        if (bb > 0.001) tmpQ.slerp(camQ, bb);
        let k = i === selIdx ? 1.18 : i === hoverIdx ? 1.1 : 1;
        if (ring && S[i * 2] > 0) {
          // cover-flow: the card facing the camera swells
          const dx = P[i3], dz = P[i3 + 2];
          const da = Math.atan2(dx, dz) - cam.theta, dy = P[i3 + 1] - cam.t.y;
          const facing = Math.pow(Math.max(0, Math.cos(da)), 14) * Math.exp(-dy * dy / 1.2);
          k *= 1 + 0.3 * facing;
        }
        tmpS.set(S[i * 2] * k, S[i * 2 + 1] * k, 1);
        tmpV.set(P[i3], P[i3 + 1], P[i3 + 2]);
        const fx = fxOf.size ? fxOf.get(i) : null;
        if (fx) fxTransform(fx, tmpV, tmpQ, tmpS);
        tmpM.compose(tmpV, tmpQ, tmpS);
        cards.setMatrixAt(i, tmpM);
        tmpS.set(BS[i3], BS[i3 + 1], BS[i3 + 2]);
        if (tmpS.x < 0.001 || tmpS.y < 0.001) tmpS.set(0, 0, 0);
        tmpV.set(BP[i3], BP[i3 + 1], BP[i3 + 2]);
        tmpM.compose(tmpV, IDQ, tmpS);
        boxes.setMatrixAt(i, tmpM);
      }
      cards.instanceMatrix.needsUpdate = true;
      boxes.instanceMatrix.needsUpdate = true;
      // tree edges follow the cards mid-flight
      if (lineMat.opacity > 0.001 && layout && layout.vparent) {
        const arr = lineGeo.attributes.position.array, vp = layout.vparent;
        let w = 0;
        for (let i = 0; i < n; i++) {
          if (!(S[i * 2] > 0.01) || vp[i] === -2) continue;
          const p = vp[i];
          arr[w++] = P[i * 3]; arr[w++] = P[i * 3 + 1] + (items[i].dir ? 0.75 : 0.45); arr[w++] = P[i * 3 + 2];
          if (p >= 0) { arr[w++] = P[p * 3]; arr[w++] = P[p * 3 + 1] - 0.75; arr[w++] = P[p * 3 + 2]; }
          else { arr[w++] = 0; arr[w++] = 1.2; arr[w++] = 0; }
        }
        lineGeo.setDrawRange(0, w / 3);
        lineGeo.attributes.position.needsUpdate = true;
      }
    }

    // ── camera ──
    function stepCamera(dt) {
      if (!camMoving) return false;
      const k = 1 - Math.exp(-dt * 4.2);
      cam.t.lerp(goal.t, k);
      cam.theta += (goal.theta - cam.theta) * k;
      cam.phi += (goal.phi - cam.phi) * k;
      cam.r += (goal.r - cam.r) * k;
      const err = cam.t.distanceTo(goal.t) + Math.abs(goal.theta - cam.theta) * cam.r + Math.abs(goal.phi - cam.phi) * cam.r + Math.abs(goal.r - cam.r);
      if (err < 0.002) camMoving = false;
      return true;
    }
    function placeCamera() {
      const sp = Math.sin(cam.phi);
      camera.position.set(cam.t.x + cam.r * sp * Math.sin(cam.theta), cam.t.y + cam.r * Math.cos(cam.phi), cam.t.z + cam.r * sp * Math.cos(cam.theta));
      camera.lookAt(cam.t);
      // Head-coupled perspective: when Friday is tracking the owner's head,
      // the view shifts with it like a window. Reads FridayTracking when that
      // is loaded, else the face-tracking globals index.html already keeps.
      const hp = headPose();
      if (hp) {
        tmpV.set(1, 0, 0).applyQuaternion(camera.quaternion).multiplyScalar(hp.x * cam.r * 0.07);
        camera.position.add(tmpV);
        tmpV.set(0, 1, 0).applyQuaternion(camera.quaternion).multiplyScalar(hp.y * cam.r * 0.05);
        camera.position.add(tmpV);
        camera.lookAt(cam.t);
      }
      camera.updateMatrixWorld();
      camQ.copy(camera.quaternion);
      sky.position.copy(camera.position);
    }
    function headPose() {
      if (reduced) return null;
      try {
        const T = window.FridayTracking;
        if (T && T.head && T.head.seen) return { x: +T.head.x || 0, y: -(+T.head.y || 0) };
        /* global isHologramMode, isFaceVisible, currFaceX, currFaceY */
        if (typeof isHologramMode !== 'undefined' && isHologramMode && typeof isFaceVisible !== 'undefined' && isFaceVisible
          && typeof currFaceX === 'number') return { x: currFaceX, y: -currFaceY };
      } catch (_) { /* tracking not loaded */ }
      return null;
    }

    // ── picking ──
    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    let pointer = null, hoverDirty = false;
    function pickAt(cx, cy) {
      if (!cards || !n) return -1;
      const r = renderer.domElement.getBoundingClientRect();
      if (cx < r.left || cy < r.top || cx > r.right || cy > r.bottom) return -1;
      ndc.set((cx - r.left) / r.width * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      // In the city a tower (or a folder's plate) stands for its file too.
      const hits = view === 'city' && boxes ? ray.intersectObjects([cards, boxes]) : ray.intersectObject(cards);
      for (const hh of hits) {
        const i = hh.instanceId;
        if (hh.object === cards ? S[i * 2] > 0.01 : BS[i * 3 + 1] > 0.01) return i;
      }
      return -1;
    }
    function setStates() {
      for (let i = 0; i < n; i++) {
        const fx = fxOf.size ? fxOf.get(i) : null;
        aState.setXYZW(i, fx && fx.kind === 'fail' ? 3 : i === selIdx ? 2 : i === hoverIdx ? 1 : 0, markArr[i], heldArr[i], diss[i]);
      }
      aState.needsUpdate = true;
      dirty = true;
    }
    function setHover(i) {
      if (i === hoverIdx) return;
      hoverIdx = i;
      setStates();
      cb.onHover && cb.onHover(i >= 0 ? items[i] : null, pointer);
    }

    // ── thumbnails: slot pool + loader ──
    const loadQueue = [], uploads = [];
    let inflight = 0, lastWant = 0;
    function wantTiles(now) {
      if (!n || now - lastWant < 220) return;
      lastWant = now;
      const cand = [];
      const e = camera.matrixWorldInverse.elements, pe = camera.projectionMatrix.elements;
      for (let i = 0; i < n; i++) {
        const s = S[i * 2];
        if (!(s > 0.01)) continue;
        const x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2];
        const vz = e[2] * x + e[6] * y + e[10] * z + e[14];
        if (vz > -0.2) continue;
        const vx = e[0] * x + e[4] * y + e[8] * z + e[12], vy = e[1] * x + e[5] * y + e[9] * z + e[13];
        const sx = pe[0] * vx / -vz, sy = pe[5] * vy / -vz;
        if (sx < -1.15 || sx > 1.15 || sy < -1.15 || sy > 1.15) continue;
        cand.push([i, s / -vz]);
      }
      cand.sort((a, b) => b[1] - a[1]);
      const keep = Math.min(cand.length, SLOTS - 16);
      wanted.fill(0);
      for (let k = 0; k < keep; k++) {
        const i = cand[k][0];
        wanted[i] = 1;
        pool.touch(i, now);
      }
      loadQueue.length = 0;
      for (let k = 0; k < keep; k++) { const i = cand[k][0]; if (pool.slotOf[i] < 0 && !failed[i]) loadQueue.push(i); }
      pump();
    }
    function pump() {
      const myGen = gen;
      while (inflight < 6 && loadQueue.length) {
        const i = loadQueue.shift();
        if (pool.slotOf[i] >= 0 || !wanted[i]) continue;
        inflight++;
        drawTile(items[i]).then(px => {
          inflight--;
          if (myGen !== gen) return;
          if (!px) { failed[i] = 1; pump(); return; }
          uploads.push([i, px]);
          dirty = true;
          pump();
        }, () => { inflight--; if (myGen === gen) failed[i] = 1; pump(); });
      }
    }
    function flushUploads() {
      let k = 0;
      const touched = new Set();
      const batch = [];
      const tb = performance.now();
      while (uploads.length && k < 16 && performance.now() - tb < 4) {
        const [i, px] = uploads.shift();
        if (!wanted[i] || pool.slotOf[i] >= 0) continue;
        const { slot: s, evicted } = pool.claim(i, wanted, performance.now());
        if (s < 0) continue;
        if (evicted >= 0) { aTile.setXYZ(evicted, -1, 0, 0); }
        batch.push([i, px, s]); touched.add(Math.floor(s / SLOTS_PER));
        k++;
      }
      if (!batch.length) return false;
      // regenerate mipmaps once per atlas, on its last copy in this batch
      const lastFor = new Map();
      batch.forEach((b, j) => lastFor.set(Math.floor(b[2] / SLOTS_PER), j));
      batch.forEach(([i, px, s], j) => {
        const a = Math.floor(s / SLOTS_PER), l = s % SLOTS_PER, ox = (l % PER) * TILE, oy = Math.floor(l / PER) * TILE;
        const tex = ensureAtlas(a);
        tex.generateMipmaps = lastFor.get(a) === j;
        renderer.copyTextureToTexture(new THREE.Vector2(ox, oy), { isDataTexture: true, image: { data: px, width: TILE, height: TILE } }, tex);
        tex.generateMipmaps = true;
        aTile.setXYZ(i, a, ox / ATLAS, oy / ATLAS);
      });
      aTile.needsUpdate = true;
      return true;
    }
    // Tiles are fetched, decoded and drawn in workers (studio_files3d_worker.js)
    // and arrive as raw RGBA pixels, so a thumbnail never costs the 3D view a
    // frame. Without workers the cards keep their plain colour.
    const workers = [], waiting = new Map();
    let jobSeq = 0, rr = 0, recv = 0, werr = '';
    try {
      for (let k = 0; k < 2; k++) {
        const w = new Worker('/static/studio_files3d_worker.js');
        w.onerror = e => { werr = String(e.message || e.type); };
        w.onmessageerror = () => { werr = 'messageerror'; };
        w.onmessage = e => {
          recv++;
          const done = waiting.get(e.data.id);
          if (!done) return;
          waiting.delete(e.data.id);
          done(e.data.px ? new Uint8Array(e.data.px) : null);
        };
        workers.push(w);
      }
    } catch (_) { /* no workers: no thumbnails */ }
    const svgBitmap = it => new Promise(res => {
      const img = new Image();
      img.onload = () => createImageBitmap(img, { resizeWidth: TILE, resizeHeight: Math.max(1, Math.round(TILE * (img.naturalHeight || 1) / (img.naturalWidth || 1))) }).then(res, () => res(null));
      img.onerror = () => res(null);
      img.src = '/api/studio-files/raw?' + qs(currentRoot, it.rel);
    });
    function drawTile(it) {
      if (!workers.length) return Promise.reject(new Error('no workers'));
      const job = {
        name: it.name, ext: it.ext, dir: it.dir, kids: it.kids.length, size: it.size,
        color: hex(it.color != null ? it.color : catInfo(it.cat).color), token: window.__FRIDAY_API_TOKEN || '',
        card: it.card || null, strip: it.strip || null,
        url: it.img || (it.card ? null : SERVER_THUMB.has(it.ext) ? '/api/studio-files/thumb?' + qs(currentRoot, it.rel) + '&s=128&m=' + it.mtime : null)
      };
      const send = bmp => new Promise(res => {
        const id = ++jobSeq;
        job.id = id;
        waiting.set(id, res);
        if (bmp) job.bitmap = bmp;
        workers[rr++ % workers.length].postMessage(job, bmp ? [bmp] : []);
      });
      return it.ext === 'svg' ? svgBitmap(it).then(send) : send(null);
    }

    // ── input ──
    const el = renderer.domElement;
    let drag = null, lastPointerDown = 0;
    el.addEventListener('pointerdown', e => {
      el.setPointerCapture(e.pointerId);
      lastPointerDown = performance.now();
      drag = { x: e.clientX, y: e.clientY, moved: false, pan: e.button === 2 || e.button === 1 || e.shiftKey, item: -1, itemOn: false };
      // a panel that can act on cards lets them be picked up: a left drag
      // that starts on a card carries the card instead of orbiting
      if (cb.onItemDrag && e.button === 0 && !e.shiftKey && !(e.ctrlKey || e.metaKey)) drag.item = pickAt(e.clientX, e.clientY);
      el.style.cursor = 'grabbing';
      cb.onInteract && cb.onInteract();
    });
    el.addEventListener('pointermove', e => {
      pointer = { x: e.clientX, y: e.clientY };
      hoverDirty = true;
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      drag.moved = true; drag.x = e.clientX; drag.y = e.clientY;
      if (drag.item >= 0) {
        cb.onItemDrag(drag.itemOn ? 'move' : 'start', drag.item, e.clientX, e.clientY);
        drag.itemOn = true; el.style.cursor = 'grabbing';
        return;
      }
      if (drag.pan) {
        const s = cam.r * 0.0016;
        tmpV.set(-dx * s, dy * s, 0).applyQuaternion(camera.quaternion);
        goal.t.add(tmpV); cam.t.add(tmpV);
      } else {
        goal.theta -= dx * 0.005; cam.theta -= dx * 0.005;
        goal.phi = Math.max(0.08, Math.min(Math.PI - 0.08, goal.phi - dy * 0.004)); cam.phi = goal.phi;
      }
      camMoving = true; dirty = true;
    });
    el.addEventListener('pointerup', e => {
      el.style.cursor = 'grab';
      const wasDrag = drag && drag.moved, carried = drag && drag.itemOn ? drag.item : -1;
      drag = null;
      if (carried >= 0) { cb.onItemDrag('end', carried, e.clientX, e.clientY); return; }
      if (wasDrag) return;
      const i = pickAt(e.clientX, e.clientY);
      cb.onPick && cb.onPick(i >= 0 ? i : -1, false, { add: e.ctrlKey || e.metaKey, range: e.shiftKey });
    });
    el.addEventListener('pointercancel', () => {
      const carried = drag && drag.itemOn ? drag.item : -1;
      drag = null; el.style.cursor = 'grab';
      if (carried >= 0) cb.onItemDrag('cancel', carried, 0, 0);
    });
    el.addEventListener('pointerleave', () => { pointer = null; if (!handPos()) setHover(-1); });
    el.addEventListener('dblclick', e => {
      const i = pickAt(e.clientX, e.clientY);
      if (i >= 0) cb.onPick && cb.onPick(i, true);
    });
    el.addEventListener('contextmenu', e => e.preventDefault());
    // The hand-tracking cursor "clicks" with element.click(): no pointer
    // events and no coordinates. Read its position from the cursor element.
    el.addEventListener('click', e => {
      if (performance.now() - lastPointerDown < 800) return;
      const hp = handPos();
      if (!hp) return;
      const i = pickAt(hp.x, hp.y);
      cb.onPick && cb.onPick(i, i >= 0 && i === selIdx);
    });
    el.addEventListener('wheel', e => {
      e.preventDefault();
      cb.onInteract && cb.onInteract();
      if (view === 'ring' && !e.ctrlKey && layout && layout.ring) {
        cb.onStep && cb.onStep(e.deltaY > 0 ? 1 : -1);
        return;
      }
      if (view === 'time' && !e.ctrlKey && layout && layout.time) {
        goal.t.z = Math.max(layout.time.minZ, Math.min(4, goal.t.z - e.deltaY * 0.03));
      } else {
        goal.r = Math.max(2.5, Math.min(1500, goal.r * (1 + e.deltaY * 0.0011)));
      }
      camMoving = true;
    }, { passive: false });
    function handPos() {
      const hc = document.getElementById('hand-cursor');
      if (!hc || !hc.classList.contains('active') || hc.classList.contains('hidden')) return null;
      const x = parseFloat(hc.style.left), y = parseFloat(hc.style.top);
      return isFinite(x) && isFinite(y) ? { x, y } : null;
    }

    // ── file-action animations ──
    // Each runs <= 600 ms, is finished at once by skipFx() (any click or key),
    // and collapses to its end state under reduced motion. The panel calls
    // them only after the server has reported what actually happened.
    const fxOf = new Map();
    const easeOut = t => 1 - Math.pow(1 - t, 3);
    const fxSprite = (map, color, pos, scale) => {
      const sp = new THREE.Sprite(additive(map, color));
      sp.position.copy(pos); sp.scale.setScalar(scale);
      scene.add(sp); fxSprites.push(sp);
      return sp;
    };
    const dropSprite = sp => {
      if (!sp) return;
      scene.remove(sp); sp.material.dispose();
      const k = fxSprites.indexOf(sp); if (k >= 0) fxSprites.splice(k, 1);
    };
    function destPoint(f) {
      const d = f.opts.dest;
      if (d >= 0 && d < n && S[d * 2] > 0.01) return new THREE.Vector3(P[d * 3], P[d * 3 + 1], P[d * 3 + 2]);
      // destination not on screen: up and away, out of the scene
      return new THREE.Vector3(P[f.i * 3], P[f.i * 3 + 1], P[f.i * 3 + 2])
        .add(new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion).multiplyScalar(cam.r * 0.6))
        .add(new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion).multiplyScalar(cam.r * 0.4));
    }
    function playFx(kind, i, opts) {
      return new Promise(resolve => {
        if (!(i >= 0 && i < n)) { resolve(false); return; }
        const prev = fxOf.get(i);
        if (prev) finishFx(prev);
        const slow = +window.__files3dFxScale || 1;   // tests slow animations down to photograph them
        const f = { kind, i, t0: performance.now(), dur: reduced ? 0 : (FX_MS[kind] || 500) * slow, opts: opts || {}, resolve, k: 0 };
        const s0 = Math.max(0.5, S[i * 2]);
        f.base = new THREE.Vector3(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]);
        f.size = s0;
        if (!reduced && dz > 0) {
          if (kind === 'delete') {
            f.vortex = f.base.clone().add(new THREE.Vector3(0, -1, 0).applyQuaternion(camera.quaternion).multiplyScalar(s0 * 1.1));
            f.spr = fxSprite(TX.spiral_haze, BRAND.magenta, f.vortex, s0 * 0.4);
            f.spr2 = fxSprite(glowTex, BRAND.violet, f.vortex, s0 * 0.6);
            // dust spirals inward to the vortex
            for (let j = 0; j < 72; j++) {
              const a = j / 72 * Math.PI * 4, r = s0 * (1.0 + (j / 72) * 1.4);
              const off = new THREE.Vector3(Math.cos(a) * r, Math.sin(a) * r, 0).applyQuaternion(camera.quaternion);
              const life = (0.3 + (j / 72) * 0.3) * slow;
              spawn(f.vortex.x + off.x, f.vortex.y + off.y, f.vortex.z + off.z, -off.x / life, -off.y / life, -off.z / life, life, j % 3 ? BRAND.magenta : BRAND.violet, 1.1);
            }
          } else if (kind === 'share') {
            f.spr = fxSprite(TX.shockwave, BRAND.cyan, f.base, s0);
            for (let j = 0; j < 40; j++) {
              const a = j / 40 * Math.PI * 2, v = new THREE.Vector3(Math.cos(a), Math.sin(a), 0).applyQuaternion(camera.quaternion).multiplyScalar(s0 * 5 / slow);
              spawn(f.base.x, f.base.y, f.base.z, v.x, v.y, v.z, 0.5 * slow, j % 2 ? BRAND.cyan : BRAND.violet, 0.8);
            }
          } else if (kind === 'copy') {
            f.ghost = makeGhost(i);
          }
        } else if (kind === 'copy') {
          f.ghost = null;
        }
        if (dz > 0 && !reduced && (kind === 'move' || kind === 'copy')) f.dest = destPoint(f);
        fxOf.set(i, f);
        setStates();
        dirty = true;
        if (!f.dur) finishFx(f);
      });
    }
    function makeGhost(i) {
      const g = planeGeo.clone();
      const at = new THREE.InstancedBufferAttribute(new Float32Array([aTile.getX(i), aTile.getY(i), aTile.getZ(i)]), 3);
      const ac = new THREE.InstancedBufferAttribute(new Float32Array([aColor.getX(i), aColor.getY(i), aColor.getZ(i)]), 3);
      const as = new THREE.InstancedBufferAttribute(new Float32Array([1, 0, 0, 0.15]), 4);
      g.setAttribute('aTile', at); g.setAttribute('aColor', ac); g.setAttribute('aState', as);
      const m = new THREE.InstancedMesh(g, cardMat, 1);
      m.frustumCulled = false;
      scene.add(m);
      return m;
    }
    function stepFx(now) {
      if (!fxOf.size) return false;
      for (const f of Array.from(fxOf.values())) {
        f.k = f.dur ? Math.min(1, (now - f.t0) / f.dur) : 1;
        const k = f.k, i = f.i;
        if (f.kind === 'delete') {
          diss[i] = Math.max(0, (k - 0.15) / 0.85);
          aState.array[i * 4 + 3] = diss[i]; aState.needsUpdate = true;
          if (f.spr) { f.spr.material.opacity = Math.min(1, Math.sin(Math.PI * k) * 1.6) * dz; f.spr.material.rotation = -k * 9; f.spr.scale.setScalar(f.size * (0.6 + 2.2 * easeOut(k))); }
          if (f.spr2) { f.spr2.material.opacity = Math.sin(Math.PI * k) * 0.7 * dz; f.spr2.scale.setScalar(f.size * (1.2 + 1.6 * Math.sin(Math.PI * k))); }
          // the burning edge throws sparks down into the vortex
          if ((f.tick = (f.tick || 0) + 1) % 2 === 0 && k > 0.15 && k < 0.9) {
            tmpV.set(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]);
            const life = 0.3 * (+window.__files3dFxScale || 1);
            spawn(tmpV.x + (Math.random() - 0.5) * f.size, tmpV.y + (Math.random() - 0.5) * f.size, tmpV.z,
              (f.vortex.x - tmpV.x) / life, (f.vortex.y - tmpV.y) / life, (f.vortex.z - tmpV.z) / life, life, Math.random() < 0.5 ? BRAND.cyan : BRAND.magenta, 0.9);
          }
        } else if (f.kind === 'share' && f.spr) {
          f.spr.material.opacity = (1 - k) * 0.9 * dz; f.spr.scale.setScalar(f.size * (1 + 5 * easeOut(k)));
        } else if (f.kind === 'rename' && !f.midDone && k >= 0.25) {
          f.midDone = true;
          if (f.opts.onMid) f.opts.onMid();
        } else if (f.kind === 'copy' && f.ghost) {
          const e = ease(k), from = f.base, to = f.dest || f.base;
          const peel = Math.min(1, k / 0.25);
          tmpV.copy(from).lerp(to, Math.max(0, (e - 0.1) / 0.9));
          const lift = from.distanceTo(to) * 0.3 * Math.sin(Math.PI * e);
          tmpV.y += lift;
          tmpV.add(new THREE.Vector3(0, 0, 1).applyQuaternion(camera.quaternion).multiplyScalar(f.size * 0.4 * peel));
          tmpS.set(f.size * (1.05 - 0.6 * e), f.size * (1.05 - 0.6 * e), 1);
          tmpM.compose(tmpV, camQ, tmpS);
          f.ghost.setMatrixAt(0, tmpM); f.ghost.instanceMatrix.needsUpdate = true;
          const ga = f.ghost.geometry.attributes.aState; ga.array[3] = 0.15 + 0.75 * Math.max(0, (k - 0.7) / 0.3); ga.needsUpdate = true;
          if ((f.tick = (f.tick || 0) + 1) % 2 === 0) spawn(tmpV.x, tmpV.y, tmpV.z, 0, 0, 0, 0.35, BRAND.cyan, 0.7);
        } else if (f.kind === 'move' && f.dest && (f.tick = (f.tick || 0) + 1) % 2 === 0) {
          spawn(P[i * 3], P[i * 3 + 1], P[i * 3 + 2], 0, 0, 0, 0.35, BRAND.cyan, 0.7);
        }
        if (k >= 1) finishFx(f);
      }
      return true;
    }
    // Where an animating card is drawn this frame (writeMatrices).
    const fxAxis = new THREE.Vector3(), fxQ = new THREE.Quaternion();
    function fxTransform(f, pos, quat, scl) {
      const k = f.k, e = ease(k);
      if (f.kind === 'delete') {
        const lift = Math.min(1, k / 0.2), e2 = easeOut(Math.max(0, (k - 0.15) / 0.85));
        pos.y += f.size * 0.12 * lift * (1 - e2);
        if (f.vortex) pos.lerp(f.vortex, e2);
        fxAxis.set(0, 0, 1); fxQ.setFromAxisAngle(fxAxis, e2 * Math.PI * 2.5); quat.multiply(fxQ);
        scl.multiplyScalar(1 - 0.85 * e2);
      } else if (f.kind === 'fail') {
        tmpS2.set(1, 0, 0).applyQuaternion(camQ).multiplyScalar(Math.sin(k * Math.PI * 7) * (1 - k) * f.size * 0.18);
        pos.add(tmpS2);
      } else if (f.kind === 'move') {
        const to = f.dest || pos;
        const d = f.base.distanceTo(to);
        pos.lerp(to, e); pos.y += d * 0.35 * Math.sin(Math.PI * e);
        scl.multiplyScalar(1 - 0.75 * e);
      } else if (f.kind === 'share') {
        tmpS2.copy(camera.position).sub(pos).normalize().multiplyScalar(Math.sin(Math.PI * k) * f.size * 1.2);
        pos.add(tmpS2); scl.multiplyScalar(1 + 0.22 * Math.sin(Math.PI * k));
      } else if (f.kind === 'rename') {
        fxAxis.set(0, 1, 0); fxQ.setFromAxisAngle(fxAxis, e * Math.PI * 2); quat.multiply(fxQ);
      } else if (f.kind === 'open') {
        const out = Math.sin(Math.PI * k);
        tmpS2.copy(camera.position).sub(pos).multiplyScalar(0.55 * out);
        pos.add(tmpS2); scl.multiplyScalar(1 + 0.6 * out);
      }
    }
    const tmpS2 = new THREE.Vector3();
    function finishFx(f) {
      if (!fxOf.has(f.i) || fxOf.get(f.i) !== f) return;
      fxOf.delete(f.i);
      const i = f.i;
      if (f.kind === 'rename' && !f.midDone && f.opts.onMid) { f.midDone = true; f.opts.onMid(); }
      if (f.kind === 'delete' || f.kind === 'move') {
        // gone from this folder: stays invisible until the folder is re-read
        S[i * 2] = S[i * 2 + 1] = tS[i * 2] = tS[i * 2 + 1] = 0; diss[i] = 1;
        BS[i * 3 + 1] = tBS[i * 3 + 1] = 0;
      }
      dropSprite(f.spr); dropSprite(f.spr2);
      if (f.ghost) { scene.remove(f.ghost); f.ghost.geometry.dispose(); }
      setStates();
      dirty = true;
      f.resolve(true);
    }
    function skipFx() { Array.from(fxOf.values()).forEach(finishFx); }

    // Dazzle level and reduced motion -> every layer's strength.
    function applyDazzle() {
      const u = cardMat.uniforms;
      u.uDazzle.value = dz;
      u.uReduced.value = reduced ? 1 : 0;
      dustGeo.setDrawRange(0, dz >= 1 ? DUST : dz > 0 ? 380 : 0);
      if (dz <= 0 || reduced) { materialize = null; diss.fill(0); if (aState) { for (let i = 0; i < n; i++) aState.array[i * 4 + 3] = 0; aState.needsUpdate = true; } }
      dirty = true;
    }

    // ── resize / visibility ──
    let W = 1, H = 1;
    const resize = () => {
      W = Math.max(1, mount.clientWidth); H = Math.max(1, mount.clientHeight);
      renderer.setSize(W, H, false);
      camera.aspect = W / H; camera.updateProjectionMatrix();
      dirty = true;
    };
    const ro = new ResizeObserver(resize);
    ro.observe(mount);
    resize();
    const io = new IntersectionObserver(es => { visible = es.some(x => x.isIntersecting); if (visible) dirty = true; });
    io.observe(mount);
    // While this view is big on screen, hold the holographic backdrop's
    // drawing (index.html animate()) so the two scenes do not split the GPU.
    let holding = false;
    const hold = on => {
      if (on === holding) return;
      holding = on;
      window.__fridayBackdropHold = Math.max(0, (window.__fridayBackdropHold || 0) + (on ? 1 : -1));
    };
    const wantHold = () => visible && !document.hidden && !disposed && W * H > 0.25 * window.innerWidth * window.innerHeight;

    // ── loop ──
    let last = performance.now(), raf = 0, bench = null;
    const frameTimes = [];
    const prof = { mat: 0, render: 0, pick: 0, tiles: 0, frames: 0, t0: performance.now(), last: null };
    const tick = () => performance.now();
    // GPU time per frame, where the browser exposes timer queries.
    const gl = renderer.getContext();
    const tq = gl.getExtension && gl.getExtension('EXT_disjoint_timer_query_webgl2');
    const gpuQ = [];
    let gpuMs = 0, gpuN = 0;
    const pollGpu = () => {
      while (gpuQ.length && gl.getQueryParameter(gpuQ[0], gl.QUERY_RESULT_AVAILABLE)) {
        const q = gpuQ.shift();
        if (!gl.getParameter(tq.GPU_DISJOINT_EXT)) { gpuMs += gl.getQueryParameter(q, gl.QUERY_RESULT) / 1e6; gpuN++; }
        gl.deleteQuery(q);
      }
    };
    let lastHandHover = 0, lastMoveHover = 0;
    const flags = { tick: 0 };
    const heldAny = () => { for (let i = 0; i < n; i++) if (heldArr[i]) return true; return false; };
    function frame(now) {
      raf = requestAnimationFrame(frame);
      hold(wantHold());
      if (disposed || document.hidden || !visible) { last = now; return; }
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (bench) {
        goal.theta += dt * 0.6; camMoving = true;
        if (now - bench.t0 > bench.ms) { const b = bench; bench = null; b.resolve(summary()); }
      }
      let moved = stepFlight(now);
      moved = stepCamera(dt) || moved;
      moved = stepFx(now) || moved;
      moved = stepMaterialize(now) || moved;
      const tsec = clock();
      cardMat.uniforms.uTime.value = tsec; dustMat.uniforms.uTime.value = tsec; partMat.uniforms.uTime.value = tsec; floorMat.uniforms.uTime.value = tsec;
      flushParticles();
      // atmosphere follows the dazzle level; the floor waits for flights to land
      const ease1 = Math.min(1, dt * 4);
      const skyTo = dz > 0 ? 0.55 + 0.45 * dz : 0;
      if (Math.abs(sky.material.opacity - skyTo) > 1e-3) { sky.material.opacity += (skyTo - sky.material.opacity) * ease1; moved = true; }
      sky.visible = sky.material.opacity > 0.01;
      const fl = layout && layout.floor;
      const floorTo = fl && dz > 0 && !flight ? 0.9 * Math.min(1, dz * 1.4) : 0;
      if (fl) { floor.position.set(fl.cx, fl.y, fl.cz); floor.scale.set(fl.span * 2.2, fl.span * 2.2, 1); floorMat.uniforms.uCenter.value.set(fl.cx, fl.y, fl.cz); floorMat.uniforms.uSpan.value = fl.span * 1.1; floorMat.uniforms.uCell.value = fl.cell; }
      if (Math.abs(floorMat.uniforms.uOpacity.value - floorTo) > 1e-3) { floorMat.uniforms.uOpacity.value += (floorTo - floorMat.uniforms.uOpacity.value) * ease1; moved = true; }
      floor.visible = floorMat.uniforms.uOpacity.value > 0.01;
      const reflTo = fl && fl.mirror && dz >= 1 && !flight ? 1 : 0;
      const ur = cardMat.uniforms.uReflect;
      if (Math.abs(ur.value - reflTo) > 1e-3) { ur.value += (reflTo - ur.value) * ease1; moved = true; }
      if (refl) {
        refl.visible = ur.value > 0.01 && !!fl;
        if (refl.visible) {
          cardMat.uniforms.uFloorY.value = fl.y;
          refl.matrix.makeScale(1, -1, 1).premultiply(tmpM.makeTranslation(0, 2 * fl.y, 0));
          refl.matrixWorld.copy(refl.matrix);
        }
      }
      dustMat.uniforms.uOpacity.value = dz > 0 ? 0.5 * dz : 0;
      dust.visible = dz > 0;
      dustMat.uniforms.uCenter.value.copy(cam.t);
      dustMat.uniforms.uRegion.value = Math.max(30, cam.r * 1.1);
      // Friday's live mood colour tints the dust and the floor's far edge
      /* global moodLerpValues */
      if (typeof moodLerpValues !== 'undefined' && moodLerpValues && moodLerpValues.accentColor && moodLerpValues.accentColor.isColor) {
        dustMat.uniforms.uColB.value.copy(moodLerpValues.accentColor).lerp(pc.set(BRAND.violet), 0.5);
        floorMat.uniforms.uColB.value.copy(dustMat.uniforms.uColB.value);
      }
      // the selected file glows softly from behind
      if (selIdx >= 0 && selIdx < n && dz > 0 && S[selIdx * 2] > 0.01 && !fxOf.has(selIdx)) {
        selGlow.material.color.setHex(heldArr[selIdx] ? BRAND.amber : BRAND.cyan);
        tmpV.set(P[selIdx * 3], P[selIdx * 3 + 1], P[selIdx * 3 + 2]);
        tmpS2.copy(tmpV).sub(camera.position).normalize().multiplyScalar(0.06 * S[selIdx * 2]);
        selGlow.position.copy(tmpV).add(tmpS2);
        selGlow.scale.setScalar(S[selIdx * 2] * (3.8 + (reduced ? 0 : 0.2 * Math.sin(tsec * 2))));
        selGlow.material.opacity = 0.65 * dz;
        selGlow.visible = true;
      } else selGlow.visible = false;
      const hpNow = headPose();
      // ambient life (dust, shimmer, held pulse) keeps a gentle 30 fps when nothing else moves
      const ambient = !reduced && (dz > 0 || heldAny()) && (flags.tick = (flags.tick + 1) % 2) === 0;
      if (hpNow) moved = true;
      // fade decor: current in (after the flight lands), old out
      const fadeIn = flight ? 0 : 1;
      if (decor) decor.userData.mats.forEach(m => { const o = m.opacity + (fadeIn - m.opacity) * Math.min(1, dt * 5); if (Math.abs(o - m.opacity) > 1e-3) { m.opacity = o; moved = true; } });
      for (let k = oldDecor.length - 1; k >= 0; k--) {
        const g = oldDecor[k];
        let alive = false;
        g.userData.mats.forEach(m => { m.opacity = Math.max(0, m.opacity - dt * 4); if (m.opacity > 0) alive = true; });
        moved = true;
        if (!alive) { disposeDecor(g); oldDecor.splice(k, 1); }
      }
      const lt = layout && layout.lines && !flight ? 0.5 : layout && layout.lines ? 0.25 : 0;
      if (Math.abs(lineMat.opacity - lt) > 1e-3) { lineMat.opacity += (lt - lineMat.opacity) * Math.min(1, dt * 4); moved = true; }
      lines && (lines.visible = lineMat.opacity > 0.01);
      placeCamera();
      // hover: mouse, or the hand cursor when hand tracking is on
      const hp = handPos();
      if (hp && now - lastHandHover > 60) { lastHandHover = now; pointer = hp; hoverDirty = true; }
      // the scene moving under a still pointer changes what it is over
      if ((camMoving || flight) && pointer && now - lastMoveHover > 120) { lastMoveHover = now; hoverDirty = true; }
      let t1 = tick();
      if (hoverDirty && pointer && !drag) {
        hoverDirty = false;
        setHover(pickAt(pointer.x, pointer.y));
      }
      let t2 = tick(); prof.pick += t2 - t1;
      if (tq) pollGpu();
      wantTiles(now);
      const up = flushUploads();
      t1 = tick(); prof.tiles += t1 - t2;
      if (moved || dirty || up || ambient || (view === 'ring' && camMoving)) {
        if (n) writeMatrices();
        t2 = tick(); prof.mat += t2 - t1;
        let q = null;
        if (tq && gpuQ.length < 8) { q = gl.createQuery(); gl.beginQuery(tq.TIME_ELAPSED_EXT, q); }
        renderer.render(scene, camera);
        if (q) { gl.endQuery(tq.TIME_ELAPSED_EXT); gpuQ.push(q); }
        prof.render += tick() - t2; prof.frames++;
        dirty = false;
        frameTimes.push(now);
        while (frameTimes.length && now - frameTimes[0] > 1000) frameTimes.shift();
      }
    }
    raf = requestAnimationFrame(frame);

    function summary() {
      let used = 0;
      if (pool) used = pool.used();
      const info = renderer.info.render;
      const pt = performance.now() - prof.t0, pf = Math.max(1, prof.frames);
      if (pt > 900) {
        prof.last = { msPerFrame: { matrices: +(prof.mat / pf).toFixed(2), render: +(prof.render / pf).toFixed(2), pick: +(prof.pick / pf).toFixed(2), tiles: +(prof.tiles / pf).toFixed(2), gpu: gpuN ? +(gpuMs / gpuN).toFixed(2) : null } };
        gpuMs = 0; gpuN = 0;
        prof.mat = prof.render = prof.pick = prof.tiles = 0; prof.frames = 0; prof.t0 = performance.now();
      }
      let nf = 0;
      if (failed) for (let i = 0; i < n; i++) nf += failed[i];
      return { fps: frameTimes.length, prof: prof.last, loader: { workers: workers.length, sent: jobSeq, recv, werr, waiting: waiting.size, queued: loadQueue.length, uploads: uploads.length, inflight, failed: nf }, items: n, visible: layout ? layout.order.length : 0, tiles: used, slots: SLOTS, calls: info.calls, triangles: info.triangles, view };
    }

    // ── public ──
    let currentRoot = '', currentPath = '', currentBase = '';
    return {
      setData(list, root, path) {
        const again = root === currentRoot && (path || '') === currentPath;
        currentRoot = root; currentPath = path || ''; currentBase = (path || '').split('/').pop() || root;
        setData(list, !again);
      },
      setView(v) { if (v === view) return; view = v; skipFx(); applyLayout(false); },
      setDazzle(level) { dz = DAZZLE[level] != null ? DAZZLE[level] : DAZZLE.full; applyDazzle(); },
      setHeld(i, on) { if (aState && i >= 0 && i < n) { heldArr[i] = on ? 1 : 0; setStates(); } },
      // several cards selected at once (ctrl/shift click); indices into the data
      setMarked(list) { if (!aState) return; markArr.fill(0); (list || []).forEach(i => { if (i >= 0 && i < n) markArr[i] = 1; }); setStates(); },
      fx: playFx,
      skipFx,
      isAnimating: () => fxOf.size > 0,
      retile(i, rel, name) {
        if (!(i >= 0 && i < n)) return;
        items[i].rel = rel; items[i].name = name;
        if (pool && pool.release(i) >= 0) { aTile.setXYZ(i, -1, 0, 0); aTile.needsUpdate = true; }
        failed[i] = 0; lastWant = 0; dirty = true;
      },
      getView: () => view,
      setGroupBy(g) { groupBy = g; if (view === 'cluster') applyLayout(false); },
      setFilter(mask) { visMask = mask; applyLayout(false); },
      select(i, fly) {
        selIdx = i;
        setStates();
        if (i < 0 || !fly || !layout) return;
        if (view === 'ring' && layout.ring) setCamGoal(ringCam(layout, layout.ring.ord.indexOf(i)));
        else if (view === 'time') setCamGoal(timeCam(layout, P[i * 3 + 2] + 0));
        else {
          goal.t.set(tP[i * 3], tP[i * 3 + 1], tP[i * 3 + 2]);
          goal.r = Math.min(cam.r, view === 'city' ? 14 : 9);
          camMoving = true;
        }
      },
      step(i, d) {
        // next / previous in the arrangement's own order
        if (!layout) return -1;
        const o = layout.order;
        if (!o.length) return -1;
        const k = o.indexOf(i);
        return o[Math.max(0, Math.min(o.length - 1, (k < 0 ? 0 : k + d)))];
      },
      nav(i, dir) {
        // Spatial: the nearest card on screen in the pressed direction.
        if (!layout || !layout.order.length) return -1;
        if (i < 0) return layout.order[0];
        const pr = j => { tmpV.set(tP[j * 3], tP[j * 3 + 1], tP[j * 3 + 2]).project(camera); return [tmpV.x * W / 2, tmpV.y * H / 2, tmpV.z]; };
        const a = pr(i), dv = { left: [-1, 0], right: [1, 0], up: [0, 1], down: [0, -1] }[dir];
        let best = -1, bs = Infinity;
        for (const j of layout.order) {
          if (j === i) continue;
          const b = pr(j);
          if (b[2] > 1) continue;
          const dx = b[0] - a[0], dy = b[1] - a[1], dist = Math.hypot(dx, dy);
          if (dist < 1) continue;
          const dot = (dx * dv[0] + dy * dv[1]) / dist;
          if (dot < 0.5) continue;
          const sc = dist * (2.2 - dot * 1.2);
          if (sc < bs) { bs = sc; best = j; }
        }
        return best >= 0 ? best : i;
      },
      resetCamera() { if (layout) setCamGoal(layout.cam); },
      pickAt: (x, y) => pickAt(x, y),   // client coordinates -> card index or -1 (tests)
      screenPos(i) {
        if (i < 0) return null;
        tmpV.set(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]).project(camera);
        return { x: (tmpV.x + 1) / 2 * W, y: (1 - tmpV.y) / 2 * H, behind: tmpV.z > 1 };
      },
      stats: summary,
      camInfo: () => ({ pos: camera.position.toArray().map(v => +v.toFixed(2)), target: cam.t.toArray().map(v => +v.toFixed(2)), goal: goal.t.toArray().map(v => +v.toFixed(2)), theta: +cam.theta.toFixed(3), phi: +cam.phi.toFixed(3), r: +cam.r.toFixed(2), goalR: +goal.r.toFixed(2) }),
      bench(ms) { return new Promise(resolve => { bench = { t0: performance.now(), ms: ms || 4000, resolve }; }); },
      dispose() {
        disposed = true;
        hold(false);
        cancelAnimationFrame(raf);
        ro.disconnect(); io.disconnect();
        freeMeshes();
        if (decor) disposeDecor(decor);
        oldDecor.forEach(disposeDecor);
        real.forEach(t => t && t.dispose());
        cardMat.dispose(); reflMat.dispose(); boxMat.dispose(); lineMat.dispose(); boxGeo.dispose(); planeGeo.dispose();
        [dustMat, partMat, floorMat, sky.material, selGlow.material].forEach(m => m.dispose());
        [dustGeo, pGeo, sky.geometry, floor.geometry].forEach(g => g.dispose());
        fxSprites.slice().forEach(dropSprite);
        Object.values(TX).forEach(t => t.dispose()); glowTex.dispose();
        workers.forEach(w => w.terminate());
        renderer.dispose();
        if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
    };
  }

  // ── React panel ─────────────────────────────────────────────────────────
  const VIEWS = [
    { id: 'wall', label: 'Wall', ico: '▦', tip: 'A curved wall of thumbnails, folder by folder' },
    { id: 'ring', label: 'Carousel', ico: '◎', tip: 'Cover-flow ring; scroll to step through' },
    { id: 'tree', label: 'Tree', ico: '⋔', tip: 'Folders as a 3D cone tree' },
    { id: 'city', label: 'City', ico: '▥', tip: 'Folders are districts, files are towers sized by file size' },
    { id: 'time', label: 'Timeline', ico: '⌛', tip: 'A tunnel through time, newest first; scroll to travel' },
    { id: 'cluster', label: 'Clusters', ico: '⁂', tip: 'Grouped by file type or by top folder' }
  ];
  const BTN = { fontSize: 11, padding: '5px 9px', minHeight: 30 };
  // Last folder and view, per browser. Storage can be missing or throw.
  const remember = (k, v) => { try { localStorage.setItem('friday_files3d_' + k, v); } catch (_) {} };
  const recall = k => { try { return localStorage.getItem('friday_files3d_' + k) || ''; } catch (_) { return ''; } };
  const PANEL_BG = 'rgba(6,10,18,0.9)';

  // props.root / props.path / props.view open a given folder and view
  // (the Code workspace opens Projects as a City); otherwise the last used.
  function Files3DPanel(props) {
    props = props || {};
    const mountRef = useRef(null), engRef = useRef(null), boxRef = useRef(null), searchRef = useRef(null);
    const [roots, setRoots] = useState([]);
    const [root, setRoot] = useState('');
    const [path, setPath] = useState('');
    const [items, setItems] = useState([]);
    const [scanInfo, setScanInfo] = useState(null);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState('');
    const [view, setView] = useState(() => props.view || (VIEWS.some(v => v.id === recall('view')) ? recall('view') : 'wall'));
    const [query, setQuery] = useState('');
    const [cats, setCats] = useState({});
    const [groupBy, setGroupBy] = useState('type');
    const [sel, setSel] = useState(-1);
    const [hover, setHover] = useState(null);
    const [preview, setPreview] = useState(null);
    const [toast, setToast] = useState(null);
    const [stats, setStats] = useState(null);
    const [noGL, setNoGL] = useState(false);
    const [stageH, setStageH] = useState(0);
    const [full, setFull] = useState(false);
    const [dazzle, setDazzle] = useState('full');
    const stageRef = useRef(null);
    const itemsRef = useRef([]); itemsRef.current = items;
    const selRef = useRef(-1); selRef.current = sel;

    // engine lifecycle
    useEffect(() => {
      if (!mountRef.current || typeof THREE === 'undefined') { setNoGL(true); return; }
      let eng;
      try {
        eng = createEngine(mountRef.current, {
          onHover: (it, p) => setHover(it && p ? { it, x: p.x, y: p.y } : null),
          onPick: (i, activate) => pick(i, activate),
          onStep: d => { const e = engRef.current; const j = e.step(selRef.current, d); if (j >= 0) choose(j, true); },
          onInteract: () => { if (boxRef.current && document.activeElement !== searchRef.current) boxRef.current.focus({ preventScroll: true }); }
        });
      } catch (e) { setNoGL(true); return; }
      engRef.current = eng;
      window.__files3d = eng;
      window.__files3dInternalsItems = () => itemsRef.current;   // read-only, for tests
      const iv = setInterval(() => setStats(eng.stats()), 1000);
      return () => { clearInterval(iv); eng.dispose(); engRef.current = null; if (window.__files3d === eng) window.__files3d = null; };
    }, []);

    // The stage fills the rest of its Studio window (windows are user-sized),
    // or the whole screen in full-screen mode.
    useEffect(() => {
      const box = boxRef.current, body = box && box.closest('.fwin-body');
      if (!body) return;
      const fit = () => {
        const st = stageRef.current;
        if (!st) return;
        const top = st.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
        setStageH(Math.max(340, body.clientHeight - top - 10));
      };
      const ro = new ResizeObserver(fit);
      ro.observe(body);
      fit();
      return () => ro.disconnect();
    }, []);
    useEffect(() => {
      const on = () => setFull(document.fullscreenElement === boxRef.current);
      document.addEventListener('fullscreenchange', on);
      return () => document.removeEventListener('fullscreenchange', on);
    }, []);
    const toggleFull = () => {
      const b = boxRef.current;
      if (!b) return;
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else if (b.requestFullscreen) b.requestFullscreen().catch(() => say('Full screen was refused by the browser.'));
    };

    useEffect(() => {
      api('/api/studio-files/roots').then(r => r.json()).then(d => {
        const rs = (d.roots || []).filter(r => r.available);
        setRoots(rs);
        const want = window.__files3dOpen && rs.find(r => r.id === window.__files3dOpen.root);
        if (want) { openPending(); return; }
        if (props.root && rs.find(r => r.id === props.root)) { setRoot(props.root); setPath(props.path || ''); return; }
        const pref = rs.find(r => r.id === recall('root')) || rs.find(r => r.id === 'creations') || rs[0];
        if (pref) setRoot(pref.id);
      }).catch(() => setErr('Could not reach the file service.'));
    }, []);

    // Deep link: StudioWS stores {root, path} and fires friday-files3d-open.
    const openPending = () => {
      const o = window.__files3dOpen;
      if (!o) return;
      window.__files3dOpen = null;
      setRoot(o.root); setPath(o.path || '');
    };
    useEffect(() => {
      window.addEventListener('friday-files3d-open', openPending);
      return () => window.removeEventListener('friday-files3d-open', openPending);
    }, []);

    const scan = useCallback((r, p) => {
      if (!r) return;
      setLoading(true); setErr(''); setSel(-1); setPreview(null);
      api('/api/studio-files/scan?' + qs(r, p) + '&limit=8000&depth=7').then(res => res.json().then(j => ({ ok: res.ok, j }))).then(({ ok, j }) => {
        setLoading(false);
        if (!ok) { setErr(j.error || 'That folder is not available.'); return; }
        const list = buildItems(j);
        setItems(list);
        setScanInfo({ truncated: j.truncated, skipped: j.skipped, ms: j.elapsed_ms, label: j.label, creations: j.creations_prefix });
        itemsRef.current = list;
        const eng = engRef.current;
        if (eng) { eng.setData(list, r, j.path); eng.select(-1); reapplyHeld(); }
      }).catch(() => { setLoading(false); setErr('Scan failed.'); });
    }, []);
    useEffect(() => { if (root) scan(root, path); }, [root, path, scan]);

    // filter
    useEffect(() => {
      const eng = engRef.current;
      if (!eng || !items.length) return;
      const t = setTimeout(() => {
        const q = query.trim().toLowerCase();
        const on = Object.keys(cats).filter(k => cats[k]);
        if (!q && !on.length) { eng.setFilter(null); return; }
        const mask = new Uint8Array(items.length);
        const terms = q.split(/\s+/).filter(Boolean);
        items.forEach((it, i) => {
          if (on.length && on.indexOf(it.cat) < 0) return;
          const hay = it.rel.toLowerCase();
          if (terms.every(tm => tm.startsWith('.') ? it.ext === tm.slice(1) : hay.indexOf(tm) >= 0)) mask[i] = 1;
        });
        eng.setFilter(mask);
      }, 160);
      return () => clearTimeout(t);
    }, [query, cats, items]);

    useEffect(() => { engRef.current && engRef.current.setView(view); if (!props.root) remember('view', view); }, [view]);  // an embedded browser leaves Studio's memory alone
    // Dazzle level: Settings › Appearance (studio_dazzle), mirrored here.
    useEffect(() => {
      const load = () => api('/api/settings').then(r => r.json()).then(d => {
        const v = ((d && (d.settings || d)) || {}).studio_dazzle;
        if (v === 'off' || v === 'subtle' || v === 'full') setDazzle(v);
      }).catch(() => {});
      load();
      const onSet = e => { if (e && e.detail) setDazzle(e.detail); };
      window.addEventListener('friday-dazzle', onSet);
      window.addEventListener('focus', load);
      return () => { window.removeEventListener('friday-dazzle', onSet); window.removeEventListener('focus', load); };
    }, []);
    useEffect(() => { engRef.current && engRef.current.setDazzle(dazzle); }, [dazzle]);
    const saveDazzle = v => {
      setDazzle(v);
      try { window.dispatchEvent(new CustomEvent('friday-dazzle', { detail: v })); } catch (_) {}
      postJSON('/api/settings', { settings: { studio_dazzle: v } }).catch(() => {});
    };
    useEffect(() => { if (root && !props.root) remember('root', root); }, [root]);
    useEffect(() => { engRef.current && engRef.current.setGroupBy(groupBy); }, [groupBy]);

    function choose(i, fly) {
      setSel(i);
      engRef.current && engRef.current.select(i, fly);
      const it = itemsRef.current[i];
      if (!it) { setPreview(null); return; }
      setPreview({ it, text: null });
      if (!it.dir && TEXT_PREVIEW.has(it.ext) && it.size < 4 * 1024 * 1024) {
        api('/api/studio-files/raw?' + qs(rootRef.current, it.rel) + '&text=1').then(r => r.json()).then(j => {
          setPreview(p => p && p.it === it ? Object.assign({}, p, { text: j.binary ? '(binary file)' : j.text, truncated: j.truncated }) : p);
        }).catch(() => {});
      }
    }
    const rootRef = useRef(''); rootRef.current = root;
    function pick(i, activate) {
      if (i < 0) { setSel(-1); engRef.current && engRef.current.select(-1); setPreview(null); return; }
      const it = itemsRef.current[i];
      if (activate && it && it.dir) { setPath(it.rel); return; }
      choose(i, true);
    }

    const say = (text, extra) => { setToast(Object.assign({ text }, extra)); setTimeout(() => setToast(t => t && t.text === text ? null : t), extra && extra.sticky ? 20000 : 5000); };
    const idxOf = rel => itemsRef.current.findIndex(x => x.rel === rel);
    const fx = (kind, rel, opts) => { const e = engRef.current, i = idxOf(rel); return e && i >= 0 ? e.fx(kind, i, opts) : Promise.resolve(false); };
    // Pending approvals by path: the card hovers in an amber "held" glow
    // until the owner decides, and again after any re-scan.
    const pendingRef = useRef(new Map());
    const reapplyHeld = () => {
      const e = engRef.current;
      if (!e) return;
      pendingRef.current.forEach((_, rel) => { const i = idxOf(rel); if (i >= 0) e.setHeld(i, true); });
    };
    const act = (what, it) => {
      if (!it) return;
      if (what === 'copy') {
        const lbl = (roots.find(r => r.id === root) || {}).label || root;
        try { navigator.clipboard.writeText(lbl + '/' + it.rel); say('Path copied'); } catch (_) { say('Copy failed'); }
        return;
      }
      postJSON('/api/studio-files/' + what, { root, path: it.rel }).then(({ ok, j }) => {
        if (!ok) { fx('fail', it.rel); say(j.error || 'Refused'); return; }
        if (what === 'open') fx('open', it.rel);
        say(what === 'open' ? 'Opened ' + it.name : 'Showing ' + it.name + ' in Explorer');
      });
    };
    // Share hands a creation to Friday's Share / Post dialog; the card
    // launches with a ripple once the dialog is actually open.
    const creationName = it => {
      const pre = scanInfo && scanInfo.creations;
      if (pre == null || !it || it.dir) return null;
      const rest = pre ? (it.rel.startsWith(pre + '/') ? it.rel.slice(pre.length + 1) : null) : it.rel;
      return rest && rest.indexOf('/') < 0 ? rest : null;
    };
    const share = it => {
      const fname = creationName(it);
      if (!fname || !window.fridayQuickPost) return;
      const open = () => window.fridayQuickPost({ title: it.name.replace(/[-_]/g, ' ').replace(/\.[^.]+$/, ''), content: '', asset: { filename: fname }, source: { kind: 'creation', ref: fname } });
      fx('share', it.rel).then(open, open);
    };
    const folderLabel = () => (roots.find(r => r.id === root) || {}).label || root;
    const change = (op, it) => {
      if (!it) return;
      const body = { op, root, path: it.rel };
      if (op === 'rename') {
        const nn = window.prompt('New name for ' + it.name, it.name);
        if (!nn || nn === it.name) return;
        body.new_name = nn;
      } else if (op === 'move' || op === 'copy') {
        const here = it.rel.includes('/') ? it.rel.slice(0, it.rel.lastIndexOf('/')) : '';
        const dest = window.prompt((op === 'move' ? 'Move' : 'Copy') + ' "' + it.name + '" to which folder? (a path inside ' + folderLabel() + ', blank = top level)', here);
        if (dest === null) return;
        body.dest_root = root; body.dest_path = dest.replace(/^\/+|\/+$/g, '');
      }
      say('Asking for your approval…', { sticky: true });
      postJSON('/api/studio-files/request-change', body).then(({ ok, j }) => {
        if (!ok || !j.approval_id) { fx('fail', it.rel); say(j.error || 'Refused'); return; }
        pendingRef.current.set(it.rel, { op, id: j.approval_id, body });
        reapplyHeld();
        say('Waiting for your approval — nothing changes until you approve it.', { sticky: true, approval: j.approval_id });
        watchApproval(j.approval_id, it.rel, op, body);
      });
    };
    // The animation follows the server's account of what happened: the
    // success animation plays only after the approved change reports ok.
    const watchApproval = (id, rel, op, body) => {
      let tries = 0;
      const release = () => { pendingRef.current.delete(rel); const e = engRef.current, i = idxOf(rel); if (e && i >= 0) e.setHeld(i, false); };
      const iv = setInterval(() => {
        if (++tries > 200) { clearInterval(iv); return; }
        api('/api/approvals/' + id).then(r => r.json()).then(d => {
          const a = d.approval || d;
          if (!a || a.status === 'pending') return;
          if (a.status === 'approved' && !a.consumed) return;
          clearInterval(iv);
          release();
          const det = a.used_detail || {};
          if (a.status !== 'approved') { say('Not approved — nothing was changed.'); return; }
          if (det.ok === false) { fx('fail', rel); say('Approved, but it failed: ' + det.error); return; }
          const destIdx = (body.dest_root === root && body.dest_path != null) ? idxOf(body.dest_path) : -1;
          const dir = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/') + 1) : '';
          const onMid = op === 'rename' ? () => { const e = engRef.current, i = idxOf(rel); if (e && i >= 0) e.retile(i, dir + det.renamed_to, det.renamed_to); } : null;
          const msg = { delete: 'Moved to the Recycle Bin.', rename: 'Renamed to ' + det.renamed_to + '.', move: 'Moved.', copy: 'Copied' + (det.name ? ' as ' + det.name : '') + '.' }[op] || 'Done.';
          say(msg);
          fx(op, rel, { dest: destIdx, onMid }).then(() => scan(rootRef.current, pathRef.current));
        }).catch(() => {});
      }, 3000);
    };
    const pathRef = useRef(''); pathRef.current = path;
    const openApprovals = () => {
      if (window.fridayRunActions) window.fridayRunActions([{ type: 'navigate', workspace: 'system', tab: 'approvals' }]);
    };

    const onKey = e => {
      if (e.target === searchRef.current) {
        if (e.key === 'Escape') { setQuery(''); boxRef.current && boxRef.current.focus(); }
        return;
      }
      const eng = engRef.current;
      if (!eng) return;
      const k = e.key;
      const vi = '123456'.indexOf(k);
      let handled = true;
      if (vi >= 0) setView(VIEWS[vi].id);
      else if (k === '/' || (k === 'f' && (e.ctrlKey || e.metaKey))) searchRef.current && searchRef.current.focus();
      else if (k === 'ArrowLeft' || k === 'ArrowRight' || k === 'ArrowUp' || k === 'ArrowDown') {
        const dir = k.slice(5).toLowerCase();
        const j = view === 'ring' && (dir === 'left' || dir === 'right') ? eng.step(sel, dir === 'right' ? 1 : -1)
          : view === 'time' && (dir === 'up' || dir === 'down') ? eng.step(sel, dir === 'up' ? 1 : -1)
            : eng.nav(sel, dir);
        if (j >= 0) choose(j, true);
      } else if (k === 'Enter') {
        const it = items[sel];
        if (it && it.dir) setPath(it.rel); else if (it) act('open', it);
      } else if (k === 'Backspace') goUp();
      else if (k === 'Escape') pick(-1);
      else if (k === 'r' || k === 'R') eng.resetCamera();
      else handled = false;
      if (handled) { e.preventDefault(); e.stopPropagation(); }
    };
    const goUp = () => { if (path) setPath(path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''); };

    // counts per category for chips
    const counts = {};
    items.forEach(it => { counts[it.cat] = (counts[it.cat] || 0) + 1; });
    const crumbs = path ? path.split('/') : [];
    const rootLabel = (roots.find(r => r.id === root) || {}).label || root;
    const selIt = preview && preview.it;
    const rawURL = selIt ? '/api/studio-files/raw?' + qs(root, selIt.rel) : '';

    const chip = (on, onClick, label, color, title) => h('button', { key: label, className: 'btn' + (on ? '' : ' btn-magenta'), onClick, title, style: Object.assign({}, BTN, color && on ? { borderColor: color, color } : null) }, label);

    const skip = () => { const e = engRef.current; if (e && e.isAnimating()) e.skipFx(); };
    return h('div', { ref: boxRef, tabIndex: 0, onKeyDown: e => { skip(); onKey(e); }, onPointerDownCapture: skip, style: full ? { outline: 'none', display: 'flex', flexDirection: 'column', height: '100vh', padding: 10, boxSizing: 'border-box', background: '#02040a' } : { outline: 'none' } },
      // toolbar
      h('div', { style: { display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 } },
        h('select', { value: root, onChange: e => { setPath(''); setRoot(e.target.value); }, 'aria-label': 'Folder', style: { background: '#0b1220', color: '#cfe3ff', border: '1px solid #24406a', borderRadius: 6, padding: '5px 6px', fontSize: 12, minHeight: 30 } },
          roots.map(r => h('option', { key: r.id, value: r.id }, r.label))),
        h('div', { style: { display: 'flex', gap: 2, alignItems: 'center', fontSize: 12, color: '#8fb2dd', flexWrap: 'wrap' } },
          h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => setPath(''), title: 'Top of ' + rootLabel }, rootLabel),
          crumbs.map((c, i) => h(React.Fragment, { key: i }, h('span', null, '›'), h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => setPath(crumbs.slice(0, i + 1).join('/')) }, c))),
          path && h('button', { className: 'btn', style: BTN, onClick: goUp, title: 'Up one folder (Backspace)' }, '↑ Up')),
        h('button', { className: 'btn btn-magenta', style: BTN, onClick: toggleFull, title: full ? 'Leave full screen (Esc)' : 'Full screen' }, full ? '⤡ Exit full screen' : '⛶ Full screen'),
        h('label', { title: 'Dazzle: how much holographic polish the 3D view uses (also in Settings › Appearance)', style: { display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: '#8fb2dd', fontFamily: 'Orbitron, Inter, sans-serif', letterSpacing: '.08em' } }, '✨ DAZZLE',
          h('select', { value: dazzle, onChange: e => saveDazzle(e.target.value), 'aria-label': 'Dazzle', style: { background: '#0b1220', color: '#cfe3ff', border: '1px solid #24406a', borderRadius: 6, padding: '4px 6px', fontSize: 11, minHeight: 30 } },
            h('option', { value: 'off' }, 'Off'), h('option', { value: 'subtle' }, 'Subtle'), h('option', { value: 'full' }, 'Full'))),
        h('input', { ref: searchRef, value: query, onChange: e => setQuery(e.target.value), placeholder: 'Search names…  (.png for a type)', 'aria-label': 'Search files', style: { flex: '1 1 180px', minWidth: 140, background: '#0b1220', color: '#e6f0ff', border: '1px solid #24406a', borderRadius: 6, padding: '6px 8px', fontSize: 12, minHeight: 30 } })),
      h('div', { style: { display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6, alignItems: 'center' } },
        VIEWS.map((v, i) => h('button', { key: v.id, className: 'btn' + (view === v.id ? '' : ' btn-magenta'), style: Object.assign({}, BTN, { fontWeight: view === v.id ? 700 : 400 }), title: v.tip + ' (' + (i + 1) + ')', onClick: () => setView(v.id), 'aria-pressed': view === v.id }, v.ico + ' ' + v.label)),
        view === 'cluster' && h('span', { style: { marginLeft: 6, display: 'flex', gap: 3 } },
          chip(groupBy === 'type', () => setGroupBy('type'), 'by type'), chip(groupBy === 'folder', () => setGroupBy('folder'), 'by folder'))),
      h('div', { style: { display: 'flex', gap: 3, flexWrap: 'wrap', marginBottom: 6 } },
        Object.keys(CATS).filter(k => counts[k]).map(k => chip(!!cats[k], () => setCats(c => Object.assign({}, c, { [k]: !c[k] })), CATS[k].ico + ' ' + CATS[k].label + ' ' + counts[k], hex(CATS[k].color), 'Show only ' + CATS[k].label.toLowerCase()))),
      // stage
      h('div', { ref: stageRef, style: { position: 'relative', height: full ? 'auto' : stageH ? stageH : 'calc(100vh - 300px)', flex: full ? '1 1 auto' : undefined, minHeight: 340, borderRadius: 10, overflow: 'hidden', border: '1px solid rgba(80,140,220,0.25)', background: '#03060d' } },
        h('div', { ref: mountRef, style: { position: 'absolute', inset: 0 } }),
        noGL && h('div', { style: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9ab' } }, '3D is unavailable in this browser.'),
        (loading || err) && h('div', { style: { position: 'absolute', top: 12, left: 12, padding: '6px 10px', borderRadius: 6, background: PANEL_BG, color: err ? '#ff8a8a' : '#9fd0ff', fontSize: 12 } }, err || 'Scanning…'),
        !loading && !err && items.length === 0 && root && h('div', { style: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#7f93ad', fontSize: 13 } }, 'This folder is empty.'),
        hover && hover.it && (!selIt || hover.it !== selIt) && h('div', { style: { position: 'fixed', left: hover.x + 14, top: hover.y + 12, pointerEvents: 'none', padding: '5px 8px', borderRadius: 6, background: PANEL_BG, border: '1px solid ' + hex(CATS[hover.it.cat].color), color: '#e6f0ff', fontSize: 11, zIndex: 50, maxWidth: 280 } },
          h('div', { style: { fontWeight: 600, wordBreak: 'break-all' } }, hover.it.name),
          h('div', { style: { color: '#8fa6c4' } }, (hover.it.dir ? hover.it.kids.length + ' items · ' : '') + fmtSize(hover.it.size) + ' · ' + fmtDate(hover.it.mtime))),
        // status / help
        h('div', { style: { position: 'absolute', left: 10, bottom: 8, fontSize: 10, color: '#8fa3bf', pointerEvents: 'none', lineHeight: 1.5, background: 'rgba(3,6,13,0.72)', padding: '3px 7px', borderRadius: 5 } },
          items.length ? (stats ? stats.visible : items.length) + ' of ' + items.length + ' shown' + (scanInfo && scanInfo.truncated ? ' · large folder: first ' + items.length + ' loaded' : '') + (stats ? ' · ' + stats.tiles + ' thumbnails live · ' + stats.fps + ' fps' : '') : '',
          h('br'), 'drag orbit · right-drag pan · scroll zoom · 1–6 views · arrows move · Enter open · / search'),
        // preview panel
        selIt && h('div', { style: { position: 'absolute', top: 10, right: 10, bottom: 10, width: 'min(380px, 46%)', display: 'flex', flexDirection: 'column', gap: 8, padding: 12, borderRadius: 10, background: PANEL_BG, border: '1px solid ' + hex(CATS[selIt.cat].color) + '88', color: '#e6f0ff', fontSize: 12, overflow: 'hidden' } },
          h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8 } },
            h('div', { style: { fontWeight: 700, fontSize: 13, wordBreak: 'break-all' } }, CATS[selIt.cat].ico + ' ' + selIt.name),
            h('button', { className: 'btn', style: BTN, onClick: () => pick(-1), 'aria-label': 'Close preview' }, '✕')),
          h('div', { style: { color: '#8fa6c4', fontSize: 11 } }, (selIt.dir ? selIt.kids.length + ' items · ' : '') + fmtSize(selIt.size) + ' · modified ' + fmtDate(selIt.mtime), h('br'), rootLabel + '/' + selIt.rel),
          h('div', { style: { flex: 1, minHeight: 0, overflow: 'auto', borderRadius: 8, background: '#02050b', display: 'flex', alignItems: selIt.cat === 'image' || selIt.cat === 'video' ? 'center' : 'stretch', justifyContent: 'center' } },
            selIt.cat === 'image' ? h('img', { src: rawURL, alt: selIt.name, style: { maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' } })
              : selIt.cat === 'video' ? h('video', { src: rawURL, controls: true, preload: 'metadata', style: { width: '100%', maxHeight: '100%' } })
                : selIt.cat === 'audio' ? h('div', { style: { padding: 20, width: '100%' } }, h('audio', { src: rawURL, controls: true, style: { width: '100%' } }))
                  : selIt.ext === 'pdf' ? h('iframe', { src: rawURL, title: selIt.name, style: { width: '100%', height: '100%', border: 'none', background: '#fff' } })
                    : preview.text != null ? h('pre', { style: { margin: 0, padding: 10, fontSize: 11, lineHeight: 1.45, whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#cfe0f5', fontFamily: 'JetBrains Mono, Consolas, monospace', width: '100%' } }, preview.text + (preview.truncated ? '\n\n… (preview cut at 256 KB)' : ''))
                      : selIt.dir ? h('div', { style: { padding: 16, color: '#9fb6d6', lineHeight: 1.7 } }, 'Folder with ' + selIt.kids.length + ' items shown here.', h('br'), 'Double-click it, press Enter, or use "Enter folder" to fly inside.')
                        : h('div', { style: { padding: 16, color: '#7f93ad' } }, TEXT_PREVIEW.has(selIt.ext) ? 'Loading…' : 'No preview for this type. Open it or show it in Explorer.')),
          h('div', { style: { display: 'flex', gap: 4, flexWrap: 'wrap' } },
            selIt.dir && h('button', { className: 'btn', style: BTN, onClick: () => setPath(selIt.rel) }, '⤵ Enter folder'),
            h('button', { className: 'btn', style: BTN, onClick: () => act('open', selIt), title: 'Open with its usual app (programs and scripts are never launched)' }, '↗ Open'),
            h('button', { className: 'btn', style: BTN, onClick: () => act('reveal', selIt) }, '📂 Show in Explorer'),
            h('button', { className: 'btn', style: BTN, onClick: () => act('copy', selIt) }, '⧉ Copy path'),
            creationName(selIt) && window.fridayQuickPost && h('button', { className: 'btn', style: BTN, onClick: () => share(selIt), title: 'Share or post this creation to your platforms' }, '📤 Share…')),
          h('details', { style: { fontSize: 11, color: '#9fb0c8' } },
            h('summary', { style: { cursor: 'pointer', padding: '4px 0' } }, 'Change this file… (needs your approval)'),
            h('div', { style: { display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' } },
              h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => change('rename', selIt) }, 'Rename…'),
              h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => change('move', selIt) }, 'Move…'),
              !selIt.dir && h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => change('copy', selIt) }, 'Copy to…'),
              h('button', { className: 'btn btn-magenta', style: Object.assign({}, BTN, { color: '#ff9a9a' }), onClick: () => change('delete', selIt) }, 'Delete…')),
            h('div', { style: { marginTop: 6, color: '#6f86a6' } }, 'These file an approval card in System › Approvals. Nothing changes until you approve it there; delete goes to the Recycle Bin.'))),
        toast && h('div', { role: 'status', style: { position: 'absolute', left: '50%', bottom: 44, transform: 'translateX(-50%)', padding: '8px 12px', borderRadius: 8, background: PANEL_BG, border: '1px solid #2e5a8f', color: '#e6f0ff', fontSize: 12, display: 'flex', gap: 8, alignItems: 'center', maxWidth: '80%' } },
          toast.text,
          toast.approval && h('button', { className: 'btn', style: BTN, onClick: openApprovals }, 'Review in Approvals'))));
  }

  window.Files3DPanel = Files3DPanel;

  // The Dazzle level also shapes page-wide motion (window open/close in
  // index.html reads :root[data-dazzle]).
  if (typeof document !== 'undefined' && window.addEventListener) {
    const markDazzle = v => { if (v === 'off' || v === 'subtle' || v === 'full') document.documentElement.dataset.dazzle = v; };
    api('/api/settings').then(r => r.json()).then(d => markDazzle(((d && (d.settings || d)) || {}).studio_dazzle)).catch(() => {});
    window.addEventListener('friday-dazzle', e => markDazzle(e && e.detail));
  }
  window.__files3dInternals = { buildItems, catOf, createSlotPool, stackSlots };
  window.Friday3D = { createEngine, registerCats, CATS, BRAND, api, postJSON, fmtSize, fmtDate, hex, recall, remember };
})();
