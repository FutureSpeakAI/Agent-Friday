/**
 * "Open in its own tab" is one click: the workspace closes in the desktop, its
 * own tab opens on it (where the window was), and the browser switches to that
 * tab, with nothing more asked of the person.
 *
 * WHICH TAB IS IN FRONT IS ASKED OF WINDOWS. Playwright reports every page it
 * drives as visible and focused, so nothing inside a page can prove a switch.
 * A browser window's title, though, is its active tab's title, and Windows will
 * say what that is -- so these tests run a real (visible) Edge window on
 * Windows and read it. PW_HEADLESS=1 runs everything else and notes that the
 * front-tab assertion was skipped.
 *
 *   FRIDAY_URL=http://localhost:3221 PW_CHANNEL=msedge npx playwright test tests/workspace_tab_open.spec.ts --workers=1
 *
 * The named-address test also needs Friday's own listener on the test server
 * (settings local_address: host agent.pwtest, serve true, https_port 3243) and
 * resolves agent.pwtest inside this browser only (--host-resolver-rules); no
 * hosts file, no certificate store and nothing outside the test browser is touched.
 */
import { test, expect, chromium, type BrowserContext, type Page } from '@playwright/test';
import { execFileSync } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

const BASE = process.env.FRIDAY_URL || 'http://localhost:3221';
const CHANNEL = process.env.PW_CHANNEL || 'msedge';
const CAN_SEE_FRONT = process.platform === 'win32' && !process.env.PW_HEADLESS;
const NAMED = process.env.FRIDAY_NAMED_ORIGIN || 'https://agent.pwtest:3243';
test.describe.configure({ mode: 'serial' });
test.setTimeout(180000);

/** The title of the tab each test-browser window is showing, topmost window first. */
function frontTitles(profileDir: string): string[] {
  const ps = `
$ErrorActionPreference='Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -TypeDefinition @"
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public static class FridayFrontTab {
  public delegate bool P(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(P f, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  public static List<string> Titles(uint pid) {
    var r = new List<string>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
      if (p == pid && IsWindowVisible(h)) { var sb = new StringBuilder(512); GetWindowText(h, sb, 512); if (sb.Length > 0) r.Add(sb.ToString()); }
      return true; }, IntPtr.Zero);
    return r;
  }
}
"@
$dir = '${profileDir.replace(/'/g, "''")}'
$p = Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Where-Object { $_.CommandLine -like "*$dir*" -and $_.CommandLine -notlike '*--type=*' } | Select-Object -First 1
if (-not $p) { '[]'; exit }
ConvertTo-Json -Compress @([FridayFrontTab]::Titles([uint32]$p.ProcessId))`;
  const out = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], { encoding: 'utf8' }).trim();
  const titles: string[] = JSON.parse(out || '[]');
  // "News · Agent Friday and 2 more pages - Profile 1 - Microsoft Edge" -> "News · Agent Friday"
  return titles.map(t => t.replace(/ - Profile \d+ - Microsoft.*$| - Microsoft.*$/, '').replace(/ and \d+ more pages?$/, ''));
}

/** Titles compared on their letters and digits: the console may not carry "·" or "—". */
const plain = (t: string) => String(t || '').replace(/[^A-Za-z0-9]+/g, ' ').trim();

async function expectFront(profileDir: string, title: string) {
  if (!CAN_SEE_FRONT) {
    test.info().annotations.push({ type: 'skipped-assertion', description: `front tab should be "${title}" (needs a visible window on Windows)` });
    return;
  }
  await expect.poll(() => plain(frontTitles(profileDir)[0]), { timeout: 15000, message: 'the tab in front' }).toBe(plain(title));
}

declare const WS: any[];
let ctx: BrowserContext;
let profile: string;

test.beforeAll(async () => {
  profile = fs.mkdtempSync(path.join(os.tmpdir(), 'friday-tabs-'));
  ctx = await chromium.launchPersistentContext(profile, {
    channel: CHANNEL, headless: !CAN_SEE_FRONT, viewport: { width: 1366, height: 768 },
    ignoreHTTPSErrors: true,            // the test authority is trusted by nothing, by design
    args: ['--host-resolver-rules=MAP agent.pwtest 127.0.0.1'],
  });
  // A scratch home shows its first-run prompts over the page.
  await ctx.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"initialized":true}' }));
  await ctx.route('**/api/privacy/cloud-consent', r => r.request().method() === 'GET'
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"needs_prompt":false}' }) : r.continue());
  await ctx.addInitScript(() => { if (window.top === window) try { Object.keys(localStorage).filter(k => /^friday_3d_on_/.test(k)).forEach(k => localStorage.setItem(k, '0')); } catch (_) {} });
});

test.afterAll(async () => {
  await ctx?.close();
});

async function closeOthers(keep?: Page) {
  for (const p of ctx.pages()) if (p !== keep) await p.close();
}

/** A real mouse click on a dock icon, once the dock's magnification under
 *  the pointer has settled (window.open needs the person's own gesture). */
async function dockClick(page: Page, id: string, button: 'left' | 'middle' = 'left') {
  const btn = page.locator(`.dock-btn[data-ws="${id}"]`);
  await btn.hover();
  await page.waitForTimeout(500);
  const box = (await btn.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2, { button });
}

async function desktop(named?: string): Promise<Page> {
  const page = await ctx.newPage();
  if (named) {
    // As if the server had proven agent.pwtest reaches this Friday.
    await page.route(BASE + '/', async r => {
      const res = await r.fetch();
      const body = (await res.text()).replace(/window\.__FRIDAY_LOCAL_ADDRESS__=\{[^<]*?\};/,
        `window.__FRIDAY_LOCAL_ADDRESS__=${JSON.stringify({ host: 'agent.pwtest', origin: named, secure: true, local: BASE })};`);
      await r.fulfill({ response: res, body });
    });
  }
  await page.goto(BASE + '/');
  await page.waitForSelector('.dock-btn[data-ws="news"]', { timeout: 90000 });
  await page.waitForTimeout(800);
  return page;
}

async function openWindow(page: Page, id: string) {
  const win = page.locator(`.fwin:has([data-ws-tab="${id}"])`);
  // Opening a window is not what is under test, and the dock magnifies under
  // the pointer (a neighbour can slide over the target mid-click).
  if (!(await win.count())) await page.locator(`.dock-btn[data-ws="${id}"]`).evaluate(el => (el as HTMLElement).click());
  await expect(win).toHaveCount(1);
  await page.waitForTimeout(600);
  return win;
}

/** The workspace the first test moved into a tab (whichever has enough in it
 *  to scroll in the Friday home under test). */
let moved = { id: 'contacts', title: 'Contacts · Agent Friday' };

const scrollRange = (win: ReturnType<Page['locator']>) => win.locator('.fwin-body').evaluate(el => {
  const sc = (window as any).fridayMainScroller(el);
  return sc ? sc.scrollHeight - sc.clientHeight : 0;
});

test('one click: the tab opens where the window was, the window folds away, the tab is in front', async () => {
  const page = await desktop();
  await closeOthers(page);
  // a workspace with enough in it to scroll: what that is depends on the home under test
  let win = null as null | ReturnType<Page['locator']>;
  for (const id of ['contacts', 'system', 'trust', 'code', 'news', 'knowledge']) {
    const w = await openWindow(page, id);
    let range = 0;
    for (let i = 0; i < 12 && range <= 200; i++) { await page.waitForTimeout(500); range = await scrollRange(w); }
    if (range > 200) {
      win = w;
      const label = await page.evaluate(x => WS.find((w: any) => w.id === x).label, id);
      moved = { id, title: label + ' · Agent Friday' };
      break;
    }
    await w.locator('.fwin-btns button').last().click();        // close it and try the next
    await expect(page.locator(`.fwin:has([data-ws-tab="${id}"])`)).toHaveCount(0);
  }
  expect(win, 'no workspace had enough in it to scroll').not.toBeNull();
  const id = moved.id;
  // somewhere down the window, so there is a place to carry across
  await win!.locator('.fwin-body').evaluate(el => { const sc = (window as any).fridayMainScroller(el); sc.scrollTop = Math.round((sc.scrollHeight - sc.clientHeight) * 0.4); });
  const asked = ctx.waitForEvent('request', r => r.resourceType() === 'document' && r.url().includes('/w/' + id));
  const [tab] = await Promise.all([ctx.waitForEvent('page'), win!.locator(`[data-ws-tab="${id}"]`).click()]);
  await tab.waitForURL(u => u.pathname === '/w/' + id, { timeout: 30000 });
  const url = new URL((await asked).url());          // what the tab was opened with
  expect(url.origin).toBe(new URL(BASE).origin);         // no named address proven here
  // the desktop window is gone (folded into its dock icon)
  await expect(page.locator(`.fwin:has([data-ws-tab="${id}"])`)).toHaveCount(0);
  await tab.waitForSelector(`[data-standalone="${id}"] .ws-tab-body > *`, { timeout: 60000 });
  await expectFront(profile, moved.title);
  // the carried scroll position was used, then dropped from the address
  expect(Number(url.searchParams.get('scroll'))).toBeCloseTo(0.4, 1);
  await expect.poll(() => tab.evaluate(() => { const sc = (window as any).fridayMainScroller(document.querySelector('.ws-tab-body')); return sc ? sc.scrollTop / (sc.scrollHeight - sc.clientHeight) : 0; }), { timeout: 20000 }).toBeGreaterThan(0.2);
  expect(new URL(tab.url()).searchParams.has('scroll')).toBe(false);
});

test('asking again finds the same tab: no copy, and it comes back to the front', async () => {
  const page = ctx.pages().find(p => !p.url().includes('/w/'))!;
  await page.bringToFront();
  await expectFront(profile, await page.title());
  const before = ctx.pages().length;
  const win = await openWindow(page, moved.id);
  let opened = false;
  ctx.once('page', () => { opened = true; });
  await win.locator(`[data-ws-tab="${moved.id}"]`).click();
  await page.waitForTimeout(1500);
  expect(opened).toBe(false);
  expect(ctx.pages().length).toBe(before);
  await expect(page.locator(`.fwin:has([data-ws-tab="${moved.id}"])`)).toHaveCount(0);
  await expectFront(profile, moved.title);
});

test('a blocked tab keeps the window open and says how to allow it', async () => {
  const page = await desktop();
  await closeOthers(page);
  await page.evaluate(() => { (window as any).open = () => null; });
  const win = await openWindow(page, 'calendar');
  await win.locator('[data-ws-tab="calendar"]').click();
  await page.waitForTimeout(800);
  await expect(page.locator('.fwin:has([data-ws-tab="calendar"])')).toHaveCount(1);
  const hint = win.locator('.fwin-tab-blocked');
  await expect(hint).toBeVisible();
  await expect(hint).toContainText('Allow pop-ups for ' + new URL(BASE).host);
  expect(ctx.pages().length).toBe(1);
  await hint.locator('button').click();
  await expect(hint).toHaveCount(0);
  await expect(page.locator('.fwin:has([data-ws-tab="calendar"])')).toHaveCount(1);
});

test('middle-click and Ctrl+click on the dock open a background tab and change nothing here', async () => {
  const page = await desktop();
  await closeOthers(page);
  const title = await page.title();
  const [bg] = await Promise.all([ctx.waitForEvent('page'), dockClick(page, 'code', 'middle')]);
  await bg.waitForURL(/\/w\/code/, { timeout: 30000 });
  await page.waitForTimeout(800);
  await expectFront(profile, title);
  await expect(page.locator('.fwin:has([data-ws-tab="code"])')).toHaveCount(0);
  await page.keyboard.down('Control');
  const [bg2] = await Promise.all([ctx.waitForEvent('page'), dockClick(page, 'wiki')]);
  await page.keyboard.up('Control');
  await bg2.waitForURL(/\/w\/wiki/, { timeout: 30000 });
  await page.waitForTimeout(800);
  await expectFront(profile, title);
  await expect(page.locator('.fwin:has([data-ws-tab="wiki"])')).toHaveCount(0);
});

test('on localhost, the tab opens on Friday\'s own proven address', async () => {
  const page = await desktop(NAMED);
  await closeOthers(page);
  const win = await openWindow(page, 'news');
  const [tab] = await Promise.all([ctx.waitForEvent('page'), win.locator('[data-ws-tab="news"]').click()]);
  await tab.waitForURL(u => u.toString().startsWith(NAMED + '/w/news'), { timeout: 30000 });
  // served through Friday's own TLS listener on the test port
  await tab.waitForSelector('[data-standalone="news"] .ws-tab-body > *', { timeout: 90000 });
  await expect(page.locator('.fwin:has([data-ws-tab="news"])')).toHaveCount(0);
  await expectFront(profile, 'News · Agent Friday');
  // the tab on the named address is still found by name from localhost
  await page.bringToFront();
  const before = ctx.pages().length;
  const again = await openWindow(page, 'news');
  await again.locator('[data-ws-tab="news"]').click();
  await page.waitForTimeout(1500);
  expect(ctx.pages().length).toBe(before);
  await expectFront(profile, 'News · Agent Friday');
});
