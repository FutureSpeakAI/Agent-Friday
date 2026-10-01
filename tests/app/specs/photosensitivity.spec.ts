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
                 transitionsPerSecond: number; swing: number; frames: number; wholeFrameRatio: number };

function explain(bad: Segment[]): string {
  return bad.map(s => `  ${s.name}: ${s.flashesPerSecond} flashes/s (${s.transitionsPerSecond} transitions), `
    + `${s.redFlashesPerSecond} red flashes/s, largest 100 ms lightness swing in a 10-degree field `
    + `${s.swing} L* (limit 6), over ${s.frames} frames`).join('\n');
}

/** Open the app with the given genome, the meter installed and the clock faked. */
async function openScene(page: Page, view: any, structureIndex = 0) {
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
  await page.clock.install();
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
  await page.clock.runFor(10_000);                      // past the load transition
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  // Friday's voice, the room the microphone hears, and the user's mic, all
  // from the page's own clock (scene_driver.js).
  await page.evaluate(DRIVER);
  // Sampled inside the scene's own render: see flash_meter.js.
  await page.evaluate(() => (window as any).__flashMeter.start(document.getElementById('friday-scene-canvas'), { drawnBy: composer }));
  return watcher;
}

const run = (page: Page, ms: number) => page.clock.runFor(Math.round(ms / FRAME) * FRAME);
const mark = (page: Page, name: string) => page.evaluate(n => (window as any).__flashMeter.mark(n), name);
const emit = (page: Page, d: any) => page.evaluate(x => (window as any).__emit(x), d);
const P = (state: string, phase: string, extra: any = {}) => ({ type: 'presence', state, phase, ...extra });

/** One structure through everything that moves it. */
async function exercise(page: Page, index: number) {
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
  await mark(page, `${id} listening`);
  await page.evaluate(() => { const w = window as any; w.__mic = 1; w.__room = 1; setSystemMood('LISTENING'); });
  await run(page, 3000);
  await page.evaluate(() => { const w = window as any; w.__mic = 0; w.__room = 0; setSystemMood('IDLE'); });
  await run(page, 800);
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
    for (let i = 0; i < n; i++) await exercise(page, i);
    const segs = await finish(page);
    expect(segs.length).toBeGreaterThanOrEqual(n * 5);
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
