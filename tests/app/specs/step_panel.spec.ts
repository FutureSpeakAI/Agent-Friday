// See & Touch, phase 5: a workflow's steps are shown one state each, and the owner can stop the run.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The server's `steps` and
// `step_update` commands are fed to the page as the window events the command runner raises; the stop route is
// recorded. Proves in a real page: the list appears with each state in words and a glyph; a step needing the
// owner reads "needs you"; Stop posts to the stop route once; Esc stops it but not from a text field; the finished
// list goes away on its own.
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function open(page: Page) {
  const stops: string[] = [];
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/steps/active', r => r.fulfill(json({ list: null })));
  await page.route('**/api/steps/*/stop', r => { stops.push(new URL(r.request().url()).pathname); return r.fulfill(json({ ok: true, text: 'Stopped by you. The step that is running finishes; the next never starts.' })); });
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window as any).FridayStepPanel, null, { timeout: 60000 });
  return stops;
}
const send = (page: Page, detail: unknown, type: string) =>
  page.evaluate(([d, t]) => window.dispatchEvent(new CustomEvent('friday:' + t, { detail: d })), [detail, type] as const);

const START = { id: 'w1', title: 'Morning brief', kind: 'workflow', steps: [
  { n: 1, text: 'Gather', state: 'doing' }, { n: 2, text: 'Write', state: 'waiting' }, { n: 3, text: 'Send', state: 'waiting' }] };

test('the list shows each step with its state in words, and a step needing the owner says so', async ({ page }) => {
  await open(page);
  await send(page, START, 'steps');
  const panel = page.getByTestId('step-panel');
  await expect(panel).toContainText('Morning brief');
  await expect(panel.locator('li[data-state=doing]')).toContainText('doing');
  await expect(panel.locator('li[data-state=waiting]')).toHaveCount(2);
  await send(page, { id: 'w1', n: 1, state: 'done' }, 'step_update');
  await send(page, { id: 'w1', n: 2, state: 'held' }, 'step_update');
  await expect(panel.locator('li[data-state=held]')).toContainText('needs you');
});

test('Stop posts one stop and shows what it did', async ({ page }) => {
  const stops = await open(page);
  await send(page, START, 'steps');
  await page.getByTestId('step-stop').click();
  await expect(page.getByTestId('step-panel')).toContainText('Stopped by you');
  expect(stops).toEqual(['/api/steps/w1/stop']);
});

test('Esc stops the run, but not while typing in a field', async ({ page }) => {
  const stops = await open(page);
  await send(page, START, 'steps');
  await page.evaluate(() => { const i = document.createElement('input'); i.id = 'probe'; document.body.appendChild(i); i.focus(); });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
  expect(stops).toEqual([]);
  await page.evaluate(() => (document.getElementById('probe') as HTMLElement).blur());
  await page.keyboard.press('Escape');
  await expect.poll(() => stops.length).toBe(1);
});

test('a finished list goes away by itself', async ({ page }) => {
  await open(page);
  await send(page, START, 'steps');
  for (const n of [1, 2, 3]) await send(page, { id: 'w1', n, state: 'done' }, 'step_update');
  await expect(page.getByTestId('step-stop')).toHaveCount(0);
  await expect(page.getByTestId('step-panel')).toHaveCount(0, { timeout: 12000 });
});
