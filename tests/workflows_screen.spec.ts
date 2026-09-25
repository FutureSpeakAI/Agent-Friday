/**
 * The Workflows screen, driven in a real browser.
 *
 * Pins the three things the screen exists for: a workflow can be made from
 * plain words (a draft to check, nothing saved until Save); each workflow says
 * what it does, when, and how it last went; and the screen says that outward
 * actions wait for the owner's OK.
 *
 * Run against a disposable server (it creates and deletes a workflow):
 *   FRIDAY_URL=http://localhost:3217 npx playwright test tests/workflows_screen.spec.ts --workers=1
 */
import { test, expect } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || 'http://localhost:3217';
const NAME = 'PW legislature watch';

test.describe.configure({ mode: 'serial' });

test.afterAll(async ({ request }) => {
  const ov = await (await request.get(BASE + '/api/workflows/overview')).json();
  for (const w of ov.workflows || []) {
    if (w.name === NAME) await request.post(BASE + '/api/workflows/remove', { data: { slug: w.slug, schedule_id: w.schedule_id } });
  }
});

test('plain words become a draft, and only Save saves it', async ({ page, request }) => {
  await page.goto(BASE + '/w/workflows');
  const ask = page.locator('#wf-ask');
  await ask.click();
  await ask.fill('Every Monday and Thursday at 9am, look through the state legislature site for new school funding bills and email the list to my editor');
  await page.getByRole('button', { name: /Draft it/ }).click();

  const editor = page.getByTestId('wf-editor');
  await expect(editor).toBeVisible();
  await expect(page.getByTestId('wf-when-sentence')).toHaveText(/Every Monday and Thursday at 9 AM/);
  await expect(editor).toContainText('send email');
  await expect(editor).toContainText('asks you');

  // A draft is not a workflow yet.
  const before = await (await request.get(BASE + '/api/workflows/overview')).json();
  expect(before.workflows.map((w: any) => w.name)).not.toContain(NAME);

  await page.locator('#wf-name').fill(NAME);
  await page.getByRole('button', { name: 'Save workflow' }).click();
  await expect(editor).toBeHidden();

  const card = page.getByTestId('wf-card').filter({ hasText: NAME });
  await expect(card).toBeVisible();
  await expect(card).toContainText('Every Monday and Thursday at 9 AM');
  await expect(card).toContainText("Hasn't run yet");
  await expect(card).toContainText('Asks you before it can send email');
  await expect(card.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
});

test('the screen says outward actions wait for your OK', async ({ page }) => {
  await page.goto(BASE + '/w/workflows');
  await expect(page.getByTestId('wf-promise')).toContainText('asks you first');
});

test('the switch pauses a scheduled workflow', async ({ page, request }) => {
  await page.goto(BASE + '/w/workflows');
  const card = page.getByTestId('wf-card').filter({ hasText: NAME });
  await card.getByRole('switch').click();
  await expect(card.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  await expect(card).toContainText('paused');
  const ov = await (await request.get(BASE + '/api/workflows/overview')).json();
  expect(ov.workflows.find((w: any) => w.name === NAME).enabled).toBe(false);
});
