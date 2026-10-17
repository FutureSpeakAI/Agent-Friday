// Google's reminders in the Day view, and a reminder that opens an event's prep card, in a real page.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The day is a fixture served by
// page.route; the prep request is answered by the spec. Capture: calendar-reminders-prep.png.
import { test, expect } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 950 } });

const pad = (n: number) => String(n).padStart(2, '0');
const T = new Date();
const day = `${T.getFullYear()}-${pad(T.getMonth() + 1)}-${pad(T.getDate())}`;
const EVENTS = [
  { id: 'e1', title: 'Dentist', start_time: `${day}T09:00:00`, end_time: `${day}T10:00:00`, all_day: false, attendees: [],
    color: '#7986cb', reminders: [{ method: 'popup', minutes: 30 }], reminders_source: 'calendar', source: 'google', type: 'normal' },
  { id: 'e2', title: 'Lunch with Dana', start_time: `${day}T12:00:00`, end_time: `${day}T13:00:00`, all_day: false,
    attendees: ['dana@example.com'], color: '#e67c73', reminders: [{ method: 'popup', minutes: 10 }, { method: 'popup', minutes: 60 }],
    reminders_source: 'event', source: 'google', type: 'normal', prep_available: true },
];
const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test('each event says its Google reminder, and a reminder for an event that matters opens its prep card', async ({ page }, info) => {
  const preps: string[] = [];
  await page.addInitScript(() => { try { localStorage.setItem('friday_cal_view', 'day'); } catch (_) { /* none */ } });
  await page.route('**/*', r => {
    const req = r.request();
    if (req.method() === 'GET') return r.fallback();
    const path = new URL(req.url()).pathname;
    if (path.startsWith('/api/calendar/prep/')) {
      preps.push(path);
      return r.fulfill(json({ status: 'ok', prep: '**Attendees & context**: Dana, an editor.\n**Talking points**: the Tuesday piece.', cached: false }));
    }
    return r.fulfill(json({ status: 'ok' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/calendar/today', r => r.fulfill(json({ status: 'ok', date: day, events: EVENTS, gaps: [], annotation: '' })));
  await page.route('**/api/calendar/day/*', r => r.fulfill(json({ status: 'ok', date: day, events: EVENTS, gaps: [], annotation: '' })));
  await page.route('**/api/calendar/tomorrow', r => r.fulfill(json({ status: 'ok', events: [], needs_prep: [] })));
  await page.route('**/api/calendar/week', r => r.fulfill(json({ status: 'ok', days: [] })));
  await page.route('**/api/media/calendar?*', r => r.fulfill(json({ cards: [] })));
  await page.goto(`${BASE}/w/calendar`, { waitUntil: 'domcontentloaded' });
  await page.locator('.cal-timeline [data-fr-ref="event:e1"]').waitFor({ timeout: 60000 });

  await expect(page.locator('.cal-timeline [data-fr-ref="event:e1"] .cal-ev-remind')).toHaveText('Reminder 30 min before');
  await expect(page.locator('.cal-timeline [data-fr-ref="event:e2"] .cal-ev-remind')).toHaveText('Reminder 10 min, 1 hour before');
  expect(preps).toEqual([]);

  // What a reminder notice's click does: the event, on its day, with its prep card
  await page.evaluate(d => (window as any).fridayNavigate({ workspace: 'calendar', event: 'e2', date: d, prep: '1' }), day);
  await expect.poll(() => preps).toEqual(['/api/calendar/prep/e2']);
  await expect(page.getByText('Talking points', { exact: false })).toBeVisible();
  await page.screenshot({ path: info.outputPath('calendar-reminders-prep.png') });
});
