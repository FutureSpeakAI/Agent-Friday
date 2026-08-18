/**
 * Harness for the app-level suite.
 *
 * The existing ~2,400 python test files and the older specs assert on the
 * SHAPE of what functions and APIs return. Every defect this suite exists to
 * catch shipped green through that. So the rule here is: drive the real UI,
 * and when something fails, say what a person would have seen.
 */
import { expect, type Page, type TestInfo } from '@playwright/test';

export const BASE = process.env.FRIDAY_BASE || 'http://localhost:3000';

/** Everything this suite creates is named with this prefix, and removed after. */
export const SCRATCH = 'zz-test-';
export const scratchName = (what: string) =>
  `${SCRATCH}${what}-${process.pid}-${Date.now().toString(36)}`;

/** Console + page errors, collected from the moment the page opens. */
export type Watcher = { errors: string[]; detach: () => void };

export function watchForErrors(page: Page): Watcher {
  const errors: string[] = [];
  const onConsole = (m: any) => {
    if (m.type() === 'error') errors.push(m.text().split('\n')[0].slice(0, 300));
  };
  const onPageError = (e: Error) => errors.push(String(e.message || e).slice(0, 300));
  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  return { errors, detach: () => { page.off('console', onConsole); page.off('pageerror', onPageError); } };
}

/**
 * Open the app and wait for it to actually render.
 *
 * We wait on a real signal — the dock exists, which only happens once <App/>
 * has mounted — never on a fixed sleep. A fixed sleep is how a suite becomes
 * flaky, and a flaky suite is one nobody runs.
 *
 * If it never mounts we fail with the browser's own error text, because
 * "timed out waiting for .dock-btn" tells you nothing and
 * "the app rendered nothing; the browser said: ReferenceError: X is not
 * defined" tells you everything.
 */
export async function openApp(page: Page, timeout = 45000): Promise<Watcher> {
  const watcher = watchForErrors(page);
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });

  // Race the mount against a fatal error. Waiting out the full timeout when
  // the bundle has already thrown just makes the suite slow to tell you
  // something it knew in the first two seconds.
  const deadline = Date.now() + timeout;
  let fatal: string | undefined;
  for (;;) {
    const mounted = await page.evaluate(() => document.querySelectorAll('.dock-btn').length >= 4)
      .catch(() => false);
    if (mounted) return watcher;
    fatal = watcher.errors.find(e => /is not defined|is not a function|Cannot read|Unexpected token|SyntaxError/i.test(e));
    if (fatal) break;
    if (Date.now() > deadline) break;
    await page.waitForTimeout(250);
  }

  const seen = await page.evaluate(() => ({
    text: (document.body.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 160),
    nodes: document.body.querySelectorAll('*').length,
  })).catch(() => ({ text: '(page unreadable)', nodes: 0 }));

  const why = fatal
    ? `The browser reported: ${fatal}`
    : watcher.errors.length
      ? `The browser reported: ${watcher.errors[0]}`
      : 'The browser reported no error at all, which usually means a syntax error killed the bundle before it could run.';

  throw new Error(
    `The app did not render.
` +
    `  The page has ${seen.nodes} elements and its visible text is ${JSON.stringify(seen.text)}.
` +
    `  ${why}
` +
    `  A person opening ${BASE} right now sees a blank screen.`,
  );
}

/** Attach a screenshot to the report so a failure can be looked at, not just read. */
export async function attachShot(page: Page, info: TestInfo, name: string) {
  await info.attach(name, { body: await page.screenshot({ fullPage: false }), contentType: 'image/png' });
}

export async function apiJson(page: Page, path: string): Promise<any> {
  const res = await page.request.get(BASE + path);
  expect(res.ok(), `GET ${path} returned HTTP ${res.status()}`).toBeTruthy();
  return res.json();
}

/**
 * Workspace dock buttons carry a hidden emoji span, so textContent is
 * "🏠Home" while innerText is "Home". Matching on text is therefore a trap.
 * The icon's alt attribute is the stable, user-meaningful handle.
 */
export const dockButton = (page: Page, workspace: string) =>
  page.locator(`.dock-btn:has(img[alt="${workspace}"])`).first();

export async function openWorkspace(page: Page, workspace: string) {
  const btn = dockButton(page, workspace);
  await expect(
    btn,
    `There is no "${workspace}" button in the dock, so a person cannot reach that screen at all.`,
  ).toHaveCount(1);
  await btn.click();
  await page.waitForTimeout(1200); // let the workspace paint
}

/**
 * KNOWN-BREAK SHIM — off by default, deliberately.
 *
 * As of commit b3bf550 the app does not start: `const DEFAULT_AGENT_SETTINGS`
 * was deleted from index.html while three references to it remain, so <App/>
 * throws on mount and the page renders nothing. See specs/smoke.spec.ts, which
 * fails on exactly this and MUST keep failing until it is fixed.
 *
 * Setting FRIDAY_TEST_SHIM=1 restores the constant in the browser only — no
 * file is modified — so the rest of the suite can still be exercised while
 * that fix is outstanding. Never set it in CI: it hides a blank screen.
 */
export async function applyKnownBreakShims(page: Page) {
  if (process.env.FRIDAY_TEST_SHIM !== '1') return;
  await page.addInitScript(() => {
    (window as any).DEFAULT_AGENT_SETTINGS = {
      temperature: 0.7, response_length: 'standard', include_sources: true,
      news_priorities: [], communication_style: 'professional',
      camera_interval_sec: 3, camera_auto_describe: false,
      tts_voice: 'Aoede', voice_language: '', voice_style_prompt: '',
      voice_temperature: null, voice_max_tokens: 0,
    };
  });
}

/** Standard opening move for every spec that needs the UI. */
export async function bootApp(page: Page) {
  await applyKnownBreakShims(page);
  return openApp(page);
}

/**
 * Wait for a screen to stop saying "Loading…".
 *
 * Screenshotting or asserting mid-load produces confident nonsense — a vision
 * judge will correctly report "the gallery failed to load" when in truth it
 * simply had not finished. But a screen that NEVER settles is a real defect,
 * so this does not quietly give up: it says which spinner is still on screen.
 */
export async function waitForSettled(page: Page, timeout = 45000) {
  const stillLoading = async () => page.evaluate(() => {
    const vis = (e: Element) => {
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden';
    };
    return [...document.querySelectorAll('body *')]
      .filter(e => e.children.length === 0 && vis(e))
      .map(e => (e.textContent || '').trim())
      .filter(t => /^(loading|fetching|generating)\b/i.test(t) || /loading[.…]{0,3}$/i.test(t))
      .slice(0, 5);
  });

  const deadline = Date.now() + timeout;
  let last: string[] = [];
  while (Date.now() < deadline) {
    last = await stillLoading();
    if (last.length === 0) return;
    await page.waitForTimeout(500);
  }
  throw new Error(
    `This screen was still loading after ${Math.round(timeout / 1000)} seconds.\n` +
    last.map(t => `  It still says: ${JSON.stringify(t)}`).join(String.fromCharCode(10)) +
    String.fromCharCode(10) +
    `  Whatever it is waiting for never arrived, so a person sees a spinner instead of content.`,
  );
}
