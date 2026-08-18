/**
 * READABILITY — the measurable half of "sloppy and bad".
 *
 * A vision judge reported that two buttons on the home screen were hard to
 * read. This measures the same thing in code, so the complaint becomes a
 * number. Vision finds it; this proves it and keeps it from coming back.
 */
import { test } from '@playwright/test';
import { bootApp, waitForSettled } from '../harness';
import { assertReadableContrast } from '../liveness';

test('text on the home screen can actually be read', async ({ page }) => {
  await bootApp(page);
  await waitForSettled(page).catch(() => { /* judged on whatever is on screen */ });
  await page.waitForTimeout(1500);
  await assertReadableContrast(page, 'The home screen', 4.5);
});
