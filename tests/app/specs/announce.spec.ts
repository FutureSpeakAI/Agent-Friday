// What is waiting for you is heard as well as seen (north star 33.3-6; WCAG 4.1.3 status messages).
//
// A screen reader says when an action starts waiting for approval: the page keeps a polite live region, and the
// approvals popup sets into it what has just arrived. One synthetic card is fed through the approvals stream.
import { test, expect } from '../fixtures';
import { bootApp } from '../harness';

const now = Math.floor(Date.now() / 1000);
const CARD = { approval_id: 'appr_announce', kind: 'media_diet_rule', status: 'pending', title: 'Add the rule limit news to 20 minutes',
  action_description: 'Friday would add the rule.', description: 'A synthetic request.', payload: {}, created_at: now,
  expires_at: now + 3600, policy_class: 'outward' };

test('a card that starts waiting is said, not only shown', async ({ page }) => {
  await page.route('**/api/approvals/events', r => r.fulfill({
    status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
    body: 'retry: 600000\n\ndata: ' + JSON.stringify({ type: 'snapshot', pending: [CARD], quiet: [] }) + '\n\n',
  }));
  await bootApp(page);
  const polite = page.locator('#fr-announce-polite');
  await expect(polite).toHaveAttribute('aria-live', 'polite');
  await expect(polite).toHaveText(/is waiting for your approval: Add the rule limit news to 20 minutes\.$/, { timeout: 30_000 });
  await expect(page.locator('#fr-announce-assertive')).toHaveAttribute('role', 'alert');
});