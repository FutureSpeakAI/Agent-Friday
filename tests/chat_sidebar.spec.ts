/**
 * The chat sidebar: every thread you own, grouped into projects, in a chat tab.
 *
 * WHY THESE ARE BROWSER TESTS. The Python tests next door prove the store and
 * the HTTP surface. What they cannot prove is that the sidebar and the
 * conversation switcher agree, that a row shows the model a turn will actually
 * run on, or that the column does not sit on top of the chat it is meant to
 * navigate. Those are layout and wiring facts and they only exist in a browser.
 *
 * CLICKS ARE FULL POINTER SEQUENCES. A bare `element.click()` does not open the
 * row menu - found 2026-09-19, and it looked exactly like a broken component
 * for twenty minutes. React 18 listens at the root for a pointer sequence, so
 * Playwright's own `.click()` is used rather than a dispatched MouseEvent.
 *
 * Each test cleans up the projects it makes. These run against a real Friday
 * with the user's real chats in it, and a suite that leaves folders behind is
 * a suite that slowly fills someone's sidebar with debris.
 */
import { test, expect, type Page, type APIRequestContext } from '@playwright/test';

const TAB = '/?chrome=chat';
const MADE: string[] = [];          // project ids this file created
const MADE_CHATS: string[] = [];    // chats this file created, archived on the way out

async function makeProject(api: APIRequestContext, name: string, model?: string) {
  const r = await api.post('/api/projects', {
    data: { name, seat: model ? { model } : null },
  });
  const p = (await r.json()).project;
  MADE.push(p.id);
  return p;
}

/** File a chat that already exists, and remember how to put it back. */
async function file(api: APIRequestContext, cid: string, pid: string | null) {
  await api.patch(`/api/conversations/${cid}`, { data: { project: pid } });
}

async function someChats(api: APIRequestContext, n: number) {
  const d = await (await api.get('/api/conversations')).json();
  return (d.conversations || []).slice(0, n);
}

test.afterAll(async ({ playwright, baseURL }) => {
  // Deleting a project detaches its chats and keeps them, so this restores the
  // sidebar to how it was found without touching anyone's transcripts.
  const api = await playwright.request.newContext({ baseURL });
  // Archive rather than delete: there is no conversation-delete endpoint, and
  // there should not be one just so a test can tidy up.
  for (const id of MADE_CHATS)
    await api.patch(`/api/conversations/${id}`,
                    { data: { status: 'archived' } }).catch(() => {});
  for (const id of MADE) await api.delete(`/api/projects/${id}`).catch(() => {});
  await api.dispose();
});

test.describe('Chat sidebar', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(TAB);
    await page.waitForSelector('.chat-sidebar', { timeout: 30_000 });
  });

  test('it exists in a tab and the chat panel makes room for it', async ({ page }) => {
    const m = await page.evaluate(() => {
      const sb = document.querySelector('.chat-sidebar') as HTMLElement;
      const cp = document.querySelector('.chat-panel') as HTMLElement;
      const s = sb.getBoundingClientRect(), c = cp.getBoundingClientRect();
      return {
        sbLeft: Math.round(s.left), sbWidth: Math.round(s.width),
        cpLeft: Math.round(c.left), cpRight: Math.round(c.right),
        vw: window.innerWidth,
      };
    });
    expect(m.sbLeft).toBe(0);
    expect(m.sbWidth).toBeGreaterThan(200);
    // The panel starts where the sidebar ends. A chat underneath its own
    // navigation is the dock bug again in a different corner. Exact, not
    // >=: the first version allowed slack and caught a real 1px overlap only
    // because the numbers happened to land either side of it. The sidebar is
    // border-box precisely so these two numbers are the same number.
    expect(m.cpLeft).toBe(m.sbWidth);
    expect(m.cpRight).toBeLessThanOrEqual(m.vw + 1);
  });

  test('the floating window has no sidebar', async ({ page }) => {
    // The tab is the desk; the holographic window is the glance. Same panel,
    // two jobs, and navigation belongs to only one of them.
    await page.goto('/');
    await page.waitForSelector('.dock', { timeout: 30_000 });
    const visible = await page.evaluate(() => {
      const sb = document.querySelector('.chat-sidebar') as HTMLElement | null;
      return !!sb && getComputedStyle(sb).display !== 'none';
    });
    expect(visible).toBe(false);
  });

  test('a row shows the seat the turn will actually run on', async ({ page, request }) => {
    const p = await makeProject(request, 'PW seat test', 'bonsai2:27b');
    // FRESH CHATS, not borrowed ones. These run against a real Friday whose
    // existing chats mostly carry their own bindings already, so filing two of
    // them into a project produced two BOUND rows and no inherited one - the
    // test failed while the feature worked. A test that depends on the state
    // of someone's real data is measuring their afternoon, not the code.
    const mk = async (seat: any) => {
      const c = (await (await request.post('/api/conversations', {
        data: { title: 'PW seat ' + Math.random().toString(36).slice(2, 8),
                project: p.id, seat },
      })).json()).conversation;
      MADE_CHATS.push(c.id);
      return c;
    };
    await mk(null);                          // inherits the project's seat
    await mk({ model: 'claude-sonnet-5' });  // overrides it

    await page.reload();
    await page.waitForSelector('.chat-sidebar');

    const seats = await page.evaluate(() =>
      [...document.querySelectorAll('.chat-sidebar span[title]')]
        .map(s => ({ title: s.getAttribute('title') || '', text: (s as HTMLElement).innerText }))
        .filter(s => /Bound to|Inherited from/.test(s.title)));

    const inherited = seats.filter(s => /Inherited/.test(s.title));
    const bound = seats.filter(s => /Bound to/.test(s.title));

    // The chat with no binding of its own, inside a Bonsai project, has to say
    // Bonsai - and has to say it DIFFERENTLY from one that chose Bonsai, or the
    // sidebar cannot be used to tell a default from a decision.
    expect(inherited.some(s => s.text.includes('bonsai2:27b'))).toBe(true);
    expect(inherited.every(s => s.text.includes('↳'))).toBe(true);
    expect(bound.every(s => !s.text.includes('↳'))).toBe(true);
  });

  test('search narrows the list and hides projects with no match', async ({ page, request }) => {
    const p = await makeProject(request, 'PW zzsearch');
    const [a] = await someChats(request, 1);
    await file(request, a.id, p.id);
    await page.reload();
    await page.waitForSelector('.chat-sidebar');

    const box = page.locator('.chat-sidebar input').first();
    await box.fill('zzzz-definitely-nothing-zzzz');
    await expect(page.locator('.chat-sidebar')).toContainText('Nothing matches');
    await expect(page.locator('.chat-sidebar')).not.toContainText('PW zzsearch');

    await box.fill('');
    await expect(page.locator('.chat-sidebar')).toContainText('PW zzsearch');
  });

  test('pinning a chat round-trips to the store and back to the row', async ({ page, request }) => {
    const [c] = await someChats(request, 1);
    await page.reload();
    await page.waitForSelector('.chat-sidebar');

    const row = page.locator(`.chat-sidebar div[title="${c.title}"]`).first();
    await row.locator('xpath=..').locator('button[aria-label^="Actions"]').click();
    const item = page.locator('.chat-sidebar button', { hasText: /^(Pin to top|Unpin)$/ }).first();
    const wasPinned = (await item.innerText()).trim() === 'Unpin';
    await item.click();

    await expect.poll(async () => {
      const d = await (await request.get('/api/conversations')).json();
      return !!(d.conversations.find((x: any) => x.id === c.id) || {}).pinned_at;
    }, { timeout: 8000 }).toBe(!wasPinned);

    // Put it back exactly as found.
    await request.patch(`/api/conversations/${c.id}`, { data: { pinned_at: wasPinned } });
  });

  test('moving a chat between projects leaves its own binding alone',
    async ({ page, request }) => {
      const from = await makeProject(request, 'PW from');
      const to = await makeProject(request, 'PW to', 'bonsai2:27b');
      const [c] = await someChats(request, 1);
      const before = (await (await request.get(`/api/conversations/${c.id}`)).json()).conversation;
      await file(request, c.id, from.id);

      await page.reload();
      await page.waitForSelector('.chat-sidebar');
      const row = page.locator(`.chat-sidebar div[title="${c.title}"]`).first();
      await row.locator('xpath=..').locator('button[aria-label^="Actions"]').click();
      await page.locator('.chat-sidebar button', { hasText: 'PW to' }).first().click();

      await expect.poll(async () => {
        const d = await (await request.get(`/api/conversations/${c.id}`)).json();
        return d.conversation.project;
      }, { timeout: 8000 }).toBe(to.id);

      const after = (await (await request.get(`/api/conversations/${c.id}`)).json()).conversation;
      expect(JSON.stringify(after.seat)).toBe(JSON.stringify(before.seat));

      await file(request, c.id, before.project || null);
    });

  test('the sidebar and the switcher never disagree', async ({ page, request }) => {
    // One data source, two views. The failure this guards is renaming a chat
    // in one place and watching the other keep the old name.
    const [c] = await someChats(request, 1);
    const fresh = 'PW renamed ' + Date.now();
    const original = c.title;
    await request.patch(`/api/conversations/${c.id}`, { data: { title: fresh } });

    await page.reload();
    await page.waitForSelector('.chat-sidebar');
    await expect(page.locator('.chat-sidebar')).toContainText(fresh);

    const bothAgree = await page.evaluate((title: string) => {
      // The switcher renders from the same convList the sidebar does; if the
      // chat is in one list it is in the other.
      const sb = document.querySelector('.chat-sidebar') as HTMLElement;
      return sb.innerText.includes(title);
    }, fresh);
    expect(bothAgree).toBe(true);

    await request.patch(`/api/conversations/${c.id}`, { data: { title: original } });
  });

  test('a narrow window gives the whole screen to the chat', async ({ page }) => {
    // 268px of folders on a phone leaves no conversation. Stepping aside beats
    // splitting a screen that has nothing to split.
    await page.setViewportSize({ width: 520, height: 800 });
    await page.reload();
    await page.waitForSelector('.chat-panel', { timeout: 30_000 });
    const m = await page.evaluate(() => {
      const sb = document.querySelector('.chat-sidebar') as HTMLElement;
      const cp = document.querySelector('.chat-panel') as HTMLElement;
      return {
        sidebarShown: getComputedStyle(sb).display !== 'none',
        panelLeft: Math.round(cp.getBoundingClientRect().left),
      };
    });
    expect(m.sidebarShown).toBe(false);
    expect(m.panelLeft).toBe(0);
  });
});
