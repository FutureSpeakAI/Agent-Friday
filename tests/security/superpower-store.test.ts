/**
 * superpower-store.test.ts — Tests for the Superpower Registry & Store.
 *
 * Track II, Phase 3: The Absorber — Superpower Registry.
 *
 * Covers:
 *   1. Store Initialization & Lifecycle
 *   2. Installation Flow (prepare → consent → install)
 *   3. Enable/Disable Toggle
 *   4. Uninstallation (clean removal)
 *   5. Updates (version bumps, preserved state)
 *   6. Usage & Error Tracking
 *   7. Auto-Disable on Errors
 *   8. Queries (by category, by tool, enabled tools)
 *   9. Import/Export
 *  10. Prompt Context Generation
 *  11. cLaw Gate Safety (consent boundary enforcement)
 */

import { describe, it, expect, beforeEach, afterAll } from 'vitest';
import { randomUUID } from 'crypto';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';

import {
  SuperpowerStore,
  type Superpower,
  type SecurityVerdictSummary,
} from '../../src/main/superpower-store';

import {
  createAdaptationPlan,
  generateConnector,
  type AdaptedConnector,
} from '../../src/main/adapter-engine';

import type { CapabilityManifest, Capability, Dependency } from '../../src/main/capability-manifest';
import type { LoadedRepo } from '../../src/main/git-loader';

// ── Isolated temp directory per test run ─────────────────────────────
const TEST_ROOT = path.join(os.tmpdir(), `sp-store-test-${randomUUID()}`);
let testCounter = 0;

/** Return a unique data dir for each test to prevent cross-test leakage. */
function uniqueDataDir(): string {
  return path.join(TEST_ROOT, `t${++testCounter}`);
}

afterAll(async () => {
  try { await fs.rm(TEST_ROOT, { recursive: true, force: true }); } catch { /* best-effort cleanup */ }
});

// ── Test Helpers ────────────────────────────────────────────────────

function makeCapability(overrides: Partial<Capability> = {}): Capability {
  return {
    id: 'cap-1',
    name: 'process_data',
    description: 'Process input data',
    category: 'data-processing',
    inputSchema: { type: 'object', properties: { input: { type: 'string', description: 'data' } }, required: ['input'] },
    outputSchema: { type: 'string', description: 'result' },
    source: { filePath: 'src/index.ts', startLine: 1, endLine: 20, exportedName: 'processData', isDefaultExport: false },
    language: 'typescript',
    confidence: 0.9,
    confidenceSignals: [],
    adaptationComplexity: 'simple',
    adaptationNotes: '',
    ...overrides,
  };
}

function makeManifest(overrides: Partial<CapabilityManifest> = {}): CapabilityManifest {
  return {
    id: 'manifest-1',
    repoId: 'repo-1',
    repoName: 'test-lib',
    analyzedAt: Date.now(),
    analysisDurationMs: 500,
    summary: 'A test library',
    primaryLanguage: 'TypeScript',
    languages: ['TypeScript'],
    repoType: 'library',
    ecosystem: 'npm',
    capabilities: [makeCapability()],
    entryPoints: [{ filePath: 'src/index.ts', reason: 'package-main', exports: [{ name: 'processData', kind: 'function', isCapability: true }], confidence: 0.9 }],
    dependencies: [{ name: 'lodash', version: '4.17.21', scope: 'runtime', ecosystem: 'npm' }],
    configSchema: null,
    confidence: { overall: 0.85, signals: [], explanation: 'Good' },
    metadata: { filesAnalyzed: 5, filesSkipped: 0, linesAnalyzed: 200, claudeCalls: 1, hasTests: true, hasDocumentation: true, hasTypes: true, license: 'MIT', readmeExcerpt: null },
    ...overrides,
  };
}

function makeRepo(): LoadedRepo {
  return {
    id: 'repo-1', name: 'test-lib', owner: 'user', branch: 'main',
    description: '', url: 'https://github.com/user/test-lib', localPath: '/tmp/test-lib',
    files: [{ path: 'src/index.ts', content: 'export function processData() {}', language: 'TypeScript', size: 30 }],
    tree: [{ path: 'src/index.ts', type: 'blob', size: 30 }],
    loadedAt: Date.now(), totalSize: 30,
  };
}

function makeConnector(repoName = 'test-lib'): AdaptedConnector {
  const manifest = makeManifest({ repoName });
  const plan = createAdaptationPlan(manifest);
  return generateConnector(plan, manifest, makeRepo());
}

function makeVerdict(approved = true): SecurityVerdictSummary {
  return {
    approved,
    riskLevel: approved ? 'low' : 'critical',
    summary: approved ? 'Approved for installation' : 'Rejected — high risk',
    reviewedAt: Date.now(),
  };
}

// ════════════════════════════════════════════════════════════════════
// 1. Store Initialization
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Initialization', () => {
  it('should initialize with empty store', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    expect(store.getAll()).toHaveLength(0);
  });

  it('should report correct initial status', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const status = store.getStatus();
    expect(status.totalInstalled).toBe(0);
    expect(status.totalEnabled).toBe(0);
    expect(status.totalTools).toBe(0);
  });
});

// ════════════════════════════════════════════════════════════════════
// 2. Installation Flow
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Installation', () => {
  let store: SuperpowerStore;

  beforeEach(async () => {
    store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
  });

  it('should prepare a superpower for installation', () => {
    const connector = makeConnector();
    const sp = store.prepareInstall(connector, makeVerdict(), 'https://github.com/user/test-lib', 'abc123');

    expect(sp.id).toBe('sp-test_lib');
    expect(sp.status).toBe('pending-consent');
    expect(sp.enabled).toBe(false);
    expect(sp.consentToken).toBe('');
  });

  it('should confirm installation with consent token', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'https://github.com/user/test-lib', 'abc123');

    const sp = store.confirmInstall('sp-test_lib', 'user-consent-token-xyz');

    expect(sp.status).toBe('installed');
    expect(sp.enabled).toBe(true);
    expect(sp.consentToken).toBe('user-consent-token-xyz');
    expect(sp.installedAt).toBeGreaterThan(0);
  });

  it('should reject duplicate installations', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    expect(() => store.prepareInstall(connector, makeVerdict(), 'url', 'abc'))
      .toThrow('already installed');
  });

  it('should reject unapproved security verdict', () => {
    const connector = makeConnector();
    expect(() => store.prepareInstall(connector, makeVerdict(false), 'url', 'abc'))
      .toThrow('Security verdict rejected');
  });

  it('should enforce superpower limit', async () => {
    const limitedStore = new SuperpowerStore({ maxSuperpowers: 2 });
    await limitedStore.initialize(uniqueDataDir());

    const c1 = makeConnector('lib-one');
    const c2 = makeConnector('lib-two');
    const c3 = makeConnector('lib-three');

    limitedStore.prepareInstall(c1, makeVerdict(), 'url', 'abc');
    limitedStore.confirmInstall(c1.id, 'token1');
    limitedStore.prepareInstall(c2, makeVerdict(), 'url', 'abc');
    limitedStore.confirmInstall(c2.id, 'token2');

    expect(() => limitedStore.prepareInstall(c3, makeVerdict(), 'url', 'abc'))
      .toThrow('limit reached');
  });

  it('should store tools from the connector', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    const sp = store.get('sp-test_lib');
    expect(sp?.tools).toHaveLength(connector.tools.length);
    expect(sp?.tools[0].name).toBe(connector.tools[0].name);
  });

  it('should store sandbox config', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    const sp = store.get('sp-test_lib');
    expect(sp?.sandbox).toBeDefined();
    expect(sp?.sandbox.maxExecutionTimeMs).toBeGreaterThan(0);
  });
});

// ════════════════════════════════════════════════════════════════════
// 3. Enable/Disable
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Enable/Disable', () => {
  let store: SuperpowerStore;

  beforeEach(async () => {
    store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
  });

  it('should disable a superpower', () => {
    const sp = store.disableSuperpower('sp-test_lib');
    expect(sp.enabled).toBe(false);
    expect(store.getEnabled()).toHaveLength(0);
  });

  it('should re-enable a superpower', () => {
    store.disableSuperpower('sp-test_lib');
    const sp = store.enableSuperpower('sp-test_lib');
    expect(sp.enabled).toBe(true);
    expect(store.getEnabled()).toHaveLength(1);
  });

  it('should remove tools from palette when disabled', () => {
    store.disableSuperpower('sp-test_lib');
    expect(store.getEnabledTools()).toHaveLength(0);
  });

  it('should restore tools when re-enabled', () => {
    store.disableSuperpower('sp-test_lib');
    store.enableSuperpower('sp-test_lib');
    expect(store.getEnabledTools().length).toBeGreaterThan(0);
  });

  it('should reject enable on non-installed superpower', () => {
    store.disableSuperpower('sp-test_lib');
    // Manually set status to something else
    const sp = store.get('sp-test_lib')!;
    (sp as any).status = 'error';
    expect(() => store.enableSuperpower('sp-test_lib')).toThrow('Cannot enable');
  });
});

// ════════════════════════════════════════════════════════════════════
// 4. Uninstallation
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Uninstall', () => {
  let store: SuperpowerStore;

  beforeEach(async () => {
    store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
  });

  it('should remove superpower completely', () => {
    store.uninstallSuperpower('sp-test_lib');
    expect(store.get('sp-test_lib')).toBeNull();
    expect(store.getAll()).toHaveLength(0);
  });

  it('should remove tools on uninstall', () => {
    store.uninstallSuperpower('sp-test_lib');
    expect(store.getEnabledTools()).toHaveLength(0);
  });

  it('should be safe to uninstall non-existent superpower', () => {
    expect(() => store.uninstallSuperpower('sp-nonexistent')).not.toThrow();
  });

  it('should allow reinstall after uninstall', () => {
    store.uninstallSuperpower('sp-test_lib');
    const connector = makeConnector();
    const sp = store.prepareInstall(connector, makeVerdict(), 'url', 'def456');
    expect(sp.status).toBe('pending-consent');
  });
});

// ════════════════════════════════════════════════════════════════════
// 5. Updates
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Updates', () => {
  let store: SuperpowerStore;

  beforeEach(async () => {
    store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
  });

  it('should update a superpower', () => {
    const newConnector = makeConnector();
    const sp = store.updateSuperpower('sp-test_lib', newConnector, makeVerdict(), 'def456');

    expect(sp.version).toBe(2);
    expect(sp.sourceCommit).toBe('def456');
    expect(sp.updatedAt).toBeGreaterThan(0);
  });

  it('should preserve consent token on update', () => {
    const newConnector = makeConnector();
    store.updateSuperpower('sp-test_lib', newConnector, makeVerdict(), 'def456');
    const sp = store.get('sp-test_lib');
    expect(sp?.consentToken).toBe('token');
  });

  it('should preserve usage count on update', () => {
    store.recordUsage('sp-test_lib');
    store.recordUsage('sp-test_lib');
    const newConnector = makeConnector();
    store.updateSuperpower('sp-test_lib', newConnector, makeVerdict(), 'def456');
    const sp = store.get('sp-test_lib');
    expect(sp?.usageCount).toBe(2);
  });

  it('should reset health on update', () => {
    store.recordError('sp-test_lib', 'some error');
    const newConnector = makeConnector();
    store.updateSuperpower('sp-test_lib', newConnector, makeVerdict(), 'def456');
    const sp = store.get('sp-test_lib');
    expect(sp?.health.errorCount).toBe(0);
    expect(sp?.health.score).toBe(1.0);
  });

  it('should increment version on each update', () => {
    const c1 = makeConnector();
    store.updateSuperpower('sp-test_lib', c1, makeVerdict(), 'v2');
    const c2 = makeConnector();
    store.updateSuperpower('sp-test_lib', c2, makeVerdict(), 'v3');
    const sp = store.get('sp-test_lib');
    expect(sp?.version).toBe(3);
  });
});

// ════════════════════════════════════════════════════════════════════
// 6. Usage & Error Tracking
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Usage Tracking', () => {
  let store: SuperpowerStore;

  beforeEach(async () => {
    store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
  });

  it('should track usage count', () => {
    store.recordUsage('sp-test_lib');
    store.recordUsage('sp-test_lib');
    store.recordUsage('sp-test_lib');
    expect(store.get('sp-test_lib')?.usageCount).toBe(3);
  });

  it('should track last used time', () => {
    const before = Date.now();
    store.recordUsage('sp-test_lib');
    expect(store.get('sp-test_lib')?.lastUsedAt).toBeGreaterThanOrEqual(before);
  });

  it('should track errors', () => {
    store.recordError('sp-test_lib', 'timeout');
    const sp = store.get('sp-test_lib');
    expect(sp?.health.errorCount).toBe(1);
    expect(sp?.health.lastError).toBe('timeout');
  });

  it('should degrade health score on errors', () => {
    store.recordError('sp-test_lib', 'err1');
    store.recordError('sp-test_lib', 'err2');
    const sp = store.get('sp-test_lib');
    expect(sp?.health.score).toBeLessThan(1.0);
  });

  it('should reset errors', () => {
    store.recordError('sp-test_lib', 'err');
    store.resetErrors('sp-test_lib');
    const sp = store.get('sp-test_lib');
    expect(sp?.health.errorCount).toBe(0);
  });
});

// ════════════════════════════════════════════════════════════════════
// 7. Auto-Disable on Errors
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Auto-Disable', () => {
  it('should auto-disable after N consecutive errors', async () => {
    const store = new SuperpowerStore({ autoDisableAfterErrors: 3 });
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    store.recordError('sp-test_lib', 'err1');
    store.recordError('sp-test_lib', 'err2');
    expect(store.get('sp-test_lib')?.enabled).toBe(true);

    store.recordError('sp-test_lib', 'err3');
    expect(store.get('sp-test_lib')?.enabled).toBe(false);
    expect(store.get('sp-test_lib')?.health.warnings.length).toBeGreaterThan(0);
  });
});

// ════════════════════════════════════════════════════════════════════
// 8. Queries
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Queries', () => {
  let store: SuperpowerStore;

  beforeEach(async () => {
    store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
  });

  it('should find superpower by tool name', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    const found = store.findSuperpowerByTool('test_lib_process_data');
    expect(found).not.toBeNull();
    expect(found?.id).toBe('sp-test_lib');
  });

  it('should return null for unknown tool', () => {
    expect(store.findSuperpowerByTool('unknown_tool')).toBeNull();
  });

  it('should get enabled tools only', () => {
    const c1 = makeConnector('lib-one');
    const c2 = makeConnector('lib-two');
    store.prepareInstall(c1, makeVerdict(), 'url', 'abc');
    store.confirmInstall(c1.id, 'token1');
    store.prepareInstall(c2, makeVerdict(), 'url', 'abc');
    store.confirmInstall(c2.id, 'token2');
    store.disableSuperpower(c2.id);

    const tools = store.getEnabledTools();
    expect(tools.every(t => t.name.startsWith('lib_one'))).toBe(true);
  });

  it('should get superpowers needing attention', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
    store.recordError('sp-test_lib', 'some error');

    const attention = store.getNeedingAttention();
    expect(attention).toHaveLength(1);
  });

  it('should report correct status', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    const status = store.getStatus();
    expect(status.totalInstalled).toBe(1);
    expect(status.totalEnabled).toBe(1);
    expect(status.totalTools).toBeGreaterThan(0);
  });
});

// ════════════════════════════════════════════════════════════════════
// 9. Import/Export
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Import/Export', () => {
  it('should export all superpowers', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    const exported = store.exportAll();
    const parsed = JSON.parse(exported);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].id).toBe('sp-test_lib');
  });

  it('should import superpowers with consent tokens', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
    const exported = store.exportAll();

    const newStore = new SuperpowerStore();
    await newStore.initialize(uniqueDataDir());
    const result = newStore.importAll(exported);
    expect(result.imported).toBe(1);
    expect(result.skipped).toBe(0);
  });

  it('should skip imports without consent token', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());

    const data = JSON.stringify([{ id: 'sp-bad', name: 'Bad', consentToken: '' }]);
    const result = store.importAll(data);
    expect(result.imported).toBe(0);
    expect(result.skipped).toBe(1);
  });

  it('should skip duplicate imports', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    const data = JSON.stringify([{ id: 'sp-test_lib', consentToken: 'other' }]);
    const result = store.importAll(data);
    expect(result.skipped).toBe(1);
  });

  it('should handle invalid import data', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const result = store.importAll('not json');
    expect(result.errors.length).toBeGreaterThan(0);
  });
});

// ════════════════════════════════════════════════════════════════════
// 10. Prompt Context
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerStore — Prompt Context', () => {
  it('should return empty string with no superpowers', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    expect(store.getPromptContext()).toBe('');
  });

  it('should include enabled superpowers in context', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');

    const context = store.getPromptContext();
    expect(context).toContain('SUPERPOWERS');
    expect(context).toContain('test-lib');
  });

  it('should exclude disabled superpowers from context', async () => {
    const store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
    store.disableSuperpower('sp-test_lib');

    expect(store.getPromptContext()).toBe('');
  });
});

// ════════════════════════════════════════════════════════════════════
// 11. cLaw Gate Safety
// ════════════════════════════════════════════════════════════════════

describe('cLaw Gate — Consent Boundary Enforcement', () => {
  let store: SuperpowerStore;

  beforeEach(async () => {
    store = new SuperpowerStore();
    await store.initialize(uniqueDataDir());
  });

  it('should NEVER install without consent token', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    expect(() => store.confirmInstall('sp-test_lib', '')).toThrow('consent token is required');
  });

  it('should NEVER install with whitespace-only consent token', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    expect(() => store.confirmInstall('sp-test_lib', '   ')).toThrow('consent token is required');
  });

  it('should require pending-consent status for confirm', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
    // Already installed — can't confirm again
    expect(() => store.confirmInstall('sp-test_lib', 'token2'))
      .toThrow('not pending consent');
  });

  it('should reject unapproved security verdicts by default', () => {
    const connector = makeConnector();
    expect(() => store.prepareInstall(connector, makeVerdict(false), 'url', 'abc'))
      .toThrow('Security verdict rejected');
  });

  it('should allow unsigned when config permits', async () => {
    const permissiveStore = new SuperpowerStore({ allowUnsigned: true });
    await permissiveStore.initialize('/tmp/test');
    const connector = makeConnector();
    expect(() => permissiveStore.prepareInstall(connector, makeVerdict(false), 'url', 'abc'))
      .not.toThrow();
  });

  it('should all superpower IDs start with sp-', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'token');
    const all = store.getAll();
    for (const sp of all) {
      expect(sp.id.startsWith('sp-')).toBe(true);
    }
  });

  it('should preserve consent token immutably through updates', () => {
    const connector = makeConnector();
    store.prepareInstall(connector, makeVerdict(), 'url', 'abc');
    store.confirmInstall('sp-test_lib', 'original-consent');

    const newConnector = makeConnector();
    store.updateSuperpower('sp-test_lib', newConnector, makeVerdict(), 'newcommit');

    const sp = store.get('sp-test_lib');
    expect(sp?.consentToken).toBe('original-consent');
  });

  it('should import skip superpowers without consent', async () => {
    const data = JSON.stringify([
      { id: 'sp-no-consent', name: 'No Consent' },
      { id: 'sp-has-consent', name: 'Has Consent', consentToken: 'valid-token' },
    ]);
    const result = store.importAll(data);
    expect(result.imported).toBe(1);
    expect(result.skipped).toBe(1);
  });

  it('should validate connector before prepare', () => {
    // Connector with empty ID should fail validation
    const connector = makeConnector();
    (connector as any).id = '';
    expect(() => store.prepareInstall(connector, makeVerdict(), 'url', 'abc'))
      .toThrow('Invalid connector');
  });
});
