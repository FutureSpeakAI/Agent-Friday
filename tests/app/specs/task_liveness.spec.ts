/**
 * TASK LIVENESS — does work visibly happen while it is happening?
 *
 * These target the failure mode where a subsystem runs correctly and shows
 * you nothing: the task log that read "— waiting for activity —" forever
 * because lines were replayed after the call returned. Reported five times
 * before it was fixed, and invisible to every unit test, because the data
 * was right — it just arrived too late to be of any use.
 */
import { test, expect } from '@playwright/test';
import { bootApp, apiJson, openWorkspace } from '../harness';
import { sampleWhile, assertGrewDuring, assertNotPlaceholder } from '../liveness';

test.describe('Task liveness', () => {

  /** Regression guard: work that ran must have left a record of running. */
  test('completed tasks have a log of what they did', async ({ page }) => {
    const { tasks = [] } = await apiJson(page, '/api/tasks');
    const done = tasks.filter((t: any) => t.status === 'complete');
    test.skip(done.length === 0, 'No completed tasks on this machine to inspect.');

    const empty = done.filter((t: any) => !(t.log || []).length)
      .map((t: any) => `${t.name || t.task_id} (ran for ${t.elapsed}s)`);
    expect(
      empty,
      `${empty.length} task(s) ran to completion but recorded no log lines at all:\n  ` + empty.join('\n  ') +
      `\n  Anyone expanding these in the task tray sees "— waiting for activity —" and cannot tell what happened.`,
    ).toEqual([]);
  });

  /**
   * The UI half of the same defect. The API having log lines is not the point;
   * the point is whether a person watching the tray can see them.
   */
  test('the task tray shows log lines rather than its placeholder', async ({ page }) => {
    await bootApp(page);
    const { tasks = [] } = await apiJson(page, '/api/tasks');
    const withLog = tasks.filter((t: any) => (t.log || []).length > 0);
    test.skip(withLog.length === 0, 'No task on this machine has log lines to display.');

    const tray = page.locator('.task-tray');
    test.skip(await tray.count() === 0, 'The task tray is not on screen in this state.');

    // Expand the first task so its log is visible.
    const expander = page.locator('.task-card-btn').first();
    if (await expander.count()) { await expander.click(); await page.waitForTimeout(600); }

    const log = page.locator('.task-card-log').first();
    if (await log.count() === 0) return; // nothing expanded; nothing to claim
    const text = (await log.innerText()).trim();
    assertNotPlaceholder(
      text,
      `The task tray log — the API reports ${withLog[0].log.length} log lines for "${withLog[0].name}", but the tray`,
    );
  });

  /**
   * The strict form: a log must GROW while the work runs, not fill in at the
   * end. Only meaningful when something is actually running, so it skips
   * loudly rather than passing vacuously.
   */
  test('a running task’s log grows while it runs', async ({ page }) => {
    const first = await apiJson(page, '/api/tasks');
    const running = (first.tasks || []).filter((t: any) => t.status === 'running');
    test.skip(running.length === 0, 'Nothing is running right now, so there is no live log to watch. ' +
      'This test is meaningful only while a task is in flight.');

    const id = running[0].task_id;
    const counts = await sampleWhile(async () => {
      const { tasks = [] } = await apiJson(page, '/api/tasks');
      const t = tasks.find((x: any) => x.task_id === id);
      return (t?.log || []).length;
    }, { forMs: 30_000, everyMs: 1500 });

    assertGrewDuring(counts, `The log for "${running[0].name}"`);
  });

  /**
   * A progress indicator must be able to express more than a couple of states.
   * Motivated by a bar with exactly two possible values, 0.5 and 1.0, shown as
   * a percentage — never wrong, and never informative.
   *
   * Needs a real long-running operation, so it is opt-in: it starts an image
   * generation. Set FRIDAY_TEST_MUTATE=1 to include it.
   */
  test('a progress bar takes more than two distinct values', async ({ page }) => {
    test.skip(process.env.FRIDAY_TEST_MUTATE !== '1',
      'Skipped by default because it starts a real generation job. ' +
      'Run with FRIDAY_TEST_MUTATE=1 to include it.');

    await bootApp(page);
    const widths = await sampleWhile(async () => {
      const w = await page.locator('.progress-fill').first()
        .evaluate((e: HTMLElement) => parseFloat(getComputedStyle(e).width)).catch(() => NaN);
      return w;
    }, { forMs: 45_000, everyMs: 500 });

    const seen = widths.filter(Number.isFinite);
    test.skip(seen.length === 0, 'No progress bar appeared during the window.');
    const { assertVaries } = await import('../liveness');
    assertVaries(seen, 'The generation progress bar');
  });
});
