/**
 * STUDIO GALLERY — does new work actually appear?
 *
 * Motivated by two defects that shipped together: a gallery that fetched once
 * on mount and never refreshed, so newly generated images never showed up; and
 * a gallery sorted in reverse, which put the newest work 7,880 pixels down the
 * page. Both passed "the gallery renders items".
 *
 * DATA SAFETY: this creates ONE tiny file, named with the zz-test- prefix, in
 * the creations directory, and deletes it again in afterEach even if the test
 * fails. It never runs image generation, so nothing reaches the real gallery.
 */
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { bootApp, apiJson, openWorkspace, SCRATCH, scratchName, waitForSettled } from '../harness';
import { assertCreatedByThisTest, assertNewestFirst, assertImagesDecoded } from '../liveness';

// Mirrors core.CREATIONS_DIR: the Desktop folder when it exists, else ~/.friday.
const desktop = path.join(os.homedir(), 'Desktop');
const CREATIONS = fs.existsSync(desktop)
  ? path.join(desktop, 'friday-creations')
  : path.join(os.homedir(), '.friday', 'friday-creations');

const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64');

const created: string[] = [];

test.afterEach(() => {
  // Only ever remove files this suite made, and only inside the scratch prefix.
  for (const f of created.splice(0)) {
    const base = path.basename(f);
    if (base.startsWith(SCRATCH) && fs.existsSync(f)) { try { fs.unlinkSync(f); } catch { /* best effort */ } }
  }
});

test.describe('Studio gallery', () => {

  test('the gallery shows work that appeared after the page loaded', async ({ page }) => {
    test.skip(!fs.existsSync(CREATIONS), `No creations directory at ${CREATIONS} on this machine.`);

    await bootApp(page);
    await openWorkspace(page, 'Studio');
    await page.waitForTimeout(1500);

    const before = ((await apiJson(page, '/api/creations')).files || []).map((f: any) => f.name);

    // Create the file only AFTER the gallery has already rendered. That is the
    // whole point: a view that fetched once on mount will never show this.
    const name = scratchName('gallery') + '.png';
    const full = path.join(CREATIONS, name);
    fs.writeFileSync(full, PNG_1x1);
    created.push(full);

    // Give the UI a generous window to notice, by whatever means it refreshes.
    let after: string[] = [];
    const deadline = Date.now() + 25_000;
    let shownInUi = false;
    while (Date.now() < deadline) {
      after = ((await apiJson(page, '/api/creations')).files || []).map((f: any) => f.name);
      shownInUi = (await page.locator(`text=${name}`).count()) > 0
        || (await page.locator(`img[src*="${name}"]`).count()) > 0;
      if (shownInUi) break;
      await page.waitForTimeout(2000);
    }

    // First: the API must see it at all. If not, the problem is the server.
    assertCreatedByThisTest(before, after, name, 'The creations API');

    // Then the real assertion: did the screen change without a reload?
    expect(
      shownInUi,
      `A new item appeared in the creations folder and the API returned it, but the Studio gallery ` +
      `on screen never showed it within 25 seconds.\n` +
      `  The file was ${name}.\n` +
      `  The gallery is displaying the list it fetched when it mounted. Anything generated after ` +
      `you opened the page is invisible until you reload.`,
    ).toBe(true);
  });

  test('the newest work is at the top, not the bottom', async ({ page }) => {
    const { files = [] } = await apiJson(page, '/api/creations');
    test.skip(files.length < 3, 'Fewer than three creations to compare ordering with.');
    assertNewestFirst(
      files.map((f: any) => new Date(f.modified).getTime()),
      'The creations list',
    );
  });

  test('every image in the gallery actually loads', async ({ page }) => {
    await bootApp(page);
    await openWorkspace(page, 'Studio');
    await waitForSettled(page);
    await assertImagesDecoded(page, 'body', 'The Studio gallery');
  });
});
