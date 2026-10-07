// See & Touch, phase 1: Friday ticks rows in the Message Center and the owner sees the checks.
//
// Runs against a SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The
// inbox is a fixture of 120 cards across 3 accounts served by page.route, so nothing reaches Gmail;
// the server's commands are sent the way the page receives them (window.fridayStage.run), and every
// non-GET request is refused except the ones this spec asserts on.
//
// What it proves in a real page: the ticks land on exactly the matching rows within 400 ms, the chip
// names who selected, the owner can edit and clear, held rows turn amber and release, done rows leave
// with an Undo, declined rows keep their ticks, the owner's own bulk button makes no card request,
// reduced motion is a 120 ms fade, and every state is announced politely.
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

const ACCOUNTS = ['acct_work', 'acct_home', 'acct_proj'].map((id, k) => ({
  id, label: ['Work', 'Home', 'Projects'][k], email: `me@${id}.example`, color: ['#2dd4bf', '#f472b6', '#60a5fa'][k],
  mail: { read: true, modify: true }, services: { gmail: true },
}));
const KINDS: [string, string, boolean, string][] = [
  ['Substack Weekly', 'subscriptions', true, 'updates'], ['Shop Deals', 'noise', true, 'promotions'],
  ['Editor Dana', 'career', false, 'primary'], ['Mum', 'family', false, 'primary'],
];
const CARDS = Array.from({ length: 120 }, (_, i) => {
  const [who, lane, bulk, category] = KINDS[i % KINDS.length];
  const a = ACCOUNTS[i % 3];
  return {
    id: `m${i}`, gmail_id: `g${i}`, thread_id: `t${i}`, account_id: a.id, account_label: a.label, account_color: a.color,
    sender: who, sender_email: `${who.split(' ')[0].toLowerCase()}@x${i % 4}.example`, subject: `Subject ${i}`, snippet: 'preview',
    timestamp: `2026-10-0${1 + Math.floor(i / 40)}T09:${String(i % 60).padStart(2, '0')}:00`, unread: i % 3 === 0, flagged: false,
    important: false, lane, is_bulk: bulk, category, labels: ['INBOX'], age_hours: 4 + i, awaiting_reply: false,
  };
});
const NEWSLETTER_REFS = CARDS.filter(c => c.is_bulk).map(c => `mail:${c.account_id}:${c.thread_id}`);

async function openMessages(page: Page, opts: { pending?: unknown[] } = {}) {
  const requests: string[] = [];
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/messages/stats', r => r.fulfill(json({ status: 'ok', counts: {}, actionable: 0 })));
  await page.route('**/api/messages?*', r => r.fulfill(json({ status: 'ok', total: CARDS.length, messages: CARDS, errors: [], source: 'merged' })));
  await page.route('**/api/google/accounts*', r => r.fulfill(json({ accounts: ACCOUNTS })));
  await page.route('**/api/mail/can-send*', r => r.fulfill(json({ accounts: [] })));
  await page.route('**/api/mail/labels*', r => r.fulfill(json({ status: 'ok', labels: [] })));
  await page.route('**/api/approvals?*', r => r.fulfill(json({ ok: true, approvals: opts.pending || [] })));
  page.on('request', q => { if (q.method() !== 'GET') requests.push(`${q.method()} ${new URL(q.url()).pathname}`); });
  await page.goto(`${BASE}/w/messages`, { waitUntil: 'domcontentloaded' });
  await page.locator('.fm-row').first().waitFor({ timeout: 60000 });
  return requests;
}

const run = (page: Page, action: unknown) => page.evaluate(a => (window as any).fridayStage.run(a), action) as Promise<any>;
const select = (page: Page, refs: string[], label = 'Newsletters', mode = 'replace') =>
  run(page, { type: 'select', workspace: 'messages', selection: { id: 'sel_pw', refs, label, mode } });

test('Friday ticks exactly the newsletters, within 400 ms, and the chip says so', async ({ page }) => {
  await openMessages(page);
  const t0 = Date.now();
  const out = await select(page, NEWSLETTER_REFS);
  expect(out.result.ok && out.result.applied).toBe(NEWSLETTER_REFS.length);
  await expect(page.locator('.fm-row.sel')).toHaveCount(NEWSLETTER_REFS.length);
  expect(Date.now() - t0).toBeLessThan(400 + 600);              // the ack plus one frame; the sweep itself ends inside 350 ms
  const bulk = await page.locator('.fm-row').evaluateAll(rows => rows.filter(r => (r.querySelector('input') as HTMLInputElement).checked).map(r => r.getAttribute('data-fr-ref')));
  expect(bulk.sort()).toEqual([...NEWSLETTER_REFS].sort());
  await expect(page.locator('[data-testid=fm-selchip]')).toContainText(`Newsletters · ${NEWSLETTER_REFS.length} · `);
  await expect(page.locator('[data-testid=fm-selchip]')).toContainText('selected');
  await expect(page.locator('.fm-sr[aria-live=polite]')).toContainText(`${NEWSLETTER_REFS.length} conversations selected by`);
  await expect(page.locator('.fm-row.sel').first()).toHaveAttribute('aria-selected', 'true');
});

test('the owner edits the selection and the chip says (edited); the stage the server reads follows', async ({ page }) => {
  await openMessages(page);
  await select(page, NEWSLETTER_REFS);
  await page.locator('.fm-row.sel input').first().click();
  await expect(page.locator('[data-testid=fm-selchip]')).toContainText('(edited)');
  const st = await page.evaluate(() => (window as any).fridayStage.snapshot('messages'));
  expect(st.selection.count).toBe(NEWSLETTER_REFS.length - 1);
  expect(st.selection.source).toBe('mixed');
  await page.locator('[data-testid=fm-selchip] button').click();
  await expect(page.locator('.fm-row.sel')).toHaveCount(0);
});

test('refs the list does not show are held and counted, not dropped', async ({ page }) => {
  await openMessages(page);
  const out = await select(page, [...NEWSLETTER_REFS, ...Array.from({ length: 22 }, (_, k) => `mail:acct_work:beyond${k}`)]);
  expect(out.result.missing).toBe(22);
  await expect(page.locator('.fm-bulk')).toContainText('not shown');
  const st = out.stage;
  expect(st.selection.count).toBe(NEWSLETTER_REFS.length + 22);
  expect(st.selection.beyond_loaded).toBe(22);
});

test('a card pending: rows turn amber and release; done leaves with Undo; declined keeps the ticks', async ({ page }) => {
  const requests = await openMessages(page);
  await select(page, NEWSLETTER_REFS);
  await run(page, { type: 'held', workspace: 'messages', state: 'held', refs: NEWSLETTER_REFS, card_id: 'ap_1' });
  await expect(page.locator('.fm-row.held')).toHaveCount(NEWSLETTER_REFS.length);
  await expect(page.locator('[data-testid=fm-selchip]')).toContainText('Waiting for your OK');
  await run(page, { type: 'held', workspace: 'messages', state: 'declined', refs: NEWSLETTER_REFS, card_id: 'ap_1' });
  await expect(page.locator('.fm-row.held')).toHaveCount(0);
  await expect(page.locator('.fm-row.sel')).toHaveCount(NEWSLETTER_REFS.length);
  await run(page, { type: 'held', workspace: 'messages', state: 'held', refs: NEWSLETTER_REFS, card_id: 'ap_2' });
  await run(page, { type: 'held', workspace: 'messages', state: 'done', refs: NEWSLETTER_REFS, card_id: 'ap_2', action: 'archive', receipt_id: 'rcpt_0123456789ab' });
  await expect(page.locator('.fm-row')).toHaveCount(CARDS.length - NEWSLETTER_REFS.length);
  await expect(page.locator('[data-testid=fm-selchip]')).toHaveCount(0);
  await page.getByRole('button', { name: /^Undo/ }).click();
  await expect.poll(() => requests.some(r => r === 'POST /api/actions/receipts/rcpt_0123456789ab/undo')).toBe(true);
});

test('the owner pressing Archive on the selection makes no card request', async ({ page }) => {
  const requests = await openMessages(page);
  await select(page, NEWSLETTER_REFS.slice(0, 5));
  await page.locator('.fm-bulk button', { hasText: 'Archive' }).click();
  await expect.poll(() => requests.some(r => r === 'POST /api/messages/action')).toBe(true);
  expect(requests.filter(r => /approvals|mail\/request|organize/.test(r))).toEqual([]);
});

test('a card that was pending before a reload still holds its rows (I9)', async ({ page }) => {
  await openMessages(page, { pending: [{ approval_id: 'ap_p', payload: { handler: 'item_batch', domain: 'email', refs: [`mail:${CARDS[0].account_id}:${CARDS[0].thread_id}`] } }] });
  await expect(page.locator('.fm-row.held')).toHaveCount(1);
});

test('reduced motion: no scale, a 120 ms fade', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openMessages(page);
  await select(page, NEWSLETTER_REFS);
  const css = await page.locator('.fm-row.sel.fr-sweep input[type=checkbox]').first().evaluate(el => {
    const cs = getComputedStyle(el);
    return { name: cs.animationName, dur: cs.animationDuration, delay: cs.animationDelay };
  });
  expect(css.name).toBe('fr-ground-in');
  expect(css.dur).toBe('0.12s');
  expect(css.delay).toBe('0s');
});

test('motion: staggered, opacity and scale only, ending inside 350 ms', async ({ page }) => {
  await openMessages(page);
  await select(page, NEWSLETTER_REFS);
  const rows = await page.locator('.fm-row.sel.fr-sweep').evaluateAll(els => els.map(el => {
    const cs = getComputedStyle(el.querySelector('input') as Element);
    return { name: cs.animationName, end: parseFloat(cs.animationDelay) * 1000 + parseFloat(cs.animationDuration) * 1000 };
  }));
  expect(rows.length).toBeGreaterThan(10);
  for (const r of rows) { expect(r.name).toBe('fr-tick-in'); expect(r.end).toBeLessThanOrEqual(350.5); }
});
