// The System workspace flags a scheduled job that is not keeping up: one that has not succeeded within twice its
// interval (overdue) and a run with no progress past a timeout (stalled). SCRATCH server only (FRIDAY_BASE must be set;
// the live :3000 is refused). The health route is stubbed with two synthetic flagged jobs, nothing else is, so this
// proves the card the page draws from the route's answer; the route's own answer, from a missed and a hung run, is
// proved against the real scheduler in tests/api/test_schedule_health_route.py. Each state saves a screenshot to the
// run's output folder.
import { test, expect } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1280, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

const FLAGGED = [
  { id: 'sch_a', name: 'Sample hourly sync', health: 'stalled', reason: 'Running for 12 minutes with no progress; it may be stuck.' },
  { id: 'sch_b', name: 'Sample daily digest', health: 'overdue', reason: 'Has not succeeded for 3 days (expected every 1 day).' },
];

test('a missed run and a hung run are flagged in the System workspace', async ({ page }, info) => {
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/schedules/health', r => r.fulfill(json({ status: 'ok', flagged: FLAGGED, checked: 5 })));
  await page.goto(`${BASE}/w/system`, { waitUntil: 'domcontentloaded' });
  const card = page.getByTestId('sys-schedule-health');
  await expect(card).toBeVisible({ timeout: 60000 });
  await expect(card).toContainText('Scheduled jobs needing attention');
  const rows = card.getByTestId('sys-schedule-flag');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toHaveAttribute('data-health', 'stalled');
  await expect(rows.nth(0)).toContainText('Sample hourly sync');
  await expect(rows.nth(0)).toContainText('no progress');
  await expect(rows.nth(1)).toHaveAttribute('data-health', 'overdue');
  await expect(rows.nth(1)).toContainText('Has not succeeded for 3 days');
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('system-schedule-health-flagged.png') });
});

test('nothing is drawn when every job is on time', async ({ page }, info) => {
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/schedules/health', r => r.fulfill(json({ status: 'ok', flagged: [], checked: 5 })));
  const answered = page.waitForResponse('**/api/schedules/health', { timeout: 60000 });
  await page.goto(`${BASE}/w/system`, { waitUntil: 'domcontentloaded' });
  await answered;
  await expect(page.getByText('Weekly Self-Improvement')).toBeVisible({ timeout: 60000 });   // the workspace has loaded
  await expect(page.getByTestId('sys-schedule-health')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('system-schedule-health-clear.png') });
});
