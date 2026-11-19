import type { Page } from '@playwright/test';

/**
 * When a Settings-walk click times out (F7), say which side stopped answering: the server (GET /api/health) or
 * the page (a trivial script in it). A red run used to say only "locator.click: Timeout 45000ms exceeded", which
 * cannot tell a covered control from a starved runner. The answer is a short string for the row's detail.
 * Never throws.
 */
export async function whoStalled(page: Page, base: string, limitMs = 8000): Promise<string> {
  const t0 = Date.now();
  const server = await page.context().request
    .get(base + '/api/health', { timeout: limitMs })
    .then(r => 'server answered /api/health ' + r.status() + ' in ' + (Date.now() - t0) + ' ms')
    .catch(() => 'server did NOT answer /api/health in ' + limitMs + ' ms');
  const t1 = Date.now();
  const timer = new Promise<string>(res => setTimeout(() => res('page did NOT answer in ' + limitMs + ' ms'), limitMs));
  const answered = page.evaluate(() => 1).then(() => 'page answered in ' + (Date.now() - t1) + ' ms').catch(() => 'page did NOT answer (closed or failed)');
  const pageSaid = await Promise.race([answered, timer]);
  return ' [' + server + '; ' + pageSaid + ']';
}
