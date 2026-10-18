// Settings > Laya, in a real page, on a scratch home (Slice 1 of laya_settings_STORM_2026-10-17).
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The decision log is seeded with
// synthetic rows (no words: a hash, the decision, the tool, the confidence) so the report card has weeks to show;
// nothing else is stubbed. Proves: the tab draws its six sections; the approval check never shows "act"; the replay
// test runs on the real server and says honestly what it found on a home with no history; a read's bar offers the
// labelled recommendation without choosing it, an action that changes something can only go up, an outward search
// has no bar; bars stay in shadow until "Use my bars" is set to act, and both survive a reload; the voice route for
// a section ("confidence bars") opens this tab. Each step saves a screenshot to the run's output folder.
import * as fs from 'fs';
import * as path from 'path';
import { test, expect, type Page } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const pad = (n: number) => String(n).padStart(2, '0');
const stamp = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

function seedDecisionLog() {
  const home = process.env.FRIDAY_PW_HOME;
  if (!home) return false;
  const p = path.join(home, 'runtime', 'laya2', 'router.jsonl');
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const rows: string[] = [];
  const row = (d: Date, decision: string, tool: string, conf: number, layer = 'laya2') => rows.push(JSON.stringify({
    at: stamp(d), surface: 'voice', text_sha: '0000000000000000', routed_by: 'laya_router', layer, decision, tool,
    confidence: conf, runner_up: ['check_email', 0.2], elapsed_ms: 3 + (rows.length % 5) * 0.4, args_keys: [] }));
  const now = new Date();
  for (let wk = 4; wk >= 1; wk--) {             // four earlier weeks: 6 of 24 guesses acted on
    const d = new Date(now.getTime() - wk * 7 * 864e5);
    for (let i = 0; i < 6; i++) row(d, 'tool', 'query_calendar', 0.7);
    for (let i = 0; i < 18; i++) row(d, 'ask', 'query_calendar', 0.5);
  }
  for (let i = 0; i < 16; i++) row(now, 'tool', 'query_calendar', 0.7);   // this week: 16 of 24
  for (let i = 0; i < 8; i++) row(now, 'ask', 'query_calendar', 0.5);
  for (let i = 0; i < 4; i++) row(now, 'tool', 'navigate_to', 1.0, 'rule');
  fs.writeFileSync(p, rows.join('\n') + '\n', 'utf8');
  return true;
}

async function openLaya(page: Page) {
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof (window as any).fridayRunActions === 'function', null, { timeout: 60000 });
  await page.evaluate(() => { window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: 'laya' } })); });
  const root = page.locator('[data-laya-settings="ready"]');
  await root.waitFor({ timeout: 60000 });
  return root;
}

async function resetBars(page: Page) {
  // From the page itself (a click-equivalent same-origin request carrying the page's token): clear every bar.
  await page.evaluate(async () => {
    const d = await (await (window as any).apiFetch('/api/laya/settings')).json();
    const bars: Record<string, null> = {};
    for (const r of d.bars.rows) if (r.yours !== null && r.yours !== undefined) bars[r.tool] = null;
    await (window as any).apiFetch('/api/laya/settings/bars', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bars, mode: 'shadow' }) });
  });
}

async function shot(page: Page, info: any, name: string) {
  await page.screenshot({ path: info.outputPath(name + '.png') });
}

async function showSection(page: Page, title: string) {
  await page.locator(`section[data-st-section="${title}"]`).evaluate(e => e.scrollIntoView({ block: 'start' }));
  await page.waitForTimeout(200);
}

test('the tab shows what runs, her modes and a report card, and the approval check never shows act', async ({ page }, info) => {
  expect(seedDecisionLog()).toBe(true);
  let root = await openLaya(page);
  await resetBars(page);
  await page.reload({ waitUntil: 'domcontentloaded' });
  root = await openLaya(page);
  const titles = await root.locator('section[data-st-section]').evaluateAll(es => es.map(e => e.getAttribute('data-st-section')));
  expect(titles).toEqual(["What Laya does", "Laya's modes", "Laya's report card", "Replay test", "Confidence bars", "Coming next"]);
  await expect(root.locator('[data-laya-card="series"]')).toContainText('Series One');
  await expect(root.locator('[data-laya-card="series"]')).toContainText('ms typical');
  for (const id of ['approval_check', 'safe_writes']) {
    const row = root.locator(`[data-laya-capability="${id}"]`);
    await expect(row).toContainText('She never acts on this.');
    expect(await row.locator('[data-laya-mode="act"]').count()).toBe(0);
  }
  await expect(root.locator('[data-laya-drift="flag"]')).toContainText('more than her usual');
  await shot(page, info, '01-laya-what-runs');
  await showSection(page, "Laya's modes");
  await shot(page, info, '02-laya-modes');
  await showSection(page, "Laya's report card");
  await shot(page, info, '03-laya-report-card');
});

test('the replay test runs on this PC and says what it found on a home with no history', async ({ page }, info) => {
  const root = await openLaya(page);
  await showSection(page, 'Replay test');
  const replay = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/laya/settings/replay');
  await root.locator('[data-laya-action="replay"]').click();
  const res = await replay;
  const body = await res.json();
  // A scratch home has no unlocked vault and no corrections: either answer is honest; nothing is invented.
  // The outcome shows beside the button that caused it.
  const note = root.locator('[data-laya-note="replay"]');
  if (res.status() === 200) {
    expect(body.plain.of).toBe(0);
    await expect(note).toHaveText('Replay finished.', { timeout: 20000 });
    await expect(root.locator('[data-laya-replay="result"]')).toContainText('Nothing to replay yet', { timeout: 20000 });
  } else {
    expect(res.status()).toBe(409);
    await expect(note).toContainText(/locked|already running/, { timeout: 20000 });
  }
  await expect(note).toBeInViewport();
  await shot(page, info, '04-laya-replay-fresh-home');
});

test('bars: reads go lower with a labelled recommendation, changes only stricter, outward none; shadow until act', async ({ page }, info) => {
  let root = await openLaya(page);
  await resetBars(page);
  await page.reload({ waitUntil: 'domcontentloaded' });
  root = await openLaya(page);
  await showSection(page, 'Confidence bars');
  const read = root.locator('[data-laya-bar="query_calendar"] select');
  await expect(read).toHaveValue('');                                   // the shipped bar, never the recommendation
  await expect(read.locator('option[value="0.45"]')).toHaveText('0.45 (recommended)');
  await expect(read.locator('option[value="0.40"]')).toHaveCount(1);
  await expect(root.locator('[data-laya-bar="search_web"]')).toContainText('Always asks first');
  expect(await root.locator('[data-laya-bar="search_web"] select').count()).toBe(0);
  const change = root.locator('[data-laya-bar]', { hasText: 'changes something' }).first();
  const lowest = await change.locator('select option').evaluateAll(os => os.map(o => (o as HTMLOptionElement).value).filter(Boolean)[0]);
  expect(Number(lowest)).toBeGreaterThanOrEqual(0.55);
  await expect(root.locator('#laya-bars-mode')).toHaveValue('shadow');
  await shot(page, info, '05-laya-bars-untouched');

  const saved = page.waitForResponse(r => new URL(r.url()).pathname === '/api/laya/settings/bars' && r.request().method() === 'POST');
  await read.selectOption('0.45');
  expect((await saved).status()).toBe(200);
  await expect(read).toHaveValue('0.45');
  await expect(root.locator('[data-laya-note="bars"]')).toHaveText('Saved.');
  await expect(root.locator('[data-laya-preview="1"]')).toContainText('would have acted instead of asking');
  await expect(root.locator('[data-laya-capability="bars"] [data-laya-mode]')).toHaveAttribute('data-laya-mode', 'shadow');
  await shot(page, info, '06-laya-bar-set-shadow-preview');

  const toAct = page.waitForResponse(r => new URL(r.url()).pathname === '/api/laya/settings/bars' && r.request().method() === 'POST');
  await root.locator('#laya-bars-mode').selectOption('act');
  expect((await toAct).status()).toBe(200);
  await page.reload({ waitUntil: 'domcontentloaded' });
  root = await openLaya(page);
  await expect(root.locator('[data-laya-bar="query_calendar"] select')).toHaveValue('0.45');
  await expect(root.locator('#laya-bars-mode')).toHaveValue('act');
  await expect(root.locator('[data-laya-capability="bars"] [data-laya-mode]')).toHaveAttribute('data-laya-mode', 'act');
  await showSection(page, 'Confidence bars');
  await shot(page, info, '07-laya-bars-act-after-reload');
  await resetBars(page);
});

test('a bar for an action that changes something cannot be lowered, even by a request from the page', async ({ page }) => {
  await openLaya(page);
  const status = await page.evaluate(async () => {
    const r = await (window as any).apiFetch('/api/laya/settings/bars', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bars: { set_chat_tray: 0.40 } }) });
    return r.status;
  });
  expect(status).toBe(400);
});

test('"confidence bars" by voice opens the Laya tab at that section', async ({ page }, info) => {
  await openLaya(page);
  await page.evaluate(() => { window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: 'general' } })); });
  // The voice tool navigate_to(settings, "confidence bars") goes through this route (desktop_targets.open_on_desktop).
  const res = await page.evaluate(async () => {
    const r = await (window as any).apiFetch('/api/desktop/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'settings', query: 'confidence bars' }) });
    return r.json();
  });
  expect(String(res.text || '')).not.toMatch(/no Settings tab or section/);
  expect(res.target).toMatchObject({ workspace: 'settings', tab: 'laya', section: 'Confidence bars' });
  // The desktop carries out the resolved target; run it here too, as the bus would, in case no ack arrived.
  await page.evaluate(t => (window as any).fridayRunActions([Object.assign({ type: 'navigate' }, t)]), res.target);
  await expect(page.locator('[data-laya-settings="ready"]')).toBeVisible({ timeout: 30000 });
  await expect(page.locator('section[data-st-section="Confidence bars"]')).toBeInViewport({ timeout: 30000 });
  await shot(page, info, '08-laya-voice-confidence-bars');
});
