/**
 * Knowledge — the wiki's pages and the galaxy built from them, one workspace.
 *
 * The galaxy (KNOWLEDGE_SYSTEM_SPEC §9 Phase 3) mounts, renders its own
 * canvas, streams graph data and holds the fps floor. Headless software-GL is
 * the worst case — a real GPU renders far above it — so the floor here is 25;
 * the spec's 30fps budget was verified at 2,000 synthetic nodes (39–40 fps
 * headless).
 *
 * The two views are joined: clicking a page's node opens that page beside
 * the graph, and opening a page flies the graph to its node and lights its
 * links. The bottom bar stays inside the viewport at every window shape, and
 * the old Wiki tab address lands on Knowledge's Pages view.
 */
import { test, expect, Page } from '@playwright/test';

// SwiftShader (Playwright's default software GL) renders bloom at ~11fps
// regardless of scene cost; ANGLE reflects real deployment (Chrome on a
// GPU). The 2k-node budget was measured at 39-40fps under ANGLE.
test.use({ launchOptions: { args: ['--use-gl=angle'] } });

test.describe('Knowledge Graph API', () => {
  test('summary returns counts and local-only default', async ({ request }) => {
    const res = await request.get('/api/knowledge-graph/summary');
    expect(res.status()).toBe(200);
    const json = await res.json();
    expect(json.status).toBe('ok');
    expect(json.counts).toHaveProperty('entities');
    expect(json.settings.indexing_mode).toBe('local_only');
  });

  test('graph returns precomputed layout positions', async ({ request }) => {
    const res = await request.get('/api/knowledge-graph/graph');
    expect(res.status()).toBe(200);
    const json = await res.json();
    expect(json.status).toBe('ok');
    for (const e of json.entities.slice(0, 10)) {
      expect(typeof e.x).toBe('number');
      expect(typeof e.y).toBe('number');
      expect(typeof e.z).toBe('number');
    }
  });

  test('structural query answers without LLM', async ({ request }) => {
    const res = await request.post('/api/knowledge-graph/query', {
      data: { question: 'what do I know about friday?' },
    });
    expect(res.status()).toBe(200);
    const json = await res.json();
    expect(json.mode).toBe('structural');
    expect(Array.isArray(json.candidates)).toBe(true);
  });
});

const graphReady = (page: Page) =>
  page.waitForFunction(() => {
    const pk = (window as any).__kgPick;
    return !!(pk && pk(0));
  }, null, { timeout: 60000 });

const openDoc = (page: Page) =>
  page.evaluate(() => {
    const d = document.querySelector('.kw-doc .kw-mono');
    return d ? (d.textContent || '').replace(/^wiki\//, '') : null;
  });

async function openKnowledge(page: Page) {
  // The scene and the graph are waited for below; `load` would also wait on
  // every slow subresource of a busy server.
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#friday-scene-canvas', { timeout: 60000 });
  await page.waitForTimeout(1200);
  await page.evaluate(() => (window as any).fridayOpenWorkspace({ workspace: 'knowledge', view: 'split' }));
  await graphReady(page);
  await page.waitForTimeout(6000);                    // entrance animation
}

test.describe('Knowledge workspace', () => {
  test('one Knowledge icon; the galaxy renders and holds the fps floor', async ({ page }) => {
    test.setTimeout(90000);
    await openKnowledge(page);
    const dock = await page.evaluate(() =>
      [...document.querySelectorAll('.dock-btn')].map(b => (b.textContent || '').trim()));
    expect(dock.some(t => /Knowledge/.test(t))).toBe(true);
    expect(dock.some(t => /^Wiki$/.test(t))).toBe(false);
    const bar = await page.textContent('[data-kw-bar]');
    expect(bar).toMatch(/\d+ pages in the galaxy · \d+ links/);
    const fps = await page.evaluate(() => (window as any).__kgFps);
    expect(fps).toBeGreaterThanOrEqual(25);
  });

  test('clicking a page node opens that page beside the graph', async ({ page }) => {
    test.setTimeout(90000);
    await openKnowledge(page);
    // Several candidates: a node in front can take the click.
    const picks = await page.evaluate(() => {
      const pk = (window as any).__kgPick;
      const r = (document.querySelector('.kw-graph .kw-scene') as HTMLElement).getBoundingClientRect();
      const out: any[] = [];
      for (let i = 0; i < 400 && out.length < 12; i++) {
        const p = pk(i);
        if (p && !p.behind && /^page:/.test(p.id) && p.x > r.left + 8 && p.x < r.right - 8
            && p.y > r.top + 8 && p.y < r.bottom - 8) out.push(p);
      }
      return out;
    });
    expect(picks.length).toBeGreaterThan(0);
    let path: string | null = null;
    for (const p of picks) {
      await page.mouse.click(p.x, p.y);
      await page.waitForTimeout(1500);
      path = await openDoc(page);
      if (path) break;
    }
    expect(path).toBeTruthy();
    const focus = await page.evaluate(() => (window as any).__kgFocus());
    const want = await page.evaluate(p => (window as any).kwNodeIdForPath(p), path);
    expect(focus.edgeFocus).toBe(want);                 // the page's own node is lit
    expect(focus.lit).toBeGreaterThan(0);
  });

  test('opening a page flies the graph to its node', async ({ page }) => {
    test.setTimeout(120000);
    await openKnowledge(page);
    // Pages from the sidebar, in order, until one that the galaxy draws
    // (index pages, drafts and .txt notes are not drawn). Sections start
    // collapsed; open them until some pages are listed.
    await page.waitForSelector('.kw-side .kw-sec-t', { timeout: 30000 });
    const rows = await page.evaluate(async () => {
      const listed = () => ([...document.querySelectorAll('.kw-side .kw-linkrow')] as HTMLElement[])
        .map((r, i) => ({ i, text: (r.textContent || '').trim() }))
        .filter(r => /\.md$/.test(r.text) && !/(^|\/)_/.test(r.text));
      for (const t of [...document.querySelectorAll('.kw-side .kw-sec-t')] as HTMLElement[]) {
        if (listed().length >= 3) break;
        t.click();
        await new Promise(res => setTimeout(res, 200));
      }
      return listed().slice(0, 8).map(r => r.i);
    });
    expect(rows.length).toBeGreaterThan(0);
    let done = false;
    for (const i of rows) {
      const before = await page.evaluate(() => (window as any).__kgFocus());
      await page.evaluate(i => (document.querySelectorAll('.kw-side .kw-linkrow')[i] as HTMLElement).click(), i);
      await page.waitForTimeout(2500);
      const path = await openDoc(page);
      const after = await page.evaluate(() => (window as any).__kgFocus());
      if (!path || !after.edgeFocus) continue;
      const want = await page.evaluate(p => (window as any).kwNodeIdForPath(p), path);
      expect(after.edgeFocus).toBe(want);
      expect(after.lit).toBeGreaterThan(0);
      expect(after.target).not.toEqual(before.target);   // the camera moved to it
      done = true;
      break;
    }
    expect(done).toBe(true);
  });

  test('a page the galaxy does not draw clears the focus and says so', async ({ page }) => {
    test.setTimeout(90000);
    await openKnowledge(page);
    await page.evaluate(() => (window as any).fridayNavigate({ workspace: 'knowledge', path: 'brain/bootstrap.md' }));
    await page.waitForTimeout(2000);
    await page.evaluate(() => (window as any).fridayNavigate({ workspace: 'knowledge', path: 'no-such-folder/_index.md' }));
    await page.waitForTimeout(2000);
    const focus = await page.evaluate(() => (window as any).__kgFocus());
    expect(focus.edgeFocus).toBeNull();
    expect(focus.lit).toBe(0);
    expect(await page.textContent('[data-kw-bar]')).toContain('not in the galaxy');
  });

  test('the old Wiki tab address opens Knowledge on its Pages view', async ({ page }) => {
    test.setTimeout(60000);
    await page.goto('/w/wiki?path=brain/bootstrap.md', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.kw-root', { timeout: 30000 });
    expect(page.url()).toMatch(/\/w\/knowledge\?/);
    expect(page.url()).toMatch(/view=pages/);
    expect(await page.getAttribute('.kw-root', 'data-kw-view')).toBe('pages');
  });
});

// The bar under the graph carries the open page and the jump between views;
// it is part of the layout, not whatever is left below a fixed-ratio canvas.
for (const [w, h] of [[1920, 1080], [2560, 1080], [1366, 768], [1080, 1920]]) {
  test(`the bottom bar is inside a ${w}x${h} tab`, async ({ browser }) => {
    test.setTimeout(90000);
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.goto('/w/knowledge', { waitUntil: 'domcontentloaded' });
    await graphReady(page);
    await page.waitForTimeout(2000);
    const m = await page.evaluate(() => {
      const r = (document.querySelector('[data-kw-bar]') as HTMLElement).getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, h: r.height, vh: innerHeight,
               scroll: document.documentElement.scrollHeight };
    });
    expect(m.h).toBeGreaterThan(0);
    expect(m.top).toBeGreaterThanOrEqual(0);
    expect(m.bottom).toBeLessThanOrEqual(m.vh);
    expect(m.scroll).toBeLessThanOrEqual(m.vh);          // nothing pushed below the fold
    await page.close();
  });
}

test('the bottom bar is inside a fullscreen tab', async ({ browser }) => {
  test.setTimeout(90000);
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('/w/knowledge', { waitUntil: 'domcontentloaded' });
  await graphReady(page);
  await page.evaluate(() => {
    const b = document.createElement('button');
    b.id = 'go-fullscreen';
    b.textContent = 'fullscreen';
    Object.assign(b.style, { position: 'fixed', left: '0', top: '0', zIndex: '99999' });
    b.onclick = () => document.documentElement.requestFullscreen();
    document.body.appendChild(b);
  });
  await page.click('#go-fullscreen');                    // fullscreen needs a user gesture
  await page.waitForTimeout(1500);
  const m = await page.evaluate(() => {
    document.getElementById('go-fullscreen')!.remove();
    const r = (document.querySelector('[data-kw-bar]') as HTMLElement).getBoundingClientRect();
    return { fs: !!document.fullscreenElement, bottom: r.bottom, top: r.top, vh: innerHeight };
  });
  expect(m.fs).toBe(true);
  expect(m.top).toBeGreaterThanOrEqual(0);
  expect(m.bottom).toBeLessThanOrEqual(m.vh);
  await page.close();
});
