/**
 * Career: the job scanner view shows open, recently seen jobs, and closed ones sit in the archive with the date
 * they closed.
 *
 * SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The job lists are synthetic and are
 * served by the spec, so no real job, tracker or feed is read. Frames (recent.png, archive.png) are saved for a
 * person to look at; the assertions are the measurable half: the closed job is not among the recent cards, it is
 * in the archive with its closing date, and no card's text is cut off.
 */
import { test, expect } from '../fixtures';
import { bootApp, openWorkspace } from '../harness';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1280, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const RECENT = [
  { key: 'k1', job_id: 'job_1', title: 'Staff Engineer, Platform', company: 'Acme Robotics', location: 'Remote', score: 0.81,
    status: 'open', first_seen: '2026-11-18T09:00:00', last_seen: '2026-11-21T09:00:00' },
  { key: 'k2', job_id: 'job_2', title: 'Data Engineer', company: 'Gamma Works', location: 'Denver, CO', score: 0.44,
    status: 'open', first_seen: '2026-11-19T09:00:00', last_seen: '2026-11-20T09:00:00' },
];
const ARCHIVE = [
  { key: 'k3', title: 'Closed Back In July', company: 'Beta Labs', location: 'Remote', status: 'closed',
    closed_at: '2026-07-20T00:00:00', close_reason: 'closing date passed', last_seen: '2026-07-02T09:00:00' },
  { key: 'k4', title: 'Also Closed In July, With A Very Long Title That Should Not Run Off The Card', company: 'Delta Systems',
    location: 'Remote', status: 'expired', closed_at: '2026-07-05T00:00:00', close_reason: 'not seen for 30 days',
    last_seen: '2026-06-05T09:00:00' },
];

test('recent lists only open jobs and the archive keeps the closed ones with their date', async ({ page }, info) => {
  await page.route('**/api/career/jobs?view=recent*', r => r.fulfill(json({ status: 'ok', view: 'recent', jobs: RECENT })));
  await page.route('**/api/career/jobs?view=archive*', r => r.fulfill(json({ status: 'ok', view: 'archive', jobs: ARCHIVE })));
  // Past first run: its own screen covers the app (as in headings.spec.ts).
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  test.setTimeout(180_000);
  await bootApp(page);
  await openWorkspace(page, 'career');
  await page.getByRole('button', { name: 'scanner' }).click();

  const recent = page.locator('[data-testid="career-recent-job"]');
  await expect(recent).toHaveCount(2);
  await page.screenshot({ path: info.outputPath('recent.png') });
  for (const text of await recent.allInnerTexts()) expect(text).not.toContain('Closed Back In July');

  const archived = page.locator('[data-testid="career-archived-job"]');
  await expect(archived).toHaveCount(2);
  await archived.first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('archive.png') });
  const closedOn = new Date('2026-07-20T00:00:00').toLocaleDateString();
  await expect(archived.first()).toContainText('closed');
  await expect(archived.first()).toContainText(closedOn);
  await expect(archived.nth(1)).toContainText('expired');

  const clipped = await page.evaluate(() => Array.from(document.querySelectorAll(
    '[data-testid="career-recent-job"], [data-testid="career-archived-job"]'))
    .some(card => (card as HTMLElement).scrollWidth > (card as HTMLElement).clientWidth + 1));
  expect(clipped, 'a job card is cut off').toBe(false);
});
