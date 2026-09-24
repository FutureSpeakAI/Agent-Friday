/**
 * The Messages workspace does what Gmail does, from the list, without
 * opening anything: hover actions, a right-click menu, select-all and a bulk
 * bar, Gmail's keys, folders and labels down the side, dark menus instead of
 * the native dropdown that drew white on white, and mail re-coloured for the
 * dark theme with a per-sender "show original".
 *
 * Read from a running app. By default every check is read-only: nothing is
 * archived, deleted or labelled, so it is safe against a real mailbox. The
 * write checks (Delete and undo, the read-only refusal, 3D Trash) run only
 * when FRIDAY_MAIL_WRITES=1, against a server whose Gmail is a stand-in.
 *
 *   FRIDAY_URL=http://localhost:3219 PW_CHANNEL=msedge FRIDAY_MAIL_WRITES=1 npx playwright test tests/mail_parity.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || '';
const WRITES = process.env.FRIDAY_MAIL_WRITES === '1';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.setTimeout(240000);
test.use({ viewport: { width: 1600, height: 1000 } });

async function open(page: Page, path = '/w/messages') {
  // a scratch test home shows its first-run prompts over the page
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await page.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET'
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"needs_prompt":false}' }) : r.continue());
  if (!WRITES) {
    // read-only: anything that could change mail is refused before it leaves the page
    await page.route('**/api/**', r => (r.request().method() === 'GET' ? r.fallback() : r.abort()));
  }
  await page.addInitScript(() => { if (window.top === window) try { localStorage.setItem('friday_3d_on_messages', path_is_3d() ? '1' : '0'); } catch (_) {} function path_is_3d() { return /view3d=1/.test(location.search); } });
  await page.goto(BASE + path);
}

test('every row acts without being opened, and says how', async ({ page }) => {
  await open(page);
  const row = page.locator('.fm-row').first();
  await row.waitFor({ timeout: 90000 });
  await row.hover();
  const acts = await row.locator('.fm-acts button').evaluateAll(bs => bs.map(b => b.getAttribute('aria-label') || ''));
  expect(acts.join(' | ')).toMatch(/Archive|Restore/);
  for (const want of ['Delete', 'Mark', 'Snooze', 'Label']) expect(acts.join(' | ')).toContain(want);
  // right-click: the whole set
  await row.click({ button: 'right' });
  const menu = page.locator('.fm-menu');
  await expect(menu).toBeVisible();
  const items = (await menu.locator('.it').allInnerTexts()).join(' ');
  for (const want of ['Archive', 'Delete (to Trash)', 'Snooze', 'Label as', 'Move to lane', 'Mute', 'Report spam', 'Filter messages like these']) expect(items).toContain(want);
  expect(items).toMatch(/Star|Unstar/);
  expect(items).toMatch(/Mark read|Mark unread/);
  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
});

test('select all, the bulk bar, and the Gmail keys that select', async ({ page }) => {
  await open(page);
  await page.locator('.fm-row').first().waitFor({ timeout: 90000 });
  const n = await page.locator('.fm-row').count();
  await page.locator('.fm-listhead input[type=checkbox]').check();
  await expect(page.locator('.fm-bulk')).toContainText(n + ' selected');
  for (const want of ['Archive', 'Delete', 'Spam', 'Mark read', 'Star', 'Snooze', 'Label', 'Lane', 'More']) await expect(page.locator('.fm-bulk')).toContainText(want);
  await page.locator('.fm-listhead input[type=checkbox]').uncheck();
  await expect(page.locator('.fm-bulk')).toHaveCount(0);
  await page.locator('.fm').focus();
  await page.keyboard.press('j'); await page.keyboard.press('x');
  await expect(page.locator('.fm-bulk')).toContainText('1 selected');
  await page.keyboard.press('*'); await page.keyboard.press('a');
  await expect(page.locator('.fm-bulk')).toContainText(n + ' selected');
  await page.keyboard.press('Escape');
});

test('labels and lanes come in a dark menu; native selects are dark too', async ({ page }) => {
  await open(page);
  const row = page.locator('.fm-row').first();
  await row.waitFor({ timeout: 90000 });
  await row.hover();
  await row.locator('button[aria-label^="Label"]').click();
  const menu = page.locator('.fm-menu');
  await expect(menu).toBeVisible();
  const c = await menu.evaluate(m => ({ bg: getComputedStyle(m).backgroundColor, fg: getComputedStyle(m.querySelector('.it')!).color }));
  const lum = (s: string) => { const [r, g, b] = s.match(/\d+/g)!.map(Number); return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255; };
  expect(lum(c.bg)).toBeLessThan(0.2);
  expect(lum(c.fg)).toBeGreaterThan(0.6);
  await expect(menu).toContainText('Career');                      // Friday's lanes are in the same menu
  await page.keyboard.press('Escape');
  // any native select on the page: dark scheme, dark options
  const sel = await page.evaluate(() => {
    const s = document.createElement('select'); s.innerHTML = '<option>x</option>'; document.body.appendChild(s);
    const o = s.options[0], out = { scheme: getComputedStyle(s).colorScheme, bg: getComputedStyle(o).backgroundColor, fg: getComputedStyle(o).color };
    s.remove(); return out;
  });
  expect(sel.scheme).toBe('dark');
  expect(lum(sel.bg)).toBeLessThan(0.2);
  expect(lum(sel.fg)).toBeGreaterThan(0.6);
});

test('mail is re-coloured for the dark theme, and "show original" is remembered per sender', async ({ page }) => {
  await open(page);
  await page.locator('.fm-row').first().waitFor({ timeout: 90000 });
  // an HTML message
  let found = false;
  for (let i = 0; i < 8 && !found; i++) {
    await page.locator('.fm-row').nth(i).click();
    found = await page.locator('.fm-msg iframe').first().waitFor({ timeout: 15000 }).then(() => true, () => false);
  }
  expect(found, 'no HTML message among the first rows').toBe(true);
  await page.waitForTimeout(1200);
  const frame = () => page.evaluate(() => {
    const f = document.querySelector('.fm-msg iframe') as HTMLIFrameElement, d = f.contentDocument!, w = d.defaultView!;
    const lum = (s: string) => { const m = s.match(/[\d.]+/g); if (!m) return -1; const [r, g, b] = m.map(Number); return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255; };
    const texts = [...d.body.querySelectorAll('p, li, td, div, span')].filter(e => (e.textContent || '').trim() && !e.children.length).slice(0, 30);
    return { adapted: f.dataset.adapted, body: lum(w.getComputedStyle(d.body).backgroundColor), minText: Math.min(...texts.map(e => lum(w.getComputedStyle(e).color))) };
  });
  const a = await frame();
  expect(a.adapted).toBe('1');
  expect(a.body).toBeLessThan(0.15);
  const toggle = page.locator('[data-testid="fm-theme-toggle"]').first();
  await toggle.click();
  await page.waitForTimeout(1000);
  const o = await frame();
  expect(o.adapted).toBe('0');
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('fm_show_original') || '{}'));
  expect(Object.keys(saved).length).toBe(1);
  await toggle.click();                                            // and back, so the next run starts adapted
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => localStorage.getItem('fm_show_original'))).toBe('{}');
});

test('folders and labels down the side read Gmail itself', async ({ page }) => {
  await open(page);
  await page.locator('.fm-row').first().waitFor({ timeout: 90000 });
  const side = page.locator('.fm-side');
  for (const f of ['Priority', 'Inbox', 'Starred', 'Important', 'Sent', 'Drafts', 'Scheduled', 'All Mail', 'Spam', 'Trash']) await expect(side).toContainText(f);
  const reads: string[] = [];
  page.on('request', r => { if (r.url().includes('/api/messages?')) reads.push(new URL(r.url()).searchParams.get('folder') || ''); });
  await side.locator('button', { hasText: 'Trash' }).click();
  await expect(page.locator('.fm-listhead')).toContainText('Trash');
  await expect(page.locator('.fm-banner', { hasText: 'Gmail’s Trash' })).toBeVisible();
  await side.locator('button', { hasText: 'Inbox' }).click();
  await expect(page.locator('[role=tablist]')).toContainText('Promotions');
  expect(reads).toEqual(expect.arrayContaining(['trash', 'inbox']));
});

test.describe('changes (stand-in Gmail only)', () => {
  test.skip(!WRITES, 'set FRIDAY_MAIL_WRITES=1 against a server with a stand-in Gmail');

  test('Delete moves to Trash only after Gmail confirms, and undo brings it back', async ({ page }) => {
    await open(page);
    await page.locator('.fm-row').first().waitFor({ timeout: 90000 });
    const writable = await page.evaluate(() => fetch('/api/google/accounts').then(r => r.json()).then(d => (d.accounts || []).filter((a: any) => a.mail && a.mail.modify).map((a: any) => a.label)));
    expect(writable.length).toBeGreaterThan(0);
    const row = page.locator('.fm-row').filter({ has: page.locator(`.fm-dot[title="${writable[0]}"]`) }).first();
    const subject = await row.locator('.fm-subj').textContent();
    const n = await page.locator('.fm-row').count();
    await row.hover();
    await row.locator('button[aria-label^="Delete"]').click();
    await expect(page.locator('.fm-toast')).toContainText('Moved to Trash in Gmail');
    await expect(page.locator('.fm-row')).toHaveCount(n - 1);
    await page.locator('.fm-toast button', { hasText: 'Undo' }).click();
    await expect(page.locator('.fm-toast')).toContainText('Undone');
    await expect(page.locator('.fm-row')).toHaveCount(n);
    await expect(page.locator('.fm-row .fm-subj', { hasText: subject || '' }).first()).toBeVisible();
  });

  test('Delete on a read-only account is refused, the row stays, and the way to allow it is offered', async ({ page }) => {
    await open(page);
    await page.locator('.fm-row').first().waitFor({ timeout: 90000 });
    const ro = await page.evaluate(() => fetch('/api/google/accounts').then(r => r.json()).then(d => (d.accounts || []).filter((a: any) => !(a.mail && a.mail.modify)).map((a: any) => a.label)));
    test.skip(!ro.length, 'every account here allows changes');
    await expect(page.locator('[data-testid="fm-readonly"]')).toContainText('Read-only in Gmail');
    const n = await page.locator('.fm-row').count();
    const row = page.locator('.fm-row').filter({ has: page.locator(`.fm-dot[title="${ro[0]}"]`) }).first();
    await row.hover();
    await row.locator('button[aria-label^="Delete"]').click();
    await expect(page.locator('.fm-dialog')).toContainText('Reconnect');
    await expect(page.locator('.fm-row')).toHaveCount(n);
  });

  test('3D: Trash zone, label zones, the new views, lasso and triage', async ({ page }) => {
    await open(page, '/w/messages?view3d=1');
    await page.waitForFunction(() => (window as any).__friday3d && (window as any).__friday3d.stats().visible > 5, null, { timeout: 90000 });
    const zones = (await page.locator('.f3-zones button').allInnerTexts()).join(' | ');
    expect(zones).toContain('Trash');
    expect(zones).toMatch(/Snooze · \w{3} \d/);
    const views = (await page.locator('.f3-seg button').allInnerTexts()).join(' | ');
    expect(views).toContain('River'); expect(views).toContain('Senders');
    await page.locator('button', { hasText: 'Lasso' }).click();
    const st = (await page.locator('.f3-stage').boundingBox())!;
    const cx = st.x + st.width / 2, cy = st.y + st.height / 2;
    await page.mouse.move(cx - 400, cy - 200); await page.mouse.down();
    for (const [x, y] of [[cx + 400, cy - 200], [cx + 400, cy + 200], [cx - 400, cy + 200], [cx - 400, cy - 190]]) await page.mouse.move(x, y, { steps: 6 });
    await page.mouse.up();
    await expect(page.locator('.f3-marks')).toContainText('selected');
    await page.locator('button', { hasText: 'Lasso' }).click();
    await page.locator('.f3-marks button', { hasText: 'Clear' }).click();
    await page.locator('button', { hasText: 'Triage unread' }).click();
    await expect(page.locator('[data-testid="f3-triage"]')).toContainText('Triage · 1 of');
    await page.keyboard.press(' ');
    await expect(page.locator('[data-testid="f3-triage"]')).toContainText('Triage · 2 of');
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-testid="f3-triage"]')).toHaveCount(0);
  });
});

test('three panes fill the frame when there is room; a narrow frame shows one pane at a time', async ({ page }) => {
  const box = (sel: string) => page.evaluate((sel: string) => {
    const e = document.querySelector(sel) as HTMLElement | null;
    if (!e || getComputedStyle(e).display === 'none') return null;
    const b = e.getBoundingClientRect();
    return { l: Math.round(b.left), r: Math.round(b.right), b: Math.round(b.bottom), w: Math.round(b.width) };
  }, sel);
  for (const [w, h, mode] of [[1920, 1080, 'wide'], [1366, 768, 'wide'], [1024, 768, 'mid'], [600, 820, 'narrow']] as const) {
    await page.setViewportSize({ width: w, height: h });
    await open(page);
    await page.locator('.fm-row').first().waitFor({ timeout: 90000 });
    await page.waitForTimeout(500);
    expect(await page.locator('.fm').getAttribute('class'), `${w}×${h}`).toContain('fm-' + mode);
    const side = await box('.fm-side'), list = await box('.fm-listwrap'), read = await box('.fm-thread');
    if (mode === 'wide') {
      expect(side && list && read, `${w}×${h}: folders, list and reading pane`).toBeTruthy();
      expect(side!.r).toBeLessThanOrEqual(list!.l);
      expect(list!.r).toBeLessThanOrEqual(read!.l);
      for (const b of [side!, list!, read!]) expect(h - b.b, `${w}×${h}: a pane stops short of the bottom`).toBeLessThanOrEqual(40);
      await expect(page.locator('.fm-reading-hint')).toContainText('Choose a conversation');
    }
    if (mode === 'mid') {
      expect(side, 'the folders are a drawer here').toBeNull();
      expect(list && read && list.r <= read.l).toBeTruthy();
      await page.locator('button', { hasText: 'Folders' }).first().click();
      await expect(page.locator('.fm-side')).toBeVisible();
      await page.locator('.fm-side .fm-side-close').click();
      await expect(page.locator('.fm-side')).toHaveCount(0);
    }
    if (mode === 'narrow') {
      expect(side).toBeNull();
      expect(read, 'no empty reading pane beside a narrow list').toBeNull();
      await page.locator('.fm-row').first().click();
      await expect(page.locator('.fm-thread')).toBeVisible();
      expect(await box('.fm-listwrap'), 'the list steps aside while reading').toBeNull();
      await page.locator('.fm-thread button', { hasText: 'Back to the list' }).click();
      await expect(page.locator('.fm-listwrap')).toBeVisible();
    }
  }
});

test('Snooze says what it is and offers Gmail’s choices', async ({ page }) => {
  await open(page);
  const row = page.locator('.fm-row').first();
  await row.waitFor({ timeout: 90000 });
  await row.click();
  const btn = page.locator('.fm-thread button', { hasText: 'Snooze' });
  await expect(btn).toBeVisible();
  await btn.click();
  const items = (await page.locator('.fm-menu .it').allInnerTexts()).join(' | ');
  for (const want of ['Later today', 'Tomorrow morning', 'This weekend', 'Next week', 'Pick date & time']) expect(items).toContain(want);
  await page.keyboard.press('Escape');
});

test('moving Messages into its own tab keeps the folder and the open conversation', async ({ page, context }) => {
  await open(page, '/');
  await page.waitForSelector('.dock-btn[data-ws="messages"]', { timeout: 90000 });
  await page.locator('.dock-btn[data-ws="messages"]').evaluate(el => (el as HTMLElement).click());
  const win = page.locator('.fwin:has([data-ws-tab="messages"])');
  await expect(win).toHaveCount(1);
  await win.locator('.fm-row').first().waitFor({ timeout: 90000 });
  // a folder other than the default, then a conversation in it
  const side = win.locator('.fm-side');
  if (!(await side.isVisible())) await win.locator('button', { hasText: 'Folders' }).first().click();
  await side.locator('button', { hasText: 'All Mail' }).click();
  await expect(win.locator('.fm-listhead')).toContainText('All Mail');
  const row = win.locator('.fm-row').first();
  await row.waitFor({ timeout: 60000 });
  const subject = (await row.locator('.fm-subj').textContent() || '').trim();
  await row.click();
  await expect(win.locator('.fm-thread')).toBeVisible();
  const asked = context.waitForEvent('request', r => r.resourceType() === 'document' && r.url().includes('/w/messages'));
  const [tab] = await Promise.all([context.waitForEvent('page'), win.locator('[data-ws-tab="messages"]').click()]);
  const url = new URL((await asked).url());
  expect(url.searchParams.get('folder')).toBeTruthy();
  expect(url.searchParams.get('thread_id')).toBeTruthy();
  await expect(page.locator('.fwin:has([data-ws-tab="messages"])')).toHaveCount(0);   // folded into the dock
  await tab.waitForSelector('[data-standalone="messages"] .fm-listhead', { timeout: 90000 });
  await expect(tab.locator('.fm-listhead')).toContainText('All Mail');
  await expect(tab.locator('.fm-thread')).toBeVisible({ timeout: 60000 });
  if (subject) await expect(tab.locator('.fm-thread')).toContainText(subject.slice(0, 30));
});
