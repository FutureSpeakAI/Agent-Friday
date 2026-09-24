/**
 * News cards reflow with the frame they are in: one column at phone width,
 * up to five on a wide screen, measured against the workspace (a narrow
 * desktop window gets few columns even on a big monitor). Nothing runs off
 * the side, no text is cut off by its box, and the whole feed can be
 * scrolled to its end. The 3D view fills the frame.
 *
 *   FRIDAY_URL=http://localhost:3221 PW_CHANNEL=msedge npx playwright test tests/news_layout.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || '';
const SHOTS = process.env.NEWS_SHOTS || '';
if (process.env.PW_CHANNEL) test.use({ channel: process.env.PW_CHANNEL });
test.setTimeout(300000);

async function prepare(page: Page) {
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await page.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET'
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"needs_prompt":false}' }) : r.continue());
  await page.addInitScript(() => { if (window.top === window) try { localStorage.setItem('friday_3d_on_news', '0'); } catch (_) {} });
}

/** Columns in the first card grid, and anything that overflows. */
function layout(page: Page, root: string) {
  return page.evaluate((root: string) => {
    const scope = document.querySelector(root) as HTMLElement;
    const grid = scope.querySelector('.news-masonry') as HTMLElement;
    const cards = grid ? Array.from(grid.querySelectorAll(':scope > .news-card')) as HTMLElement[] : [];
    const top = cards.length ? Math.min(...cards.map(c => c.getBoundingClientRect().top)) : 0;
    const firstRow = cards.filter(c => Math.abs(c.getBoundingClientRect().top - top) < 4);
    const lefts = new Set(cards.map(c => Math.round(c.getBoundingClientRect().left)));
    const colW = grid ? parseFloat(getComputedStyle(grid).gridTemplateColumns.split(' ')[0]) : 0;
    const feature = cards.find(c => c.classList.contains('feature'));
    const overflowing = Array.from(scope.querySelectorAll('.news-card, .news-headline, .fp-lead, .fp-lead-headline, .cluster-card')).filter(el => (el as HTMLElement).scrollWidth > (el as HTMLElement).clientWidth + 1).length;
    const body = document.querySelector('.ws-tab-body') as HTMLElement | null;
    return {
      cards: cards.length, columns: lefts.size, firstRow: firstRow.length, colW,
      featureCols: feature && colW ? Math.round(feature.getBoundingClientRect().width / colW) : null,
      overflowing,
      sideways: body ? body.scrollWidth - body.clientWidth : document.documentElement.scrollWidth - window.innerWidth,
    };
  }, root);
}

const SIZES = [
  { width: 390, height: 844, cols: [1, 1] },
  { width: 1024, height: 768, cols: [3, 3] },
  { width: 1366, height: 768, cols: [4, 4] },
  { width: 1920, height: 1080, cols: [5, 5] },
];

for (const s of SIZES) {
  test(`News in its own tab at ${s.width}×${s.height}: ${s.cols[0]} column${s.cols[0] > 1 ? 's' : ''}, nothing cut off, readable to the end`, async ({ page }) => {
    await page.setViewportSize({ width: s.width, height: s.height });
    await prepare(page);
    await page.goto(BASE + '/w/news');
    // the feed tab: every card, the lead one first
    await page.waitForSelector('.news-toolbar', { timeout: 90000 });
    await page.locator('.news-tabs button', { hasText: /feed/i }).first().click();
    await page.waitForSelector('.news-masonry > .news-card', { timeout: 90000 });
    await page.waitForTimeout(800);
    const m = await layout(page, '.news-ws');
    expect(m.cards).toBeGreaterThan(8);
    expect(m.firstRow, JSON.stringify(m)).toBeGreaterThanOrEqual(s.cols[0] - (m.featureCols === 2 ? 1 : 0));
    expect(m.columns, JSON.stringify(m)).toBeGreaterThanOrEqual(s.cols[0]);
    expect(m.columns, JSON.stringify(m)).toBeLessThanOrEqual(s.cols[1]);
    if (s.width >= 1024) expect(m.featureCols, 'the lead card spans two columns on a wide frame').toBe(2);
    expect(m.overflowing, 'boxes whose text runs past their edge').toBe(0);
    expect(m.sideways, 'the page scrolls sideways').toBeLessThanOrEqual(1);
    // scrolled to the end, the last card is on screen
    const end = await page.evaluate(() => {
      const b = document.querySelector('.ws-tab-body') as HTMLElement;
      b.scrollTop = b.scrollHeight;
      const cards = Array.from(document.querySelectorAll('.news-masonry > .news-card')) as HTMLElement[];
      const last = cards.reduce((a, c) => (c.getBoundingClientRect().bottom > a.getBoundingClientRect().bottom ? c : a), cards[0]);
      return { bottom: Math.round(last.getBoundingClientRect().bottom), vh: window.innerHeight, scrolled: b.scrollTop > 0 || b.scrollHeight <= b.clientHeight };
    });
    expect(end.scrolled).toBe(true);
    expect(end.bottom, 'the last card is below the window after scrolling to the end').toBeLessThanOrEqual(end.vh);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/news-${s.width}x${s.height}-bottom.png` });
  });
}

test('the front page reflows too, and its lead story uses the width', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await prepare(page);
  await page.goto(BASE + '/w/news');
  await page.waitForSelector('.news-toolbar', { timeout: 90000 });
  const fp = page.locator('.fp-lead');
  if (!(await fp.count())) test.skip(true, 'no front page has been composed in this home');
  const m = await page.evaluate(() => {
    const lead = document.querySelector('.fp-lead') as HTMLElement, ws = document.querySelector('.news-ws') as HTMLElement;
    const grids = Array.from(document.querySelectorAll('.news-masonry')) as HTMLElement[];
    return { leadW: lead.getBoundingClientRect().width, wsW: ws.getBoundingClientRect().width, cols: grids.map(g => getComputedStyle(g).gridTemplateColumns.split(' ').length) };
  });
  expect(m.leadW).toBeGreaterThan(m.wsW * 0.9);
  for (const c of m.cols) expect(c).toBeGreaterThanOrEqual(4);
});

test('in a desktop window the cards follow the window, not the monitor', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await prepare(page);
  await page.goto(BASE + '/');
  await page.waitForSelector('.dock', { timeout: 90000 });
  await page.evaluate(() => { try { localStorage.setItem('friday_fwin_news', JSON.stringify({ x: 40, y: 60, w: 700, h: 700 })); } catch (_) {} });
  await page.locator('.dock-btn[data-ws="news"]').dispatchEvent('click', { bubbles: true });
  await page.waitForSelector('.fwin .news-toolbar', { timeout: 90000 });
  await page.locator('.fwin .news-tabs button', { hasText: /feed/i }).first().click();
  await page.waitForSelector('.fwin .news-masonry > .news-card', { timeout: 90000 });
  await page.waitForTimeout(600);
  const m = await layout(page, '.fwin .news-ws');
  expect(m.columns, JSON.stringify(m)).toBe(2);                  // a 700px window on a 1920px monitor
  expect(m.overflowing).toBe(0);
});

for (const s of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  test(`the 3D view fills the tab at ${s.width}×${s.height}`, async ({ page }) => {
    await page.setViewportSize(s);
    await prepare(page);
    await page.goto(BASE + '/w/news?view3d=1');
    await page.waitForSelector('.f3-stage canvas', { timeout: 90000 });
    await page.waitForTimeout(1200);
    const g = await page.evaluate(() => { const r = (document.querySelector('.f3-stage') as HTMLElement).getBoundingClientRect(); return { gap: Math.round(window.innerHeight - r.bottom), h: Math.round(r.height) }; });
    expect(g.gap, JSON.stringify(g)).toBeLessThanOrEqual(40);    // only the tab's own padding below it
    expect(g.gap, JSON.stringify(g)).toBeGreaterThanOrEqual(0);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/news-3d-${s.width}x${s.height}.png` });
  });
}
