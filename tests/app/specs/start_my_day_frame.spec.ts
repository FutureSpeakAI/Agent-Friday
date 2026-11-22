/**
 * Start My Day, as the owner sees it: pressing the button opens News on today's WRITTEN briefing (the one the voice
 * reads), with its generation time, its links, the count of dropped items and a "Didn't run" line for each section
 * skill that could not run; News > Customize Briefing lists those section skills with an on/off switch.
 *
 * SCRATCH server only. Today's briefing is written into the scratch home by tests/app/helpers/seed_start_my_day.py
 * (a fixed synthetic draft through the REAL finishing step and writer; no model call). The scratch home has no
 * skills and no Google, so both sections honestly say they did not run. The voice session the button also starts
 * is not under test here (headless Chromium has no microphone); only the briefing and the News frame are.
 *
 * Frames saved for a person to look at: briefing.png (News opened by Start My Day), customize.png (the switches).
 */
import { execFileSync } from 'child_process';
import * as path from 'path';
import { test, expect } from '../fixtures';
import { bootApp } from '../harness';

const BASE = process.env.FRIDAY_BASE || '';
const HOME = process.env.FRIDAY_PW_HOME || '';
test.skip(!BASE || /:3000(\/|$)/.test(BASE) || !HOME, 'needs the scratch server and its scratch home');
test.use({ viewport: { width: 1440, height: 900 } });

function python(): string {
  if (process.env.FRIDAY_PYTHON) return process.env.FRIDAY_PYTHON;
  const common = execFileSync('git', ['rev-parse', '--path-format=absolute', '--git-common-dir']).toString().trim();
  return path.join(path.dirname(common), 'venv', 'Scripts', 'python.exe');
}

function seedTodaysBriefing() {
  const script = path.join(__dirname, '..', 'helpers', 'seed_start_my_day.py');
  return execFileSync(python(), [script], { env: { ...process.env, FRIDAY_HOME: HOME } }).toString().trim();
}

test('Start My Day opens News on the written briefing it speaks, and the section skills can be switched', async ({ page }, info) => {
  test.setTimeout(180_000);
  seedTodaysBriefing();
  await page.request.post(`${BASE}/api/settings`, { data: { settings: { landing_mode: 'always' } } });
  // Past first run: its own screen ("Before anything else") covers the start screen (as in headings.spec.ts).
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ initialized: true }) }));
  // The START MY DAY button is on the classic start screen; the Simple home has none (static/friday_display_style.js).
  await page.addInitScript(() => { try { localStorage.setItem('friday.display-style.v1', 'classic'); } catch (_) {} });
  await bootApp(page);
  const answer = page.waitForResponse(r => r.url().includes('/api/voice/start-my-day'));
  await page.getByRole('button', { name: /START MY DAY/ }).click();
  const body = await (await answer).json();
  expect(body.briefing && body.briefing.made_now).toBe(false);        // the stored one was fresh: nothing generated

  const reader = page.locator('.news-briefing-reader');
  await expect(reader).toBeVisible({ timeout: 20_000 });
  await expect(reader).toContainText(/Generated \w+ \d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
  await expect(reader).toContainText("Didn't run: events-horizon is not installed.");
  await expect(reader).toContainText('3 items dropped: no source');
  await expect(reader.locator('a[href^="https://mail.google.com/mail/u/owner@example.test/#all/"]')).toHaveCount(1);
  await expect(reader.locator('a[href="https://calendar.example.test/event?eid=ev1"]')).toHaveCount(1);
  await expect(reader).not.toContainText('made up, with no id');
  await page.screenshot({ path: info.outputPath('briefing.png') });

  await reader.getByRole('button', { name: '← Back' }).click();
  await page.getByRole('button', { name: /Customize/ }).click();
  const list = page.getByTestId('briefing-section-skills');
  await expect(list).toContainText('events-horizon · not installed · every day');
  const outing = list.locator('[data-skill="family-outing"] [role="switch"]');
  await expect(outing).toHaveAttribute('aria-checked', 'true');
  await outing.click();
  await expect(outing).toHaveAttribute('aria-checked', 'false');
  await page.screenshot({ path: info.outputPath('customize.png') });
  const saved = await (await page.request.get(`${BASE}/api/briefing/sections`)).json();
  expect(saved.sections.map((s: any) => [s.skill, s.enabled])).toEqual([['events-horizon', true], ['family-outing', false]]);
  await outing.click();                                                  // leave the scratch home as it was
  await expect(outing).toHaveAttribute('aria-checked', 'true');
});
