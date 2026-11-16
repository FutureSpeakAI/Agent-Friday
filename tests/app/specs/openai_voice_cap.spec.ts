// Settings > Voice: the owner's monthly limit for OpenAI voice is a real control. A limit typed there is saved
// (read back from the saved settings, and after a reload), a blank means no limit, and a bad value is refused.
// Held by the same setting the bridge reads (services/openai_live_bridge._cap). SCRATCH server only.
import { test, expect, type Page } from '../fixtures';
import { showSettingsTab } from '../helpers/settings_tab';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });

const CAP = '[data-openai-voice-cap="1"] input';

async function savedCap(page: Page) {
  return page.evaluate(async () => (await (await (window as any).apiFetch('/api/settings')).json()).settings.voice_openai_monthly_cap_usd);
}

async function openVoice(page: Page) {
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof (window as any).fridayRunActions === 'function', null, { timeout: 60000 });
  await showSettingsTab(page, 'voice', page.locator(CAP));
}

test('the OpenAI voice monthly limit is kept, cleared with a blank, and survives a reload', async ({ page }, info) => {
  await openVoice(page);
  const field = page.locator(CAP);
  await expect(field).toHaveAttribute('placeholder', 'No limit');

  await field.fill('25');
  await field.blur();
  await expect.poll(() => savedCap(page)).toBe(25);
  await page.screenshot({ path: info.outputPath('openai-cap-25.png') });

  await openVoice(page);
  await expect(page.locator(CAP)).toHaveValue('25');

  await page.locator(CAP).fill('');
  await page.locator(CAP).blur();
  await expect.poll(() => savedCap(page)).toBeNull();
  await page.screenshot({ path: info.outputPath('openai-cap-blank.png') });
});

test('a negative limit is refused and the saved one stays', async ({ page }) => {
  await openVoice(page);
  await page.locator(CAP).fill('10');
  await page.locator(CAP).blur();
  await expect.poll(() => savedCap(page)).toBe(10);
  await page.locator(CAP).fill('-5');
  await page.locator(CAP).blur();
  await expect(page.locator(CAP)).toHaveValue('10');
  expect(await savedCap(page)).toBe(10);
  await page.locator(CAP).fill('');
  await page.locator(CAP).blur();
  await expect.poll(() => savedCap(page)).toBeNull();
});
