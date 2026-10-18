// Connect your services, in a real page, on a fresh scratch home with zero keys.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The list is the server's own
// /api/setup/connections for an empty home: nothing is stubbed except the setup-conversation state used to show
// the same screen inside setup. Proves: the five categories render; nothing is preselected and nothing reads as
// "needs attention"; every sign-up link is https and opens in a new tab; Test connection on an unconnected
// service says so in words with a fix; "connect OpenRouter" (the navigate action voice sends) lands on that card
// with its masked field focused; the MCP form and the coming-soon list are there. Each step saves a screenshot to
// the run's output folder (uploaded by CI) so the screens can be looked at.
//
// ONE APP LOAD FOR THE SETTINGS TESTS. Booting the desktop page is the expensive part (tens of seconds on the CI
// runner), and each test used to pay it before its first assertion. The five Settings tests now share one page,
// opened once per worker; before each test the panel is put back the way a fresh visit shows it (the local-only
// choice undone through its own button, the panel shown again, scrolled to the top), and every step opens a card
// only if it is closed, so no test depends on another's order or leftovers. Playwright traces the shared page as it
// does every page. The setup test needs its own stubbed routes, so it keeps its own page.
import { test, expect, type Page } from '../fixtures';
import type { BrowserContext, Locator } from '@playwright/test';
import { settle } from '../helpers/settle';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
const VIEWPORT = { width: 1440, height: 900 };
test.use({ viewport: VIEWPORT });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const PANEL = '[data-testid="connect-services"]';

async function shot(page: Page, info: any, name: string, target?: any) {
  const path = info.outputPath(name + '.png');
  if (target) await target.screenshot({ path });
  else await page.screenshot({ path });
}

/** Open a card's details if they are closed (the button toggles). */
async function expand(card: Locator) {
  const toggle = card.getByRole('button', { name: /(Show|Hide) details for/ }).first();
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
}

test.describe('Connect your services in Settings (one app load)', () => {
  let ctx: BrowserContext;
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(240_000);
    // Playwright traces this context too (the config's trace: 'retain-on-failure').
    ctx = await browser.newContext({ viewport: VIEWPORT });
    page = await ctx.newPage();
    await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
    await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof (window as any).fridayRunActions === 'function', null, { timeout: 120_000 });
  });

  test.beforeEach(async () => {
    // Show the panel (again), as a visit from the rail does, and put it back the way a fresh visit shows it.
    await page.evaluate(() => { window.dispatchEvent(new CustomEvent('friday:settings-tab', { detail: { tab: 'connections' } })); });
    const panel = page.locator(PANEL).first();
    await panel.waitFor({ timeout: 60_000 });
    await expect(panel.locator('.sc-cat-title').first()).toBeVisible({ timeout: 30_000 });
    const back = panel.getByRole('button', { name: 'Show cloud options again' });
    if (await back.isVisible()) {
      await back.click();
      await expect(panel.getByRole('button', { name: "I'll just use local AI" })).toBeVisible();
    }
    await panel.locator('.sc-cat-title').first().evaluate(e => e.scrollIntoView({ block: 'start' }));
    await settle(page, { root: PANEL, quietMs: 200, timeout: 3000 });
  });

  test.afterAll(async () => { await ctx.close(); });

  test('the screen lists every category, preselects nothing and is honest on a fresh install', async ({}, info) => {
    const panel = page.locator(PANEL).first();
    const titles = await panel.locator('.sc-cat-title').allInnerTexts();
    expect(titles.slice(0, 5)).toEqual(['AI model providers', 'Creative and media', 'Search and web', 'Accounts', 'Add any MCP server']);
    await expect(panel.getByRole('button', { name: "I'll just use local AI" })).toBeVisible();
    // Nothing chosen for the person, nothing connected, nothing "needs attention" on a home with no keys.
    expect(await panel.locator('input[type=checkbox]:checked, input[type=radio]:checked').count()).toBe(0);
    expect(await panel.locator('.sc-pill.needs_attention').count()).toBe(0);
    expect(await panel.locator('.sc-pill.connected').count()).toBe(0);
    await expect(panel.locator('.sc-rec').first()).toHaveText('Recommended');
    await shot(page, info, '01-connect-your-services-overview');
    // One frame per category: the scrolling pane clips an element screenshot.
    const heads = panel.locator('.sc-cat-title');
    for (let i = 0; i < await heads.count(); i++) {
      await heads.nth(i).evaluate(e => e.scrollIntoView({ block: 'start' }));
      await settle(page, { root: PANEL, quietMs: 150, timeout: 2000 });
      await shot(page, info, '01-category-' + (i + 1));
    }
  });

  test('a card shows access, steps, cost, a verified link and a live test', async ({}, info) => {
    const card = page.locator(PANEL).first().locator('[data-connection="provider:openrouter"]');
    await expand(card);
    await expect(card).toContainText('What it can access');
    await expect(card).toContainText('How to connect');
    await expect(card).toContainText('Cost:');
    const key = card.locator('input[type=password]');
    await expect(key).toHaveAttribute('autocomplete', 'new-password');
    const link = card.locator('a.sc-link').first();
    await expect(link).toHaveAttribute('href', /^https:\/\//);
    await expect(link).toHaveAttribute('target', '_blank');
    await shot(page, info, '02-openrouter-card', card);
    await card.getByRole('button', { name: 'Test connection' }).click();
    const result = card.locator('.sc-test');
    await expect(result).toHaveAttribute('data-test-state', 'not_connected', { timeout: 30000 });
    await expect(result).toContainText('How to fix:');
    await shot(page, info, '03-openrouter-test-not-connected', card);
  });

  test('"connect OpenRouter" by voice opens that card with its masked field focused', async ({}, info) => {
    // The exact action the voice route sends through the desktop bus (desktop_targets.resolve_settings).
    await page.evaluate(() => (window as any).fridayRunActions([{ type: 'navigate', workspace: 'settings', tab: 'connections', connect: 'provider:openrouter' }]));
    const card = page.locator('[data-connection="provider:openrouter"]');
    await expect(card.locator('.sc-item-body')).toBeVisible({ timeout: 30000 });
    await expect.poll(() => page.evaluate(() => {
      const a = document.activeElement as HTMLInputElement | null;
      const host = a ? a.closest('[data-connection]') : null;
      return a ? a.type + '|' + (host ? host.getAttribute('data-connection') : '') : '';
    }), { timeout: 10000 }).toBe('password|provider:openrouter');
    await shot(page, info, '04-voice-connect-openrouter-focused');
  });

  test('accounts, local runtimes, the MCP form and the coming-soon list', async ({}, info) => {
    const panel = page.locator(PANEL).first();
    const google = panel.locator('[data-connection="google"]');
    await expand(google);
    await expect(google).toContainText('What it can access');
    await shot(page, info, '05-google-account-card', google);
    const ollama = panel.locator('[data-connection="local:ollama"]');
    await expand(ollama);
    await expect(ollama.locator('input[type=text]')).toHaveValue('http://localhost:11434');
    await shot(page, info, '06-ollama-card', ollama);
    const mcp = panel.locator('[data-connection="mcp:add"]');
    await expand(mcp);
    await mcp.getByRole('button', { name: /Fill the form with Context7/ }).click();
    await expect(mcp.locator('input[type=url]')).toHaveValue('https://mcp.context7.com/mcp');
    await shot(page, info, '07-add-mcp-server', mcp);
    const soon = panel.locator('details.sc-soon').first();
    if (!(await soon.evaluate(d => (d as HTMLDetailsElement).open))) await soon.locator('summary').click();
    await expect(soon).toContainText('Not built in yet');
    await shot(page, info, '08-coming-soon', soon.locator('xpath=ancestor::section[1]'));
  });

  test("I'll just use local AI is an answer, and the cloud options come back on request", async ({}, info) => {
    const panel = page.locator(PANEL).first();
    await panel.getByRole('button', { name: "I'll just use local AI" }).click();
    await expect(panel.getByTestId('connect-services-said')).toContainText('Local AI it is');
    await expect(panel.getByRole('button', { name: 'Show cloud options again' })).toBeVisible();
    await shot(page, info, '09-local-ai-only');
    await panel.getByRole('button', { name: 'Show cloud options again' }).click();
    await expect(panel.getByRole('button', { name: "I'll just use local AI" })).toBeVisible();
  });
});

test('setup shows the same screen as its connect step', async ({ page }, info) => {
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/setup-chat/rerun', r => r.fulfill(json({ ok: true })));
  const groups = [['welcome', 'Hello'], ['connect', 'Connect'], ['knowledge', 'Your files'], ['finish', 'Finish']]
    .map(([id, label], i) => ({ id, label, first: id, state: i === 0 ? 'done' : i === 1 ? 'current' : 'todo' }));
  await page.route('**/api/setup-chat/state', r => r.fulfill(json({
    ok: true, stage: 'connect', consent_done: true, completed: false, agent_name: 'Friday',
    transcript: [{ role: 'friday', stage: 'connect',
      text: 'Now the services you might want. Every one is optional; each card says what connecting it unlocks and exactly what it can reach.' }],
    groups, prompt: { stage: 'connect', input: 'none', chips: [{ label: 'Done for now', value: 'done' }],
      card: 'connections', placeholder: '', recommended: false },
  })));
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof (window as any).fridayRunActions === 'function', null, { timeout: 60000 });
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('friday:setup-chat-rerun')));
  const card = page.locator('.sc-card', { hasText: 'Connect your services' }).first();
  await expect(card).toBeVisible({ timeout: 60000 });
  await expect(card.getByTestId('connect-services')).toBeVisible({ timeout: 30000 });
  await shot(page, info, '10-setup-connect-step');
});
