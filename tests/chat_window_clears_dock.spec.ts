/**
 * A floating chat window must never end underneath the dock.
 *
 * The dock is fixed to the bottom of the screen and the composer is the bottom
 * row of a chat window, so an overlap hides the one control the window exists
 * to offer. Measured 2026-09-19 at 1440x620 before the fix: all four cascaded
 * windows ran past the dock's top edge, the fourth by 118px.
 *
 * These tests are geometric on purpose. They read real rects out of a running
 * app rather than asserting that some string appears in index.html, because
 * the failure being guarded against is a layout one - a rule can be present
 * and still be beaten by a transform, a media query, or a stale state value,
 * which is exactly what happened to the first attempt at this fix.
 *
 * FALSIFICATION. Each test carries its own counterfactual: it recomputes what
 * the pre-fix rule would have produced from the same live measurements and
 * asserts that rule WOULD have overlapped. So a run where the window happens
 * to fit for unrelated reasons - a tall viewport, a short dock - reports
 * itself as uninformative instead of passing quietly. The 900px-tall case is
 * the specific trap: there, both the old and new rules yield 640px, so a test
 * written only at that size proves nothing.
 */
import { test, expect, type Page } from '@playwright/test';

const WIN_W = 422;          // border-box width of a ConversationWindow
const SHORT = { width: 1440, height: 620 };   // laptop with browser chrome

type Box = { top: number; bottom: number; height: number; z: number };

async function openWindows(page: Page, n: number): Promise<string[]> {
  const ids = await page.evaluate(async (count: number) => {
    const r = await fetch('/api/conversations').then(x => x.json());
    const ids = (r.conversations || []).slice(0, count).map((c: any) => c.id);
    for (const id of ids) (window as any).fridayOpenChatWindow(id, 'dock clearance');
    return ids;
  }, n);
  await page.waitForTimeout(900);
  return ids;
}

/** Every floating chat window, plus the dock, measured together. */
async function measure(page: Page): Promise<{ vh: number; dockTop: number; dockH: number; dockZ: number; wins: Box[] }> {
  return page.evaluate((w: number) => {
    const dock = document.querySelector('.dock') as HTMLElement;
    const dr = dock.getBoundingClientRect();
    const wins = ([...document.querySelectorAll('body *')] as HTMLElement[])
      .filter(el => {
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return s.position === 'fixed' && Math.round(r.width) === w && r.height > 100;
      })
      .map(el => {
        const r = el.getBoundingClientRect();
        return {
          top: Math.round(r.top), bottom: Math.round(r.bottom),
          height: Math.round(r.height), z: parseInt(getComputedStyle(el).zIndex || '0', 10),
        };
      });
    return {
      vh: window.innerHeight,
      dockTop: Math.round(dr.top), dockH: Math.round(dr.height),
      dockZ: parseInt(getComputedStyle(dock).zIndex || '0', 10),
      wins,
    };
  }, WIN_W);
}

test.describe('Chat windows and the dock', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(SHORT);
    await page.goto('/');
    // The React layer mounts the dock and the window host; wait for the dock
    // rather than a timeout, so a slow machine does not measure an empty page.
    await page.waitForSelector('.dock', { timeout: 30_000 });
    await page.waitForTimeout(2500);
  });

  test('no window ends below the top of the dock', async ({ page }) => {
    await openWindows(page, 4);
    const m = await measure(page);

    expect(m.wins.length, 'no chat windows opened - nothing was measured').toBeGreaterThan(1);
    expect(m.dockH, 'the dock has no height, so this proves nothing').toBeGreaterThan(20);

    for (const w of m.wins) {
      expect(w.bottom, `a window ending at ${w.bottom} runs under the dock at ${m.dockTop}`)
        .toBeLessThanOrEqual(m.dockTop);
    }

    // Counterfactual: the pre-fix height was min(640px, 78vh), ignoring the
    // dock entirely. If that rule would ALSO have fitted here, this run is
    // not evidence and the test says so rather than passing.
    const oldH = Math.min(640, Math.round(m.vh * 0.78)) + 2;   // +2 borders
    const oldWouldOverlap = m.wins.some(w => w.top + oldH > m.dockTop);
    expect(oldWouldOverlap,
      `uninformative run: at ${m.vh}px tall the OLD rule (${oldH}px) also cleared ` +
      `a dock at ${m.dockTop}. Use a shorter viewport.`).toBe(true);
  });

  test('a window dragged to the bottom stops above the dock', async ({ page }) => {
    await openWindows(page, 1);
    const before = await measure(page);
    expect(before.wins.length).toBe(1);

    const bar = page.locator(`div[style*="width: ${WIN_W}"]`).first();
    const box = await bar.boundingBox();
    expect(box).not.toBeNull();

    // Grab the title bar and shove far past the bottom edge.
    await page.mouse.move(box!.x + 200, box!.y + 10);
    await page.mouse.down();
    for (const y of [300, 600, 1200, 2400]) {
      await page.mouse.move(box!.x + 200, y);
      await page.waitForTimeout(60);
    }
    await page.waitForTimeout(150);
    const after = await measure(page);
    await page.mouse.up();

    expect(after.wins[0].bottom,
      `dragged window ends at ${after.wins[0].bottom}, dock starts at ${after.dockTop}`)
      .toBeLessThanOrEqual(after.dockTop);

    // Counterfactual: the old clamp allowed a top of innerHeight - 60, which
    // put the whole window inside the dock and past the bottom of the screen.
    expect(after.vh - 60, 'uninformative run: the old clamp would have fitted too')
      .toBeGreaterThan(after.dockTop - 100);
  });

  test('raising windows never walks the z-stack into the dock layer', async ({ page }) => {
    const ids = await openWindows(page, 3);
    expect(ids.length).toBeGreaterThan(1);

    // The old code handed out an ever-increasing counter from 42. The dock is
    // at 70, so somewhere around the 28th raise - an ordinary afternoon -
    // windows began painting over the shell. 60 raises is well past that.
    const RAISES = 60;
    const worst = await page.evaluate(async ({ ids, raises, w }: any) => {
      let maxZ = 0;
      for (let n = 0; n < raises; n++) {
        (window as any).fridayOpenChatWindow(ids[n % ids.length]);
        await new Promise(r => setTimeout(r, 20));
        for (const el of [...document.querySelectorAll('body *')] as HTMLElement[]) {
          const s = getComputedStyle(el);
          if (s.position !== 'fixed') continue;
          if (Math.round(el.getBoundingClientRect().width) !== w) continue;
          maxZ = Math.max(maxZ, parseInt(s.zIndex || '0', 10));
        }
      }
      return maxZ;
    }, { ids, raises: RAISES, w: WIN_W });

    const m = await measure(page);
    expect(worst, `after ${RAISES} raises a window reached z=${worst}, at or above the dock's ${m.dockZ}`)
      .toBeLessThan(m.dockZ);

    // Counterfactual: the old counter would have been 42 + 60 = 102 here.
    expect(42 + RAISES, 'uninformative run: the old counter would not have reached the dock either')
      .toBeGreaterThanOrEqual(m.dockZ);
  });

  test('the dock is measured by geometry, not by its class', async ({ page }) => {
    await openWindows(page, 1);

    // `.dock.hidden` slides away over 350ms. Freeze it mid-slide - which is
    // also the permanent state whenever compositing is paused, as in a
    // background tab or under prefers-reduced-motion - and the class says
    // "hidden" while most of the dock is still on screen. A window that
    // trusted the class would expand straight into it.
    const r = await page.evaluate(async () => {
      const d = document.querySelector('.dock') as HTMLElement;
      d.classList.add('hidden');
      d.style.transition = 'none';
      d.style.transform = 'translateY(40%)';     // stuck 60% on screen
      window.dispatchEvent(new Event('resize'));
      await new Promise(r => setTimeout(r, 600));
      const dr = d.getBoundingClientRect();
      const win = ([...document.querySelectorAll('body *')] as HTMLElement[]).find(el => {
        const s = getComputedStyle(el), b = el.getBoundingClientRect();
        return s.position === 'fixed' && Math.round(b.width) === 422 && b.height > 100;
      })!;
      const wr = win.getBoundingClientRect();
      return {
        claimsHidden: d.classList.contains('hidden'),
        dockTop: Math.round(dr.top), vh: window.innerHeight,
        winBottom: Math.round(wr.bottom),
      };
    });

    expect(r.claimsHidden, 'the dock should still be wearing the hidden class').toBe(true);
    expect(r.dockTop, 'the dock should still be occupying screen space').toBeLessThan(r.vh - 20);
    expect(r.winBottom,
      `the window expanded to ${r.winBottom} against a dock still covering from ${r.dockTop} - ` +
      `it trusted the class instead of the geometry`).toBeLessThanOrEqual(r.dockTop);
  });
});
