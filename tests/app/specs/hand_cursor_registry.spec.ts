// The hand cursor's target registry must hold every actionable element on every workspace,
// so snap works everywhere with no per-screen code (HIG hand-cursor.md §1.6).
//
// Runs against a SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused).
// For each workspace tab it walks the DOM for anything focusable, with an interactive role,
// or with a click handler, and asks window.FridayHandCursor.has(el). Anything missed fails
// with its tag, role and text. It also proves the frozen-point rule in the page: a pinch
// that starts on a small button and drifts off it still clicks that button.
import { test, expect } from '@playwright/test';

const BASE = process.env.FRIDAY_BASE || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');

const WORKSPACES = ['news', 'messages', 'calendar', 'contacts', 'career', 'code', 'futurespeak', 'knowledge', 'workflows', 'system'];

const WALK = `(() => {
  const HC = window.FridayHandCursor; if (!HC) return { error: 'FridayHandCursor missing' };
  const sel = 'button, a[href], input, select, textarea, summary, [role=button], [role=link], [role=option], [role=menuitem], [role=tab], [role=switch], [role=radio], [role=checkbox], [tabindex]:not([tabindex="-1"]), [onclick]';
  const missed = []; let seen = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) continue;
    if (el.disabled || el.closest('[inert],[aria-hidden="true"],[hidden],[data-fr-target="off"],#hand-cursor')) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.pointerEvents === 'none' || +cs.opacity < 0.05) continue;
    seen++;
    if (!HC.has(el)) missed.push(el.tagName.toLowerCase() + (el.getAttribute('role') ? '[role=' + el.getAttribute('role') + ']' : '') + ' "' + (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40) + '"');
  }
  return { seen, missed: missed.slice(0, 25), missedCount: missed.length, targets: HC.targets().length };
})()`;

test.describe('hand cursor registry', () => {
  for (const ws of WORKSPACES) {
    test(`every actionable element in ${ws} is a snap target`, async ({ page }) => {
      await page.goto(`${BASE}/w/${ws}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2500);
      const r = await page.evaluate(WALK) as any;
      expect(r.error).toBeUndefined();
      expect(r.seen, 'the walk saw nothing actionable; the page did not render').toBeGreaterThan(3);
      expect(r.missed, `${r.missedCount} actionable elements are not in the registry`).toEqual([]);
    });
  }

  test('a pinch whose own motion drifts off a small button still clicks it (freeze at onset)', async ({ page }) => {
    await page.goto(`${BASE}/w/system`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    const out = await page.evaluate(() => new Promise(resolve => {
      const HC = (window as any).FridayHandCursor;
      // A 24 px button of our own, so the test does not depend on a workspace's content.
      const b = document.createElement('button'); b.textContent = 'tiny'; b.style.cssText = 'position:fixed;left:300px;top:300px;width:24px;height:24px;z-index:10000'; // on top, as a real control is; a covered element is rightly not a target
      let clicks = 0; b.addEventListener('click', () => { clicks++; }); document.body.appendChild(b);
      const cursor = document.getElementById('hand-cursor'); if (cursor) cursor.classList.add('active');
      // In the page a frame runs on requestAnimationFrame, after the registry's mutation
      // observer has seen the new element; the test waits the same way.
      requestAnimationFrame(() => {
      let t = 1000; const f = (x: number, y: number, pinching: boolean) => HC.frame({ x, y, visible: true, pinching, t: (t += 33) });
      for (let i = 0; i < 6; i++) f(311, 311, false);          // settle on the button
      const lockedBefore = HC.locked && HC.locked.el === b;
      f(311, 311, true);                                        // pinch onset: freeze here
      for (let i = 0; i < 8; i++) f(311 + i * 3, 311 + i * 3, true); // the pinch itself drags the raw hand ~30 px, off the button
      f(360, 360, false);                                       // release far off the button
      setTimeout(() => { b.remove(); resolve({ clicks, lockedBefore }); }, 50);
      });
    })) as any;
    expect(out.lockedBefore, 'the cursor did not lock onto the 24 px button').toBe(true);
    expect(out.clicks, 'the click did not land on the button the pinch started on').toBe(1);
  });
});
