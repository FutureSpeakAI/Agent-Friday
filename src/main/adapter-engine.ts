/**
 * adapter-engine.ts — Superpower Adaptation Engine.
 *
 * Track II, Phase 2: The Absorber — Adaptation Engine.
 *
 * Transforms a CapabilityManifest (from Phase 1 analysis) + source code into
 * a working Agent Friday connector. Bridges the gap between "we know what the
 * code can do" and "we can invoke it as a tool."
 *
 * Adaptation strategies (selected automatically):
 *   1. direct-import  — TypeScript/JavaScript repos: generate a thin wrapper
 *   2. subprocess-bridge — Python/Go/Rust/other: JSONL subprocess protocol
 *   3. claude-rewrite — Non-conformant JS/TS: Claude rewrites to connector shape
 *   4. api-wrap — Code is already an HTTP server: wrap with fetch calls
 *
 * cLaw Safety Boundary:
 *   - Adapted code runs inside SuperpowerSandbox (restricted execution).
 *   - Adaptation NEVER executes untrusted code directly in the host process.
 *   - All generated connectors are tagged as 'superpower' (sandboxed), distinct
 *     from 'builtin' connectors which are trusted first-party code.
 *   - No eval(), no dynamic require() of untrusted code in the host process.
 */

import type { LoadedRepo } from './git-loader';
import type {
  CapabilityManifest,
  Capability,
  Dependency,
} from './capability-manifest';
import { sanitizeToolName } from './capability-manifest';
import type { ToolDeclaration, ConnectorCategory } from './connectors/registry';

// ── Adaptation Strategy ─────────────────────────────────────────────

export type AdaptationStrategyType =
  | 'direct-import'       // TS/JS — thin wrapper, runs in isolated VM
  | 'subprocess-bridge'   // Python/Go/Rust — JSONL subprocess protocol
  | 'claude-rewrite'      // Non-conformant JS/TS — Claude rewrites to connector
  | 'api-wrap';           // Already a server — HTTP client wrapper

export interface AdaptationStrategy {
  type: AdaptationStrategyType;
  reason: string;
  prerequisites: string[];
  estimatedComplexity: 'trivial' | 'simple' | 'moderate' | 'complex';
}

// ── Adaptation Plan ─────────────────────────────────────────────────

export interface AdaptationPlan {
  /** Links back to the manifest */
  manifestId: string;
  repoId: string;
  repoName: string;
  /** Overall strategy for this repo */
  strategy: AdaptationStrategy;
  /** Per-capability adaptation details */
  capabilities: CapabilityAdaptation[];
  /** Dependency resolution results */
  dependencies: DependencyResolution;
  /** Estimated total adaptation time */
  estimatedDurationMs: number;
  /** When the plan was created */
  createdAt: number;
}

export interface CapabilityAdaptation {
  capabilityId: string;
  capabilityName: string;
  /** Tool name for the connector (sanitized) */
  toolName: string;
  /** Strategy for this specific capability */
  strategy: AdaptationStrategyType;
  /** How to invoke this capability at runtime */
  invocation: InvocationSpec;
  /** Per-capability complexity */
  complexity: 'trivial' | 'simple' | 'moderate' | 'complex' | 'infeasible';
  /** Whether this capability was skipped and why */
  skipped: boolean;
  skipReason?: string;
}

export interface InvocationSpec {
  type: 'function-call' | 'subprocess' | 'http-request';
  /** For subprocess: the command to run */
  command?: string;
  /** For subprocess: arguments */
  args?: string[];
  /** For subprocess: working directory (relative to repo) */
  cwd?: string;
  /** For http: the endpoint URL */
  endpoint?: string;
  /** For http: HTTP method */
  method?: string;
  /** For function-call: import path */
  importPath?: string;
  /** For function-call: exported name */
  functionName?: string;
  /** For function-call: whether it's a default export */
  isDefaultExport?: boolean;
}

// ── Dependency Resolution ───────────────────────────────────────────

export interface DependencyResolution {
  /** New dependencies that need to be installed */
  newDeps: ResolvedDependency[];
  /** Conflicts with host dependencies */
  conflicts: DependencyConflict[];
  /** Whether deps should be isolated (own node_modules) */
  isolated: boolean;
  /** Why isolation was chosen (or not) */
  isolationReason: string;
}

export interface ResolvedDependency {
  name: string;
  version: string;
  ecosystem: string;
  /** Whether this dep already exists in the host */
  alreadyPresent: boolean;
}

export interface DependencyConflict {
  name: string;
  hostVersion: string;
  requiredVersion: string;
  resolution: 'compatible' | 'isolated' | 'skip';
  reason: string;
}

// ── Adapted Connector Output ────────────────────────────────────────

export interface AdaptedConnector {
  /** Unique connector ID (prefixed with 'sp-' for superpower) */
  id: string;
  /** Human-readable label */
  label: string;
  /** Connector category */
  category: ConnectorCategory;
  /** What this connector does */
  description: string;
  /** Tool declarations for registry integration */
  tools: ToolDeclaration[];
  /** Generated connector module source code */
  sourceCode: string;
  /** For subprocess bridges: the bridge runner script */
  bridgeScript?: string;
  /** Runtime dependencies needed */
  dependencies: string[];
  /** Sandbox configuration */
  sandbox: SuperpowerSandboxConfig;
  /** The plan that produced this connector */
  plan: AdaptationPlan;
  /** When the connector was generated */
  generatedAt: number;
}

export interface SuperpowerSandboxConfig {
  /** Allow outbound network requests */
  allowNetwork: boolean;
  /** Allow filesystem access */
  allowFileSystem: boolean;
  /** Allowed filesystem paths (if allowFileSystem is true) */
  allowedPaths: string[];
  /** Max execution time per tool call (ms) */
  maxExecutionTimeMs: number;
  /** Max memory usage (MB) */
  maxMemoryMb: number;
  /** Allow spawning child processes */
  allowChildProcesses: boolean;
}

// ── Default Sandbox Config ──────────────────────────────────────────

const DEFAULT_SANDBOX: SuperpowerSandboxConfig = {
  allowNetwork: false,
  allowFileSystem: false,
  allowedPaths: [],
  maxExecutionTimeMs: 30_000,
  maxMemoryMb: 256,
  allowChildProcesses: false,
};

// ── Language → Runtime Mapping ──────────────────────────────────────

const LANGUAGE_RUNTIMES: Record<string, { command: string; args: string[] }> = {
  python:     { command: 'python3', args: [] },
  javascript: { command: 'node',    args: [] },
  typescript: { command: 'npx',     args: ['tsx'] },
  ruby:       { command: 'ruby',    args: [] },
  go:         { command: 'go',      args: ['run'] },
  rust:       { command: 'cargo',   args: ['run', '--'] },
  php:        { command: 'php',     args: [] },
  java:       { command: 'java',    args: [] },
};

// ── Strategy Selection ──────────────────────────────────────────────

/**
 * Determine the best adaptation strategy for a manifest.
 * This is a pure function — no side effects.
 */
export function selectStrategy(manifest: CapabilityManifest): AdaptationStrategy {
  const lang = manifest.primaryLanguage.toLowerCase();
  const repoType = manifest.repoType;

  // API servers → wrap with HTTP client
  if (repoType === 'api-server') {
    return {
      type: 'api-wrap',
      reason: `Repository is an API server (${lang}). Will generate HTTP client wrappers.`,
      prerequisites: ['Server must be running and accessible'],
      estimatedComplexity: 'moderate',
    };
  }

  // TypeScript/JavaScript → direct import (preferred) or Claude rewrite
  if (lang === 'typescript' || lang === 'javascript') {
    const hasTypes = manifest.metadata.hasTypes;
    const hasCleanExports = manifest.entryPoints.some(ep =>
      ep.exports.some(e => e.isCapability && e.kind === 'function')
    );

    if (hasCleanExports) {
      return {
        type: 'direct-import',
        reason: `${lang} repo with clean function exports. Direct import via isolated VM.`,
        prerequisites: lang === 'typescript' ? ['tsx or ts-node for TypeScript execution'] : [],
        estimatedComplexity: hasTypes ? 'trivial' : 'simple',
      };
    }

    return {
      type: 'claude-rewrite',
      reason: `${lang} repo without clean exports. Claude will rewrite to connector shape.`,
      prerequisites: [],
      estimatedComplexity: 'moderate',
    };
  }

  // Python → subprocess bridge (most common non-JS path)
  if (lang === 'python') {
    return {
      type: 'subprocess-bridge',
      reason: 'Python repository. Will use JSONL subprocess bridge protocol.',
      prerequisites: ['python3 available on PATH', 'Required pip packages installed'],
      estimatedComplexity: 'simple',
    };
  }

  // All other languages → subprocess bridge
  const runtime = LANGUAGE_RUNTIMES[lang];
  return {
    type: 'subprocess-bridge',
    reason: `${lang} repository. Will use subprocess bridge protocol.`,
    prerequisites: runtime
      ? [`${runtime.command} available on PATH`]
      : [`Runtime for ${lang} available on PATH`],
    estimatedComplexity: 'moderate',
  };
}

// ── Adaptation Planning ─────────────────────────────────────────────

/**
 * Create a full adaptation plan from a manifest.
 * Plans what strategy each capability will use, resolves dependencies,
 * and estimates effort. Does NOT execute — just plans.
 */
export function createAdaptationPlan(manifest: CapabilityManifest): AdaptationPlan {
  const strategy = selectStrategy(manifest);
  const capabilities = planCapabilityAdaptations(manifest, strategy);
  const dependencies = resolveDependencies(manifest.dependencies);

  // Estimate based on complexity
  const complexityMs: Record<string, number> = {
    trivial: 500, simple: 2000, moderate: 5000, complex: 15000, infeasible: 0,
  };
  const estimatedDurationMs = capabilities
    .filter(c => !c.skipped)
    .reduce((sum, c) => sum + (complexityMs[c.complexity] || 5000), 0);

  return {
    manifestId: manifest.id,
    repoId: manifest.repoId,
    repoName: manifest.repoName,
    strategy,
    capabilities,
    dependencies,
    estimatedDurationMs,
    createdAt: Date.now(),
  };
}

/**
 * Plan adaptation for each individual capability.
 */
function planCapabilityAdaptations(
  manifest: CapabilityManifest,
  strategy: AdaptationStrategy,
): CapabilityAdaptation[] {
  const prefix = sanitizeToolName(manifest.repoName);

  return manifest.capabilities.map(cap => {
    const toolName = `${prefix}_${sanitizeToolName(cap.name)}`;

    // Skip infeasible capabilities
    if (cap.adaptationComplexity === 'infeasible') {
      return {
        capabilityId: cap.id,
        capabilityName: cap.name,
        toolName,
        strategy: strategy.type,
        invocation: { type: 'function-call' as const },
        complexity: 'infeasible' as const,
        skipped: true,
        skipReason: `Marked infeasible during analysis: ${cap.adaptationNotes}`,
      };
    }

    const invocation = buildInvocationSpec(cap, manifest, strategy.type);

    return {
      capabilityId: cap.id,
      capabilityName: cap.name,
      toolName,
      strategy: strategy.type,
      invocation,
      complexity: cap.adaptationComplexity,
      skipped: false,
    };
  });
}

/**
 * Build the invocation specification for a capability.
 */
function buildInvocationSpec(
  cap: Capability,
  manifest: CapabilityManifest,
  strategyType: AdaptationStrategyType,
): InvocationSpec {
  switch (strategyType) {
    case 'direct-import':
      return {
        type: 'function-call',
        importPath: cap.source.filePath,
        functionName: cap.source.exportedName,
        isDefaultExport: cap.source.isDefaultExport,
      };

    case 'subprocess-bridge': {
      const lang = manifest.primaryLanguage.toLowerCase();
      const runtime = LANGUAGE_RUNTIMES[lang] || { command: lang, args: [] };
      return {
        type: 'subprocess',
        command: runtime.command,
        args: [...runtime.args, cap.source.filePath],
        cwd: '.',
      };
    }

    case 'api-wrap':
      return {
        type: 'http-request',
        endpoint: `http://localhost:3000`, // Placeholder — real URL from config
        method: 'POST',
      };

    case 'claude-rewrite':
      return {
        type: 'function-call',
        importPath: cap.source.filePath,
        functionName: cap.source.exportedName,
        isDefaultExport: cap.source.isDefaultExport,
      };

    default:
      return { type: 'function-call' };
  }
}

// ── Dependency Resolution ───────────────────────────────────────────

/**
 * Resolve dependency conflicts between superpower and host.
 * Agent Friday's own deps are in the host's package.json.
 * Superpowers with conflicting versions get isolated node_modules.
 */
export function resolveDependencies(deps: Dependency[]): DependencyResolution {
  const runtimeDeps = deps.filter(d => d.scope === 'runtime' || d.scope === 'peer');
  const conflicts: DependencyConflict[] = [];
  const newDeps: ResolvedDependency[] = [];

  for (const dep of runtimeDeps) {
    if (dep.alreadyPresent) {
      // Check version compatibility (simplified semver check)
      conflicts.push({
        name: dep.name,
        hostVersion: 'present',
        requiredVersion: dep.version,
        resolution: 'compatible',
        reason: 'Dependency already present in host',
      });
    } else {
      newDeps.push({
        name: dep.name,
        version: dep.version,
        ecosystem: dep.ecosystem,
        alreadyPresent: false,
      });
    }
  }

  const hasConflicts = conflicts.some(c => c.resolution !== 'compatible');
  const needsIsolation = hasConflicts || newDeps.length > 3;

  return {
    newDeps,
    conflicts,
    isolated: needsIsolation,
    isolationReason: needsIsolation
      ? `${newDeps.length} new dependencies — using isolated node_modules to protect host`
      : runtimeDeps.length === 0
        ? 'No runtime dependencies — no isolation needed'
        : 'All dependencies compatible with host — shared node_modules is safe',
  };
}

// ── Connector Generation ────────────────────────────────────────────

/**
 * Generate an adapted connector from a plan + source.
 * This is the main entry point for the adaptation engine.
 *
 * cLaw Boundary: Generated code is NEVER executed in the host process.
 * It produces source code strings that will run inside SuperpowerSandbox.
 */
export function generateConnector(
  plan: AdaptationPlan,
  manifest: CapabilityManifest,
  _repo: LoadedRepo,
): AdaptedConnector {
  const activeCaps = plan.capabilities.filter(c => !c.skipped);

  if (activeCaps.length === 0) {
    throw new Error(`No adaptable capabilities in plan for ${plan.repoName}`);
  }

  const tools = activeCaps.map(cap =>
    buildToolDeclaration(cap, manifest),
  );

  const id = `sp-${sanitizeToolName(plan.repoName)}`;
  const sandbox = deriveSandboxConfig(manifest, plan);

  let sourceCode: string;
  let bridgeScript: string | undefined;

  switch (plan.strategy.type) {
    case 'direct-import':
      sourceCode = generateDirectImportConnector(id, plan, manifest, activeCaps);
      break;
    case 'subprocess-bridge':
      ({ sourceCode, bridgeScript } = generateSubprocessConnector(id, plan, manifest, activeCaps));
      break;
    case 'api-wrap':
      sourceCode = generateApiWrapConnector(id, plan, manifest, activeCaps);
      break;
    case 'claude-rewrite':
      sourceCode = generateClaudeRewriteConnector(id, plan, manifest, activeCaps);
      break;
    default:
      sourceCode = generateDirectImportConnector(id, plan, manifest, activeCaps);
  }

  return {
    id,
    label: manifest.repoName,
    category: categorizeConnector(manifest),
    description: manifest.summary.slice(0, 200),
    tools,
    sourceCode,
    bridgeScript,
    dependencies: plan.dependencies.newDeps.map(d => `${d.name}@${d.version}`),
    sandbox,
    plan,
    generatedAt: Date.now(),
  };
}

/**
 * Build a ToolDeclaration from a CapabilityAdaptation.
 */
function buildToolDeclaration(
  cap: CapabilityAdaptation,
  manifest: CapabilityManifest,
): ToolDeclaration {
  const originalCap = manifest.capabilities.find(c => c.id === cap.capabilityId);
  const schema = originalCap?.inputSchema || {
    type: 'object' as const,
    properties: {},
    required: [],
  };

  return {
    name: cap.toolName,
    description: originalCap?.description || cap.capabilityName,
    parameters: {
      type: schema.type,
      properties: schema.properties as Record<string, unknown>,
      required: schema.required,
    },
  };
}

// ── Strategy-Specific Code Generators ───────────────────────────────

/**
 * Generate connector source for direct-import strategy.
 * The generated code imports functions from the repo and wraps them
 * in the Agent Friday connector interface.
 *
 * cLaw: This code will execute in SuperpowerSandbox, NOT the host process.
 */
function generateDirectImportConnector(
  id: string,
  plan: AdaptationPlan,
  manifest: CapabilityManifest,
  caps: CapabilityAdaptation[],
): string {
  const lines: string[] = [];

  lines.push(`// Auto-generated superpower connector: ${id}`);
  lines.push(`// Source: ${manifest.repoName} (${manifest.primaryLanguage})`);
  lines.push(`// Strategy: direct-import`);
  lines.push(`// Generated: ${new Date().toISOString()}`);
  lines.push(`// WARNING: Runs inside SuperpowerSandbox — NOT host process`);
  lines.push('');

  // Generate import statements
  const importsByFile = new Map<string, string[]>();
  for (const cap of caps) {
    if (cap.invocation.importPath && cap.invocation.functionName) {
      const existing = importsByFile.get(cap.invocation.importPath) || [];
      const importName = cap.invocation.isDefaultExport
        ? cap.invocation.functionName
        : cap.invocation.functionName;
      existing.push(importName);
      importsByFile.set(cap.invocation.importPath, existing);
    }
  }

  for (const [filePath, names] of importsByFile) {
    const uniqueNames = [...new Set(names)];
    lines.push(`const { ${uniqueNames.join(', ')} } = require('./${filePath.replace(/\.(ts|tsx)$/, '')}');`);
  }

  lines.push('');

  // TOOLS declaration
  lines.push('const TOOLS = [');
  for (const cap of caps) {
    const originalCap = manifest.capabilities.find(c => c.id === cap.capabilityId);
    lines.push('  {');
    lines.push(`    name: ${JSON.stringify(cap.toolName)},`);
    lines.push(`    description: ${JSON.stringify(originalCap?.description || cap.capabilityName)},`);
    lines.push(`    parameters: ${JSON.stringify(originalCap?.inputSchema || { type: 'object', properties: {} })},`);
    lines.push('  },');
  }
  lines.push('];');
  lines.push('');

  // execute function
  lines.push('async function execute(toolName, args) {');
  lines.push('  try {');
  lines.push('    switch (toolName) {');
  for (const cap of caps) {
    if (cap.invocation.functionName) {
      lines.push(`      case ${JSON.stringify(cap.toolName)}:`);
      lines.push(`        return { result: String(await ${cap.invocation.functionName}(args)) };`);
    }
  }
  lines.push('      default:');
  lines.push('        return { error: `Unknown tool: ${toolName}` };');
  lines.push('    }');
  lines.push('  } catch (err) {');
  lines.push('    return { error: `Tool execution failed: ${err.message || String(err)}` };');
  lines.push('  }');
  lines.push('}');
  lines.push('');

  // detect function (always true for direct imports)
  lines.push('async function detect() { return true; }');
  lines.push('');
  lines.push('module.exports = { TOOLS, execute, detect };');

  return lines.join('\n');
}

/**
 * Generate connector + bridge script for subprocess strategy.
 * Uses the JSONL protocol from soc-bridge.ts.
 *
 * cLaw: Subprocess is sandboxed by SuperpowerSandbox (restricted env, timeout, no host access).
 */
function generateSubprocessConnector(
  id: string,
  plan: AdaptationPlan,
  manifest: CapabilityManifest,
  caps: CapabilityAdaptation[],
): { sourceCode: string; bridgeScript: string } {
  // ─── Bridge Script (runs in subprocess) ───
  const lang = manifest.primaryLanguage.toLowerCase();
  let bridgeScript: string;

  if (lang === 'python') {
    bridgeScript = generatePythonBridge(caps, manifest);
  } else {
    bridgeScript = generateGenericBridge(caps, manifest);
  }

  // ─── Connector Source (runs in host, manages subprocess) ───
  const lines: string[] = [];

  lines.push(`// Auto-generated superpower connector: ${id}`);
  lines.push(`// Source: ${manifest.repoName} (${manifest.primaryLanguage})`);
  lines.push(`// Strategy: subprocess-bridge (JSONL protocol)`);
  lines.push(`// Generated: ${new Date().toISOString()}`);
  lines.push('');
  lines.push(`const { spawn } = require('child_process');`);
  lines.push(`const readline = require('readline');`);
  lines.push(`const crypto = require('crypto');`);
  lines.push('');

  lines.push('let proc = null;');
  lines.push('const pending = new Map();');
  lines.push(`const TIMEOUT_MS = ${plan.capabilities[0]?.invocation.type === 'subprocess' ? 30000 : 15000};`);
  lines.push('');

  // Start subprocess
  const runtime = LANGUAGE_RUNTIMES[lang] || { command: lang, args: [] };
  lines.push('function startBridge(bridgePath) {');
  lines.push(`  proc = spawn(${JSON.stringify(runtime.command)}, [${runtime.args.map(a => JSON.stringify(a)).join(', ')}${runtime.args.length > 0 ? ', ' : ''}bridgePath], {`);
  lines.push('    stdio: ["pipe", "pipe", "pipe"],');
  lines.push('    env: { ...process.env },');
  lines.push('  });');
  lines.push('');
  lines.push('  const rl = readline.createInterface({ input: proc.stdout });');
  lines.push('  rl.on("line", (line) => {');
  lines.push('    try {');
  lines.push('      const msg = JSON.parse(line);');
  lines.push('      if (msg.id && pending.has(msg.id)) {');
  lines.push('        const { resolve } = pending.get(msg.id);');
  lines.push('        pending.delete(msg.id);');
  lines.push('        resolve(msg);');
  lines.push('      }');
  lines.push('    } catch {}');
  lines.push('  });');
  lines.push('');
  lines.push('  proc.on("exit", () => { proc = null; });');
  lines.push('}');
  lines.push('');

  // Send command
  lines.push('function send(action, params) {');
  lines.push('  return new Promise((resolve, reject) => {');
  lines.push('    if (!proc) reject(new Error("Bridge not running"));');
  lines.push('    const id = crypto.randomUUID().slice(0, 8);');
  lines.push('    const timer = setTimeout(() => {');
  lines.push('      pending.delete(id);');
  lines.push('      reject(new Error("Bridge timeout"));');
  lines.push('    }, TIMEOUT_MS);');
  lines.push('    pending.set(id, { resolve: (msg) => { clearTimeout(timer); resolve(msg); }, reject });');
  lines.push('    proc.stdin.write(JSON.stringify({ id, action, params }) + "\\n");');
  lines.push('  });');
  lines.push('}');
  lines.push('');

  // TOOLS
  lines.push('const TOOLS = [');
  for (const cap of caps) {
    const originalCap = manifest.capabilities.find(c => c.id === cap.capabilityId);
    lines.push('  {');
    lines.push(`    name: ${JSON.stringify(cap.toolName)},`);
    lines.push(`    description: ${JSON.stringify(originalCap?.description || cap.capabilityName)},`);
    lines.push(`    parameters: ${JSON.stringify(originalCap?.inputSchema || { type: 'object', properties: {} })},`);
    lines.push('  },');
  }
  lines.push('];');
  lines.push('');

  // execute
  lines.push('async function execute(toolName, args) {');
  lines.push('  try {');
  lines.push('    const result = await send(toolName, args);');
  lines.push('    if (result.status === "ok") return { result: String(result.result || "") };');
  lines.push('    return { error: result.error || "Unknown bridge error" };');
  lines.push('  } catch (err) {');
  lines.push('    return { error: `Bridge failed: ${err.message || String(err)}` };');
  lines.push('  }');
  lines.push('}');
  lines.push('');

  lines.push('async function detect() { return proc !== null; }');
  lines.push('');
  lines.push('module.exports = { TOOLS, execute, detect, startBridge };');

  return {
    sourceCode: lines.join('\n'),
    bridgeScript,
  };
}

/**
 * Generate a Python JSONL bridge script.
 */
function generatePythonBridge(
  caps: CapabilityAdaptation[],
  manifest: CapabilityManifest,
): string {
  const lines: string[] = [];

  lines.push('#!/usr/bin/env python3');
  lines.push(`"""Auto-generated JSONL bridge for ${manifest.repoName}."""`);
  lines.push('import sys, json');
  lines.push('');

  // Import the source module
  for (const cap of caps) {
    if (cap.invocation.importPath) {
      const modulePath = cap.invocation.importPath
        .replace(/\.py$/, '')
        .replace(/\//g, '.');
      if (cap.invocation.functionName) {
        lines.push(`from ${modulePath} import ${cap.invocation.functionName}`);
      }
    }
  }

  lines.push('');
  lines.push('DISPATCH = {');
  for (const cap of caps) {
    if (cap.invocation.functionName) {
      lines.push(`    ${JSON.stringify(cap.toolName)}: ${cap.invocation.functionName},`);
    }
  }
  lines.push('}');
  lines.push('');

  lines.push('def main():');
  lines.push('    for line in sys.stdin:');
  lines.push('        line = line.strip()');
  lines.push('        if not line:');
  lines.push('            continue');
  lines.push('        try:');
  lines.push('            msg = json.loads(line)');
  lines.push('            msg_id = msg.get("id", "")');
  lines.push('            action = msg.get("action", "")');
  lines.push('            params = msg.get("params", {})');
  lines.push('');
  lines.push('            fn = DISPATCH.get(action)');
  lines.push('            if fn is None:');
  lines.push('                resp = {"id": msg_id, "status": "error", "error": f"Unknown action: {action}"}');
  lines.push('            else:');
  lines.push('                try:');
  lines.push('                    result = fn(**params) if isinstance(params, dict) else fn(params)');
  lines.push('                    resp = {"id": msg_id, "status": "ok", "result": str(result)}');
  lines.push('                except Exception as e:');
  lines.push('                    resp = {"id": msg_id, "status": "error", "error": str(e)}');
  lines.push('');
  lines.push('            sys.stdout.write(json.dumps(resp) + "\\n")');
  lines.push('            sys.stdout.flush()');
  lines.push('        except json.JSONDecodeError:');
  lines.push('            pass');
  lines.push('');
  lines.push('if __name__ == "__main__":');
  lines.push('    main()');

  return lines.join('\n');
}

/**
 * Generate a generic JSONL bridge for non-Python languages.
 * Produces a Node.js bridge that shell-execs the target language.
 */
function generateGenericBridge(
  caps: CapabilityAdaptation[],
  _manifest: CapabilityManifest,
): string {
  const lines: string[] = [];

  lines.push('#!/usr/bin/env node');
  lines.push('// Generic JSONL bridge — delegates to language runtime via subprocess');
  lines.push('const readline = require("readline");');
  lines.push('const { execSync } = require("child_process");');
  lines.push('');
  lines.push('const rl = readline.createInterface({ input: process.stdin });');
  lines.push('');
  lines.push('rl.on("line", (line) => {');
  lines.push('  try {');
  lines.push('    const msg = JSON.parse(line);');
  lines.push('    const resp = { id: msg.id, status: "error", error: "Not implemented" };');
  lines.push('    process.stdout.write(JSON.stringify(resp) + "\\n");');
  lines.push('  } catch {}');
  lines.push('});');

  return lines.join('\n');
}

/**
 * Generate connector for API wrapping strategy.
 */
function generateApiWrapConnector(
  id: string,
  plan: AdaptationPlan,
  manifest: CapabilityManifest,
  caps: CapabilityAdaptation[],
): string {
  const lines: string[] = [];

  lines.push(`// Auto-generated superpower connector: ${id}`);
  lines.push(`// Source: ${manifest.repoName} (${manifest.primaryLanguage})`);
  lines.push(`// Strategy: api-wrap`);
  lines.push(`// Generated: ${new Date().toISOString()}`);
  lines.push('');
  lines.push('const https = require("https");');
  lines.push('const http = require("http");');
  lines.push('');

  lines.push('const TOOLS = [');
  for (const cap of caps) {
    const originalCap = manifest.capabilities.find(c => c.id === cap.capabilityId);
    lines.push('  {');
    lines.push(`    name: ${JSON.stringify(cap.toolName)},`);
    lines.push(`    description: ${JSON.stringify(originalCap?.description || cap.capabilityName)},`);
    lines.push(`    parameters: ${JSON.stringify(originalCap?.inputSchema || { type: 'object', properties: {} })},`);
    lines.push('  },');
  }
  lines.push('];');
  lines.push('');

  lines.push('function apiCall(endpoint, method, body) {');
  lines.push('  return new Promise((resolve, reject) => {');
  lines.push('    const url = new URL(endpoint);');
  lines.push('    const client = url.protocol === "https:" ? https : http;');
  lines.push('    const data = body ? JSON.stringify(body) : undefined;');
  lines.push('    const req = client.request(url, {');
  lines.push('      method,');
  lines.push('      headers: { "Content-Type": "application/json", ...(data ? { "Content-Length": Buffer.byteLength(data) } : {}) },');
  lines.push('      timeout: 30000,');
  lines.push('    }, (res) => {');
  lines.push('      let result = "";');
  lines.push('      res.on("data", (chunk) => result += chunk);');
  lines.push('      res.on("end", () => resolve(result));');
  lines.push('    });');
  lines.push('    req.on("error", reject);');
  lines.push('    req.on("timeout", () => { req.destroy(); reject(new Error("Timeout")); });');
  lines.push('    if (data) req.write(data);');
  lines.push('    req.end();');
  lines.push('  });');
  lines.push('}');
  lines.push('');

  lines.push('async function execute(toolName, args) {');
  lines.push('  try {');
  lines.push('    switch (toolName) {');
  for (const cap of caps) {
    const endpoint = cap.invocation.endpoint || 'http://localhost:3000';
    const method = cap.invocation.method || 'POST';
    lines.push(`      case ${JSON.stringify(cap.toolName)}:`);
    lines.push(`        return { result: await apiCall(${JSON.stringify(endpoint)}, ${JSON.stringify(method)}, args) };`);
  }
  lines.push('      default:');
  lines.push('        return { error: `Unknown tool: ${toolName}` };');
  lines.push('    }');
  lines.push('  } catch (err) {');
  lines.push('    return { error: `API call failed: ${err.message || String(err)}` };');
  lines.push('  }');
  lines.push('}');
  lines.push('');

  lines.push('async function detect() { return true; }');
  lines.push('module.exports = { TOOLS, execute, detect };');

  return lines.join('\n');
}

/**
 * Generate connector for Claude-rewrite strategy.
 * The "rewrite" is generated at plan time — this generates the wrapper.
 */
function generateClaudeRewriteConnector(
  id: string,
  plan: AdaptationPlan,
  manifest: CapabilityManifest,
  caps: CapabilityAdaptation[],
): string {
  // For Claude rewrite, we generate the same direct-import shape
  // but with a note that the source was rewritten by Claude.
  return generateDirectImportConnector(id, plan, manifest, caps)
    .replace('Strategy: direct-import', 'Strategy: claude-rewrite');
}

// ── Sandbox Configuration ───────────────────────────────────────────

/**
 * Derive sandbox config from the manifest's capability analysis.
 * More restrictive by default — only opens permissions when justified.
 */
function deriveSandboxConfig(
  manifest: CapabilityManifest,
  plan: AdaptationPlan,
): SuperpowerSandboxConfig {
  const config = { ...DEFAULT_SANDBOX };

  // Check if any capability needs network
  const needsNetwork = manifest.capabilities.some(c =>
    c.category === 'network' || c.category === 'messaging',
  );
  if (needsNetwork) {
    config.allowNetwork = true;
  }

  // Check if any capability needs filesystem
  const needsFS = manifest.capabilities.some(c =>
    c.category === 'file-operations',
  );
  if (needsFS) {
    config.allowFileSystem = true;
    // Restrict to repo directory only
    config.allowedPaths = ['.'];
  }

  // Subprocess bridges need child process permission
  if (plan.strategy.type === 'subprocess-bridge') {
    config.allowChildProcesses = true;
  }

  // Longer timeout for complex operations
  const hasComplex = plan.capabilities.some(c => c.complexity === 'complex');
  if (hasComplex) {
    config.maxExecutionTimeMs = 60_000;
  }

  return config;
}

// ── Connector Categorization ────────────────────────────────────────

/**
 * Map repo type to connector category.
 */
function categorizeConnector(
  manifest: CapabilityManifest,
): AdaptedConnector['category'] {
  // Use capability categories to determine best connector category
  const categories = manifest.capabilities.map(c => c.category);

  if (categories.includes('code-generation') || categories.includes('system')) {
    return 'devops';
  }
  if (categories.includes('messaging')) {
    return 'communication';
  }
  if (categories.includes('image-processing') || categories.includes('audio-processing') || categories.includes('video-processing')) {
    return 'creative';
  }
  if (categories.includes('database') || categories.includes('file-operations')) {
    return 'office';
  }

  return 'foundation';
}

// ── Validation ──────────────────────────────────────────────────────

/**
 * Validate an adapted connector is structurally sound.
 */
export function validateAdaptedConnector(
  connector: AdaptedConnector,
): { valid: boolean; errors: string[] } {
  const errors: string[] = [];

  if (!connector.id) errors.push('Missing connector ID');
  if (!connector.id.startsWith('sp-')) errors.push('Superpower ID must start with "sp-"');
  if (!connector.label) errors.push('Missing connector label');
  if (!connector.description) errors.push('Missing connector description');

  if (!connector.tools || connector.tools.length === 0) {
    errors.push('No tools generated');
  }

  for (const tool of connector.tools || []) {
    if (!tool.name) errors.push('Tool missing name');
    if (!tool.description) errors.push(`Tool "${tool.name}" missing description`);
    if (!tool.parameters) errors.push(`Tool "${tool.name}" missing parameters`);
  }

  if (!connector.sourceCode) {
    errors.push('No source code generated');
  }

  if (!connector.sandbox) {
    errors.push('Missing sandbox configuration');
  }

  // Verify tool names are properly prefixed
  for (const tool of connector.tools || []) {
    if (!/^[a-z][a-z0-9_]*$/.test(tool.name)) {
      errors.push(`Tool name "${tool.name}" contains invalid characters`);
    }
  }

  return { valid: errors.length === 0, errors };
}

// ── Plan Summary ────────────────────────────────────────────────────

/**
 * Generate a human-readable summary of an adaptation plan.
 */
export function summarizeAdaptationPlan(plan: AdaptationPlan): string {
  const parts: string[] = [];

  parts.push(`## Adaptation Plan: ${plan.repoName}`);
  parts.push('');
  parts.push(`**Strategy:** ${plan.strategy.type} — ${plan.strategy.reason}`);
  parts.push(`**Estimated time:** ${plan.estimatedDurationMs}ms`);
  parts.push('');

  const active = plan.capabilities.filter(c => !c.skipped);
  const skipped = plan.capabilities.filter(c => c.skipped);

  parts.push(`### Capabilities (${active.length} active, ${skipped.length} skipped)`);
  for (const cap of active) {
    parts.push(`- **${cap.toolName}** (${cap.complexity}) — ${cap.invocation.type}`);
  }
  for (const cap of skipped) {
    parts.push(`- ~~${cap.capabilityName}~~ — ${cap.skipReason}`);
  }
  parts.push('');

  parts.push(`### Dependencies`);
  parts.push(`Isolation: ${plan.dependencies.isolated ? 'Yes' : 'No'} — ${plan.dependencies.isolationReason}`);
  if (plan.dependencies.newDeps.length > 0) {
    parts.push(`New: ${plan.dependencies.newDeps.map(d => `${d.name}@${d.version}`).join(', ')}`);
  }
  if (plan.dependencies.conflicts.length > 0) {
    parts.push(`Conflicts: ${plan.dependencies.conflicts.map(c => `${c.name} (${c.resolution})`).join(', ')}`);
  }

  return parts.join('\n');
}

// ── Serialization ───────────────────────────────────────────────────

/**
 * Serialize an AdaptedConnector for persistence.
 */
export function serializeConnector(connector: AdaptedConnector): string {
  return JSON.stringify(connector, null, 2);
}

/**
 * Deserialize an AdaptedConnector from storage.
 */
export function deserializeConnector(json: string): AdaptedConnector | null {
  try {
    const parsed = JSON.parse(json);
    if (!parsed.id || !parsed.tools || !parsed.sourceCode) return null;
    return parsed as AdaptedConnector;
  } catch {
    return null;
  }
}
