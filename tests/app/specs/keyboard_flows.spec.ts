// The key flows, done the way a keyboard and screen-reader user does them (north star 34.14-2; burn-down BD-21:
// "a keyboard-only and a screen-reader run of the first-run flows pass as tests").
//
// No mouse anywhere. Controls are reached with Tab (or the arrow keys inside a tab list) and found by the name a
// screen reader announces: role plus accessible name. The flows: find the privacy settings; approve one waiting
// action and deny another; stop a running task; find a receipt. (The first-run choices are radio groups; AX1's
// accessibility_keyboard.spec.ts drives them by keyboard.)
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Every write is answered here and
// recorded; nothing reaches a Friday.
import { test, expect, type Page } from '../fixtures';
import { bootApp, BASE } from '../harness';
import { settle } from '../helpers/settle';

test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
// Wide enough that the top bar keeps the Activity chip in the bar, not behind its overflow.
test.use({ viewport: { width: 1920, height: 1080 } });

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const now = Math.floor(Date.now() / 1000);
const card = (id: string, title: string) => ({
  approval_id: id, kind: 'media_diet_rule', status: 'pending', title, action_description: 'Friday would ' + title.toLowerCase() + '.',
  description: 'A synthetic request.', payload: {}, created_at: now, expires_at: now + 3600, policy_class: 'outward',
});
const A = card('appr_kb_a', 'Add the rule limit news to 20 minutes');
const B = card('appr_kb_b', 'Add the rule block one site');
const TASK = { id: 'tk1', source: 'task', kind: 'task', label: 'Research the thing', model: '', status: 'running', trace_id: 'tr_kb',
  queue_position: 0, stoppable: true, stop: { method: 'POST', url: '/api/tasks/tk1/stop-after-step' } };
const TRACE = { trace_id: 'tr_kb', kind: 'task', label: 'Research the thing', model: 'gemma-test', status: 'complete', started: now - 600 };

async function fixtures(page: Page) {
  const writes: { url: string; body: any }[] = [];
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : (() => {
    const q = r.request();
    let body: any = null;
    try { body = q.postData() ? JSON.parse(q.postData() || 'null') : null; } catch { body = q.postData(); }
    writes.push({ url: new URL(q.url()).pathname, body });
    return r.fulfill(json({ ok: true, status: 'ok' }));
  })()));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  // Two actions wait. The stream says so once and asks the browser not to reconnect for ten minutes, so a decided
  // card does not come back.
  await page.route('**/api/approvals/events', r => r.fulfill({
    status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
    body: 'retry: 600000\n\ndata: ' + JSON.stringify({ type: 'snapshot', pending: [A, B], quiet: [] }) + '\n\n',
  }));
  await page.route('**/api/lanes/summary', r => r.fulfill(json({ needs_you: 0, approvals: 0, summary: '', running: 1, queued: 0,
    activity: 1, updates: 0, held_sends: 0, quiet: { active: false } })));
  await page.route('**/api/lanes/needs-you', r => r.fulfill(json({ count: 0, approvals: 0, batches: [], singles: [], others: [], summary: '',
    quiet: { active: false, why: '', until: 0 } })));
  await page.route('**/api/lanes/activity', r => r.fulfill(json({ running: 1, queued: 0, count: 1, tasks: [TASK] })));
  await page.route('**/api/lanes/updates**', r => r.fulfill(json({ count: 0, today_only: true, groups: [] })));
  await page.route('**/api/lanes/receipts**', r => r.fulfill(json({ count: 1, traces: [TRACE] })));
  await page.route('**/api/local-queue', r => r.fulfill(json({ jobs: [], running: [], queued: [], count: 0 })));
  return writes;
}

/** What has focus, as a screen reader would announce it: role and accessible name, plus the group it sits in. */
async function focused(page: Page) {
  return page.evaluate(() => {
    const e = document.activeElement as HTMLElement | null;
    if (!e || e === document.body) return { name: '', role: '', group: '', text: '' };
    const byId = (ids: string) => ids.split(/\s+/).map(i => document.getElementById(i)?.textContent || '').join(' ');
    const name = (e.getAttribute('aria-label') || (e.getAttribute('aria-labelledby') ? byId(e.getAttribute('aria-labelledby')!) : '') ||
      e.textContent || '').replace(/\s+/g, ' ').trim();
    const g = e.closest('[role="group"]');
    return { name, role: e.getAttribute('role') || e.tagName.toLowerCase(), group: g ? g.getAttribute('aria-label') || '' : '', text: (e.textContent || '').trim() };
  });
}

type Focus = Awaited<ReturnType<typeof focused>>;

/** Press Tab until what has focus matches (at most `max` presses), then, if it never did, Shift+Tab the other way
 *  round. Fails naming what Tab passed through. Where focus already is counts. */
async function tabTo(page: Page, what: string, match: (f: Focus) => boolean, max = 80) {
  const now = await focused(page);
  if (match(now)) return now;
  const seen: string[] = [];
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let i = 0; i < max; i++) {
      await page.keyboard.press(key);
      const f = await focused(page);
      if (match(f)) return f;
      seen.push(f.role + ' "' + f.name.slice(0, 30) + '"');
    }
  }
  throw new Error(`Tab never reached ${what} in ${max} presses each way. It went: ${seen.slice(-30).join(' → ')}`);
}

async function start(page: Page) {
  const writes = await fixtures(page);
  await bootApp(page);
  await settle(page, { quietMs: 800, timeout: 15_000 });
  await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); });
  return writes;
}

test('the privacy settings, found and opened by keyboard', async ({ page }) => {
  await start(page);
  // A screen-reader user jumps to the dock's Settings button (it is a real button, named Settings) and presses Enter.
  const dock = page.locator('.dock-btn[data-ws="settings"]').first();
  await dock.focus();
  await page.keyboard.press('Enter');
  const rail = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(rail).toBeVisible({ timeout: 30_000 });
  // A screen-reader user moves by landmark: to the navigation named "Settings sections", then Tab along it.
  await rail.getByRole('button').first().focus();
  await tabTo(page, 'the "Privacy & Data" section', f => f.name === 'Privacy & Data', 30);
  await page.keyboard.press('Enter');
  await expect(rail.getByRole('button', { name: 'Privacy & Data', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'Privacy & Data' }).first()).toBeVisible();
});

test('approve one waiting action and deny another, by keyboard, each heard inside its own card', async ({ page }) => {
  const writes = await start(page);
  await expect(page.getByRole('group', { name: A.title })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('group', { name: B.title })).toBeVisible();
  const a = await tabTo(page, `"Approve" in "${A.title}"`, f => f.name === 'Approve' && f.group === A.title);
  expect(a.group, 'the button is announced with the card it belongs to').toBe(A.title);
  await page.keyboard.press('Enter');
  await expect.poll(() => writes.filter(w => w.url === `/api/approvals/${A.approval_id}/decide`).map(w => w.body && w.body.decision))
    .toEqual(['approve']);
  // The decided card leaves, and the other card's buttons are enabled again once the decision is answered.
  await expect(page.getByRole('group', { name: A.title })).toHaveCount(0);
  await expect(page.getByRole('group', { name: B.title }).getByRole('button', { name: 'Deny' })).toBeEnabled();
  await tabTo(page, `"Deny" in "${B.title}"`, f => f.name === 'Deny' && f.group === B.title);
  await page.keyboard.press('Space');
  await expect.poll(() => writes.filter(w => w.url === `/api/approvals/${B.approval_id}/decide`).map(w => w.body && w.body.decision))
    .toEqual(['deny']);
  expect(writes.filter(w => /\/decide$/.test(w.url)).length, 'one decision each, nothing else decided').toBe(2);
});

async function openActivity(page: Page) {
  const chip = page.getByTestId('local-ai-queue-chip');
  const more = page.getByRole('button', { name: 'More Friday controls' });
  await expect(chip.or(more).first()).toBeVisible({ timeout: 30_000 });
  if (!(await chip.isVisible())) {
    // The top bar tucked the chip behind its overflow: open that by keyboard first.
    await more.focus();
    await page.keyboard.press('Enter');
    await expect(chip).toBeVisible();
  }
  await chip.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Activity' });
  await expect(dialog).toBeVisible();
  // Into the tab list; Home goes to the first tab, and focus goes with the selection.
  await tabTo(page, 'a tab in the Activity dialog', f => f.role === 'tab', 20);
  await page.keyboard.press('Home');
  await expect.poll(() => focused(page).then(f => f.name)).toMatch(/^Needs you/);
  return dialog;
}

test('stop a running task, by keyboard', async ({ page }) => {
  const writes = await start(page);
  const dialog = await openActivity(page);
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => focused(page).then(f => f.role + ':' + f.name), 'the arrow key moves focus to the next tab')
    .toMatch(/^tab:Activity/);
  await expect(dialog.getByRole('tab', { name: /^Activity/ })).toHaveAttribute('aria-selected', 'true');
  await tabTo(page, 'the task\'s "Stop" button', f => f.name === 'Stop', 20);
  await page.keyboard.press('Enter');
  await expect.poll(() => writes.map(w => w.url)).toContain('/api/tasks/tk1/stop-after-step');
});

test('find a receipt and open it, by keyboard, the button naming which one', async ({ page }) => {
  await start(page);
  await page.evaluate(() => { (window as any).__openedTrace = null; (window as any).fridayOpenTrace = (id: string) => { (window as any).__openedTrace = id; }; });
  const dialog = await openActivity(page);
  await page.keyboard.press('End');
  await expect.poll(() => focused(page).then(f => f.role + ':' + f.name)).toMatch(/^tab:Receipts/);
  await expect(dialog.getByRole('tab', { name: /^Receipts/ })).toHaveAttribute('aria-selected', 'true');
  await tabTo(page, 'the receipt\'s open button', f => f.name === 'Open the trace: ' + TRACE.label, 20);
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => (window as any).__openedTrace)).toBe(TRACE.trace_id);
});
