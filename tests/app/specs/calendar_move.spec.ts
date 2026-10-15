// Moving and adding events in a real page: a drag asks first, a slipped drag asks nothing, quick add shows first.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The calendar is a fixture served by
// page.route and every write request is recorded and answered by the spec, so nothing reaches any calendar.
// Captures: calendar-move-confirm.png, calendar-move-undo.png, calendar-quickadd-preview.png.
import { test, expect, type Page } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 950 } });

const pad = (n: number) => String(n).padStart(2, '0');
const day = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const TODAY = new Date();
const at = (h: number) => `${day(TODAY)}T${pad(h)}:00:00`;
const EVENTS = [
  { id: 'e1', title: 'Dentist', start_time: at(9), end_time: at(10), all_day: false, attendees: ['dr@example.com'], color: '#7986cb',
    calendar_key: 'home|primary', calendar_id: 'primary', calendar_name: 'Home', account_id: 'home', source: 'google', type: 'normal', can_edit: true },
  { id: 'e2', title: 'Shared webinar', start_time: at(13), end_time: at(14), all_day: false, attendees: [], color: '#e67c73',
    calendar_key: 'home|fam', calendar_id: 'fam', calendar_name: 'Family', account_id: 'home', source: 'google', type: 'normal', can_edit: false },
];
const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });

type Write = { method: string, path: string, body: any };
async function openWeek(page: Page, view = 'week') {
  const writes: Write[] = [];
  await page.addInitScript(v => { try { localStorage.setItem('friday_cal_view', v); } catch (_) { /* none */ } }, view);
  await page.route('**/*', r => {
    const req = r.request();
    if (req.method() === 'GET') return r.fallback();
    const path = new URL(req.url()).pathname;
    if (path === '/api/desktop/state') return r.fulfill(json({ status: 'ok' }));
    let body: any = null; try { body = req.postDataJSON(); } catch (_) { body = null; }
    writes.push({ method: req.method(), path, body });
    if (/\/api\/calendar\/events\/[^/]+\/move$/.test(path)) return r.fulfill(json({ status: 'ok', receipt_id: 'rcpt_0000000000000000000' }));
    if (path === '/api/calendar/quick-add') return r.fulfill(json({ status: 'ok', parsed_by: 'code',
      preview: { title: 'Lunch with Dana', start_time: at(12) + '-05:00', end_time: at(13) + '-05:00', location: '', attendees: [] },
      calendars: [{ key: 'home|primary', name: 'Home', primary: true, on: true }, { key: 'home|fam', name: 'Family', primary: false, on: true }] }));
    if (path === '/api/calendar/events') return r.fulfill(json({ status: 'ok', created_in_google: true, event: { id: 'n1', title: 'Lunch with Dana' } }));
    return r.fulfill(json({ status: 'ok' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/calendar/today', r => r.fulfill(json({ status: 'ok', date: day(TODAY), events: EVENTS, gaps: [], annotation: '' })));
  await page.route('**/api/calendar/tomorrow', r => r.fulfill(json({ status: 'ok', events: [], needs_prep: [] })));
  await page.route('**/api/calendar/week', r => r.fulfill(json({ status: 'ok', days: [] })));
  await page.route('**/api/calendar/range?*', r => r.fulfill(json({ status: 'ok', events: EVENTS, errors: [] })));
  await page.route('**/api/media/calendar?*', r => r.fulfill(json({ cards: [] })));
  await page.goto(`${BASE}/w/calendar`, { waitUntil: 'domcontentloaded' });
  await page.locator(`${view === 'week' ? '.calv-week' : '.cal-timeline'} [data-fr-ref="event:e1"]`).waitFor({ timeout: 60000 });
  return writes;
}
async function drag(page: Page, ref: string, dy: number, dx = 0, scope = '.calv-week') {
  const box = (await page.locator(`${scope} [data-fr-ref="${ref}"]`).boundingBox())!;
  const x = box.x + box.width / 2, y = box.y + 10;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx / 2, y + dy / 2, { steps: 4 });
  await page.mouse.move(x + dx, y + dy, { steps: 4 });
  await page.mouse.up();
}
const moves = (w: Write[]) => w.filter(x => /\/move$/.test(x.path));

test('a drag asks first: Cancel changes nothing, Move sends exactly the shown time, and Undo is offered', async ({ page }, info) => {
  const writes = await openWeek(page);
  await drag(page, 'event:e1', 72);                  // 48 px an hour: an hour and a half later
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Move “Dentist”?');
  await expect(dialog).toContainText('Google will not email them');
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.screenshot({ path: info.outputPath('calendar-move-confirm.png') });
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  expect(moves(writes)).toEqual([]);

  await drag(page, 'event:e1', 72);
  await page.getByRole('button', { name: 'Move', exact: true }).click();
  await expect.poll(() => moves(writes).length).toBe(1);
  const sent = moves(writes)[0];
  expect(sent.path).toBe('/api/calendar/events/e1/move');
  expect(sent.body.confirmed).toBe(true);
  expect(sent.body.calendar_key).toBe('home|primary');
  expect(new Date(sent.body.start_time).getHours() * 60 + new Date(sent.body.start_time).getMinutes()).toBe(10 * 60 + 30);
  await expect(page.getByRole('button', { name: 'Undo' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('calendar-move-undo.png') });
});

test('a slipped drag, Esc mid-drag, and an event she cannot edit ask nothing and send nothing', async ({ page }) => {
  // In the Day view, where a click on an event without prep does nothing, so only the drag is under test.
  const writes = await openWeek(page, 'day');
  const day = '.cal-timeline';
  await drag(page, 'event:e1', 3, 0, day);           // a twitch
  await drag(page, 'event:e1', 9, 2, day);           // under one 15-minute step (13 px here)
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await drag(page, 'event:e2', 104, 0, day);         // a shared event she may not change
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const box = (await page.locator(`${day} [data-fr-ref="event:e1"]`).boundingBox())!;
  await page.mouse.move(box.x + 20, box.y + 10);
  await page.mouse.down();
  await page.mouse.move(box.x + 20, box.y + 120, { steps: 5 });
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(moves(writes)).toEqual([]);
});

test('quick add shows the event and its calendar first; only Add writes it, where she chose', async ({ page }, info) => {
  const writes = await openWeek(page);
  await page.locator('.cal-quickadd input').fill('lunch with Dana Tuesday 12pm');
  await page.locator('.cal-quickadd input').press('Enter');
  const preview = page.getByRole('group', { name: 'Check the event before adding it' });
  await expect(preview).toContainText('Lunch with Dana');
  expect(writes.filter(w => w.path === '/api/calendar/events')).toEqual([]);
  await preview.locator('select').selectOption('home|fam');
  await page.screenshot({ path: info.outputPath('calendar-quickadd-preview.png') });
  await preview.getByRole('button', { name: /^Add to Family$/ }).click();
  await expect.poll(() => writes.filter(w => w.path === '/api/calendar/events').length).toBe(1);
  const add = writes.find(w => w.path === '/api/calendar/events')!;
  expect(add.body).toMatchObject({ title: 'Lunch with Dana', calendar_key: 'home|fam', confirmed: true });
  await expect(preview).toHaveCount(0);
});
