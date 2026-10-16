// CI-ONLY visual evidence for the round-2 field fixes (never merged): screenshots
// of each changed surface on a scratch server, saved under test-results/app.
// Data the scratch home cannot have (a reply from a local model) is served by
// page.route; everything else is the scratch server's own.
import { test, expect, type Page } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 950 } });

async function shot(page: Page, name: string) {
  await page.waitForTimeout(1200);
  await page.screenshot({ path: test.info().outputPath(name + '.png'), fullPage: false });
}

async function boot(page: Page, path: string) {
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ initialized: true }) }));
  await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
}

test('Models card: the model that answered, and the prediction when they differ', async ({ page }) => {
  await page.route('**/api/intelligence*', async route => {
    const resp = await route.fetch();
    const body = await resp.json();
    if (body && body.soup) {
      body.soup.answer = {
        answering: { model: 'bonsai2:27b', label: 'bonsai2:27b', where: 'local', provider: 'this machine',
          at: Date.now() / 1000 - 120, workspace: 'calendar', basis: 'answered', why: 'the model chosen for this chat (bonsai2:27b)' },
        next: Object.assign({}, body.soup.now, { basis: 'predicted' }), disagrees: true,
        note: 'Your routing mode is Cloud only, but the last reply (in calendar) came from bonsai2:27b on this machine: the model chosen for this chat (bonsai2:27b). A model chosen for that chat or workspace keeps it local; change it in that chat\'s model picker.' };
    }
    await route.fulfill({ response: resp, json: body });
  });
  await boot(page, '/w/settings');
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: 'intelligence' } })));
  await page.getByText('Answering you').first().waitFor({ timeout: 60000 });
  await page.getByText('Answering you').first().scrollIntoViewIfNeeded();
  await expect(page.getByText('Next message, by your settings').first()).toBeVisible();
  await shot(page, 'models-card-one-truth');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
});

async function seedGrant(page: Page) {
  return page.evaluate(async () => {
    const r = await fetch('/api/governance/send-grants', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'recipients', recipients: ['editor@example.test', '@desk.example.test'], note: 'CI evidence' }) });
    return r.status;
  });
}

test('Privacy: pre-approved sends', async ({ page }) => {
  await boot(page, '/w/settings');
  test.info().annotations.push({ type: 'seed', description: String(await seedGrant(page)) });
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: 'privacy' } })));
  const panel = page.getByTestId('send-grants');
  await panel.waitFor({ timeout: 60000 });
  await panel.scrollIntoViewIfNeeded();
  await shot(page, 'settings-preapproved-sends');
});

test('System: approvals card lists pre-approved sends', async ({ page }) => {
  await boot(page, '/w/system');
  await seedGrant(page);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  const t = page.getByText(/pre-approv/i).first();
  try { await t.waitFor({ timeout: 30000 }); await t.scrollIntoViewIfNeeded(); } catch (_) {}
  await shot(page, 'system-approvals');
});

test('Studio podcasts: the hosts\' names', async ({ page }) => {
  await boot(page, '/w/studio?view=podcasts');
  try { await page.evaluate(() => (window as any).fridayNavigate && (window as any).fridayNavigate({ workspace: 'studio', view: 'podcasts' })); } catch (_) {}
  await page.waitForTimeout(3000);
  const t = page.getByText(/hosts' names/i).first();
  try { await t.waitFor({ timeout: 30000 }); await t.scrollIntoViewIfNeeded(); } catch (_) {}
  await shot(page, 'studio-podcast-hosts');
});

test('News: trusted outlets and honest trust labels', async ({ page }) => {
  await boot(page, '/w/news');
  try { await page.getByRole('button', { name: /⚙\s*Customize/ }).first().click({ timeout: 20000 }); } catch (_) {}
  const t = page.getByText('Trusted outlets').first();
  try { await t.waitFor({ timeout: 30000 }); await t.scrollIntoViewIfNeeded(); } catch (_) {}
  await shot(page, 'news-trusted-outlets');
  try { await page.getByRole('button', { name: /Sources/ }).first().click({ timeout: 15000 }); } catch (_) {}
  await shot(page, 'news-sources');
});
