// The avatar's displayed frame, without a browser: the visible-region maths,
// the spring that eases it, the Simple layout with the cards closed, and the
// mounted controller driven frame by frame against a small fake window.
// The browser layout and frame-timing proofs are tests/unit/test_avatar_center.py.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const impl = require(path.resolve(__dirname, '../../static/friday_holographic_workspace.js'));
const { visibleRegion, regionFit, offsetProjection, createRectSpring, spatialLayout, FRAME_OMEGA, FRAME_OMEGA_REDUCED } = impl;

const VIEW = { w: 1280, h: 800 };
const BAR = { top: 36 };
const near = (a, b, tolerance, note) => assert.ok(Math.abs(a - b) <= tolerance, `${note || ''} ${a} is not within ${tolerance} of ${b}`);

// ── the region ───────────────────────────────────────────────────────────────

test('with nothing open the region is the desktop below the top bar', () => {
    const region = visibleRegion(VIEW, BAR, []);
    assert.deepEqual([region.x, region.y, region.w, region.h], [0, 36, 1280, 764]);
    assert.deepEqual(region.applied, []);
});

test('a docked chat on either side takes its width off that side', () => {
    const right = visibleRegion(VIEW, BAR, [{ x: 960, y: 36, w: 320, h: 764, kind: 'panel', name: 'chat' }]);
    assert.deepEqual([right.x, right.w], [0, 960]);
    const left = visibleRegion(VIEW, BAR, [{ x: 0, y: 36, w: 640, h: 764, kind: 'panel', name: 'chat' }]);
    assert.deepEqual([left.x, left.w], [640, 640]);
});

test('a short tray hugging the edge counts; a window of the same height does not', () => {
    const tray = { x: 944, y: 400, w: 320, h: 300 };      // 16 px off the edge, 300 tall: the task tray
    assert.equal(visibleRegion(VIEW, BAR, [{ ...tray, kind: 'panel' }]).w, 944);
    assert.equal(visibleRegion(VIEW, BAR, [{ ...tray, kind: 'window' }]).w, 1280);
});

test('a tall window beside the avatar is avoided on the side it sits on', () => {
    const snapped = { x: 0, y: 36, w: 640, h: 700, kind: 'window', name: 'workspace' };
    const right = visibleRegion(VIEW, BAR, [snapped]);
    assert.deepEqual([right.x, right.w], [640, 640]);
    const mirrored = visibleRegion(VIEW, BAR, [{ ...snapped, x: 640 }]);
    assert.deepEqual([mirrored.x, mirrored.w], [0, 640]);
});

test('a window in the middle of the desktop, or covering all of it, changes nothing', () => {
    const middle = { x: 340, y: 100, w: 600, h: 600, kind: 'window' };
    assert.equal(visibleRegion(VIEW, BAR, [middle]).w, 1280);
    const full = { x: 0, y: 36, w: 1280, h: 764, kind: 'window' };
    assert.equal(visibleRegion(VIEW, BAR, [full]).w, 1280);
});

test('panels on both sides leave the middle', () => {
    const region = visibleRegion(VIEW, BAR, [
        { x: 0, y: 36, w: 300, h: 764, kind: 'panel' }, { x: 900, y: 36, w: 380, h: 764, kind: 'panel' }]);
    assert.deepEqual([region.x, region.w], [300, 600]);
});

test('a wide, short piece at the bottom or the top cuts that edge', () => {
    const bottom = visibleRegion(VIEW, BAR, [{ x: 0, y: 640, w: 1280, h: 160, kind: 'panel' }]);
    assert.deepEqual([bottom.y, bottom.h], [36, 604]);
    const top = visibleRegion(VIEW, BAR, [{ x: 0, y: 36, w: 1280, h: 120, kind: 'panel' }]);
    assert.deepEqual([top.y, top.h], [156, 644]);
});

test('a cut that would leave the avatar too little room is skipped', () => {
    const region = visibleRegion(VIEW, BAR, [{ x: 0, y: 36, w: 1100, h: 764, kind: 'window' }]);
    assert.equal(region.w, 1280);
    const panelFirst = visibleRegion(VIEW, BAR, [
        { x: 0, y: 36, w: 600, h: 764, kind: 'window' }, { x: 900, y: 36, w: 380, h: 764, kind: 'panel' }]);
    assert.deepEqual([panelFirst.x, panelFirst.w], [0, 900], 'the panel is honoured first; the window would leave 300 px');
});

test('pieces outside the desktop, or not numbers, are ignored', () => {
    const region = visibleRegion(VIEW, BAR, [
        { x: 1400, y: 0, w: 300, h: 800, kind: 'panel' }, { x: NaN, y: 0, w: 10, h: 10 }, null, { x: 0, y: 0, w: 0, h: 500, kind: 'panel' }]);
    assert.equal(region.w, 1280);
});

test('the region always lies inside the desktop', () => {
    const pieces = [];
    for (let i = 0; i < 40; i++) pieces.push({ x: (i * 97) % 1280, y: (i * 53) % 800, w: 80 + (i * 131) % 700, h: 60 + (i * 71) % 760, kind: i % 2 ? 'panel' : 'window' });
    for (let n = 0; n < pieces.length; n++) {
        const r = visibleRegion(VIEW, BAR, pieces.slice(0, n));
        assert.ok(r.x >= 0 && r.y >= 36 && r.x + r.w <= 1280.0001 && r.y + r.h <= 800.0001 && r.w >= 1280 * .3 - 1e-6 && r.h >= 800 * .35 - 1e-6, JSON.stringify(r));
    }
});

test('the fit keeps full size while three quarters of the desktop remains, then shrinks to a floor', () => {
    const full = regionFit({ x: 0, y: 36, w: 1280, h: 764 }, VIEW);
    assert.equal(full.scale, 1); near(full.dy, 18, 1e-9);
    const third = regionFit({ x: 0, y: 36, w: 960, h: 764 }, VIEW);
    assert.equal(third.scale, 1); near(third.dx, -160, 1e-9);
    const half = regionFit({ x: 640, y: 36, w: 640, h: 764 }, VIEW);
    near(half.scale, 640 / 1280 / .75, 1e-9); near(half.dx, 320, 1e-9);
    assert.equal(regionFit({ x: 0, y: 36, w: 400, h: 764 }, VIEW).scale, .6);
});

test('the projection offset puts the focal point at the middle of the region', () => {
    const camera = fakeCamera();
    const region = { x: 640, y: 36, w: 640, h: 764 };
    const fit = regionFit(region, VIEW);
    offsetProjection(camera, fit.scale, fit.dx, fit.dy, VIEW.w, VIEW.h);
    const point = project(camera, [0, 0, 0]);
    near(point.x, region.x + region.w / 2, .01); near(point.y, region.y + region.h / 2, .01);
});

// ── the spring ───────────────────────────────────────────────────────────────

const rect = (x, w = 400) => ({ x, y: 36, w, h: 700 });
function run(spring, seconds, fps, omega) {
    const samples = [], dt = 1 / fps;
    for (let t = 0; t < seconds - 1e-9; t += dt) { spring.step(dt, omega); samples.push({ t: t + dt, x: spring.current.x, w: spring.current.w }); }
    return samples;
}

test('a target the spring starts on does not move', () => {
    const spring = createRectSpring();
    spring.retarget(rect(100)); assert.equal(spring.active, false);
    spring.step(1 / 60); assert.equal(spring.current.x, 100);
});

test('the spring reaches its target in about 0.4 s without passing it, at any frame rate', () => {
    for (const fps of [24, 30, 60, 144]) {
        const spring = createRectSpring();
        spring.retarget(rect(0)); spring.retarget(rect(600, 700));
        const samples = run(spring, 1, fps);
        assert.ok(samples.every(s => s.x <= 600 + 1e-6 && s.w <= 700 + 1e-6), `no overshoot at ${fps} fps`);
        assert.ok(samples.every((s, i) => !i || s.x >= samples[i - 1].x - 1e-9), `monotone at ${fps} fps`);
        const at = seconds => samples.find(s => s.t >= seconds - 1e-9).x;
        assert.ok(at(.2) > 150 && at(.2) < 560, `part way at 0.2 s (${fps} fps): ${at(.2)}`);
        near(at(.4), 600, 600 * .03, `0.4 s at ${fps} fps`);
        assert.equal(spring.current.x, 600); assert.equal(spring.active, false);
    }
});

test('the path does not depend on the frame rate', () => {
    const at = fps => { const s = createRectSpring(); s.retarget(rect(0)); s.retarget(rect(500)); return run(s, .2, fps).pop().x; };
    near(at(30), at(120), .01);
    near(at(60), at(120), .01);
});

test('a target that moves mid-flight bends the path: no jump in position or speed', () => {
    const spring = createRectSpring();
    spring.retarget(rect(0)); spring.retarget(rect(600));
    const dt = 1 / 60, xs = [];
    for (let i = 0; i < 12; i++) { spring.step(dt); xs.push(spring.current.x); }
    const before = xs[xs.length - 1] - xs[xs.length - 2];
    spring.retarget(rect(100));                       // the owner changed their mind
    const first = spring.current.x;
    assert.equal(first, xs[xs.length - 1], 'the displayed frame does not move at the retarget');
    spring.step(dt);
    const after = spring.current.x - first;
    assert.ok(after > 0 && Math.abs(after) <= before * 1.02, `speed carries on and bends: ${before} then ${after}`);
    const rest = []; for (let i = 0; i < 120; i++) { spring.step(dt); rest.push(spring.current.x); }
    assert.equal(spring.current.x, 100);
    const steps = rest.map((x, i) => x - (i ? rest[i - 1] : first));
    assert.ok(after > 0 && after < before, 'the motion carries forward for a frame, slowing, before it turns');
    // A critically damped spring changes its per-frame step by at most (w^2 * distance + 2w * speed) * dt^2.
    const bound = (FRAME_OMEGA ** 2 * 600 + 2 * FRAME_OMEGA * 1500) * dt * dt;
    const jerk = steps.map((v, i) => Math.abs(v - (i ? steps[i - 1] : after)));
    assert.ok(Math.max(...jerk) <= bound, `speed changes smoothly (${Math.max(...jerk)} of ${bound} px per frame)`);
});

test('a gentler spring has a lower peak speed and still arrives', () => {
    const peak = omega => { const s = createRectSpring(); s.retarget(rect(0)); s.retarget(rect(600)); const xs = run(s, 1.5, 60, omega).map(v => v.x); return { top: Math.max(...xs.map((x, i) => i ? x - xs[i - 1] : x)), end: s.current.x }; };
    const normal = peak(FRAME_OMEGA), gentle = peak(FRAME_OMEGA_REDUCED);
    assert.ok(gentle.top < normal.top * .8, `${gentle.top} vs ${normal.top}`);
    assert.equal(gentle.end, 600);
});

test('immediate retargets and settle land exactly; a null target clears it', () => {
    const spring = createRectSpring();
    spring.retarget(rect(0)); spring.retarget(rect(300), true);
    assert.equal(spring.current.x, 300); assert.equal(spring.active, false);
    spring.retarget(rect(900)); spring.step(1 / 60); assert.ok(spring.active);
    spring.settle(); assert.equal(spring.current.x, 900);
    assert.equal(spring.retarget(null), null); assert.equal(spring.current, null);
});

test('a huge frame gap cannot throw the frame past its target', () => {
    const spring = createRectSpring();
    spring.retarget(rect(0)); spring.retarget(rect(600));
    spring.step(30);
    assert.ok(spring.current.x <= 600 && spring.current.x > 500, 'one stalled frame is one capped step, never a leap past the target');
    for (let i = 0; i < 120; i++) spring.step(1 / 60);
    assert.equal(spring.current.x, 600);
});

// ── the cards closed ─────────────────────────────────────────────────────────

const AREA = { x: 16, y: 60, w: 600, h: 700 };      // what is left beside a half-width chat on a 1280 desktop
test('closed cards give the avatar the whole work area and the cards a hairline', () => {
    const open = spatialLayout(AREA, { enabled: true, viewportWidth: 1280, arrangement: 'companion', rightChat: true });
    const closed = spatialLayout({ ...AREA, w: 1200 }, { enabled: true, hideCards: true, viewportWidth: 1920, arrangement: 'companion', rightChat: true });
    assert.equal(open.cards, undefined);
    assert.equal(closed.cards, 'hidden'); assert.equal(closed.layout, 'wide');
    assert.deepEqual(closed.stage, { x: 16, y: 60, w: 1199, h: 700 });
    assert.deepEqual(closed.content, { x: 1215, y: 60, w: 1, h: 700 });
    assert.deepEqual(impl.separatingSides(closed), ['left']);
});

test('closed cards leave compact, short and narrow layouts as they were', () => {
    for (const options of [{ compact: true, viewportWidth: 1920 }, { shortLandscape: true, viewportWidth: 1920 }, { viewportWidth: 900 }]) {
        const layout = spatialLayout({ x: 16, y: 60, w: 800, h: 600 }, { enabled: true, hideCards: true, arrangement: 'companion', ...options });
        assert.equal(layout.cards, undefined, JSON.stringify(options));
    }
    // A work area beside a half-width chat on a laptop is narrower than the cards need; the avatar still takes it.
    assert.equal(spatialLayout({ x: 16, y: 60, w: 608, h: 600 }, { enabled: true, hideCards: true, viewportWidth: 1280, arrangement: 'companion', rightChat: true }).cards, 'hidden');
});

// ── the mounted controller, frame by frame ───────────────────────────────────

function fakeCamera() {
    const near_ = .1, far = 1000, f = 1 / Math.tan(25 * Math.PI / 180), aspect = VIEW.w / VIEW.h;
    const base = () => { const e = new Float64Array(16); e[0] = f / aspect; e[5] = f; e[10] = (far + near_) / (near_ - far); e[11] = -1; e[14] = 2 * far * near_ / (near_ - far); return e; };
    const view = new Float64Array(16); view[0] = view[5] = view[10] = view[15] = 1; view[14] = -20;
    const camera = {
        fov: 50, aspect, near: near_, far, position: { x: 0, y: 0, z: 20 },
        projectionMatrix: { elements: base() }, matrixWorldInverse: { elements: view },
        projectionMatrixInverse: { copy() { return this; }, invert() { return this; } },
        reset() { this.projectionMatrix.elements.set(base()); }
    };
    return camera;
}
function project(camera, [x, y, z]) {
    const m = camera.matrixWorldInverse.elements, p = camera.projectionMatrix.elements;
    const vx = m[0] * x + m[4] * y + m[8] * z + m[12], vy = m[1] * x + m[5] * y + m[9] * z + m[13], vz = m[2] * x + m[6] * y + m[10] * z + m[14];
    const cx = p[0] * vx + p[4] * vy + p[8] * vz + p[12], cy = p[1] * vx + p[5] * vy + p[9] * vz + p[13], cw = p[3] * vx + p[7] * vy + p[11] * vz + p[15];
    return { x: (cx / cw + 1) * VIEW.w / 2, y: (1 - cy / cw) * VIEW.h / 2 };
}

const SIMPLE_QUERY = /\.([\w-]+)|:not\(\.([\w-]+)\)|\[hidden\]/g;
class El {
    constructor(tag, classes = '') {
        this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.listeners = {}; this.attrs = {};
        this.classList = new Set(classes.split(/\s+/).filter(Boolean)); this.dataset = {}; this.box = null; this.scrollHeight = 0; this.isConnected = false;
        const props = new Map();
        this.style = { setProperty: (k, v) => props.set(k, String(v)), getPropertyValue: k => props.get(k) || '', removeProperty: k => props.delete(k), props };
        const cl = this.classList; cl.contains = cl.has.bind(cl); cl.add = Set.prototype.add.bind(cl); cl.toggle = (n, on) => (on ? cl.add(n) : cl.delete(n));
    }
    set className(v) { this.classList.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => this.classList.add(c)); }
    get className() { return Array.from(this.classList).join(' '); }
    setAttribute(k, v) { this.attrs[k] = String(v); } getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    hasAttribute(k) { return k in this.attrs; } removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    removeEventListener() {}
    click() { (this.listeners.click || []).forEach(fn => fn({ target: this, isTrusted: true })); }
    appendChild(child) { child.parentNode = this; this.children.push(child); mark(child, this.isConnected); return child; }
    insertBefore(child, ref) { child.parentNode = this; this.children.splice(Math.max(0, this.children.indexOf(ref)), 0, child); mark(child, this.isConnected); return child; }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); this.parentNode = null; mark(this, false); }
    matches(selector) {
        return selector.split(',').some(part => {
            part = part.trim();
            const want = [], not = []; let hidden = false;
            part.replace(SIMPLE_QUERY, (m, c, n) => { if (m === '[hidden]') hidden = true; else if (n) not.push(n); else want.push(c); return m; });
            if (!want.length && !hidden) return false;
            return want.every(c => this.classList.has(c)) && !not.some(c => this.classList.has(c)) && (!hidden || 'hidden' in this.attrs);
        });
    }
    closest(selector) { for (let e = this; e; e = e.parentNode) if (e.matches && e.matches(selector)) return e; return null; }
    walk(out = []) { for (const c of this.children) { out.push(c); c.walk(out); } return out; }
    querySelectorAll(selector) { return this.walk().filter(e => e.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    getBoundingClientRect() {
        const b = this.box || { left: 0, top: 0, width: 0, height: 0 };
        return { ...b, right: b.left + b.width, bottom: b.top + b.height, x: b.left, y: b.top };
    }
}
function mark(el, on) { el.isConnected = on; el.children.forEach(c => mark(c, on)); }

function makeWindow({ style = 'classic', width = VIEW.w, height = VIEW.h, reduced = false, storage = {} } = {}) {
    const listeners = {}, queue = []; let now = 1000, nextId = 1;
    const root = new El('html'), body = new El('body'), bar = new El('div', 'top-bar'), dock = new El('div', 'dock');
    root.isConnected = true; mark(body, true); root.appendChild(body); body.parentNode = root; root.isConnected = true;
    bar.box = { left: 0, top: 0, width, height: 36 }; dock.box = { left: 400, top: height - 80, width: 480, height: 80 };
    body.appendChild(bar); body.appendChild(dock);
    const uiRoot = new El('div'); uiRoot.attrs.id = 'ui-root'; body.appendChild(uiRoot);
    const doc = {
        documentElement: root, body, hidden: false, readyState: 'complete',
        createElement: tag => new El(tag), createComment: () => new El('#comment'),
        getElementById: id => id === 'ui-root' ? uiRoot : null,
        querySelector: s => body.querySelector(s), querySelectorAll: s => body.querySelectorAll(s),
        addEventListener() {}, removeEventListener() {}
    };
    const win = {
        document: doc, innerWidth: width, innerHeight: height, listeners,
        localStorage: { getItem: k => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); } },
        matchMedia: () => ({ matches: reduced, addEventListener() {} }),
        performance: { now: () => now }, CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
        requestAnimationFrame: fn => { queue.push([nextId, fn]); return nextId++; },
        cancelAnimationFrame: id => { const i = queue.findIndex(q => q[0] === id); if (i >= 0) queue.splice(i, 1); },
        setInterval: () => 1, clearInterval() {}, queueMicrotask() {},
        MutationObserver: class { observe() {} disconnect() {} }, ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
        getComputedStyle: el => ({
            display: el.style.getPropertyValue('display') || 'block', visibility: el.style.getPropertyValue('visibility') || 'visible',
            zIndex: '0', getPropertyValue: k => el.style.getPropertyValue(k)
        }),
        addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter(f => f !== fn); },
        dispatchEvent(event) { (listeners[event.type] || []).slice().forEach(fn => fn(event)); return true; },
        FridayDisplayStyle: { get: () => style, set(v) { style = v; win.dispatchEvent(new win.CustomEvent('friday:display-style', { detail: { style } })); } },
        THREE: { Vector3: class { constructor() { this.v = [0, 0, 0]; } copy(o) { this.v = o.v ? o.v.slice() : [o.x, o.y, o.z]; return this; }
            project(camera) { const p = project(camera, this.v); this.x = p.x / VIEW.w * 2 - 1; this.y = 1 - p.y / VIEW.h * 2; return this; } } }
    };
    // The page's own desktop-area function, reduced to what the controller reads.
    win.fridayDesktopArea = () => {
        const dockRight = parseFloat(root.style.getPropertyValue('--fr-chat-dock')) || 0, dockLeft = parseFloat(root.style.getPropertyValue('--fr-chat-dock-left')) || 0;
        const area = { x: dockLeft, y: 36, w: win.innerWidth - dockLeft - dockRight, h: win.innerHeight - 36 - 80 };
        if (body.classList.has('friday-experience-desktop')) { area.x += 16; area.y += 12; area.w -= 32; area.h -= 24; }
        return win.FridayHolographicWorkspace && win.FridayHolographicWorkspace.workspaceArea ? win.FridayHolographicWorkspace.workspaceArea(area) : area;
    };
    // The app's side of the chat tray: opens it, sizes it, publishes the dock.
    // A closed tray keeps its width and waits past the edge, as the page's does.
    win.chat = new El('div', 'chat-panel'); win.chat.dataset.width = '320'; body.appendChild(win.chat);
    const placeChat = () => {
        const open = win.chat.classList.has('open'), w = Number(win.chat.dataset.width);
        win.chat.box = { left: open ? win.innerWidth - w : win.innerWidth + 40, top: 36, width: w, height: win.innerHeight - 36 };
        root.style.setProperty('--fr-chat-dock', (open ? w : 0) + 'px'); root.style.setProperty('--fr-chat-dock-left', '0px');
        win.dispatchEvent(new win.CustomEvent('friday:chat-dock'));
    };
    placeChat();
    win.addEventListener('friday:chat-tray', event => {
        const d = event.detail || {}, sizes = { third: 1 / 3, half: .5, two_thirds: 2 / 3 };
        const frac = sizes[d.size] || d.frac || 0;
        if (frac) win.chat.dataset.width = String(Math.round(win.innerWidth * frac));
        if (d.width) win.chat.dataset.width = String(d.width);
        if (typeof d.visible === 'boolean') { win.chat.classList.toggle('open', d.visible); }
        else if (frac) win.chat.classList.toggle('open', true);
        placeChat();
    });
    win.clock = { advance(ms) { now += ms; const run = queue.splice(0); run.forEach(([, fn]) => fn(now)); } };
    return { win, doc, root, body };
}

function boot(options = {}) {
    const env = makeWindow(options), { win, body } = env;
    if (options.style !== 'classic') { body.classList.add('friday-experience-enabled'); body.classList.add('friday-experience-desktop'); body.dataset.fridayHomeVisible = 'true'; }
    const api = impl.mount(win); win.FridayHolographicWorkspace = api;
    // The page publishes the API after mounting; its next layout event (the app mounting) is the first that sees it.
    win.dispatchEvent(new win.CustomEvent('friday:surface-changed'));
    const camera = fakeCamera();
    const sceneState = { camera, scene: null, basePosition: { x: 0, y: 0, z: 20 }, targetLook: { x: 0, y: 0, z: 0 }, structures: {}, keys: [], delta: 1 / 60, rootBreath: 1, headZoom: 1, glassAt: 1 };
    function frame(fps = 60) {
        const dt = 1 / fps;
        win.clock.advance(dt * 1000);
        camera.reset(); sceneState.delta = dt;
        api.fitSceneCamera(sceneState); api.projectSceneStage(camera);
        return { ...api.avatarFrame, frame: camera };
    }
    function settle(limit = 240) { let last; for (let i = 0; i < limit; i++) { last = frame(); if (!last.moving) break; } return last; }
    return { ...env, api, camera, frame, settle, sceneState };
}

const centre = r => ({ x: r.x + r.w / 2, y: r.y + r.h / 2 });

test('Classic: nothing open, the focal point sits in the middle of the desktop below the bar', () => {
    const page = boot({ style: 'classic' });
    const last = page.settle();
    assert.equal(last.mode, 'classic');
    near(last.focus.x, 640, .01); near(last.focus.y, 36 + 764 / 2, .01);
    assert.equal(last.scale, 1);
});

test('Classic: a docked chat moves the focal point into what is left, eased and settled', () => {
    const page = boot({ style: 'classic' });
    page.settle();
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'half' } }));
    const xs = [];
    for (let i = 0; i < 90; i++) { const f = page.frame(); xs.push(f.focus.x); if (!f.moving) break; }
    assert.ok(xs.length > 12 && xs.length < 70, `eased over about ${xs.length} frames`);
    assert.ok(xs.every((x, i) => !i || x <= xs[i - 1] + 1e-9), 'moves one way');
    assert.ok(xs[0] > 560, `the first frame has barely moved: ${xs[0]}`);
    const last = page.settle();
    near(last.focus.x, 320, .01); near(last.focus.y, 418, .01);
    near(last.scale, .5 / .75, 1e-9);
    assert.deepEqual(last.applied.map(a => a.side), ['right']);
});

test('Classic: closing the chat again returns the focal point to the middle', () => {
    const page = boot({ style: 'classic' });
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'third' } }));
    page.settle();
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: false } }));
    const last = page.settle();
    near(last.focus.x, 640, .01); assert.equal(last.scale, 1);
});

test('Classic: a layout change in mid-flight bends the move, with no jump', () => {
    const page = boot({ style: 'classic' });
    page.settle();
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'half' } }));
    const xs = [];
    for (let i = 0; i < 8; i++) xs.push(page.frame().focus.x);
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'third' } }));
    for (let i = 0; i < 90; i++) { const f = page.frame(); xs.push(f.focus.x); if (!f.moving) break; }
    const steps = xs.map((x, i) => i ? Math.abs(x - xs[i - 1]) : 0);
    assert.ok(Math.max(...steps) < 45, `largest single-frame step ${Math.max(...steps)}`);
    near(xs[xs.length - 1], (1280 - 427) / 2, 1.5);
});

test('Classic: a task tray hugging the right edge, and a snapped workspace, each move the focal point', () => {
    const page = boot({ style: 'classic' });
    const tray = new El('div', 'task-tray'); tray.box = { left: 944, top: 400, width: 320, height: 300 };
    page.body.appendChild(tray);
    page.win.dispatchEvent(new page.win.CustomEvent('friday:snapped', { detail: {} }));
    page.win.clock.advance(20);
    let last = page.settle();
    near(last.focus.x, 472, .01, 'centred in the 944 px left of the tray');
    tray.remove();
    const left = new El('div', 'fwin'); left.box = { left: 0, top: 36, width: 640, height: 700 };
    page.body.appendChild(left);
    page.win.dispatchEvent(new page.win.CustomEvent('friday:snapped', { detail: {} }));
    last = page.settle();
    near(last.focus.x, 960, .01, 'centred in the half the workspace leaves');
    assert.deepEqual(last.applied.map(a => a.side), ['left']);
});

test('Classic: the offset applies to the Window view too, whatever the head does to the frustum', () => {
    const page = boot({ style: 'classic' });
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'third' } }));
    page.settle();
    // An off-axis frustum (the head moved right): the shift still lands where the region says, plus the head's own parallax.
    const e = page.camera.projectionMatrix.elements;
    page.camera.reset();
    const base = page.camera.projectionMatrix.elements.slice();
    base[8] += .1;
    page.camera.reset = function () { this.projectionMatrix.elements.set(base); };
    const last = page.frame();
    assert.ok(last.focus.x < 640 - 100 && last.focus.x > 100);
    assert.ok(e);
});

test('Classic: a tray wider than the width the app published (big mode) is avoided at its real width', () => {
    const page = boot({ style: 'classic' });
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'third' } }));
    page.settle();
    page.win.chat.box = { left: 1280 - 600, top: 36, width: 600, height: 764 };
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-dock'));
    const last = page.settle();
    near(last.focus.x, 340, .01);
});

test('Classic: the dock hiding and the bar hiding are not panels; only the bar moves the region', () => {
    const page = boot({ style: 'classic' });
    const before = page.settle();
    page.body.querySelector('.dock').classList.add('hidden');
    page.body.querySelector('.dock').box = { left: 400, top: 800, width: 480, height: 0 };
    page.win.dispatchEvent(new page.win.CustomEvent('friday:surface-changed'));
    near(page.settle().focus.y, before.focus.y, .01, 'the dock does not move the avatar');
    page.body.querySelector('.top-bar').box = { left: 0, top: -36, width: 1280, height: 36 };
    page.win.dispatchEvent(new page.win.CustomEvent('friday:surface-changed'));
    near(page.settle().focus.y, 400, .01, 'a bar that has slid away leaves the whole desktop');
});

test('Classic: switching to Simple clears the Classic frame', () => {
    const page = boot({ style: 'classic' });
    page.settle();
    page.body.classList.add('friday-experience-enabled'); page.body.classList.add('friday-experience-desktop'); page.body.dataset.fridayHomeVisible = 'true';
    page.win.FridayDisplayStyle.set('simple');
    const last = page.settle();
    assert.equal(last.mode, 'simple');
    assert.equal(last.focus, undefined);
});

test('Classic: reduced motion still moves, with a lower peak speed', () => {
    const peak = reduced => {
        const page = boot({ style: 'classic', reduced });
        page.settle();
        page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'half' } }));
        const xs = [page.frame().focus.x];
        for (let i = 0; i < 200; i++) { const f = page.frame(); xs.push(f.focus.x); if (!f.moving) break; }
        return { top: Math.max(...xs.map((x, i) => i ? Math.abs(x - xs[i - 1]) : 0)), frames: xs.length, end: xs[xs.length - 1] };
    };
    const normal = peak(false), gentle = peak(true);
    assert.ok(gentle.frames > 3 && gentle.top < normal.top * .8, JSON.stringify({ normal, gentle }));
    near(gentle.end, normal.end, .01);
});

test('Classic: a resize snaps rather than easing across the old size', () => {
    const page = boot({ style: 'classic' });
    page.settle();
    page.win.innerWidth = 1000; page.win.innerHeight = 700;
    page.win.dispatchEvent(new page.win.CustomEvent('resize'));
    page.win.clock.advance(20);
    const f = page.frame();
    near(f.shown.w, 1000, .01); assert.equal(f.moving, false);
});

test('Simple: the chat opening eases the stage the avatar is drawn into instead of jumping it', () => {
    const page = boot({ style: 'simple', width: 1920, height: 1000 });
    const before = page.settle();
    assert.equal(before.mode, 'simple');
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'half' } }));
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-dock'));
    const stages = [];
    for (let i = 0; i < 120; i++) { const f = page.frame(); stages.push({ ...f.shown }); if (!f.moving) break; }
    const target = page.api.stageRect;
    assert.ok(stages.length > 10, `eased over ${stages.length} frames`);
    near(stages[0].x, before.shown.x, 80, 'the first frame is still near where it was');
    near(stages[stages.length - 1].x, target.x, .01); near(stages[stages.length - 1].w, target.w, .01);
    const widths = stages.map(s => s.w);
    assert.ok(widths.every((w, i) => !i || w <= widths[i - 1] + 1e-9), 'the stage narrows without bouncing');
});

test('Simple: while the layout\'s own motion runs, the displayed stage follows it exactly', () => {
    const page = boot({ style: 'simple' });
    page.settle();
    page.api.setArrangement('present');
    const f = page.frame();
    near(f.shown.x, page.api.stageRect.x, 1e-6, 'follows the layout motion');
    assert.equal(f.moving, false);
});

test('Simple: with arrangement motion off the stage jumps, as the owner asked', () => {
    const page = boot({ style: 'simple' });
    page.settle();
    page.api.setMotion('off');
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, size: 'half' } }));
    const f = page.frame();
    near(f.shown.w, page.api.stageRect.w, 1e-6); assert.equal(f.moving, false);
});

test('Cards: closing them opens the chat at half and gives the avatar the rest; opening puts both back', () => {
    const page = boot({ style: 'simple', width: 1920, height: 1000 });
    page.settle();
    const sent = []; page.win.addEventListener('friday:chat-tray', e => sent.push(e.detail));
    const normal = { ...page.api.stageRect };
    const result = page.api.setCards('hidden');
    assert.deepEqual([result.applied, result.cards], [true, 'hidden']);
    assert.deepEqual(sent[0], { visible: true, size: 'half' });
    page.win.clock.advance(20);
    const closed = page.settle();
    assert.equal(page.body.dataset.fridayCards, 'hidden');
    assert.equal(page.api.layoutRect.cards, 'hidden');
    // 1920 wide, chat 960, the avatar's area 960 - 2 * 16 gutter.
    near(page.api.stageRect.w, 960 - 32 - 1, 1.5);
    assert.ok(page.api.stageRect.w > normal.w);
    near(closed.shown.w, page.api.stageRect.w, .01);
    const shown = page.api.setCards('shown');
    assert.deepEqual([shown.applied, shown.cards], [true, 'shown']);
    assert.deepEqual(sent[sent.length - 1], { visible: false, width: 320 }, 'the chat was closed before, at its own width: it closes again at that width');
    assert.equal(page.win.chat.dataset.width, '320');
    page.win.clock.advance(20);
    page.settle();
    assert.equal(page.body.dataset.fridayCards, 'shown');
    near(page.api.stageRect.w, normal.w, 1.5);
});

test('Cards: a chat that was open at its own width comes back at that width', () => {
    const page = boot({ style: 'simple', width: 1920, height: 1000 });
    page.win.dispatchEvent(new page.win.CustomEvent('friday:chat-tray', { detail: { visible: true, frac: .25 } }));
    page.settle();
    const sent = []; page.win.addEventListener('friday:chat-tray', e => sent.push(e.detail));
    page.api.setCards('hidden');
    page.settle();
    page.api.setCards('shown');
    assert.deepEqual(sent[sent.length - 1], { visible: true, width: 480 });
    assert.equal(page.win.chat.dataset.width, '480');
});

test('Cards: Classic has none to close, and the choice is remembered', () => {
    const classic = boot({ style: 'classic' });
    const refused = classic.api.setCards('hidden');
    assert.deepEqual([refused.applied, refused.reason], [false, 'classic']);
    const storage = {};
    const page = boot({ style: 'simple', storage });
    page.api.setCards('hidden');
    assert.equal(JSON.parse(storage.friday_holographic_workspace_v1).cards, 'hidden');
    const again = boot({ style: 'simple', storage });
    assert.equal(again.api.state.cards, 'hidden');
    assert.equal(again.api.setCards('sideways').applied, false);
});

test('Cards: a workspace window brings the layout back while the choice stays', () => {
    const page = boot({ style: 'simple', width: 1920, height: 1000 });
    page.api.setCards('hidden'); page.settle();
    assert.equal(page.body.dataset.fridayCards, 'hidden');
    page.body.dataset.fridayHomeVisible = 'false';
    page.win.dispatchEvent(new page.win.CustomEvent('friday:surface-changed'));
    page.settle();
    assert.equal(page.body.dataset.fridayCards, 'shown');
    assert.equal(page.api.state.cards, 'hidden');
    page.body.dataset.fridayHomeVisible = 'true';
    page.win.dispatchEvent(new page.win.CustomEvent('friday:surface-changed'));
    page.settle();
    assert.equal(page.body.dataset.fridayCards, 'hidden');
});

test('Cards: the control is a labelled button that toggles, keeps its place and goes with Home', () => {
    const page = boot({ style: 'simple', width: 1920, height: 1000 });
    page.settle();
    const button = page.body.querySelector('.fr-cards-toggle');
    assert.ok(button, 'the control is on the desktop');
    assert.equal(button.tagName, 'BUTTON'); assert.equal(button.type, 'button');
    assert.equal(button.attrs['aria-label'], 'Hide the cards'); assert.equal(button.textContent, 'Hide cards');
    button.click(); page.settle();
    assert.equal(page.body.querySelector('.fr-cards-toggle'), button, 'the same element, so keyboard focus stays on it');
    assert.equal(button.attrs['aria-label'], 'Show the cards'); assert.equal(button.textContent, 'Show cards');
    button.click(); page.settle();
    assert.equal(button.attrs['aria-label'], 'Hide the cards');
    page.body.dataset.fridayHomeVisible = 'false';
    page.win.dispatchEvent(new page.win.CustomEvent('friday:surface-changed'));
    assert.equal(page.body.querySelector('.fr-cards-toggle'), null, 'no control where there are no cards');
});

test('destroying the controller removes what it added', () => {
    const page = boot({ style: 'simple', width: 1920, height: 1000 });
    page.api.setCards('hidden'); page.settle();
    page.api.destroy();
    assert.equal(page.body.querySelector('.fr-cards-toggle'), null);
    assert.equal(page.body.dataset.fridayCards, undefined);
    assert.equal(page.api.avatarFrame.mode, 'none');
});
