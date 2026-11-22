// A turn from the owner's phone, tablet or watch that lands in the open docked chat shows there, also after the owner has
// typed at the desktop (tv_gateway/owner_turn.announce_changed sends `conversation_changed`; the docked chat re-reads its
// transcript, and holds the re-read only while its own turn is still streaming).
//
// The app is the real one on the scratch server. The spec answers the desktop's own turn on /api/chat/stream, feeds one
// `conversation_changed` through /api/desktop/events, and adds the device's message to the transcript the store returns.
import { test, expect } from '../fixtures';
import type { Page } from '@playwright/test';
import { bootApp } from '../harness';

const DEVICE_TEXT = 'zz-device turn from the tablet';
const json = (o: unknown) => ({ status: 200, headers: { 'content-type': 'application/json' }, body: JSON.stringify(o) });
const sse = (frames: unknown[], retryMs: number) => ({
  status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
  body: 'retry: ' + retryMs + '\n\n' + frames.map(f => 'data: ' + JSON.stringify(f) + '\n\n').join(''),
});

async function open(page: Page) {
  const state = { turns: 0, landed: false, served: false, rereads: 0 };
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/work/forecast', r => r.fulfill(json({ will_pause: false })));
  await page.route('**/api/chat/stream', r => {
    state.turns += 1;
    return r.fulfill(sse([{ started: true, turn_id: 'zz-turn-' + state.turns },
      { done: true, payload: { response: 'zz-desk reply ' + state.turns, vision_events: [], actions: [] } }], 300000));
  });
  // the desktop bus: quiet until the device's turn lands, then one conversation_changed (no content, as the gateway sends it)
  await page.route('**/api/desktop/events*', r => {
    if (state.landed && !state.served) {
      state.served = true;
      return r.fulfill(sse([{ type: 'conversation_changed', domain: 'conversations', via: 'device' }], 300));
    }
    return r.fulfill(sse([], 300));
  });
  await page.route(/\/api\/conversations\/[^/?]+\/messages(\?.*)?$/, async r => {
    if (r.request().method() !== 'GET' || !state.landed) return r.fallback();
    state.rereads += 1;
    const real = await r.fetch();
    const d = await real.json();
    d.messages = (d.messages || []).concat([{ id: 'zz-device-1', role: 'user', text: DEVICE_TEXT, ts: Date.now() / 1000, meta: { kind: 'turn' } }]);
    return r.fulfill(json(d));
  });
  await bootApp(page);
  await expect(page.getByTestId('friday-top-bar')).toHaveAttribute('data-ready', 'true', { timeout: 30000 });
  return state;
}

async function openChat(page: Page) {
  if (await page.locator('.chat-panel.open').count()) return;
  const btn = page.locator('button[aria-label^="Open chat with"]').first();
  if (await btn.evaluate(el => !!el.closest('[data-overflow="true"]'))) {
    await page.getByRole('button', { name: 'More Friday controls' }).click();
  }
  await btn.click();
  await expect(page.locator('.chat-panel.open')).toHaveCount(1);
}

test('a device turn shows in the docked chat after the owner has typed at the desktop', async ({ page }, info) => {
  const state = await open(page);
  await openChat(page);
  const box = page.locator('.chat-panel.open textarea[data-chat-input]').first();
  await box.fill('zz-desk question');
  await box.press('Enter');
  await expect(page.locator('.chat-panel.open')).toContainText('zz-desk reply 1', { timeout: 20000 });

  state.landed = true;                                  // the phone's turn is in the store now
  await expect.poll(() => state.served, { message: 'the conversation_changed event was never delivered', timeout: 15000 }).toBe(true);
  await expect(page.locator('.chat-panel.open'), 'the docked chat never re-read after its own turn had ended')
    .toContainText(DEVICE_TEXT, { timeout: 15000 });
  expect(state.rereads).toBeGreaterThan(0);
  await page.locator('.chat-panel.open').screenshot({ path: info.outputPath('docked-chat-after-device-turn.png') });
});
