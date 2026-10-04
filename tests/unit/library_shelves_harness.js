/* A headless page for the Library's Shelves view: a DOM stub, a THREE stub that
 * keeps the real math the layouts and cameras read, a fake clock, and the real
 * engine (static/studio_files3d.js) and component (static/library_shelves.js)
 * loaded into it. The stub records every InstancedMesh and Mesh that is built, so
 * a test can count draws; it draws nothing.
 *
 * Used by tests/unit/test_library_shelves_*.py through `node`.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..', '..');

function makeThree(rec) {
  class Vector3 {
    constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
    set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }
    setScalar(s) { this.x = this.y = this.z = s; return this; }
    copy(v) { this.x = v.x; this.y = v.y; this.z = v.z; return this; }
    clone() { return new Vector3(this.x, this.y, this.z); }
    add(v) { this.x += v.x; this.y += v.y; this.z += v.z; return this; }
    sub(v) { this.x -= v.x; this.y -= v.y; this.z -= v.z; return this; }
    addScaledVector(v, s) { this.x += v.x * s; this.y += v.y * s; this.z += v.z * s; return this; }
    multiplyScalar(s) { this.x *= s; this.y *= s; this.z *= s; return this; }
    length() { return Math.hypot(this.x, this.y, this.z); }
    normalize() { const l = this.length() || 1; return this.multiplyScalar(1 / l); }
    lerp(v, k) { this.x += (v.x - this.x) * k; this.y += (v.y - this.y) * k; this.z += (v.z - this.z) * k; return this; }
    distanceTo(v) { return Math.hypot(this.x - v.x, this.y - v.y, this.z - v.z); }
    toArray() { return [this.x, this.y, this.z]; }
    applyQuaternion() { return this; }
    project() { this.x = 0; this.y = 0; this.z = 0; return this; }
  }
  class Vector2 { constructor(x = 0, y = 0) { this.x = x; this.y = y; } set(x, y) { this.x = x; this.y = y; return this; } }
  class Euler { constructor(x = 0, y = 0, z = 0, o = 'XYZ') { this.set(x, y, z, o); } set(x, y, z, o) { this.x = x; this.y = y; this.z = z; this.order = o; return this; } }
  class Quaternion {
    constructor(x = 0, y = 0, z = 0, w = 1) { this.x = x; this.y = y; this.z = z; this.w = w; }
    set(x, y, z, w) { this.x = x; this.y = y; this.z = z; this.w = w; return this; }
    copy(q) { this.x = q.x; this.y = q.y; this.z = q.z; this.w = q.w; return this; }
    slerp() { return this; }
    setFromRotationMatrix() { return this; }
    setFromUnitVectors() { return this; }
    // YXZ order, the only one the engine uses for layouts
    setFromEuler(e) {
      const c1 = Math.cos(e.x / 2), c2 = Math.cos(e.y / 2), c3 = Math.cos(e.z / 2);
      const s1 = Math.sin(e.x / 2), s2 = Math.sin(e.y / 2), s3 = Math.sin(e.z / 2);
      this.x = s1 * c2 * c3 + c1 * s2 * s3; this.y = c1 * s2 * c3 - s1 * c2 * s3;
      this.z = c1 * c2 * s3 - s1 * s2 * c3; this.w = c1 * c2 * c3 + s1 * s2 * s3;
      return this;
    }
  }
  Quaternion.slerpFlat = (dst, di, a, ai, b, bi, t) => { for (let k = 0; k < 4; k++) dst[di + k] = a[ai + k] + (b[bi + k] - a[ai + k]) * t; };
  class Matrix4 {
    constructor() { this.elements = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; }
    compose() { return this; } lookAt() { return this; } makeScale() { return this; } makeTranslation() { return this; } premultiply() { return this; } copy() { return this; }
  }
  class Color {
    constructor(c) { this.r = 0; this.g = 0; this.b = 0; this.isColor = true; if (c != null) this.set(c); }
    set(c) { return typeof c === 'number' ? this.setHex(c) : this.copy(c); }
    setHex(h) { this.r = ((h >> 16) & 255) / 255; this.g = ((h >> 8) & 255) / 255; this.b = (h & 255) / 255; return this; }
    setHSL() { return this; } multiplyScalar(s) { this.r *= s; this.g *= s; this.b *= s; return this; }
    copy(c) { this.r = c.r; this.g = c.g; this.b = c.b; return this; } lerp() { return this; }
  }
  class Attr {
    constructor(array, w) { this.array = array; this.itemSize = w; }
    setUsage() { return this; }
    getX(i) { return this.array[i * this.itemSize]; } getY(i) { return this.array[i * this.itemSize + 1]; } getZ(i) { return this.array[i * this.itemSize + 2]; }
    setXYZ(i, x, y, z) { const a = this.array, w = this.itemSize; a[i * w] = x; a[i * w + 1] = y; a[i * w + 2] = z; return this; }
    setXYZW(i, x, y, z, ww) { const a = this.array, w = this.itemSize; a[i * w] = x; a[i * w + 1] = y; a[i * w + 2] = z; a[i * w + 3] = ww; return this; }
  }
  class BufferGeometry {
    constructor() { this.attributes = {}; }
    setAttribute(k, a) { this.attributes[k] = a; return this; } setDrawRange() {} dispose() {} translate() { return this; }
    clone() { const g = new BufferGeometry(); Object.assign(g.attributes, this.attributes); return g; }
    setFromPoints() { return this; }
  }
  class Obj {
    constructor() { this.children = []; this.position = new Vector3(); this.rotation = new Euler(); this.quaternion = new Quaternion(); this.scale = new Vector3(1, 1, 1); this.visible = true; this.userData = {}; this.matrix = new Matrix4(); this.matrixWorld = new Matrix4(); }
    add(o) { this.children.push(o); return this; }
    remove(o) { const k = this.children.indexOf(o); if (k >= 0) this.children.splice(k, 1); return this; }
    traverse(f) { f(this); this.children.slice().forEach(c => c.traverse && c.traverse(f)); }
  }
  const mat = extra => class { constructor(o) { this.uniforms = (o && o.uniforms) || {}; this.userData = {}; Object.assign(this, o || {}); this.color = new Color(typeof this.color === 'number' ? this.color : 0); if (extra) extra(this); } dispose() {} };
  const geomClass = () => class extends BufferGeometry { constructor() { super(); this.parameters = arguments[0]; } };
  class Mesh extends Obj {
    constructor(g, m) { super(); this.geometry = g; this.material = m; rec.meshes.push(this); }
  }
  class InstancedMesh extends Mesh {
    constructor(g, m, count) {
      super(g, m); rec.meshes.pop(); rec.instanced.push(this);
      this.count = count; this.cap = count;
      this.instanceMatrix = { setUsage() {}, needsUpdate: false };
      this.draws = 0;
    }
    setMatrixAt() {} setColorAt() { this.instanceColor = { needsUpdate: false }; }
  }
  class Camera extends Obj {
    constructor() { super(); this.fov = 52; this.aspect = 1; this.matrixWorldInverse = new Matrix4(); this.projectionMatrix = new Matrix4(); }
    lookAt() {} updateMatrixWorld() {} updateProjectionMatrix() {}
  }
  class WebGLRenderer {
    constructor() {
      this.domElement = makeEl('canvas');
      this.capabilities = { maxTextures: 16, getMaxAnisotropy: () => 4 };
      this.info = { render: { calls: 0, triangles: 0 } };
    }
    setPixelRatio() {} setClearColor() {} setSize() {} getPixelRatio() { return 1; } initTexture() {} copyTextureToTexture() {} compile() {} dispose() {}
    getContext() { return { getExtension: () => null }; }
    render() { rec.renders++; }
  }
  const tex = () => class { constructor(a) { this.image = a; } dispose() {} };
  const T = {
    Vector3, Vector2, Euler, Quaternion, Matrix4, Color, BufferGeometry, BufferAttribute: Attr, InstancedBufferAttribute: Attr,
    Object3D: Obj, Group: Obj, Scene: Obj, Mesh, InstancedMesh, Points: Mesh, LineSegments: Mesh, Line: Mesh,
    Sprite: class extends Obj { constructor(m) { super(); this.material = m; } },
    PerspectiveCamera: Camera, WebGLRenderer,
    HemisphereLight: Obj, DirectionalLight: Obj,
    Raycaster: class { setFromCamera() {} intersectObject() { return []; } intersectObjects() { return []; } },
    TextureLoader: class { load(u, ok) { return {}; } },
    DataTexture: tex(), CanvasTexture: tex(),
    MeshBasicMaterial: mat(), MeshLambertMaterial: mat(), ShaderMaterial: mat(), SpriteMaterial: mat(), LineBasicMaterial: mat(), PointsMaterial: mat(),
    PlaneGeometry: geomClass(), SphereGeometry: geomClass(), BoxGeometry: geomClass(), CylinderGeometry: geomClass(), EdgesGeometry: geomClass(),
    AdditiveBlending: 2, BackSide: 1, DoubleSide: 2, DynamicDrawUsage: 35048, RGBAFormat: 1023, LinearFilter: 1006, LinearMipmapLinearFilter: 1008
  };
  return T;
}

function makeCtx() {
  const calls = [];
  const grad = { addColorStop() {} };
  const ctx = new Proxy({ calls }, {
    get(t, k) {
      if (k in t) return t[k];
      if (k === 'measureText') return s => ({ width: String(s).length * 8 });
      if (k === 'createLinearGradient' || k === 'createRadialGradient') return () => grad;
      return (...a) => { calls.push([k, ...a]); };
    },
    set(t, k, v) { t[k] = v; calls.push(['set:' + k, v]); return true; }
  });
  return ctx;
}

function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(), style: {}, children: [], width: 300, height: 150, clientWidth: 1000, clientHeight: 700, dataset: {},
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); c.parentNode = null; },
    remove() {}, addEventListener() {}, removeEventListener() {}, setPointerCapture() {}, focus() {},
    getBoundingClientRect() { return { left: 0, top: 0, right: 1000, bottom: 700, width: 1000, height: 700 }; },
    getContext() { return this._ctx || (this._ctx = makeCtx()); }
  };
  return el;
}

// The page. opts.reduced: prefers-reduced-motion. Returns {window, rec, clock, frame(ms), vars}.
function makePage(opts) {
  opts = opts || {};
  const rec = { meshes: [], instanced: [], renders: 0 };
  const listeners = {};
  const clock = { t: 1000, timers: [], raf: null };
  const tokens = { '--fr-cyan': '#00d4ff', '--fr-violet': '#7b61ff', '--fr-magenta': '#ff00ff', '--fr-surface': '#0a0e1a', '--fr-text': 'rgba(255,255,255,0.86)', '--fr-dim': 'rgba(255,255,255,0.46)', '--fr-cat-blue': '#60a5fa' };
  const win = {
    THREE: makeThree(rec), devicePixelRatio: 1, innerWidth: 1280, innerHeight: 800,
    addEventListener(k, f) { (listeners[k] = listeners[k] || []).push(f); },
    removeEventListener(k, f) { listeners[k] = (listeners[k] || []).filter(x => x !== f); },
    dispatchEvent(ev) { (listeners[ev.type] || []).slice().forEach(f => f(ev)); return true; },
    matchMedia: q => ({ matches: !!opts.reduced && /reduce/.test(q), addEventListener() {} }),
    fetch: opts.fetch || (() => Promise.reject(new Error('no network'))),
    __FRIDAY_API_TOKEN: 'test'
  };
  win.window = win;
  const doc = {
    hidden: false, documentElement: { dataset: {}, style: {} }, head: makeEl('head'), body: makeEl('body'),
    createElement: makeEl, getElementById: () => null
  };
  const g = {
    window: win, document: doc, THREE: win.THREE, navigator: { userAgent: 'node' },
    performance: { now: () => clock.t },
    requestAnimationFrame: f => { clock.raf = f; return 1; }, cancelAnimationFrame() { clock.raf = null; },
    setTimeout: (f, ms) => { const id = clock.timers.length + 1; clock.timers.push({ id, at: clock.t + (ms || 0), f }); return id; },
    clearTimeout: id => { clock.timers = clock.timers.filter(x => x.id !== id); },
    setInterval: () => 0, clearInterval() {},
    ResizeObserver: class { observe() {} disconnect() {} },
    IntersectionObserver: class { constructor(cb) { this.cb = cb; } observe() { this.cb([{ isIntersecting: true }]); } disconnect() {} },
    Worker: class { constructor() { throw new Error('no workers'); } },
    getComputedStyle: () => ({ getPropertyValue: k => tokens[k] || '' }),
    fetch: win.fetch, console, Math, JSON, Promise, Map, Set, Float32Array, Float64Array, Int32Array, Uint8Array, Array, Object, String, Number, Date, Error
  };
  g.globalThis = g;
  win.performance = g.performance;
  const React = { createElement: (t, p, ...c) => ({ t, p, c }), useState: v => [typeof v === 'function' ? v() : v, () => {}], useEffect() {}, useRef: v => ({ current: v }), useCallback: f => f, Fragment: 'f' };
  g.React = React; win.React = React;
  const ctx = vm.createContext(g);
  const load = rel => vm.runInContext(fs.readFileSync(path.join(ROOT, rel), 'utf8'), ctx, { filename: rel });
  // advance the fake clock, running timers and animation frames at 16 ms
  function advance(ms, step) {
    step = step || 16;
    const end = clock.t + ms;
    while (clock.t < end) {
      clock.t = Math.min(end, clock.t + step);
      clock.timers.sort((a, b) => a.at - b.at);
      while (clock.timers.length && clock.timers[0].at <= clock.t) clock.timers.shift().f();
      if (clock.raf) { const f = clock.raf; clock.raf = null; f(clock.t); }
    }
  }
  return { window: win, document: doc, rec, clock, load, advance, tokens, ctx, makeEl };
}

module.exports = { makePage, makeThree, makeCtx, makeEl, ROOT };
