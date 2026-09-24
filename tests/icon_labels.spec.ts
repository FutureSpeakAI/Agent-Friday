/**
 * Nothing needs guessing: every visible button either says what it does in
 * words, or (where space is tight) carries an aria-label for screen readers,
 * alongside its tooltip. Checked on every workspace in its own tab, in 3D
 * where a workspace has it, and on the desktop with windows and chat open.
 *
 *   FRIDAY_URL=http://localhost:3219 PW_CHANNEL=msedge npx playwright test tests/icon_labels.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || '';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.setTimeout(600000);
test.use({ viewport: { width: 1600, height: 1000 } });

async function prepare(page: Page) {
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await page.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET'
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"needs_prompt":false}' }) : r.continue());
  // read-only: nothing is changed while looking
  await page.route('**/api/**', r => (r.request().method() === 'GET' ? r.fallback() : r.abort()));
}

const unlabelled = (page: Page) => page.evaluate(() => {
  const out: string[] = [];
  for (const el of Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], [role="switch"]')) as HTMLElement[]) {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none') continue;
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (/[A-Za-z]{2,}/.test(text)) continue;                      // says it in words
    if ((el.getAttribute('aria-label') || '').trim()) continue;    // labelled for a screen reader
    out.push(JSON.stringify(text) + ' ' + el.outerHTML.slice(0, 90));
  }
  return Array.from(new Set(out));
});
declare const WS: any[];

test('every workspace, in its own tab and in 3D', async ({ page }) => {
  await prepare(page);
  await page.goto(BASE + '/w/news');
  await page.waitForFunction(() => typeof WS !== 'undefined', null, { timeout: 90000 });
  const ids: string[] = await page.evaluate(() => WS.filter((w: any) => w.id !== 'settings').map((w: any) => w.id));
  const bad: string[] = [];
  for (const id of ids) {
    for (const view3d of [false, true]) {
      await page.goto(BASE + '/w/' + id + (view3d ? '?view3d=1' : ''));
      await page.waitForSelector('.ws-tab-body > *', { timeout: 60000 });
      await page.waitForTimeout(view3d ? 3000 : 2500);
      if (view3d && !(await page.locator('.f3, .f3-host.on').count())) continue;
      for (const u of await unlabelled(page)) bad.push(id + (view3d ? ' (3D)' : '') + ': ' + u);
    }
  }
  expect(bad, bad.join('\n')).toEqual([]);
});

test('the desktop: windows, chat panel and chat window', async ({ page }) => {
  await prepare(page);
  await page.goto(BASE + '/');
  await page.waitForSelector('.dock', { timeout: 90000 });
  await page.waitForTimeout(1500);
  for (const id of ['messages', 'news', 'home']) await page.locator(`.dock-btn[data-ws="${id}"]`).dispatchEvent('click', { bubbles: true });
  await page.evaluate(async () => { const r = await fetch('/api/conversations').then(x => x.json()); (window as any).fridayOpenChatWindow((r.conversations || [])[0].id, 'x'); });
  await page.waitForTimeout(2500);
  const bad = await unlabelled(page);
  expect(bad, bad.join('\n')).toEqual([]);
});
