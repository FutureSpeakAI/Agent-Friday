/**
 * The top bar's Activity button stays in sight while something waits for the owner.
 *
 * At common Windows laptop widths a pending approval adds a wide "waiting for you" chip, and the
 * top bar used to tuck the Activity button behind the "⋯" overflow to make room, so the way to
 * what is waiting was hidden exactly when it mattered. Now, while anything waits (an approval, a
 * card that needs the owner, a held send), Activity never collapses: other chips go first
 * (static/friday_responsive_topbar.js, packTopbar's `keep`; the node test
 * scripts/check_responsive_topbar.cjs checks the rule without a browser).
 *
 * One pending approval is seeded by answering the approvals stream the top bar listens to
 * (/api/approvals/events) and the Needs-you summary it polls (/api/lanes/summary) with
 * synthetic data; nothing is written to any Friday.
 */
import { test, expect } from '../fixtures';
import { bootApp } from '../harness';
import { expectVisible } from '../vision';

const CARD = {
  approval_id: 'appr_synthetic_topbar',
  kind: 'external_message',
  status: 'pending',
  title: 'Send email to sam@example.test',
  action_description: 'Send mail as you.\n\nFrom: me@example.test\nTo: sam@example.test\nSubject: Synthetic\n\nHello.',
  description: 'This leaves your machine.',
  payload: { handler: 'gmail_send', to: ['sam@example.test'] },
  created_at: Math.floor(Date.now() / 1000),
  expires_at: Math.floor(Date.now() / 1000) + 3600,
  policy_class: 'gated',
};

for (const [width, height] of [[1366, 768], [1280, 720]] as const) {
  test(`Activity stays out of the overflow with an approval waiting at ${width}x${height}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height });
    await page.route('**/api/approvals/events', route => route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: 'data: ' + JSON.stringify({ type: 'snapshot', pending: [CARD], quiet: [] }) + '\n\n',
    }));
    await page.route('**/api/lanes/summary', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ needs_you: 1, approvals: 1, summary: 'one thing needs you', running: 0, queued: 0,
                             activity: 0, updates: 0, held_sends: 0, quiet: { active: false } }),
    }));
    await bootApp(page);
    const bar = page.getByTestId('friday-top-bar');
    await expect(bar).toHaveAttribute('data-ready', 'true');
    // The approval is waiting: its chip is in the bar or in the pullout.
    await expect(page.getByTestId('ws-tab-approvals')).toHaveCount(1, { timeout: 15000 });
    const activityCell = page.locator('.friday-topbar-cell[data-kind="activity"]');
    await expect(activityCell, 'The top bar has no Activity button to keep in sight').toHaveCount(1);
    await expect(activityCell).toHaveAttribute('data-keep', 'true');
    await expect(activityCell, 'Activity was tucked behind the overflow while something waits')
      .toHaveAttribute('data-overflow', 'false');
    const button = activityCell.getByRole('button', { name: 'Activity' });
    await expect(button).toBeVisible();
    // Inside the bar, not inside the (closed) pullout.
    expect(await activityCell.evaluate(el => !el.closest('#friday-topbar-overflow'))).toBe(true);
    const box = await button.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width, 'Activity is off the screen').toBeTruthy();
    await page.screenshot({ path: info.outputPath(`activity-kept-${width}x${height}.png`) });
    await bar.screenshot({ path: info.outputPath(`top-bar-${width}x${height}.png`) });
    // The attributes above can all be right while Windows fonts push the chip out of sight (LESSONS).
    // With FRIDAY_VISION_CHECK=1 and a local vision model, a model also looks; otherwise this notes why not.
    await expectVisible(bar, 'a button labelled "Activity" in this top bar, with its label readable');
  });
}
