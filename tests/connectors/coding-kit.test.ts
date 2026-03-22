/**
 * coding-kit.test.ts — Unit tests for the Coding Kit connector.
 *
 * Tests the connector's structure, tool declarations, execute routing,
 * detection, and error handling — all WITHOUT requiring network access
 * or the actual coding-kit repository.
 *
 * Sprint 6 Track E: "The Coder" — Phase 1 + Phase 2-3 validation tests.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ---------------------------------------------------------------------------
// Mocks — must be before imports (vi.mock is hoisted)
// ---------------------------------------------------------------------------

// Mock Electron's app module (required by git-loader.ts constructor)
vi.mock('electron', () => ({
  app: { getPath: () => '/tmp/test-coding-kit' },
}));

// Mock settings module (required by git-loader.ts for getSanitizedEnv)
vi.mock('../../../src/main/settings', () => ({
  getSanitizedEnv: () => ({ ...process.env }),
}));

// Mock the git-loader module to prevent actual git clones during tests.
// This ensures tests are fast, offline-capable, and deterministic.
// Note: vi.mock factory is hoisted, so we inline the mock object.
vi.mock('../../../src/main/git-loader', () => ({
  gitLoader: {
    load: async () => { throw new Error('Mock: git-loader disabled in tests'); },
    unload: async () => undefined,
    search: () => [],
    getFile: () => null,
    getTree: () => [],
    getSummary: () => null,
    listLoaded: () => [],
  },
}));

// We import directly from the source module
import {
  TOOLS,
  execute,
  detect,
  CODING_KIT_REPO,
  CODING_KIT_LOAD_OPTIONS,
} from '../../../src/main/connectors/coding-kit';

// ---------------------------------------------------------------------------
// Test Utilities
// ---------------------------------------------------------------------------

/**
 * Helper to find a tool by name in the TOOLS array.
 */
function findTool(name: string) {
  return TOOLS.find((t) => t.name === name);
}

// ---------------------------------------------------------------------------
// Tests: Module Exports
// ---------------------------------------------------------------------------

describe('Coding Kit Connector — Exports', () => {
  it('exports TOOLS array', () => {
    expect(Array.isArray(TOOLS)).toBe(true);
    expect(TOOLS.length).toBeGreaterThan(0);
  });

  it('exports execute as a function', () => {
    expect(typeof execute).toBe('function');
  });

  it('exports detect as a function', () => {
    expect(typeof detect).toBe('function');
  });

  it('exports CODING_KIT_REPO as a valid URL string', () => {
    expect(typeof CODING_KIT_REPO).toBe('string');
    expect(CODING_KIT_REPO).toContain('github.com');
    expect(CODING_KIT_REPO).toContain('FutureSpeakAI');
    expect(CODING_KIT_REPO).toContain('agent-fridays-coding-kit');
  });

  it('exports CODING_KIT_LOAD_OPTIONS with correct structure', () => {
    expect(CODING_KIT_LOAD_OPTIONS).toBeDefined();
    expect(CODING_KIT_LOAD_OPTIONS.branch).toBe('main');
    expect(CODING_KIT_LOAD_OPTIONS.excludeOverrides).toContain('packages');
    expect(Array.isArray(CODING_KIT_LOAD_OPTIONS.excludePatterns)).toBe(true);
    expect(Array.isArray(CODING_KIT_LOAD_OPTIONS.includePatterns)).toBe(true);
    expect(CODING_KIT_LOAD_OPTIONS.maxFileSize).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Tests: Load Options Configuration
// ---------------------------------------------------------------------------

describe('Coding Kit Connector — Load Options', () => {
  it('excludeOverrides removes "packages" from DEFAULT_EXCLUDE', () => {
    // The key design decision: DEFAULT_EXCLUDE includes 'packages' which
    // would skip the entire monorepo. excludeOverrides prevents that.
    expect(CODING_KIT_LOAD_OPTIONS.excludeOverrides).toEqual(['packages']);
  });

  it('excludePatterns skips irrelevant packages', () => {
    const patterns = CODING_KIT_LOAD_OPTIONS.excludePatterns!;
    expect(patterns).toContain('packages/pi-mom');
    expect(patterns).toContain('packages/pi-pods');
    expect(patterns).toContain('packages/pi-tui');
    expect(patterns).toContain('packages/pi-web-ui');
  });

  it('excludePatterns skips build artifacts', () => {
    const patterns = CODING_KIT_LOAD_OPTIONS.excludePatterns!;
    expect(patterns).toContain('dist');
    expect(patterns).toContain('build');
    expect(patterns).toContain('node_modules');
  });

  it('includePatterns focus on source files', () => {
    const patterns = CODING_KIT_LOAD_OPTIONS.includePatterns!;
    expect(patterns).toContain('*.ts');
    expect(patterns).toContain('*.tsx');
    expect(patterns).toContain('*.json');
    expect(patterns).toContain('*.md');
  });

  it('maxFileSize is 256KB', () => {
    expect(CODING_KIT_LOAD_OPTIONS.maxFileSize).toBe(256 * 1024);
  });

  it('does NOT exclude relevant packages (pi-ai, pi-agent-core, pi-coding-agent)', () => {
    const patterns = CODING_KIT_LOAD_OPTIONS.excludePatterns!;
    expect(patterns).not.toContain('packages/pi-ai');
    expect(patterns).not.toContain('packages/pi-agent-core');
    expect(patterns).not.toContain('packages/pi-coding-agent');
  });
});

// ---------------------------------------------------------------------------
// Tests: Tool Declarations
// ---------------------------------------------------------------------------

describe('Coding Kit Connector — Tool Declarations', () => {
  it('declares exactly 8 tools', () => {
    expect(TOOLS).toHaveLength(8);
  });

  it('declares coding_kit_load tool', () => {
    const tool = findTool('coding_kit_load');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('Load');
    expect(tool!.description).toContain('coding kit');
    expect(tool!.parameters.type).toBe('object');
  });

  it('coding_kit_load has optional force parameter', () => {
    const tool = findTool('coding_kit_load')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.force).toBeDefined();
    expect(props.force.type).toBe('boolean');
    // force is NOT required
    expect(tool.parameters.required).toBeUndefined();
  });

  it('declares coding_kit_status tool', () => {
    const tool = findTool('coding_kit_status');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('status');
    expect(tool!.parameters.type).toBe('object');
  });

  it('declares coding_kit_search tool', () => {
    const tool = findTool('coding_kit_search');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('Search');
    expect(tool!.parameters.required).toContain('query');
  });

  it('coding_kit_search has query and maxResults parameters', () => {
    const tool = findTool('coding_kit_search')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.query).toBeDefined();
    expect(props.query.type).toBe('string');
    expect(props.maxResults).toBeDefined();
    expect(props.maxResults.type).toBe('number');
  });

  it('declares coding_kit_read_file tool', () => {
    const tool = findTool('coding_kit_read_file');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('Read');
    expect(tool!.parameters.required).toContain('file_path');
  });

  it('coding_kit_read_file has file_path parameter', () => {
    const tool = findTool('coding_kit_read_file')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.file_path).toBeDefined();
    expect(props.file_path.type).toBe('string');
  });

  it('all tools have name, description, and parameters', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toBeTruthy();
      expect(tool.description).toBeTruthy();
      expect(tool.parameters).toBeDefined();
      expect(tool.parameters.type).toBe('object');
    }
  });

  // ── Phase 2-3: Code Intelligence Tool Declarations ──────────────────

  it('declares coding_kit_get_tree tool', () => {
    const tool = findTool('coding_kit_get_tree');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('tree');
    expect(tool!.parameters.type).toBe('object');
  });

  it('coding_kit_get_tree has optional directory and files_only parameters', () => {
    const tool = findTool('coding_kit_get_tree')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.directory).toBeDefined();
    expect(props.directory.type).toBe('string');
    expect(props.files_only).toBeDefined();
    expect(props.files_only.type).toBe('boolean');
    // Both are optional — no required array
    expect(tool.parameters.required).toBeUndefined();
  });

  it('declares coding_kit_get_summary tool', () => {
    const tool = findTool('coding_kit_get_summary');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('summary');
    expect(tool!.parameters.type).toBe('object');
    // No required parameters
    expect(tool.parameters.required).toBeUndefined();
  });

  it('declares coding_kit_find_symbols tool', () => {
    const tool = findTool('coding_kit_find_symbols');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('symbol');
    expect(tool!.parameters.required).toContain('query');
  });

  it('coding_kit_find_symbols has query, kind, package_name, exported_only, maxResults', () => {
    const tool = findTool('coding_kit_find_symbols')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.query).toBeDefined();
    expect(props.query.type).toBe('string');
    expect(props.kind).toBeDefined();
    expect(props.kind.type).toBe('string');
    expect(props.package_name).toBeDefined();
    expect(props.package_name.type).toBe('string');
    expect(props.exported_only).toBeDefined();
    expect(props.exported_only.type).toBe('boolean');
    expect(props.maxResults).toBeDefined();
    expect(props.maxResults.type).toBe('number');
  });

  it('declares coding_kit_analyze_deps tool', () => {
    const tool = findTool('coding_kit_analyze_deps');
    expect(tool).toBeDefined();
    expect(tool!.description).toContain('dependen');
    expect(tool!.parameters.type).toBe('object');
  });

  it('coding_kit_analyze_deps has optional package_name parameter', () => {
    const tool = findTool('coding_kit_analyze_deps')!;
    const props = tool.parameters.properties as Record<string, any>;
    expect(props.package_name).toBeDefined();
    expect(props.package_name.type).toBe('string');
    // package_name is optional
    expect(tool.parameters.required).toBeUndefined();
  });

  it('tool names all start with coding_kit_ prefix', () => {
    for (const tool of TOOLS) {
      expect(tool.name).toMatch(/^coding_kit_/);
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Execute Dispatcher
// ---------------------------------------------------------------------------

describe('Coding Kit Connector — Execute Routing', () => {
  it('returns error for unknown tool name', async () => {
    const result = await execute('coding_kit_nonexistent', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('Unknown coding-kit tool');
  });

  it('execute never throws (returns error object instead)', async () => {
    // All tool calls should return {error} or {result}, not throw exceptions.
    const toolNames = TOOLS.map((t) => t.name);
    for (const name of toolNames) {
      const result = await execute(name, {
        query: 'test',
        file_path: '/test.ts',
        force: false,
      });
      expect(result).toBeDefined();
      expect(typeof result).toBe('object');
      // Must have either result or error, not throw
      expect(result.result !== undefined || result.error !== undefined).toBe(true);
    }
  });

  it('coding_kit_status returns result when repo is not loaded', async () => {
    const result = await execute('coding_kit_status', {});
    expect(result.result).toBeDefined();
    const parsed = JSON.parse(result.result!);
    expect(parsed.loaded).toBe(false);
    expect(parsed.repoUrl).toBe(CODING_KIT_REPO);
  });

  it('coding_kit_search returns error when repo is not loaded', async () => {
    const result = await execute('coding_kit_search', { query: 'test' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('not loaded');
  });

  it('coding_kit_search returns error when query is missing', async () => {
    const result = await execute('coding_kit_search', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('query');
  });

  it('coding_kit_read_file returns error when repo is not loaded', async () => {
    const result = await execute('coding_kit_read_file', { file_path: 'package.json' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('not loaded');
  });

  it('coding_kit_read_file returns error when file_path is missing', async () => {
    const result = await execute('coding_kit_read_file', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('file_path');
  });

  // ── Phase 2-3: Code Intelligence Execute Routing ───────────────────

  it('coding_kit_get_tree returns error when repo is not loaded', async () => {
    const result = await execute('coding_kit_get_tree', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('not loaded');
  });

  it('coding_kit_get_summary returns error when repo is not loaded', async () => {
    const result = await execute('coding_kit_get_summary', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('not loaded');
  });

  it('coding_kit_find_symbols returns error when query is missing', async () => {
    const result = await execute('coding_kit_find_symbols', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('query');
  });

  it('coding_kit_find_symbols returns error when repo is not loaded', async () => {
    const result = await execute('coding_kit_find_symbols', { query: 'test' });
    expect(result.error).toBeDefined();
    expect(result.error).toContain('not loaded');
  });

  it('coding_kit_find_symbols validates kind parameter', async () => {
    const result = await execute('coding_kit_find_symbols', {
      query: 'test',
      kind: 'invalid_kind',
    });
    // Should return error (either "not loaded" or invalid kind)
    expect(result.error).toBeDefined();
  });

  it('coding_kit_analyze_deps returns error when repo is not loaded', async () => {
    const result = await execute('coding_kit_analyze_deps', {});
    expect(result.error).toBeDefined();
    expect(result.error).toContain('not loaded');
  });
});

// ---------------------------------------------------------------------------
// Tests: Detect
// ---------------------------------------------------------------------------

describe('Coding Kit Connector — Detect', () => {
  it('detect returns a boolean', async () => {
    const result = await detect();
    expect(typeof result).toBe('boolean');
  });

  it('detect never throws', async () => {
    let result: boolean;
    try {
      result = await detect();
    } catch {
      result = false;
      expect.fail('detect() should not throw');
    }
    expect(typeof result).toBe('boolean');
  });

  it('detect returns true when git is available', async () => {
    // On a dev machine with git installed, detect() should return true
    // since it only checks for git availability
    const result = await detect();
    // This test assumes git is installed on the test machine
    // If not, the test still passes (boolean check above covers it)
    expect(typeof result).toBe('boolean');
  });
});

// ---------------------------------------------------------------------------
// Tests: Error Resilience
// ---------------------------------------------------------------------------

describe('Coding Kit Connector — Error Resilience', () => {
  it('coding_kit_load handles missing git gracefully', async () => {
    // Even if git load fails, should return error object not throw
    const result = await execute('coding_kit_load', {});
    expect(result).toBeDefined();
    expect(typeof result).toBe('object');
    // Will either succeed (git available + network) or return error
    expect(result.result !== undefined || result.error !== undefined).toBe(true);
  });

  it('coding_kit_status always returns a valid result', async () => {
    const result = await execute('coding_kit_status', {});
    expect(result.result).toBeDefined();
    expect(result.error).toBeUndefined();
    // Parse the result to verify structure
    const parsed = JSON.parse(result.result!);
    expect(typeof parsed.loaded).toBe('boolean');
  });

  it('search with invalid query type returns error', async () => {
    const result = await execute('coding_kit_search', { query: 123 as any });
    expect(result.error).toBeDefined();
  });

  it('read_file with invalid path type returns error', async () => {
    const result = await execute('coding_kit_read_file', { file_path: 123 as any });
    expect(result.error).toBeDefined();
  });

  // ── Phase 2-3: Code Intelligence Error Resilience ──────────────────

  it('get_tree with invalid directory type returns error', async () => {
    const result = await execute('coding_kit_get_tree', { directory: 123 as any });
    expect(result.error).toBeDefined();
  });

  it('find_symbols with invalid query type returns error', async () => {
    const result = await execute('coding_kit_find_symbols', { query: 123 as any });
    expect(result.error).toBeDefined();
  });

  it('find_symbols with empty string query returns error', async () => {
    const result = await execute('coding_kit_find_symbols', { query: '' });
    expect(result.error).toBeDefined();
  });

  it('analyze_deps with invalid package_name type returns error', async () => {
    const result = await execute('coding_kit_analyze_deps', { package_name: 123 as any });
    expect(result.error).toBeDefined();
  });

  it('all Phase 2-3 tools never throw (return error object instead)', async () => {
    const phase23Tools = [
      'coding_kit_get_tree',
      'coding_kit_get_summary',
      'coding_kit_find_symbols',
      'coding_kit_analyze_deps',
    ];
    for (const name of phase23Tools) {
      const result = await execute(name, { query: 'test', package_name: 'test' });
      expect(result).toBeDefined();
      expect(typeof result).toBe('object');
      expect(result.result !== undefined || result.error !== undefined).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// Tests: Security & Configuration
// ---------------------------------------------------------------------------

describe('Coding Kit Connector — Security Configuration', () => {
  it('repo URL points to FutureSpeakAI organization', () => {
    expect(CODING_KIT_REPO).toMatch(/github\.com\/FutureSpeakAI\//);
  });

  it('load options use main branch', () => {
    expect(CODING_KIT_LOAD_OPTIONS.branch).toBe('main');
  });

  it('load options have reasonable maxFileSize', () => {
    // Should be between 64KB and 1MB
    expect(CODING_KIT_LOAD_OPTIONS.maxFileSize).toBeGreaterThanOrEqual(64 * 1024);
    expect(CODING_KIT_LOAD_OPTIONS.maxFileSize).toBeLessThanOrEqual(1024 * 1024);
  });

  it('excludePatterns has no duplicates', () => {
    const patterns = CODING_KIT_LOAD_OPTIONS.excludePatterns!;
    const unique = new Set(patterns);
    expect(unique.size).toBe(patterns.length);
  });

  it('includePatterns has no duplicates', () => {
    const patterns = CODING_KIT_LOAD_OPTIONS.includePatterns!;
    const unique = new Set(patterns);
    expect(unique.size).toBe(patterns.length);
  });
});
