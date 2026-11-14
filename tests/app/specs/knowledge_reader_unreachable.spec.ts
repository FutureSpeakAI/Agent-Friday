// The knowledge reader tells the owner the truth about a page it could not load.
//
// Only a 404 from Friday means there is no page at that path. When the server
// cannot be reached at all (a restart, a release gate, a dropped connection)
// the page is still on disk, so the reader must say Friday could not be
// reached and offer to try again, never that the page was moved or deleted.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Every
// wiki answer here comes from the test, so no page is read from or written to disk.
import { test, expect, type Page } from '../fixtures';
import { settle } from '../helpers/settle';
import { openApp, openWorkspace } from '../harness';

test.skip(!process.env.FRIDAY_BASE || /:3000(\/|$)/.test(process.env.FRIDAY_BASE || ''),
  'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });

const SECTION = 'zz-test-reader';
const PAGE = 'alpha.md';
const PATH = `${SECTION}/${PAGE}`;

const json = (body: unknown, status = 200) =>
  ({ status, contentType: 'application/json', body: JSON.stringify(body) });

/** A wiki with one section and one page, read in the flat Pages view (3D off), and
 *  every page read answered by `answer` (fulfil, 404 or abort). */
async function wikiWithOnePage(page: Page, answer: (r: any) => Promise<void>) {
  await page.addInitScript(() => {
    try { localStorage.setItem('friday.render3d', 'off'); } catch (_) {}
  });
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
  await page.route('**/api/wiki/structure', r => r.fulfill(json({
    status: 'ok',
    structure: { [SECTION]: [{ name: 'alpha', filename: PAGE, size: 8, modified: 0, modified_iso: null }] },
    recent: [], pending_count: 0,
  })));
  await page.route('**/api/wiki/pending', r => r.fulfill(json({ status: 'ok', pending: [] })));
  // Both routes the reader tries: the path form and the older two-level form.
  await page.route('**/api/wiki/page?*', answer);
  await page.route(`**/api/wiki/${SECTION}/${PAGE}`, answer);
}

async function openThePage(page: Page) {
  await openApp(page);
  await openWorkspace(page, 'Knowledge');
  const section = page.locator('.kw-sec-t', { hasText: SECTION });
  await expect(section, 'the stubbed section is not listed').toHaveCount(1);
  if ((await section.getAttribute('aria-expanded')) !== 'true') await section.click();
  const row = page.locator('.kw-file', { hasText: PAGE });
  await expect(row, 'the stubbed page is not listed under its section').toHaveCount(1);
  await row.click();
  await settle(page, { quietMs: 500, timeout: 8000 });
  await expect(page.locator('.kw-doc-head', { hasText: PATH })).toBeVisible();
}

test('a page Friday cannot be reached for is not called moved or deleted', async ({ page }) => {
  await wikiWithOnePage(page, r => r.abort('connectionrefused'));
  await openThePage(page);
  const notice = page.locator('.kw-doc .kw-locked');
  await expect(notice).toHaveCount(1);
  await expect(notice).toContainText('could not be reached');
  await expect(notice).not.toContainText('moved or deleted');
  await expect(notice.getByRole('button', { name: 'Try again' })).toBeVisible();
  // Nothing destructive is offered for a page that was never read.
  await expect(page.locator('.kw-doc-head button', { hasText: 'Delete' })).toBeDisabled();
  await expect(page.locator('.kw-doc-head button', { hasText: 'Edit' })).toBeDisabled();
});

test('a page Friday answers 404 for is the one called moved or deleted', async ({ page }) => {
  await wikiWithOnePage(page, r => r.fulfill(json({ status: 'not_found', path: PATH }, 404)));
  await openThePage(page);
  const notice = page.locator('.kw-doc .kw-locked');
  await expect(notice).toHaveCount(1);
  await expect(notice).toContainText('moved or deleted');
  await expect(notice).not.toContainText('could not be reached');
});

test('Try again reads the page once Friday is back', async ({ page }) => {
  let back = false;
  await wikiWithOnePage(page, r => back
    ? r.fulfill(json({ status: 'ok', path: PATH, content: '# Alpha\n\nBack again.\n', section: SECTION, filename: PAGE, locked: false }))
    : r.abort('connectionrefused'));
  await openThePage(page);
  const notice = page.locator('.kw-doc .kw-locked');
  await expect(notice).toContainText('could not be reached');
  back = true;
  await notice.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.kw-doc-body')).toContainText('Back again.');
  await expect(page.locator('.kw-doc .kw-locked')).toHaveCount(0);
  await expect(page.locator('.kw-doc-head button', { hasText: 'Edit' })).toBeEnabled();
});
