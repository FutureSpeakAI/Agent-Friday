/**
 * Approval cards pop up in every open Friday tab and leave all of them when
 * decided anywhere.
 *
 * Three real tabs on two addresses (127.0.0.1 and localhost stand in for the
 * desktop and a tab on Friday's named address). The card is a real one: a goal
 * that requires approval, whose milestone is started through the API, raises a
 * `goal_milestone` card and waits. The tests DENY it, so nothing is run.
 *
 * Run against a disposable server (it creates goals and decides their cards):
 *   FRIDAY_URL=http://127.0.0.1:3218 npx playwright test tests/approval_popups.spec.ts --workers=1
 */
import { test, expect, type Page, type APIRequestContext } from '@playwright/test';

const BASE = process.env.FRIDAY_URL || 'http://127.0.0.1:3218';
const OTHER = BASE.replace('127.0.0.1', 'localhost');

test.describe.configure({ mode: 'serial' });
test.setTimeout(120000);

async function raiseCard(request: APIRequestContext, title: string): Promise<string> {
  const g = await (await request.post(BASE + '/api/goals', { data: {
    title, status: 'active', approval_required: true,
    milestones: [{ name: 'Send the roundup', success_criteria: 'Email the weekly roundup to the desk' }],
  } })).json();
  const goal = g.goal;
  const run = await (await request.post(`${BASE}/api/goals/${goal.goal_id}/milestones/${goal.milestones[0].milestone_id}/run`)).json();
  expect(run.status).toBe('pending_approval');
  return run.approval_id;
}

const popupCard = (p: Page, title: string) =>
  p.getByTestId('approval-popup').getByTestId('approval-card').filter({ hasText: title });

test('a card pops up in every tab, one decision wins, and it leaves them all', async ({ browser, request }) => {
  const ctx = await browser.newContext();
  const tabs: Page[] = [];
  for (const u of [BASE + '/?skip_setup=1', OTHER + '/w/news', BASE + '/w/workflows']) {
    const p = await ctx.newPage();
    await p.goto(u, { waitUntil: 'domcontentloaded' });
    tabs.push(p);
  }
  const title = 'PW roundup ' + Date.now();
  const id = await raiseCard(request, title);
  for (const p of tabs) await expect(popupCard(p, title)).toBeVisible({ timeout: 10000 });

  // Two tabs press Deny at the same instant: exactly one decides.
  const answers = await Promise.all([tabs[1], tabs[2]].map(async p => {
    const resp = p.waitForResponse(r => r.url().includes(`/api/approvals/${id}/decide`));
    await popupCard(p, title).getByRole('button', { name: 'Deny' }).click();
    return (await resp).json();
  }));
  expect(answers.filter(a => a.won)).toHaveLength(1);
  expect(answers.filter(a => a.already_decided)).toHaveLength(1);
  const loser = answers[0].already_decided ? tabs[1] : tabs[2];
  await expect(loser.getByText(/Already decided: this was declined/)).toBeVisible();

  for (const p of tabs) await expect(popupCard(p, title)).toHaveCount(0, { timeout: 10000 });
  const card = await (await request.get(`${BASE}/api/approvals/${id}`)).json();
  expect(card.approval.status).toBe('denied');
  await ctx.close();
});

test('a tab opened later shows the card that was already waiting', async ({ browser, request }) => {
  const title = 'PW late ' + Date.now();
  const id = await raiseCard(request, title);
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  await p.goto(OTHER + '/w/system', { waitUntil: 'domcontentloaded' });
  await expect(popupCard(p, title)).toBeVisible({ timeout: 10000 });
  // The card is exactly the stored one: its title and what it will do.
  const stored = (await (await request.get(`${BASE}/api/approvals/${id}`)).json()).approval;
  await expect(popupCard(p, title)).toContainText(stored.action_description);
  // Decided outside any tab (as voice or a text reply would), it leaves.
  await request.post(`${BASE}/api/approvals/${id}/decide`, { data: { decision: 'deny', decided_by: 'owner:voice' } });
  await expect(popupCard(p, title)).toHaveCount(0, { timeout: 10000 });
  await ctx.close();
});

test('the tabs of one address share one stream', async ({ browser }) => {
  const ctx = await browser.newContext();
  const pages: Page[] = [];
  for (const w of ['news', 'workflows', 'calendar', 'content']) {
    const p = await ctx.newPage();
    await p.goto(`${BASE}/w/${w}`, { waitUntil: 'domcontentloaded' });
    pages.push(p);
  }
  await expect.poll(async () => (await Promise.all(pages.map(p =>
    p.evaluate(() => (window as any).fridayApprovalFeed && (window as any).fridayApprovalFeed.isLeader()))))
    .filter(Boolean).length, { timeout: 10000 }).toBe(1);
  await ctx.close();
});
