// Deleting an event in a real page: on the event, a confirmation that names it, Cancel in focus, then one request.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Events are fixtures; the delete route
// is answered by the spec, so nothing reaches any calendar. Capture: calendar-delete-confirm.png.
import { test, expect } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 950 } });

const pad = (n: number) => String(n).padStart(2, '0');
const T = new Date();
const day = `${T.getFullYear()}-${pad(T.getMonth() + 1)}-${pad(T.getDate())}`;
const EVENTS = [
  { id: 'e1', title: 'Dentist', start_time: `${day}T09:00:00`, end_time: `${day}T11:00:00`, all_day: false, attendees: [],
    color: '#7986cb', calendar_key: 'home|primary', calendar_id: 'primary', account_id: 'home', calendar_name: 'Home', source: 'google', type: 'normal', can_edit: true },
  { id: 'e2', title: 'Shared webinar', start_time: `${day}T13:00:00`, end_time: `${day}T15:00:00`, all_day: false, attendees: [],
    color: '#e67c73', calendar_key: 'home|fam', calendar_id: 'fam', account_id: 'home', calendar_name: 'Family', source: 'google', type: 'normal', can_edit: false },
];
const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test('delete asks with the event named, Cancel changes nothing, Delete sends exactly that event', async ({ page }, info) => {
  const writes: { path: string, body: any }[] = [];
  await page.addInitScript(() => { try { localStorage.setItem('friday_cal_view', 'day'); } catch (_) { /* none */ } });
  await page.route('**/*', r => {
    const req = r.request();
    if (req.method() === 'GET') return r.fallback();
    const path = new URL(req.url()).pathname;
    if (path !== '/api/desktop/state') { let body: any = null; try { body = req.postDataJSON(); } catch (_) { body = null; } writes.push({ path, body }); }
    return r.fulfill(json({ ok: true, id: 'e1', title: 'Dentist' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/calendar/today', r => r.fulfill(json({ status: 'ok', date: day, events: EVENTS, gaps: [], annotation: '' })));
  await page.route('**/api/calendar/tomorrow', r => r.fulfill(json({ status: 'ok', events: [], needs_prep: [] })));
  await page.route('**/api/calendar/week', r => r.fulfill(json({ status: 'ok', days: [] })));
  await page.route('**/api/media/calendar?*', r => r.fulfill(json({ cards: [] })));
  await page.goto(`${BASE}/w/calendar`, { waitUntil: 'domcontentloaded' });
  const dentist = page.locator('.cal-timeline [data-fr-ref="event:e1"]');
  await dentist.waitFor({ timeout: 60000 });

  await expect(page.locator('.cal-timeline [data-fr-ref="event:e2"] .calv-delete')).toHaveCount(0);
  await dentist.getByRole('button', { name: 'Delete “Dentist”' }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('Delete “Dentist”?');
  await expect(dialog).toContainText('It is removed from Google Calendar.');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.screenshot({ path: info.outputPath('calendar-delete-confirm.png') });
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  expect(writes.filter(w => /delete/.test(w.path))).toEqual([]);

  await dentist.getByRole('button', { name: 'Delete “Dentist”' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete event' }).click();
  await expect.poll(() => writes.filter(w => /delete/.test(w.path)).length).toBe(1);
  expect(writes.find(w => /delete/.test(w.path))).toEqual({ path: '/api/calendar/events/e1/delete',
    body: { calendar_id: 'primary', account_id: 'home', confirmed: true } });
});
