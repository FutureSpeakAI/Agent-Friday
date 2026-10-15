// Which calendars, and free/busy, in a real page: the owner's ticks, Friday's "only these" chip, the week's open time.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Calendars, events and free/busy are
// fixtures served by page.route; writes are recorded and answered by the spec.
// Captures: calendar-calendars-panel.png, calendar-only-chip.png, calendar-freebusy.png.
import { test, expect } from '../fixtures';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1500, height: 950 } });

const pad = (n: number) => String(n).padStart(2, '0');
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const T = new Date();
const today = iso(T);
const CALS = [
  { key: 'home|primary', account_id: 'home', calendar_id: 'primary', name: 'Home', color: '#7986cb', primary: true, on: true, google_selected: true, access_role: 'owner' },
  { key: 'home|fam', account_id: 'home', calendar_id: 'fam', name: 'Family', color: '#e67c73', primary: false, on: true, google_selected: true, access_role: 'writer' },
  { key: 'home|hol', account_id: 'home', calendar_id: 'hol', name: 'Holidays', color: '#0b8043', primary: false, on: false, google_selected: false, access_role: 'reader' },
  { key: 'old|primary', account_id: 'old', calendar_id: 'primary', name: 'Old account', color: '#9fc6e7', primary: true, on: true, google_selected: true, access_role: 'owner' },
];
const ACCOUNTS = [
  { account_id: 'home', label: 'Home', can_list: true, reason: '' },
  { account_id: 'old', label: 'Old account', can_list: false, reason: 'This connection can read only its main calendar. Reconnect Google in Settings > Connections to see the others.' },
];
const EVENTS = [
  { id: 'e1', title: 'Dentist', start_time: `${today}T09:00:00`, end_time: `${today}T10:00:00`, all_day: false, attendees: [], color: '#7986cb', calendar_key: 'home|primary', calendar_name: 'Home', source: 'google', type: 'normal' },
  { id: 'e2', title: 'School play', start_time: `${today}T15:00:00`, end_time: `${today}T16:00:00`, all_day: false, attendees: [], color: '#e67c73', calendar_key: 'home|fam', calendar_name: 'Family', source: 'google', type: 'normal' },
];
function weekDays() {
  const s = new Date(T); s.setDate(s.getDate() - s.getDay());
  return [0, 1, 2, 3, 4, 5, 6].map(i => { const d = new Date(s); d.setDate(d.getDate() + i); return d; });
}
const FREEBUSY = {
  status: 'ok', errors: [], free_minutes: 1500, working: { start: '09:00', end: '17:00', days: [0, 1, 2, 3, 4] },
  days: weekDays().map(d => {
    const day = iso(d), work = d.getDay() >= 1 && d.getDay() <= 5;
    return { date: day, working: work, free_minutes: work ? 300 : 0,
      busy: [{ start: `${day}T09:00:00`, end: `${day}T11:00:00`, calendars: ['Home', 'Family'] }],
      free: work ? [{ start: `${day}T11:00:00`, end: `${day}T16:00:00`, minutes: 300 }] : [] };
  }),
};
const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

test('the owner ticks calendars, Friday narrows a view with a chip, and free/busy shows the open time', async ({ page }, info) => {
  const writes: { path: string, body: any }[] = [];
  const ranges: string[] = [];
  await page.addInitScript(() => { try { localStorage.setItem('friday_cal_view', 'week'); } catch (_) { /* none */ } });
  await page.route('**/*', r => {
    const req = r.request();
    if (req.method() === 'GET') return r.fallback();
    const path = new URL(req.url()).pathname;
    if (path !== '/api/desktop/state') { let body: any = null; try { body = req.postDataJSON(); } catch (_) { body = null; } writes.push({ path, body }); }
    return r.fulfill(json({ status: 'ok', visible: {} }));
  });
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/calendar/calendars', r => r.fulfill(json({ status: 'ok', calendars: CALS, accounts: ACCOUNTS, errors: [] })));
  await page.route('**/api/calendar/today', r => r.fulfill(json({ status: 'ok', date: today, events: EVENTS, gaps: [], annotation: '' })));
  await page.route('**/api/calendar/tomorrow', r => r.fulfill(json({ status: 'ok', events: [], needs_prep: [] })));
  await page.route('**/api/calendar/week', r => r.fulfill(json({ status: 'ok', days: [] })));
  await page.route('**/api/media/calendar?*', r => r.fulfill(json({ cards: [] })));
  await page.route('**/api/calendar/range?*', r => {
    const u = new URL(r.request().url()); ranges.push(u.search);
    const keys = (u.searchParams.get('keys') || '').split(',').filter(Boolean);
    return r.fulfill(json({ status: 'ok', errors: [], events: keys.length ? EVENTS.filter(e => keys.includes(e.calendar_key)) : EVENTS }));
  });
  await page.route('**/api/calendar/freebusy?*', r => r.fulfill(json(FREEBUSY)));
  await page.goto(`${BASE}/w/calendar`, { waitUntil: 'domcontentloaded' });
  await page.locator('.calv-week [data-fr-ref="event:e1"]').waitFor({ timeout: 60000 });

  // The owner's ticks: every calendar, grouped by account, with the old connection's reason
  await page.locator('[data-cal-calendars="toggle"]').click();
  const panel = page.getByRole('dialog', { name: 'Calendars shown in Friday' });
  await expect(panel).toContainText('never Google');
  await expect(panel).toContainText('Reconnect Google');
  await expect(panel.getByRole('checkbox', { name: 'Holidays' })).not.toBeChecked();
  await page.screenshot({ path: info.outputPath('calendar-calendars-panel.png') });
  await panel.getByRole('checkbox', { name: 'Family' }).click();
  await expect.poll(() => writes.filter(w => w.path === '/api/calendar/calendars/visible').map(w => w.body)).toEqual([{ changes: { 'home|fam': false } }]);
  await panel.getByRole('button', { name: 'Close' }).click();

  // Friday's "only these": a chip that says who chose it, cleared with its x; nothing written
  const before = writes.length;
  await page.evaluate(() => (window as any).fridayNavigate({ workspace: 'calendar', only: 'cal-family' }));
  await expect(page.locator('.calv-chip-friday')).toContainText('Only Family');
  await expect.poll(() => ranges.some(q => q.includes('keys=home%7Cfam'))).toBe(true);
  await expect(page.locator('.calv-week [data-fr-ref="event:e1"]')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('calendar-only-chip.png') });
  await page.getByRole('button', { name: 'Show every calendar again' }).click();
  await expect(page.locator('.calv-chip-friday')).toHaveCount(0);
  await expect(page.locator('.calv-week [data-fr-ref="event:e1"]')).toBeVisible();
  expect(writes.length).toBe(before);

  // Free/busy: busy names calendars, free shows its length, no event title anywhere
  await page.locator('[data-cal-view="freebusy"]').click();
  await expect(page.locator('.calv-fb-sum')).toContainText('25 h free this week');
  await expect(page.locator('.calv-busy').first()).toContainText('Busy · Home, Family');
  await expect(page.locator('.calv-free').first()).toContainText('Free · 5 h');
  await expect(page.locator('.calv-fb')).not.toContainText('Dentist');
  await page.screenshot({ path: info.outputPath('calendar-freebusy.png') });
});
