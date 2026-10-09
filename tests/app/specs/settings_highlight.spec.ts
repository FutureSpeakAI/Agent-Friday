// See & Touch, phase 6: Friday takes the owner to a Settings row, outlined; a row she changed shows who and when.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The settings and the change list are
// fixtures served by page.route; every non-GET request is recorded. Proves in a real page: highlight() scrolls to
// and outlines the row with the 2px --fr-cyan ring and clears it on the next input; the provenance line under a
// row names Friday's proposal with an Undo; Undo posts to the undo route once and the line says it was undone.
import { test, expect, type Page } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const KEY = 'settings.accessibility.big_mode';

async function openSettings(page: Page) {
  const writes: string[] = [];
  let undone = false;
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/settings/changes?*', r => r.fulfill(json({ status: 'ok', changes: [{
    id: 'chg_1', path: KEY, label: 'big mode', old: 'off', new: 'on', by: 'Friday, by a proposal you accepted',
    at: Date.now() / 1000 - 60, undone, undoable: !undone }] })));
  await page.route('**/api/settings/changes/chg_1/undo', r => { undone = true; writes.push('POST undo'); return r.fulfill(json({ ok: true, text: 'Put back: big mode is off again.' })); });
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window as any).FridaySettingRows, null, { timeout: 60000 });
  return writes;
}

test('a row Friday is taken to is outlined, and the outline goes on the next input', async ({ page }) => {
  await openSettings(page);
  await page.evaluate(() => { window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: 'voice' } })); });
  const row = page.locator(`[data-st-key="${KEY}"]`);
  await row.waitFor({ timeout: 30000 });
  expect(await page.evaluate(k => (window as any).FridaySettingRows.highlight(k), KEY)).toBe(true);
  await expect(row).toHaveClass(/fr-st-hit/);
  const outline = await row.evaluate(e => getComputedStyle(e).outlineStyle + ' ' + getComputedStyle(e).outlineWidth);
  expect(outline).toBe('solid 2px');
  await page.waitForTimeout(800);
  await page.mouse.wheel(0, 40);
  await expect(row).not.toHaveClass(/fr-st-hit/);
});

test('the provenance line names Friday\'s proposal and Undo works once', async ({ page }) => {
  const writes = await openSettings(page);
  await page.evaluate(() => { window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: 'voice' } })); });
  const line = page.getByTestId('st-provenance').first();
  await expect(line).toContainText('Friday, by a proposal you accepted');
  await page.getByTestId('st-undo').first().click();
  await expect(line).toContainText('undone');
  expect(writes).toEqual(['POST undo']);
});
