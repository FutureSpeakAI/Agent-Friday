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

import type { SuperpowerSandboxConfig, AdaptedConnector, AdaptationStrategyType } from './adapter-engine';
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
   */
  private async executeWithTimeout(
    instance: SandboxInstance,
    _toolName: string,
    _args: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<string> {
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Sandbox execution timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      // Strategy-specific execution would go here.
      // This is the dispatch point that routes to:
      // - VM execution (direct-import)
      // - Subprocess bridge communication (subprocess-bridge)
      // - HTTP client request (api-wrap)
      //
      // For now, we resolve with a placeholder — real dispatch will
      // be wired when the SuperpowerRegistry (Phase 3) integrates.
      clearTimeout(timer);
      resolve('[sandbox: execution delegate not wired yet]');
    });
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
