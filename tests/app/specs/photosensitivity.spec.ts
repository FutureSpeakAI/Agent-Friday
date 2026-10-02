/**
 * Photosensitivity: the holographic scene never flashes (WCAG 2.3.1).
 *
 * Every structure is measured on real rendered frames by
 * tests/app/photosensitivity/flash_meter.js:
 *   - no more than three flashes in any one second, general or red;
 *   - within any 10-degree field, mean lightness (L*) moves by less than 6
 *     in any 100 ms: gentle, even in a dark scene.
 *
 * Each structure is measured through its arrival, at rest, while Friday
 * speaks (a syllable-like voice, in the speaking mood, the fastest motion
 * the scene has, with the microphone hearing it as it does live), through
 * every processing-state gesture, and while the user talks. That runs at v1, and again with an evolved genome (the
 * brightest, busiest corner of every gene; Giga Earth at its final form).
 * Giga Earth is also run at every form of its track, and on a live
 * evolution step received while it is on screen.
 *
 * Time is the page's own clock under Playwright's fake clock, so every frame
 * is 1/60 s of scene time. A slow renderer makes this suite slow; it never
 * changes what it measures. It needs WebGL: run it where the scene renders.
 *
 * FRIDAY_PAGE=<path to index.html> serves that page in place of the
 * server's, to check a branch before it is deployed.
 */
import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { BASE, openApp } from '../harness';

const DIR = path.join(__dirname, '..', 'photosensitivity');
const METER = path.join(DIR, 'flash_meter.js');
const DRIVER = fs.readFileSync(path.join(DIR, 'scene_driver.js'), 'utf8');
const GENOMES = JSON.parse(fs.readFileSync(path.join(DIR, 'genomes.json'), 'utf8'));
const FRAME = 1000 / 60;
// Giga Earth's place on the scene's evolution path (index.html EVOLUTION_PATH).
const EDEN_INDEX = 12;

// The page's top-level bindings (classic-script globals, not window properties).
declare const EVOLUTION_PATH: { id: string }[];
declare const FridayGestures: unknown;
declare function setEvolution(i: number): void;
declare function setSystemMood(m: string): void;
declare const composer: { render: (...a: unknown[]) => unknown };

// In order, one at a time (they share the GPU), and a failure never skips
// the tests after it.
test.describe.configure({ mode: 'default' });
test.use({
  viewport: { width: 1280, height: 800 },
  // A real GPU when there is one: a software renderer only makes this slower.
  launchOptions: { args: ['--use-gl=angle', '--ignore-gpu-blocklist'] },
});

type Segment = { name: string; ok: boolean; flashesPerSecond: number; redFlashesPerSecond: number;
                 transitionsPerSecond: number; swing: number; frames: number; wholeFrameRatio: number;
                 contextLost?: boolean };

function explain(bad: Segment[]): string {
  return bad.map(s => `  ${s.name}: ${s.flashesPerSecond} flashes/s (${s.transitionsPerSecond} transitions), `
    + `${s.redFlashesPerSecond} red flashes/s, largest 100 ms lightness swing in a 10-degree field `
    + `${s.swing} L* (limit 6), over ${s.frames} frames`
    + (s.contextLost ? ' (the GPU took the scene away during this segment)' : '')).join('\n');
}

/** Open the app with the given genome, the meter installed and the clock
 *  faked (or, with realClock, on the real clock, to time real frames). */
async function openScene(page: Page, view: any, structureIndex = 0, opts: { realClock?: boolean } = {}) {
  await page.addInitScript(() => {
    // The approvals stream is a stand-in the test feeds: real approvals on
    // the server must not move the scene during a measurement.
    (window as any).__fakeES = [];
    class FakeES {
      url: string; readyState = 1; onmessage: any; onopen: any;
      constructor(url: string) {
        this.url = url; (window as any).__fakeES.push(this);
        setTimeout(() => {
          if (this.onopen) this.onopen({});
          if (/approvals\/events/.test(url) && this.onmessage) this.onmessage({ data: JSON.stringify({ type: 'snapshot', pending: [] }) });
        }, 50);
      }
      close() { this.readyState = 2; }
      addEventListener(t: string, fn: any) { if (t === 'message') this.onmessage = fn; }
      removeEventListener() {}
    }
    (window as any).EventSource = FakeES;
    (window as any).__emit = (d: any) => (window as any).__fakeES.filter((e: any) => /approvals\/events/.test(e.url))
      .forEach((e: any) => e.onmessage && e.onmessage({ data: JSON.stringify(d) }));
  });
  await page.addInitScript({ path: METER });
  if (!opts.realClock) await page.clock.install();
  if (process.env.FRIDAY_PAGE) {
    const html = fs.readFileSync(process.env.FRIDAY_PAGE, 'utf8');
    await page.route(BASE.replace(/\/$/, '') + '/', async r => {
      const served = await (await r.fetch()).text();
      const token = (served.match(/<script>window\.__FRIDAY_API_TOKEN=[^<]*<\/script>/) || [''])[0];
      await r.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html.replace('<head>', '<head>\n' + token) });
    });
  }
  // Nothing the measurement does may change the user's state.
  await page.route('**/api/**', r => r.request().method() === 'GET' ? r.fallback()
    : r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await page.route('**/api/avatar/genome', r => r.fulfill({ json: view }));
  await page.route('**/api/approvals?**', r => r.fulfill({ json: { approvals: [] } }));
  await page.route('**/api/evolution', r => r.request().method() === 'GET'
    ? r.fulfill({ json: { day: 1, structure: 'DAY 1', structure_index: structureIndex, calendar_index: structureIndex,
        preferred_scene_index: structureIndex, first_launch: '2026-09-29' } })
    : r.fulfill({ json: { status: 'ok' } }));
  const watcher = await openApp(page, 90_000);
  await page.waitForFunction(() => (window as any).fridayVibe && typeof FridayGestures !== 'undefined');
  if (opts.realClock) { await page.waitForTimeout(10_000); await page.evaluate(DRIVER); return watcher; }
  await page.clock.runFor(10_000);                      // past the load transition
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  // Friday's voice, the room the microphone hears, and the user's mic, all
  // from the page's own clock (scene_driver.js).
  await page.evaluate(DRIVER);
  // Sampled inside the scene's own render: see flash_meter.js.
  await page.evaluate(() => (window as any).__flashMeter.start(document.getElementById('friday-scene-canvas'), { drawnBy: composer }));
  // When the GPU gives the scene back, the page rebuilds it (a new canvas, a
  // new composer): the meter follows, every time.
  await page.evaluate(() => {
    const follow = (c: any) => c && c.addEventListener('webglcontextrestored', () => setTimeout(() => {
      const next = document.getElementById('friday-scene-canvas');
      (window as any).__flashMeter.rehook(next, composer);
      follow(next);
    }, 0));
    follow(document.getElementById('friday-scene-canvas'));
  });
  return watcher;
}

const run = (page: Page, ms: number) => page.clock.runFor(Math.round(ms / FRAME) * FRAME);
const mark = (page: Page, name: string) => page.evaluate(n => (window as any).__flashMeter.mark(n), name);
const emit = (page: Page, d: any) => page.evaluate(x => (window as any).__emit(x), d);
const P = (state: string, phase: string, extra: any = {}) => ({ type: 'presence', agent: 'friday', state, phase, ...extra });

/** What the avatar showed while helpers worked under a flood of others' frames. */
type Flood = { status: string; twists: number; helpers: number };

/** Start ten helpers of Friday's, and a flood of frames from other agents
 *  (her helpers' own work, other models, background jobs): 100 a second. */
const floodStart = (page: Page) => page.evaluate(() => {
    const w = window as any;
    for (let i = 0; i < 10; i++) w.__emit({ type: 'presence', state: 'subagent', phase: 'start', ref: 'h' + i, agent: 'friday' });
    const others = ['helper:1', 'helper:2', 'salon:host', 'laya', 'needle', 'background:sched', 'model:other'];
    const kinds = [{ state: 'tool', phase: 'start' }, { state: 'round', phase: 'step', n: 2 }, { state: 'egress', phase: 'sent', route: 'cloud' },
                   { state: 'verify', phase: 'once', ok: true }, { state: 'error', phase: 'once' }, { state: 'retrieval', phase: 'once', n: 5 }];
    let i = 0;
    w.__flood = setInterval(() => { for (let k = 0; k < 5; k++, i++)
      w.__emit(Object.assign({ type: 'presence', agent: others[i % others.length], ref: 'x' + i }, kinds[i % kinds.length])); }, 50);
});
const floodStop = (page: Page) => page.evaluate(() => { const w = window as any; clearInterval(w.__flood);
  for (let i = 0; i < 10; i++) w.__emit({ type: 'presence', state: 'subagent', phase: 'end', ref: 'h' + i, agent: 'friday' }); });

/** The flood for `ms` of scene time, and what the avatar showed in the middle of it. */
async function flood(page: Page, ms: number): Promise<Flood> {
  await floodStart(page);
  await run(page, ms / 2);
  const seen: Flood = await page.evaluate(() => ({ status: (FridayGestures as any).status(),
    twists: (FridayGestures as any)._twists().length, helpers: (FridayGestures as any)._helpers() }));
  await run(page, ms / 2);
  await floodStop(page);
  return seen;
}

/** One structure through everything that moves it. */
async function exercise(page: Page, index: number): Promise<Flood> {
  const id = await page.evaluate(i => EVOLUTION_PATH[i].id, index);
  await mark(page, `${id} arriving`);
  await page.evaluate(i => setEvolution(i), index);
  await run(page, 4000);
  await mark(page, `${id} at rest`);
  await run(page, 3000);
  await mark(page, `${id} speaking`);
  await page.evaluate(() => { const w = window as any; w.__voice = 1; w.__room = 1; w._fridayMoodSignals = { ttsActive: true }; setSystemMood('SPEAKING'); });
  await run(page, 6000);
  await page.evaluate(() => { const w = window as any; w.__voice = 0; w.__room = 0; w._fridayMoodSignals = { ttsActive: false }; setSystemMood('IDLE'); });
  await run(page, 1500);
  await mark(page, `${id} gestures`);
  await emit(page, P('tool', 'start', { ref: 'c1' })); await run(page, 300);
  await emit(page, P('tool', 'start', { ref: 'c2' })); await run(page, 700);
  await emit(page, P('tool', 'end', { ref: 'c1', ok: true })); await emit(page, P('tool', 'end', { ref: 'c2', ok: true })); await run(page, 600);
  await emit(page, P('route', 'step', { route: 'local' })); await emit(page, P('round', 'step', { n: 1 })); await run(page, 800);
  await emit(page, P('round', 'step', { n: 2 })); await run(page, 800);
  await emit(page, P('round', 'step', { n: 3 })); await emit(page, P('egress', 'sent', { route: 'cloud' })); await run(page, 1200);
  await emit(page, { type: 'pending', approval: { approval_id: 'flash-1', status: 'pending', kind: 'email_send', title: 'x', created_at: 1 } });
  await run(page, 1500);
  await emit(page, { type: 'resolved', approval_id: 'flash-1', status: 'approved' }); await run(page, 800);
  await emit(page, P('verify', 'once', { ok: true })); await run(page, 1800);
  // A failed step: the block knocks and corrects (three times).
  await emit(page, P('tool', 'start', { ref: 'f1' })); await run(page, 600);
  await emit(page, P('tool', 'end', { ref: 'f1', ok: false })); await run(page, 1200);
  await emit(page, P('error', 'once')); await run(page, 1500);
  // Friday's other real events, and the user's own: a memory search, a reflex,
  // a saved memory, private local work, a tool that worked, typing, being
  // talked over.
  await emit(page, P('retrieval', 'once', { n: 4 })); await run(page, 900);
  await emit(page, P('reflex', 'once')); await run(page, 500);
  await emit(page, P('memory_saved', 'once')); await run(page, 900);
  await emit(page, P('handoff', 'start')); await run(page, 900);
  await emit(page, P('handoff', 'sent')); await run(page, 900);
  await emit(page, P('tool', 'start', { ref: 'w1' })); await run(page, 600);
  await emit(page, P('tool', 'end', { ref: 'w1', ok: true })); await run(page, 900);
  await page.evaluate(() => { for (let i = 0; i < 8; i++) setTimeout(() => (window as any).fridayAvatar.typed(), i * 90); });
  await run(page, 1500);
  await page.evaluate(() => (window as any).fridayAvatar.yielded()); await run(page, 1500);
  await mark(page, `${id} helpers and a flood of others' events`);
  const seen = await flood(page, 4000);
  await run(page, 1500);
  await mark(page, `${id} listening`);
  await page.evaluate(() => { const w = window as any; w.__mic = 1; w.__room = 1; setSystemMood('LISTENING'); });
  await run(page, 3000);
  await page.evaluate(() => { const w = window as any; w.__mic = 0; w.__room = 0; setSystemMood('IDLE'); });
  await run(page, 800);
  return seen;
}

async function finish(page: Page): Promise<Segment[]> {
  return page.evaluate(() => (window as any).__flashMeter.finish());
}

test('the meter itself: a fast full-screen flash fails, slow and small changes pass', async ({ page }) => {
  await page.clock.install();
  await page.setContent('<canvas id="c" width="640" height="400" style="width:640px;height:400px"></canvas>');
  await page.addScriptTag({ path: METER });      // init scripts do not run for setContent
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.evaluate(() => {
    const c = document.getElementById('c') as HTMLCanvasElement, g = c.getContext('2d')!;
    const w = window as any; w.__mode = 'dark';
    const draw = () => {
      requestAnimationFrame(draw);
      const t = performance.now() / 1000, m = w.__mode;
      g.fillStyle = '#000'; g.fillRect(0, 0, 640, 400);
      if (m === 'flash5' && Math.floor(t * 10) % 2) { g.fillStyle = '#fff'; g.fillRect(0, 0, 640, 400); }
      if (m === 'flash2' && Math.floor(t * 4) % 2) { g.fillStyle = '#fff'; g.fillRect(0, 0, 640, 400); }
      if (m === 'red5' && Math.floor(t * 10) % 2) { g.fillStyle = '#f00'; g.fillRect(0, 0, 640, 400); }
      if (m === 'spot5' && Math.floor(t * 10) % 2) { g.fillStyle = '#fff'; g.fillRect(300, 180, 20, 20); }
      // Up over 4 s and down over 4 s, never a jump.
      if (m === 'fade') { const v = Math.round(255 * 0.4 * (1 - Math.abs((t % 8) / 4 - 1))); g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(0, 0, 640, 400); }
      if (m === 'step' && t % 4 > 2) { g.fillStyle = '#999'; g.fillRect(0, 0, 640, 400); }
    };
    draw();
    w.__flashMeter.start(c);
  });
  const results: Record<string, Segment> = {};
  for (const m of ['dark', 'flash5', 'flash2', 'red5', 'spot5', 'fade', 'step']) {
    await page.evaluate(x => { (window as any).__mode = x; (window as any).__flashMeter.mark(x); }, m);
    await page.clock.runFor(4000);
  }
  for (const s of await page.evaluate(() => (window as any).__flashMeter.finish()) as Segment[]) results[s.name] = s;
  expect(results.dark.ok).toBe(true);
  expect(results.flash5.ok, 'a 5 Hz full-screen flash must fail').toBe(false);
  expect(results.flash5.flashesPerSecond).toBeGreaterThan(3);
  expect(results.flash2.flashesPerSecond).toBeLessThanOrEqual(3);
  expect(results.red5.ok, 'a 5 Hz red flash must fail').toBe(false);
  expect(results.spot5.ok, 'a small flickering spot is not a flash').toBe(true);
  expect(results.fade.ok, 'a slow fade is gentle').toBe(true);
  expect(results.step.ok, 'a sudden jump in brightness is not gentle').toBe(false);
});

for (const [label, key] of [['v1', 'v1'], ['an evolved genome', 'evolved']] as const) {
  test(`no structure flashes, at ${label}`, async ({ page }) => {
    test.setTimeout(45 * 60_000);
    const watcher = await openScene(page, GENOMES[key]);
    const n = await page.evaluate(() => EVOLUTION_PATH.length);
    const floods: Flood[] = [];
    for (let i = 0; i < n; i++) floods.push(await exercise(page, i));
    const segs = await finish(page);
    expect(segs.length).toBeGreaterThanOrEqual(n * 6);
    // Under the flood, only Friday's own state: her ten helpers as one, and
    // nothing of anyone else's work.
    for (const f of floods) expect(f).toEqual({ status: 'Waiting on 10 helpers', twists: 0, helpers: 10 });
    expect(segs.every(s => s.frames > 60), 'every segment rendered frames').toBe(true);
    const bad = segs.filter(s => !s.ok);
    expect(bad, `The scene flashed:\n${explain(bad)}`).toEqual([]);
    expect(watcher.errors.filter(e => /is not defined|is not a function|Cannot read/.test(e))).toEqual([]);
  });
}

test('Giga Earth does not flash at any form of its track, nor when a step lands', async ({ browser }) => {
  test.setTimeout(45 * 60_000);
  const segs: Segment[] = [];
  for (let stage = 0; stage <= 6; stage++) {
    const view = JSON.parse(JSON.stringify(GENOMES.v1));
    view.v1 = false; view.expression.EDEN = { stage };
    view.step = { content_hash: `sha256:flash-eden-${stage}`, name: `Giga Earth: form ${stage}` };
    // A context per form: Playwright's clock belongs to the context.
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const p = await ctx.newPage();
    await openScene(p, view, EDEN_INDEX);
    expect(await p.evaluate(i => EVOLUTION_PATH[i].id, EDEN_INDEX)).toBe('EDEN');
    await exercise(p, EDEN_INDEX);
    if (stage === 5) {
      // A step lands while Giga Earth is on screen: the new look arrives
      // through the genome loader, as a weekly step does.
      await mark(p, 'EDEN a step lands');
      await p.evaluate(v => (window as any).FridayGenome._receive(v, true), JSON.parse(JSON.stringify(GENOMES.evolved)));
      await run(p, 4000);
    }
    for (const s of await finish(p)) segs.push({ ...s, name: `form ${stage}: ${s.name}` });
    await ctx.close();
  }
  const bad = segs.filter(s => !s.ok);
  expect(bad, `Giga Earth flashed:\n${explain(bad)}`).toEqual([]);
});

test("the frame budget holds while ten helpers work under a flood of others' events", async ({ page }) => {
  // Real frames on the real clock: dropping others' frames and showing one
  // calm state for ten helpers must cost no more than the frame budget
  // (§6.4): p95 within 10% and 1 ms of the calm frames either side of it.
  test.setTimeout(15 * 60_000);
  await openScene(page, GENOMES.v1, 0, { realClock: true });
  const p95 = (ms: number) => page.evaluate(ms => new Promise<number>(res => {
    const t: number[] = []; let last = performance.now(); const end = last + ms;
    const f = () => { const x = performance.now(); t.push(x - last); last = x;
      if (x < end) requestAnimationFrame(f); else { t.sort((a, b) => a - b); res(t[Math.floor(t.length * 0.95)]); } };
    requestAnimationFrame(f); }), ms);
  const rows: string[] = [];
  for (const i of [0, 12, 7]) {                          // the lattice, Giga Earth, the Mandelbrot set
    await page.evaluate(i => setEvolution(i), i);
    await page.waitForTimeout(9000);
    const before = await p95(3000);
    await floodStart(page);
    const busy = await p95(3000);
    await floodStop(page);
    await page.waitForTimeout(2000);
    const after = await p95(3000);
    const calm = Math.max(before, after);
    rows.push(`structure ${i}: calm p95 ${calm.toFixed(1)} ms, flooded ${busy.toFixed(1)} ms`);
    expect(busy, rows.join(' | ')).toBeLessThanOrEqual(calm * 1.10 + 1);
  }
  console.log(rows.join(' | '));
});

test('the scene fades back in when the GPU gives it back', async ({ page }) => {
  // The GPU can take the scene away (a voice model loading fills it). The
  // canvas goes black at once; the browser does that, not the page. When the
  // GPU gives it back the page rebuilds the scene, and the rebuilt scene must
  // fade in from black, not appear in one frame.
  test.setTimeout(10 * 60_000);
  await openScene(page, GENOMES.v1, EDEN_INDEX);
  await mark(page, 'the GPU takes the scene away');
  await page.evaluate(() => {
    const c = document.getElementById('friday-scene-canvas') as HTMLCanvasElement;
    const gl = (c.getContext('webgl2') || c.getContext('webgl')) as WebGLRenderingContext;
    (window as any).__lose = gl.getExtension('WEBGL_lose_context');
    (window as any).__lose.loseContext();
  });
  await run(page, 1000);
  await mark(page, 'the GPU gives the scene back');
  await page.evaluate(() => (window as any).__lose.restoreContext());
  await run(page, 6000);
  const segs = await finish(page);
  const away = segs.find(x => x.name === 'the GPU takes the scene away')!;
  const back = segs.find(x => x.name === 'the GPU gives the scene back')!;
  expect(away.contextLost, 'the context was really lost').toBe(true);
  expect(back.frames, 'frames drawn after the scene came back').toBeGreaterThan(200);
  expect(back.ok, explain([back])).toBe(true);
});

test('a window resize keeps the picture', async ({ page }) => {
  // A resize gives the scene new buffers. The picture must carry over at the
  // new size, not drop to black and fade back in as a fresh scene does.
  test.setTimeout(10 * 60_000);
  await openScene(page, GENOMES.v1, EDEN_INDEX);
  await mark(page, 'the window is resized');
  await run(page, 500);
  await page.setViewportSize({ width: 1100, height: 680 });
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await run(page, 2000);
  const segs = await finish(page);
  const s = segs.find(x => x.name === 'the window is resized')!;
  expect(s.frames, 'frames drawn after the resize').toBeGreaterThan(60);
  expect(s.ok, explain([s])).toBe(true);
  // Dropping to black and fading back would take two seconds and halve the
  // frame's light from one frame to the next as it fell.
  expect(s.wholeFrameRatio, `the whole frame kept its light through the resize: ${JSON.stringify(s)}`).toBeLessThan(1.25);
});

/** The process orbs' own server rows, so nothing live wanders into a measurement. */
async function quietOrbRoutes(page: Page) {
  await page.route('**/api/processes**', r => r.fulfill({ json: { processes: [] } }));
  await page.route(/\/api\/tasks(\?.*)?$/, r => r.fulfill({ json: { tasks: [
    { task_id: 'tmail', status: 'running', seat_is_local: false, trace_id: 'tr-m' },
    { task_id: 'tpar', status: 'running', trace_id: 'tr-p' },
    { task_id: 'tkid', status: 'running', trace_id: 'tr-k', parent_trace_id: 'tr-p' } ] } }));
  await page.route(/\/api\/tasks\/[^/]+(\/digest)?$/, r => r.fulfill({ json: { status: 'completed', model: 'model-x', cost_usd: 0.01 } }));
}
/** One orb of every kind, as the orb layer adds them (avatar-visual-genome.md §16). */
const addAllKinds = (page: Page) => page.evaluate(() => {
  const w = window as any, add = (o: any) => w.fridayAddOrb(o);
  add({ id: 'agent-r', label: 'Research', category: 'default', research_commission_id: 'rc1', task_id: 'tres' });
  add({ id: 'image-m', label: 'Poster', category: 'creative' });
  add({ id: 'sched-s', label: 'Digest', category: 'monitoring' });
  add({ id: 'agent-mail', label: 'Inbox', category: 'communication', task_id: 'tmail' });
  add({ id: 'code-x', label: 'Tests', category: 'default', task_id: 'tcode' });
  add({ id: 'pull-y', name: 'Pulling Model', label: 'Model', category: 'default' });
  add({ id: 'agent-p', label: 'Parent', category: 'default', task_id: 'tpar' });
  add({ id: 'agent-k', label: 'Moon', category: 'default', task_id: 'tkid' });
});

test('the process orbs never flash: every kind, every state, the hand on them', async ({ page }) => {
  test.setTimeout(25 * 60_000);
  await quietOrbRoutes(page);
  const watcher = await openScene(page, GENOMES.v1, 0);
  for (const idx of [0, EDEN_INDEX, 6]) {                 // the lattice, Giga Earth, the Dirac cloud
    await page.evaluate(i => setEvolution(i), idx);
    await run(page, 5000);
    await mark(page, `orbs arrive (structure ${idx})`);
    await addAllKinds(page);
    await run(page, 3000);
    await mark(page, `orbs at work (structure ${idx})`);
    await page.evaluate(() => {
      const w = window as any;
      w.fridayUpdateOrb('agent-r', { progress: 0.7 }); w.fridayUpdateOrb('image-m', { progress: 0.3 });
      w.__sparks = setInterval(() => ['agent-r', 'agent-mail', 'code-x', 'agent-p'].forEach(id =>
        FridayOrbScene.frame({ type: 'presence', state: 'tool', phase: 'start', agent: id })), 120);
    });
    await run(page, 4000);
    await page.evaluate(() => clearInterval((window as any).__sparks));
    await mark(page, `the hand on the orbs (structure ${idx})`);
    // a drag across the screen, a throw and its undo, a status, a pause asked of a row that cannot
    for (let i = 0; i <= 60; i++) {
      await page.evaluate(k => { const s = FridayOrbScene._state('agent-r'); if (s) s.drag = { x: 200 + k * 14, y: 220 + 3 * k }; }, i);
      await run(page, FRAME);
    }
    await page.evaluate(() => { const s = FridayOrbScene._state('agent-r'); if (s) s.drag = null; });
    await page.evaluate(() => { FridayOrbHands.run({ op: 'cancel', target: 'the media one' }); });
    await run(page, 1500);
    await page.evaluate(() => { FridayOrbHands.run({ op: 'undo', target: 'it' }); FridayOrbHands.run({ op: 'status', target: 'the research one' });
                                FridayOrbHands.run({ op: 'pause', target: 'the scheduled one' }); });
    await run(page, 2500);
    await mark(page, `an orb needs the owner's OK (structure ${idx})`);
    await emit(page, { type: 'pending', approval: { approval_id: 'ap-orb', status: 'pending', kind: 'cloud_spill', title: 'Move to the cloud',
      subject_type: 'task', subject_id: 'tcode', created_at: Date.now() / 1000 } });
    await run(page, 4000);
    await emit(page, { type: 'resolved', approval_id: 'ap-orb', status: 'approved' });
    await run(page, 1500);
    await mark(page, `orbs finish, fail and leave (structure ${idx})`);
    await page.evaluate(() => {
      const w = window as any;
      for (const id of ['agent-r', 'image-m', 'agent-p', 'agent-k']) { w.fridayUpdateOrb(id, { status: 'completed' }); w.fridayRemoveOrb(id); }
      w.fridayUpdateOrb('pull-y', { status: 'error' }); w.fridayRemoveOrb('pull-y');
    });
    await run(page, 4000);
    await page.evaluate(() => { const w = window as any;
      for (const id of ['sched-s', 'agent-mail', 'code-x']) w.fridayRemoveOrb(id);
      FridayOrbHands.run({ op: 'open', target: 'the system one' }); });
    await run(page, 3000);
  }
  const segs = await finish(page);
  expect(segs.length).toBe(15);
  expect(segs.every(s => s.frames > 60), 'every segment rendered frames').toBe(true);
  const bad = segs.filter(s => !s.ok);
  expect(bad, `The orbs flashed:\n${explain(bad)}`).toEqual([]);
  expect(watcher.errors.filter(e => /is not defined|is not a function|Cannot read/.test(e))).toEqual([]);
  // nothing is left behind once every orb has gone (a failure goes when it is looked at)
  expect(await page.evaluate(() => (window as any).fridayGetOrbs().filter((o: any) => o.id !== 'helper-swarm').length)).toBe(0);
});

test('the frame budget holds with a full sky of busy orbs', async ({ page }) => {
  // Real frames on the real clock: eight orbs at work (forms, sparks,
  // labels, the keep-out) cost no more than the frame budget: p95 within 10%
  // and 1 ms of the calm frames either side.
  test.setTimeout(15 * 60_000);
  await quietOrbRoutes(page);
  await openScene(page, GENOMES.v1, 0, { realClock: true });
  const p95 = (ms: number) => page.evaluate(ms => new Promise<number>(res => {
    const t: number[] = []; let last = performance.now(); const end = last + ms;
    const f = () => { const x = performance.now(); t.push(x - last); last = x;
      if (x < end) requestAnimationFrame(f); else { t.sort((a, b) => a - b); res(t[Math.floor(t.length * 0.95)]); } };
    requestAnimationFrame(f); }), ms);
  const rows: string[] = [];
  for (const i of [0, EDEN_INDEX, 7]) {
    await page.evaluate(i => setEvolution(i), i);
    await page.waitForTimeout(9000);
    const before = await p95(3000);
    await addAllKinds(page);
    await page.evaluate(() => { (window as any).__sparks = setInterval(() => ['agent-r', 'agent-mail', 'code-x', 'agent-p'].forEach(id =>
      FridayOrbScene.frame({ type: 'presence', state: 'tool', phase: 'start', agent: id })), 120); });
    await page.waitForTimeout(1500);
    const busy = await p95(3000);
    await page.evaluate(() => { const w = window as any; clearInterval(w.__sparks);
      for (const o of w.fridayGetOrbs()) { w.fridayUpdateOrb(o.id, { status: 'completed' }); w.fridayRemoveOrb(o.id); } });
    await page.waitForTimeout(4000);
    const after = await p95(3000);
    const calm = Math.max(before, after);
    rows.push(`structure ${i}: calm p95 ${calm.toFixed(1)} ms, with orbs ${busy.toFixed(1)} ms`);
    expect(busy, rows.join(' | ')).toBeLessThanOrEqual(calm * 1.10 + 1);
  }
  console.log(rows.join(' | '));
});
