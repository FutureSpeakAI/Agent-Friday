/**
 * SMOKE TIER — must stay fast, because a suite that is slow is a suite that
 * does not get run. Everything here answers one question: if I opened Friday
 * right now, would it work at all?
 */
import { test, expect } from '@playwright/test';
import { bootApp, openApp, attachShot, apiJson, dockButton, BASE } from '../harness';
import { assertImagesDecoded, assertRenderedOnce } from '../liveness';

test.describe('Smoke — does the app work at all', () => {

  /**
   * The single most important test in this repository.
   *
   * Nothing else matters if this fails, and on 2026-08-17 it does: the app
   * serves a blank page. Roughly 2,400 python test files pass while it does,
   * because none of them open the app.
   */
  test('the app actually renders when you open it', async ({ page }, info) => {
    await bootApp(page);
    const dock = await page.locator('.dock-btn').count();
    expect(dock, `The app mounted but the dock has ${dock} buttons — a person has nothing to click.`)
      .toBeGreaterThanOrEqual(4);
    await attachShot(page, info, 'app-on-open.png');
  });

  test('opening the app logs no uncaught errors', async ({ page }) => {
    const w = await bootApp(page);
    await page.waitForTimeout(2500);
    // Ignore noise we do not control: WebGL driver warnings and favicon 404s.
    const real = w.errors.filter(e => !/WebGL|GL_INVALID|favicon/i.test(e));
    expect(
      real,
      `The browser reported ${real.length} error(s) just from opening the app:\n  ` + real.join('\n  '),
    ).toEqual([]);
  });

  test('every workspace in the dock can be reached', async ({ page }) => {
    await bootApp(page);
    const names = await page.locator('.dock-btn img[alt]').evaluateAll(
      els => els.map(e => e.getAttribute('alt')!).filter(Boolean));
    expect(names.length, 'The dock rendered no named workspace icons.').toBeGreaterThan(4);
    for (const n of names) {
      await expect(dockButton(page, n), `Workspace "${n}" is not reachable from the dock.`).toHaveCount(1);
    }
  });

  /** Motivated by a home icon that rendered twice. */
  test('no workspace icon is rendered twice in the dock', async ({ page }) => {
    await bootApp(page);
    const names = await page.locator('.dock-btn img[alt]').evaluateAll(
      els => els.map(e => e.getAttribute('alt')!));
    const dupes = names.filter((n, i) => names.indexOf(n) !== i);
    expect([...new Set(dupes)],
      `These workspace icons appear more than once in the dock: ${[...new Set(dupes)].join(', ')}.`)
      .toEqual([]);
  });

  /** A broken-image glyph is in the DOM exactly like a good image is. */
  test('every image on the first screen actually loads', async ({ page }) => {
    await bootApp(page);
    await page.waitForTimeout(2000);
    await assertImagesDecoded(page, 'body', 'The first screen a person sees');
  });

  test('the APIs the first screen depends on all answer', async ({ page }) => {
    for (const path of ['/api/jobs', '/api/creations', '/api/models', '/api/tasks', '/api/system']) {
      const res = await page.request.get(BASE + path);
      expect(res.status(), `GET ${path} answered HTTP ${res.status()}; the screen that uses it will be empty.`).toBe(200);
    }
  });
});
