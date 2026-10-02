/**
 * The app suite's `test` and `expect`. Every spec imports them from here.
 *
 * Against the live Friday (LIVE_READONLY=1, see target.js) the run is
 * read-only by construction: every non-GET request the page makes is blocked
 * and the test fails naming it, and the `request` fixture refuses non-GET
 * calls outright. Against a scratch server nothing is changed.
 */
import { test as base, expect } from '@playwright/test';
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { resolveTarget } = require('./target.js');

const TARGET = resolveTarget(process.env);
const READS = new Set(['GET', 'HEAD', 'OPTIONS']);

export const test = base.extend<{ liveReadOnly: void }>({
  liveReadOnly: [async ({ context }, use) => {
    if (!TARGET.live) {
      await use();
      return;
    }
    const attempted: string[] = [];
    await context.route('**/*', route => {
      const r = route.request();
      if (!READS.has(r.method())) {
        attempted.push(r.method() + ' ' + r.url());
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    await use();
    if (attempted.length) {
      throw new Error('LIVE_READONLY: this test tried to write to the live Friday:\n  '
        + attempted.join('\n  '));
    }
  }, { auto: true }],

  request: async ({ request }, use) => {
    if (!TARGET.live) {
      await use(request);
      return;
    }
    const refuse = (what: string) => () => {
      throw new Error('LIVE_READONLY: request.' + what + ' to the live Friday is refused');
    };
    const guarded = new Proxy(request, {
      get(target, prop, receiver) {
        if (['post', 'put', 'patch', 'delete'].includes(String(prop))) return refuse(String(prop));
        if (prop === 'fetch') {
          return (url: any, opts: any = {}) => {
            const m = String(opts.method || 'GET').toUpperCase();
            if (!READS.has(m)) refuse('fetch ' + m)();
            return target.fetch(url, opts);
          };
        }
        const v = Reflect.get(target, prop, receiver);
        return typeof v === 'function' ? v.bind(target) : v;
      },
    });
    await use(guarded);
  },
});

export { expect };
export type { Page, TestInfo, APIRequestContext } from '@playwright/test';
