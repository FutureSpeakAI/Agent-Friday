// Headings a screen reader can move by (WCAG 2.2 1.3.1 and 2.4.6; north star 33.1-6; burn-down BD-21).
//
// The page has one top-level heading, and every open window's title is a level-2 heading under it, so a person
// who lists headings (or presses H in a screen reader) can go from window to window. Inside a window nothing claims
// level 1 or 2: a workspace's own headings are read at level 3 and down, under its window's title. The outline of
// each window is printed.
import { test, expect } from '../fixtures';
import { bootApp } from '../harness';
import { settle } from '../helpers/settle';

const WORKSPACES = ['settings', 'calendar', 'messages', 'news', 'knowledge'];

test('one top-level heading, a level-2 heading per window, nothing above level 3 inside a window', async ({ page }) => {
  // Past first run: its own screen ("Before anything else") is a page of its own, with its own h1.
  await page.route('**/api/setup/status', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ initialized: true }) }));
  await bootApp(page);
  for (const ws of WORKSPACES) {
    await page.evaluate(w => (window as any).fridayRunActions([{ type: 'navigate', workspace: w }]), ws);
    const title = page.locator(`.fwin[data-friday-workspace="${ws}"] .fwin-title-text`).first();
    await expect(title).toBeVisible({ timeout: 30_000 });
    await expect(title, ws + ": the window's title is a level-2 heading").toHaveAttribute('role', 'heading');
    await expect(title).toHaveAttribute('aria-level', '2');
  }
  await settle(page, { quietMs: 1000, timeout: 15_000 });
  const found = await page.evaluate(() => {
    const level = (e: Element) => {
      // The level a screen reader announces: aria-level when set (it overrides the tag), else the tag's.
      if (e.getAttribute('aria-level')) return Number(e.getAttribute('aria-level'));
      const m = /^H([1-6])$/.exec(e.tagName);
      return m ? Number(m[1]) : 2;
    };
    const all = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]'));
    const tops = all.filter(e => level(e) === 1).map(e => (e.textContent || '').trim());
    const wins = Array.from(document.querySelectorAll('.fwin')).map(w => {
      const t = w.querySelector('.fwin-title-text');
      const body = w.querySelector('.fwin-body');
      const inner = body ? Array.from(body.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')) : [];
      return {
        ws: (w as HTMLElement).dataset.fridayWorkspace || '?',
        title: t ? { role: t.getAttribute('role'), level: t.getAttribute('aria-level'), text: (t.textContent || '').trim() } : null,
        tooHigh: inner.filter(e => level(e) < 3).map(e => e.tagName + ' ' + (e.textContent || '').trim().slice(0, 40)),
        outline: inner.map(e => level(e) + ' ' + (e.textContent || '').trim().slice(0, 40)),
      };
    });
    return { tops, wins };
  });
  for (const w of found.wins) console.log(`[headings] ${w.ws}: ${w.outline.join(' | ') || '(no headings inside)'}`);
  expect(found.tops, 'the page has exactly one top-level heading').toEqual(['Agent Friday™']);
  for (const w of found.wins) {
    expect(w.title, `${w.ws}: the window's title is a level-2 heading`).toMatchObject({ role: 'heading', level: '2' });
    expect(w.title!.text.length, `${w.ws}: the heading has text`).toBeGreaterThan(0);
  }
  expect(found.wins.filter(w => w.tooHigh.length).map(w => `${w.ws}: ${w.tooHigh.join(', ')}`),
    'headings inside a window at level 1 or 2 (they would sit beside the window titles)').toEqual([]);
});
