// Settings > Privacy & Data: standing grants are listed in plain words and can be revoked; a declined offer can be
// stopped for good. The page is given a seeded list (the real list is produced by services/standing_grants and held
// by tests/unit/test_standing_grants_settings.py); this spec holds what the page shows and what its buttons send.
// SCRATCH server only.
import { test, expect, type Page } from '../fixtures';
import { showSettingsTab } from '../helpers/settings_tab';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });

const NOW = Math.floor(Date.now() / 1000);
const LIST = {
  grants: [{
    grant_id: 'grant_demo000001', tool: 'draft_email', to: 'to=ann@example.com', pressed_by: 'owner:ui',
    device: 'this_pc', made_at: NOW - 3 * 86400, ends_at: NOW + 27 * 86400, uses_used: 4, uses_max: 50,
  }],
  declined: [{ pattern: 'abc123', tool: 'create_task', at: NOW - 86400 }],
  stopped: [{ pattern: 'def456', tool: 'update_task' }],
};

async function openPrivacy(page: Page, sent: string[]) {
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await page.route('**/api/governance/standing-grants', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(LIST) }));
  await page.route('**/api/governance/standing-grants/stop-offering', async r => {
    sent.push('stop ' + r.request().postData());
    await r.fulfill({ status: 200, contentType: 'application/json', body: '{"stopped":true}' });
  });
  await page.route('**/api/governance/grants/grant_demo000001', async r => {
    sent.push(r.request().method() + ' ' + r.request().url().split('/').pop());
    await r.fulfill({ status: 200, contentType: 'application/json', body: '{"revoked":true}' });
  });
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof (window as any).fridayRunActions === 'function', null, { timeout: 60000 });
  await showSettingsTab(page, 'privacy', page.locator('[data-testid="standing-grants"]'));
}

test('a standing grant is listed with what, to whom, who pressed, uses and end; Revoke sends the revoke', async ({ page }, info) => {
  const sent: string[] = [];
  await openPrivacy(page, sent);
  const row = page.locator('[data-testid="standing-grant"]');
  await expect(row).toHaveCount(1);
  await expect(row).toContainText('ann@example.com');
  await expect(row).toContainText('Pressed by you on this PC');
  await expect(row).toContainText('Used 4 of 50');
  await row.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('standing-grants-list.png') });
  await row.getByRole('button', { name: /Revoke the standing grant/ }).click();
  await expect.poll(() => sent).toContain('DELETE grant_demo000001');
});

test('a declined offer can be stopped for good, and a stopped one is listed', async ({ page }) => {
  const sent: string[] = [];
  await openPrivacy(page, sent);
  await expect(page.locator('[data-testid="stopped-offer"]')).toContainText('update_task');
  const declined = page.locator('[data-testid="declined-offer"]');
  await expect(declined).toContainText('create_task');
  await declined.getByRole('button', { name: "Don't offer again" }).click();
  await expect.poll(() => sent.join('|')).toContain('"pattern":"abc123"');
});
