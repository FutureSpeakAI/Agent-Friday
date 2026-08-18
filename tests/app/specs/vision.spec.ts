/**
 * MACHINE VISION — for defects no selector can express.
 *
 * "The model picker is sloppy and bad" is a real defect report and there is no
 * DOM assertion for it. So we screenshot the running app and have a model look
 * at it with a critical eye.
 *
 * Judged against STATED INTENT, never a golden image. A golden image would
 * have frozen the reverse-sorted gallery and the two-value progress bar as
 * correct, because they were what the app did on the day the golden was taken.
 * We say what each screen is supposed to achieve and ask whether it does.
 *
 * The judge runs locally (Ollama). Screenshots of Friday contain real calendar,
 * message, family, health and finance data, and must not leave the machine to
 * satisfy a test. Slow — around 30-60s per screen — so this is the full tier,
 * never the smoke tier.
 */
import { test, expect } from '@playwright/test';
import { bootApp, openWorkspace, waitForSettled } from '../harness';
import { judgeConfirmed, describeVerdict } from '../vision';

const SCREENS: { workspace: string | null; intent: string; reveal?: string }[] = [
  {
    workspace: null,
    intent: 'show the Friday desktop on open: a dock of workspace buttons with legible icons ' +
            'and labels, and a main area with readable content. The default panel is a finite ' +
            'morning edition: sections reading "Nothing here today, on purpose." are a ' +
            'deliberate design choice (the edition ends rather than infinitely scrolling) and ' +
            'are NOT a fault. A blank screen or a spinner that never resolves IS a fault.',
  },
  {
    workspace: 'Settings',
    reveal: 'Models',
    intent: 'let someone choose which AI model to use: the model options must be readable, ' +
            'not overlapping or clipped, and the currently selected one must be clear.',
  },
  {
    workspace: 'Studio',
    reveal: 'Gallery',
    // State the intent as the screen actually IS, not as you imagine it. An
    // inaccurate intent makes the judge report a mismatch that is your error,
    // not the app's — this gallery lists documents as well as images.
    intent: 'list the work Friday has produced, newest first, in a readable list or grid. ' +
            'Much of this work is written documents, which correctly appear as text entries rather ' +
            'than pictures — that is expected and is not a fault. Report a problem only if entries ' +
            'are unreadable, clipped, overlapping, or if an image thumbnail is present but broken.',
  },
];

for (const screen of SCREENS) {
  const name = screen.workspace ?? 'the home screen';

  test(`a model inspecting ${name} finds nothing visibly wrong`, async ({ page }, info) => {
    await bootApp(page);
    if (screen.workspace) await openWorkspace(page, screen.workspace);
    if (screen.reveal) {
      const tab = page.getByRole('button', { name: screen.reveal, exact: false }).first();
      if (await tab.count()) { await tab.click(); await page.waitForTimeout(800); }
    }
    await waitForSettled(page);
    await page.waitForTimeout(1200); // let the last paint land

    const png = await page.screenshot({ fullPage: false });
    await info.attach(`${name}.png`, { body: png, contentType: 'image/png' });

    const verdict = await judgeConfirmed(png, screen.intent);
    test.skip(!verdict.judged, `No vision judge available (${verdict.why}). ` +
      `Start Ollama, or set FRIDAY_VISION=off to silence this.`);

    // Record what it saw even on success — useful when reading a passing run.
    await info.attach(`${name}-verdict.txt`, {
      body: Buffer.from(describeVerdict(verdict, screen.intent), 'utf8'),
      contentType: 'text/plain',
    });

    expect(verdict.ok, describeVerdict(verdict, screen.intent) +
      String.fromCharCode(10) + '  (Both an initial and a confirming pass saw these.)').toBe(true);
  });
}
