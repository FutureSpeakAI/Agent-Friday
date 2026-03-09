#!/usr/bin/env node
/**
 * fix-libsodium-esm.js — Postinstall patch for libsodium ESM resolution
 *
 * Problem: libsodium-wrappers-sumo's ESM entry does:
 *   import e from "./libsodium-sumo.mjs"
 *
 * This relative import expects libsodium-sumo.mjs as a sibling file, but it
 * lives in a separate npm package (libsodium-sumo). CJS resolves it through
 * node_modules traversal; ESM relative imports don't.
 *
 * Vitest runs ESM, so tests using the crypto vault (which depends on
 * libsodium-wrappers-sumo) fail with "Cannot find module" unless the file
 * exists at the expected relative path.
 *
 * Fix: Copy the file from libsodium-sumo to where the ESM import expects it.
 * This runs as a postinstall script to survive `npm install`.
 */

const fs = require('fs');
const path = require('path');

const src = path.join(
  __dirname, '..', 'node_modules', 'libsodium-sumo',
  'dist', 'modules-sumo-esm', 'libsodium-sumo.mjs'
);

const dest = path.join(
  __dirname, '..', 'node_modules', 'libsodium-wrappers-sumo',
  'dist', 'modules-sumo-esm', 'libsodium-sumo.mjs'
);

if (!fs.existsSync(src)) {
  console.log('[fix-libsodium-esm] Source not found, skipping (libsodium-sumo not installed)');
  process.exit(0);
}

if (fs.existsSync(dest)) {
  console.log('[fix-libsodium-esm] Already patched, skipping');
  process.exit(0);
}

fs.copyFileSync(src, dest);
console.log('[fix-libsodium-esm] Patched: copied libsodium-sumo.mjs for ESM resolution');
