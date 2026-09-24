/**
 * One chat, everywhere. The docked chat panel and every in-desktop chat window
 * render the same ChatSurface, so a window has the whole toolbar (model
 * switcher, voice, audio devices, cite/show sources, attach) and the same
 * markdown. Windows resize from every edge and corner and remember their size.
 * Every chat box sends on Enter, takes a new line on Shift+Enter, and never
 * sends while an input method is composing.
 *
 * Read from a running app: a missing button or a window that will not resize
 * only shows in a rendered page. The chat reply is stubbed, so no model runs.
 *
 *   FRIDAY_URL=http://localhost:3217 PW_CHANNEL=msedge npx playwright test tests/chat_surface.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || '';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.setTimeout(180000);
test.use({ viewport: { width: 1600, height: 1000 } });

const REPLY = 'Here is **bold** and a list:\n\n- one\n- two';

async function boot(page: Page): Promise<{ sent: any[] }> {
  const sent: any[] = [];
  await page.route('**/api/chat/stream', async r => {
    sent.push(JSON.parse(r.request().postData() || '{}'));
    const body = 'data: ' + JSON.stringify({ delta: 'Here is ' }) + '\n\n'
      + 'data: ' + JSON.stringify({ done: true, payload: { response: REPLY, model: 'stub-model', seat: 'local' } }) + '\n\n';
    await r.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body });
  });
  // the pause forecast would hold a message for a decision; not what these tests are about
  await page.route('**/api/work/forecast', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"will_pause":false}' }));
  // a scratch test home is not "set up", and the first-run wizard would cover the page
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  // nor has it made the cloud-privacy decision, whose prompt would cover it a few seconds in
  await page.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET'
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"needs_prompt":false}' }) : r.continue());
  await page.goto(BASE + '/');
  await page.waitForSelector('.dock', { timeout: 90000 });
  await page.waitForTimeout(1500);
  return { sent };
}

async function openWindow(page: Page): Promise<string> {
  const cid = await page.evaluate(async () => {
    const r = await fetch('/api/conversations').then(x => x.json());
    const id = (r.conversations || [])[0].id;
    (window as any).fridayOpenChatWindow(id, 'spec');
    return id;
  });
  await page.waitForSelector('.chat-win textarea[data-chat-input]', { timeout: 20000 });
  return cid;
}

test('a chat window has the same toolbar as the docked chat', async ({ page }) => {
  await boot(page);
  await page.evaluate(() => localStorage.removeItem('friday_chatwin_size'));
  await openWindow(page);
  const read = (sel: string) => page.evaluate((s: string) => {
    const root = document.querySelector(s) as HTMLElement;
    const has = (t: string) => [...root.querySelectorAll('button')].some(b => (b.textContent || '').includes(t));
    return {
      switcher: !!root.querySelector('[data-chat-head]') && root.innerText.includes('▾'),
      newChat: has('New Chat'), heartbeat: has('Heartbeat'), newTab: has('New tab'),
      mic: has('🎤') || has('■'), devices: !!root.querySelector('[data-audio-device-toggle]'),
      attach: has('📁') && !!root.querySelector('input[type=file]'),
      cite: has('Cite Sources'), show: has('Show Sources'),
      textarea: !!root.querySelector('textarea[data-chat-input]'), send: has('Send'),
    };
  }, sel);
  const win = await read('.chat-win');
  const panel = await read('.chat-panel');
  expect(win).toEqual(panel);
  expect(Object.values(win).every(Boolean), JSON.stringify(win)).toBe(true);
});

test('a chat window resizes from every edge and corner, within limits, and remembers its size', async ({ page }) => {
  await boot(page);
  await page.evaluate(() => localStorage.removeItem('friday_chatwin_size'));
  const cid = await openWindow(page);
  const win = page.locator('.chat-win');
  const drag = async (d: string, dx: number, dy: number) => {
    const e = (await page.locator('.chat-win .win-edge-' + d).boundingBox())!;
    const x = e.x + e.width / 2, y = e.y + e.height / 2;
    await page.mouse.move(x, y); await page.mouse.down();
    await page.mouse.move(x + dx / 2, y + dy / 2); await page.mouse.move(x + dx, y + dy);
    await page.mouse.up();
    return (await win.boundingBox())!;
  };
  expect(await page.locator('.chat-win .win-edge').count()).toBe(8);
  const b0 = (await win.boundingBox())!;
  const w = await drag('w', -100, 0);                     // left edge: wider, right side fixed
  expect(Math.round(w.width - b0.width)).toBe(100);
  expect(Math.round(w.x + w.width)).toBe(Math.round(b0.x + b0.width));
  const n = await drag('n', 0, 40);                       // top edge: shorter, bottom fixed
  expect(Math.round(w.height - n.height)).toBe(40);
  expect(Math.round(n.y + n.height)).toBe(Math.round(w.y + w.height));
  const sw = await drag('sw', 30, -30);                   // corner: both at once
  expect(Math.round(n.width - sw.width)).toBe(30);
  expect(Math.round(n.height - sw.height)).toBe(30);
  const tiny = await drag('e', -2000, 0);                 // never below the minimum
  expect(tiny.width).toBeGreaterThanOrEqual(380);
  expect(tiny.width).toBeLessThan(400);
  const back = await drag('e', 140, 0);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('friday_chatwin_size') || 'null'));
  expect(saved && Math.abs(saved.w + 2 - back.width)).toBeLessThanOrEqual(1);

  await page.reload();
  await page.waitForSelector('.dock', { timeout: 90000 });
  await page.evaluate((id: string) => (window as any).fridayOpenChatWindow(id, 'again'), cid);
  const again = (await page.locator('.chat-win').boundingBox())!;
  expect(Math.round(again.width)).toBe(Math.round(back.width));
  expect(Math.round(again.height)).toBe(Math.round(back.height));
});

test('workspace windows resize from the left and top edges too', async ({ page }) => {
  await boot(page);
  await page.evaluate(() => localStorage.removeItem('friday_fwin_news'));
  await page.locator('.dock-btn[data-ws="news"]').dispatchEvent('click', { bubbles: true });
  const win = page.locator('.fwin').filter({ has: page.locator('[data-ws-tab="news"]') });
  await win.waitFor({ timeout: 20000 });
  await page.waitForTimeout(600);
  expect(await win.locator('.win-edge').count()).toBe(8);
  const b0 = (await win.boundingBox())!;
  const e = (await win.locator('.win-edge-nw').boundingBox())!;
  await page.mouse.move(e.x + 5, e.y + 5); await page.mouse.down();
  // inward, so the result never depends on where the window happened to open
  await page.mouse.move(e.x + 25, e.y + 15); await page.mouse.move(e.x + 55, e.y + 35); await page.mouse.up();
  const b1 = (await win.boundingBox())!;
  expect(Math.round(b0.width - b1.width)).toBe(50);
  expect(Math.round(b0.height - b1.height)).toBe(30);
  expect(Math.round(b1.x + b1.width)).toBe(Math.round(b0.x + b0.width));
  expect(Math.round(b1.y + b1.height)).toBe(Math.round(b0.y + b0.height));
});

test('Enter sends, Shift+Enter is a new line, and composing never sends: chat window and docked chat', async ({ page }) => {
  const { sent } = await boot(page);
  const cid = await openWindow(page);
  for (const root of ['.chat-win', '.chat-panel']) {
    if (root === '.chat-panel') await page.evaluate(() => document.querySelector('.chat-panel')!.classList.add('open'));
    const ta = page.locator(`${root} textarea[data-chat-input]`);
    await ta.click();
    await ta.fill('');
    await ta.type('line one');
    await page.keyboard.down('Shift'); await page.keyboard.press('Enter'); await page.keyboard.up('Shift');
    await ta.type('line two');
    expect(await ta.inputValue(), root).toBe('line one\nline two');
    const before = sent.length;
    // an IME confirming a word with Enter
    await ta.dispatchEvent('keydown', { key: 'Enter', keyCode: 229, isComposing: true, bubbles: true });
    await page.waitForTimeout(300);
    expect(sent.length, root + ': sent while composing').toBe(before);
    expect(await ta.inputValue(), root).toBe('line one\nline two');
    await ta.press('Enter');
    await expect.poll(() => sent.length, { message: root }).toBe(before + 1);
    expect(sent[sent.length - 1].message).toBe('line one\nline two');
    await expect(ta).toHaveValue('');
    if (root === '.chat-win') expect(sent[sent.length - 1].conversation_id).toBe(cid);
    // the reply is markdown, and the typed line break survives in the transcript
    await expect(page.locator(`${root} strong`, { hasText: 'bold' }).last()).toBeVisible({ timeout: 15000 });
    expect(await page.locator(`${root} li`).count()).toBeGreaterThanOrEqual(2);
    const shown = await page.locator(root).evaluate(el => [...el.querySelectorAll('div')].some(d => d.childElementCount === 0 && d.textContent === 'line one\nline two' && getComputedStyle(d).whiteSpace === 'pre-wrap'));
    expect(shown, root + ': the new line is kept on screen').toBe(true);
  }
});

test('the greeting ask bar follows the same keys', async ({ page }) => {
  const { sent } = await boot(page);
  const ta = page.locator('textarea[placeholder*="Ask Friday"]').first();
  if (!(await ta.count())) test.skip(true, 'greeting bar not shown on this page');
  await ta.click();
  await ta.type('a');
  await page.keyboard.down('Shift'); await page.keyboard.press('Enter'); await page.keyboard.up('Shift');
  await ta.type('b');
  expect(await ta.inputValue()).toBe('a\nb');
  await ta.press('Enter');
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].message).toBe('a\nb');
});
