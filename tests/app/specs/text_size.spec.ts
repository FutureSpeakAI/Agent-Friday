// Text size (north star 33.1-8; WCAG 1.4.4; burn-down BD-21 "an in-app text-size setting with a zoom test"):
// Settings saves the size; at 200% what a person reads in workspace windows and the chat is twice the size,
// and it wraps inside its window instead of running off the side.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Every write is answered here.
import { test, expect, type Page } from '../fixtures';
import { settle } from '../helpers/settle';
import { showSettingsTab } from '../helpers/settings_tab';
import { BASE, openApp } from '../harness';

test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function answerWrites(page: Page, saved: string[] = []) {
  await page.route('**/*', r => {
    if (r.request().method() === 'GET') return r.fallback();
    if (new URL(r.request().url()).pathname === '/api/settings') saved.push(r.request().postData() || '');
    return r.fulfill(json({ status: 'ok' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
}

test('Settings saves the text size and the page takes it at once', async ({ page }) => {
  const saved: string[] = [];
  await answerWrites(page, saved);
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window as any).FridaySettingRows, null, { timeout: 60_000 });
  const big = page.locator('[data-text-scale="200"]');
  await showSettingsTab(page, 'appearance', big);
  await big.click();
  await expect.poll(() => saved.some(b => /"text_scale"\s*:\s*200/.test(b))).toBe(true);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--fr-text-zoom').trim())).toBe('2');
});

test('at 200% the text in windows and the chat is twice the size, scaled once, and wraps inside its window', async ({ page }) => {
  await answerWrites(page);
  await openApp(page);
  const measure = () => page.evaluate(() => Array.from(document.querySelectorAll('.fwin .fwin-body')).map(b => {
    const e = b as HTMLElement;
    return { ws: (e.closest('.fwin') as HTMLElement | null)?.dataset.fridayWorkspace || '?',
      zoom: getComputedStyle(e).zoom, width: Math.round(e.getBoundingClientRect().width), over: e.scrollWidth - e.clientWidth };
  }));
  const results: { ws: string, before: number, after: number }[] = [];
  for (const ws of ['knowledge', 'messages', 'news']) {
    // One window at a time, maximized: the width a person gives the text they are reading.
    await page.evaluate(w => (window as any).fridayRunActions([{ type: 'navigate', workspace: w, max: true }]), ws);
    const win = page.locator(`.fwin[data-friday-workspace="${ws}"] .fwin-body`);
    await expect(win).toBeVisible({ timeout: 30_000 });
    await page.evaluate(() => (window as any).fridayApplyTextScale(100));
    await settle(page, { quietMs: 1000, timeout: 15_000 });
    const before = (await measure()).find(m => m.ws === ws)!;
    await page.evaluate(() => (window as any).fridayApplyTextScale(200));
    await settle(page, { quietMs: 1000, timeout: 15_000 });
    const after = (await measure()).find(m => m.ws === ws)!;
    expect(after.zoom, ws + ': the text is twice the size').toBe('2');
    expect(after.width, ws + ': the window keeps its size').toBe(before.width);
    results.push({ ws, before: before.over, after: after.over });
  }
  // Nothing is scaled twice: no zoomed element sits inside another zoomed element.
  const nested = await page.evaluate(() => Array.from(document.querySelectorAll('*')).filter(e => {
    if (getComputedStyle(e).zoom === '1') return false;
    for (let p = e.parentElement; p; p = p.parentElement) if (getComputedStyle(p).zoom !== '1') return true;
    return false;
  }).length);
  expect(nested, 'elements zoomed inside a zoomed ancestor').toBe(0);
  const chat = await page.evaluate(() => { const t = document.querySelector('.fx-chat-transcript'); return t ? getComputedStyle(t).zoom : null; });
  if (chat !== null) expect(chat).toBe('2');
  // What grew wraps inside its window: 200% adds no sideways scrolling a window did not already have at 100%.
  expect(results.filter(r => r.before <= 2 && r.after > 2), 'windows that scroll sideways only at 200%').toEqual([]);
});