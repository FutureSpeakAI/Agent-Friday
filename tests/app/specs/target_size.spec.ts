// Target size (WCAG 2.2 AA 2.5.8; north star 33.1-13; burn-down BD-21): every control a person presses is at least
// 24 by 24 CSS pixels, and in Big mode at least 44 by 44. Measured on the desktop, in Settings and in a few
// workspaces, each in its own tab. Inline links inside running text are exempt, as WCAG allows.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Every write is answered here.
import { test, expect, type Page } from '../fixtures';
import { settle } from '../helpers/settle';
import { BASE } from '../harness';

test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });
test.setTimeout(10 * 60_000);

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
const CONTROLS = 'button, [role="button"], [role="tab"], [role="switch"], [role="radio"], [role="checkbox"], '
  + '[role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"], [role="option"], select, summary, '
  + 'input[type="button"], input[type="submit"], input[type="reset"]';
const PAGES = ['/', '/w/settings', '/w/calendar', '/w/messages', '/w/tasks', '/w/knowledge'];

async function small(page: Page, min: number): Promise<string[]> {
  return page.evaluate(([sel, m]) => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll(sel as string)) as HTMLElement[]) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) continue;
      if (cs.display === 'inline') continue;                       // inline in running text: exempt
      if (el.closest('[aria-hidden="true"], [inert]')) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;               // not drawn
      if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) continue;  // off screen
      if (r.width + 0.5 < (m as number) || r.height + 0.5 < (m as number)) {
        const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('title') || '').trim().slice(0, 40);
        out.push(`${el.tagName.toLowerCase()}${el.className && typeof el.className === 'string' ? '.' + el.className.split(/\s+/)[0] : ''} `
          + `"${name}" ${Math.round(r.width)}x${Math.round(r.height)}`);
      }
    }
    return out;
  }, [CONTROLS, min] as const);
}

async function open(page: Page, path: string) {
  await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded' });
  await page.locator('#ui-inner, #ui-root').first().waitFor({ state: 'attached', timeout: 60_000 });
  await settle(page, { quietMs: 1000, timeout: 15000 });
}

test.beforeEach(async ({ page }) => {
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
});

test('every control is at least 24 by 24', async ({ page }, info) => {
  const found: Record<string, string[]> = {};
  for (const p of PAGES) {
    await open(page, p);
    const s = await small(page, 24);
    if (s.length) found[p] = s;
  }
  await info.attach('target-size-24.json', { body: JSON.stringify(found, null, 1), contentType: 'application/json' });
  for (const [p, s] of Object.entries(found)) console.log(`[target-size] ${p}: ${s.length}\n  ` + s.slice(0, 40).join('\n  '));
  expect(found, 'controls smaller than 24 by 24').toEqual({});
});

test('in Big mode every control is at least 44 by 44', async ({ page }, info) => {
  const found: Record<string, string[]> = {};
  for (const p of ['/', '/w/settings']) {
    await open(page, p);
    await page.evaluate(() => document.body.classList.add('fr-big'));
    await settle(page, { quietMs: 500, timeout: 5000 });
    const s = await small(page, 44);
    if (s.length) found[p] = s;
  }
  await info.attach('target-size-44.json', { body: JSON.stringify(found, null, 1), contentType: 'application/json' });
  for (const [p, s] of Object.entries(found)) console.log(`[target-size-big] ${p}: ${s.length}\n  ` + s.slice(0, 40).join('\n  '));
  expect(found, 'controls smaller than 44 by 44 in Big mode').toEqual({});
});
