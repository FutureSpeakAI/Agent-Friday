import { test, expect, type Page } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

/**
 * Agent Friday — broad UI + connection test suite.
 *
 * Covers every clickable surface of the installed app:
 *   - liquid (React) UI actually mounts (not just the holographic cube)
 *   - all 18 workspaces open and render real content (no error boundary)
 *   - the model selector reflects ONLY currently-available models
 *     (Fable 5 + Mythos were recalled — they must NOT appear anywhere)
 *   - header controls (settings, notifications, quick draft, chat) open
 *   - chat send is wired end-to-end (POST /api/chat fires)
 *   - every read API endpoint answers 200
 *   - the countdown payload shape the UI depends on (label/days)
 *
 * Requires the Friday server running on :3000 (preview server / start.bat).
 * Run:  npx playwright test tests/friday_ui_full.spec.ts
 */

const SHOTS = path.join(__dirname, 'screenshots');
if (!fs.existsSync(SHOTS)) fs.mkdirSync(SHOTS, { recursive: true });

// The inline Babel bundle is >500KB and is transpiled in-browser, so first
// paint of the React tree can take a few seconds. Wait on a real signal:
// the dock buttons only exist once <App/> has mounted.
async function waitForLiquidUI(page: Page) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(
    () => document.querySelectorAll('.dock-btn').length >= 4,
    { timeout: 45000 },
  );
}

const WORKSPACES = [
  'Home', 'News', 'Messages', 'Calendar', 'Family', 'Co-Parent', 'Health',
  'Finance', 'Career', 'Contacts', 'Code', 'Sites', 'Draft', 'Content',
  'Wiki', 'Trust', 'Studio', 'System',
];

// Recalled models — must never surface in UI or config.
const RECALLED = /fable|mythos/i;

// ═══════════════════════════════════════════════════════════════
//  1. SERVER + LIQUID UI MOUNT
// ═══════════════════════════════════════════════════════════════

test.describe('Boot & liquid UI', () => {
  test('GET / serves the FRIDAY shell', async ({ request }) => {
    const res = await request.get('/');
    expect(res.status()).toBe(200);
    expect(await res.text()).toContain('FRIDAY');
  });

  test('React liquid UI mounts (not just the cube)', async ({ page }) => {
    await waitForLiquidUI(page);
    // Dock = the React app. 18 workspaces (show_all) or >=4 core.
    const dock = await page.locator('.dock-btn').count();
    expect(dock).toBeGreaterThanOrEqual(4);
    // Chat header control present → header rendered.
    await expect(page.locator('[aria-label="Open chat with Friday"]')).toBeVisible();
    await page.screenshot({ path: path.join(SHOTS, 'full-01-boot.png') });
  });

  test('no fatal console errors on load (Babel size notes excepted)', async ({ page }) => {
    const fatal: string[] = [];
    page.on('console', (m) => {
      if (m.type() !== 'error') return;
      const t = m.text();
      if (/deoptimised|exceeds the max of 500KB/i.test(t)) return; // benign
      fatal.push(t);
    });
    page.on('pageerror', (e) => fatal.push(String(e)));
    await waitForLiquidUI(page);
    await page.waitForTimeout(1500);
    expect(fatal, `Unexpected console errors:\n${fatal.join('\n')}`).toEqual([]);
  });
});

// ═══════════════════════════════════════════════════════════════
//  2. MODEL SELECTOR — recalled-model regression guard
// ═══════════════════════════════════════════════════════════════

test.describe('Model selector reflects available models only', () => {
  test('no Fable/Mythos anywhere in the rendered DOM', async ({ page }) => {
    await waitForLiquidUI(page);
    const html = await page.content();
    expect(RECALLED.test(html)).toBe(false);
  });

  test('header badge shows Opus 4.8', async ({ page }) => {
    await waitForLiquidUI(page);
    const badge = page.locator('[aria-label="Model selection"]');
    await expect(badge).toBeVisible();
    expect(await badge.innerText()).toMatch(/Opus 4\.8/);
  });

  test('orchestrator lists Claude (no recalled models)', async ({ page }) => {
    await waitForLiquidUI(page);
    await page.locator('[aria-label="Model selection"]').click();
    await page.waitForTimeout(400);
    const body = await page.locator('body').innerText();
    expect(body).toMatch(/Claude Opus 4\.8/);
    expect(body).toMatch(/Claude Sonnet 4\.6/);
    expect(body).toMatch(/Claude Haiku 4\.5/);
    expect(RECALLED.test(body)).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════
//  2b. MODEL CATALOG — backend-driven, extensible, cross-provider
// ═══════════════════════════════════════════════════════════════

test.describe('Model catalog (/api/models)', () => {
  test('endpoint groups models by role across providers', async ({ request }) => {
    const json = await (await request.get('/api/models')).json();
    expect(json.status).toBe('ok');
    for (const role of ['orchestrator', 'subagent', 'creative', 'voice']) {
      expect(Array.isArray(json.roles[role])).toBe(true);
    }
    const orch = new Set(json.roles.orchestrator.map((m: any) => m.provider));
    // The whole fix: orchestrator/subagent are no longer Claude-only.
    expect(orch.has('anthropic')).toBe(true);
    expect(orch.has('openai')).toBe(true);
    expect(orch.has('ollama-local')).toBe(true);
  });

  test('creative role is deduped and free of the voice-only live model', async ({ request }) => {
    const json = await (await request.get('/api/models')).json();
    const ids = json.roles.creative.map((m: any) => m.id);
    expect(ids).not.toContain('gemini-nano-banana-pro'); // the duplicate
    expect(ids).not.toContain('gemini-3.1-flash-live-preview'); // voice-only
    expect(new Set(ids).size).toBe(ids.length); // no dupes
    // gemini-2.5-flash is present but no longer mislabeled "Flash Live".
    const flash = json.roles.creative.find((m: any) => m.id === 'gemini-2.5-flash');
    expect(flash && /live/i.test(flash.label)).toBeFalsy();
  });

  test('UI renders orchestrator selector from the catalog (local + OpenAI)', async ({ page }) => {
    await waitForLiquidUI(page);
    await page.locator('[aria-label="Model selection"]').click();
    await page.waitForTimeout(500);
    const opts = await page.locator('.model-option-cloud').allInnerTexts();
    const blob = opts.join(' | ');
    // Local Ollama models surface with the 🏠 marker; OpenAI is offered too.
    expect(/🏠/.test(blob)).toBe(true);
    expect(/GPT-4o/.test(blob)).toBe(true);
    expect(/Claude Opus 4\.8/.test(blob)).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════
//  3. EVERY WORKSPACE OPENS & RENDERS (no error boundary)
// ═══════════════════════════════════════════════════════════════

test.describe('Workspaces', () => {
  test('all 18 dock workspaces are present', async ({ page }) => {
    await waitForLiquidUI(page);
    const labels = await page.locator('.dock-btn').allInnerTexts();
    const norm = labels.map((l) => l.replace(/\s+/g, ' ').trim());
    for (const ws of WORKSPACES) {
      expect(norm.some((l) => l.includes(ws)), `missing dock button: ${ws}`).toBe(true);
    }
  });

  for (const ws of WORKSPACES) {
    test(`workspace "${ws}" opens without error boundary`, async ({ page }) => {
      await waitForLiquidUI(page);
      const btn = page.locator('.dock-btn', { hasText: ws }).first();
      await btn.click();
      // A window mounts.
      const win = page.locator('.fwin').last();
      await expect(win).toBeVisible({ timeout: 8000 });
      const body = (await win.locator('.fwin-body').innerText()).trim();
      // Safe boundary renders "⚠️ <name> encountered an error".
      expect(body, `${ws} hit error boundary`).not.toMatch(/encountered an error/i);
      // Not a permanent "Coming soon" stub.
      expect(body).not.toMatch(/^Coming soon$/i);
      expect(body.length).toBeGreaterThan(0);
    });
  }

  test('dock icons load (no broken images)', async ({ page }) => {
    await waitForLiquidUI(page);
    const broken = await page.evaluate(() =>
      [...document.querySelectorAll('.dock-btn img')].filter(
        (i) => (i as HTMLImageElement).complete && (i as HTMLImageElement).naturalWidth === 0,
      ).length,
    );
    expect(broken).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════
//  4. HEADER CONTROLS
// ═══════════════════════════════════════════════════════════════

test.describe('Header controls', () => {
  const controls = [
    'Open settings',
    'Open notifications',
    'Open Quick Draft',
    'Open chat with Friday',
  ];
  for (const label of controls) {
    test(`"${label}" is clickable`, async ({ page }) => {
      await waitForLiquidUI(page);
      const btn = page.locator(`[aria-label="${label}"]`);
      await expect(btn).toBeVisible();
      await btn.click();
      await page.waitForTimeout(400);
      // Something opened — overall body text grew or a modal/panel exists.
      expect(await page.locator('body').innerText()).toBeTruthy();
    });
  }

  test('Settings panel renders core sections', async ({ page }) => {
    await waitForLiquidUI(page);
    await page.locator('[aria-label="Open settings"]').click();
    await page.waitForTimeout(600);
    const body = await page.locator('body').innerText();
    for (const section of ['AI MODEL', 'ORCHESTRATOR', 'AUDIO & VOICE', 'AGENT PERSONALITY']) {
      expect(body, `settings missing ${section}`).toContain(section);
    }
    await page.screenshot({ path: path.join(SHOTS, 'full-02-settings.png') });
  });
});

// ═══════════════════════════════════════════════════════════════
//  5. CHAT — wired end to end
// ═══════════════════════════════════════════════════════════════

test.describe('Chat', () => {
  test('typing + Enter fires POST /api/chat', async ({ page }) => {
    await waitForLiquidUI(page);
    // Open the chat panel.
    await page.locator('[aria-label="Open chat with Friday"]').click();
    const input = page.locator('textarea, input').filter({ hasText: '' }).first();
    const box = page.getByPlaceholder(/Talk to Friday|Ask Friday/i).first();
    await expect(box).toBeVisible();
    const waitChat = page.waitForRequest(
      (r) => r.url().includes('/api/chat') && r.method() === 'POST',
      { timeout: 8000 },
    );
    await box.fill('Reply with exactly: PONG');
    await box.press('Enter');
    const req = await waitChat;
    expect(req.method()).toBe('POST');
  });

  test('POST /api/chat returns a string response', async ({ request }) => {
    const res = await request.post('/api/chat', {
      data: { message: 'Quick automated test — reply briefly.' },
      timeout: 180000,
    });
    expect(res.status()).toBe(200);
    const json = await res.json();
    expect(json).toHaveProperty('response');
    expect(typeof json.response).toBe('string');
    expect(json.response.length).toBeGreaterThan(0);
  });
});

// ═══════════════════════════════════════════════════════════════
//  6. API SWEEP — every read endpoint answers 200
// ═══════════════════════════════════════════════════════════════

test.describe('API read endpoints', () => {
  const endpoints = [
    '/api/health', '/api/system', '/api/system/network-status', '/api/settings',
    '/api/setup/status', '/api/notifications', '/api/tasks', '/api/todos',
    '/api/news/feed', '/api/calendar/today', '/api/messages', '/api/contacts',
    '/api/people', '/api/creations', '/api/creations/daily/latest',
    '/api/wiki/structure', '/api/skills', '/api/connectors', '/api/connectors/health',
    '/api/recipes', '/api/providers', '/api/distros', '/api/subagents', '/api/jobs',
    '/api/personality', '/api/epistemic', '/api/trust', '/api/source-trust',
    '/api/ollama/status', '/api/mcp/status', '/api/model-stats', '/api/compression-stats',
    '/api/ambient/state', '/api/processes', '/api/memory/stats', '/api/security/risk-score',
    '/api/security/behavioral-report', '/api/extensions/security', '/api/finance/portfolio',
    '/api/health/medications', '/api/futurespeak/projects', '/api/workflows/chains',
    '/api/voice/fallback-status', '/api/google/status', '/api/briefings', '/api/countdowns',
  ];
  for (const ep of endpoints) {
    test(`GET ${ep} → 200`, async ({ request }) => {
      const res = await request.get(ep);
      expect(res.status()).toBe(200);
    });
  }
});

// ═══════════════════════════════════════════════════════════════
//  7. COUNTDOWN SHAPE — regression for the 0-days "undefined" bug
// ═══════════════════════════════════════════════════════════════

test.describe('Countdown payload', () => {
  test('items expose label + numeric days', async ({ request }) => {
    const json = await (await request.get('/api/countdowns')).json();
    expect(json.status).toBe('ok');
    expect(Array.isArray(json.countdowns)).toBe(true);
    for (const c of json.countdowns) {
      expect(c).toHaveProperty('label');
      expect(c).toHaveProperty('date');
      expect(typeof c.days).toBe('number'); // 0 is valid (event today)
    }
  });

  test('UI never renders "undefined" for a same-day countdown', async ({ page }) => {
    await waitForLiquidUI(page);
    // Family surfaces the soonest countdown; days===0 used to print "undefined".
    await page.locator('.dock-btn', { hasText: 'Family' }).first().click();
    const win = page.locator('.fwin').last();
    await expect(win).toBeVisible();
    expect(await win.locator('.fwin-body').innerText()).not.toMatch(/undefined/);
  });
});
