/**
 * Config for the app-level suite (tests/app).
 *
 * Deliberately separate from playwright.config.ts so the older specs and
 * whatever else runs against ./tests are untouched by this.
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/app',
  timeout: 600_000,          // vision judging on a local model is slow
  expect: { timeout: 10_000 },
  retries: 0,                // a flaky pass is worse than an honest fail
  workers: 2,                // the server is a single local process; do not swamp it
  fullyParallel: true,
  reporter: [['list']],
  use: {
    baseURL: process.env.FRIDAY_BASE || 'http://localhost:3000',
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
