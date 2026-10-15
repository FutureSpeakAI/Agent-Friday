// The Calendar's Day, Week and Month views, drawn in the owner's Google colours, in a real page.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). The calendar is a fixture served by
// page.route (two calendars, a coloured event, an all-day entry); every non-GET request is recorded and refused,
// so the spec also proves that looking at the views writes nothing. Each view is captured to the test's output
// folder (calendar-day.png, calendar-week.png, calendar-month.png) for the visual check.
import { test, expect, type Page } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 950 } });

const pad = (n: number) => String(n).padStart(2, '0');
const day = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const at = (d: Date, h: number, m = 0) => `${day(d)}T${pad(h)}:${pad(m)}:00`;
const plus = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };

const TODAY = new Date();
const HOME = '#7986cb', FAMILY = '#e67c73', BANANA = '#fbd75b';
function ev(id: string, title: string, d: Date, h: number, hours: number, color: string, cal: string, extra: object = {}) {
  return { id, title, start_time: at(d, h), end_time: at(d, h + hours), all_day: false, attendees: [], color,
           calendar_key: 'home|' + cal, calendar_id: cal, calendar_name: cal === 'fam' ? 'Family' : 'Home',
           account_id: 'home', source: 'google', type: 'normal', reminders: [], self_rsvp: '', can_edit: true, ...extra };
}
const EVENTS = [
  ev('e1', 'Dentist', TODAY, 9, 1, HOME, 'primary'),
  ev('e2', 'Book club', TODAY, 18, 2, BANANA, 'primary'),
  ev('e3', 'School play', plus(TODAY, 1), 15, 1, FAMILY, 'fam'),
  ev('e4', 'Lunch with Dana', TODAY, 12, 1, HOME, 'primary', { attendees: ['dana@example.com'] }),
  ev('e5', 'Overlapping call', TODAY, 9, 1, FAMILY, 'fam'),
  { id: 'e6', title: 'Grandparents visit', start_time: day(plus(TODAY, 2)), end_time: day(plus(TODAY, 4)), all_day: true,
    attendees: [], color: FAMILY, calendar_key: 'home|fam', calendar_id: 'fam', calendar_name: 'Family', account_id: 'home',
    source: 'google', type: 'normal' },
];
const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function openCalendar(page: Page) {
  const writes: string[] = [];
  await page.addInitScript(() => { try { localStorage.setItem('friday_cal_view', 'day'); } catch (_) { /* none */ } });
  await page.route('**/*', r => {
    if (r.request().method() === 'GET') return r.fallback();
    const path = new URL(r.request().url()).pathname;
    if (path !== '/api/desktop/state') writes.push(`${r.request().method()} ${path}`);
    return r.fulfill(json({ status: 'ok' }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  const dayOf = (iso: string) => EVENTS.filter(e => String(e.start_time).slice(0, 10) === iso);
  await page.route('**/api/calendar/today', r => r.fulfill(json({ status: 'ok', date: day(TODAY), events: dayOf(day(TODAY)), gaps: [], annotation: 'Two things this morning, then an open afternoon.', google_connected: true })));
  await page.route('**/api/calendar/tomorrow', r => r.fulfill(json({ status: 'ok', date: day(plus(TODAY, 1)), events: dayOf(day(plus(TODAY, 1))), needs_prep: [] })));
  await page.route('**/api/calendar/week', r => r.fulfill(json({ status: 'ok', days: [0, 1, 2, 3, 4, 5, 6].map(i => { const d = plus(TODAY, i); return { date: day(d), weekday: d.toLocaleDateString('en-US', { weekday: 'short' }), day: d.getDate(), count: dayOf(day(d)).length, density: 'light', has_career: false, is_today: i === 0 }; }) })));
  await page.route('**/api/calendar/day/*', r => { const iso = new URL(r.request().url()).pathname.split('/').pop() || ''; return r.fulfill(json({ status: 'ok', date: iso, events: dayOf(iso), gaps: [], annotation: '' })); });
  await page.route('**/api/calendar/range?*', r => r.fulfill(json({ status: 'ok', events: EVENTS, errors: [] })));
  await page.route('**/api/media/calendar?*', r => r.fulfill(json({ cards: [] })));
  await page.goto(`${BASE}/w/calendar`, { waitUntil: 'domcontentloaded' });
  await page.locator('.calv-switch').waitFor({ timeout: 60000 });
  return writes;
}

test('Day, Week and Month draw the events in their Google colours, and looking writes nothing', async ({ page }, info) => {
  const writes = await openCalendar(page);

  // Day: the timeline, each event in its calendar's or its own colour
  const dentist = page.locator('.cal-timeline [data-fr-ref="event:e1"]');
  await expect(dentist).toBeVisible();
  expect(await dentist.evaluate(el => (el as HTMLElement).style.getPropertyValue('--ev'))).toBe(HOME);
  expect(await page.locator('.cal-timeline [data-fr-ref="event:e2"]').evaluate(el => (el as HTMLElement).style.getPropertyValue('--ev'))).toBe(BANANA);
  // Two events at the same time sit side by side, not one on top of the other
  const d1 = await dentist.boundingBox();
  const d5 = await page.locator('.cal-timeline [data-fr-ref="event:e5"]').boundingBox();
  expect(d1 && d5 && Math.abs(d1.y - d5.y) < 2 && (d1.x + d1.width <= d5.x + 1 || d5.x + d5.width <= d1.x + 1)).toBeTruthy();
  await page.screenshot({ path: info.outputPath('calendar-day.png'), fullPage: false });

  // Week: seven columns, overlapping events side by side, the all-day entry above
  await page.locator('[data-cal-view="week"]').click();
  await expect(page.locator('.calv-week')).toBeVisible();
  await expect(page.locator('.calv-week-head .calv-dayhead')).toHaveCount(7);
  const a = await page.locator('.calv-week [data-fr-ref="event:e1"]').boundingBox();
  const b = await page.locator('.calv-week [data-fr-ref="event:e5"]').boundingBox();
  expect(a && b && Math.abs(a.y - b.y) < 2 && (a.x + a.width <= b.x + 1 || b.x + b.width <= a.x + 1)).toBeTruthy();
  expect(await page.locator('.calv-week [data-fr-ref="event:e5"]').evaluate(el => (el as HTMLElement).style.getPropertyValue('--ev'))).toBe(FAMILY);
  await page.screenshot({ path: info.outputPath('calendar-week.png'), fullPage: false });

  // Month: the grid, the all-day entry on each of its days, a day opens in the Day view
  await page.locator('[data-cal-view="month"]').click();
  await expect(page.locator('.calv-month-grid .calv-cell')).toHaveCount(42);
  await expect(page.locator('.calv-month [data-fr-ref="event:e6"]')).toHaveCount(2);
  await page.screenshot({ path: info.outputPath('calendar-month.png'), fullPage: false });
  await page.locator(`.calv-cell[data-cal-day="${day(plus(TODAY, 1))}"] .calv-cell-num`).click();
  await expect(page.locator('[data-cal-view="day"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.cal-timeline [data-fr-ref="event:e3"]')).toBeVisible();

  // Friday sees the events in every view
  const st = await page.evaluate(() => (window as any).fridayStage && (window as any).fridayStage.snapshot('calendar'));
  expect((st.items || []).map((i: any) => i.ref)).toContain('event:e3');
  expect(writes.filter(w => /calendar/.test(w))).toEqual([]);
});

test('a failed read says so instead of drawing an empty week', async ({ page }) => {
  await openCalendar(page);
  await page.unroute('**/api/calendar/range?*');
  await page.route('**/api/calendar/range?*', r => r.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ status: 'error', message: 'Google did not answer.' }) }));
  await page.locator('[data-cal-view="week"]').click();
  await expect(page.getByText("Couldn't read your calendar.")).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});
