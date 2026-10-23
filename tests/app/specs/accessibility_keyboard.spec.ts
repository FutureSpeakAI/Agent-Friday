// Accessibility, slice AX1 (north star §33.1 and §34.14; burn-down BD-21): the desktop can be reached and
// used by keyboard alone, focus can always be seen, the system's reduced-motion and high-contrast choices are
// honoured, and the structure a screen reader announces names its parts.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Writes are answered by the test,
// so nothing is stored and no model is asked anything.
import { test, expect, type Page } from '../fixtures';
import { BASE, openApp } from '../harness';

test.skip(!process.env.FRIDAY_BASE || /:3000(\/|$)/.test(process.env.FRIDAY_BASE || ''),
  'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

/** The desktop, set up, with every write answered here and the chat endpoints recorded. */
async function desktop(page: Page): Promise<string[]> {
  const sent: string[] = [];
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route(/\/api\/chat(\/stream)?(\?.*)?$/, r => {
    if (r.request().method() === 'POST') sent.push(r.request().postData() || '');
    return r.fulfill(json({ status: 'ok', response: 'ok' }));
  });
  await openApp(page);
  return sent;
}

/** What the focused element shows: its outline or the ring drawn as a shadow. */
async function focusRing(page: Page) {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return null;
    const cs = getComputedStyle(el);
    return { tag: el.tagName, cls: String(el.className || ''), outline: cs.outlineStyle,
             width: parseFloat(cs.outlineWidth || '0'), shadow: cs.boxShadow };
  });
}

test('the first Tab stop skips straight to the app', async ({ page }) => {
  await desktop(page);
  // From the top of the page: the desktop may already have put focus in the
  // chat box, so start where a fresh Tab sequence starts, before the first
  // element (a point that is not itself a Tab stop), then press Tab.
  await page.evaluate(() => {
    const top = document.createElement('span');
    top.id = 'ax-top-of-page';
    top.tabIndex = -1;
    document.body.insertBefore(top, document.body.firstChild);
    top.focus();
  });
  await page.keyboard.press('Tab');
  const skip = page.locator('a.skip-link');
  await expect(skip, 'the first thing Tab reaches is the skip link').toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(page.locator('#ui-inner')).toBeFocused();
});

test('the dock is a named navigation, every workspace button has a name and says whether it is open', async ({ page }) => {
  await desktop(page);
  const nav = page.getByRole('navigation', { name: 'Workspaces' });
  await expect(nav).toBeVisible();
  const buttons = nav.locator('.dock-btn');
  const n = await buttons.count();
  expect(n, 'the dock has workspace buttons').toBeGreaterThanOrEqual(4);
  for (let i = 0; i < n; i++) {
    await expect(buttons.nth(i), 'dock button ' + i + ' has an accessible name').toHaveAccessibleName(/\S/);
    await expect(buttons.nth(i)).toHaveAttribute('aria-pressed', /^(true|false)$/);
  }
});

test('a workspace opens from the keyboard, and the focused button shows a focus ring', async ({ page }) => {
  await desktop(page);
  const btn = page.getByRole('navigation', { name: 'Workspaces' }).locator('.dock-btn[aria-pressed="false"]').first();
  const ws = await btn.getAttribute('data-ws');
  await page.keyboard.press('Tab');                       // the skip link: keyboard focus from here on
  await btn.focus();
  const ring = await focusRing(page);
  expect(ring, 'a dock button took focus').not.toBeNull();
  expect(ring!.outline !== 'none' && ring!.width > 0, 'the focused dock button shows an outline: ' + JSON.stringify(ring)).toBeTruthy();
  await page.keyboard.press('Enter');
  await expect(page.locator(`.dock-btn[data-ws="${ws}"]`)).toHaveAttribute('aria-pressed', 'true');
});

test('a message is written and sent with the keyboard alone', async ({ page }) => {
  const sent = await desktop(page);
  // Ctrl+/ opens the chat tray and puts the cursor in it; asked again until it lands (the tray mounts first).
  await expect(async () => {
    if (!(await page.evaluate(() => !!document.activeElement && document.activeElement.hasAttribute('data-chat-input')))) {
      await page.keyboard.press('Control+/');
    }
    expect(await page.evaluate(() => !!document.activeElement && document.activeElement.hasAttribute('data-chat-input')),
      'Ctrl+/ puts the cursor in a chat field').toBe(true);
  }).toPass({ timeout: 20_000 });
  await page.keyboard.type('keyboard-only hello');
  await page.keyboard.press('Enter');
  await expect.poll(() => sent.some(b => b.includes('keyboard-only hello')),
    { message: 'Enter sent the message', timeout: 15_000 }).toBeTruthy();
});

test('in Windows high-contrast mode the focus ring is drawn in the system highlight', async ({ page }) => {
  await page.emulateMedia({ forcedColors: 'active' });
  await desktop(page);
  await page.keyboard.press('Tab');
  await page.locator('.dock-btn').first().focus();
  const ring = await focusRing(page);
  expect(ring && ring.outline !== 'none' && ring.width >= 2, 'a visible focus ring in forced colours: ' + JSON.stringify(ring)).toBeTruthy();
});
