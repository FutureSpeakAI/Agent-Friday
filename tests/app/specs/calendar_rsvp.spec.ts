// Replying to an invitation in a real page: three buttons, a confirmation that names the organiser, then one request.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Events are fixtures served by
// page.route; writes are recorded and answered by the spec. Capture: calendar-rsvp-confirm.png.
import { test, expect } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 950 } });

const pad = (n: number) => String(n).padStart(2, '0');
const T = new Date();
const day = `${T.getFullYear()}-${pad(T.getMonth() + 1)}-${pad(T.getDate())}`;
const EVENTS = [
  { id: 'inv', title: 'Lunch with Dana', start_time: `${day}T12:00:00`, end_time: `${day}T14:30:00`, all_day: false, attendees: ['dana@example.com', 'me@example.com'],
    color: '#e67c73', calendar_key: 'home|fam', calendar_name: 'Family', source: 'google', type: 'normal', self_rsvp: 'needsAction', organizer_self: false, can_edit: false },
  { id: 'mine', title: 'Book club', start_time: `${day}T18:00:00`, end_time: `${day}T19:30:00`, all_day: false, attendees: ['sam@example.com'],
    color: '#7986cb', calendar_key: 'home|primary', calendar_name: 'Home', source: 'google', type: 'normal', self_rsvp: 'accepted', organizer_self: true, can_edit: true },
];
const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test('a reply asks first, says the organiser will know, and sends exactly the chosen answer', async ({ page }, info) => {
  const writes: { path: string, body: any }[] = [];
  await page.addInitScript(() => { try { localStorage.setItem('friday_cal_view', 'day'); } catch (_) { /* none */ } });
  await page.route('**/*', r => {
    const req = r.request();
    if (req.method() === 'GET') return r.fallback();
    const path = new URL(req.url()).pathname;
    if (path !== '/api/desktop/state') { let body: any = null; try { body = req.postDataJSON(); } catch (_) { body = null; } writes.push({ path, body }); }
    return r.fulfill(json({ status: 'ok', response: 'accepted', before: 'needsAction' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/calendar/today', r => r.fulfill(json({ status: 'ok', date: day, events: EVENTS, gaps: [], annotation: '' })));
  await page.route('**/api/calendar/tomorrow', r => r.fulfill(json({ status: 'ok', events: [], needs_prep: [] })));
  await page.route('**/api/calendar/week', r => r.fulfill(json({ status: 'ok', days: [] })));
  await page.route('**/api/media/calendar?*', r => r.fulfill(json({ cards: [] })));
  await page.goto(`${BASE}/w/calendar`, { waitUntil: 'domcontentloaded' });
  const lunch = page.locator('.cal-timeline [data-fr-ref="event:inv"]');
  await lunch.waitFor({ timeout: 60000 });

  await expect(lunch.getByRole('group', { name: 'Your reply to Lunch with Dana' })).toContainText('Not replied');
  await expect(page.locator('.cal-timeline [data-fr-ref="event:mine"] .calv-rsvp')).toHaveCount(0);   // she organises it
  await lunch.getByRole('button', { name: 'Going' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Reply “Going” to “Lunch with Dana”?');
  await expect(dialog).toContainText('Google will let the organiser know.');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.screenshot({ path: info.outputPath('calendar-rsvp-confirm.png') });
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(writes.filter(w => /rsvp/.test(w.path))).toEqual([]);

  await lunch.getByRole('button', { name: 'Maybe' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Send reply' }).click();
  await expect.poll(() => writes.filter(w => /rsvp/.test(w.path)).length).toBe(1);
  expect(writes.find(w => /rsvp/.test(w.path))).toEqual({ path: '/api/calendar/events/inv/rsvp',
    body: { calendar_key: 'home|fam', response: 'tentative', confirmed: true } });
});
