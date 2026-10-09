// See & Touch, phase 7: a workspace that was open-only shows Friday its rows, and a counts-only workspace shows no title.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Rows are rendered into the page by the
// spec so the registrar (static/workspace_stages.js) and domList are proven in a real browser, not in the
// workspaces' own data: calendar events can be ticked (Ctrl-click) and pointed at; health rows publish a kind only.
import { test, expect, type Page } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function open(page: Page) {
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window as any).fridayWorkspaceStages && !!(window as any).fridayStage, null, { timeout: 60000 });
}
// The probe sits above the shell's own overlays (the Depth entry, docks), so a click reaches the row itself.
const addRows = (page: Page, html: string) => page.evaluate(h => { const d = document.createElement('div'); d.id = 'probe'; d.style.cssText = 'position:fixed;left:30%;top:35%;z-index:2147483000;padding:12px;background:#222;color:#fff'; d.innerHTML = h; document.body.appendChild(d); }, html);

test('calendar rows get a stage; Ctrl-click ticks one; a pointed row is outlined', async ({ page }) => {
  await open(page);
  await addRows(page, `<div data-fr-ref="event:e1" data-fr-title="Dentist" data-fr-facets='{"kind":"normal","when":"today"}'>Dentist</div>
    <div data-fr-ref="event:e2" data-fr-title="Lunch" data-fr-facets='{"kind":"normal","when":"tomorrow"}'>Lunch</div>`);
  await expect.poll(() => page.evaluate(() => (window as any).fridayStage.registered('calendar'))).toBe(true);
  await page.locator('[data-fr-ref="event:e2"]').click({ modifiers: ['Control'] });
  const st = await page.evaluate(() => (window as any).fridayStage.snapshot('calendar'));
  expect(st.items.map((i: any) => i.ref)).toEqual(['event:e1', 'event:e2']);
  expect(st.selection.refs).toEqual(['event:e2']);
  await expect(page.locator('[data-fr-ref="event:e2"]')).toHaveAttribute('data-fr-sel', 'on');
  await page.evaluate(() => (window as any).fridayStage.run({ type: 'point', workspace: 'calendar', refs: ['event:e1'], badges: 'none' }));
  await expect(page.locator('[data-fr-ref="event:e1"]')).toHaveAttribute('data-fr-point', 'on');
});

test('a counts-only workspace publishes a kind and no title', async ({ page }) => {
  await open(page);
  await addRows(page, `<div data-fr-ref="health:med-0" data-fr-title="Metformin" data-fr-facets='{"kind":"medication"}'>x</div>`);
  await expect.poll(() => page.evaluate(() => (window as any).fridayStage.registered('health'))).toBe(true);
  const st = await page.evaluate(() => (window as any).fridayStage.snapshot('health'));
  expect(st.items[0].title).toBe('');
  expect(st.items[0].facets).toEqual({ kind: 'medication' });
});

test('the stage goes when the rows do', async ({ page }) => {
  await open(page);
  await addRows(page, `<div data-fr-ref="wf:morning" data-fr-title="Morning" data-fr-facets='{"status":"idle"}'>x</div>`);
  await expect.poll(() => page.evaluate(() => (window as any).fridayStage.registered('workflows'))).toBe(true);
  await page.evaluate(() => document.getElementById('probe')!.remove());
  await expect.poll(() => page.evaluate(() => (window as any).fridayStage.registered('workflows'))).toBe(false);
});
