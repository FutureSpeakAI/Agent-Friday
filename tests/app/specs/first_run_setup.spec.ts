/**
 * FIRST RUN, AS A NEW PERSON SEES IT (first-run spec, Part A and B3).
 *
 * A fresh scratch home, the real page and the real server: the consent
 * screens, then every step of the setup conversation answered the way a
 * person would (chips, typing, the cards), with a screenshot of each step
 * kept in the test output for a person to look at. Then the Locks section
 * and Messages > Clean up. Nothing here runs against the live Friday.
 *
 * Run on its own (its own scratch home): it finishes setup, which no other
 * spec should inherit.
 */
import * as fs from 'fs';
import { test, expect, type Page } from '../fixtures';
import { BASE } from '../harness';

async function shot(page: Page, info: any, name: string) {
  const file = info.outputPath(name + '.png');
  fs.mkdirSync(require('path').dirname(file), { recursive: true });
  await page.screenshot({ path: file, fullPage: false });
  await info.attach(name, { path: file, contentType: 'image/png' });
}

const chat = (page: Page) => page.locator('.sc-root');

/** Where the newest line and the step's card sit, and every scrolling box
 *  from the conversation up: kept with the screenshots, so a layout that
 *  hides either can be read without rerunning. */
async function layout(page: Page, info: any, name: string) {
  const data = await page.evaluate(() => {
    const box = (el: Element | null) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el as HTMLElement);
      return { cls: (el as HTMLElement).className, top: Math.round(r.top), bottom: Math.round(r.bottom),
               h: Math.round(r.height), sh: (el as HTMLElement).scrollHeight, st: (el as HTMLElement).scrollTop,
               ch: (el as HTMLElement).clientHeight, overflowY: cs.overflowY, position: cs.position };
    };
    const chain: any[] = [];
    let el: Element | null = document.querySelector('.sc-log');
    while (el && chain.length < 14) { chain.push(box(el)); el = el.parentElement; }
    const msgs = document.querySelectorAll('.sc-log .sc-msg');
    return { viewport: { w: innerWidth, h: innerHeight }, chain,
             newest: box(msgs[msgs.length - 1] || null), side: box(document.querySelector('.sc-side')) };
  });
  await info.attach(name + '-layout.json', { body: JSON.stringify(data, null, 1), contentType: 'application/json' });
}

/** The newest line of the conversation is on screen (soft: the walk goes on). */
async function newestInView(page: Page, info: any, name: string) {
  await layout(page, info, name);
  await expect.soft(page.locator('.sc-log .sc-msg').last(), name + ': the newest line is on screen').toBeInViewport();
}

async function stage(page: Page): Promise<string> {
  const st = await (await page.request.get(`${BASE}/api/setup-chat/state`)).json();
  return st.stage;
}

async function waitStage(page: Page, want: string) {
  await expect.poll(() => stage(page), { timeout: 30_000, message: `the conversation should reach "${want}"` }).toBe(want);
  await page.waitForTimeout(300);
}

async function chip(page: Page, label: RegExp | string) {
  await chat(page).locator('.sc-chips button', { hasText: label }).first().click();
}

async function say(page: Page, text: string) {
  await chat(page).locator('#sc-input').fill(text);
  await chat(page).getByRole('button', { name: 'Send', exact: true }).click();
}

test('a new person sets Friday up, end to end, and every step is on screen', async ({ page }, info) => {
  test.skip(!process.env.FRIDAY_FIRST_RUN_WALK,
    'finishes setup, so it runs alone on its own scratch home: set FRIDAY_FIRST_RUN_WALK=1');
  test.setTimeout(600_000);
  await page.setViewportSize({ width: 1366, height: 860 });
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });

  // ── the consent screens: what Friday keeps, the lock, other people, calls, updates
  const consent = page.locator('[data-friday-surface="consent"]');
  await expect(consent).toBeVisible({ timeout: 120_000 });
  await shot(page, info, '00a-consent-what-friday-keeps');
  await consent.getByRole('button', { name: 'Continue', exact: true }).click();
  await shot(page, info, '00b-consent-the-lock');
  await consent.getByRole('button', { name: 'Skip for now' }).click();
  await shot(page, info, '00c-consent-other-people');
  await consent.getByRole('button', { name: 'I understand' }).click();
  await consent.getByText(/Stand back on her own/).first().click();
  await shot(page, info, '00d-consent-calls');
  await consent.getByRole('button', { name: 'Continue', exact: true }).click();
  await consent.getByText("Don't check", { exact: true }).first().click();
  await shot(page, info, '00e-consent-updates');
  await consent.getByRole('button', { name: 'Continue', exact: true }).click();

  // ── the conversation
  await expect(chat(page)).toBeVisible({ timeout: 60_000 });
  await waitStage(page, 'welcome');
  await expect(chat(page)).toContainText('Nothing about you goes to the people who made me. Ever.');
  const chips = await chat(page).locator('.sc-chips button').allTextContents();
  expect(chips).toEqual(['Talk', 'Type', 'I use a screen reader']);
  await shot(page, info, '01-hello-the-promises-and-talk-or-type');
  await chip(page, 'Type');

  await waitStage(page, 'offer');
  await expect(chat(page)).toContainText('Finding you money and taking back your data are coming in a later update.');
  await shot(page, info, '02-the-offer');
  await chip(page, 'Clean up my inbox');

  await waitStage(page, 'agent_name');
  await shot(page, info, '03-her-name');
  await chip(page, 'Friday');

  await waitStage(page, 'voice');
  await shot(page, info, '04-her-voice');
  await chip(page, /Choose later|OK/);

  await waitStage(page, 'pronouns');
  expect(await chat(page).locator('.sc-chips button.rec').count(), 'no pronoun is recommended').toBe(0);
  await shot(page, info, '05-her-pronouns');
  await chip(page, 'Just use my name');

  await waitStage(page, 'framing');
  await shot(page, info, '06-how-you-think-of-her');
  await chip(page, 'An assistant');

  await waitStage(page, 'about_name');
  await say(page, 'Sam');
  await waitStage(page, 'about_city');
  await say(page, 'Springfield, IL');
  await waitStage(page, 'about_tz');
  await expect(page.locator('.sc-side')).toContainText('Goes by Sam');
  // The card "beside this" must be on screen, not scrolled away above the newest message.
  await expect.soft(page.locator('.sc-side').getByText('Goes by Sam')).toBeInViewport();
  await newestInView(page, info, '07');
  await shot(page, info, '07-about-you-card-and-time-zone');
  await chip(page, 'Yes');

  await waitStage(page, 'basics');
  await shot(page, info, '08-basics');
  await page.locator('.sc-side').getByRole('button', { name: 'Continue', exact: true }).click();

  await waitStage(page, 'posture');
  const cards = page.locator('.sc-side .sc-option');
  await expect(cards).toHaveCount(3);
  expect(await page.locator('.sc-side .sc-option.on').count(), 'nothing is pre-selected').toBe(0);
  await expect(page.locator('.sc-side .sc-rec')).toHaveCount(1);
  await expect.soft(cards.first()).toBeInViewport();
  await newestInView(page, info, '09');
  await shot(page, info, '09-where-she-may-think');
  await chip(page, /^Cloud for public things/);

  await waitStage(page, 'never_send');
  await say(page, 'Project Bluebird, Acme Holdings');
  await waitStage(page, 'connect');
  await shot(page, info, '10-never-send-then-connect');
  await chip(page, 'Done for now');

  await waitStage(page, 'knowledge');
  await chip(page, 'Not now');
  await waitStage(page, 'reader');
  await chat(page).locator('.sc-chips button').first().click();
  await waitStage(page, 'questions');
  await chip(page, 'Skip the rest');
  await waitStage(page, 'style');
  await page.locator('.sc-side').getByRole('button', { name: 'Keep my default style' }).click();

  await waitStage(page, 'money');
  const rec = await chat(page).locator('.sc-chips button.rec').allTextContents();
  expect(rec).toEqual(['$10 a month (recommended)']);
  await shot(page, info, '11-money');
  await chip(page, '$10 a month (recommended)');

  if ((await stage(page)) === 'scheduled_cloud') {
    await shot(page, info, '11b-scheduled-jobs');
    await chip(page, /No, keep them paused/);
  }

  await waitStage(page, 'routines');
  const boxes = page.locator('.sc-side input[type="checkbox"]');
  // A scratch home may have no routines yet (the scheduler seeds them on a
  // real install); then the card says so, and it is the card that must show.
  if (await boxes.count()) await expect.soft(boxes.first()).toBeInViewport();
  else await expect.soft(page.locator('.sc-side .sc-card').first()).toBeInViewport();
  await newestInView(page, info, '12');
  for (let i = 0; i < await boxes.count(); i++) {
    expect(await boxes.nth(i).isChecked(), 'no routine is ticked until the person ticks it').toBe(false);
  }
  await shot(page, info, '12-routines-nothing-ticked');
  if (await boxes.count()) await boxes.first().check();
  await page.locator('.sc-side').getByRole('button', { name: /^Schedule/ }).click();

  await waitStage(page, 'memory_rules');
  await newestInView(page, info, '13');
  await shot(page, info, '13-what-she-remembers-and-asks');
  await chip(page, 'Looks right');

  await waitStage(page, 'household');
  await chip(page, 'Yes');
  await waitStage(page, 'room_voice');
  await expect(chat(page).getByRole('button', { name: "Open Windows' Other users page" })).toBeVisible();
  await shot(page, info, '14-household');
  await chip(page, 'Require my name');

  await waitStage(page, 'brain_warm');
  await chip(page, 'Hand it back when I quit');

  await waitStage(page, 'finish');
  await expect(page.locator('.sc-receipt-table')).toContainText('Who decided');
  await expect(page.locator('.sc-receipt-table')).toContainText('Cloud for public things');
  await shot(page, info, '15-finish-and-the-receipt');
  await page.locator('.sc-side .sc-receipt').scrollIntoViewIfNeeded();
  await shot(page, info, '15b-the-receipt');
  await page.locator('.sc-side').getByRole('button', { name: 'Open my desktop' }).click();
  await expect(chat(page)).toHaveCount(0, { timeout: 30_000 });

  const rec2 = await (await page.request.get(`${BASE}/api/setup-chat/record`)).json();
  expect(rec2.verify.ok, 'the setup record checks out').toBeTruthy();
  expect(JSON.stringify(rec2.entries)).not.toContain('Springfield');

  // ── Privacy & Data > Locks
  await page.goto(`${BASE}/w/settings`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.st-root', { timeout: 60_000 });
  const rail = page.getByRole('navigation', { name: 'Settings sections' });
  await rail.getByRole('button', { name: 'Privacy & Data', exact: true }).click();
  await expect(page.locator('.lk-lines')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.lk-limit')).toContainText('under your own Windows account');
  await shot(page, info, '16-locks');

  // ── Messages > Clean up
  await page.goto(`${BASE}/w/messages`, { waitUntil: 'domcontentloaded' });
  const open = page.locator('[data-testid="fm-cleanup-open"]');
  if (await open.count()) {
    await open.click();
    await expect(page.locator('[data-testid="fm-cleanup"]')).toBeVisible({ timeout: 30_000 });
    await shot(page, info, '17-messages-clean-up');
  }
});
