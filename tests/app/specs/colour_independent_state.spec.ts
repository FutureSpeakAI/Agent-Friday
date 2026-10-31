// State is never shown by colour alone (WCAG 2.2 1.4.1; north star 33.1-11; burn-down BD-21).
//
// The page's own stylesheet draws each kind of state as a different shape: ok a filled circle, busy a half-filled
// circle, warn a diamond, bad a square, off a hollow ring. This renders one mark of each kind in the real page, with
// the real CSS, and proves that no two look alike once colour is taken away. The same for the step marks in a
// process thread, the trust marks, the tone marks, and the dock's "open" and "notice" dots.
import { test, expect } from '../fixtures';
import { bootApp } from '../harness';

type Shape = { radius: string; transform: string; image: string; filled: boolean; border: string };

test('each kind of state has its own shape, not just its own colour', async ({ page }) => {
  await bootApp(page);
  const shapes = await page.evaluate(() => {
    // A plain host: inside a real container (the dock, say) other rules would add their own transforms.
    const host = document.createElement('div');
    document.body.appendChild(host);
    const groups: Record<string, string[][]> = {
      'fr-state': [['ok'], ['busy'], ['warn'], ['bad'], ['off']],
      'thread-dot': [['done'], ['in-progress'], ['pending'], ['error']],
      'trust-dot': [['green'], ['yellow'], ['red']],
      'trust-obs-dot': [['green'], ['yellow'], ['red']],
      'sentiment-dot': [['positive'], ['neutral'], ['negative']],
    };
    const out: Record<string, Record<string, Shape>> = {};
    const read = (el: Element): Shape => {
      const s = getComputedStyle(el);
      const bg = s.backgroundColor;
      return { radius: s.borderTopLeftRadius, transform: s.transform, image: s.backgroundImage,
        filled: !(bg === 'transparent' || /rgba\(.*,\s*0\)$/.test(bg)), border: s.borderTopWidth };
    };
    for (const [cls, states] of Object.entries(groups)) {
      out[cls] = {};
      for (const [st] of states) {
        const el = document.createElement('span');
        if (cls === 'fr-state') { el.className = 'fr-state'; el.setAttribute('data-state', st); } else el.className = cls + ' ' + st;
        host.appendChild(el);
        out[cls][st] = read(el);
      }
    }
    // The dock: an open window and a waiting notice.
    const open = document.createElement('button'); open.className = 'dock-btn active';
    const notice = document.createElement('button'); notice.className = 'dock-btn has-notice';
    for (const b of [open, notice]) { const d = document.createElement('span'); d.className = 'dot'; b.appendChild(d); document.body.appendChild(b); }
    out['dock'] = { open: read(open.querySelector('.dot')!), notice: read(notice.querySelector('.dot')!) };
    return out;
  });
  for (const [group, byState] of Object.entries(shapes)) {
    const sig = Object.entries(byState).map(([st, s]) => [st, JSON.stringify([s.radius, s.transform, s.image, s.filled, s.border])]);
    const seen = new Map<string, string>();
    for (const [st, k] of sig) {
      expect(seen.get(k), `${group}: "${st}" looks the same as "${seen.get(k)}" without colour`).toBeUndefined();
      seen.set(k, st);
    }
  }
});

test('the camera light says its state in words too', async ({ page }) => {
  await bootApp(page);
  const cam = page.locator('#camera-indicator');
  await expect(cam).toHaveAttribute('role', 'img');
  await expect(cam).toHaveAttribute('aria-label', /^Camera (off|on|not live)/);
});
