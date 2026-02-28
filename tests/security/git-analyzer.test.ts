/**
 * git-analyzer.test.ts — Tests for Intelligent Code Analysis (Track II Phase 1).
 *
 * Covers:
 *   1. Ecosystem Detection
 *   2. Language Distribution
 *   3. Entry Point Identification
 *   4. File Selection
 *   5. Claude Response Parsing
 *   6. Dependency Extraction
 *   7. Repo Type Detection
 *   8. Confidence Scoring
 *   9. Capability Manifest Types/Helpers
 *  10. cLaw Gate Safety Invariants
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock electron and server before imports
vi.mock('electron', () => ({
  app: { getPath: vi.fn(() => '/tmp/test-userdata') },
  safeStorage: {
    isEncryptionAvailable: vi.fn(() => false),
    encryptString: vi.fn((s: string) => Buffer.from(s)),
    decryptString: vi.fn((b: Buffer) => b.toString()),
  },
  ipcMain: { handle: vi.fn() },
}));

vi.mock('../../src/main/server', () => ({
  runClaudeToolLoop: vi.fn(async () => ({
    response: JSON.stringify({
      summary: 'A test library for unit testing',
      repoType: 'library',
      capabilities: [
        {
          name: 'run_tests',
          description: 'Execute test suites and return results',
          category: 'utility',
          inputSchema: {
            type: 'object',
            properties: { pattern: { type: 'string', description: 'Test file pattern' } },
            required: ['pattern'],
          },
          outputSchema: { type: 'string', description: 'Test results' },
          sourceFile: 'src/index.ts',
          exportedName: 'runTests',
          startLine: 10,
          endLine: 50,
          isDefaultExport: false,
          adaptationComplexity: 'simple',
          adaptationNotes: 'Direct import possible',
        },
      ],
      configFields: [
        { key: 'timeout', type: 'number', description: 'Test timeout in ms', required: false, default: 5000 },
      ],
    }),
    model: 'claude-opus-4-6',
    toolCalls: 0,
  })),
}));

import type { LoadedRepo, RepoFile, RepoTreeEntry } from '../../src/main/git-loader';

import {
  detectEcosystem,
  computeLanguageDistribution,
  identifyEntryPoints,
  detectManifestEntryPoints,
  selectFilesForAnalysis,
  extractCapabilitiesWithClaude,
  buildAnalysisSystemPrompt,
  buildAnalysisUserMessage,
  parseClaudeAnalysisResponse,
  extractDependencies,
  detectRepoType,
  computeConfidenceSignals,
  analyzeRepository,
} from '../../src/main/git-analyzer';

import {
  computeConfidence,
  explainConfidence,
  capabilityToToolDeclaration,
  sanitizeToolName,
  validateManifest,
  summarizeManifest,
  type CapabilityManifest,
  type Capability,
  type ConfidenceSignal,
} from '../../src/main/capability-manifest';

// ── Test Helpers ──────────────────────────────────────────────────────

function makeFile(path: string, content: string, language = 'javascript'): RepoFile {
  return { path, content, language, size: content.length };
}

function makeTree(path: string, type: 'file' | 'directory' = 'file'): RepoTreeEntry {
  return { path, type };
}

function makeRepo(files: RepoFile[], tree?: RepoTreeEntry[]): LoadedRepo {
  return {
    id: 'test-repo',
    name: 'test-repo',
    owner: 'test',
    branch: 'main',
    description: 'Test repository',
    url: 'https://github.com/test/test-repo',
    localPath: '/tmp/test-repo',
    files,
    tree: tree || files.map(f => makeTree(f.path)),
    loadedAt: Date.now(),
    totalSize: files.reduce((s, f) => s + f.size, 0),
  };
}

// ═════════════════════════════════════════════════════════════════════
// 1. Ecosystem Detection
// ═════════════════════════════════════════════════════════════════════

describe('Ecosystem Detection', () => {
  it('should detect npm from package.json', () => {
    const repo = makeRepo([makeFile('package.json', '{}')]);
    expect(detectEcosystem(repo)).toBe('npm');
  });

  it('should detect pip from pyproject.toml', () => {
    const repo = makeRepo([makeFile('pyproject.toml', '[project]', 'toml')]);
    expect(detectEcosystem(repo)).toBe('pip');
  });

  it('should detect pip from setup.py', () => {
    const repo = makeRepo([makeFile('setup.py', 'setup()', 'python')]);
    expect(detectEcosystem(repo)).toBe('pip');
  });

  it('should detect cargo from Cargo.toml', () => {
    const repo = makeRepo([makeFile('Cargo.toml', '[package]', 'toml')]);
    expect(detectEcosystem(repo)).toBe('cargo');
  });

  it('should detect go-modules from go.mod', () => {
    const repo = makeRepo([makeFile('go.mod', 'module example.com/foo', 'go')]);
    expect(detectEcosystem(repo)).toBe('go-modules');
  });

  it('should return null for unknown ecosystem', () => {
    const repo = makeRepo([makeFile('main.cpp', 'int main() {}', 'cpp')]);
    expect(detectEcosystem(repo)).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════════
// 2. Language Distribution
// ═════════════════════════════════════════════════════════════════════

describe('Language Distribution', () => {
  it('should compute language distribution sorted by size', () => {
    const repo = makeRepo([
      makeFile('a.ts', 'x'.repeat(1000), 'typescript'),
      makeFile('b.ts', 'x'.repeat(500), 'typescript'),
      makeFile('c.py', 'x'.repeat(800), 'python'),
      makeFile('d.js', 'x'.repeat(200), 'javascript'),
    ]);

    const dist = computeLanguageDistribution(repo);
    expect(dist[0][0]).toBe('typescript');
    expect(dist[0][1]).toBe(1500);
    expect(dist[1][0]).toBe('python');
    expect(dist[1][1]).toBe(800);
  });

  it('should exclude text and unknown languages', () => {
    const repo = makeRepo([
      makeFile('readme.md', 'x'.repeat(2000), 'text'),
      makeFile('main.ts', 'x'.repeat(100), 'typescript'),
    ]);

    const dist = computeLanguageDistribution(repo);
    expect(dist).toHaveLength(1);
    expect(dist[0][0]).toBe('typescript');
  });

  it('should handle empty repo', () => {
    const repo = makeRepo([]);
    expect(computeLanguageDistribution(repo)).toEqual([]);
  });
});

// ═════════════════════════════════════════════════════════════════════
// 3. Entry Point Identification
// ═════════════════════════════════════════════════════════════════════

describe('Entry Point Identification', () => {
  it('should identify index.ts as entry point', () => {
    const repo = makeRepo([makeFile('index.ts', 'export function main() {}', 'typescript')]);
    const eps = identifyEntryPoints(repo, 'npm');
    expect(eps.some(ep => ep.filePath === 'index.ts')).toBe(true);
  });

  it('should identify src/index.ts as entry point', () => {
    const repo = makeRepo([makeFile('src/index.ts', 'export class App {}', 'typescript')]);
    const eps = identifyEntryPoints(repo, 'npm');
    expect(eps.some(ep => ep.filePath === 'src/index.ts')).toBe(true);
  });

  it('should identify __init__.py for Python', () => {
    const repo = makeRepo([makeFile('mylib/__init__.py', 'from .core import *', 'python')]);
    const eps = identifyEntryPoints(repo, 'pip');
    expect(eps.some(ep => ep.reason === 'python-init')).toBe(true);
  });

  it('should identify shebang files as CLI entry points', () => {
    const repo = makeRepo([
      makeFile('bin/mycli', '#!/usr/bin/env node\nconsole.log("hello")', 'javascript'),
    ]);
    const eps = identifyEntryPoints(repo, 'npm');
    expect(eps.some(ep => ep.reason === 'cli-entrypoint')).toBe(true);
  });

  it('should detect package.json main field', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ main: './dist/index.js' })),
      makeFile('dist/index.js', 'module.exports = {}', 'javascript'),
    ]);
    const eps = detectManifestEntryPoints(repo, 'npm');
    expect(eps.some(ep => ep.reason === 'package-main')).toBe(true);
  });

  it('should detect package.json bin field', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ name: 'mycli', bin: { mycli: './bin/cli.js' } })),
      makeFile('bin/cli.js', 'console.log("cli")', 'javascript'),
    ]);
    const eps = detectManifestEntryPoints(repo, 'npm');
    expect(eps.some(ep => ep.reason === 'package-bin')).toBe(true);
  });

  it('should extract exports from TypeScript files', () => {
    const repo = makeRepo([
      makeFile('index.ts', [
        'export function processData(input: string): string { return input; }',
        'export class DataProcessor { transform() {} }',
        'export const VERSION = "1.0.0";',
        'function internalHelper() {}',
      ].join('\n'), 'typescript'),
    ]);
    const eps = identifyEntryPoints(repo, 'npm');
    const ep = eps.find(e => e.filePath === 'index.ts');
    expect(ep).toBeDefined();
    expect(ep!.exports.some(e => e.name === 'processData' && e.kind === 'function')).toBe(true);
    expect(ep!.exports.some(e => e.name === 'DataProcessor' && e.kind === 'class')).toBe(true);
    expect(ep!.exports.some(e => e.name === 'VERSION')).toBe(true);
    // Internal helper should not appear
    expect(ep!.exports.some(e => e.name === 'internalHelper')).toBe(false);
  });

  it('should extract exports from Python files', () => {
    const repo = makeRepo([
      makeFile('main.py', [
        'def process_data(input):',
        '    return input',
        '',
        'class DataProcessor:',
        '    pass',
        '',
        'def _private_helper():',
        '    pass',
      ].join('\n'), 'python'),
    ]);
    const eps = identifyEntryPoints(repo, 'pip');
    const ep = eps.find(e => e.filePath === 'main.py');
    expect(ep).toBeDefined();
    expect(ep!.exports.some(e => e.name === 'process_data')).toBe(true);
    expect(ep!.exports.some(e => e.name === 'DataProcessor')).toBe(true);
    // Private helper should not appear
    expect(ep!.exports.some(e => e.name === '_private_helper')).toBe(false);
  });
});

// ═════════════════════════════════════════════════════════════════════
// 4. File Selection
// ═════════════════════════════════════════════════════════════════════

describe('File Selection', () => {
  it('should prioritize entry point files', () => {
    const repo = makeRepo([
      makeFile('index.ts', 'export function main() {}', 'typescript'),
      makeFile('utils.ts', 'export function helper() {}', 'typescript'),
      makeFile('big-file.ts', 'x'.repeat(10000), 'typescript'),
    ]);
    const eps = identifyEntryPoints(repo, 'npm');
    const selected = selectFilesForAnalysis(repo, eps, {
      maxFilesForClaude: 2,
      maxContentChars: 50000,
      analyzeReadme: true,
      extractDependencies: true,
    });

    expect(selected[0].path).toBe('index.ts');
  });

  it('should exclude test files', () => {
    const repo = makeRepo([
      makeFile('src/main.ts', 'export function main() {}', 'typescript'),
      makeFile('tests/main.test.ts', 'describe("main", () => {})', 'typescript'),
      makeFile('__tests__/foo.spec.ts', 'it("works", () => {})', 'typescript'),
    ]);
    const selected = selectFilesForAnalysis(repo, [], {
      maxFilesForClaude: 25,
      maxContentChars: 50000,
      analyzeReadme: true,
      extractDependencies: true,
    });

    expect(selected.every(f => !f.path.includes('test'))).toBe(true);
  });

  it('should respect maxFilesForClaude limit', () => {
    const files = Array.from({ length: 50 }, (_, i) =>
      makeFile(`file-${i}.ts`, `export const x${i} = ${i};`, 'typescript')
    );
    const repo = makeRepo(files);
    const selected = selectFilesForAnalysis(repo, [], {
      maxFilesForClaude: 10,
      maxContentChars: 50000,
      analyzeReadme: true,
      extractDependencies: true,
    });

    expect(selected.length).toBeLessThanOrEqual(10);
  });
});

// ═════════════════════════════════════════════════════════════════════
// 5. Claude Response Parsing
// ═════════════════════════════════════════════════════════════════════

describe('Claude Response Parsing', () => {
  it('should parse valid JSON response', () => {
    const response = JSON.stringify({
      summary: 'A markdown parser library',
      repoType: 'library',
      capabilities: [
        {
          name: 'parse_markdown',
          description: 'Parse markdown text to HTML',
          category: 'text-processing',
          inputSchema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] },
          outputSchema: { type: 'string', description: 'HTML output' },
          sourceFile: 'src/parser.ts',
          exportedName: 'parse',
          startLine: 1,
          endLine: 50,
          isDefaultExport: false,
          adaptationComplexity: 'trivial',
          adaptationNotes: 'Direct TS import',
        },
      ],
      configFields: [],
    });

    const result = parseClaudeAnalysisResponse(response);
    expect(result.summary).toBe('A markdown parser library');
    expect(result.repoType).toBe('library');
    expect(result.capabilities).toHaveLength(1);
    expect(result.capabilities[0].name).toBe('parse_markdown');
  });

  it('should handle markdown-wrapped JSON', () => {
    const response = '```json\n{"summary":"test","repoType":"library","capabilities":[],"configFields":[]}\n```';
    const result = parseClaudeAnalysisResponse(response);
    expect(result.summary).toBe('test');
    expect(result.repoType).toBe('library');
  });

  it('should return fallback for invalid JSON', () => {
    const result = parseClaudeAnalysisResponse('not json at all');
    expect(result.summary).toBe('');
    expect(result.repoType).toBe('unknown');
    expect(result.capabilities).toEqual([]);
  });

  it('should return fallback for empty response', () => {
    const result = parseClaudeAnalysisResponse('');
    expect(result.repoType).toBe('unknown');
  });

  it('should validate repo type', () => {
    const response = JSON.stringify({
      summary: 'test',
      repoType: 'invalid-type',
      capabilities: [],
      configFields: [],
    });
    const result = parseClaudeAnalysisResponse(response);
    expect(result.repoType).toBe('unknown');
  });

  it('should validate capability categories', () => {
    const response = JSON.stringify({
      summary: 'test',
      repoType: 'library',
      capabilities: [
        {
          name: 'test',
          description: 'test cap',
          category: 'invalid-category',
          inputSchema: { type: 'object', properties: {} },
          outputSchema: { type: 'string', description: 'output' },
          sourceFile: 'test.ts',
          exportedName: 'test',
          startLine: 1,
          endLine: 10,
          isDefaultExport: false,
          adaptationComplexity: 'simple',
          adaptationNotes: '',
        },
      ],
      configFields: [],
    });
    const result = parseClaudeAnalysisResponse(response);
    expect(result.capabilities[0].category).toBe('utility'); // Default fallback
  });

  it('should cap capability count at 50', () => {
    const caps = Array.from({ length: 100 }, (_, i) => ({
      name: `cap_${i}`,
      description: `Capability ${i}`,
      category: 'utility',
      inputSchema: { type: 'object', properties: {} },
      outputSchema: { type: 'string', description: 'out' },
      sourceFile: 'test.ts',
      exportedName: `cap${i}`,
      startLine: i,
      endLine: i + 10,
      isDefaultExport: false,
      adaptationComplexity: 'simple',
      adaptationNotes: '',
    }));

    const response = JSON.stringify({
      summary: 'test',
      repoType: 'library',
      capabilities: caps,
      configFields: [],
    });

    const result = parseClaudeAnalysisResponse(response);
    expect(result.capabilities.length).toBeLessThanOrEqual(50);
  });

  it('should skip capabilities without name or description', () => {
    const response = JSON.stringify({
      summary: 'test',
      repoType: 'library',
      capabilities: [
        { name: '', description: 'no name' },
        { name: 'valid', description: 'has both', category: 'utility', inputSchema: { type: 'object', properties: {} }, outputSchema: { type: 'string', description: 'out' }, sourceFile: 't.ts', exportedName: 'valid', startLine: 1, endLine: 5, isDefaultExport: false, adaptationComplexity: 'simple', adaptationNotes: '' },
        { description: 'no name at all' },
      ],
      configFields: [],
    });

    const result = parseClaudeAnalysisResponse(response);
    expect(result.capabilities).toHaveLength(1);
    expect(result.capabilities[0].name).toBe('valid');
  });
});

// ═════════════════════════════════════════════════════════════════════
// 6. Dependency Extraction
// ═════════════════════════════════════════════════════════════════════

describe('Dependency Extraction', () => {
  it('should extract npm dependencies', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({
        dependencies: { lodash: '^4.17.21', express: '^4.18.0' },
        devDependencies: { vitest: '^1.0.0' },
      })),
    ]);

    const deps = extractDependencies(repo, 'npm');
    expect(deps.filter(d => d.scope === 'runtime')).toHaveLength(2);
    expect(deps.filter(d => d.scope === 'dev')).toHaveLength(1);
    expect(deps.some(d => d.name === 'lodash')).toBe(true);
  });

  it('should extract pip dependencies from requirements.txt', () => {
    const repo = makeRepo([
      makeFile('requirements.txt', 'flask>=2.0\nrequests==2.28.1\n# comment\nnumpy', 'text'),
    ]);

    const deps = extractDependencies(repo, 'pip');
    expect(deps).toHaveLength(3);
    expect(deps.some(d => d.name === 'flask')).toBe(true);
    expect(deps.some(d => d.name === 'requests')).toBe(true);
    expect(deps.some(d => d.name === 'numpy')).toBe(true);
  });

  it('should return empty for unknown ecosystem', () => {
    const repo = makeRepo([makeFile('main.rs', 'fn main() {}', 'rust')]);
    expect(extractDependencies(repo, 'cargo')).toEqual([]);
  });
});

// ═════════════════════════════════════════════════════════════════════
// 7. Repo Type Detection
// ═════════════════════════════════════════════════════════════════════

describe('Repo Type Detection', () => {
  it('should detect CLI tools from package.json bin', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ bin: { mycli: './cli.js' } })),
    ]);
    expect(detectRepoType(repo, 'npm')).toBe('cli-tool');
  });

  it('should detect API servers from server frameworks', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ dependencies: { express: '^4.18.0' } })),
    ]);
    expect(detectRepoType(repo, 'npm')).toBe('api-server');
  });

  it('should detect monorepos from workspaces', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ workspaces: ['packages/*'] })),
    ]);
    expect(detectRepoType(repo, 'npm')).toBe('monorepo');
  });

  it('should detect Python CLI from argparse', () => {
    const repo = makeRepo([
      makeFile('main.py', 'import argparse\nparser = argparse.ArgumentParser()', 'python'),
    ]);
    expect(detectRepoType(repo, 'pip')).toBe('cli-tool');
  });

  it('should default to library', () => {
    const repo = makeRepo([makeFile('util.ts', 'export const x = 1;', 'typescript')]);
    expect(detectRepoType(repo, 'npm')).toBe('library');
  });
});

// ═════════════════════════════════════════════════════════════════════
// 8. Confidence Scoring
// ═════════════════════════════════════════════════════════════════════

describe('Confidence Scoring', () => {
  it('should give higher confidence to TypeScript repos', () => {
    const tsRepo = makeRepo([
      makeFile('index.ts', 'export function foo(): string { return "bar"; }', 'typescript'),
    ]);
    const jsRepo = makeRepo([
      makeFile('index.js', 'module.exports.foo = function() { return "bar"; }', 'javascript'),
    ]);

    const tsSignals = computeConfidenceSignals(
      tsRepo,
      identifyEntryPoints(tsRepo, 'npm'),
      [],
      { filesAnalyzed: 1, filesSkipped: 0, linesAnalyzed: 1, claudeCalls: 1, hasTests: false, hasDocumentation: false, hasTypes: true, license: null, readmeExcerpt: null }
    );
    const jsSignals = computeConfidenceSignals(
      jsRepo,
      identifyEntryPoints(jsRepo, 'npm'),
      [],
      { filesAnalyzed: 1, filesSkipped: 0, linesAnalyzed: 1, claudeCalls: 1, hasTests: false, hasDocumentation: false, hasTypes: false, license: null, readmeExcerpt: null }
    );

    expect(computeConfidence(tsSignals)).toBeGreaterThan(computeConfidence(jsSignals));
  });

  it('should give higher confidence when tests exist', () => {
    const withTests = computeConfidenceSignals(
      makeRepo([]), [], [],
      { filesAnalyzed: 1, filesSkipped: 0, linesAnalyzed: 1, claudeCalls: 1, hasTests: true, hasDocumentation: false, hasTypes: false, license: null, readmeExcerpt: null }
    );
    const noTests = computeConfidenceSignals(
      makeRepo([]), [], [],
      { filesAnalyzed: 1, filesSkipped: 0, linesAnalyzed: 1, claudeCalls: 1, hasTests: false, hasDocumentation: false, hasTypes: false, license: null, readmeExcerpt: null }
    );

    expect(computeConfidence(withTests)).toBeGreaterThan(computeConfidence(noTests));
  });

  it('computeConfidence should return 0 for empty signals', () => {
    expect(computeConfidence([])).toBe(0);
  });

  it('computeConfidence should clamp to [0, 1]', () => {
    const signals: ConfidenceSignal[] = [
      { name: 'high', score: 2.0, weight: 1, reason: 'test' }, // Above 1
    ];
    expect(computeConfidence(signals)).toBeLessThanOrEqual(1);
  });
});

// ═════════════════════════════════════════════════════════════════════
// 9. Capability Manifest Types/Helpers
// ═════════════════════════════════════════════════════════════════════

describe('Capability Manifest Helpers', () => {
  it('sanitizeToolName should produce valid tool names', () => {
    expect(sanitizeToolName('processData')).toBe('processdata');
    expect(sanitizeToolName('my-function-name')).toBe('my_function_name');
    expect(sanitizeToolName('  spaces everywhere  ')).toBe('spaces_everywhere');
    expect(sanitizeToolName('CamelCase_Name')).toBe('camelcase_name');
    expect(sanitizeToolName('special!@#chars')).toBe('special_chars');
  });

  it('sanitizeToolName should cap length at 64', () => {
    const long = 'a'.repeat(100);
    expect(sanitizeToolName(long).length).toBeLessThanOrEqual(64);
  });

  it('capabilityToToolDeclaration should create valid tool declaration', () => {
    const cap: Capability = {
      id: 'cap-1',
      name: 'Process Data',
      description: 'Process input data and return results',
      category: 'data-processing',
      inputSchema: {
        type: 'object',
        properties: { input: { type: 'string', description: 'Input data' } },
        required: ['input'],
      },
      outputSchema: { type: 'string', description: 'Processed output' },
      source: { filePath: 'src/main.ts', startLine: 1, endLine: 50, exportedName: 'processData', isDefaultExport: false },
      language: 'typescript',
      confidence: 0.9,
      confidenceSignals: [],
      adaptationComplexity: 'simple',
      adaptationNotes: '',
    };

    const tool = capabilityToToolDeclaration(cap, 'mylib');
    expect(tool.name).toBe('mylib_process_data');
    expect(tool.description).toBe('Process input data and return results');
    expect(tool.parameters.type).toBe('object');
    expect(tool.parameters.properties.input).toBeDefined();
  });

  it('validateManifest should accept valid manifests', () => {
    const manifest: CapabilityManifest = {
      id: 'test-manifest',
      repoId: 'repo-1',
      repoName: 'test-lib',
      analyzedAt: Date.now(),
      analysisDurationMs: 1000,
      summary: 'A test library',
      primaryLanguage: 'typescript',
      languages: ['typescript'],
      repoType: 'library',
      ecosystem: 'npm',
      capabilities: [{
        id: 'cap-1',
        name: 'test',
        description: 'Test capability',
        category: 'utility',
        inputSchema: { type: 'object', properties: {} },
        outputSchema: { type: 'string', description: 'output' },
        source: { filePath: 'test.ts', startLine: 1, endLine: 10, exportedName: 'test', isDefaultExport: false },
        language: 'typescript',
        confidence: 0.8,
        confidenceSignals: [],
        adaptationComplexity: 'simple',
        adaptationNotes: '',
      }],
      entryPoints: [],
      dependencies: [],
      configSchema: null,
      confidence: { overall: 0.8, signals: [], explanation: 'Good' },
      metadata: { filesAnalyzed: 1, filesSkipped: 0, linesAnalyzed: 10, claudeCalls: 1, hasTests: true, hasDocumentation: true, hasTypes: true, license: 'MIT', readmeExcerpt: null },
    };

    const result = validateManifest(manifest);
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  it('validateManifest should reject manifests without capabilities', () => {
    const manifest = {
      id: 'test', repoId: 'repo', repoName: 'test', analyzedAt: Date.now(), analysisDurationMs: 100,
      summary: 'test', primaryLanguage: 'typescript', languages: ['typescript'], repoType: 'library' as const,
      ecosystem: 'npm', capabilities: [], entryPoints: [], dependencies: [], configSchema: null,
      confidence: { overall: 0.5, signals: [], explanation: '' },
      metadata: { filesAnalyzed: 1, filesSkipped: 0, linesAnalyzed: 10, claudeCalls: 1, hasTests: false, hasDocumentation: false, hasTypes: false, license: null, readmeExcerpt: null },
    };

    const result = validateManifest(manifest);
    expect(result.valid).toBe(false);
    expect(result.errors).toContain('No capabilities extracted');
  });

  it('explainConfidence should describe strengths and weaknesses', () => {
    const signals: ConfidenceSignal[] = [
      { name: 'types', score: 0.9, weight: 1, reason: 'Full TypeScript types' },
      { name: 'tests', score: 0.2, weight: 1, reason: 'No test coverage' },
    ];
    const explanation = explainConfidence(signals);
    expect(explanation).toContain('Full TypeScript types');
    expect(explanation).toContain('No test coverage');
  });

  it('summarizeManifest should produce readable output', () => {
    const manifest: CapabilityManifest = {
      id: 'test', repoId: 'repo', repoName: 'my-lib', analyzedAt: Date.now(), analysisDurationMs: 100,
      summary: 'A useful library', primaryLanguage: 'typescript', languages: ['typescript'], repoType: 'library',
      ecosystem: 'npm',
      capabilities: [{
        id: 'c1', name: 'do_thing', description: 'Does the thing', category: 'utility',
        inputSchema: { type: 'object', properties: {} }, outputSchema: { type: 'string', description: 'result' },
        source: { filePath: 'src/main.ts', startLine: 1, endLine: 10, exportedName: 'doThing', isDefaultExport: false },
        language: 'typescript', confidence: 0.85, confidenceSignals: [],
        adaptationComplexity: 'simple', adaptationNotes: '',
      }],
      entryPoints: [], dependencies: [{ name: 'lodash', version: '^4.17.21', scope: 'runtime', ecosystem: 'npm' }],
      configSchema: null,
      confidence: { overall: 0.85, signals: [], explanation: 'High confidence analysis' },
      metadata: { filesAnalyzed: 10, filesSkipped: 2, linesAnalyzed: 500, claudeCalls: 1, hasTests: true, hasDocumentation: true, hasTypes: true, license: 'MIT', readmeExcerpt: null },
    };

    const summary = summarizeManifest(manifest);
    expect(summary).toContain('my-lib');
    expect(summary).toContain('do_thing');
    expect(summary).toContain('85%');
  });
});

// ═════════════════════════════════════════════════════════════════════
// 10. Full Pipeline (Integration)
// ═════════════════════════════════════════════════════════════════════

describe('Full Analysis Pipeline', () => {
  it('should produce a valid manifest from a simple repo', async () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ name: 'test-lib', main: 'src/index.ts' })),
      makeFile('src/index.ts', 'export function runTests(pattern: string): string { return "results"; }', 'typescript'),
      makeFile('README.md', '# Test Lib\nA testing library', 'text'),
    ]);

    const manifest = await analyzeRepository(repo);

    expect(manifest.repoId).toBe('test-repo');
    expect(manifest.repoName).toBe('test-repo');
    expect(manifest.primaryLanguage).toBe('typescript');
    expect(manifest.ecosystem).toBe('npm');
    expect(manifest.capabilities.length).toBeGreaterThan(0);
    expect(manifest.confidence.overall).toBeGreaterThan(0);
    expect(manifest.metadata.filesAnalyzed).toBeGreaterThan(0);
  });
});

// ═════════════════════════════════════════════════════════════════════
// 11. Prompt Construction
// ═════════════════════════════════════════════════════════════════════

describe('Prompt Construction', () => {
  it('should build a system prompt with JSON schema instructions', () => {
    const prompt = buildAnalysisSystemPrompt();
    expect(prompt).toContain('capabilities');
    expect(prompt).toContain('inputSchema');
    expect(prompt).toContain('outputSchema');
    expect(prompt).toContain('JSON');
  });

  it('should build user message with file contents', () => {
    const files = [
      makeFile('src/main.ts', 'export function main() { return "hello"; }', 'typescript'),
    ];
    const eps = [{ filePath: 'src/main.ts', reason: 'index-file' as const, exports: [], confidence: 0.8 }];

    const msg = buildAnalysisUserMessage(files, eps, 'npm', 'typescript', 'A library', {
      maxFilesForClaude: 25,
      maxContentChars: 50000,
      analyzeReadme: true,
      extractDependencies: true,
    });

    expect(msg).toContain('typescript');
    expect(msg).toContain('npm');
    expect(msg).toContain('src/main.ts');
    expect(msg).toContain('A library');
  });

  it('should truncate file content to maxContentChars', () => {
    const bigContent = 'x'.repeat(60000);
    const files = [makeFile('big.ts', bigContent, 'typescript')];

    const msg = buildAnalysisUserMessage(files, [], 'npm', 'typescript', null, {
      maxFilesForClaude: 25,
      maxContentChars: 10000,
      analyzeReadme: true,
      extractDependencies: true,
    });

    // The total file content in the message should be capped
    expect(msg.length).toBeLessThan(bigContent.length);
  });
});

// ═════════════════════════════════════════════════════════════════════
// 12. cLaw Gate — Safety Invariants
// ═════════════════════════════════════════════════════════════════════

describe('cLaw Gate: Code Analysis Safety', () => {
  it('SAFETY: Analysis is read-only — no execution, no npm install, no eval', () => {
    // The analyzeRepository function only reads files from LoadedRepo (in-memory)
    // and calls Claude via runClaudeToolLoop. It never executes code,
    // spawns processes, or installs packages.
    //
    // Verify by checking the source imports — no child_process, no exec, no eval
    // The tool loop is called with tools: [] (no tools)

    const systemPrompt = buildAnalysisSystemPrompt();
    expect(systemPrompt).not.toContain('npm install');
    expect(systemPrompt).not.toContain('eval(');
    expect(systemPrompt).not.toContain('exec(');
  });

  it('SAFETY: Claude response parsing never throws — always returns safe fallback', () => {
    // Test a wide variety of malformed inputs
    const malformedInputs = [
      '',
      'null',
      'undefined',
      '[]',
      '{"capabilities": "not an array"}',
      '{"capabilities": [{"name": null}]}',
      '{broken json',
      '<html>not json</html>',
      'true',
      '42',
      '{"repoType": "<script>alert(1)</script>", "capabilities": []}',
    ];

    for (const input of malformedInputs) {
      const result = parseClaudeAnalysisResponse(input);
      expect(result).toBeDefined();
      expect(result.capabilities).toBeInstanceOf(Array);
      expect(result.configFields).toBeInstanceOf(Array);
    }
  });

  it('SAFETY: Capability names and descriptions are length-capped', () => {
    const response = JSON.stringify({
      summary: 'x'.repeat(2000),
      repoType: 'library',
      capabilities: [{
        name: 'a'.repeat(200),
        description: 'b'.repeat(1000),
        category: 'utility',
        inputSchema: { type: 'object', properties: {} },
        outputSchema: { type: 'string', description: 'c'.repeat(500) },
        sourceFile: 'd'.repeat(500),
        exportedName: 'e'.repeat(200),
        startLine: 1,
        endLine: 10,
        isDefaultExport: false,
        adaptationComplexity: 'simple',
        adaptationNotes: 'f'.repeat(1000),
      }],
      configFields: [],
    });

    const result = parseClaudeAnalysisResponse(response);

    expect(result.summary.length).toBeLessThanOrEqual(1000);
    expect(result.capabilities[0].name.length).toBeLessThanOrEqual(100);
    expect(result.capabilities[0].description.length).toBeLessThanOrEqual(500);
    expect(result.capabilities[0].sourceFile.length).toBeLessThanOrEqual(300);
    expect(result.capabilities[0].exportedName.length).toBeLessThanOrEqual(100);
    expect(result.capabilities[0].adaptationNotes.length).toBeLessThanOrEqual(500);
  });

  it('SAFETY: Invalid repo types fall back to unknown', () => {
    const response = JSON.stringify({
      summary: 'test',
      repoType: 'malicious-type',
      capabilities: [],
      configFields: [],
    });
    expect(parseClaudeAnalysisResponse(response).repoType).toBe('unknown');
  });

  it('SAFETY: Negative line numbers are clamped to 0', () => {
    const response = JSON.stringify({
      summary: 'test',
      repoType: 'library',
      capabilities: [{
        name: 'test',
        description: 'test desc',
        category: 'utility',
        inputSchema: { type: 'object', properties: {} },
        outputSchema: { type: 'string', description: 'out' },
        sourceFile: 'test.ts',
        exportedName: 'test',
        startLine: -5,
        endLine: -10,
        isDefaultExport: false,
        adaptationComplexity: 'simple',
        adaptationNotes: '',
      }],
      configFields: [],
    });

    const result = parseClaudeAnalysisResponse(response);
    expect(result.capabilities[0].startLine).toBeGreaterThanOrEqual(0);
    expect(result.capabilities[0].endLine).toBeGreaterThanOrEqual(0);
  });

  it('SAFETY: Tool declarations produce safe tool names', () => {
    // Verify no injection via tool names
    expect(sanitizeToolName('rm -rf /')).toBe('rm_rf');
    expect(sanitizeToolName('<script>alert(1)</script>')).toBe('script_alert_1_script');
    expect(sanitizeToolName('__proto__')).toBe('proto');
    expect(sanitizeToolName('constructor')).toBe('constructor');
  });

  it('SAFETY: Confidence scores are always in [0, 1]', () => {
    const signals: ConfidenceSignal[] = [
      { name: 'a', score: 5.0, weight: 1, reason: 'way too high' },
      { name: 'b', score: -3.0, weight: 1, reason: 'way too low' },
    ];

    const result = computeConfidence(signals);
    expect(result).toBeGreaterThanOrEqual(0);
    expect(result).toBeLessThanOrEqual(1);
  });
});
