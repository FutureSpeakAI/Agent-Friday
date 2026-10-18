/**
 * Wait until the page is quiet, instead of sleeping for a fixed time.
 *
 * "Quiet" means both, for `quietMs` in a row:
 *  - no network request in flight, apart from Friday's own polling and its
 *    streams (they never stop, so they cannot count), and
 *  - no change to the DOM under `root` (nodes, attributes or text).
 * It never waits longer than `timeout`. Reaching the cap is not an error: the
 * caller's next assertion decides. The result says whether the page settled.
 *
 * A fixed sleep is too long on a fast runner and too short on a slow one; this
 * waits for exactly as long as the page is still busy. Use it only where the
 * test needs "everything that was going to happen has happened" (a section
 * fetching its own data, an animation finishing before a screenshot). Where
 * the test waits for one thing, wait for that thing (an expect on a locator).
 * Where the test is ABOUT time (a 700 ms hold, three seconds after leaving),
 * keep the clock wait: that is the behaviour under test.
 *
 * The pattern (requests in flight, ignore polling and streams, hard cap) is
 * the one browser agents use after each action; this is our own code, with no
 * dependency and nothing sent anywhere.
 */
import type { Page, Request } from '@playwright/test';

export type SettleOptions = {
  /** How long nothing may happen, in ms (default 300). */
  quietMs?: number;
  /** Hard cap, in ms (default 5000). */
  timeout?: number;
  /** CSS selector whose subtree is watched for DOM changes (default: the whole body). */
  root?: string;
  /** Request URLs that never count as busy (default: Friday's polling and streams). */
  ignore?: RegExp;
};

export type Settled = { settled: boolean; waitedMs: number; busy: string[] };

/** Endpoints the desktop page polls on a timer, and its streams: never "in flight" for settling. */
export const POLLING = new RegExp(
  '/api/(health|processes|tasks|meetings/status|control/permission|local-queue|arbiter/status|lanes/summary' +
  '|settings/changes|desktop/events|desktop/state|notifications|connectors/health|phone/status|machine/level' +
  '|system/network-status|intelligence|ambient/state|approvals|activity|presence)(\\?|/stream|$)' +
  '|/(events|stream|sse)(\\?|$)');

type Tracker = { inflight: Map<Request, string>; lastChange: number };
const trackers = new WeakMap<Page, Tracker>();

/** Start counting this page's requests. settle() does this itself; call it early to also see requests already running. */
export function trackNetwork(page: Page, ignore: RegExp = POLLING): Tracker {
  let t = trackers.get(page);
  if (t) return t;
  t = { inflight: new Map(), lastChange: Date.now() };
  const tr = t;
  const done = (r: Request) => { if (tr.inflight.delete(r)) tr.lastChange = Date.now(); };
  page.on('request', r => {
    const kind = r.resourceType();
    if (kind === 'websocket' || kind === 'eventsource' || ignore.test(r.url())) return;
    tr.inflight.set(r, r.method() + ' ' + r.url());
    tr.lastChange = Date.now();
  });
  page.on('requestfinished', done);
  page.on('requestfailed', done);
  trackers.set(page, t);
  return t;
}

/** Install (once per document) a MutationObserver that records when the DOM under `root` last changed. */
async function domQuietFor(page: Page, root: string): Promise<number> {
  return page.evaluate((sel) => {
    const w = window as any;
    w.__fridaySettle = w.__fridaySettle || {};
    const box = w.__fridaySettle;
    if (!box[sel]) {
      const el = document.querySelector(sel);
      if (!el) return 0;
      box[sel] = { last: performance.now() };
      // Inline style changes do not count: the desktop's dock and scene restyle elements every frame
      // (depth, parallax), which would keep the page "busy" forever.
      new MutationObserver(records => {
        if (records.some(r => !(r.type === 'attributes' && r.attributeName === 'style'))) box[sel].last = performance.now();
      }).observe(el, { childList: true, subtree: true, attributes: true, characterData: true });
    }
    return performance.now() - box[sel].last;
  }, root).catch(() => 0);   // mid-navigation: not quiet yet
}

export async function settle(page: Page, opts: SettleOptions = {}): Promise<Settled> {
  const quietMs = opts.quietMs ?? 300;
  const cap = opts.timeout ?? 5000;
  const root = opts.root ?? 'body';
  const tracker = trackNetwork(page, opts.ignore);
  const start = Date.now();
  for (;;) {
    const now = Date.now();
    const netQuiet = tracker.inflight.size === 0 && now - tracker.lastChange >= quietMs;
    const dom = await domQuietFor(page, root);
    if (netQuiet && dom >= quietMs) return { settled: true, waitedMs: Date.now() - start, busy: [] };
    if (now - start >= cap) {
      return { settled: false, waitedMs: now - start, busy: [...tracker.inflight.values()].slice(0, 5) };
    }
    await new Promise(r => setTimeout(r, 50));
  }
}
