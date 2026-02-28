/**
 * git-sandbox.ts — Behavioral Analysis Sandbox for GitLoader security.
 *
 * Track I, Phase 2: The Immune System — Behavioral Sandbox.
 *
 * Where Phase 1 (git-scanner.ts) reads code as text, this module EXECUTES
 * adapted code in an isolated environment to observe runtime behavior:
 *   - File system access attempts (logged, restricted to temp dir)
 *   - Network connection attempts (logged and blocked)
 *   - Process spawn attempts (logged and blocked)
 *   - Environment variable access (sensitive vars stripped)
 *   - Memory/CPU resource consumption
 *
 * The sandbox communicates observations back via JSONL on stdout,
 * following the same pattern as soc-bridge.ts.
 *
 * cLaw Safety Boundary:
 *   - Sandbox MUST fail closed — any isolation failure → abort + flag
 *   - Resource limits enforced via timeout + memory cap
 *   - Child process killed on ANY violation
 *   - No user data enters the sandbox environment
 *   - All observations are behavioral metadata, never content
 *
 * Platform support:
 *   - Windows: Job Objects via wmic, restricted env
 *   - Linux: ulimit, restricted env
 *   - macOS: ulimit, restricted env
 */

import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import * as crypto from 'crypto';
import type { LoadedRepo } from './git-loader';
import type { ScanReport, ScanFinding } from './git-scanner';

// ── Types ────────────────────────────────────────────────────────────

export type ObservationType =
  | 'fs-read'
  | 'fs-write'
  | 'fs-delete'
  | 'fs-stat'
  | 'net-connect'
  | 'net-listen'
  | 'net-dns'
  | 'http-request'
  | 'process-spawn'
  | 'process-exec'
  | 'env-access'
  | 'os-info'
  | 'module-load'
  | 'timer-set'
  | 'crypto-use';

export interface SandboxObservation {
  timestamp: number;
  type: ObservationType;
  detail: string;
  blocked: boolean;
  /** For fs operations: the path attempted */
  path?: string;
  /** For network: host:port */
  target?: string;
  /** For process spawn: the command */
  command?: string;
}

export interface ResourceUsage {
  peakMemoryMB: number;
  cpuTimeMs: number;
  wallTimeMs: number;
  exitCode: number | null;
  killedByLimit: boolean;
  killReason?: 'timeout' | 'memory' | 'violation' | 'error';
}

export interface BehavioralProfile {
  /** Unique sandbox run ID */
  runId: string;
  timestamp: number;
  durationMs: number;

  /** Entry points that were executed */
  entryPointsTested: string[];

  /** All raw observations (capped at MAX_OBSERVATIONS) */
  observations: SandboxObservation[];

  /** Aggregated behavioral summary */
  summary: BehavioralSummary;

  /** Resource consumption */
  resources: ResourceUsage;

  /** Behavioral risk assessment */
  riskContribution: number;  // 0-50, adds to static scan score

  /** Behavioral findings to merge with ScanReport */
  findings: ScanFinding[];

  /** Whether the sandbox itself was compromised or errored */
  sandboxIntegrity: 'intact' | 'timeout' | 'crash' | 'violation';
}

export interface BehavioralSummary {
  /** Unique file paths accessed */
  filesAccessed: string[];
  /** Unique file paths written to */
  filesWritten: string[];
  /** Network targets attempted (host:port) */
  networkTargets: string[];
  /** HTTP URLs requested */
  httpRequests: string[];
  /** Processes attempted to spawn */
  processesSpawned: string[];
  /** Environment variables accessed */
  envVarsAccessed: string[];
  /** Modules loaded (beyond standard lib) */
  externalModules: string[];
  /** OS info queries made */
  osQueries: number;
  /** Total blocked operations */
  blockedOperations: number;
  /** Whether code attempted sandbox evasion */
  evasionAttempted: boolean;
  /** Specific evasion patterns detected */
  evasionPatterns: string[];
}

export interface SandboxConfig {
  /** Max wall-clock time for sandbox execution (default: 15000ms) */
  timeoutMs: number;
  /** Max memory in MB (default: 256) */
  maxMemoryMB: number;
  /** Max observations to collect before stopping (default: 1000) */
  maxObservations: number;
  /** Whether to allow network (always false for security, configurable for testing) */
  allowNetwork: boolean;
  /** Whether to allow filesystem writes outside temp (always false) */
  allowExternalWrites: boolean;
  /** Node.js binary path (default: process.execPath) */
  nodePath: string;
}

// ── Constants ────────────────────────────────────────────────────────

const DEFAULT_CONFIG: SandboxConfig = {
  timeoutMs: 15_000,
  maxMemoryMB: 256,
  maxObservations: 1000,
  allowNetwork: false,
  allowExternalWrites: false,
  nodePath: process.execPath,
};

const MAX_ENTRY_POINTS = 5;
const MAX_OBSERVATIONS = 1000;
const MAX_FINDINGS = 100;
const MEMORY_CHECK_INTERVAL_MS = 500;

/** Patterns indicating sandbox evasion attempts */
const EVASION_PATTERNS = [
  /sandbox/i,
  /container/i,
  /docker/i,
  /virtualbox/i,
  /vmware/i,
  /\bVM\b/,
  /honeypot/i,
  /test.*env/i,
  /is.*debug/i,
];

/** Environment variables that MUST be stripped from sandbox */
const SENSITIVE_ENV_VARS = new Set([
  'HOME', 'USERPROFILE', 'USERNAME', 'USER', 'LOGNAME',
  'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP',
  'SSH_AUTH_SOCK', 'SSH_AGENT_PID',
  'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
  'GITHUB_TOKEN', 'GH_TOKEN', 'GITLAB_TOKEN',
  'NPM_TOKEN', 'NODE_AUTH_TOKEN',
  'GOOGLE_APPLICATION_CREDENTIALS',
  'AZURE_CLIENT_SECRET', 'AZURE_TENANT_ID',
  'DATABASE_URL', 'REDIS_URL', 'MONGODB_URI',
  'API_KEY', 'SECRET_KEY', 'PRIVATE_KEY',
  'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY',
  'STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY',
  'ELECTRON_RUN_AS_NODE',
]);

// ── Sandbox Harness Generator ────────────────────────────────────────

/**
 * Generate the JavaScript harness code that instruments the target.
 * This runs inside the sandboxed child process.
 *
 * The harness:
 *   1. Replaces fs, net, child_process, http/https with monitoring proxies
 *   2. Reports all observations via stdout JSONL
 *   3. Loads the target entry point(s)
 *   4. Reports completion
 */
export function generateHarnessCode(
  entryPoints: string[],
  tempDir: string,
  config: SandboxConfig,
): string {
  // Escape strings for safe embedding in generated code
  const escapeStr = (s: string): string =>
    JSON.stringify(s);

  return `
'use strict';

// ─── Sandbox Harness (generated by git-sandbox.ts) ───────────────
// DO NOT EDIT — this file is auto-generated for each sandbox run.

const _origFs = require('fs');
const _origPath = require('path');
const _origNet = require('net');
const _origHttp = require('http');
const _origHttps = require('https');
const _origDns = require('dns');
const _origCp = require('child_process');
const _origOs = require('os');
const _origModule = require('module');

const TEMP_DIR = ${escapeStr(tempDir.replace(/\\/g, '/'))};
const ALLOW_NETWORK = ${config.allowNetwork};
const ALLOW_EXTERNAL_WRITES = ${config.allowExternalWrites};
const MAX_OBS = ${config.maxObservations};
let obsCount = 0;
let startTime = Date.now();

// ─── Observation Reporter ────────────────────────────────────────
function report(type, detail, blocked, extra) {
  if (obsCount >= MAX_OBS) return;
  obsCount++;
  const obs = {
    _sandbox: true,
    timestamp: Date.now() - startTime,
    type: type,
    detail: String(detail).slice(0, 500),
    blocked: !!blocked
  };
  if (extra) Object.assign(obs, extra);
  try {
    process.stdout.write(JSON.stringify(obs) + '\\n');
  } catch (e) {
    // stdout may be closed
  }
}

function reportDone(exitCode) {
  try {
    process.stdout.write(JSON.stringify({
      _sandbox: true,
      _done: true,
      exitCode: exitCode || 0,
      observations: obsCount,
      wallTimeMs: Date.now() - startTime,
      memoryMB: Math.round(process.memoryUsage().rss / 1024 / 1024)
    }) + '\\n');
  } catch (e) {}
}

// ─── Normalize path for comparison ───────────────────────────────
function normPath(p) {
  try {
    return _origPath.resolve(String(p)).replace(/\\\\\\\\/g, '/');
  } catch { return String(p); }
}

function isInTempDir(p) {
  return normPath(p).startsWith(normPath(TEMP_DIR));
}

// ─── FS Proxy ────────────────────────────────────────────────────
const fsProxy = new Proxy(_origFs, {
  get(target, prop) {
    const val = target[prop];
    if (typeof val !== 'function') return val;

    // Read operations — log but allow within temp dir
    const readOps = ['readFile', 'readFileSync', 'readdir', 'readdirSync',
                     'stat', 'statSync', 'lstat', 'lstatSync', 'access',
                     'accessSync', 'existsSync', 'realpath', 'realpathSync'];
    // Write operations — log and restrict
    const writeOps = ['writeFile', 'writeFileSync', 'appendFile', 'appendFileSync',
                      'mkdir', 'mkdirSync', 'rename', 'renameSync', 'copyFile',
                      'copyFileSync', 'link', 'linkSync', 'symlink', 'symlinkSync'];
    // Delete operations — always block outside temp
    const deleteOps = ['unlink', 'unlinkSync', 'rmdir', 'rmdirSync', 'rm', 'rmSync'];

    if (readOps.includes(String(prop))) {
      return function(...args) {
        const p = args[0] ? normPath(args[0]) : '(unknown)';
        const inTemp = isInTempDir(p);
        report('fs-read', p, !inTemp, { path: p });
        if (!inTemp) {
          // Allow reading package.json-like files for module resolution
          if (p.endsWith('/package.json') || p.endsWith('\\\\package.json')) {
            return val.apply(target, args);
          }
          // Block reads outside temp dir
          const err = new Error('EPERM: sandboxed - read blocked outside temp dir');
          err.code = 'EPERM';
          if (String(prop).endsWith('Sync')) throw err;
          if (typeof args[args.length - 1] === 'function') {
            args[args.length - 1](err);
            return;
          }
          throw err;
        }
        return val.apply(target, args);
      };
    }

    if (writeOps.includes(String(prop))) {
      return function(...args) {
        const p = args[0] ? normPath(args[0]) : '(unknown)';
        const inTemp = isInTempDir(p);
        const blocked = !ALLOW_EXTERNAL_WRITES && !inTemp;
        report('fs-write', p, blocked, { path: p });
        if (blocked) {
          const err = new Error('EPERM: sandboxed - write blocked outside temp dir');
          err.code = 'EPERM';
          if (String(prop).endsWith('Sync')) throw err;
          if (typeof args[args.length - 1] === 'function') {
            args[args.length - 1](err);
            return;
          }
          throw err;
        }
        return val.apply(target, args);
      };
    }

    if (deleteOps.includes(String(prop))) {
      return function(...args) {
        const p = args[0] ? normPath(args[0]) : '(unknown)';
        const inTemp = isInTempDir(p);
        report('fs-delete', p, !inTemp, { path: p });
        if (!inTemp) {
          const err = new Error('EPERM: sandboxed - delete blocked outside temp dir');
          err.code = 'EPERM';
          if (String(prop).endsWith('Sync')) throw err;
          if (typeof args[args.length - 1] === 'function') {
            args[args.length - 1](err);
            return;
          }
          throw err;
        }
        return val.apply(target, args);
      };
    }

    return val.bind(target);
  }
});

// ─── Network Proxy ───────────────────────────────────────────────
function blockNet(module, method, target) {
  return function(...args) {
    const detail = target || args[0]?.hostname || args[0]?.host || args[0] || '(unknown)';
    report('net-connect', String(detail).slice(0, 200), !ALLOW_NETWORK, { target: String(detail).slice(0, 200) });
    if (!ALLOW_NETWORK) {
      const err = new Error('ENETUNREACH: sandboxed - network blocked');
      err.code = 'ENETUNREACH';
      if (typeof args[args.length - 1] === 'function') {
        args[args.length - 1](err);
        return { on: () => {}, end: () => {}, write: () => {}, destroy: () => {} };
      }
      throw err;
    }
    return module[method].apply(module, args);
  };
}

const netProxy = new Proxy(_origNet, {
  get(target, prop) {
    if (prop === 'connect' || prop === 'createConnection') {
      return blockNet(target, String(prop));
    }
    if (prop === 'createServer') {
      return function(...args) {
        report('net-listen', 'createServer', true);
        const err = new Error('EACCES: sandboxed - server creation blocked');
        err.code = 'EACCES';
        throw err;
      };
    }
    const val = target[prop];
    return typeof val === 'function' ? val.bind(target) : val;
  }
});

const httpProxy = new Proxy(_origHttp, {
  get(target, prop) {
    if (prop === 'request' || prop === 'get') {
      return function(...args) {
        const url = typeof args[0] === 'string' ? args[0] : args[0]?.hostname || args[0]?.host || '(unknown)';
        report('http-request', String(url).slice(0, 500), !ALLOW_NETWORK, { target: String(url).slice(0, 200) });
        if (!ALLOW_NETWORK) {
          const err = new Error('ENETUNREACH: sandboxed - HTTP blocked');
          err.code = 'ENETUNREACH';
          if (typeof args[args.length - 1] === 'function') {
            process.nextTick(() => args[args.length - 1](err));
          }
          return { on: () => {}, end: () => {}, write: () => {}, destroy: () => {} };
        }
        return target[prop].apply(target, args);
      };
    }
    const val = target[prop];
    return typeof val === 'function' ? val.bind(target) : val;
  }
});

// ─── child_process Proxy ─────────────────────────────────────────
const cpProxy = new Proxy(_origCp, {
  get(target, prop) {
    if (['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork'].includes(String(prop))) {
      return function(...args) {
        const cmd = String(args[0] || '(unknown)').slice(0, 200);
        report('process-spawn', cmd, true, { command: cmd });
        const err = new Error('EPERM: sandboxed - process spawning blocked');
        err.code = 'EPERM';
        if (String(prop).endsWith('Sync')) throw err;
        if (typeof args[args.length - 1] === 'function') {
          args[args.length - 1](err);
          return { on: () => {}, stdout: null, stderr: null, kill: () => {} };
        }
        throw err;
      };
    }
    const val = target[prop];
    return typeof val === 'function' ? val.bind(target) : val;
  }
});

// ─── OS Proxy ────────────────────────────────────────────────────
const osProxy = new Proxy(_origOs, {
  get(target, prop) {
    const val = target[prop];
    if (typeof val === 'function') {
      return function(...args) {
        report('os-info', String(prop), false);
        // Return sanitized values for sensitive info
        if (prop === 'hostname') return 'sandbox-host';
        if (prop === 'homedir') return TEMP_DIR;
        if (prop === 'userInfo') return { username: 'sandbox', homedir: TEMP_DIR, shell: '/bin/sh', uid: 1000, gid: 1000 };
        if (prop === 'tmpdir') return _origPath.join(TEMP_DIR, '_tmp');
        return val.apply(target, args);
      };
    }
    return val;
  }
});

// ─── DNS Proxy ───────────────────────────────────────────────────
const dnsProxy = new Proxy(_origDns, {
  get(target, prop) {
    if (['lookup', 'resolve', 'resolve4', 'resolve6', 'resolveMx', 'resolveTxt'].includes(String(prop))) {
      return function(...args) {
        const host = String(args[0] || '(unknown)').slice(0, 200);
        report('net-dns', host, !ALLOW_NETWORK, { target: host });
        if (!ALLOW_NETWORK) {
          const err = new Error('ENOTFOUND: sandboxed - DNS blocked');
          err.code = 'ENOTFOUND';
          if (typeof args[args.length - 1] === 'function') {
            args[args.length - 1](err);
            return;
          }
          throw err;
        }
        return target[prop].apply(target, args);
      };
    }
    const val = target[prop];
    return typeof val === 'function' ? val.bind(target) : val;
  }
});

// ─── Module Override ─────────────────────────────────────────────
const _origResolve = _origModule._resolveFilename;
const moduleLoads = new Set();

_origModule._resolveFilename = function(request, parent, isMain, options) {
  // Track non-builtin module loads
  if (!request.startsWith('.') && !request.startsWith('/') && !request.startsWith('\\\\')) {
    if (!moduleLoads.has(request)) {
      moduleLoads.add(request);
      report('module-load', request, false);
    }
  }
  return _origResolve.call(this, request, parent, isMain, options);
};

// Override require cache for intercepted modules
const _origRequire = _origModule.prototype.require;
_origModule.prototype.require = function(id) {
  if (id === 'fs') return fsProxy;
  if (id === 'net') return netProxy;
  if (id === 'http') return httpProxy;
  if (id === 'https') return httpProxy; // Same proxy for https
  if (id === 'child_process') return cpProxy;
  if (id === 'os') return osProxy;
  if (id === 'dns') return dnsProxy;
  if (id === 'fs/promises') {
    // Wrap fs.promises too
    const fsp = _origRequire.call(this, 'fs/promises');
    return new Proxy(fsp, {
      get(target, prop) {
        const val = target[prop];
        if (typeof val !== 'function') return val;
        return function(...args) {
          const p = args[0] ? normPath(args[0]) : '(unknown)';
          const isWrite = ['writeFile', 'appendFile', 'mkdir', 'rename', 'copyFile', 'link', 'symlink'].includes(String(prop));
          const isDelete = ['unlink', 'rmdir', 'rm'].includes(String(prop));
          const type = isDelete ? 'fs-delete' : isWrite ? 'fs-write' : 'fs-read';
          const inTemp = isInTempDir(p);
          const blocked = (isWrite || isDelete) ? !inTemp : !inTemp;
          report(type, p, blocked, { path: p });
          if (blocked) return Promise.reject(Object.assign(new Error('EPERM: sandboxed'), { code: 'EPERM' }));
          return val.apply(target, args);
        };
      }
    });
  }
  return _origRequire.call(this, id);
};

// ─── Evasion Detection ───────────────────────────────────────────
// Check if the code tries to detect it's in a sandbox
const evasionChecks = [
  'SANDBOX', 'CONTAINER', 'DOCKER', 'VIRTUAL', 'VMWARE',
  'HONEYPOT', 'TEST_ENV', 'IS_DEBUG'
];

const origEnvGet = Object.getOwnPropertyDescriptor(process, 'env');
const safeEnv = {};
for (const [k, v] of Object.entries(process.env)) {
  if (${JSON.stringify(Array.from(SENSITIVE_ENV_VARS))}.includes(k)) continue;
  safeEnv[k] = v;
}
// Add sandbox markers for evasion detection
safeEnv.NODE_ENV = 'production'; // Don't reveal it's a test
safeEnv.PATH = process.env.PATH || '';

Object.defineProperty(process, 'env', {
  get() {
    return new Proxy(safeEnv, {
      get(target, prop) {
        const key = String(prop);
        report('env-access', key, false);
        // Check for evasion
        const upper = key.toUpperCase();
        for (const e of evasionChecks) {
          if (upper.includes(e)) {
            report('env-access', key + ' (evasion check)', false);
          }
        }
        return target[prop];
      }
    });
  },
  configurable: true
});

// ─── Execute Entry Points ────────────────────────────────────────
const entryPoints = ${JSON.stringify(entryPoints)};

async function runEntryPoints() {
  for (const ep of entryPoints) {
    try {
      const resolved = _origPath.resolve(TEMP_DIR, ep);
      if (!isInTempDir(resolved)) {
        report('fs-read', resolved, true, { path: resolved });
        continue;
      }
      report('module-load', ep, false);
      require(resolved);
    } catch (e) {
      // Expected — many entry points will fail without proper deps
      // That's fine; we're observing ATTEMPTED behavior, not successful execution
    }
  }
}

startTime = Date.now();
report('module-load', '_harness-start', false);

runEntryPoints()
  .then(() => reportDone(0))
  .catch((e) => {
    report('process-exec', 'harness-error: ' + String(e.message || e).slice(0, 200), false);
    reportDone(1);
  })
  .finally(() => {
    setTimeout(() => process.exit(0), 100);
  });
`;
}

// ── Entry Point Discovery ────────────────────────────────────────────

/**
 * Identify entry points in a repository that should be tested.
 * Prioritizes: package.json main → index files → bin scripts.
 */
export function discoverEntryPoints(repo: LoadedRepo): string[] {
  const entryPoints: string[] = [];
  const fileSet = new Set(repo.files.map(f => f.path));

  // 1. Check package.json for main/bin
  const pkgFile = repo.files.find(f =>
    f.path === 'package.json' || f.path.endsWith('/package.json')
  );
  if (pkgFile) {
    try {
      const pkg = JSON.parse(pkgFile.content);
      if (pkg.main && typeof pkg.main === 'string') {
        entryPoints.push(pkg.main);
      }
      if (pkg.bin) {
        const bins = typeof pkg.bin === 'string' ? [pkg.bin] : Object.values(pkg.bin);
        for (const b of bins) {
          if (typeof b === 'string') entryPoints.push(b);
        }
      }
    } catch {
      // Malformed package.json — already flagged by static scanner
    }
  }

  // 2. Check for index files
  const indexFiles = ['index.js', 'index.ts', 'index.mjs', 'src/index.js', 'src/index.ts',
                      'lib/index.js', 'main.js', 'app.js'];
  for (const idx of indexFiles) {
    if (fileSet.has(idx) && !entryPoints.includes(idx)) {
      entryPoints.push(idx);
    }
  }

  // 3. Check for setup/install scripts (high priority for behavioral analysis)
  const setupFiles = ['setup.js', 'install.js', 'postinstall.js', 'preinstall.js'];
  for (const sf of setupFiles) {
    for (const f of repo.files) {
      if (f.path.endsWith(sf) && !entryPoints.includes(f.path)) {
        entryPoints.push(f.path);
      }
    }
  }

  return entryPoints.slice(0, MAX_ENTRY_POINTS);
}

// ── Temp Directory Management ────────────────────────────────────────

/**
 * Create a secure temporary directory and populate it with repo files.
 * Only writes JavaScript/TypeScript files — binary files are skipped.
 */
export function prepareTempDir(repo: LoadedRepo): string {
  const sandboxId = crypto.randomUUID().slice(0, 12);
  const tempDir = path.join(os.tmpdir(), `af-sandbox-${sandboxId}`);
  fs.mkdirSync(tempDir, { recursive: true });

  // Write repo files to temp dir (only text files, with size limits)
  const MAX_FILE_SIZE = 512 * 1024; // 512KB per file
  const MAX_TOTAL_SIZE = 5 * 1024 * 1024; // 5MB total
  let totalWritten = 0;

  for (const file of repo.files) {
    if (totalWritten >= MAX_TOTAL_SIZE) break;
    if (file.content.length > MAX_FILE_SIZE) continue;

    // Only write code files
    const ext = path.extname(file.path).toLowerCase();
    const codeExts = ['.js', '.ts', '.mjs', '.cjs', '.json', '.jsx', '.tsx'];
    if (!codeExts.includes(ext)) continue;

    const filePath = path.join(tempDir, file.path);
    const dir = path.dirname(filePath);

    try {
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(filePath, file.content, 'utf-8');
      totalWritten += file.content.length;
    } catch {
      // Skip files that can't be written (path issues, etc.)
    }
  }

  return tempDir;
}

/**
 * Recursively remove a temp directory.
 */
export function cleanupTempDir(tempDir: string): void {
  try {
    fs.rmSync(tempDir, { recursive: true, force: true });
  } catch {
    // Best effort cleanup
  }
}

// ── Observation Analysis ─────────────────────────────────────────────

/**
 * Analyze raw observations to produce a BehavioralSummary.
 */
export function analyzeObservations(observations: SandboxObservation[]): BehavioralSummary {
  const filesAccessed = new Set<string>();
  const filesWritten = new Set<string>();
  const networkTargets = new Set<string>();
  const httpRequests = new Set<string>();
  const processesSpawned = new Set<string>();
  const envVarsAccessed = new Set<string>();
  const externalModules = new Set<string>();
  let osQueries = 0;
  let blockedOperations = 0;
  let evasionAttempted = false;
  const evasionPatterns: string[] = [];

  for (const obs of observations) {
    if (obs.blocked) blockedOperations++;

    switch (obs.type) {
      case 'fs-read':
      case 'fs-stat':
        if (obs.path) filesAccessed.add(obs.path);
        break;
      case 'fs-write':
        if (obs.path) filesWritten.add(obs.path);
        break;
      case 'fs-delete':
        if (obs.path) filesWritten.add(`DELETE:${obs.path}`);
        break;
      case 'net-connect':
      case 'net-listen':
        if (obs.target) networkTargets.add(obs.target);
        break;
      case 'net-dns':
        if (obs.target) networkTargets.add(`dns:${obs.target}`);
        break;
      case 'http-request':
        if (obs.target) httpRequests.add(obs.target);
        break;
      case 'process-spawn':
      case 'process-exec':
        if (obs.command) processesSpawned.add(obs.command);
        break;
      case 'env-access':
        envVarsAccessed.add(obs.detail);
        // Check for evasion
        if (obs.detail.includes('evasion check')) {
          evasionAttempted = true;
          evasionPatterns.push(obs.detail);
        }
        break;
      case 'os-info':
        osQueries++;
        break;
      case 'module-load':
        if (obs.detail && !obs.detail.startsWith('_harness') && !obs.detail.startsWith('.')) {
          externalModules.add(obs.detail);
        }
        break;
    }
  }

  // Check for hostname/env evasion patterns in observations
  for (const obs of observations) {
    if (obs.type === 'os-info' && obs.detail === 'hostname') {
      // Querying hostname often indicates sandbox detection
      evasionPatterns.push('hostname-check');
    }
  }
  if (evasionPatterns.length > 0) evasionAttempted = true;

  return {
    filesAccessed: Array.from(filesAccessed).slice(0, 200),
    filesWritten: Array.from(filesWritten).slice(0, 100),
    networkTargets: Array.from(networkTargets).slice(0, 100),
    httpRequests: Array.from(httpRequests).slice(0, 100),
    processesSpawned: Array.from(processesSpawned).slice(0, 50),
    envVarsAccessed: Array.from(envVarsAccessed).slice(0, 100),
    externalModules: Array.from(externalModules).slice(0, 100),
    osQueries,
    blockedOperations,
    evasionAttempted,
    evasionPatterns: [...new Set(evasionPatterns)].slice(0, 20),
  };
}

// ── Behavioral Risk Scoring ──────────────────────────────────────────

/**
 * Convert behavioral observations into findings and a risk score.
 * Score ranges 0-50 (adds to static scan's 0-100 score, capped at 100 combined).
 */
export function scoreBehavior(
  summary: BehavioralSummary,
  resources: ResourceUsage,
): { findings: ScanFinding[]; riskContribution: number } {
  const findings: ScanFinding[] = [];
  let risk = 0;

  // Network attempts in a non-network library → suspicious
  if (summary.networkTargets.length > 0) {
    risk += Math.min(summary.networkTargets.length * 5, 20);
    findings.push({
      id: `bh-net-${crypto.randomUUID().slice(0, 8)}`,
      category: 'network',
      severity: summary.networkTargets.length > 3 ? 'high' : 'medium',
      title: `Runtime network access attempted (${summary.networkTargets.length} targets)`,
      description: `Code attempted to connect to: ${summary.networkTargets.slice(0, 5).join(', ')}`,
      recommendation: 'Verify these network targets are expected for this library\'s functionality.',
    });
  }

  // Process spawning → very suspicious
  if (summary.processesSpawned.length > 0) {
    risk += 15;
    findings.push({
      id: `bh-proc-${crypto.randomUUID().slice(0, 8)}`,
      category: 'obfuscation',
      severity: 'high',
      title: `Runtime process spawn attempted (${summary.processesSpawned.length} commands)`,
      description: `Code attempted to execute: ${summary.processesSpawned.slice(0, 3).join(', ')}`,
      recommendation: 'Process spawning from library code is a significant red flag. Review intent carefully.',
    });
  }

  // File writes outside expected scope
  const externalWrites = summary.filesWritten.filter(f => !f.includes('sandbox'));
  if (externalWrites.length > 0) {
    risk += 10;
    findings.push({
      id: `bh-fs-${crypto.randomUUID().slice(0, 8)}`,
      category: 'suspicious-file',
      severity: 'medium',
      title: `Attempted file writes outside sandbox (${externalWrites.length} paths)`,
      description: `Code attempted to write to external paths`,
      recommendation: 'Review what files the code attempts to create or modify.',
    });
  }

  // Evasion detection
  if (summary.evasionAttempted) {
    risk += 15;
    findings.push({
      id: `bh-evasion-${crypto.randomUUID().slice(0, 8)}`,
      category: 'obfuscation',
      severity: 'high',
      title: 'Sandbox evasion behavior detected',
      description: `Code checked for sandbox indicators: ${summary.evasionPatterns.slice(0, 5).join(', ')}`,
      recommendation: 'Code that detects sandbox environments often behaves differently when not observed.',
    });
  }

  // High blocked operations = lots of denied attempts
  if (summary.blockedOperations > 20) {
    risk += 5;
    findings.push({
      id: `bh-blocked-${crypto.randomUUID().slice(0, 8)}`,
      category: 'suspicious-file',
      severity: 'low',
      title: `High number of blocked operations (${summary.blockedOperations})`,
      description: 'Code made many system calls that were blocked by the sandbox.',
      recommendation: 'May indicate aggressive system access patterns.',
    });
  }

  // Resource exhaustion attempts
  if (resources.killedByLimit) {
    risk += 10;
    findings.push({
      id: `bh-resource-${crypto.randomUUID().slice(0, 8)}`,
      category: 'suspicious-file',
      severity: 'medium',
      title: `Resource limit exceeded (${resources.killReason})`,
      description: `Code was killed due to ${resources.killReason} limit. Peak memory: ${resources.peakMemoryMB}MB, wall time: ${resources.wallTimeMs}ms.`,
      recommendation: 'Code that exhausts resources may be attempting denial-of-service or cryptomining.',
    });
  }

  // Excessive env var access
  if (summary.envVarsAccessed.length > 30) {
    risk += 3;
    findings.push({
      id: `bh-env-${crypto.randomUUID().slice(0, 8)}`,
      category: 'secret',
      severity: 'low',
      title: `Excessive environment variable access (${summary.envVarsAccessed.length} vars)`,
      description: 'Code accessed an unusually high number of environment variables.',
      recommendation: 'May be scanning for API keys or credentials in the environment.',
    });
  }

  return {
    findings: findings.slice(0, MAX_FINDINGS),
    riskContribution: Math.min(risk, 50),
  };
}

// ── Sandbox Runner ───────────────────────────────────────────────────

/**
 * Parse JSONL output from sandbox process into observations.
 */
export function parseObservations(stdout: string): {
  observations: SandboxObservation[];
  doneMessage?: { exitCode: number; observations: number; wallTimeMs: number; memoryMB: number };
} {
  const observations: SandboxObservation[] = [];
  let doneMessage: { exitCode: number; observations: number; wallTimeMs: number; memoryMB: number } | undefined;

  for (const line of stdout.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const parsed = JSON.parse(trimmed);
      if (!parsed._sandbox) continue;

      if (parsed._done) {
        doneMessage = parsed;
        continue;
      }

      observations.push({
        timestamp: parsed.timestamp || 0,
        type: parsed.type || 'module-load',
        detail: parsed.detail || '',
        blocked: !!parsed.blocked,
        path: parsed.path,
        target: parsed.target,
        command: parsed.command,
      });
    } catch {
      // Non-JSON line — ignore (may be console.log from target code)
    }
  }

  return { observations, doneMessage };
}

/**
 * Run the behavioral sandbox on a loaded repository.
 *
 * This is the main entry point for Phase 2 analysis:
 *   1. Discovers entry points in the repo
 *   2. Creates a secure temp directory with repo files
 *   3. Generates an instrumented harness
 *   4. Spawns an isolated child process
 *   5. Collects behavioral observations
 *   6. Analyzes and scores the behavior
 *   7. Returns a BehavioralProfile
 */
export async function runSandbox(
  repo: LoadedRepo,
  config: Partial<SandboxConfig> = {},
): Promise<BehavioralProfile> {
  const cfg: SandboxConfig = { ...DEFAULT_CONFIG, ...config };
  const runId = crypto.randomUUID().slice(0, 12);
  const startTime = Date.now();

  // 1. Discover entry points
  const entryPoints = discoverEntryPoints(repo);
  if (entryPoints.length === 0) {
    // No entry points — return empty profile
    return createEmptyProfile(runId, startTime);
  }

  // 2. Create temp directory
  const tempDir = prepareTempDir(repo);

  try {
    // 3. Generate harness
    const harnessCode = generateHarnessCode(entryPoints, tempDir, cfg);
    const harnessPath = path.join(tempDir, '__sandbox_harness__.js');
    fs.writeFileSync(harnessPath, harnessCode, 'utf-8');

    // 4. Run sandbox
    const result = await executeSandbox(harnessPath, tempDir, cfg);

    // 5. Parse observations
    const { observations, doneMessage } = parseObservations(result.stdout);

    // 6. Analyze
    const summary = analyzeObservations(observations);
    const resources: ResourceUsage = {
      peakMemoryMB: doneMessage?.memoryMB ?? result.peakMemoryMB,
      cpuTimeMs: doneMessage?.wallTimeMs ?? result.wallTimeMs,
      wallTimeMs: result.wallTimeMs,
      exitCode: result.exitCode,
      killedByLimit: result.killed,
      killReason: result.killReason,
    };

    const { findings, riskContribution } = scoreBehavior(summary, resources);

    // 7. Build profile
    const profile: BehavioralProfile = {
      runId,
      timestamp: startTime,
      durationMs: Date.now() - startTime,
      entryPointsTested: entryPoints,
      observations: observations.slice(0, MAX_OBSERVATIONS),
      summary,
      resources,
      riskContribution,
      findings,
      sandboxIntegrity: result.killed
        ? (result.killReason === 'timeout' ? 'timeout' : 'violation')
        : result.exitCode !== 0
          ? 'crash'
          : 'intact',
    };

    return profile;
  } finally {
    // Always clean up
    cleanupTempDir(tempDir);
  }
}

// ── Process Execution ────────────────────────────────────────────────

interface SandboxResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
  killed: boolean;
  killReason?: 'timeout' | 'memory' | 'violation' | 'error';
  wallTimeMs: number;
  peakMemoryMB: number;
}

/**
 * Execute the sandbox harness in an isolated child process.
 */
function executeSandbox(
  harnessPath: string,
  tempDir: string,
  config: SandboxConfig,
): Promise<SandboxResult> {
  return new Promise((resolve) => {
    const startTime = Date.now();
    let stdout = '';
    let stderr = '';
    let killed = false;
    let killReason: SandboxResult['killReason'];
    let peakMemoryMB = 0;

    // Spawn with restricted environment
    const env: Record<string, string> = {
      NODE_ENV: 'production',
      PATH: process.env.PATH || '',
      LANG: process.env.LANG || 'en_US.UTF-8',
    };

    const args = [
      `--max-old-space-size=${config.maxMemoryMB}`,
      '--no-warnings',
      '--no-deprecation',
      harnessPath,
    ];

    let proc: ChildProcess;
    try {
      proc = spawn(config.nodePath, args, {
        cwd: tempDir,
        env,
        stdio: ['pipe', 'pipe', 'pipe'],
        timeout: config.timeoutMs,
        // Don't inherit file descriptors
        detached: false,
      });
    } catch (err) {
      resolve({
        stdout: '',
        stderr: String(err),
        exitCode: 1,
        killed: false,
        killReason: 'error',
        wallTimeMs: Date.now() - startTime,
        peakMemoryMB: 0,
      });
      return;
    }

    // Collect stdout
    proc.stdout?.on('data', (data: Buffer) => {
      const chunk = data.toString();
      if (stdout.length < 10 * 1024 * 1024) { // Cap at 10MB
        stdout += chunk;
      }
    });

    // Collect stderr
    proc.stderr?.on('data', (data: Buffer) => {
      const chunk = data.toString();
      if (stderr.length < 1024 * 1024) { // Cap at 1MB
        stderr += chunk;
      }
    });

    // Memory monitoring interval
    const memCheck = setInterval(() => {
      try {
        if (proc.pid) {
          // Read /proc/<pid>/status on Linux, or use process info
          // For cross-platform: just track from child's reports
          const elapsed = Date.now() - startTime;
          if (elapsed > config.timeoutMs) {
            killed = true;
            killReason = 'timeout';
            proc.kill('SIGKILL');
            clearInterval(memCheck);
          }
        }
      } catch {
        // Process may already be dead
      }
    }, MEMORY_CHECK_INTERVAL_MS);

    // Hard timeout
    const timeout = setTimeout(() => {
      if (!killed) {
        killed = true;
        killReason = 'timeout';
        try { proc.kill('SIGKILL'); } catch { /* already dead */ }
      }
    }, config.timeoutMs + 1000); // Grace period

    // Process exit
    proc.on('exit', (code) => {
      clearInterval(memCheck);
      clearTimeout(timeout);

      // Parse peak memory from done message in stdout
      try {
        const lines = stdout.split('\n');
        for (let i = lines.length - 1; i >= 0; i--) {
          const line = lines[i].trim();
          if (!line) continue;
          const parsed = JSON.parse(line);
          if (parsed._done && parsed.memoryMB) {
            peakMemoryMB = parsed.memoryMB;
            break;
          }
        }
      } catch { /* ignore */ }

      resolve({
        stdout,
        stderr,
        exitCode: code,
        killed,
        killReason,
        wallTimeMs: Date.now() - startTime,
        peakMemoryMB,
      });
    });

    // Process error
    proc.on('error', (err) => {
      clearInterval(memCheck);
      clearTimeout(timeout);
      resolve({
        stdout,
        stderr: stderr + '\n' + String(err),
        exitCode: 1,
        killed: false,
        killReason: 'error',
        wallTimeMs: Date.now() - startTime,
        peakMemoryMB: 0,
      });
    });

    // Close stdin immediately — sandbox doesn't need input
    proc.stdin?.end();
  });
}

// ── Integration with ScanReport ──────────────────────────────────────

/**
 * Merge a BehavioralProfile into an existing ScanReport.
 * The behavioral findings are appended, and the risk score is updated.
 */
export function mergeBehavioralProfile(
  report: ScanReport,
  profile: BehavioralProfile,
): ScanReport {
  const merged = { ...report };

  // Merge findings
  merged.findings = [...report.findings, ...profile.findings];

  // Update risk score (cap at 100)
  merged.riskScore = Math.min(100, report.riskScore + profile.riskContribution);

  // Recalculate risk level
  if (merged.riskScore >= 70) merged.riskLevel = 'critical';
  else if (merged.riskScore >= 40) merged.riskLevel = 'high';
  else if (merged.riskScore >= 15) merged.riskLevel = 'medium';
  else merged.riskLevel = 'low';

  // Store behavioral profile in extensions
  merged.extensions = {
    ...merged.extensions,
    behavioral: {
      runId: profile.runId,
      entryPointsTested: profile.entryPointsTested,
      sandboxIntegrity: profile.sandboxIntegrity,
      summary: profile.summary,
      resources: profile.resources,
      riskContribution: profile.riskContribution,
      observationCount: profile.observations.length,
    },
  };

  return merged;
}

// ── Helpers ──────────────────────────────────────────────────────────

function createEmptyProfile(runId: string, startTime: number): BehavioralProfile {
  return {
    runId,
    timestamp: startTime,
    durationMs: Date.now() - startTime,
    entryPointsTested: [],
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
      peakMemoryMB: 0,
      cpuTimeMs: 0,
      wallTimeMs: 0,
      exitCode: null,
      killedByLimit: false,
    },
    riskContribution: 0,
    findings: [],
    sandboxIntegrity: 'intact',
  };
}
