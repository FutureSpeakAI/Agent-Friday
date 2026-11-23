/**
 * The start screen's countdowns: they move into the space the chat tray leaves, their text is never cut off,
 * and a hidden one stays hidden after a reload.
 *
 * SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Synthetic countdowns are served by
 * the spec (no real wiki, task or calendar is read), the start screen is held open with landing_mode "always",
 * and the settings the hide control writes go to the scratch home's own settings file.
 *
 * Frames are saved for a person to look at: closed.png (tray closed), open.png (tray open), hidden.png (one
 * countdown hidden). The assertions are the measurable half: every countdown's box lies inside the viewport
 * minus the tray, and no text overflows its own box.
 */
import { test, expect, type Page } from '../fixtures';
import { bootApp } from '../harness';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1280, height: 800 } });
test.describe.configure({ timeout: 180_000 });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const SOON = Math.floor(Date.now() / 1000) + 3 * 86400;
const GUTTER = 16;                     // the side gutter the cluster keeps (no countdown touches the window edge)
const inDays = (n: number) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
const COUNTDOWNS = [
  { id: 'todo:t-grant', label: 'Send the grant report to the quarterly review panel', emoji: '', why: 'a deadline you set',
    date: inDays(3), at: SOON, all_day: false, days: 3, kind: 'commitment', source: 'todo', short: 'in 3 days' },
  { id: 'wiki:people/dana-reyes.md#0', label: "Dana's birthday", emoji: '🎂', why: 'from your wiki',
    date: inDays(12), at: null, all_day: true, days: 12, kind: 'personal', source: 'wiki', short: 'in 12 days' },
  { id: 'todo:t-lease', label: 'Renew the lease', emoji: '', why: 'a deadline you set',
    date: inDays(20), at: null, all_day: true, days: 20, kind: 'commitment', source: 'todo', short: 'in 20 days' },
  { id: 'wiki:people/sam-lee.md#0', label: "Sam's anniversary dinner at the harbour restaurant", emoji: '💍', why: 'from your wiki',
    date: inDays(31), at: null, all_day: true, days: 31, kind: 'personal', source: 'wiki', short: 'in 31 days' },
];

async function seed(page: Page) {
  await page.route('**/api/countdowns*', route => route.fulfill(json({ status: 'ok', countdowns: COUNTDOWNS, failed: [] })));
}

async function alwaysShow(page: Page) {
  const r = await page.request.post(`${BASE}/api/settings`, { data: { settings: { landing_mode: 'always' } } });
  expect(r.ok()).toBeTruthy();
  // Past first run (its own screen covers the start screen, as in headings.spec.ts), on the classic start screen:
  // the countdown cluster is not drawn in the Simple home (static/friday_display_style.js).
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ initialized: true }) }));
  await page.addInitScript(() => { try { localStorage.setItem('friday.display-style.v1', 'classic'); } catch (_) {} });
}

async function openChat(page: Page) {
  if (await page.locator('.chat-panel.open').count()) return;
  await page.locator('button[aria-label^="Open chat with"]').first().click();
  await expect(page.locator('.chat-panel.open')).toHaveCount(1);
}

/** Where the countdowns are, against the space the tray leaves, and whether any text overflows its own box. */
async function layout(page: Page) {
  return page.evaluate(() => {
    const tray = document.querySelector('.chat-panel.open') as HTMLElement | null;
    const trayRect = tray ? tray.getBoundingClientRect() : null;
    const dockRight = getComputedStyle(document.documentElement).getPropertyValue('--fr-chat-dock').trim();
    const items = Array.from(document.querySelectorAll('[data-testid="countdowns"] .greeting-ticker-item')) as HTMLElement[];
    return {
      viewport: window.innerWidth,
      tray: trayRect ? { left: trayRect.left, right: trayRect.right, width: trayRect.width } : null,
      dockRight,
      items: items.map(el => {
        const box = el.getBoundingClientRect();
        const text = Array.from(el.querySelectorAll('.cd-what span, .days')) as HTMLElement[];
        return {
          text: el.innerText.replace(/\s+/g, ' ').trim(),
          left: box.left, right: box.right, top: box.top, bottom: box.bottom,
          clipped: text.some(t => t.scrollWidth > t.clientWidth + 1),
        };
      }),
    };
  });
}

test('the countdowns sit in the space the tray leaves and nothing is cut off', async ({ page }, info) => {
  await seed(page);
  await alwaysShow(page);
  await bootApp(page);
  await expect(page.locator('[data-testid="countdowns"] .greeting-ticker-item')).toHaveCount(4);

  const closed = await layout(page);
  await page.screenshot({ path: info.outputPath('closed.png') });
  expect(closed.tray).toBeNull();
  for (const item of closed.items) {
    expect(item.left, item.text).toBeGreaterThanOrEqual(GUTTER - 1);
    expect(item.right, item.text).toBeLessThanOrEqual(closed.viewport - GUTTER + 1);
    expect(item.clipped, item.text).toBe(false);
  }

  await openChat(page);
  await expect(page.locator('[data-testid="countdowns"] .greeting-ticker-item')).toHaveCount(4);
  const open = await layout(page);
  await page.screenshot({ path: info.outputPath('open.png') });
  expect(open.tray, 'the tray did not open').not.toBeNull();
  const freeRight = open.tray!.left;
  for (const item of open.items) {
    expect(item.right, `${item.text} runs under the tray`).toBeLessThanOrEqual(freeRight - GUTTER + 1);
    expect(item.left, item.text).toBeGreaterThanOrEqual(GUTTER - 1);
    expect(item.clipped, `${item.text} is cut off`).toBe(false);
  }
});

test('a hidden countdown stays hidden after a reload and is written to the settings', async ({ page }, info) => {
  await seed(page);
  await alwaysShow(page);
  await bootApp(page);
  const items = page.locator('[data-testid="countdowns"] .greeting-ticker-item');
  await expect(items).toHaveCount(4);

  await page.getByRole('button', { name: "Hide Dana's birthday" }).click();
  await expect(items).toHaveCount(3);
  await expect.poll(async () => {
    const got = await (await page.request.get(`${BASE}/api/settings`)).json();
    return Object.keys((got.settings || {}).dismissed_countdowns || {});
  }).toEqual(['wiki:people/dana-reyes.md#0']);
  await page.screenshot({ path: info.outputPath('hidden.png') });
  const fewer = await layout(page);                                    // three fit on one row: still inside the gutter
  for (const item of fewer.items) {
    expect(item.left, item.text).toBeGreaterThanOrEqual(GUTTER - 1);
    expect(item.right, item.text).toBeLessThanOrEqual(fewer.viewport - GUTTER + 1);
    expect(item.clipped, item.text).toBe(false);
  }

  await page.reload();
  await bootApp(page);
  await expect(page.locator('[data-testid="countdowns"] .greeting-ticker-item')).toHaveCount(3);
  await expect(page.locator('[data-testid="countdowns"]')).not.toContainText("Dana's birthday");

  // leave the scratch home as the next test expects it
  await page.request.post(`${BASE}/api/settings`, { data: { settings: { dismissed_countdowns: {} } } });
});
