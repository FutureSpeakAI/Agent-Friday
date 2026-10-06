/**
 * Every available workspace but Settings can be its own browser tab (/w/<id>), and the
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
  // Held workspaces stay registered but do not offer navigation until enabled.
  // Use the policy as the inventory so missing available controls still fail.
  return page.evaluate(() => (WS as any[]).filter(w => !fridayWsHeld(w)).map(w => ({ id: w.id, label: w.label })));
}
declare const WS: any[];
declare function fridayWsHeld(workspace: any): boolean;

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
      // the desktop's own top bar, with its model selector and chat button
      bar: document.querySelectorAll('[data-testid="friday-top-bar"]').length,
      chat: !!document.querySelector('[data-testid="friday-top-bar"] [aria-label="Open chat with Friday"]'),
    }));
    expect(dom.dock, w.id).toBe(0);
    expect(dom.windows, w.id).toBe(0);
    expect(dom.scene, w.id).toBe(false);
    expect(dom.canvas, w.id).toBe(false);
    expect(dom.title, w.id).toBe(w.label + ' · Agent Friday™');
    expect(dom.back, w.id).toBe('/?workspace=' + w.id);
    expect(dom.bar, w.id).toBe(1);
    expect(dom.chat, w.id).toBe(true);
    expect(errors, w.id).toEqual([]);
    page.off('pageerror', onErr);
  }
});

test('Settings is not a tab; it opens in the desktop', async ({ page }) => {
  await page.goto(BASE + '/w/settings');
  expect(new URL(page.url()).search).toBe('?workspace=settings');
});

test('every own-tab control targets /w/<id>: dock Ctrl-click, dock middle-click, and the window menu', async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__opened = [];
    (window as any).__targets = [];
    // Stands in for a new tab: blank until pointed somewhere. The window menu
    // opens its workspace's NAMED tab blank and then points it at /w/<id>
    // (tests/workspace_tab_open.spec.ts drives the real thing).
    window.open = ((url: string, target?: string) => {
      const w = (window as any);
      w.__targets.push(String(target));
      if (url) w.__opened.push(String(url));
      return { focus() {}, postMessage() {},
        location: { href: 'about:blank', pathname: 'blank', origin: location.origin,
          replace(u: string) { w.__opened.push(String(u)); } } } as any;
    }) as any;
  });
  const all = await workspaces(page);
  await page.waitForSelector('.dock-btn', { timeout: 60000 });
  expect(all.length).toBeGreaterThan(15);
  await expect.poll(() => page.locator('.dock-btn[data-ws]').evaluateAll(buttons => buttons.map(button => button.getAttribute('data-ws')).sort()), {
    message: 'every available registry workspace has a dock control',
  }).toEqual(all.map(w => w.id).sort());
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
  const targets = () => page.evaluate(() => (window as any).__targets.slice());
  expect((await targets()).every((t: string) => t === '_blank'), 'the dock opens plain (background) tabs').toBe(true);
  // More owns a body-portal menu: the own-tab action only exists while open.
  // Visit each workspace instead of treating the currently mounted actions as
  // an inventory, which would pass vacuously when every menu is closed.
  for (const w of all.filter(x => x.id !== 'settings')) {
    await dockBtn(w.id).dispatchEvent('click', { bubbles: true });
    const win = page.locator(`.fwin[data-friday-workspace="${w.id}"]`);
    await expect(win, w.id).toHaveCount(1);
    await win.locator(`[data-ws-more="${w.id}"]`).dispatchEvent('click', { bubbles: true });
    const ownTab = page.locator(`[data-ws-tab="${w.id}"]`);
    await expect(ownTab, w.id).toBeVisible();
    await expect(ownTab).toHaveAttribute('role', 'menuitem');
    await ownTab.dispatchEvent('click', { bubbles: true });
    // A window carries its active subview and scroll position in the query.
    // Its destination remains this Friday's standalone workspace route.
    const ownTabUrl = new URL((await opened()).pop(), page.url());
    expect(ownTabUrl.origin, w.id).toBe(new URL(page.url()).origin);
    expect(ownTabUrl.pathname, w.id).toBe('/w/' + encodeURIComponent(w.id));
    expect((await targets()).pop(), "the window menu uses the workspace's own named tab").toBe('friday-ws-' + w.id);
    await expect(win, w.id).toHaveCount(0);
  }
  // Settings opens as its own panel and never offers a window or own-tab menu.
  await dockBtn('settings').dispatchEvent('click', { bubbles: true });
  await expect(page.locator('.fwin[data-friday-workspace="settings"]')).toHaveCount(0);
  await expect(page.locator('[data-ws-tab="settings"]')).toHaveCount(0);
});
