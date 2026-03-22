/**
 * Preload ↔ types.d.ts Sync Contract — Structural verification that every
 * namespace exposed by contextBridge.exposeInMainWorld('eve', { ... }) in
 * preload.ts has a corresponding type declaration in types.d.ts.
 *
 * This test catches the most common integration bug in the codebase: adding
 * a new IPC namespace to preload.ts without updating types.d.ts. Vite won't
 * catch this — only `npx tsc --noEmit` or this test will.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// ── Paths ──────────────────────────────────────────────────────────────────

const PRELOAD_PATH = resolve(__dirname, '../../src/main/preload.ts');
const TYPES_PATH = resolve(__dirname, '../../src/renderer/types.d.ts');

// ── Helpers ────────────────────────────────────────────────────────────────

/**
 * Extract top-level namespace keys from the preload `contextBridge.exposeInMainWorld('eve', { ... })`.
 *
 * Strategy: After the opening `exposeInMainWorld('eve', {`, look for lines that
 * match `  someKey: {` or `  someKey: (` at the 2-space indent level. These are
 * the top-level namespace names. Top-level function-style entries like
 * `getApiPort: ()` are also captured.
 */
function extractPreloadNamespaces(source: string): string[] {
  const namespaces: string[] = [];

  // Find the eve object literal start
  const eveStart = source.indexOf("exposeInMainWorld('eve',");
  if (eveStart === -1) return namespaces;

  // Work line by line from the eve start
  const lines = source.slice(eveStart).split('\n');

  // Track brace depth to stay within the top-level eve object
  let depth = 0;
  let started = false;

  for (const line of lines) {
    if (!started) {
      if (line.includes('{')) {
        started = true;
        depth = 1;
      }
      continue;
    }

    // Count braces to track depth
    for (const ch of line) {
      if (ch === '{') depth++;
      if (ch === '}') depth--;
    }

    // At depth 1 we are inside the top-level eve object
    // Match namespace-style keys (object or function) at 2-space indent
    if (depth >= 1) {
      const match = line.match(/^  (\w+)\s*:\s*[{(]/);
      if (match) {
        namespaces.push(match[1]);
      }
    }

    if (depth <= 0) break;
  }

  return namespaces;
}

/**
 * Extract namespace keys declared inside `eve: { ... }` in types.d.ts.
 *
 * Strategy: Find the `eve: {` block in the Window interface. The eve object's
 * direct child namespaces are at exactly 6-space indent. We scan from `eve: {`
 * until we find its closing `};` at the same indent level as `eve:`.
 *
 * We avoid brace-depth counting because TypeScript type literals (e.g.,
 * `Promise<{ id: string }>`) contain `{`/`}` that confuse depth trackers.
 * Instead, we rely on the stable indentation pattern of the declaration file.
 */
function extractTypesNamespaces(source: string): string[] {
  const namespaces: string[] = [];

  // Find the line containing `eve: {`
  const lines = source.split('\n');
  let eveLineIdx = -1;
  let eveIndent = '';

  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^(\s*)eve:\s*\{/);
    if (m) {
      eveLineIdx = i;
      eveIndent = m[1]; // e.g., "    " (4 spaces)
      break;
    }
  }

  if (eveLineIdx === -1) return namespaces;

  // The direct children of eve are at eveIndent + 2 more spaces
  const childIndent = eveIndent + '  ';
  // The closing `};` of eve is at eveIndent level
  const closingPattern = new RegExp(`^${eveIndent}\\};`);

  for (let i = eveLineIdx + 1; i < lines.length; i++) {
    const line = lines[i];

    // Stop when we hit the closing `};` at the eve indent level
    if (closingPattern.test(line)) break;

    // Match direct child namespace keys: exactly childIndent + word + `: {` or `: (`
    const childMatch = line.match(new RegExp(`^${childIndent}(\\w+)\\s*:\\s*[{(]`));
    if (childMatch) {
      namespaces.push(childMatch[1]);
    }
  }

  return namespaces;
}

// ── Tests ──────────────────────────────────────────────────────────────────

describe('Preload ↔ types.d.ts Contract', () => {
  const preloadSource = readFileSync(PRELOAD_PATH, 'utf-8');
  const typesSource = readFileSync(TYPES_PATH, 'utf-8');

  const preloadNamespaces = extractPreloadNamespaces(preloadSource);
  const typesNamespaces = extractTypesNamespaces(typesSource);

  it('preload.ts exposes at least one namespace', () => {
    expect(preloadNamespaces.length).toBeGreaterThan(0);
  });

  it('types.d.ts declares at least one namespace', () => {
    expect(typesNamespaces.length).toBeGreaterThan(0);
  });

  it('preload namespace count roughly matches types.d.ts count', () => {
    // Allow a small delta for top-level bare functions (getApiPort, etc.)
    // that may not have their own namespace in types.d.ts
    const delta = Math.abs(preloadNamespaces.length - typesNamespaces.length);
    expect(delta).toBeLessThan(10);
  });

  it('every preload namespace has a matching types.d.ts declaration', () => {
    const typesSet = new Set(typesNamespaces);
    const missing: string[] = [];

    for (const ns of preloadNamespaces) {
      // Skip top-level bare functions (not sub-objects)
      // These are like getApiPort, getGeminiApiKey — declared directly, not as namespaces
      if (['getApiPort', 'getGeminiApiKey', 'getLiveSystemInstruction', 'onFileModified'].includes(ns)) {
        continue;
      }
      if (!typesSet.has(ns)) {
        missing.push(ns);
      }
    }

    expect(
      missing,
      `Preload namespaces missing from types.d.ts: [${missing.join(', ')}]. ` +
      `Update src/renderer/types.d.ts to match preload.ts.`,
    ).toEqual([]);
  });

  it('every types.d.ts namespace has a matching preload implementation', () => {
    const preloadSet = new Set(preloadNamespaces);
    const orphaned: string[] = [];

    for (const ns of typesNamespaces) {
      if (!preloadSet.has(ns)) {
        orphaned.push(ns);
      }
    }

    expect(
      orphaned,
      `types.d.ts namespaces with no preload implementation: [${orphaned.join(', ')}]. ` +
      `These types declare an API that doesn't exist at runtime.`,
    ).toEqual([]);
  });

  it('known critical namespaces are present in both files', () => {
    // These are the namespaces explicitly mentioned in the project memory
    // as requiring sync verification.
    const critical = [
      'ollama',
      'voice',
      'setup',
      'localConversation',
      'settings',
      'memory',
      'mcp',
      'voiceState',
      'voiceFallback',
    ];

    const preloadSet = new Set(preloadNamespaces);
    const typesSet = new Set(typesNamespaces);

    for (const ns of critical) {
      expect(preloadSet.has(ns), `Critical namespace "${ns}" missing from preload.ts`).toBe(true);
      expect(typesSet.has(ns), `Critical namespace "${ns}" missing from types.d.ts`).toBe(true);
    }
  });

  it('types.d.ts file contains the Window interface with eve property', () => {
    expect(typesSource).toContain('interface Window');
    expect(typesSource).toContain('eve: {');
  });

  it('preload.ts uses contextBridge.exposeInMainWorld', () => {
    expect(preloadSource).toContain("contextBridge.exposeInMainWorld('eve'");
  });
});
