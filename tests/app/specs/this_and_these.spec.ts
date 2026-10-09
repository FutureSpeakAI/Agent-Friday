// See & Touch, phase 3: "this" and "these", the hand cursor in the stage, and ticks in Media.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Media is a fixture of 12
// cards served by page.route. Proves in a real page: Friday's ticks and the owner's are one set with a
// chip that says who; a held card turns amber and releases; the row the reticle is on reaches the stage
// as the cursor for three seconds; a quick pinch on a card ticks it and a pinch held 700 ms opens it.
import { test, expect, type Page } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 900 } });

const CARDS = Array.from({ length: 12 }, (_, i) => ({
  id: `c${i}`, title: `Card ${i}`, kind: i % 2 ? 'post' : 'draft', status: i % 3 ? 'draft' : 'review', project: i % 2 ? 'Harbor' : '',
  privacy: 'private', origin: 'friday', maker: 'Friday', tags: [], when: '2026-10-01', signed: true, details: {},
}));

async function open(page: Page) {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/media?*', r => r.fulfill(json({ status: 'ok', cards: CARDS, counts: { all: CARDS.length, kinds: {}, tags: {} }, projects: [], collections: [] })));
  await page.goto(`${BASE}/w/media`, { waitUntil: 'domcontentloaded' });
  await page.locator('.md-card').first().waitFor({ timeout: 60000 });
}
const run = (page: Page, a: unknown) => page.evaluate(x => (window as any).fridayStage.run(x), a) as Promise<any>;
const mref = (i: number) => `media:c${i}`;

test('Friday ticks cards, the chip says so, the owner edits, and the stage follows', async ({ page }) => {
  await open(page);
  const out = await run(page, { type: 'select', workspace: 'media', selection: { id: 'sel_1', refs: [mref(1), mref(3), mref(5)], label: 'Drafts', mode: 'replace' } });
  expect(out.result).toMatchObject({ ok: true, applied: 3, missing: 0, count: 3 });
  await expect(page.locator('.md-card.multi')).toHaveCount(3);
  await expect(page.locator('[data-testid=md-selchip]')).toContainText('Drafts · 3 · ');
  await page.locator('.md-card.multi [data-fr-tick]').first().click();
  await expect(page.locator('[data-testid=md-selchip]')).toContainText('(edited)');
  const st = await page.evaluate(() => (window as any).fridayStage.snapshot('media'));
  expect(st.selection.count).toBe(2);
  expect(st.selection.source).toBe('mixed');
});

test('a held card turns amber, releases when declined, and the ticks stay', async ({ page }) => {
  await open(page);
  await run(page, { type: 'select', workspace: 'media', selection: { id: 'sel_2', refs: [mref(1), mref(3)], label: 'Drafts', mode: 'replace' } });
  await run(page, { type: 'held', workspace: 'media', state: 'held', refs: [mref(1), mref(3)], card_id: 'ap_1' });
  await expect(page.locator('.md-card.needs-you')).toHaveCount(2);
  await expect(page.locator('[data-testid=md-selchip]')).toContainText('Waiting for your OK');
  await run(page, { type: 'held', workspace: 'media', state: 'declined', refs: [mref(1), mref(3)], card_id: 'ap_1' });
  await expect(page.locator('.md-card.needs-you')).toHaveCount(0);
  await expect(page.locator('.md-card.multi')).toHaveCount(2);
});

test('the row the reticle is on reaches the stage as the cursor, for three seconds after it leaves', async ({ page }) => {
  await open(page);
  await page.evaluate(() => { (window as any).fridayStage.cursor('media:c4', 'locked'); });
  let st = await page.evaluate(() => (window as any).fridayStage.snapshot('media'));
  expect(st.cursor).toMatchObject({ ref: 'media:c4', state: 'locked' });
  await page.evaluate(() => { (window as any).fridayStage.cursor('', ''); });
  st = await page.evaluate(() => (window as any).fridayStage.snapshot('media'));
  expect(st.cursor && st.cursor.ref).toBe('media:c4');
  expect(st.cursor.age_s).toBeLessThan(3);
  await page.waitForTimeout(3300);
  st = await page.evaluate(() => (window as any).fridayStage.snapshot('media'));
  expect(st.cursor).toBeNull();
});

test('a quick pinch on a card ticks it; a pinch held for 700 ms opens it', async ({ page }) => {
  await open(page);
  await page.evaluate(() => {
    const HC = (window as any).FridayHandCursor; const el = document.querySelector('.md-card[data-id="c2"]') as HTMLElement;
    const r = el.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
    let t = 5000; const f = (pinching: boolean) => HC.frame({ x, y, visible: true, pinching, t: (t += 33) });
    for (let i = 0; i < 6; i++) f(false);
    f(true); for (let i = 0; i < 3; i++) f(true); f(false);              // a quick pinch
  });
  await expect(page.locator('.md-card[data-id="c2"].multi')).toHaveCount(1);
  await page.evaluate(() => {
    const HC = (window as any).FridayHandCursor; const el = document.querySelector('.md-card[data-id="c4"]') as HTMLElement;
    const r = el.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
    let t = 9000; const f = (pinching: boolean) => HC.frame({ x, y, visible: true, pinching, t: (t += 33) });
    for (let i = 0; i < 6; i++) f(false);
    f(true); for (let i = 0; i < 30; i++) f(true); f(false);              // held for about a second
  });
  await expect(page.locator('.md-card[data-id="c4"].multi')).toHaveCount(0);
});
