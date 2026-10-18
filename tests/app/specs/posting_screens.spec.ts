// The posting screens, rendered: each new state is driven in a real page and looked at.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Synthetic posts go to the in-memory
// mock platform, which reaches no network. What is proved, one screen state at a time:
//  1. scheduling a post while the owner has switched the Content publisher off says the post won't go out, with
//     an "Open Workflows" button (the publisher's part of the answer is given in its real words: see below);
//  2. Workflows lists the Content publisher as on after it was switched on automatically, with the note saying
//     when and why, and its switch turns it off (the job's row is given in its real shape: see below);
//  3. "Check connection" on a connected account says nothing was posted;
//  4. a held post's Media card offers "Publish anyway…", and the confirm lists why it was held (the post and its
//     release are route-stubbed: nothing is released and nothing reaches a network);
//  5. an OAuth platform without the owner's developer app shows the client ID / secret entry, and YouTube says it
//     can't be connected yet;
//  6. the Connect-your-services card for an OAuth platform says what happened and never that a window opened.
// Every state is saved as a full-page screenshot in the run's output folder (the CI job uploads test-results/app)
// and attached to the report, so a person can look at it.
import { test, expect, type Page } from '../fixtures';
import type { BrowserContext, Locator, TestInfo } from '@playwright/test';
import { createHash } from 'crypto';
import * as fs from 'fs';
import { settle } from '../helpers/settle';
import { showSettingsTab } from '../helpers/settings_tab';
import { scratchName } from '../harness';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
const VIEWPORT = { width: 1440, height: 900 };
test.use({ viewport: VIEWPORT });

const PUBLISHER = 'sch_content_publisher';
const TOASTS = '#friday-toast-host [role="status"]';

/** A full-page screenshot, saved and attached. `focus` is scrolled into view; `hold` (a notice) is hovered, which
 *  keeps a Friday toast on screen until the picture is taken (fridayToast retains a hovered notice). */
async function shot(page: Page, info: TestInfo, name: string, focus?: Locator, hold?: Locator) {
  if (focus) await focus.scrollIntoViewIfNeeded().catch(() => { /* the screenshot still shows the page */ });
  if (hold) await hold.hover();
  await settle(page, { quietMs: 200, timeout: 2000 });
  if (hold) await expect(hold, 'the notice went away before it could be photographed').toBeVisible();
  const path = info.outputPath(name + '.png');
  await page.screenshot({ path, fullPage: true });
  await info.attach(name, { path, contentType: 'image/png' });
}

/** The setup overlay would cover every screen on a scratch home: the home is treated as set up. */
async function setUp(page: Page) {
  await page.route('**/api/setup/status', r => r.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ initialized: true }) }));
}

/** A call made from the page itself (same origin, the page's own token), as the app makes it. */
async function api(page: Page, method: string, path: string, body?: unknown): Promise<{ status: number; data: any }> {
  return page.evaluate(async ([m, p, b]) => {
    const f = (window as any).apiFetch || fetch;
    const init: any = { method: m, headers: { 'Content-Type': 'application/json' } };
    if (b !== null) init.body = JSON.stringify(b);
    const r = await f(p, init);
    let d: any = null;
    try { d = await r.json(); } catch { /* no body */ }
    return { status: r.status, data: d };
  }, [method, path, body === undefined ? null : body] as const);
}

/** The Media card id of a post (services/media_index._id_for("post", id)). */
const cardOf = (postId: string) => 'post:' + createHash('sha1').update('post|' + postId).digest('hex').slice(0, 12);

async function newPost(page: Page, what: string): Promise<{ id: string; card: string }> {
  const r = await api(page, 'POST', '/api/content/posts', {
    title: scratchName(what), body: 'A synthetic post for the posting screens check.', platforms: ['mock'] });
  expect(r.data && r.data.ok, 'creating a synthetic post failed: ' + JSON.stringify(r.data)).toBeTruthy();
  const id = r.data.post.id;
  return { id, card: r.data.media_card || cardOf(id) };
}

async function openApp(page: Page, path: string, ready: () => Promise<boolean>) {
  await page.goto(BASE + path, { waitUntil: 'domcontentloaded' });
  await expect.poll(ready, { timeout: 120_000, message: 'the page at ' + path + ' never became ready' }).toBe(true);
}

async function openCard(page: Page, card: string) {
  await openApp(page, '/w/media?card=' + encodeURIComponent(card),
    () => page.getByRole('button', { name: 'Back to Media' }).isVisible());
}

/** "YYYY-MM-DD HH:MM" a week from now, the form Media's Schedule… prompt takes. */
function aWeekFromNow(): string {
  const d = new Date(Date.now() + 7 * 86400_000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} 10:00`;
}

async function scheduleFromCard(page: Page) {
  page.once('dialog', d => d.accept(aWeekFromNow()));
  await page.getByRole('button', { name: 'Schedule…', exact: true }).click();
}

// The scratch server runs with FRIDAY_TESTING=1, under which server.py starts no background daemons: the
// scheduler seeds no built-in jobs and the publisher registers none, so the Content publisher job does not exist
// there. Its switching (on for a scheduled post, the owner's off winning) is proved against the real scheduler in
// tests/unit/test_content_publisher_job.py. These two tests check what a person SEES of it: the server's answers
// for that job are given here in the exact shape and words the server sends (services/workflow_overview
// _auto_enabled_note, services/publisher.ensure_publisher_running), and the screens are driven for real.
const AUTO_NOTE = (at: Date) => {
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][at.getMonth()];
  const p = (n: number) => String(n).padStart(2, '0');
  return `Switched on automatically on ${p(at.getDate())} ${mon} ${at.getFullYear()}, ${p(at.getHours())}:${p(at.getMinutes())}`
    + ' because a post was scheduled for a connected account. You can switch it off here.';
};
const OFF_NOTICE = "This post won't go out: the Content publisher is switched off. Turn it on in Workflows.";

test('Workflows lists the Content publisher as on after it was switched on automatically, says why, and its switch turns it off', async ({ page }, info) => {
  test.setTimeout(300_000);
  await setUp(page);
  let on = true;
  const patches: any[] = [];
  const row = () => ({
    schedule_id: PUBLISHER, name: 'Content publisher', when_text: 'Every 15 minutes', enabled: on,
    running: false, next_run: null, last_run: null, announces_finish: false, finish_notice: true,
    schedule_status: on ? 'scheduled' : null, status_reason: '', model: '', note: on ? AUTO_NOTE(new Date()) : '',
  });
  await page.route(u => u.pathname === '/api/workflows/overview', async route => {
    const res = await route.fetch();
    const d = await res.json();
    d.routines = (d.routines || []).filter((r: any) => r.schedule_id !== PUBLISHER).concat([row()]);
    await route.fulfill({ response: res, json: d });
  });
  await page.route(u => u.pathname === '/api/schedules/' + PUBLISHER, route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { status: 'ok', schedule: { id: PUBLISHER, enabled: on } } });
    const body = route.request().postDataJSON();
    patches.push(body);
    if (body && typeof body.enabled === 'boolean') on = body.enabled;
    return route.fulfill({ json: { status: 'ok', schedule: { id: PUBLISHER, enabled: on } } });
  });

  await openApp(page, '/w/workflows', () => page.getByText(/built-in routines/).first().isVisible());
  const routines = page.locator('details.wfx-disclosure').filter({ hasText: 'built-in routines' }).first();
  if (!(await routines.evaluate(d => (d as HTMLDetailsElement).open))) await routines.locator('summary').click();
  const line = routines.locator('.wfx-routine').filter({ has: page.locator('strong', { hasText: /^Content publisher$/ }) });
  await expect(line, 'Workflows does not list the Content publisher').toHaveCount(1);
  const sw = line.getByRole('switch', { name: 'Run Content publisher' });
  await expect(sw).toHaveAttribute('aria-checked', 'true');
  const note = line.getByTestId('wf-routine-note');
  await expect(note, 'Workflows does not say why the Content publisher is on').toBeVisible();
  await expect(note).toContainText('Switched on automatically on');
  await expect(note).toContainText('because a post was scheduled for a connected account. You can switch it off here.');
  await shot(page, info, 'posting-2a-workflows-publisher-on-with-note', line);

  await sw.click();
  await expect.poll(() => patches.length, { timeout: 15_000, message: 'the switch sent nothing' }).toBe(1);
  expect(patches[0]).toEqual({ enabled: false });
  await expect(sw).toHaveAttribute('aria-checked', 'false', { timeout: 15_000 });
  await expect(line.getByTestId('wf-routine-note')).toHaveCount(0);
  await shot(page, info, 'posting-2b-workflows-publisher-switched-off', line);
});

test('a post scheduled while the Content publisher is switched off says it will not go out, with Open Workflows', async ({ page }, info) => {
  test.setTimeout(300_000);
  await setUp(page);
  await openApp(page, '/w/media', () => page.evaluate(() => typeof (window as any).fridayToast === 'function'));
  const post = await newPost(page, 'owner-off');
  try {
    // The schedule really happens (the mock platform; nothing leaves); its answer carries what the publisher
    // check says when the owner has switched the job off.
    const cardPaths = new Set(['/api/media/' + encodeURIComponent(post.card), '/api/media/' + post.card]);
    let scheduled = 0;
    await page.route(u => cardPaths.has(u.pathname), async route => {
      if (route.request().method() !== 'PATCH') return route.continue();
      const res = await route.fetch();
      const d = await res.json();
      if (d && d.status === 'ok') {
        scheduled += 1;
        d.publisher = { on: false, auto_enabled: false, notice: OFF_NOTICE,
          action: { label: 'Open Workflows', target: { workspace: 'workflows' } } };
      }
      await route.fulfill({ response: res, json: d });
    });
    await openCard(page, post.card);
    await scheduleFromCard(page);
    await expect.poll(() => scheduled, { timeout: 30_000, message: 'the post was not scheduled' }).toBe(1);
    await expect(page.locator(TOASTS).filter({ hasText: /^Scheduled\./ })).toBeVisible({ timeout: 15_000 });
    const notice = page.locator(TOASTS).filter({ hasText: OFF_NOTICE });
    await expect(notice, 'no notice that the post will not go out').toBeVisible({ timeout: 15_000 });
    await expect(notice.getByRole('button', { name: 'Open Workflows' })).toBeVisible();
    await shot(page, info, 'posting-1-notice-publisher-off', undefined, notice);
  } finally {
    await api(page, 'DELETE', '/api/content/posts/' + encodeURIComponent(post.id)).catch(() => {});
  }
});

test('a held post offers Publish anyway…, and the confirm lists why it was held (stubbed: nothing is released)', async ({ page }, info) => {
  test.setTimeout(300_000);
  await setUp(page);
  await openApp(page, '/w/media', () => page.evaluate(() => typeof (window as any).fridayToast === 'function'));
  const post = await newPost(page, 'held');
  try {
    const reasons = [
      { platform: 'mock', error: 'the text may name a private place (synthetic hold)' },
      { platform: 'bluesky', error: 'a link points at this computer (synthetic hold)' },
    ];
    const releases: any[] = [];
    const cardPaths = new Set(['/api/media/' + encodeURIComponent(post.card), '/api/media/' + post.card]);
    // The card says it is held; the post's targets are HELD with their reasons; the release is answered here.
    await page.route(u => cardPaths.has(u.pathname), async route => {
      if (route.request().method() !== 'GET') return route.continue();
      const res = await route.fetch();
      const d = await res.json();
      if (d && d.card) Object.assign(d.card, { held: true, status: 'review', badges: ['held'] });
      await route.fulfill({ response: res, json: d });
    });
    await page.route(u => u.pathname === '/api/content/posts/' + post.id, async route => {
      if (route.request().method() !== 'GET') return route.continue();
      const res = await route.fetch();
      const d = await res.json();
      const real = (d && d.post && d.post.targets && d.post.targets[0]) || {};
      if (d && d.post) {
        d.post.status = 'HELD';
        d.post.targets = reasons.map((r, i) => ({ ...real, id: (real.id || 'tgt_synthetic') + '_' + i, platform: r.platform, status: 'HELD', error: r.error }));
      }
      await route.fulfill({ response: res, json: d });
    });
    await page.route(u => u.pathname === '/api/content/posts/' + post.id + '/release', route => {
      releases.push(route.request().postDataJSON());
      return route.fulfill({ json: { ok: true } });
    });

    await openCard(page, post.card);
    const button = page.getByTestId('md-release-held');
    await expect(button, "a held post's card has no Publish anyway… button").toBeVisible({ timeout: 30_000 });
    await expect(button).toHaveText('Publish anyway…');
    await shot(page, info, 'posting-4a-held-post-publish-anyway', button);

    let asked = '';
    page.once('dialog', d => { asked = d.message(); return d.accept(); });
    await button.click();
    await expect.poll(() => asked, { timeout: 15_000, message: 'no confirm was shown before releasing' }).not.toBe('');
    const path = info.outputPath('posting-4b-confirm-dialog.txt');
    fs.writeFileSync(path, asked, 'utf-8');
    await info.attach('posting-4b-confirm-dialog', { path, contentType: 'text/plain' });
    for (const r of reasons) expect(asked).toContain(r.platform + ': ' + r.error);
    expect(asked).toContain('Publish it anyway?');
    expect(asked).toContain('Its approval card still asks you before anything goes out.');

    await expect.poll(() => releases.length, { timeout: 15_000 }).toBe(1);
    expect(releases[0]).toEqual({ ack: true });
    const released = page.locator(TOASTS).filter({ hasText: 'Released. Its approval card asks you before it goes out.' });
    await expect(released).toBeVisible({ timeout: 15_000 });
    await shot(page, info, 'posting-4c-held-post-released-toast', undefined, released);
  } finally {
    await api(page, 'DELETE', '/api/content/posts/' + encodeURIComponent(post.id)).catch(() => {});
  }
});

test.describe('Settings, Connections: the publishing accounts (one app load)', () => {
  let ctx: BrowserContext;
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(240_000);
    ctx = await browser.newContext({ viewport: VIEWPORT });
    page = await ctx.newPage();
    await setUp(page);
    await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof (window as any).fridayRunActions === 'function', null, { timeout: 120_000 });
    await showSettingsTab(page, 'connections', channels().locator('.card').first());
  });

  test.afterAll(async () => { await ctx.close(); });

  /** The Publishing channels section (static/media_ws.js MediaChannelsSettings). */
  const channels = () => page.getByText(/^Where Media posts go\./).locator('xpath=..');
  const account = (label: string) => channels().locator('.card').filter({ has: page.getByText(label, { exact: true }) }).first();

  test('Check connection on a connected account says nothing was posted', async ({}, info) => {
    const card = account('Mock Platform');
    await expect(card).toContainText('Connected');
    await expect(card.getByRole('button', { name: 'Test post' })).toHaveCount(0);
    await card.scrollIntoViewIfNeeded();
    await card.getByRole('button', { name: 'Check connection' }).click();
    const said = page.locator(TOASTS).filter({ hasText: 'Nothing was posted.' });
    await expect(said).toBeVisible({ timeout: 30_000 });
    await expect(said).not.toContainText('Test post sent');
    await shot(page, info, 'posting-3-check-connection-nothing-posted', card, said);
  });

  test('an OAuth platform asks for its developer app, and YouTube says it cannot be connected yet', async ({}, info) => {
    const linkedin = account('LinkedIn');
    await linkedin.getByRole('button', { name: /Connect$/ }).click();
    const form = linkedin.getByTestId('cp-oauth-app-form');
    await expect(form, 'no developer-app entry on the LinkedIn card').toBeVisible({ timeout: 30_000 });
    await expect(form.getByRole('textbox', { name: 'Client ID' })).toBeVisible();
    await expect(form.getByLabel('Client secret')).toHaveAttribute('type', 'password');
    await expect(form.locator('code')).toContainText('/api/content/platforms/linkedin/callback');
    await expect(form.getByRole('button', { name: 'Save and sign in' })).toBeVisible();
    const needs = page.locator(TOASTS).filter({ hasText: 'LinkedIn needs your own developer app' });
    await expect(needs).toBeVisible({ timeout: 15_000 });
    await expect(needs).toContainText('then press Save and sign in.');
    // The redirect address is shown whole: it wraps inside the card instead of running past its edge.
    const code = form.locator('code');
    const fits = await code.evaluate(el => {
      const card = el.closest('.card') as HTMLElement;
      const r = el.getBoundingClientRect(), c = card.getBoundingClientRect();
      return r.width > 0 && r.right <= c.right + 0.5;
    });
    expect(fits, 'the redirect address runs past the edge of the LinkedIn card').toBe(true);
    await shot(page, info, 'posting-5a-linkedin-developer-app-form', form, needs);
    await needs.getByRole('button', { name: 'Dismiss notification' }).click();

    const youtube = account('YouTube');
    await youtube.scrollIntoViewIfNeeded();
    await youtube.getByRole('button', { name: /Connect$/ }).click();
    const cannot = page.locator(TOASTS).filter({ hasText: "YouTube can't be connected from here yet" });
    await expect(cannot).toBeVisible({ timeout: 30_000 });
    await expect(youtube.getByTestId('cp-oauth-app-form')).toHaveCount(0);
    await shot(page, info, 'posting-5b-youtube-cannot-connect-yet', youtube, cannot);
  });

  test('the Connect-your-services card for an OAuth platform says what happened, not that a window opened', async ({}, info) => {
    const card = page.locator('[data-connection="platform:linkedin"]');
    await card.scrollIntoViewIfNeeded();
    const toggle = card.getByRole('button', { name: /(Show|Hide) details for/ }).first();
    if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
    await card.getByRole('button', { name: 'Connect', exact: true }).click();
    const said = card.locator('.sc-msgline');
    await expect(said).toBeVisible({ timeout: 30_000 });
    await expect(said).toContainText('needs your own developer app');
    await expect(said).toContainText('Publishing channels');
    await expect(said).not.toContainText('window that opened');
    await expect(said).not.toContainText('opens in a new browser tab');
    await shot(page, info, 'posting-6-connect-services-oauth-card', card);
  });
});
