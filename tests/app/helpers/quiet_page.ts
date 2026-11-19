import type { Page } from '../fixtures';

/**
 * Start a page with the 3D scene off, before any page script runs (F7).
 *
 * The Settings walk clicks hundreds of controls. On a 2 to 4 core CI runner the software-rendered 3D scene kept the
 * machine so busy that a trivial /api/health took 6.7 s and a trivial script in the page 6.6 s (the failed run's
 * diagnostic), and Playwright's click, which needs several round trips, ran out of its 45 s. The walk checks that
 * controls keep their values; it does not look at the scene. This is the switch three_d_off.spec.ts uses.
 */
export async function quietPage(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try { localStorage.setItem('friday.render3d', 'off'); } catch (_) { /* a page with no storage has no switch */ }
  });
}
