/**
 * Every workspace but Settings can be its own browser tab (/w/<id>), and the
 * desktop's own-tab controls open exactly that - not a second copy of the
 * whole desktop, which is what "open in new tab" used to do.
 *
 * Read from a running app, not from index.html: the failure being guarded
 * against is "the tab shows the desktop", which only a rendered page can show.
 *
 *   FRIDAY_URL=http://localhost:3217 PW_CHANNEL=msedge npx playwright test tests/workspace_tabs.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || '';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.setTimeout(240000);
test.use({ viewport: { width: 1600, height: 900 } });

async function workspaces(page: Page): Promise<{ id: string; label: string }[]> {
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof (window as any).WS !== 'undefined' || typeof WS !== 'undefined', null, { timeout: 90000 });
  // eslint-disable-next-line no-undef
  return page.evaluate(() => (WS as any[]).map(w => ({ id: w.id, label: w.label })));
}
declare const WS: any[];

test('each workspace renders alone in its own tab: no desktop, dock, windows or scene', async ({ page }) => {
  const all = await workspaces(page);
  expect(all.length).toBeGreaterThan(15);
  for (const w of all.filter(x => x.id !== 'settings')) {
    const errors: string[] = [];
    const onErr = (e: Error) => errors.push(e.message);
    page.on('pageerror', onErr);
    await page.goto(BASE + '/w/' + w.id);
    await page.waitForSelector(`[data-standalone="${w.id}"] .ws-tab-body > *`, { timeout: 60000 });
    const dom = await page.evaluate(() => ({
      dock: document.querySelectorAll('.dock, .dock-btn').length,
      windows: document.querySelectorAll('.fwin').length,
      // the holographic scene is never started in a tab
      scene: typeof (window as any).renderer !== 'undefined' && !!(window as any).renderer,
      canvas: (() => { const c = document.getElementById('friday-scene-canvas'); return !!c && getComputedStyle(c).display !== 'none'; })(),
      title: document.title,
      back: (document.querySelector('[data-testid="ws-tab-back"]') as HTMLAnchorElement | null)?.getAttribute('href'),
      approvals: !!document.querySelector('[data-testid="ws-tab-approvals"]'),
    }));
    expect(dom.dock, w.id).toBe(0);
    expect(dom.windows, w.id).toBe(0);
    expect(dom.scene, w.id).toBe(false);
    expect(dom.canvas, w.id).toBe(false);
    expect(dom.title, w.id).toBe(w.label + ' — Friday');
    expect(dom.back, w.id).toBe('/?workspace=' + w.id);
    expect(dom.approvals, w.id).toBe(true);
    expect(errors, w.id).toEqual([]);
    page.off('pageerror', onErr);
  }
});

test('Settings is not a tab; it opens in the desktop', async ({ page }) => {
  await page.goto(BASE + '/w/settings');
  expect(new URL(page.url()).search).toBe('?workspace=settings');
});

test('every own-tab control targets /w/<id>: dock Ctrl-click, dock middle-click, and the window button', async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__opened = [];
    window.open = ((url: string) => { (window as any).__opened.push(String(url)); return { focus() {} } as any; }) as any;
  });
  const all = await workspaces(page);
  await page.waitForSelector('.dock-btn', { timeout: 60000 });
  const opened = () => page.evaluate(() => (window as any).__opened.slice());
  const dockBtn = (id: string) => page.locator(`.dock-btn[data-ws="${id}"]`);
  for (const w of all) {
    const before = (await opened()).length;
    // dispatched: the dock's own geometry and hit areas have their own tests
    await dockBtn(w.id).dispatchEvent('click', { ctrlKey: true, bubbles: true });
    const after = await opened();
    if (w.id === 'settings') expect(after.length, 'settings never becomes a tab').toBe(before);
    else expect(after[after.length - 1], w.id).toBe('/w/' + encodeURIComponent(w.id));
  }
  // middle-click, once
  await dockBtn('news').dispatchEvent('auxclick', { button: 1, bubbles: true });
  expect((await opened()).pop()).toBe('/w/news');
  // the ↗ on a workspace window
  for (const id of ['messages', 'studio', 'settings']) await dockBtn(id).dispatchEvent('click', { bubbles: true });
  await page.waitForTimeout(1200);
  const tabBtns = await page.evaluate(() => [...document.querySelectorAll('[data-ws-tab]')].map(b => ({ id: b.getAttribute('data-ws-tab'), hidden: (b as HTMLElement).hidden })));
  // Settings opens as its own panel, not a window: it offers no own-tab button
  expect(tabBtns.filter(b => b.id === 'settings' && !b.hidden)).toEqual([]);
  for (const b of tabBtns.filter(x => !x.hidden)) {
    await page.locator(`[data-ws-tab="${b.id}"]`).dispatchEvent('click', { bubbles: true });
    expect((await opened()).pop(), b.id).toBe('/w/' + b.id);
  }
  expect(tabBtns.filter(x => !x.hidden).map(x => x.id)).toEqual(expect.arrayContaining(['messages', 'studio']));
});
