/**
 * Settings -> General -> Local address, in a browser.
 *
 * The card explains the one security decision in plain words and shows the
 * certificate's thumbprint so the person can compare it with the one Windows
 * shows. The trust step itself is answered by this test (page.route), never by
 * the server: nothing here can reach Windows' certificate store.
 *
 *   FRIDAY_URL=http://localhost:3221 PW_CHANNEL=msedge npx playwright test tests/local_address_settings.spec.ts --workers=1
 *
 * Expects the test server's local_address to be host agent.pwtest, serve true,
 * https_port 3243, http_port 3280 (a scratch Friday home), and resolves
 * agent.pwtest inside this browser only.
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || 'http://localhost:3221';
const HTTP_NAMED = process.env.FRIDAY_NAMED_HTTP || 'http://agent.pwtest:3280';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.use({ viewport: { width: 1366, height: 900 }, launchOptions: { args: ['--host-resolver-rules=MAP agent.pwtest 127.0.0.1'] } });
test.setTimeout(180000);

async function prepare(page: Page) {
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await page.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET'
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"needs_prompt":false}' }) : r.continue());
  // The Windows steps are answered here and nowhere else.
  await page.route('**/api/local-address/trust', r => r.fulfill({ status: 500, body: 'this test never lets the trust step reach the server' }));
  await page.route('**/api/local-address/untrust', r => r.fulfill({ status: 500, body: 'never reaches the server' }));
  await page.route('**/api/local-address/hosts-entry', r => r.fulfill({ status: 500, body: 'never reaches the server' }));
}

async function card(page: Page) {
  await page.goto(BASE + '/?workspace=settings');
  const c = page.locator('[data-testid="local-address"]');
  await c.waitFor({ timeout: 90000 });
  await c.scrollIntoViewIfNeeded();
  return c;
}

test('the card says where Friday lives and what is missing, in plain words', async ({ page }) => {
  await prepare(page);
  const c = await card(page);
  await expect(c).toContainText('agent.pwtest');
  await expect(c).toContainText('This PC knows the name');
  await expect(c).toContainText('Windows needs one line in its hosts file');
  await expect(c).toContainText('Secure (the padlock)');
  await expect(c).toContainText('browsers switch off the microphone and camera');
  await expect(c).toContainText('Google sign-in');
  await expect(c).toContainText('Returns to ' + BASE);
  await page.screenshot({ path: test.info().outputPath('local-address-card.png') });
});

test('the consent step explains the change and shows the thumbprint Windows will show', async ({ page }) => {
  await prepare(page);
  const st = await (await page.request.get(BASE + '/api/local-address')).json();
  const expected = String(st.ca.sha1).replace(/:/g, '').replace(/(.{8})(?=.)/g, '$1 ');
  const c = await card(page);
  await c.getByRole('button', { name: 'Enable secure local address' }).click();
  const consent = c.locator('.la-consent');
  await expect(consent).toBeVisible();
  await expect(consent).toContainText(st.ca.subject);
  await expect(consent).toContainText('can vouch for agent.pwtest and nothing else');
  await expect(consent).toContainText('only your Windows account');
  await expect(consent).toContainText('undo it here at any time');
  await expect(consent).toContainText('Firefox keeps its own list');
  await expect(c.getByTestId('la-thumbprint')).toHaveText(expected);
  await page.screenshot({ path: test.info().outputPath('local-address-consent.png') });
  // "Not now" changes nothing
  await consent.getByRole('button', { name: 'Not now' }).click();
  await expect(consent).toHaveCount(0);
});

test('while Windows asks, the card says so; then it reports the answer', async ({ page }) => {
  await prepare(page);
  const real = await (await page.request.get(BASE + '/api/local-address')).json();
  let phase = 'idle';
  const job = () => phase === 'idle' ? { kind: null, state: 'idle', message: '', started: 0, finished: 0 }
    : phase === 'waiting' ? { kind: 'trust', state: 'waiting', message: 'Waiting for you to answer Windows’ Security Warning.', started: Date.now() / 1000, finished: 0 }
      : { kind: 'trust', state: 'cancelled', message: 'Windows did not add the certificate (the warning was answered No). Nothing changed.', started: 0, finished: Date.now() / 1000 };
  await page.route('**/api/local-address?**', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...real, job: job() }) }));
  await page.route('**/api/local-address', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...real, job: job() }) }));
  let trustCalls = 0;
  await page.route('**/api/local-address/trust', r => {
    trustCalls++;
    expect(r.request().method()).toBe('POST');
    expect(r.request().headers()['x-friday-token'], 'the page\'s own token').toBeTruthy();
    phase = 'waiting';
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, job: job() }) });
  });
  const c = await card(page);
  await c.getByRole('button', { name: 'Enable secure local address' }).click();
  await c.getByRole('button', { name: 'Trust Friday’s certificate' }).click();
  await expect(c.locator('.la-job.waiting')).toContainText('Waiting for you to answer');
  expect(trustCalls).toBe(1);
  phase = 'cancelled';
  await expect(c.locator('.la-job.cancelled')).toContainText('Nothing changed', { timeout: 10000 });
});

test('on the plain-http name, voice says why the microphone is unavailable and where it works', async ({ page }) => {
  await prepare(page);
  await page.goto(HTTP_NAMED + '/w/news');
  await page.waitForSelector('[data-standalone="news"]', { timeout: 90000 });
  const why = await page.evaluate(() => ({ secure: window.isSecureContext, reason: (window as any).fridayMicBlockedReason() }));
  expect(why.secure).toBe(false);
  expect(why.reason).toContain('not secure');
  expect(why.reason).toContain('http://localhost:');
  // and on localhost itself nothing is in the way
  await page.goto(BASE + '/w/news');
  await page.waitForSelector('[data-standalone="news"]', { timeout: 90000 });
  expect(await page.evaluate(() => (window as any).fridayMicBlockedReason())).toBe('');
});

test('tab links move to the proven address only from bare localhost', async ({ page }) => {
  await prepare(page);
  const proven = 'https://agent.pwtest:3243';
  const linkFrom = async (url: string) => {
    await page.goto(url);
    await page.waitForFunction(() => typeof (window as any).fridayWorkspaceTabUrl === 'function', null, { timeout: 90000 });
    return page.evaluate(o => {
      (window as any).__FRIDAY_LOCAL_ADDRESS__ = { host: 'agent.pwtest', origin: o, secure: true };
      return (window as any).fridayWorkspaceTabUrl('news', { tab: 'feed' });
    }, proven);
  };
  expect(await linkFrom(BASE + '/w/news')).toBe(proven + '/w/news?tab=feed');       // localhost -> the name
  expect(await linkFrom(HTTP_NAMED + '/w/news')).toBe('/w/news?tab=feed');          // already on the name: stays
  const onIp = BASE.replace('localhost', '127.0.0.1');
  expect(await linkFrom(onIp + '/w/news')).toBe(proven + '/w/news?tab=feed');       // 127.0.0.1 is bare loopback too
});
