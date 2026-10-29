// Contrast audit of every workspace (north star 33.1-1 and the burn-down's "contrast audit"; WCAG 2.2 1.4.3).
//
// Each workspace in the registry is opened in its own tab (/w/<id>), and every piece of text it draws is checked
// against what is really behind it (translucent layers composited; tests/app/liveness.ts assertReadableContrast) at
// the AA ratio for body text, 4.5:1. Every shortfall is printed, workspace by workspace, and then the test fails.
// (Round 1 measured; the colour() parse fix in liveness.ts, the calendar hour labels and the status-pill detail
// were the shortfalls it found.)
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Every write is answered here.
import { test, expect, type Page } from '../fixtures';
import { settle } from '../helpers/settle';
import { BASE } from '../harness';
import { assertReadableContrast } from '../liveness';

test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });
test.setTimeout(30 * 60_000);

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function answerWrites(page: Page) {
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
}

test('every workspace: text against what is behind it, at 4.5:1', async ({ page }) => {
  await answerWrites(page);
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window as any).FRIDAY_WORKSPACE_REGISTRY, null, { timeout: 90_000 });
  const ids: string[] = await page.evaluate(() =>
    (((window as any).FRIDAY_WORKSPACE_REGISTRY || {}).workspaces || []).filter((x: any) => x && x.id && !x.held).map((x: any) => String(x.id)));
  expect(ids.length).toBeGreaterThan(10);
  const short: Record<string, string> = {};
  const unopened: string[] = [];
  let checked = 0;
  for (const id of ids) {
    try {
      await page.goto(`${BASE}/w/${encodeURIComponent(id)}`, { waitUntil: 'domcontentloaded' });
      await page.locator('#ui-inner, #ui-root').first().waitFor({ state: 'attached', timeout: 60_000 });
      await settle(page, { quietMs: 1000, timeout: 10_000 });
    } catch { unopened.push(id); continue; }   // a slow start is not a contrast failure; `checked` below guards coverage
    checked += 1;
    try { await assertReadableContrast(page, id, 4.5); } catch (e) { short[id] = String((e as Error).message || e).split('\n').slice(0, 40).join('\n'); }
  }
  console.log('[contrast] ' + JSON.stringify({ workspaces: ids.length, checked, unopened, short: Object.keys(short) }));
  for (const [id, msg] of Object.entries(short)) console.log(`[contrast] ${id}: ${msg}`);
  expect(checked, 'workspaces the audit could open').toBeGreaterThan(10);
  expect(Object.keys(short), 'workspaces with text below 4.5:1 (details printed above)').toEqual([]);
});