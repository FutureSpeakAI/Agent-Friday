// See & Touch, phase 4: Friday writes into a field the owner can see; only the owner presses Send.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The inbox is a fixture served
// by page.route; every non-GET request is recorded and refused, so the spec can assert that a fill makes no
// request to a send, save or create route. Proves in a real page: a reply is written above the quoted
// original with the Send button untouched and enabled; Undo restores what was there; the calendar quick-add
// line is filled but no event is created until the owner presses Enter; the workflow editor fill does not
// save; a field nobody registered is refused.
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

const ACCOUNT = { id: 'acct_work', label: 'Work', email: 'me@work.example', color: '#2dd4bf', mail: { read: true, modify: true }, services: { gmail: true } };
const CARD = {
  id: 'm1', gmail_id: 'g1', thread_id: 't1', account_id: ACCOUNT.id, account_label: 'Work', account_color: ACCOUNT.color,
  sender: 'Editor Dana', sender_email: 'dana@editor.example', subject: 'Lunch Tuesday?', snippet: 'preview', timestamp: '2026-10-01T09:00:00',
  unread: true, flagged: false, important: false, lane: 'career', is_bulk: false, category: 'primary', labels: ['INBOX'], age_hours: 4,
};

async function openMessages(page: Page) {
  const writes: string[] = [];
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/*', r => {
    if (r.request().method() === 'GET') return r.fallback();
    writes.push(`${r.request().method()} ${new URL(r.request().url()).pathname}`);
    return r.fulfill(json({ status: 'ok' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/messages/stats', r => r.fulfill(json({ status: 'ok', counts: {}, actionable: 0 })));
  await page.route('**/api/messages?*', r => r.fulfill(json({ status: 'ok', total: 1, messages: [CARD], errors: [], source: 'merged' })));
  await page.route('**/api/messages/t1*', r => r.fulfill(json({ status: 'ok', messages: [{ id: 'g1', sender: 'Editor Dana <dana@editor.example>', date: 'Mon', body: 'Can we do lunch Tuesday?', to: 'me@work.example' }] })));
  await page.route('**/api/google/accounts*', r => r.fulfill(json({ accounts: [ACCOUNT] })));
  await page.route('**/api/mail/can-send*', r => r.fulfill(json({ accounts: [ACCOUNT] })));
  await page.route('**/api/mail/labels*', r => r.fulfill(json({ status: 'ok', labels: [] })));
  await page.route('**/api/mail/signature*', r => r.fulfill(json({ status: 'ok', signature: '' })));
  await page.route('**/api/approvals?*', r => r.fulfill(json({ ok: true, approvals: [] })));
  await page.goto(`${BASE}/w/messages`, { waitUntil: 'domcontentloaded' });
  await page.locator('.fm-row').first().waitFor({ timeout: 60000 });
  return writes;
}
const run = (page: Page, a: unknown) => page.evaluate(x => (window as any).fridayStage.run(x), a) as Promise<any>;

test('a reply is written above the quote, Send is untouched, and no write request is made', async ({ page }) => {
  const writes = await openMessages(page);
  await page.locator('.fm-row').first().click();
  await page.getByRole('button', { name: /Reply$/ }).first().click();
  await expect(page.locator('.fm-compose')).toBeVisible();
  const st = await page.evaluate(() => (window as any).fridayStage.snapshot('messages'));
  expect(st.fields.map((f: any) => f.key)).toEqual(expect.arrayContaining(['reply.body', 'reply.subject', 'reply.to']));
  const before = writes.length;
  const out = await run(page, { type: 'fill', workspace: 'messages', field: 'reply.body', text: 'Tuesday works. See you at noon.', mode: 'replace' });
  expect(out.result.ok).toBe(true);
  await expect(page.locator('.fm-editor')).toContainText('Tuesday works. See you at noon.');
  await expect(page.locator('.fm-editor .fm-quote, .fm-editor')).toContainText('Can we do lunch Tuesday?');   // the quoted original stays
  await expect(page.locator('[data-testid=fr-fill-reply\\.body]')).toContainText('wrote this');
  const send = page.getByRole('button', { name: /Send/ }).first();
  await expect(send).toBeEnabled();
  expect(writes.length).toBe(before);                       // a fill made no write request of any kind
});

test('Undo puts the reply back as it was, and an edit by the owner makes it theirs', async ({ page }) => {
  await openMessages(page);
  await page.locator('.fm-row').first().click();
  await page.getByRole('button', { name: /Reply$/ }).first().click();
  await page.locator('.fm-editor').click();
  await page.keyboard.type('draft by me');
  await run(page, { type: 'fill', workspace: 'messages', field: 'reply.body', text: 'Friday wrote this', mode: 'replace' });
  await page.locator('[data-testid=fr-fill-reply\\.body] button', { hasText: 'Undo' }).click();
  await expect(page.locator('.fm-editor')).toContainText('draft by me');
  await run(page, { type: 'fill', workspace: 'messages', field: 'reply.body', text: 'Friday again', mode: 'replace' });
  await page.locator('.fm-editor').click();
  await page.keyboard.type(' plus mine');
  await expect(page.locator('[data-testid=fr-fill-reply\\.body]')).toHaveCount(0);
});

test('a field nobody registered is refused', async ({ page }) => {
  await openMessages(page);
  const out = await run(page, { type: 'fill', workspace: 'messages', field: 'send_button', text: 'click' });
  expect(out.result).toEqual({ ok: false, reason: 'not a field here' });
});

test('the calendar quick-add is filled but no event is created until the owner presses Enter', async ({ page }) => {
  const writes: string[] = [];
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/*', r => {
    if (r.request().method() === 'GET') return r.fallback();
    writes.push(`${r.request().method()} ${new URL(r.request().url()).pathname}`);
    return r.fulfill(json({ status: 'ok', event: {} }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.goto(`${BASE}/w/calendar`, { waitUntil: 'domcontentloaded' });
  await page.locator('.cal-quickadd input').waitFor({ timeout: 60000 });
  const out = await run(page, { type: 'fill', workspace: 'calendar', field: 'quickadd', text: 'lunch with Dana Tuesday 12pm' });
  expect(out.result.ok).toBe(true);
  await expect(page.locator('.cal-quickadd input')).toHaveValue('lunch with Dana Tuesday 12pm');
  expect(writes.filter(w => /quick-add|events/.test(w))).toEqual([]);
  await page.locator('.cal-quickadd input').press('Enter');
  await expect.poll(() => writes.some(w => /quick-add/.test(w))).toBe(true);
});

test('the workflow editor fill does not save', async ({ page }) => {
  const writes: string[] = [];
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/*', r => {
    if (r.request().method() === 'GET') return r.fallback();
    writes.push(`${r.request().method()} ${new URL(r.request().url()).pathname}`);
    return r.fulfill(json({ status: 'ok' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.goto(`${BASE}/w/workflows`, { waitUntil: 'domcontentloaded' });
  await page.locator('#wf-ask').waitFor({ timeout: 60000 });
  const out = await run(page, { type: 'fill', workspace: 'workflows', field: 'compose', text: 'Every weekday at 7:30, check the council site' });
  expect(out.result.ok).toBe(true);
  await expect(page.locator('#wf-ask')).toHaveValue('Every weekday at 7:30, check the council site');
  expect(writes.filter(w => /workflows\/(save|draft)/.test(w))).toEqual([]);
});
