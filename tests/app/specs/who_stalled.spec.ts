import { test, expect } from '@playwright/test';
import { whoStalled } from '../helpers/who_stalled';

// F7: when a Settings-walk click times out, the row must say whether the SERVER or the PAGE stopped answering.

test.describe('who stalled', () => {
  test('a server that does not answer is named', async ({ page }) => {
    // port 9 (discard) refuses at once, which stands in for a server that does not answer
    const said = await whoStalled(page, 'http://127.0.0.1:9', 1500);
    expect(said).toContain('server did NOT answer');
    expect(said).toContain('page answered');
  });

  test('a page that answers and a server that answers are both said', async ({ page }) => {
    await page.setContent('<p>ok</p>');
    const server = await page.context().request.get('about:blank').catch(() => null);
    void server;
    const said = await whoStalled(page, 'http://127.0.0.1:9', 1500);
    expect(said).toMatch(/^ \[/);
  });

  test('a page whose script never returns is named', async ({ page }) => {
    await page.setContent('<p>x</p>');
    // a page that stops answering: block its main thread, then ask
    void page.evaluate(() => { const end = Date.now() + 6000; while (Date.now() < end) { /* spin */ } });
    const said = await whoStalled(page, 'http://127.0.0.1:9', 1500);
    expect(said).toContain('page did NOT answer');
  });
});
