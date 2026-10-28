// Headings a screen reader can move by (WCAG 2.2 1.3.1 and 2.4.6; north star 33.1-6; burn-down BD-21).
//
// The page has one top-level heading, and every open window's title is a level-2 heading under it, so a person
// who lists headings (or presses H in a screen reader) can go from window to window. Inside a window nothing claims
// level 1. A workspace's own top headings are h2 today and sit at the window title's level; the outline of each window
// is printed so that can be read from the run (a follow-up moves them to level 3).
import { test, expect } from '../fixtures';
import { bootApp } from '../harness';
import { settle } from '../helpers/settle';

const WORKSPACES = ['settings', 'calendar', 'messages', 'news', 'knowledge'];

test('one top-level heading, a level-2 heading per window, no level-1 heading inside a window', async ({ page }) => {
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
        tooHigh: inner.filter(e => level(e) < 2).map(e => e.tagName + ' ' + (e.textContent || '').trim().slice(0, 40)),
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
    'level-1 headings inside a window (the page has one, outside every window)').toEqual([]);
});
