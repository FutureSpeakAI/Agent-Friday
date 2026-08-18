/**
 * Liveness assertions.
 *
 * The dominant failure mode in this codebase is not a wrong answer. It is a
 * subsystem that runs, reports success, and produces nothing: a progress bar
 * with two values, a cancel that cancels nothing, a log that never fills, a
 * gallery that never refreshes, a claim with no artifact behind it.
 *
 * Correctness assertions do not catch that, because nothing is incorrect --
 * it is merely absent. These test for presence and change, and each is written
 * to generalise past the specific bug that motivated it.
 */
import { expect, type Page } from '@playwright/test';

const plural = (n: number, s: string) => `${n} ${s}${n === 1 ? '' : 's'}`;
const NL = String.fromCharCode(10);

/** Poll `probe` every `everyMs` for up to `forMs`, returning every sample. */
export async function sampleWhile<T>(
  probe: () => Promise<T>,
  opts: { forMs: number; everyMs?: number; until?: (v: T) => boolean },
): Promise<T[]> {
  const everyMs = opts.everyMs ?? 250;
  const deadline = Date.now() + opts.forMs;
  const samples: T[] = [];
  while (Date.now() < deadline) {
    let v: T;
    try { v = await probe(); } catch { await new Promise(r => setTimeout(r, everyMs)); continue; }
    samples.push(v);
    if (opts.until?.(v)) break;
    await new Promise(r => setTimeout(r, everyMs));
  }
  return samples;
}

/**
 * A progress indicator must take more than `min` distinct values.
 *
 * Motivated by a bar with exactly two possible values, 0.5 and 1.0, shown as a
 * percentage. It was never wrong -- 50% really was the state -- it simply could
 * not express anything else, so it told you nothing.
 */
export function assertVaries(samples: number[], label: string, min = 2) {
  const seen = [...new Set(samples.filter(n => Number.isFinite(n)))].sort((a, b) => a - b);
  expect(
    seen.length,
    `${label} only ever showed ${plural(seen.length, 'distinct value')} across ` +
    `${plural(samples.length, 'sample')}: [${seen.join(', ')}].` + NL +
    `  An indicator that can show at most ${seen.length} value(s) is decoration, not progress -- ` +
    `it cannot tell anyone how far along the work is.`,
  ).toBeGreaterThan(min);
}

/**
 * A log must gain lines WHILE the work runs, not only once it returns.
 *
 * Motivated by task logs reading "waiting for activity" forever because lines
 * were replayed after the call completed. Asserting the log is non-empty at the
 * end would have passed. This samples during.
 */
export function assertGrewDuring(counts: number[], label: string) {
  const first = counts[0] ?? 0;
  const last = counts[counts.length - 1] ?? 0;
  const grewMidway = counts.some((n, i) => i > 0 && n > counts[0]);
  expect(
    grewMidway,
    `${label} did not grow while the work was running.` + NL +
    `  Line counts sampled during execution: [${counts.join(', ')}] ` +
    `(started at ${first}, ended at ${last}).` + NL +
    (last > first
      ? `  It filled in only after the work finished, so anyone watching saw nothing happen the whole time.`
      : `  It never filled in at all.`),
  ).toBe(true);
}

/** A panel must not be showing its empty-state placeholder. */
export function assertNotPlaceholder(text: string, label: string, placeholders: RegExp[] = [
  /waiting for activity/i, /no items/i, /nothing (here|yet)/i, /^$/,
]) {
  const hit = placeholders.find(p => p.test(text.trim()));
  expect(
    hit,
    `${label} is still showing its empty-state placeholder: ${JSON.stringify(text.trim().slice(0, 120))}.` + NL +
    `  The panel rendered, but nothing ever arrived to put in it.`,
  ).toBeUndefined();
}

/**
 * A collection must contain something that did not exist before this test ran.
 *
 * Motivated by a gallery that fetched once on mount and never again, so newly
 * generated images never appeared. "The gallery has items" passed; it had
 * yesterday's items.
 */
export function assertCreatedByThisTest(before: string[], after: string[], expectedName: string, label: string) {
  const added = after.filter(x => !before.includes(x));
  expect(
    added.some(x => x.includes(expectedName)),
    `${label} never showed the item this test just created.` + NL +
    `  Expected something containing ${JSON.stringify(expectedName)}.` + NL +
    `  Before: ${plural(before.length, 'item')}. After: ${plural(after.length, 'item')}. ` +
    `New: ${added.length ? added.slice(0, 5).map(a => JSON.stringify(a)).join(', ') : 'none'}.` + NL +
    `  If the count did not change, the view is showing a list it fetched once and never refreshed.`,
  ).toBe(true);
}

/**
 * A success must arrive with the thing it claims to have produced.
 *
 * Motivated by "I have opened the file for you" with no tool call behind it,
 * and by a cancel that returned success while the job ran to completion.
 */
export function assertClaimBackedByArtifact(claim: string, artifact: unknown, label: string) {
  const missing = artifact == null ||
    (Array.isArray(artifact) && artifact.length === 0) ||
    (typeof artifact === 'string' && artifact.trim() === '');
  expect(
    missing,
    `${label} reported success but produced nothing to back it up.` + NL +
    `  It said: ${JSON.stringify(String(claim).slice(0, 160))}` + NL +
    `  The artifact that should have accompanied that claim was ${JSON.stringify(artifact)}.` + NL +
    `  A success with no artifact is the app agreeing with you rather than doing the work.`,
  ).toBe(false);
}

/** An element must appear exactly once. Motivated by a home icon rendered twice. */
export async function assertRenderedOnce(page: Page, selector: string, label: string) {
  const n = await page.locator(selector).count();
  expect(n, `${label} appears ${plural(n, 'time')} on screen; it should appear exactly once.`).toBe(1);
}

/** Newest-first ordering. Motivated by a gallery that put the newest work 7,880px down. */
export function assertNewestFirst(timestamps: number[], label: string) {
  const firstBad = timestamps.findIndex((t, i) => i > 0 && t > timestamps[i - 1]);
  expect(
    firstBad,
    `${label} is not ordered newest-first: item ${firstBad + 1} is newer than item ${firstBad}.` + NL +
    `  The most recent work is buried down the page where nobody will scroll to it.`,
  ).toBe(-1);
}

/**
 * Every image on screen must have actually decoded.
 *
 * A broken-image glyph sits in the DOM exactly like a good image, so "the img
 * element exists" passes while a person sees a broken icon.
 */
export async function assertImagesDecoded(page: Page, scope: string, label: string) {
  // Wait for decoding to settle first. Checking mid-load reports every image as
  // broken, which is how an assertion becomes noise and gets deleted.
  await page.waitForFunction((sel) => {
    const root = document.querySelector(sel) || document.body;
    return [...root.querySelectorAll('img')].every(i => i.complete);
  }, scope, { timeout: 15000 }).catch(() => { /* report whatever we have */ });

  const broken = await page.evaluate((sel) => {
    const root = document.querySelector(sel) || document.body;
    return [...root.querySelectorAll('img')]
      .filter(i => i.complete && i.naturalWidth === 0 && (i.getAttribute('src') || '').trim() !== '')
      .map(i => ({ src: (i.getAttribute('src') || '').slice(0, 120), alt: i.getAttribute('alt') || '' }));
  }, scope);

  expect(
    broken.map(b => b.src),
    `${label}: ${plural(broken.length, 'image')} did not load, so a person sees a broken-image icon:` + NL +
    broken.slice(0, 8).map(b => `  - ${b.src}${b.alt ? ` (the "${b.alt}" icon)` : ''}`).join(NL),
  ).toEqual([]);
}

/**
 * Text must have enough contrast against its background to be read.
 *
 * This is the code half of a verification loop: the vision judge reported that
 * buttons on the home screen were hard to read, and this measures the same
 * thing, turning an opinion into a number. WCAG AA asks 4.5:1 for normal text
 * and 3:1 for large text.
 */
export async function assertReadableContrast(page: Page, label: string, minRatio = 3) {
  const bad = await page.evaluate((min) => {
    const lum = (c: number[]) => {
      const [r, g, b] = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const parse = (s: string): number[] => {
      const n = (s.match(/[0-9.]+/g) || []).map(Number);
      if (n.length < 3) return [0, 0, 0, 0];
      return [n[0], n[1], n[2], n.length > 3 ? n[3] : 1];
    };
    const over = (fg: number[], bg: number[]) => {
      const a = fg[3];
      return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1];
    };

    /**
     * Resolve what is REALLY behind this text by compositing every translucent
     * layer down to the page background. Treating rgba(255,255,255,0.06) as
     * solid white is how a contrast check ends up reporting a dark theme as
     * white-on-white, and then gets deleted for crying wolf.
     */
    const bgOf = (el: Element): number[] => {
      const layers: number[][] = [];
      let e: Element | null = el;
      while (e) {
        const c = parse(getComputedStyle(e).backgroundColor);
        if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
        e = e.parentElement;
      }
      let base = layers.length && layers[layers.length - 1][3] >= 1
        ? layers.pop() as number[]
        : parse(getComputedStyle(document.documentElement).backgroundColor || 'rgb(0,0,0)');
      if (base[3] < 1) base = over(base, [0, 0, 0, 1]);
      for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
      return base;
    };

    const out: any[] = [];
    for (const e of document.querySelectorAll('button, a, h1, h2, h3, label, p, span')) {
      const el = e as HTMLElement;
      if (el.children.length > 0) continue;         // only nodes rendering their own text
      const text = (el.innerText || '').trim();
      if (text.length < 3) continue;                 // skip icon-only glyphs
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 8 || r.bottom < 0 || r.top > innerHeight) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.1) continue;
      const bg = bgOf(el);
      const fg = over(parse(cs.color), bg);
      const ratio = (Math.max(lum(fg), lum(bg)) + 0.05) / (Math.min(lum(fg), lum(bg)) + 0.05);
      if (ratio < min) out.push({
        text: text.slice(0, 40), ratio: +ratio.toFixed(2), color: cs.color,
        behind: 'rgb(' + bg.slice(0, 3).map(Math.round).join(',') + ')',
      });
    }
    return out;
  }, minRatio);

  expect(
    bad.map((b: any) => b.text),
    `${label}: ${plural(bad.length, 'piece')} of text cannot comfortably be read against ` +
    `what is actually behind it (below ${minRatio}:1):` + NL +
    bad.slice(0, 8).map((b: any) => `  - ${JSON.stringify(b.text)} at ${b.ratio}:1 -- ${b.color} on ${b.behind}`)
       .join(NL),
  ).toEqual([]);
}
