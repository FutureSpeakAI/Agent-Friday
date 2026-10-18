/**
 * Show a Settings tab the way Friday's own actions do (the friday:settings-tab event), and wait until `ready`
 * is on screen.
 *
 * The event is only heard once Settings has mounted its listener. On a fresh page load that can come after
 * fridayRunActions exists, and an event sent too early is simply lost: the tab never opens and the test waits
 * out its whole budget. The event is idempotent, so it is sent again until `ready` shows.
 */
import { expect, type Locator, type Page } from '@playwright/test';

export async function showSettingsTab(page: Page, tab: string, ready: Locator, timeout = 60_000) {
  await expect(async () => {
    await page.evaluate(t => { window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: t } })); }, tab);
    await expect(ready).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout });
}
