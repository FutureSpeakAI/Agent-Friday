/**
 * No status, attribution or badge on the page ever reads "[object Object]".
 *
 * That text is what JavaScript prints when an object is concatenated into a
 * string, so it is the one cheap signature of a payload whose shape changed
 * under a label builder. It reached the long-turn line under the chat once
 * ("Still working (5m, round [object Object])"), because the liveness `step`
 * arrived as a step record instead of the round number.
 *
 * The page records every text node and every title/aria-label/placeholder
 * attribute that contains the signature, from first paint on, while a turn
 * runs long enough to be polled with that exact bad payload, and while the
 * model picker and Settings are opened. Any hit fails the test and names the
 * text it was found in.
 *
 * The page is this tree's index.html, served in place of the running app's
 * page with the token the server injects into it. Every request that is not a
 * GET is answered here, so no turn reaches a model and nothing is written.
 *
 *   FRIDAY_URL=http://127.0.0.1:3000 npx playwright test tests/no_object_object.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

// 127.0.0.1, not localhost: route.fetch runs in node, which resolves
// localhost to ::1, and the server listens on IPv4.
const BASE = process.env.FRIDAY_URL || 'http://127.0.0.1:3000';
const INDEX = process.env.FRIDAY_INDEX || path.join(__dirname, '..', 'index.html');
const SIGNATURE = '[object Object]';
test.setTimeout(180000);
test.use({ viewport: { width: 1600, height: 1000 }, serviceWorkers: 'block' });

async function boot(page: Page) {
  let fromTree = false;
  await page.route('**/*', route => route.request().method() === 'GET'
    ? route.continue()
    : route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await page.routeWebSocket(/.*/, () => {});
  await page.route(BASE + '/', async route => {
    const served = await (await route.fetch()).text();
    const token = (served.match(/<script>window\.__FRIDAY_API_TOKEN=[^<]*<\/script>/) || [''])[0];
    fromTree = true;
    await route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: fs.readFileSync(INDEX, 'utf8').replace('<head>', '<head>\n' + token),
    });
  });
  // The turn never answers, so it stays in flight and is polled for liveness.
  await page.route('**/api/chat/stream', () => {});
  await page.route('**/api/chat', () => {});
  await page.route('**/api/work/forecast', route => route.fulfill({ json: { will_pause: false } }));
  await page.route('**/api/setup/status', route => route.fulfill({ json: { initialized: true } }));
  await page.route('**/api/privacy/cloud-consent', route => route.request().method() === 'GET'
    ? route.fulfill({ json: { needs_prompt: false } }) : route.fallback());
  // The payload that produced "round [object Object]": a tool-step record
  // where the round belongs.
  await page.route('**/api/chat/turn/*/liveness', route => route.fulfill({ json: {
    state: 'working', alive: true, elapsed_s: 312, quiet_for_s: 2, quiet_after_s: 120,
    step: { type: 'tool', name: 'search_wiki', status: 'ok' },
    label: 'Reasoning (step 4)', model: 'local-model', hang: { stalled: false },
  } }));
  await page.addInitScript((sig: string) => {
    const hits: string[] = [];
    (window as any).__objectObjectHits = hits;
    const ATTRS = ['title', 'aria-label', 'placeholder', 'alt'];
    const CODE = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
    const check = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        // The page's own script source is text too, and may name the phrase.
        const host = node.parentElement;
        if (host && CODE.has(host.tagName)) return;
        if ((node.nodeValue || '').includes(sig)) hits.push((node.nodeValue || '').slice(0, 200));
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const el = node as Element;
      if (CODE.has(el.tagName)) return;
      for (const a of ATTRS) {
        const v = el.getAttribute(a);
        if (v && v.includes(sig)) hits.push(a + '=' + v.slice(0, 200));
      }
      el.childNodes.forEach(check);
    };
    new MutationObserver(records => {
      for (const r of records) {
        if (r.type === 'characterData' || r.type === 'attributes') check(r.target);
        r.addedNodes.forEach(check);
      }
    }).observe(document, { subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ATTRS });
  }, SIGNATURE);
  await page.clock.install();
  await page.goto(BASE + '/');
  await page.waitForSelector('.dock', { timeout: 90000 });
  await page.waitForFunction(() => typeof (window as any).fridayOpenWorkspace === 'function');
  expect(fromTree, "the page is not this tree's index.html").toBe(true);
}

const hits = (page: Page) => page.evaluate(() => (window as any).__objectObjectHits as string[]);

test('a long turn polled with a step record never shows [object Object]', async ({ page }) => {
  await boot(page);
  const input = page.locator('.chat-panel textarea[data-chat-input]');
  if (!(await page.locator('.chat-panel.open').count())) {
    await page.getByRole('button', { name: 'Open chat with Friday', exact: true }).click();
  }
  await expect(input).toBeVisible();
  await input.fill('a question that takes a while');
  await input.press('Enter');
  // The turn is in flight once the indicator shows; only then does the
  // liveness poll run.
  await expect(page.locator('.typing-indicator .label')).toBeVisible();
  // The first liveness poll comes 90 s into a turn, on a 15 s tick. Jump the
  // clock rather than run it: replaying two minutes of animation frames of
  // the 3D scene would take far longer than the test.
  await page.clock.fastForward(95000);
  await page.clock.fastForward(15000);
  const line = page.locator('.typing-indicator .label');
  await expect(line).toContainText('Still working (5m)');
  await expect(line).toContainText('Reasoning (step 4)');
  expect(await hits(page)).toEqual([]);
});

test('the model picker and Settings never show [object Object]', async ({ page }) => {
  await boot(page);
  await page.clock.fastForward(20000);
  for (const tab of ['models', 'privacy', 'general']) {
    await page.evaluate(t => (window as any).fridayOpenWorkspace({ workspace: 'settings', tab: t }), tab);
    await page.clock.fastForward(5000);
  }
  expect(await hits(page)).toEqual([]);
});
