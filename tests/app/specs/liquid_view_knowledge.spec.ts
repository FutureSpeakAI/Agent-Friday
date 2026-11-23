/**
 * The desktop draws Knowledge from its A2UI document (contract draft 3): /liquid/knowledge on a scratch Friday whose
 * wiki holds a few SYNTHETIC pages. The page asks the PC's own route for the bound tree (the same document and data
 * the phone gets from /tv/v1/workspaces/knowledge/view) and static/friday_a2ui.js draws it. Frames are kept at
 * desktop and phone width; nothing is written to any live Friday.
 */
import * as fs from 'fs';
import * as path from 'path';
import { test, expect } from '../fixtures';

const PAGES: Record<string, string[]> = {
  entities: ['sample-person-one', 'sample-person-two'],
  projects: ['garden-plan'],
  notes: ['reading-list', 'weekly-review', 'ideas'],
};

test.beforeAll(() => {
  const home = process.env.FRIDAY_PW_HOME;
  if (!home) return;                                   // a read-only live run seeds nothing
  for (const [section, names] of Object.entries(PAGES)) {
    const dir = path.join(home, 'wiki', section);
    fs.mkdirSync(dir, { recursive: true });
    for (const n of names) fs.writeFileSync(path.join(dir, n + '.md'), '# ' + n.replace(/-/g, ' ') + '\n\nSynthetic page.\n');
  }
});

for (const [name, size] of [['desktop', { width: 1280, height: 820 }], ['phone', { width: 412, height: 900 }]] as const) {
  test(`Knowledge draws from its A2UI document (${name})`, async ({ page }, info) => {
    await page.setViewportSize(size);
    await page.goto('/liquid/knowledge');
    const token = await page.evaluate(() => (window as any).__FRIDAY_API_TOKEN || '');
    const api = await page.request.get('/api/liquid/views/knowledge/rendered', { headers: { 'X-Friday-Token': token } });
    const body = await api.text();
    expect(api.status(), body).toBe(200);
    expect(JSON.parse(body).ok, body.slice(0, 600)).toBe(true);
    const drawn = await page.evaluate(async () => {
      try {
        const r = await fetch('/api/liquid/views/knowledge/rendered', { headers: { 'X-Friday-Token': (window as any).__FRIDAY_API_TOKEN || '' } });
        const t = await r.text();
        try { const p = JSON.parse(t); return 'ok ' + (window as any).FridayA2ui.toHtml(p.tree, p.look).slice(0, 80); } catch (e) { return 'ERR ' + e + ' ' + r.status + ' ' + t.slice(0, 200); }
      } catch (e) { return 'FETCH ' + e; }
    });
    expect(drawn).toMatch(/^ok /);
    const view = page.locator('[data-a2ui-view]');
    await expect(view.locator('h2')).toHaveText('Knowledge');
    await expect(view.locator('section[data-a2ui="status_block"]')).toHaveCount(2);
    await expect(view.locator('table').first().locator('tbody tr')).toHaveCount(3);       // entities, notes, projects
    await expect(view.locator('a, input, iframe, script, img')).toHaveCount(0);
    const shot = await page.screenshot({ fullPage: true });
    await info.attach(`liquid-knowledge-${name}`, { body: shot, contentType: 'image/png' });
    const out = process.env.FRIDAY_FRAMES_DIR;
    if (out) { fs.mkdirSync(out, { recursive: true }); fs.writeFileSync(path.join(out, `desktop-liquid-knowledge-${name}.png`), shot); }
  });
}
