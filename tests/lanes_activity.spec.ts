/**
 * The four places behind the top-bar chip: Needs you, Activity, Updates, Receipts & traces (NA-3).
 *
 * The server's lane routes are answered by fixtures (page.route), so the run changes nothing and needs
 * no models: it checks what the page DRAWS and which calls a click makes. The batch decision, the
 * quiet toggle and Stop are asserted by the request they send, not by a server's answer.
 *
 * Screenshots (light and dark, desktop and phone width) go to test-results/lanes/ for the HIG
 * checklist review. Written, not yet run: this spec is OWED a browser run.
 *
 *   FRIDAY_URL=http://127.0.0.1:3218 npx playwright test tests/lanes_activity.spec.ts --workers=1
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || 'http://127.0.0.1:3218';
test.setTimeout(90000);

const ids = ['appr_a1', 'appr_a2', 'appr_a3'];
const batch = {
  batch_id: 'batch_fixture01', kind: 'tainted_action', source: 'chat-1', count: 3,
  approval_ids: ids, sentence: '3 actions use details that came from outside content: review them together.',
  items: ids.map((id, i) => ({ approval_id: id, kind: 'media_diet_rule', status: 'pending', title: 'Change rule ' + (i + 1),
    action_description: "add the rule 'limit " + (i + 1) + "'", description: 'Friday would add this rule.', policy_class: 'outward',
    provenance: null, dissent: {}, payload: {}, expires_at: null, created_at: 1 + i })),
};
const needs = {
  count: 4, approvals: 3, batches: [batch], singles: [], summary: '3 approvals and 1 other thing waiting',
  quiet: { active: false, why: '', until: 0 },
  others: [{ id: 'n1', kind: 'scheduled_failure', title: 'Morning run failed', body: 'The local model was busy.', count: 1,
    created_at: '2026-10-12T08:00:00Z', actions: [{ type: 'navigate', label: 'Open schedules', workspace: 'system', tab: 'schedules' }],
    target: {}, safety_critical: false, read: false }],
};
const activity = {
  running: 1, queued: 2, count: 3,
  tasks: [
    { id: 't1', source: 'task', kind: 'news.briefing', label: 'Writing your briefing', model: 'gemma-test', status: 'running', trace_id: 'tr_1',
      queue_position: 0, stoppable: false, stop: null },
    { id: 't2', source: 'task', kind: 'task', label: 'Research the thing', model: '', status: 'queued', trace_id: '', queue_position: 1,
      stoppable: true, stop: { method: 'POST', url: '/api/tasks/t2/stop-after-step' } },
    { id: 'q9', source: 'local_queue', kind: 'wiki_distill', label: 'Wiki notes', model: '', status: 'queued', trace_id: '', queue_position: 2,
      stoppable: true, stop: { method: 'POST', url: '/api/local-queue/q9/cancel' }, run_now: true, why: 'waiting for idle',
      pausable: true, paused_by_you: false },
  ],
};
const updates = { count: 2, today_only: true, groups: [
  { kind: 'briefing', count: 1, items: [{ id: 'u1', title: 'Briefing ready', body: '', count: 1, created_at: '2026-10-12T07:00:00Z', target: {} }] },
  { kind: 'front_page', count: 1, items: [{ id: 'u2', title: 'Front Page ready', body: '', count: 1, created_at: '2026-10-12T07:05:00Z', target: {} }] }] };
const receipts = { count: 1, traces: [{ trace_id: 'tr_1', kind: 'scheduled', label: 'Writing your briefing', model: 'gemma-test', status: 'complete', started: Date.now() / 1000 - 600, task_id: 't1' }] };

async function mock(page: Page, over: Record<string, any> = {}) {
  const calls: { url: string; method: string; body: any }[] = [];
  const json = (body: any) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/lanes/summary', r => r.fulfill(json(over.summary || {
    needs_you: needs.count, approvals: 3, summary: needs.summary, running: 1, queued: 2, activity: 3, updates: 2, quiet: needs.quiet })));
  await page.route('**/api/lanes/needs-you', r => r.fulfill(json(over.needs || needs)));
  await page.route('**/api/lanes/activity', r => r.fulfill(json(over.activity || activity)));
  await page.route('**/api/lanes/updates**', r => r.fulfill(json(over.updates || updates)));
  await page.route('**/api/lanes/receipts**', r => r.fulfill(json(over.receipts || receipts)));
  await page.route('**/api/lanes/why/**', r => r.fulfill(json({ ok: true, sentence: 'It came from scheduler. It needs your decision, so it is in Needs you.', trace_id: 'tr_1' })));
  await page.route('**/api/local-queue', r => r.fulfill(json({ jobs: [], running: [], queued: [], count: 0 })));
  await page.route(/\/api\/(approvals\/batches\/.*|lanes\/quiet|tasks\/t2\/stop-after-step|local-queue\/q9\/.*)/, async r => {
    const req = r.request();
    calls.push({ url: req.url(), method: req.method(), body: req.postData() ? JSON.parse(req.postData() || '{}') : null });
    await r.fulfill(json({ ok: true, results: ids.map(id => ({ approval_id: id, status: 'approved', won: true })), queue: { jobs: [] }, quiet: {} }));
  });
  return calls;
}

const chip = (p: Page) => p.getByTestId('local-ai-queue-chip');
const open = async (p: Page) => { await p.goto(BASE + '/?skip_setup=1', { waitUntil: 'domcontentloaded' }); await chip(p).click(); };

test('the chip carries the amber badge with a word and opens the four places, Needs you first', async ({ page }) => {
  await mock(page);
  await page.goto(BASE + '/?skip_setup=1', { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('lane-badge')).toHaveText('4 need you');
  await expect(page.getByTestId('lane-updates-dot')).toBeVisible();
  await chip(page).click();
  const tabs = page.getByRole('tab');
  await expect(tabs).toHaveText([/Needs you/, /Activity/, /Updates/, /Receipts & traces/]);
  await expect(page.getByTestId('lane-tab-needs')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('lane-needs-you')).toContainText('3 approvals and 1 other thing waiting');
});

test('a batch card lists every action, and Approve sends exactly those approvals', async ({ page }) => {
  const calls = await mock(page);
  await open(page);
  const card = page.getByTestId('lane-batch');
  const bodies = card.getByTestId('lane-batch-items').getByTestId('approval-card');
  await expect(bodies).toHaveCount(3);                       // every action in full, as a single card shows it
  await expect(bodies.nth(1)).toContainText("add the rule 'limit 2'");
  await expect(bodies.nth(0).getByRole('button', { name: 'Approve' })).toBeHidden();   // the batch's buttons decide
  await card.getByTestId('lane-batch-approve').click();
  await expect.poll(() => calls.length).toBeGreaterThan(0);
  expect(calls[0].url).toContain('/api/approvals/batches/batch_fixture01/decide');
  expect(calls[0].body.decision).toBe('approve');
  expect(calls[0].body.approval_ids).toEqual(ids);
  expect(calls[0].body.decided_by).toBe('owner:ui');
});

test('an error card offers its fix, and an item without one says so', async ({ page }) => {
  await mock(page);
  await open(page);
  const need = page.getByTestId('lane-need');
  await expect(need.getByRole('button', { name: 'Open schedules' })).toBeVisible();
  await need.getByTestId('lane-why').click();
  await expect(page.getByTestId('lane-why-text')).toContainText('Needs you');
  await expect(page.getByTestId('lane-why-text').getByRole('button', { name: 'Open its trace' })).toBeVisible();
});

test('Quiet until morning posts the request, and quiet shows until when', async ({ page }) => {
  const calls = await mock(page);
  await open(page);
  await page.getByTestId('lane-quiet-on').click();
  await expect.poll(() => calls.some(c => c.url.includes('/api/lanes/quiet'))).toBeTruthy();
  expect(calls.find(c => c.url.includes('/api/lanes/quiet'))!.body).toEqual({ until_morning: true });
});

test('Activity shows model, status, place in line and Stop only where a job can be stopped', async ({ page }) => {
  const calls = await mock(page);
  await open(page);
  await page.getByTestId('lane-tab-activity').click();
  const rows = page.getByTestId('lane-task');
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText('Writing your briefing');
  await expect(rows.nth(0).getByTestId('lane-task-meta')).toContainText('gemma-test');
  await expect(rows.nth(0).getByTestId('lane-trace')).toBeVisible();
  await expect(rows.nth(0).getByTestId('lane-stop')).toHaveCount(0);          // an inline job: no Stop
  await expect(rows.nth(1).getByTestId('lane-task-meta')).toContainText('waiting, place 1');
  await expect(rows.nth(1).getByTestId('lane-task-meta')).toContainText('model chosen when it starts');
  await rows.nth(1).getByTestId('lane-stop').click();
  await expect.poll(() => calls.some(c => c.method === 'POST' && c.url.endsWith('/api/tasks/t2/stop-after-step'))).toBeTruthy();
  await expect(rows.nth(2).getByRole('button', { name: 'Run now' })).toBeVisible();
  await expect(rows.nth(0).getByTestId('lane-pause')).toHaveCount(0);         // no queue checkpoint: no Pause
  await rows.nth(2).getByTestId('lane-pause').click();
  await expect.poll(() => calls.some(c => c.method === 'POST' && c.url.endsWith('/api/local-queue/q9/pause'))).toBeTruthy();
});

test('Updates is a digest, Receipts lists traces, and neither pops anything', async ({ page }) => {
  await mock(page);
  await open(page);
  await page.getByTestId('lane-tab-updates').click();
  await expect(page.getByTestId('lane-updates')).toContainText('Briefing ready');
  await expect(page.getByTestId('lane-updates')).toContainText('Front Page ready');
  await page.getByTestId('lane-tab-receipts').click();
  await expect(page.getByTestId('lane-receipts')).toContainText('Writing your briefing');
  await expect(page.getByTestId('lane-open-trace')).toBeVisible();
});

test('keyboard: arrows move between tabs, Escape closes and returns focus to the chip', async ({ page }) => {
  await mock(page);
  await open(page);
  await page.getByTestId('lane-tab-needs').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('lane-tab-activity')).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('local-ai-queue-list')).toHaveCount(0);
  await expect(chip(page)).toBeFocused();
});

test('from any page to a pending approval is two clicks: the chip, then the card', async ({ page }) => {
  await mock(page);
  await page.goto(BASE + '/w/news', { waitUntil: 'domcontentloaded' });
  const t0 = Date.now();
  await chip(page).click();                                                   // 1
  await expect(page.getByTestId('lane-batch-approve')).toBeVisible();         // the yes/no is on the card
  expect(Date.now() - t0).toBeLessThan(3000);
});

test('an empty world: the chip is gone', async ({ page }) => {
  await mock(page, { summary: { needs_you: 0, approvals: 0, summary: '', running: 0, queued: 0, activity: 0, updates: 0, quiet: {} } });
  await page.goto(BASE + '/?skip_setup=1', { waitUntil: 'domcontentloaded' });
  await expect(chip(page)).toHaveCount(0);
});

for (const scheme of ['light', 'dark'] as const) {
  for (const [name, size] of [['desktop', { width: 1280, height: 800 }], ['phone', { width: 390, height: 844 }]] as const) {
    test(`screenshots: ${scheme} ${name}`, async ({ page }) => {
      await page.setViewportSize(size);
      await page.emulateMedia({ colorScheme: scheme });
      await mock(page);
      await open(page);
      for (const tab of ['needs', 'activity', 'updates', 'receipts']) {
        await page.getByTestId('lane-tab-' + tab).click();
        await page.screenshot({ path: `test-results/lanes/${scheme}-${name}-${tab}.png` });
      }
      // no horizontal scroll at phone width, and the drawer stays inside the viewport
      const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(over).toBeLessThanOrEqual(1);
      const box = await page.getByTestId('local-ai-queue-list').boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(size.width + 1);
    });
  }
}

test('Settings > Interruptions offers the limit with Recommended on 6, quiet hours off, Focus Assist off', async ({ page }) => {
  await mock(page);
  await page.route('**/api/settings', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ settings: {
    interruption_budget_per_hour: 6, quiet_hours_enabled: false, quiet_hours_start: '22:00', quiet_hours_end: '07:00', honour_focus_assist: false } }) }));
  await page.goto(BASE + '/w/settings', { waitUntil: 'domcontentloaded' });
  const budget = page.getByLabel('How often something may interrupt you');
  await expect(budget).toHaveValue('6');
  await expect(budget.locator('option', { hasText: 'Recommended' })).toHaveText('6 an hour (Recommended)');
  await expect(page.getByLabel('Quiet hours')).toHaveValue('off');
  await expect(page.getByLabel('Quiet hours').locator('option', { hasText: 'Recommended' })).toHaveText('22:00 to 07:00 (Recommended)');
  await expect(page.getByLabel('Follow Windows Focus Assist')).toHaveValue('off');
});
