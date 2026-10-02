/**
 * Config for the app-level suite (tests/app).
 *
 * Deliberately separate from playwright.config.ts so the older specs and
 * whatever else runs against ./tests are untouched by this.
 */
import { defineConfig } from '@playwright/test';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { execSync } from 'child_process';
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { resolveTarget, scratchServer } = require('./tests/app/target.js');

// TESTS NEVER WRITE TO THE LIVE FRIDAY. With FRIDAY_BASE unset the suite runs
// against a scratch server with its own temporary FRIDAY_HOME, started here;
// the live server (:3000, or any non-loopback host) is refused unless
// LIVE_READONLY=1, which blocks every non-GET request (tests/app/fixtures.ts).
const target = resolveTarget(process.env);

function python(): string {
  if (process.env.FRIDAY_PYTHON) return process.env.FRIDAY_PYTHON;
  const local = path.join(__dirname, 'venv', 'Scripts', 'python.exe');
  if (fs.existsSync(local)) return local;
  try {
    // A worktree has no venv of its own; the main checkout's is beside .git.
    const common = execSync('git rev-parse --path-format=absolute --git-common-dir',
      { cwd: __dirname }).toString().trim();
    const main = path.join(path.dirname(common), 'venv', 'Scripts', 'python.exe');
    if (fs.existsSync(main)) return main;
  } catch { /* fall through */ }
  return 'python';
}

let webServer;
// Listing tests starts no server, so it needs no home.
if (target.scratch && !process.argv.includes('--list')) {
  // One temporary home per run: workers re-read this file and reuse it.
  if (!process.env.FRIDAY_PW_HOME) {
    process.env.FRIDAY_PW_HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'friday-pw-home-'));
  }
  webServer = scratchServer(target, __dirname, python(), process.env.FRIDAY_PW_HOME);
}
// Specs and helpers read the resolved target, never a default of their own.
process.env.FRIDAY_BASE = target.baseURL;

export default defineConfig({
  webServer,
  globalTeardown: target.scratch ? require.resolve("./tests/app/teardown.js") : undefined,
  testDir: './tests/app',
  timeout: 600_000,          // vision judging on a local model is slow
  expect: { timeout: 10_000 },
  retries: 0,                // a flaky pass is worse than an honest fail
  workers: 2,                // the server is a single local process; do not swamp it
  fullyParallel: true,
  reporter: [['list']],
  use: {
    baseURL: target.baseURL,
    headless: true,
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    // Without a real GL backend, headless Chromium renders the holographic
    // scene as a tangle of wireframe boxes, and the vision judge correctly
    // but uselessly reports it as broken. This repo hit the same thing with
    // the 3D knowledge galaxy.
    launchOptions: { args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] },
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
