// Accessibility audit of every workspace (north star 33.1-1, 33.1-5, 34.14-2; burn-down BD-21: "the audit runs
// in CI and passes on every workspace"). Each workspace is opened in its own tab (/w/<id>, the same page a person
// gets from "open in a new tab"), and its accessibility tree (what a screen reader reads) is checked:
//   - every control a person can operate has a name (a button, link, field, switch, radio, tab, menu item...);
//   - every image that carries meaning has a text alternative (decorative ones are alt="" and not in the tree).
// The findings are attached to the run as one JSON file and printed, workspace by workspace.
//
// SCRATCH server only (FRIDAY_BASE must be set; the live :3000 is refused). Every write is answered here.
import { test, expect, type Page } from '../fixtures';
import { settle } from '../helpers/settle';
import { BASE } from '../harness';

test.skip(!BASE || /:3000(\/|$)/.test(BASE), 'set FRIDAY_BASE to a scratch server (never the live :3000)');
test.use({ viewport: { width: 1440, height: 900 } });
test.setTimeout(30 * 60_000);

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

// Roles a person operates. A line of the aria snapshot for one of these with no quoted name has no name.
const OPERABLE = ['button', 'link', 'textbox', 'searchbox', 'checkbox', 'radio', 'switch', 'combobox', 'slider',
  'spinbutton', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'treeitem'];
const UNNAMED = new RegExp(`^\\s*- (${OPERABLE.join('|')}|img)(?:\\s*\\[[^\\]]*\\])*\\s*:?\\s*$`);

type Finding = { workspace: string; role: string; line: string };

async function answerWrites(page: Page) {
  await page.route('**/*', r => (r.request().method() === 'GET' ? r.fallback() : r.fulfill(json({ status: 'ok' }))));
  await page.route('**/api/setup/status', r => r.fulfill(json({ initialized: true })));
}

async function workspaceIds(page: Page): Promise<string[]> {
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window as any).FRIDAY_WORKSPACE_REGISTRY, null, { timeout: 90_000 });
  return page.evaluate(() => {
    const w = window as any;
    const all = ((w.FRIDAY_WORKSPACE_REGISTRY || {}).workspaces || []) as any[];
    return all.filter(x => x && x.id && !x.held).map(x => String(x.id));
  });
}

test('every workspace: each control has a name and each meaningful image a text alternative', async ({ page }, info) => {
  await answerWrites(page);
  const ids = await workspaceIds(page);
  expect(ids.length, 'the registry lists the workspaces').toBeGreaterThan(10);
  const findings: Finding[] = [];
  const notOpened: string[] = [];
  for (const id of ids) {
    try {
      await page.goto(`${BASE}/w/${encodeURIComponent(id)}`, { waitUntil: 'domcontentloaded' });
      await page.locator('#ui-inner, #ui-root').first().waitFor({ state: 'attached', timeout: 60_000 });
      await settle(page, { quietMs: 1000, timeout: 10000 });          // let the workspace draw its first data
    } catch {
      notOpened.push(id);
      continue;
    }
    let snap = '';
    try { snap = await page.locator('body').ariaSnapshot({ timeout: 20_000 }); } catch { notOpened.push(id); continue; }
    for (const line of snap.split('\n')) {
      const m = line.match(UNNAMED);
      if (m) findings.push({ workspace: id, role: m[1], line: line.trim() });
    }
  }
  const byWs: Record<string, number> = {};
  for (const f of findings) byWs[f.workspace] = (byWs[f.workspace] || 0) + 1;
  const report = { workspaces: ids.length, notOpened, unnamed: findings.length, byWorkspace: byWs, findings };
  await info.attach('accessibility-audit.json', { body: JSON.stringify(report, null, 1), contentType: 'application/json' });
  console.log('[a11y-audit] ' + JSON.stringify({ workspaces: ids.length, notOpened, unnamed: findings.length, byWorkspace: byWs }));
  for (const f of findings.slice(0, 300)) console.log(`[a11y-audit] ${f.workspace}: ${f.line}`);
  expect(notOpened, 'workspaces that did not open for the audit').toEqual([]);
  expect(findings, 'controls with no name, or meaningful images with no text alternative').toEqual([]);
});
