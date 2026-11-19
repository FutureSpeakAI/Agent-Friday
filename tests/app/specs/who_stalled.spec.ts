import { test, expect } from '@playwright/test';
import { BASE } from '../harness';
import { whoStalled } from '../helpers/who_stalled';

// F7: when a Settings-walk click times out, the row must say whether the SERVER or the PAGE stopped answering.

test.describe('who stalled', () => {
  test('a server that does not answer is named, and the page is said to answer', async ({ page }) => {
    await page.setContent('<p>ok</p>');
    // port 9 (discard) refuses at once, which stands in for a server that does not answer
    const said = await whoStalled(page, 'http://127.0.0.1:9', 1500);
    expect(said).toContain('server did NOT answer');
    expect(said).toContain('page answered');
  });

  test('a server and a page that both answer are both said to', async ({ page }) => {
    await page.setContent('<p>ok</p>');
    const said = await whoStalled(page, BASE, 8000);
    expect(said).toContain('server answered');
    expect(said).toContain('page answered');
  });

  test('a page whose script never returns is named', async ({ page }) => {
    await page.setContent('<p>x</p>');
    // block the page's main thread, then ask
    void page.evaluate(() => { const end = Date.now() + 6000; while (Date.now() < end) { /* spin */ } }).catch(() => undefined);
    const said = await whoStalled(page, 'http://127.0.0.1:9', 1500);
    expect(said).toContain('page did NOT answer');
  });
});
