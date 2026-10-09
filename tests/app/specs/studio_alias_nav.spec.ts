// Audit B2: navigate_to(file | creation) lands on the file view with that file focused.
//
// "studio" is an alias for Media, so a target addressed to it opened the Media library and dropped
// root, path and file. Files and creations now open the Files browser inside the Library workspace
// ("Browse this PC"). SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused).
// The folder scan is a fixture served by page.route; nothing on this PC is read.
import { test, expect } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

test('the Library takes a file target, shows the folder and reports the chosen file', async ({ page }) => {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/studio-files/roots', r => r.fulfill(json({ roots: [{ id: 'documents', label: 'Documents', available: true }] })));
  await page.route('**/api/studio-files/scan?*', r => r.fulfill(json({
    path: 'Finance', label: 'Documents', truncated: false, skipped: 0, elapsed_ms: 1,
    items: [{ rel: 'Finance/budget.xlsx', name: 'budget.xlsx', ext: '.xlsx', size: 1200, mtime: 1759000000, dir: false },
            { rel: 'Finance/notes.txt', name: 'notes.txt', ext: '.txt', size: 40, mtime: 1759000000, dir: false }],
  })));
  await page.goto(`${BASE}/w/library`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => (window as any).fridayNavigate, null, { timeout: 60000 });
  await page.evaluate(() => (window as any).fridayNavigate({ workspace: 'library', view: 'pc', root: 'documents', path: 'Finance', file: 'budget.xlsx' }));
  await expect.poll(() => page.evaluate(() => (window as any).__files3dShowing && (window as any).__files3dShowing.file), { timeout: 20000 }).toBe('budget.xlsx');
  const showing = await page.evaluate(() => (window as any).__files3dShowing);
  expect(showing).toMatchObject({ root: 'documents', path: 'Finance', file: 'budget.xlsx' });
});

test('a target addressed to the studio alias still lands on a workspace that exists', async ({ page }) => {
  await page.goto(`${BASE}/w/media`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => (window as any).fridayResolveTarget, null, { timeout: 60000 });
  const ws = await page.evaluate(() => (window as any).fridayResolveTarget({ workspace: 'studio', view: 'library' }).workspace);
  expect(ws).toBe('media');                              // the alias is still Media; the server no longer sends files through it
});
