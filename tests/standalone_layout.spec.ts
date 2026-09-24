/**
 * A workspace in its own tab (/w/<id>) uses the whole window and all of its
 * content can be reached.
 *
 * - Nothing is below the fold for good: the tab's body is the scrolling area
 *   (under a fixed header), and after scrolling it to the end the last of the
 *   workspace is on screen.
 * - Nothing is clipped: no box cuts off a large part of its own content
 *   without being scrollable.
 * - No dead space: when a workspace scrolls a pane of its own, that pane
 *   reaches the bottom of the window instead of stopping short of it.
 *
 * Read from a running app at the window sizes people actually use.
 *
 *   FRIDAY_URL=http://localhost:3221 PW_CHANNEL=msedge npx playwright test tests/standalone_layout.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || '';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.setTimeout(600000);

async function prepare(page: Page) {
  // a scratch test home shows its first-run prompts over the page
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await page.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET'
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"needs_prompt":false}' }) : r.continue());
  await page.addInitScript(() => { if (window.top === window) try { Object.keys(localStorage).filter(k => /^friday_3d_on_/.test(k)).forEach(k => localStorage.setItem(k, '0')); } catch (_) {} });
}

async function workspaceIds(page: Page): Promise<{ id: string; label: string }[]> {
  await page.goto(BASE + '/w/news');
  await page.waitForFunction(() => typeof (window as any).WS !== 'undefined' || typeof WS !== 'undefined', null, { timeout: 90000 });
  // eslint-disable-next-line no-undef
  return page.evaluate(() => (WS as any[]).filter(w => w.id !== 'settings').map(w => ({ id: w.id, label: w.label })));
}
declare const WS: any[];

/** What a person could and could not reach, measured in the page. */
async function measure(page: Page) {
  return page.evaluate(() => {
    const body = document.querySelector('.ws-tab-body') as HTMLElement;
    const vh = window.innerHeight;
    const cs = (el: Element) => getComputedStyle(el);
    const scrolls = (el: Element) => /(auto|scroll)/.test(cs(el).overflowY);
    // scroll the tab to its end
    if (body) body.scrollTop = body.scrollHeight;
    const all = body ? Array.from(body.querySelectorAll('*')) as HTMLElement[] : [];
    const insideScroller = (el: HTMLElement) => { for (let p = el.parentElement; p && p !== body; p = p.parentElement) if (scrolls(p) && p.scrollHeight > p.clientHeight + 2) return true; return false; };
    let lastBottom = 0;
    const clipped: string[] = [];
    const tag = (el: Element) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
    for (const el of all) {
      const s = cs(el);
      if (s.display === 'none' || s.visibility === 'hidden' || s.position === 'fixed') continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      if (!insideScroller(el)) lastBottom = Math.max(lastBottom, r.bottom);
      if (/(hidden|clip)/.test(s.overflowY) && el.clientHeight > 150 && el.scrollHeight - el.clientHeight > 40 && !el.closest('canvas, svg, iframe'))
        clipped.push(tag(el) + ' (' + (el.scrollHeight - el.clientHeight) + 'px cut)');
    }
    // dead space: a pane that scrolls on its own should reach the bottom, or
    // the workspace's own footer when it keeps one in view under its panes
    // (marked data-ws-footer, as Knowledge's bar is)
    const panes = all.filter(el => scrolls(el) && el.scrollHeight > el.clientHeight + 8 && el.clientHeight > 120 && cs(el).display !== 'none');
    const footer = body ? body.querySelector('[data-ws-footer]') : null;
    const floor = footer ? footer.getBoundingClientRect().top : vh;
    const gaps = panes.map(p => ({ pane: tag(p), gap: Math.round(floor - p.getBoundingClientRect().bottom) }));
    return {
      vh, bodyScrolls: body ? scrolls(body) : false,
      bodyOverflow: body ? body.scrollHeight - body.clientHeight : 0,
      pageOverflow: document.documentElement.scrollHeight - vh,
      unreachable: Math.round(lastBottom - vh), clipped: clipped.slice(0, 6), gaps: gaps.filter(g => g.gap > 40).slice(0, 6),
    };
  });
}

for (const size of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  test(`every standalone workspace can be read to its end, at ${size.width}×${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    await prepare(page);
    const all = await workspaceIds(page);
    expect(all.length).toBeGreaterThan(15);
    const bad: string[] = [];
    for (const w of all) {
      await page.goto(BASE + '/w/' + w.id);
      await page.waitForSelector(`[data-standalone="${w.id}"] .ws-tab-body > *`, { timeout: 60000 });
      await page.waitForTimeout(2500);                        // let the workspace load its data
      const m = await measure(page);
      if (!m.bodyScrolls) bad.push(`${w.id}: the tab body does not scroll`);
      if (m.pageOverflow > 1) bad.push(`${w.id}: ${m.pageOverflow}px beyond the page, which cannot scroll`);
      if (m.unreachable > 2) bad.push(`${w.id}: ${m.unreachable}px below the fold cannot be reached`);
      for (const c of m.clipped) bad.push(`${w.id}: clipped ${c}`);
      for (const g of m.gaps) bad.push(`${w.id}: ${g.pane} stops ${g.gap}px short of the bottom`);
    }
    expect(bad, bad.join('\n')).toEqual([]);
  });
}

test('the tab header carries the desktop wordmark, and the title names the workspace', async ({ page }) => {
  await prepare(page);
  await page.goto(BASE + '/w/news');
  await page.waitForSelector('.ws-tab-head', { timeout: 60000 });
  const head = await page.locator('.ws-tab-head').innerText();
  expect(head).toContain('AGENT FRIDAY');
  expect(head).toContain('by');
  expect(head).toContain('FutureSpeak.AI');
  const font = await page.locator('.ws-tab-mark').evaluate(el => getComputedStyle(el).fontFamily);
  expect(font).toContain('Orbitron');
  await expect(page).toHaveTitle('News · Agent Friday');
});

test('a workspace tab shrunk very small stays a workspace, not the desktop widget', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 440 });
  await prepare(page);
  await page.goto(BASE + '/w/news');
  await page.waitForSelector('[data-standalone="news"] .ws-tab-body > *', { timeout: 60000 });
  await page.waitForTimeout(1500);
  const s = await page.evaluate(() => ({ condensed: document.body.classList.contains('condensed'), ui: getComputedStyle(document.getElementById('ui-root')!).display }));
  expect(s.condensed).toBe(false);
  expect(s.ui).not.toBe('none');
  await expect(page.locator('.ws-tab-head')).toBeVisible();
});
