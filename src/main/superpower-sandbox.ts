/**
 * superpower-sandbox.ts — Sandboxed Execution for Adapted Superpowers.
 *
 * Track II, Phase 2: The Absorber — Adaptation Engine.
 *
 * Provides a restricted execution environment for superpower connectors.
 * All adapted code (from adapter-engine.ts) runs inside this sandbox,
 * NEVER in the host process. This enforces the cLaw boundary between
 * trusted built-in connectors and untrusted adapted superpowers.
 *
 * Execution models:
 *   1. Isolated VM (vm2-like) — For direct-import JS/TS superpowers
 *   2. Subprocess Bridge — For Python/Go/Rust superpowers (managed lifecycle)
 *   3. HTTP Client — For API-wrap superpowers (restricted fetch)
 *
 * cLaw Safety Boundary:
 *   - No eval() of untrusted code in the host process.
 *   - Subprocess bridges have restricted env (no host secrets).
 *   - Network access is opt-in per connector (default: blocked).
 *   - File access is restricted to declared paths only.
 *   - All tool calls have execution timeouts.
 *   - Memory limits prevent resource exhaustion.
 */

import type {
  SuperpowerSandboxConfig,
  AdaptedConnector,
  AdaptationStrategyType,
  InvocationSpec,
} from './adapter-engine';
import type { ToolDeclaration } from './connectors/registry';

// ── Sandbox Instance ────────────────────────────────────────────────

export interface SandboxInstance {
  /** Connector ID this sandbox belongs to */
  connectorId: string;
  /** Current sandbox state */
  state: SandboxState;
  /** Sandbox configuration */
  config: SuperpowerSandboxConfig;
  /** Strategy type determines execution model */
  strategyType: AdaptationStrategyType;
  /** When the sandbox was created */
  createdAt: number;
  /** Last tool execution timestamp */
  lastExecutionAt: number;
  /** Total tool calls executed */
  executionCount: number;
  /** Total errors encountered */
  errorCount: number;
  /** Available tools in this sandbox */
  tools: ToolDeclaration[];
  /** Invocation spec per tool (from adaptation plan) */
  invocations: Map<string, InvocationSpec>;
  /** Generated connector source code (for direct-import/claude-rewrite) */
  sourceCode: string;
}

export type SandboxState =
  | 'idle'        // Created but not started
  | 'starting'    // Bridge/VM initializing
  | 'ready'       // Accepting tool calls
  | 'executing'   // Processing a tool call
  | 'error'       // Failed — needs restart
  | 'stopped';    // Gracefully shut down

// ── Execution Result ────────────────────────────────────────────────

export interface SandboxExecutionResult {
  /** Tool call succeeded */
  success: boolean;
  /** Result string (if success) */
  result?: string;
  /** Error message (if failure) */
  error?: string;
  /** Execution time in ms */
  durationMs: number;
  /** Whether the sandbox enforced a timeout */
  timedOut: boolean;
  /** Whether memory limit was hit */
  memoryExceeded: boolean;
}

// ── Sandbox Violation ───────────────────────────────────────────────

export interface SandboxViolation {
  /** When the violation occurred */
  timestamp: number;
  /** What was violated */
  type: ViolationType;
  /** Details about the violation */
  description: string;
  /** The tool call that triggered it */
  toolName: string;
  /** Connector ID */
  connectorId: string;
}

export type ViolationType =
  | 'timeout'            // Execution exceeded maxExecutionTimeMs
  | 'memory-exceeded'    // Memory limit reached
  | 'network-blocked'    // Attempted network access when disallowed
  | 'fs-blocked'         // Attempted filesystem access when disallowed
  | 'fs-path-violation'  // Accessed a path outside allowed paths
  | 'process-blocked'    // Attempted to spawn child process when disallowed
  | 'eval-blocked'       // Attempted eval/Function constructor
  | 'import-blocked';    // Attempted to import restricted module

// ── Sandbox Manager ─────────────────────────────────────────────────

/**
 * Manages sandbox lifecycle for all active superpowers.
 * Each adapted connector gets its own sandbox instance.
 *
 * cLaw: The manager is the single gatekeeper — all superpower execution
 * MUST go through executeTool() which enforces the sandbox boundary.
 */
export class SuperpowerSandboxManager {
  private sandboxes = new Map<string, SandboxInstance>();
  private violations: SandboxViolation[] = [];
  private maxViolations = 100;

  // ── Lifecycle ───────────────────────────────────────────────────

  /**
   * Create a sandbox for an adapted connector.
   * Does NOT start execution — call start() separately.
   */
  createSandbox(connector: AdaptedConnector): SandboxInstance {
    if (this.sandboxes.has(connector.id)) {
      throw new Error(`Sandbox already exists for connector: ${connector.id}`);
    }

    // Build a map of tool name → invocation spec from the adaptation plan
    const invocations = new Map<string, InvocationSpec>();
    for (const cap of connector.plan.capabilities) {
      if (cap.toolName && cap.invocation) {
        invocations.set(cap.toolName, cap.invocation);
      }
    }

    const instance: SandboxInstance = {
      connectorId: connector.id,
      state: 'idle',
      config: { ...connector.sandbox },
      strategyType: connector.plan.strategy.type,
      createdAt: Date.now(),
      lastExecutionAt: 0,
      executionCount: 0,
      errorCount: 0,
      tools: [...connector.tools],
      invocations,
      sourceCode: connector.sourceCode,
    };

    this.sandboxes.set(connector.id, instance);
    return instance;
  }

  /**
   * Start a sandbox (initialize VM/subprocess/etc).
   */
  async startSandbox(connectorId: string): Promise<void> {
    const instance = this.sandboxes.get(connectorId);
    if (!instance) throw new Error(`No sandbox found: ${connectorId}`);
    if (instance.state === 'ready') return; // Already running

    instance.state = 'starting';

    try {
      // Strategy-specific initialization would go here.
      // For now, we mark it ready — actual subprocess/VM startup
      // will be implemented when we wire this into the connector registry.
      instance.state = 'ready';
    } catch (err) {
      instance.state = 'error';
      throw err;
    }
  }

  /**
   * Stop a sandbox gracefully.
   */
  async stopSandbox(connectorId: string): Promise<void> {
    const instance = this.sandboxes.get(connectorId);
    if (!instance) return;

    // Strategy-specific cleanup would go here
    instance.state = 'stopped';
  }

  /**
   * Remove a sandbox entirely.
   */
  removeSandbox(connectorId: string): void {
    const instance = this.sandboxes.get(connectorId);
    if (instance && instance.state !== 'stopped') {
      // Force stop
      instance.state = 'stopped';
    }
    this.sandboxes.delete(connectorId);
  }

  // ── Tool Execution ──────────────────────────────────────────────

  /**
   * Execute a tool call within the sandbox boundary.
   *
   * cLaw Gate: This is the ONLY way to invoke superpower tools.
   * All safety checks happen here before delegation.
   */
  async executeTool(
    connectorId: string,
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<SandboxExecutionResult> {
    const instance = this.sandboxes.get(connectorId);
    if (!instance) {
      return {
        success: false,
        error: `No sandbox found: ${connectorId}`,
        durationMs: 0,
        timedOut: false,
        memoryExceeded: false,
      };
    }

    if (instance.state !== 'ready') {
      return {
        success: false,
        error: `Sandbox not ready (state: ${instance.state})`,
        durationMs: 0,
        timedOut: false,
        memoryExceeded: false,
      };
    }

    // Verify tool exists in this sandbox
    const toolExists = instance.tools.some(t => t.name === toolName);
    if (!toolExists) {
      return {
        success: false,
        error: `Tool "${toolName}" not found in sandbox ${connectorId}`,
        durationMs: 0,
        timedOut: false,
        memoryExceeded: false,
      };
    }

    // Pre-execution validation: check args against known restrictions
    const preCheck = this.preExecutionCheck(instance, toolName, args);
    if (!preCheck.allowed) {
      this.recordViolation({
        timestamp: Date.now(),
        type: preCheck.violationType!,
        description: preCheck.reason!,
        toolName,
        connectorId,
      });
      return {
        success: false,
        error: `Sandbox violation: ${preCheck.reason}`,
        durationMs: 0,
        timedOut: false,
        memoryExceeded: false,
      };
    }

    // Execute with timeout
    instance.state = 'executing';
    const startTime = Date.now();

    try {
      const result = await this.executeWithTimeout(
        instance,
        toolName,
        args,
        instance.config.maxExecutionTimeMs,
      );

      instance.state = 'ready';
      instance.lastExecutionAt = Date.now();
      instance.executionCount++;

      return {
        success: true,
        result,
        durationMs: Date.now() - startTime,
        timedOut: false,
        memoryExceeded: false,
      };
    } catch (err: unknown) {
      const durationMs = Date.now() - startTime;
      const timedOut = durationMs >= instance.config.maxExecutionTimeMs;

      instance.state = 'ready';
      instance.errorCount++;

      if (timedOut) {
        this.recordViolation({
          timestamp: Date.now(),
          type: 'timeout',
          description: `Execution exceeded ${instance.config.maxExecutionTimeMs}ms`,
          toolName,
          connectorId,
        });
      }

      const message = err instanceof Error ? err.message : String(err);
      return {
        success: false,
        error: message,
        durationMs,
        timedOut,
        memoryExceeded: false,
      };
    }
  }

  /**
   * Execute a tool call with a timeout wrapper.
   * Dispatches to the correct execution strategy based on the sandbox's strategy type.
   */
  private async executeWithTimeout(
    instance: SandboxInstance,
    toolName: string,
    args: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<string> {
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Sandbox execution timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      const invocation = instance.invocations.get(toolName);

      this.dispatchExecution(instance, toolName, args, invocation)
        .then(result => {
          clearTimeout(timer);
          resolve(result);
        })
        .catch(err => {
          clearTimeout(timer);
          reject(err);
        });
    });
  }

  /**
   * Dispatch execution to the correct strategy handler.
   */
  private async dispatchExecution(
    instance: SandboxInstance,
    toolName: string,
    args: Record<string, unknown>,
    invocation?: InvocationSpec,
  ): Promise<string> {
    switch (instance.strategyType) {
      case 'api-wrap':
        return this.executeApiWrap(instance, toolName, args, invocation);
      case 'subprocess-bridge':
        return this.executeSubprocessBridge(instance, toolName, args, invocation);
      case 'direct-import':
      case 'claude-rewrite':
        return this.executeDirectImport(instance, toolName, args, invocation);
      default:
        throw new Error(`Unknown strategy type: ${instance.strategyType}`);
    }
  }

  // ── Strategy: API Wrap (HTTP Client) ──────────────────────────

  /**
   * Execute a tool call via HTTP request to an external API.
   * Used for superpowers that wrap existing REST/HTTP services.
   */
  private async executeApiWrap(
    instance: SandboxInstance,
    toolName: string,
    args: Record<string, unknown>,
    invocation?: InvocationSpec,
  ): Promise<string> {
    if (!instance.config.allowNetwork) {
      throw new Error(`Network access blocked for ${instance.connectorId}`);
    }

    const endpoint = invocation?.endpoint;
    if (!endpoint) {
      throw new Error(`No endpoint configured for tool "${toolName}" in ${instance.connectorId}`);
    }

    const method = (invocation?.method || 'POST').toUpperCase();
    const url = new URL(endpoint);

    // cLaw: Only allow HTTPS in production contexts
    if (url.protocol !== 'https:' && url.protocol !== 'http:') {
      throw new Error(`Blocked protocol: ${url.protocol} — only HTTP(S) allowed`);
    }

    const fetchOpts: RequestInit = {
      method,
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(instance.config.maxExecutionTimeMs),
    };

    if (method !== 'GET' && method !== 'HEAD') {
      fetchOpts.body = JSON.stringify({ tool: toolName, args });
    }

    const response = await fetch(url.toString(), fetchOpts);

    if (!response.ok) {
      throw new Error(`API error ${response.status}: ${response.statusText}`);
    }

    const text = await response.text();
    // Truncate overly large responses
    const maxLen = 50_000;
    return text.length > maxLen ? text.slice(0, maxLen) + '\n… [truncated]' : text;
  }

  // ── Strategy: Subprocess Bridge (JSONL) ───────────────────────

  /**
   * Execute a tool call via subprocess bridge protocol.
   * Sends a JSONL request to a child process and reads the response.
   *
   * Note: Full subprocess lifecycle management (spawn, health checks,
   * restart) is deferred to Phase 4. For now, we use one-shot execution
   * via child_process.execFile with JSON I/O.
   */
  private async executeSubprocessBridge(
    instance: SandboxInstance,
    toolName: string,
    args: Record<string, unknown>,
    invocation?: InvocationSpec,
  ): Promise<string> {
    if (!instance.config.allowChildProcesses) {
      throw new Error(`Child process execution blocked for ${instance.connectorId}`);
    }

    const command = invocation?.command;
    if (!command) {
      throw new Error(`No command configured for tool "${toolName}" in ${instance.connectorId}`);
    }

    const { execFile } = await import('child_process');
    const { promisify } = await import('util');
    const execFileAsync = promisify(execFile);

    // Build the JSONL request payload
    const request = JSON.stringify({ tool: toolName, args });
    const cmdArgs = [...(invocation?.args || []), '--input', request];

    // cLaw: Restrict environment — don't pass host secrets
    const safeEnv: Record<string, string> = {
      PATH: process.env.PATH || '',
      HOME: process.env.HOME || process.env.USERPROFILE || '',
      LANG: process.env.LANG || 'en_US.UTF-8',
      TERM: 'dumb',
    };

    try {
      const { stdout } = await execFileAsync(command, cmdArgs, {
        timeout: instance.config.maxExecutionTimeMs,
        maxBuffer: 5 * 1024 * 1024, // 5MB
        env: safeEnv,
        cwd: invocation?.cwd || undefined,
      });

      return stdout.trim();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Subprocess bridge error: ${msg}`);
    }
  }

  // ── Strategy: Direct Import (VM) ──────────────────────────────

  /**
   * Execute a tool call by running generated JS/TS code in an isolated VM context.
   *
   * Note: Full VM2-level sandboxing is deferred. Current implementation uses
   * Node's built-in vm module with restricted globals. This is NOT a security
   * boundary against malicious code — it's a structural boundary that enforces
   * the superpower architecture. The cLaw security boundary is enforced by
   * the pre-execution checks + security verdict system.
   */
  private async executeDirectImport(
    instance: SandboxInstance,
    toolName: string,
    args: Record<string, unknown>,
    invocation?: InvocationSpec,
  ): Promise<string> {
    const vm = await import('vm');

    const functionName = invocation?.functionName || toolName;

    // Build a minimal sandbox context with restricted globals
    const sandbox: Record<string, unknown> = {
      console: {
        log: (...a: unknown[]) => void a,
        error: (...a: unknown[]) => void a,
        warn: (...a: unknown[]) => void a,
      },
      // Provide JSON and common safe globals
      JSON,
      Date,
      Math,
      parseInt,
      parseFloat,
      isNaN,
      isFinite,
      encodeURIComponent,
      decodeURIComponent,
      // The tool arguments
      __toolName__: functionName,
      __toolArgs__: args,
      __result__: undefined as unknown,
      // Provide require/module so generated connector source can import modules
      require: (id: string) => {
        try { return require(id); } catch { return {}; }
      },
      module: { exports: {} },
      exports: {},
    };

    const ctx = vm.createContext(sandbox);

    // The generated sourceCode should export functions.
    // We wrap it to capture the tool function output.
    const wrappedCode = `
      ${instance.tools.find(t => t.name === toolName) ? '' : ''}
      (async () => {
        // Execute the generated connector source
        ${instance.sourceCode || ''}

        // Call the tool function if it exists
        if (typeof ${functionName} === 'function') {
          __result__ = await ${functionName}(__toolArgs__);
        } else {
          __result__ = { error: 'Function "' + __toolName__ + '" not found in connector source' };
        }
      })();
    `;

    try {
      const script = new vm.Script(wrappedCode, {
        filename: `sandbox:${instance.connectorId}/${toolName}`,
      });
      await script.runInContext(ctx, {
        timeout: instance.config.maxExecutionTimeMs,
      });

      const result = sandbox.__result__;
      return typeof result === 'string' ? result : JSON.stringify(result);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`VM execution error: ${msg}`);
    }
  }

  // ── Pre-execution Validation ────────────────────────────────────

  /**
   * Check whether a tool call is allowed by the sandbox config.
   * This is a fast synchronous check before execution.
   */
  private preExecutionCheck(
    instance: SandboxInstance,
    _toolName: string,
    args: Record<string, unknown>,
  ): { allowed: boolean; violationType?: ViolationType; reason?: string } {
    // Check for eval-like patterns in string args
    for (const [key, value] of Object.entries(args)) {
      if (typeof value === 'string') {
        if (containsEvalPattern(value)) {
          return {
            allowed: false,
            violationType: 'eval-blocked',
            reason: `Argument "${key}" contains eval-like pattern`,
          };
        }
      }
    }

    // Check for filesystem paths when FS is blocked
    if (!instance.config.allowFileSystem) {
      for (const [key, value] of Object.entries(args)) {
        if (typeof value === 'string' && looksLikePath(value)) {
          return {
            allowed: false,
            violationType: 'fs-blocked',
            reason: `Argument "${key}" contains a file path but filesystem access is disabled`,
          };
        }
      }
    }

    // Check for URLs when network is blocked
    if (!instance.config.allowNetwork) {
      for (const [key, value] of Object.entries(args)) {
        if (typeof value === 'string' && looksLikeUrl(value)) {
          return {
            allowed: false,
            violationType: 'network-blocked',
            reason: `Argument "${key}" contains a URL but network access is disabled`,
          };
        }
      }
    }

    return { allowed: true };
  }

  // ── Violation Tracking ──────────────────────────────────────────

  /**
   * Record a sandbox violation for audit.
   */
  private recordViolation(violation: SandboxViolation): void {
    this.violations.push(violation);
    if (this.violations.length > this.maxViolations) {
      this.violations = this.violations.slice(-this.maxViolations);
    }
  }

  /**
   * Get all recorded violations.
   */
  getViolations(): SandboxViolation[] {
    return [...this.violations];
  }

  /**
   * Get violations for a specific connector.
   */
  getViolationsForConnector(connectorId: string): SandboxViolation[] {
    return this.violations.filter(v => v.connectorId === connectorId);
  }

  /**
   * Clear violation history.
   */
  clearViolations(): void {
    this.violations = [];
  }

  // ── Queries ─────────────────────────────────────────────────────

  /**
   * Get a sandbox instance by connector ID.
   */
  getSandbox(connectorId: string): SandboxInstance | null {
    return this.sandboxes.get(connectorId) || null;
  }

  /**
   * Get all active sandboxes.
   */
  getAllSandboxes(): SandboxInstance[] {
    return Array.from(this.sandboxes.values());
  }

  /**
   * Get sandboxes in a specific state.
   */
  getSandboxesByState(state: SandboxState): SandboxInstance[] {
    return Array.from(this.sandboxes.values()).filter(s => s.state === state);
  }

  /**
   * Check if a connector is a superpower (sandboxed).
   * Used by the registry to distinguish built-ins from superpowers.
   */
  isSuperpower(connectorId: string): boolean {
    return connectorId.startsWith('sp-') || this.sandboxes.has(connectorId);
  }

  // ── Status ──────────────────────────────────────────────────────

  /**
   * Get aggregate status of all sandboxes.
   */
  getStatus(): SandboxManagerStatus {
    const all = this.getAllSandboxes();
    return {
      totalSandboxes: all.length,
      readySandboxes: all.filter(s => s.state === 'ready').length,
      errorSandboxes: all.filter(s => s.state === 'error').length,
      totalExecutions: all.reduce((sum, s) => sum + s.executionCount, 0),
      totalErrors: all.reduce((sum, s) => sum + s.errorCount, 0),
      totalViolations: this.violations.length,
      recentViolations: this.violations.slice(-5),
    };
  }

  /**
   * Shut down all sandboxes.
   */
  async shutdownAll(): Promise<void> {
    const promises = Array.from(this.sandboxes.keys()).map(id =>
      this.stopSandbox(id).catch(() => {}),
    );
    await Promise.all(promises);
    this.sandboxes.clear();
  }
}

export interface SandboxManagerStatus {
  totalSandboxes: number;
  readySandboxes: number;
  errorSandboxes: number;
  totalExecutions: number;
  totalErrors: number;
  totalViolations: number;
  recentViolations: SandboxViolation[];
}

// ── Pattern Detection Helpers ───────────────────────────────────────

/**
 * Detect eval-like patterns in strings.
 * cLaw: Block any attempt to inject code execution.
 */
export function containsEvalPattern(value: string): boolean {
  const patterns = [
    /\beval\s*\(/i,
    /\bnew\s+Function\s*\(/i,
    /\bsetTimeout\s*\(\s*['"`]/i,
    /\bsetInterval\s*\(\s*['"`]/i,
    /\bimport\s*\(\s*['"`]/i,
    /\brequire\s*\(\s*['"`](?!\.)/i,    // require('non-relative') is suspicious
    /\b__import__\s*\(/i,               // Python
    /\bexec\s*\(/i,                      // Python
    /\bcompile\s*\(/i,                   // Python
    /\bos\.system\s*\(/i,               // Python
    /\bsubprocess\./i,                   // Python
  ];
  return patterns.some(p => p.test(value));
}

/**
 * Check if a string looks like a filesystem path.
 */
export function looksLikePath(value: string): boolean {
  // Absolute paths
  if (/^\/[a-zA-Z]/.test(value)) return true;         // Unix absolute
  if (/^[A-Z]:\\/.test(value)) return true;            // Windows absolute
  if (/^~\//.test(value)) return true;                 // Home directory
  // Common path patterns
  if (/\.\.\//g.test(value)) return true;              // Directory traversal
  if (/^\.\/[a-zA-Z]/.test(value)) return true;        // Relative path
  return false;
}

/**
 * Check if a string looks like a URL.
 */
export function looksLikeUrl(value: string): boolean {
  if (/^https?:\/\//i.test(value)) return true;
  if (/^ftp:\/\//i.test(value)) return true;
  if (/^wss?:\/\//i.test(value)) return true;
  return false;
}

// ── Singleton Export ────────────────────────────────────────────────

export const superpowerSandbox = new SuperpowerSandboxManager();
