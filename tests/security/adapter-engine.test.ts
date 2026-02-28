/**
 * adapter-engine.test.ts — Tests for the Superpower Adaptation Engine.
 *
 * Track II, Phase 2: The Absorber — Adaptation Engine.
 *
 * Covers:
 *   1. Strategy Selection (language/repo-type → strategy mapping)
 *   2. Adaptation Planning (manifest → plan with per-cap adaptations)
 *   3. Dependency Resolution (conflict detection, isolation decisions)
 *   4. Connector Generation (source code generation for each strategy)
 *   5. Sandbox Configuration (permission derivation from capabilities)
 *   6. Validation (adapted connector structural checks)
 *   7. Serialization (round-trip persistence)
 *   8. Bridge Generation (Python JSONL, generic bridges)
 *   9. SuperpowerSandbox Manager (lifecycle, execution, violations)
 *  10. Pattern Detection (eval blocking, path/URL detection)
 *  11. cLaw Gate Safety (sandbox boundaries, superpower tagging)
 */

import { describe, it, expect, beforeEach } from 'vitest';

import {
  selectStrategy,
  createAdaptationPlan,
  resolveDependencies,
  generateConnector,
  validateAdaptedConnector,
  summarizeAdaptationPlan,
  serializeConnector,
  deserializeConnector,
  type AdaptationPlan,
  type AdaptedConnector,
} from '../../src/main/adapter-engine';

import {
  SuperpowerSandboxManager,
  containsEvalPattern,
  looksLikePath,
  looksLikeUrl,
  type SandboxInstance,
} from '../../src/main/superpower-sandbox';

import type { CapabilityManifest, Capability, Dependency } from '../../src/main/capability-manifest';
import type { LoadedRepo } from '../../src/main/git-loader';

// ── Test Helpers ────────────────────────────────────────────────────

function makeCapability(overrides: Partial<Capability> = {}): Capability {
  return {
    id: 'cap-1',
    name: 'process_data',
    description: 'Process input data and return results',
    category: 'data-processing',
    inputSchema: {
      type: 'object',
      properties: {
        input: { type: 'string', description: 'Input data to process' },
      },
      required: ['input'],
    },
    outputSchema: { type: 'string', description: 'Processed result' },
    source: {
      filePath: 'src/index.ts',
      startLine: 10,
      endLine: 50,
      exportedName: 'processData',
      isDefaultExport: false,
    },
    language: 'typescript',
    confidence: 0.9,
    confidenceSignals: [],
    adaptationComplexity: 'simple',
    adaptationNotes: 'Clean function export',
    ...overrides,
  };
}

function makeDependency(overrides: Partial<Dependency> = {}): Dependency {
  return {
    name: 'lodash',
    version: '4.17.21',
    scope: 'runtime',
    ecosystem: 'npm',
    alreadyPresent: false,
    ...overrides,
  };
}

function makeManifest(overrides: Partial<CapabilityManifest> = {}): CapabilityManifest {
  return {
    id: 'manifest-1',
    repoId: 'repo-1',
    repoName: 'test-lib',
    analyzedAt: Date.now(),
    analysisDurationMs: 1000,
    summary: 'A test library for processing data',
    primaryLanguage: 'TypeScript',
    languages: ['TypeScript'],
    repoType: 'library',
    ecosystem: 'npm',
    capabilities: [makeCapability()],
    entryPoints: [{
      filePath: 'src/index.ts',
      reason: 'package-main',
      exports: [{ name: 'processData', kind: 'function', isCapability: true }],
      confidence: 0.95,
    }],
    dependencies: [makeDependency()],
    configSchema: null,
    confidence: { overall: 0.85, signals: [], explanation: 'Good analysis' },
    metadata: {
      filesAnalyzed: 10,
      filesSkipped: 2,
      linesAnalyzed: 500,
      claudeCalls: 1,
      hasTests: true,
      hasDocumentation: true,
      hasTypes: true,
      license: 'MIT',
      readmeExcerpt: 'A test library',
    },
    ...overrides,
  };
}

function makeRepo(): LoadedRepo {
  return {
    id: 'repo-1',
    name: 'test-lib',
    owner: 'test-user',
    branch: 'main',
    description: 'A test library',
    url: 'https://github.com/test-user/test-lib',
    localPath: '/tmp/repos/test-lib',
    files: [
      { path: 'src/index.ts', content: 'export function processData(input: string) { return input; }', language: 'TypeScript', size: 60 },
      { path: 'package.json', content: '{"name":"test-lib","main":"src/index.ts"}', language: 'JSON', size: 40 },
    ],
    tree: [
      { path: 'src/index.ts', type: 'blob', size: 60 },
      { path: 'package.json', type: 'blob', size: 40 },
    ],
    loadedAt: Date.now(),
    totalSize: 100,
  };
}

// ════════════════════════════════════════════════════════════════════
// 1. Strategy Selection
// ════════════════════════════════════════════════════════════════════

describe('Adaptation Engine — Strategy Selection', () => {
  it('should select direct-import for TypeScript library with clean exports', () => {
    const manifest = makeManifest({ primaryLanguage: 'TypeScript', repoType: 'library' });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('direct-import');
    expect(strategy.estimatedComplexity).toBe('trivial'); // has types
  });

  it('should select direct-import for JavaScript with function exports', () => {
    const manifest = makeManifest({
      primaryLanguage: 'JavaScript',
      repoType: 'library',
      metadata: { ...makeManifest().metadata, hasTypes: false },
    });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('direct-import');
    expect(strategy.estimatedComplexity).toBe('simple'); // no types
  });

  it('should select subprocess-bridge for Python repos', () => {
    const manifest = makeManifest({
      primaryLanguage: 'Python',
      repoType: 'library',
      entryPoints: [],
    });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('subprocess-bridge');
    expect(strategy.prerequisites).toContain('python3 available on PATH');
  });

  it('should select api-wrap for API server repos', () => {
    const manifest = makeManifest({ repoType: 'api-server' });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('api-wrap');
    expect(strategy.estimatedComplexity).toBe('moderate');
  });

  it('should select claude-rewrite for JS without clean exports', () => {
    const manifest = makeManifest({
      primaryLanguage: 'JavaScript',
      repoType: 'standalone',
      entryPoints: [{ filePath: 'index.js', reason: 'index-file', exports: [], confidence: 0.5 }],
    });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('claude-rewrite');
  });

  it('should select subprocess-bridge for Go repos', () => {
    const manifest = makeManifest({ primaryLanguage: 'Go', entryPoints: [] });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('subprocess-bridge');
  });

  it('should select subprocess-bridge for Rust repos', () => {
    const manifest = makeManifest({ primaryLanguage: 'Rust', entryPoints: [] });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('subprocess-bridge');
  });

  it('should select subprocess-bridge for Ruby repos', () => {
    const manifest = makeManifest({ primaryLanguage: 'Ruby', entryPoints: [] });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('subprocess-bridge');
  });

  it('should handle unknown languages gracefully', () => {
    const manifest = makeManifest({ primaryLanguage: 'Haskell', entryPoints: [] });
    const strategy = selectStrategy(manifest);
    expect(strategy.type).toBe('subprocess-bridge');
  });
});

// ════════════════════════════════════════════════════════════════════
// 2. Adaptation Planning
// ════════════════════════════════════════════════════════════════════

describe('Adaptation Engine — Planning', () => {
  it('should create a complete plan from a manifest', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);

    expect(plan.manifestId).toBe(manifest.id);
    expect(plan.repoId).toBe(manifest.repoId);
    expect(plan.repoName).toBe(manifest.repoName);
    expect(plan.strategy.type).toBe('direct-import');
    expect(plan.capabilities).toHaveLength(1);
    expect(plan.createdAt).toBeGreaterThan(0);
  });

  it('should generate tool names with repo prefix', () => {
    const manifest = makeManifest({ repoName: 'my-cool-lib' });
    const plan = createAdaptationPlan(manifest);
    expect(plan.capabilities[0].toolName).toBe('my_cool_lib_process_data');
  });

  it('should skip infeasible capabilities', () => {
    const manifest = makeManifest({
      capabilities: [
        makeCapability({ adaptationComplexity: 'infeasible', adaptationNotes: 'Requires GPU' }),
      ],
    });
    const plan = createAdaptationPlan(manifest);
    expect(plan.capabilities[0].skipped).toBe(true);
    expect(plan.capabilities[0].skipReason).toContain('infeasible');
  });

  it('should estimate duration based on complexity', () => {
    const manifest = makeManifest({
      capabilities: [
        makeCapability({ id: 'cap-1', adaptationComplexity: 'simple' }),
        makeCapability({ id: 'cap-2', name: 'other_fn', adaptationComplexity: 'moderate' }),
      ],
    });
    const plan = createAdaptationPlan(manifest);
    expect(plan.estimatedDurationMs).toBeGreaterThan(0);
  });

  it('should build correct invocation spec for direct-import', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const invocation = plan.capabilities[0].invocation;

    expect(invocation.type).toBe('function-call');
    expect(invocation.importPath).toBe('src/index.ts');
    expect(invocation.functionName).toBe('processData');
  });

  it('should build correct invocation spec for subprocess-bridge', () => {
    const manifest = makeManifest({
      primaryLanguage: 'Python',
      entryPoints: [],
      capabilities: [makeCapability({
        language: 'python',
        source: { filePath: 'src/main.py', startLine: 1, endLine: 20, exportedName: 'process', isDefaultExport: false },
      })],
    });
    const plan = createAdaptationPlan(manifest);
    const invocation = plan.capabilities[0].invocation;

    expect(invocation.type).toBe('subprocess');
    expect(invocation.command).toBe('python3');
  });

  it('should handle multiple capabilities in plan', () => {
    const manifest = makeManifest({
      capabilities: [
        makeCapability({ id: 'cap-1', name: 'fn_one' }),
        makeCapability({ id: 'cap-2', name: 'fn_two' }),
        makeCapability({ id: 'cap-3', name: 'fn_three' }),
      ],
    });
    const plan = createAdaptationPlan(manifest);
    expect(plan.capabilities).toHaveLength(3);
    const toolNames = plan.capabilities.map(c => c.toolName);
    expect(toolNames).toContain('test_lib_fn_one');
    expect(toolNames).toContain('test_lib_fn_two');
    expect(toolNames).toContain('test_lib_fn_three');
  });
});

// ════════════════════════════════════════════════════════════════════
// 3. Dependency Resolution
// ════════════════════════════════════════════════════════════════════

describe('Adaptation Engine — Dependency Resolution', () => {
  it('should identify new dependencies', () => {
    const deps: Dependency[] = [
      makeDependency({ name: 'lodash', alreadyPresent: false }),
      makeDependency({ name: 'axios', alreadyPresent: false }),
    ];
    const resolution = resolveDependencies(deps);
    expect(resolution.newDeps).toHaveLength(2);
    expect(resolution.newDeps[0].name).toBe('lodash');
  });

  it('should mark already-present deps as compatible', () => {
    const deps: Dependency[] = [
      makeDependency({ name: 'lodash', alreadyPresent: true }),
    ];
    const resolution = resolveDependencies(deps);
    expect(resolution.conflicts).toHaveLength(1);
    expect(resolution.conflicts[0].resolution).toBe('compatible');
  });

  it('should isolate when more than 3 new deps', () => {
    const deps: Dependency[] = Array.from({ length: 5 }, (_, i) =>
      makeDependency({ name: `dep-${i}`, alreadyPresent: false }),
    );
    const resolution = resolveDependencies(deps);
    expect(resolution.isolated).toBe(true);
    expect(resolution.isolationReason).toContain('isolated');
  });

  it('should not isolate when few compatible deps', () => {
    const deps: Dependency[] = [
      makeDependency({ name: 'lodash', alreadyPresent: true }),
    ];
    const resolution = resolveDependencies(deps);
    expect(resolution.isolated).toBe(false);
  });

  it('should skip dev dependencies', () => {
    const deps: Dependency[] = [
      makeDependency({ name: 'jest', scope: 'dev' }),
      makeDependency({ name: 'lodash', scope: 'runtime' }),
    ];
    const resolution = resolveDependencies(deps);
    expect(resolution.newDeps).toHaveLength(1);
    expect(resolution.newDeps[0].name).toBe('lodash');
  });

  it('should handle empty dependencies', () => {
    const resolution = resolveDependencies([]);
    expect(resolution.newDeps).toHaveLength(0);
    expect(resolution.conflicts).toHaveLength(0);
    expect(resolution.isolated).toBe(false);
  });
});

// ════════════════════════════════════════════════════════════════════
// 4. Connector Generation
// ════════════════════════════════════════════════════════════════════

describe('Adaptation Engine — Connector Generation', () => {
  it('should generate a valid connector for direct-import', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.id).toBe('sp-test_lib');
    expect(connector.id.startsWith('sp-')).toBe(true);
    expect(connector.tools).toHaveLength(1);
    expect(connector.tools[0].name).toBe('test_lib_process_data');
    expect(connector.sourceCode).toContain('direct-import');
    expect(connector.sourceCode).toContain('module.exports');
  });

  it('should generate connector with subprocess bridge for Python', () => {
    const manifest = makeManifest({
      primaryLanguage: 'Python',
      entryPoints: [],
      capabilities: [makeCapability({
        language: 'python',
        source: { filePath: 'src/main.py', startLine: 1, endLine: 20, exportedName: 'process', isDefaultExport: false },
      })],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sourceCode).toContain('subprocess-bridge');
    expect(connector.sourceCode).toContain('JSONL');
    expect(connector.bridgeScript).toBeDefined();
    expect(connector.bridgeScript).toContain('python3');
    expect(connector.bridgeScript).toContain('json.loads');
  });

  it('should generate connector with API wrapper for server repos', () => {
    const manifest = makeManifest({ repoType: 'api-server' });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sourceCode).toContain('api-wrap');
    expect(connector.sourceCode).toContain('apiCall');
    expect(connector.sourceCode).toContain('http');
  });

  it('should throw when no adaptable capabilities exist', () => {
    const manifest = makeManifest({
      capabilities: [makeCapability({ adaptationComplexity: 'infeasible' })],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();

    expect(() => generateConnector(plan, manifest, repo))
      .toThrow('No adaptable capabilities');
  });

  it('should include sandbox configuration', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sandbox).toBeDefined();
    expect(connector.sandbox.maxExecutionTimeMs).toBeGreaterThan(0);
    expect(typeof connector.sandbox.allowNetwork).toBe('boolean');
  });

  it('should include the plan in the connector', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.plan).toBe(plan);
    expect(connector.generatedAt).toBeGreaterThan(0);
  });
});

// ════════════════════════════════════════════════════════════════════
// 5. Sandbox Configuration Derivation
// ════════════════════════════════════════════════════════════════════

describe('Adaptation Engine — Sandbox Config', () => {
  it('should enable network for network capabilities', () => {
    const manifest = makeManifest({
      capabilities: [makeCapability({ category: 'network' })],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sandbox.allowNetwork).toBe(true);
  });

  it('should enable filesystem for file-operations capabilities', () => {
    const manifest = makeManifest({
      capabilities: [makeCapability({ category: 'file-operations' })],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sandbox.allowFileSystem).toBe(true);
    expect(connector.sandbox.allowedPaths).toContain('.');
  });

  it('should block network by default', () => {
    const manifest = makeManifest({
      capabilities: [makeCapability({ category: 'data-processing' })],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sandbox.allowNetwork).toBe(false);
  });

  it('should enable child processes for subprocess-bridge', () => {
    const manifest = makeManifest({
      primaryLanguage: 'Python',
      entryPoints: [],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sandbox.allowChildProcesses).toBe(true);
  });

  it('should increase timeout for complex capabilities', () => {
    const manifest = makeManifest({
      capabilities: [makeCapability({ adaptationComplexity: 'complex' })],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sandbox.maxExecutionTimeMs).toBe(60_000);
  });
});

// ════════════════════════════════════════════════════════════════════
// 6. Validation
// ════════════════════════════════════════════════════════════════════

describe('Adaptation Engine — Validation', () => {
  it('should validate a well-formed connector', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);
    const result = validateAdaptedConnector(connector);

    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  it('should reject connector without sp- prefix', () => {
    const connector = {
      id: 'bad-id',
      label: 'Test',
      description: 'Test',
      category: 'foundation' as const,
      tools: [{ name: 'test_tool', description: 'Test', parameters: { type: 'object', properties: {} } }],
      sourceCode: 'code',
      dependencies: [],
      sandbox: { allowNetwork: false, allowFileSystem: false, allowedPaths: [], maxExecutionTimeMs: 30000, maxMemoryMb: 256, allowChildProcesses: false },
      plan: {} as AdaptationPlan,
      generatedAt: Date.now(),
    };
    const result = validateAdaptedConnector(connector);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.includes('sp-'))).toBe(true);
  });

  it('should reject connector with no tools', () => {
    const connector = {
      id: 'sp-test',
      label: 'Test',
      description: 'Test',
      category: 'foundation' as const,
      tools: [],
      sourceCode: 'code',
      dependencies: [],
      sandbox: { allowNetwork: false, allowFileSystem: false, allowedPaths: [], maxExecutionTimeMs: 30000, maxMemoryMb: 256, allowChildProcesses: false },
      plan: {} as AdaptationPlan,
      generatedAt: Date.now(),
    };
    const result = validateAdaptedConnector(connector);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.includes('No tools'))).toBe(true);
  });

  it('should reject tools with invalid names', () => {
    const connector = {
      id: 'sp-test',
      label: 'Test',
      description: 'Test',
      category: 'foundation' as const,
      tools: [{ name: 'INVALID NAME!', description: 'Test', parameters: { type: 'object', properties: {} } }],
      sourceCode: 'code',
      dependencies: [],
      sandbox: { allowNetwork: false, allowFileSystem: false, allowedPaths: [], maxExecutionTimeMs: 30000, maxMemoryMb: 256, allowChildProcesses: false },
      plan: {} as AdaptationPlan,
      generatedAt: Date.now(),
    };
    const result = validateAdaptedConnector(connector);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.includes('invalid characters'))).toBe(true);
  });

  it('should reject connector without source code', () => {
    const connector = {
      id: 'sp-test',
      label: 'Test',
      description: 'Test',
      category: 'foundation' as const,
      tools: [{ name: 'test_tool', description: 'Test', parameters: { type: 'object', properties: {} } }],
      sourceCode: '',
      dependencies: [],
      sandbox: { allowNetwork: false, allowFileSystem: false, allowedPaths: [], maxExecutionTimeMs: 30000, maxMemoryMb: 256, allowChildProcesses: false },
      plan: {} as AdaptationPlan,
      generatedAt: Date.now(),
    };
    const result = validateAdaptedConnector(connector);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.includes('source code'))).toBe(true);
  });
});

// ════════════════════════════════════════════════════════════════════
// 7. Serialization
// ════════════════════════════════════════════════════════════════════

describe('Adaptation Engine — Serialization', () => {
  it('should round-trip serialize a connector', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    const json = serializeConnector(connector);
    const restored = deserializeConnector(json);

    expect(restored).not.toBeNull();
    expect(restored!.id).toBe(connector.id);
    expect(restored!.tools).toHaveLength(connector.tools.length);
    expect(restored!.sourceCode).toBe(connector.sourceCode);
  });

  it('should return null for invalid JSON', () => {
    expect(deserializeConnector('not json')).toBeNull();
  });

  it('should return null for missing required fields', () => {
    expect(deserializeConnector('{"foo": "bar"}')).toBeNull();
  });

  it('should produce valid JSON string', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    const json = serializeConnector(connector);
    expect(() => JSON.parse(json)).not.toThrow();
  });
});

// ════════════════════════════════════════════════════════════════════
// 8. Plan Summary
// ════════════════════════════════════════════════════════════════════

describe('Adaptation Engine — Plan Summary', () => {
  it('should generate readable summary', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const summary = summarizeAdaptationPlan(plan);

    expect(summary).toContain('test-lib');
    expect(summary).toContain('direct-import');
    expect(summary).toContain('Dependencies');
  });

  it('should show skipped capabilities in summary', () => {
    const manifest = makeManifest({
      capabilities: [
        makeCapability({ adaptationComplexity: 'infeasible', adaptationNotes: 'Needs GPU' }),
      ],
    });
    const plan = createAdaptationPlan(manifest);
    const summary = summarizeAdaptationPlan(plan);

    expect(summary).toContain('skipped');
    expect(summary).toContain('infeasible');
  });
});

// ════════════════════════════════════════════════════════════════════
// 9. SuperpowerSandbox Manager
// ════════════════════════════════════════════════════════════════════

describe('SuperpowerSandbox — Lifecycle', () => {
  let manager: SuperpowerSandboxManager;
  let connector: AdaptedConnector;

  beforeEach(() => {
    manager = new SuperpowerSandboxManager();
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    connector = generateConnector(plan, manifest, repo);
  });

  it('should create a sandbox for a connector', () => {
    const sandbox = manager.createSandbox(connector);
    expect(sandbox.connectorId).toBe(connector.id);
    expect(sandbox.state).toBe('idle');
    expect(sandbox.tools).toHaveLength(connector.tools.length);
  });

  it('should reject duplicate sandbox creation', () => {
    manager.createSandbox(connector);
    expect(() => manager.createSandbox(connector)).toThrow('already exists');
  });

  it('should start a sandbox', async () => {
    manager.createSandbox(connector);
    await manager.startSandbox(connector.id);
    const sandbox = manager.getSandbox(connector.id);
    expect(sandbox?.state).toBe('ready');
  });

  it('should stop a sandbox', async () => {
    manager.createSandbox(connector);
    await manager.startSandbox(connector.id);
    await manager.stopSandbox(connector.id);
    const sandbox = manager.getSandbox(connector.id);
    expect(sandbox?.state).toBe('stopped');
  });

  it('should remove a sandbox', () => {
    manager.createSandbox(connector);
    manager.removeSandbox(connector.id);
    expect(manager.getSandbox(connector.id)).toBeNull();
  });

  it('should list all sandboxes', () => {
    manager.createSandbox(connector);
    expect(manager.getAllSandboxes()).toHaveLength(1);
  });

  it('should filter sandboxes by state', async () => {
    manager.createSandbox(connector);
    await manager.startSandbox(connector.id);
    expect(manager.getSandboxesByState('ready')).toHaveLength(1);
    expect(manager.getSandboxesByState('idle')).toHaveLength(0);
  });

  it('should shutdown all sandboxes', async () => {
    manager.createSandbox(connector);
    await manager.startSandbox(connector.id);
    await manager.shutdownAll();
    expect(manager.getAllSandboxes()).toHaveLength(0);
  });
});

describe('SuperpowerSandbox — Execution', () => {
  let manager: SuperpowerSandboxManager;
  let connector: AdaptedConnector;

  beforeEach(async () => {
    manager = new SuperpowerSandboxManager();
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    connector = generateConnector(plan, manifest, repo);
    manager.createSandbox(connector);
    await manager.startSandbox(connector.id);
  });

  it('should execute a tool call', async () => {
    const result = await manager.executeTool(connector.id, 'test_lib_process_data', { input: 'hello' });
    expect(result.success).toBe(true);
    expect(result.durationMs).toBeGreaterThanOrEqual(0);
  });

  it('should reject tool calls on non-existent sandbox', async () => {
    const result = await manager.executeTool('sp-nonexistent', 'some_tool', {});
    expect(result.success).toBe(false);
    expect(result.error).toContain('No sandbox found');
  });

  it('should reject tool calls on non-ready sandbox', async () => {
    await manager.stopSandbox(connector.id);
    const result = await manager.executeTool(connector.id, 'test_lib_process_data', {});
    expect(result.success).toBe(false);
    expect(result.error).toContain('not ready');
  });

  it('should reject unknown tool names', async () => {
    const result = await manager.executeTool(connector.id, 'nonexistent_tool', {});
    expect(result.success).toBe(false);
    expect(result.error).toContain('not found');
  });

  it('should track execution count', async () => {
    await manager.executeTool(connector.id, 'test_lib_process_data', { input: 'a' });
    await manager.executeTool(connector.id, 'test_lib_process_data', { input: 'b' });
    const sandbox = manager.getSandbox(connector.id);
    expect(sandbox?.executionCount).toBe(2);
  });

  it('should update lastExecutionAt', async () => {
    const before = Date.now();
    await manager.executeTool(connector.id, 'test_lib_process_data', { input: 'a' });
    const sandbox = manager.getSandbox(connector.id);
    expect(sandbox?.lastExecutionAt).toBeGreaterThanOrEqual(before);
  });
});

describe('SuperpowerSandbox — Violations', () => {
  let manager: SuperpowerSandboxManager;
  let connector: AdaptedConnector;

  beforeEach(async () => {
    manager = new SuperpowerSandboxManager();
    // Create connector with NO network, NO filesystem
    const manifest = makeManifest({
      capabilities: [makeCapability({ category: 'data-processing' })],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    connector = generateConnector(plan, manifest, repo);
    manager.createSandbox(connector);
    await manager.startSandbox(connector.id);
  });

  it('should block eval patterns in args', async () => {
    const result = await manager.executeTool(connector.id, 'test_lib_process_data', {
      input: 'eval("malicious code")',
    });
    expect(result.success).toBe(false);
    expect(result.error).toContain('eval-like pattern');
  });

  it('should block network URLs when network is disabled', async () => {
    const result = await manager.executeTool(connector.id, 'test_lib_process_data', {
      input: 'https://evil.com/steal-data',
    });
    expect(result.success).toBe(false);
    expect(result.error).toContain('network access is disabled');
  });

  it('should block filesystem paths when FS is disabled', async () => {
    const result = await manager.executeTool(connector.id, 'test_lib_process_data', {
      input: '/etc/passwd',
    });
    expect(result.success).toBe(false);
    expect(result.error).toContain('filesystem access is disabled');
  });

  it('should record violations', async () => {
    await manager.executeTool(connector.id, 'test_lib_process_data', {
      input: 'eval("hack")',
    });
    const violations = manager.getViolations();
    expect(violations).toHaveLength(1);
    expect(violations[0].type).toBe('eval-blocked');
  });

  it('should get violations for specific connector', async () => {
    await manager.executeTool(connector.id, 'test_lib_process_data', {
      input: 'eval("x")',
    });
    const violations = manager.getViolationsForConnector(connector.id);
    expect(violations).toHaveLength(1);

    const otherViolations = manager.getViolationsForConnector('sp-other');
    expect(otherViolations).toHaveLength(0);
  });

  it('should clear violations', async () => {
    await manager.executeTool(connector.id, 'test_lib_process_data', {
      input: 'eval("x")',
    });
    manager.clearViolations();
    expect(manager.getViolations()).toHaveLength(0);
  });
});

describe('SuperpowerSandbox — Status', () => {
  it('should report aggregate status', async () => {
    const manager = new SuperpowerSandboxManager();
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    manager.createSandbox(connector);
    await manager.startSandbox(connector.id);

    const status = manager.getStatus();
    expect(status.totalSandboxes).toBe(1);
    expect(status.readySandboxes).toBe(1);
    expect(status.errorSandboxes).toBe(0);
    expect(status.totalExecutions).toBe(0);
  });

  it('should identify superpowers', () => {
    const manager = new SuperpowerSandboxManager();
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);
    manager.createSandbox(connector);

    expect(manager.isSuperpower(connector.id)).toBe(true);
    expect(manager.isSuperpower('sp-anything')).toBe(true);
    expect(manager.isSuperpower('builtin-calculator')).toBe(false);
  });
});

// ════════════════════════════════════════════════════════════════════
// 10. Pattern Detection
// ════════════════════════════════════════════════════════════════════

describe('Pattern Detection — Eval Patterns', () => {
  it('should detect eval()', () => {
    expect(containsEvalPattern('eval("code")')).toBe(true);
  });

  it('should detect new Function()', () => {
    expect(containsEvalPattern('new Function("return 1")')).toBe(true);
  });

  it('should detect setTimeout with string', () => {
    expect(containsEvalPattern('setTimeout("alert(1)", 0)')).toBe(true);
  });

  it('should detect Python exec()', () => {
    expect(containsEvalPattern('exec("import os")')).toBe(true);
  });

  it('should detect Python os.system()', () => {
    expect(containsEvalPattern('os.system("rm -rf /")')).toBe(true);
  });

  it('should detect Python subprocess', () => {
    expect(containsEvalPattern('subprocess.run(["ls"])')).toBe(true);
  });

  it('should not flag normal strings', () => {
    expect(containsEvalPattern('hello world')).toBe(false);
    expect(containsEvalPattern('processing data')).toBe(false);
    expect(containsEvalPattern('evaluate the result')).toBe(false);
  });

  it('should detect dynamic import', () => {
    expect(containsEvalPattern('import("malicious-module")')).toBe(true);
  });
});

describe('Pattern Detection — Paths', () => {
  it('should detect Unix absolute paths', () => {
    expect(looksLikePath('/etc/passwd')).toBe(true);
    expect(looksLikePath('/usr/local/bin')).toBe(true);
  });

  it('should detect Windows absolute paths', () => {
    expect(looksLikePath('C:\\Users\\test')).toBe(true);
  });

  it('should detect home directory paths', () => {
    expect(looksLikePath('~/Documents/file.txt')).toBe(true);
  });

  it('should detect directory traversal', () => {
    expect(looksLikePath('../../../etc/passwd')).toBe(true);
  });

  it('should detect relative paths', () => {
    expect(looksLikePath('./config/settings.json')).toBe(true);
  });

  it('should not flag normal strings', () => {
    expect(looksLikePath('hello world')).toBe(false);
    expect(looksLikePath('some data')).toBe(false);
  });
});

describe('Pattern Detection — URLs', () => {
  it('should detect HTTP URLs', () => {
    expect(looksLikeUrl('http://example.com')).toBe(true);
    expect(looksLikeUrl('https://api.example.com/v1')).toBe(true);
  });

  it('should detect WebSocket URLs', () => {
    expect(looksLikeUrl('ws://localhost:8080')).toBe(true);
    expect(looksLikeUrl('wss://secure.example.com')).toBe(true);
  });

  it('should detect FTP URLs', () => {
    expect(looksLikeUrl('ftp://files.example.com')).toBe(true);
  });

  it('should not flag normal strings', () => {
    expect(looksLikeUrl('hello world')).toBe(false);
    expect(looksLikeUrl('example.com')).toBe(false); // No protocol
  });
});

// ════════════════════════════════════════════════════════════════════
// 11. cLaw Gate Safety
// ════════════════════════════════════════════════════════════════════

describe('cLaw Gate — Superpower Safety Boundaries', () => {
  it('should prefix all superpower connector IDs with sp-', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);
    expect(connector.id.startsWith('sp-')).toBe(true);
  });

  it('should NEVER generate eval/require in direct-import source', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sourceCode).not.toContain('eval(');
    expect(connector.sourceCode).not.toContain('new Function(');
    // Note: require() IS used for module loading, but not for dynamic code execution
  });

  it('should have sandbox config for every generated connector', () => {
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sandbox).toBeDefined();
    expect(connector.sandbox.maxExecutionTimeMs).toBeGreaterThan(0);
    expect(connector.sandbox.maxMemoryMb).toBeGreaterThan(0);
  });

  it('should default to restrictive sandbox (no network, no fs, no child processes)', () => {
    const manifest = makeManifest({
      capabilities: [makeCapability({ category: 'computation' })],
    });
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);

    expect(connector.sandbox.allowNetwork).toBe(false);
    expect(connector.sandbox.allowFileSystem).toBe(false);
    expect(connector.sandbox.allowChildProcesses).toBe(false);
  });

  it('should sandbox manager block eval in tool arguments', async () => {
    const manager = new SuperpowerSandboxManager();
    const manifest = makeManifest();
    const plan = createAdaptationPlan(manifest);
    const repo = makeRepo();
    const connector = generateConnector(plan, manifest, repo);
    manager.createSandbox(connector);
    await manager.startSandbox(connector.id);

    const result = await manager.executeTool(connector.id, 'test_lib_process_data', {
      code: 'new Function("return process.env")()',
    });
    expect(result.success).toBe(false);
  });

  it('should distinguish superpowers from builtins', () => {
    const manager = new SuperpowerSandboxManager();
    expect(manager.isSuperpower('sp-markdown-parser')).toBe(true);
    expect(manager.isSuperpower('calculator')).toBe(false);
    expect(manager.isSuperpower('firecrawl')).toBe(false);
  });

  it('should never allow superpower execution without sandbox', async () => {
    const manager = new SuperpowerSandboxManager();
    // Try to execute without creating a sandbox first
    const result = await manager.executeTool('sp-test', 'some_tool', {});
    expect(result.success).toBe(false);
    expect(result.error).toContain('No sandbox found');
  });

  it('should categorize connectors appropriately', () => {
    // DevOps for code-gen
    const devManifest = makeManifest({
      capabilities: [makeCapability({ category: 'code-generation' })],
    });
    const devPlan = createAdaptationPlan(devManifest);
    const devConnector = generateConnector(devPlan, devManifest, makeRepo());
    expect(devConnector.category).toBe('devops');

    // Creative for image processing
    const imgManifest = makeManifest({
      capabilities: [makeCapability({ category: 'image-processing' })],
    });
    const imgPlan = createAdaptationPlan(imgManifest);
    const imgConnector = generateConnector(imgPlan, imgManifest, makeRepo());
    expect(imgConnector.category).toBe('creative');
  });
});
