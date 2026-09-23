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
  const hex = c => '#' + c.toString(16).padStart(6, '0');

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
      used() { let u = 0; for (let s = 0; s < slots; s++) if (itemIn[s] >= 0) u++; return u; }
    };
  }

  // ── engine ──────────────────────────────────────────────────────────────
  function createEngine(mount, cb) {
    const THREE = window.THREE;
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setClearColor(0x03060d, 1);
    mount.appendChild(renderer.domElement);
    renderer.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;outline:none;cursor:grab;touch-action:none';
    const scene = new THREE.Scene();
    const FOG = new THREE.Color(0x03060d);
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

    const cardMat = new THREE.ShaderMaterial({
      uniforms: {
        uAtlas: { value: atlases }, uTileUV: { value: TILE / ATLAS }, uPad: { value: 1.5 / TILE },
        uFog: { value: FOG }, uFogDensity: { value: 0.0055 }
      },
      vertexShader: [
        'attribute vec3 aTile; attribute vec3 aColor; attribute vec2 aState;',
        'varying vec2 vUv; varying vec3 vTile; varying vec3 vColor; varying vec2 vState; varying float vDepth;',
        'void main(){ vUv=uv; vTile=aTile; vColor=aColor; vState=aState;',
        '  vec4 mv = modelViewMatrix * instanceMatrix * vec4(position,1.0);',
        '  vDepth = -mv.z; gl_Position = projectionMatrix * mv; }'
      ].join('\n'),
      fragmentShader: [
        'uniform sampler2D uAtlas[8]; uniform float uTileUV; uniform float uPad; uniform vec3 uFog; uniform float uFogDensity;',
        'varying vec2 vUv; varying vec3 vTile; varying vec3 vColor; varying vec2 vState; varying float vDepth;',
        'vec4 atl(float i, vec2 uv){',
        '  if(i<0.5) return texture2D(uAtlas[0],uv); if(i<1.5) return texture2D(uAtlas[1],uv);',
        '  if(i<2.5) return texture2D(uAtlas[2],uv); if(i<3.5) return texture2D(uAtlas[3],uv);',
        '  if(i<4.5) return texture2D(uAtlas[4],uv); if(i<5.5) return texture2D(uAtlas[5],uv);',
        '  if(i<6.5) return texture2D(uAtlas[6],uv); return texture2D(uAtlas[7],uv); }',
        'void main(){',
        '  vec2 uv=vUv; float edge=min(min(uv.x,1.0-uv.x),min(uv.y,1.0-uv.y));',
        '  vec3 col;',
        '  if(vTile.x < -0.5){',
        '    col = mix(vColor*0.16, vColor*0.42, uv.y);',
        '    col = mix(col, vColor*0.75, (1.0-step(0.2,uv.y))*0.55);',
        '  } else {',
        '    vec2 t = vec2(uv.x, 1.0-uv.y)*(1.0-2.0*uPad)+uPad;',
        '    col = atl(vTile.x, vTile.yz + t*uTileUV).rgb;',
        '  }',
        '  if(!gl_FrontFacing) col = vColor*0.14;',
        '  float st = vState.x;',
        '  float bw = st > 1.5 ? 0.06 : 0.035;',
        '  float border = 1.0 - smoothstep(0.0, bw, edge);',
        '  vec3 rim = st > 1.5 ? vec3(0.55,0.95,1.0) : vColor*(1.0+st*0.7);',
        '  col = mix(col, rim, border*(0.5+0.5*min(st,1.0)));',
        '  col *= mix(1.0, 0.22, vState.y);',
        '  if(st > 0.5) col += vec3(0.05,0.07,0.1);',
        '  float f = 1.0 - exp(-uFogDensity*uFogDensity*vDepth*vDepth);',
        '  gl_FragColor = vec4(mix(col, uFog, f), 1.0); }'
      ].join('\n'),
      side: THREE.DoubleSide
    });
    const boxMat = new THREE.MeshLambertMaterial({ color: 0xffffff });
    const boxGeo = new THREE.BoxGeometry(1, 1, 1); boxGeo.translate(0, 0.5, 0);
    const planeGeo = new THREE.PlaneGeometry(1, 1);
    const lineMat = new THREE.LineBasicMaterial({ color: 0x3d7fd0, transparent: true, opacity: 0, depthWrite: false });

    // ── per-item state ──
    let items = [], n = 0;
    let cards = null, boxes = null, lines = null, lineGeo = null, edges = null;
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
      [cards, boxes, lines].forEach(m => { if (m) { scene.remove(m); if (m.geometry !== planeGeo && m.geometry !== boxGeo) m.geometry.dispose(); } });
      cards = boxes = lines = null;
    }

    function setData(list) {
      gen++;
      freeMeshes();
      items = list; n = list.length;
      const cap = Math.max(1, n);
      const g = planeGeo.clone();
      aTile = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3).fill(-1), 3);
      aColor = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3), 3);
      aState = new THREE.InstancedBufferAttribute(new Float32Array(cap * 2), 2);
      aTile.setUsage(THREE.DynamicDrawUsage); aState.setUsage(THREE.DynamicDrawUsage);
      g.setAttribute('aTile', aTile); g.setAttribute('aColor', aColor); g.setAttribute('aState', aState);
      cards = new THREE.InstancedMesh(g, cardMat, cap);
      cards.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      cards.frustumCulled = false;
      cards.count = n;
      scene.add(cards);
      boxes = new THREE.InstancedMesh(boxGeo, boxMat, cap);
      boxes.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      boxes.frustumCulled = false; boxes.count = n;
      const c = new THREE.Color();
      for (let i = 0; i < n; i++) {
        c.setHex(CATS[items[i].cat].color);
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
      applyLayout(true);
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
      } else {
        clusterLayout(L, act, put);
      }
      if (!L.cam) L.cam = { t: [0, 0, 0], theta: 0, phi: 1.2, r: 60 };
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
        const color = groupBy === 'type' && CATS[k] ? CATS[k].color : 0x9fd0ff;
        let label = groupBy === 'type' && CATS[k] ? CATS[k].label : k;
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
          const m = new THREE.LineBasicMaterial({ color: 0x3fa9ff, transparent: true, opacity: 0 });
          const loop = new THREE.Line(cg, m);
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
    function applyLayout(instant) {
      if (!n) { dirty = true; return; }
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
        flight = { t0: performance.now(), dur: 1300 };
      }
      setCamGoal(layout.cam, instant);
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
      camera.updateMatrixWorld();
      camQ.copy(camera.quaternion);
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
      for (let i = 0; i < n; i++) aState.setXY(i, i === selIdx ? 2 : i === hoverIdx ? 1 : 0, 0);
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
        color: hex(CATS[it.cat].color), token: window.__FRIDAY_API_TOKEN || '',
        url: SERVER_THUMB.has(it.ext) ? '/api/studio-files/thumb?' + qs(currentRoot, it.rel) + '&s=128&m=' + it.mtime : null
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
      drag = { x: e.clientX, y: e.clientY, moved: false, pan: e.button === 2 || e.button === 1 || e.shiftKey };
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
      const wasDrag = drag && drag.moved;
      drag = null;
      if (wasDrag) return;
      const i = pickAt(e.clientX, e.clientY);
      cb.onPick && cb.onPick(i >= 0 ? i : -1, false);
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
      if (moved || dirty || up || (view === 'ring' && camMoving)) {
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
        currentRoot = root; currentPath = path || ''; currentBase = (path || '').split('/').pop() || root;
        setData(list);
      },
      setView(v) { if (v === view) return; view = v; applyLayout(false); },
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
        cardMat.dispose(); boxMat.dispose(); lineMat.dispose(); boxGeo.dispose(); planeGeo.dispose();
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

  function Files3DPanel() {
    const mountRef = useRef(null), engRef = useRef(null), boxRef = useRef(null), searchRef = useRef(null);
    const [roots, setRoots] = useState([]);
    const [root, setRoot] = useState('');
    const [path, setPath] = useState('');
    const [items, setItems] = useState([]);
    const [scanInfo, setScanInfo] = useState(null);
    const [loading, setLoading] = useState(false);
    const [err, setErr] = useState('');
    const [view, setView] = useState(() => VIEWS.some(v => v.id === recall('view')) ? recall('view') : 'wall');
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
        setScanInfo({ truncated: j.truncated, skipped: j.skipped, ms: j.elapsed_ms, label: j.label });
        const eng = engRef.current;
        if (eng) { eng.setData(list, r, j.path); eng.select(-1); }
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

    useEffect(() => { engRef.current && engRef.current.setView(view); remember('view', view); }, [view]);
    useEffect(() => { if (root) remember('root', root); }, [root]);
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
    const act = (what, it) => {
      if (!it) return;
      if (what === 'copy') {
        const lbl = (roots.find(r => r.id === root) || {}).label || root;
        try { navigator.clipboard.writeText(lbl + '/' + it.rel); say('Path copied'); } catch (_) { say('Copy failed'); }
        return;
      }
      postJSON('/api/studio-files/' + what, { root, path: it.rel }).then(({ ok, j }) => say(ok ? (what === 'open' ? 'Opened ' + it.name : 'Showing ' + it.name + ' in Explorer') : (j.error || 'Refused')));
    };
    const change = (op, it) => {
      if (!it) return;
      const body = { op, root, path: it.rel };
      if (op === 'rename') {
        const nn = window.prompt('New name for ' + it.name, it.name);
        if (!nn || nn === it.name) return;
        body.new_name = nn;
      } else if (op === 'move') {
        const dest = window.prompt('Move "' + it.name + '" to which folder? (a path inside ' + ((roots.find(r => r.id === root) || {}).label || root) + ', blank = top level)', it.rel.includes('/') ? it.rel.slice(0, it.rel.lastIndexOf('/')) : '');
        if (dest === null) return;
        body.dest_root = root; body.dest_path = dest;
      }
      say('Asking for your approval…', { sticky: true });
      postJSON('/api/studio-files/request-change', body).then(({ ok, j }) => {
        if (!ok || !j.approval_id) { say(j.error || 'Refused'); return; }
        say('Waiting for your approval — nothing changes until you approve it.', { sticky: true, approval: j.approval_id });
        watchApproval(j.approval_id);
      });
    };
    const watchApproval = id => {
      let tries = 0;
      const iv = setInterval(() => {
        if (++tries > 200) { clearInterval(iv); return; }
        api('/api/approvals/' + id).then(r => r.json()).then(d => {
          const a = d.approval || d;
          if (!a || a.status === 'pending') return;
          if (a.status === 'approved' && !a.consumed) return;
          clearInterval(iv);
          const det = a.used_detail || {};
          if (a.status === 'approved') say(det.ok === false ? 'Approved, but it failed: ' + det.error : 'Done — the change was applied.');
          else say('Not approved — nothing was changed.');
          if (a.status === 'approved' && det.ok !== false) scan(rootRef.current, pathRef.current);
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

    return h('div', { ref: boxRef, tabIndex: 0, onKeyDown: onKey, style: full ? { outline: 'none', display: 'flex', flexDirection: 'column', height: '100vh', padding: 10, boxSizing: 'border-box', background: '#02040a' } : { outline: 'none' } },
      // toolbar
      h('div', { style: { display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 } },
        h('select', { value: root, onChange: e => { setPath(''); setRoot(e.target.value); }, 'aria-label': 'Folder', style: { background: '#0b1220', color: '#cfe3ff', border: '1px solid #24406a', borderRadius: 6, padding: '5px 6px', fontSize: 12, minHeight: 30 } },
          roots.map(r => h('option', { key: r.id, value: r.id }, r.label))),
        h('div', { style: { display: 'flex', gap: 2, alignItems: 'center', fontSize: 12, color: '#8fb2dd', flexWrap: 'wrap' } },
          h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => setPath(''), title: 'Top of ' + rootLabel }, rootLabel),
          crumbs.map((c, i) => h(React.Fragment, { key: i }, h('span', null, '›'), h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => setPath(crumbs.slice(0, i + 1).join('/')) }, c))),
          path && h('button', { className: 'btn', style: BTN, onClick: goUp, title: 'Up one folder (Backspace)' }, '↑ Up')),
        h('button', { className: 'btn btn-magenta', style: BTN, onClick: toggleFull, title: full ? 'Leave full screen (Esc)' : 'Full screen' }, full ? '⤡ Exit full screen' : '⛶ Full screen'),
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
        h('div', { style: { position: 'absolute', left: 10, bottom: 8, fontSize: 10, color: '#6f86a6', pointerEvents: 'none', lineHeight: 1.5 } },
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
            h('button', { className: 'btn', style: BTN, onClick: () => act('copy', selIt) }, '⧉ Copy path')),
          h('details', { style: { fontSize: 11, color: '#9fb0c8' } },
            h('summary', { style: { cursor: 'pointer', padding: '4px 0' } }, 'Change this file… (needs your approval)'),
            h('div', { style: { display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' } },
              h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => change('rename', selIt) }, 'Rename…'),
              h('button', { className: 'btn btn-magenta', style: BTN, onClick: () => change('move', selIt) }, 'Move…'),
              h('button', { className: 'btn btn-magenta', style: Object.assign({}, BTN, { color: '#ff9a9a' }), onClick: () => change('delete', selIt) }, 'Delete…')),
            h('div', { style: { marginTop: 6, color: '#6f86a6' } }, 'These file an approval card in System › Approvals. Nothing changes until you approve it there; delete goes to the Recycle Bin.'))),
        toast && h('div', { role: 'status', style: { position: 'absolute', left: '50%', bottom: 44, transform: 'translateX(-50%)', padding: '8px 12px', borderRadius: 8, background: PANEL_BG, border: '1px solid #2e5a8f', color: '#e6f0ff', fontSize: 12, display: 'flex', gap: 8, alignItems: 'center', maxWidth: '80%' } },
          toast.text,
          toast.approval && h('button', { className: 'btn', style: BTN, onClick: openApprovals }, 'Review in Approvals'))));
  }

  window.Files3DPanel = Files3DPanel;
  window.__files3dInternals = { buildItems, catOf, createSlotPool };
})();
