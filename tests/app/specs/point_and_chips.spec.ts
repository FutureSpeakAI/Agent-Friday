// See & Touch, phase 2: Friday points at rows and sets filter chips; the owner sees both.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The Message Center
// inbox is a fixture served by page.route; the server's commands are sent the way the page receives
// them (window.fridayStage.run). Proves in a real page: outlines land on exactly the matched rows,
// badges are numbered in the order asked and stop at 12, the outline fades on the owner's next input,
// a spoken filter shows as a chip "by Friday" and its x removes the filter and updates the list, and
// reduced motion keeps a fade.
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

const ACCOUNT = { id: 'acct_work', label: 'Work', email: 'me@work.example', color: '#2dd4bf', mail: { read: true, modify: true }, services: { gmail: true } };
const CARDS = Array.from({ length: 40 }, (_, i) => ({
  id: `m${i}`, gmail_id: `g${i}`, thread_id: `t${i}`, account_id: ACCOUNT.id, account_label: 'Work', account_color: ACCOUNT.color,
  sender: i % 2 ? 'Mum' : 'Editor Dana', sender_email: i % 2 ? 'mum@home.example' : 'dana@editor.example', subject: `Subject ${i}`, snippet: 'preview',
  timestamp: `2026-10-01T09:${String(i).padStart(2, '0')}:00`, unread: i % 4 === 0, flagged: false, important: false,
  lane: i % 2 ? 'family' : 'career', is_bulk: false, category: 'primary', labels: ['INBOX'], age_hours: 4 + i, awaiting_reply: false,
}));

async function open(page: Page) {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/messages/stats', r => r.fulfill(json({ status: 'ok', counts: {}, actionable: 0 })));
  await page.route('**/api/messages?*', r => r.fulfill(json({ status: 'ok', total: CARDS.length, messages: CARDS, errors: [], source: 'merged' })));
  await page.route('**/api/google/accounts*', r => r.fulfill(json({ accounts: [ACCOUNT] })));
  await page.route('**/api/mail/can-send*', r => r.fulfill(json({ accounts: [] })));
  await page.route('**/api/mail/labels*', r => r.fulfill(json({ status: 'ok', labels: [] })));
  await page.route('**/api/approvals?*', r => r.fulfill(json({ ok: true, approvals: [] })));
  await page.goto(`${BASE}/w/messages`, { waitUntil: 'domcontentloaded' });
  await page.locator('.fm-row').first().waitFor({ timeout: 60000 });
}
const run = (page: Page, a: unknown) => page.evaluate(x => (window as any).fridayStage.run(x), a) as Promise<any>;
const ref = (i: number) => `mail:${ACCOUNT.id}:t${i}`;

test('outlines land on exactly the matched rows, numbered in the order asked', async ({ page }) => {
  await open(page);
  const refs = [ref(8), ref(0), ref(4)];
  const out = await run(page, { type: 'point', workspace: 'messages', id: 'pt_1', refs, badges: 'numbers' });
  expect(out.result).toEqual({ ok: true, count: 3 });
  const pointed = await page.locator('[data-fr-point]').evaluateAll(els => els.map(e => [e.getAttribute('data-fr-ref'), e.getAttribute('data-fr-n')]));
  expect(pointed.sort()).toEqual([[ref(0), '2'], [ref(4), '3'], [ref(8), '1']].sort());
  await expect(page.locator('[data-fr-point]').first()).toHaveCSS('outline-color', 'rgb(0, 212, 255)');
  expect(out.stage.pointed.refs).toEqual(refs);
});

test('twelve rows get badges and the rest are only counted; the next input clears them', async ({ page }) => {
  await open(page);
  const refs = Array.from({ length: 20 }, (_, i) => ref(i));
  const out = await run(page, { type: 'point', workspace: 'messages', id: 'pt_2', refs, badges: 'numbers' });
  expect(out.result.count).toBe(12);
  await expect(page.locator('[data-fr-point]')).toHaveCount(12);
  await page.waitForTimeout(400);                       // the page arms its listeners a moment after the paint
  await page.keyboard.press('j');
  await expect(page.locator('[data-fr-point]')).toHaveCount(0);
});

test('a spoken filter shows as a chip by Friday; its x removes the filter and the list follows', async ({ page }) => {
  await open(page);
  const out = await run(page, { type: 'chips', workspace: 'messages', set: [{ key: 'unread', value: '1' }], remove: [] });
  expect(out.result.ok).toBe(true);
  await expect(page.locator('[data-testid=fr-chips] .fr-chip')).toContainText('Unread only');
  await expect(page.locator('[data-testid=fr-chips] .fr-chip')).toContainText('by');
  expect(out.stage.filters.map((f: any) => [f.key, f.by])).toEqual([['unread', 'friday']]);
  await expect(page.locator('.fm-row')).toHaveCount(CARDS.filter(c => c.unread).length);
  await page.locator('[data-testid=fr-chips] .fr-chip button').click();
  await expect(page.locator('[data-testid=fr-chips]')).toHaveCount(0);
  await expect(page.locator('.fm-row')).toHaveCount(CARDS.length);
});

test('a filter the owner changes afterwards stops being marked as Friday', async ({ page }) => {
  await open(page);
  await run(page, { type: 'chips', workspace: 'messages', set: [{ key: 'lane', value: 'career' }], remove: [] });
  await page.getByText('Family', { exact: false }).first().click();             // the owner picks another lane
  const st = await page.evaluate(() => (window as any).fridayStage.snapshot('messages'));
  expect(st.filters.find((f: any) => f.key === 'lane').by).toBe('owner');
});

test('a filter the workspace does not have is refused, not shown', async ({ page }) => {
  await open(page);
  const out = await run(page, { type: 'chips', workspace: 'messages', set: [{ key: 'colour', value: 'red' }], remove: [] });
  expect(out.result.ok).toBe(false);
  expect(out.result.rejected[0].key).toBe('colour');
  await expect(page.locator('[data-testid=fr-chips]')).toHaveCount(0);
});

test('reduced motion: the outline still fades in, never snaps off', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await open(page);
  await run(page, { type: 'point', workspace: 'messages', id: 'pt_3', refs: [ref(0)], badges: 'numbers' });
  const t = await page.locator('[data-fr-point]').first().evaluate(el => getComputedStyle(el).transitionDuration);
  expect(parseFloat(t)).toBeGreaterThan(0);
});
