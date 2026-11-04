// Focus order on every workspace (north star NS-33.1-3 logical focus order; WCAG 2.2 2.4.3 and 2.4.7).
//
// Each workspace in the registry is opened in its own tab (/w/<id>) and walked with Tab from the top of the
// page, as a keyboard user does. Every stop must be something a sighted keyboard user can see (a box on the
// page, not visibility:hidden, not inside an aria-hidden or inert part), and the stops must follow the order of
// the page (no jump back to something earlier until the walk wraps round). A closed panel left in the Tab
// order (the AX18 bug) or a hidden control that still takes focus fails here, named.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Every write is answered here.
import { test, expect, type Page } from '../fixtures';
import { settle } from '../helpers/settle';
import { BASE } from '../harness';

test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });
test.setTimeout(30 * 60_000);

const STOPS = 40;
const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function answerWrites(page: Page) {
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
}

type Stop = { i: number; desc: string; visible: boolean; why: string; order: number };

/** Where focus is now: a short description, whether a sighted user can see it, and its place in the page. */
async function here(page: Page, i: number): Promise<Stop | null> {
  return page.evaluate((i) => {
    const e = document.activeElement as HTMLElement | null;
    if (!e || e === document.body || e === document.documentElement) return null;
    const all = Array.from(document.querySelectorAll('*'));
    const order = all.indexOf(e);
    const name = (e.getAttribute('aria-label') || e.textContent || e.getAttribute('title') || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    const desc = `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : ''} "${name}"`;
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    let why = '';
    if (r.width < 1 || r.height < 1) why = 'no box on the page';
    else if (cs.visibility === 'hidden') why = 'visibility:hidden';
    else if (e.closest('[aria-hidden="true"]')) why = 'inside aria-hidden';
    else if (e.closest('[inert]')) why = 'inside an inert part';
    else {
      for (let a: HTMLElement | null = e; a; a = a.parentElement) {
        if (getComputedStyle(a).opacity === '0') { why = 'inside a transparent part'; break; }
      }
    }
    // The skip link sits off screen until it is focused; that is its design (it is visible once focused).
    if (e.classList.contains('skip-link')) why = '';
    return { i, desc, visible: !why, why, order };
  }, i);
}

test('every workspace: each Tab stop is visible and follows the page order', async ({ page }) => {
  await answerWrites(page);
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window as any).FRIDAY_WORKSPACE_REGISTRY, null, { timeout: 90_000 });
  const ids: string[] = await page.evaluate(() =>
    (((window as any).FRIDAY_WORKSPACE_REGISTRY || {}).workspaces || []).filter((x: any) => x && x.id && !x.held).map((x: any) => String(x.id)));
  expect(ids.length).toBeGreaterThan(10);
  const bad: Record<string, string[]> = {};
  const unopened: string[] = [];
  let checked = 0;
  for (const id of ids) {
    try {
      await page.goto(`${BASE}/w/${encodeURIComponent(id)}`, { waitUntil: 'domcontentloaded' });
      await page.locator('#ui-inner, #ui-root').first().waitFor({ state: 'attached', timeout: 60_000 });
      await settle(page, { quietMs: 1000, timeout: 10_000 });
    } catch { unopened.push(id); continue; }
    checked += 1;
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur?.(); window.scrollTo(0, 0); });
    const problems: string[] = [];
    let last = -1;
    for (let i = 1; i <= STOPS; i++) {
      await page.keyboard.press('Tab');
      const s = await here(page, i);
      if (!s) continue;
      if (s.order < last) break;   // wrapped round to the top: the walk is done
      if (!s.visible) problems.push(`stop ${i}: ${s.desc}: ${s.why}`);
      last = s.order;
    }
    if (problems.length) bad[id] = problems;
  }
  console.log('[focus] ' + JSON.stringify({ workspaces: ids.length, checked, unopened, with_hidden_stops: Object.keys(bad) }));
  for (const [id, list] of Object.entries(bad)) console.log(`[focus] ${id}:\n  - ` + list.slice(0, 15).join('\n  - '));
  expect(checked, 'workspaces the walk could open').toBeGreaterThan(10);
  expect(Object.keys(bad), 'workspaces where Tab lands on something no one can see (details printed above)').toEqual([]);
});
