// 3D rendering switched off (north star 21.22-3, 21.2-13; burn-down BD-21): with the setting off no WebGL
// context is ever created, the desktop is still fully usable, and the knowledge view is its flat Pages form.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Writes are answered by the test.
import { test, expect, type Page } from '../fixtures';
import { BASE, openApp, openWorkspace } from '../harness';
import { showSettingsTab } from '../helpers/settings_tab';

test.skip(!process.env.FRIDAY_BASE || /:3000(\/|$)/.test(process.env.FRIDAY_BASE || ''),
  'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

/** Before any page script: 3D off, and a count of WebGL contexts actually made. The page's own switch
 *  wraps getContext on top of this one, so a WebGL context can only be made through here. */
async function threeDOff(page: Page) {
  await page.addInitScript(() => {
    try { localStorage.setItem('friday.render3d', 'off'); } catch (_) {}
    const w = window as any;
    w.__webglMade = 0;
    const real = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: any[]) {
      const ctx = (real as any).call(this, kind, ...rest);
      if (ctx && /webgl/i.test(String(kind))) w.__webglMade++;
      return ctx;
    } as any;
  });
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
}

test('with 3D off the desktop makes no WebGL context and stays usable', async ({ page }) => {
  await threeDOff(page);
  await openApp(page);
  await expect(page.locator('html')).toHaveAttribute('data-render3d', 'off');
  await expect(page.locator('#friday-scene-canvas')).toBeHidden();
  // The dock and a workspace still work.
  const dock = page.locator('.dock-btn').first();
  await expect(dock).toBeVisible();
  await page.waitForTimeout(1500);
  expect(await page.evaluate(() => (window as any).__webglMade), 'a WebGL context was created').toBe(0);
});

test('with 3D off the knowledge view is Pages only and still makes no WebGL context', async ({ page }) => {
  await threeDOff(page);
  await openApp(page);
  await openWorkspace(page, 'Knowledge');
  const tabs = page.locator('.kw-views .kw-view');
  await expect(tabs).toHaveCount(1);
  await expect(tabs.first()).toHaveText('Pages');
  await page.waitForTimeout(1500);
  expect(await page.evaluate(() => (window as any).__webglMade), 'the galaxy made a WebGL context').toBe(0);
});

test('the Settings switch says what it does and saves the choice', async ({ page }) => {
  const saved: string[] = [];
  await page.route('**/api/settings', r => {
    // Answered here: the scratch server keeps its 3D on for every other spec.
    if (r.request().method() !== 'POST') return r.fallback();
    saved.push(r.request().postData() || '');
    return r.fulfill(json({ status: 'ok' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window as any).FridaySettingRows, null, { timeout: 60000 });
  const off = page.locator('[data-render3d-choice="off"]');
  await showSettingsTab(page, 'appearance', off);
  await expect(page.getByRole('radiogroup', { name: '3D rendering' })).toBeVisible();
  await off.click();
  await expect.poll(() => saved.some(b => /"render_3d"\s*:\s*false/.test(b))).toBe(true);
  expect(await page.evaluate(() => localStorage.getItem('friday.render3d'))).toBe('off');
});
