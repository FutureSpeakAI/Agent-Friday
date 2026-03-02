/**
 * workflow-executor.ts — Intelligent Workflow Replay Engine for Agent Friday (AGI OS).
 *
 * Track V, Phase 2: Transforms recorded workflow templates into executable
 * adaptive replays. Unlike brittle macros, the executor understands INTENT
 * behind each step, falling back to Claude-powered resolution when the
 * recorded method fails.
 *
 * Architecture:
 *   WorkflowTemplate (recorded) → WorkflowExecutor (this file) → soc-bridge / browser
 *
 * Failure hierarchy:
 *   1. Execute recorded method
 *   2. Retry with slight variations (transient)
 *   3. Claude interprets intent and finds alternative (persistent)
 *   4. Pause and ask user (fatal / decision point)
 *
 * cLaw Gate: Every step is logged. Scheduled workflows with destructive actions
 * require explicit standing permission. The agent cannot infer consent.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';

import { workflowRecorder } from './workflow-recorder';
import type { WorkflowTemplate, WorkflowStep, WorkflowParameter } from './workflow-recorder';
import {
  withRetry,
  classifyError,
  failClosedIntegrity,
  TransientError,
  PersistentError,
  FatalIntegrityError,
  type ErrorSource,
} from './errors';
import { contextStream } from './context-stream';
import { operateComputer, takeScreenshot, browserTask } from './soc-bridge';

/* ═══════════════════════════════════════════════════════════════════════════
   DATA MODEL
   ═══════════════════════════════════════════════════════════════════════════ */

export type ExecutionStatus =
  | 'idle'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type StepStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'retrying'
  | 'claude_resolving'
  | 'waiting_user'
  | 'skipped'
  | 'failed';

export interface StepResult {
  stepId: string;
  stepOrder: number;
  intent: string;
  status: StepStatus;
  method: 'recorded' | 'retry_variation' | 'claude_resolved' | 'user_resolved' | 'skipped';
  startedAt: number;
  completedAt: number;
  durationMs: number;
  error?: string;
  claudeAlternative?: string;
  screenshotBefore?: string;
  screenshotAfter?: string;
}

export interface ExecutionRun {
  id: string;
  templateId: string;
  templateName: string;
  status: ExecutionStatus;
  parameters: Record<string, string>;
  stepResults: StepResult[];
  startedAt: number;
  completedAt?: number;
  totalDurationMs?: number;
  triggeredBy: 'user' | 'schedule' | 'api';
  scheduledTaskId?: string;
  error?: string;
}

export interface StandingPermission {
  id: string;
  templateId: string;
  templateName: string;
  /** Which actions are permitted (e.g. 'click', 'type', 'navigate') */
  allowedActions: string[];
  /** Whether destructive actions (delete, submit, send) are permitted */
  allowDestructive: boolean;
  /** Max number of scheduled runs before permission expires */
  maxRuns?: number;
  runsUsed: number;
  grantedAt: number;
  expiresAt?: number;
  /** User must have explicitly confirmed this — not inferred */
  explicitlyGranted: boolean;
}

export interface ExecutorConfig {
  maxConcurrentRuns: number;
  stepTimeoutMs: number;
  claudeResolveTimeoutMs: number;
  maxRetries: number;
  screenshotOnFailure: boolean;
  maxRunHistory: number;
}

/* ═══════════════════════════════════════════════════════════════════════════
   CONSTANTS
   ═══════════════════════════════════════════════════════════════════════════ */

const SOURCE: ErrorSource = 'workflow-executor';
const LOG_PREFIX = '[WorkflowExecutor]';

const DEFAULT_CONFIG: ExecutorConfig = {
  maxConcurrentRuns: 1,
  stepTimeoutMs: 30_000,
  claudeResolveTimeoutMs: 15_000,
  maxRetries: 2,
  screenshotOnFailure: true,
  maxRunHistory: 100,
};

/** Actions considered destructive — require standing permission for scheduled runs */
const DESTRUCTIVE_KEYWORDS = [
  'delete', 'remove', 'send', 'submit', 'publish', 'post',
  'transfer', 'pay', 'purchase', 'confirm', 'approve',
];

/* ═══════════════════════════════════════════════════════════════════════════
   WORKFLOW EXECUTOR ENGINE
   ═══════════════════════════════════════════════════════════════════════════ */

class WorkflowExecutor {
  private runs: ExecutionRun[] = [];
  private activeRun: ExecutionRun | null = null;
  private permissions: StandingPermission[] = [];
  private config: ExecutorConfig = { ...DEFAULT_CONFIG };
  private filePath: string = '';
  private permissionsPath: string = '';
  private savePromise: Promise<void> = Promise.resolve();
  private abortController: AbortController | null = null;
  private pauseResolve: (() => void) | null = null;
  private userResponseResolve: ((response: string) => void) | null = null;

  /* ── Initialization ── */

  async initialize(): Promise<void> {
    const dataDir = path.join(app.getPath('userData'), 'workflows');
    await fs.mkdir(dataDir, { recursive: true });

    this.filePath = path.join(dataDir, 'execution-history.json');
    this.permissionsPath = path.join(dataDir, 'standing-permissions.json');

    try {
      const data = await fs.readFile(this.filePath, 'utf-8');
      const saved = JSON.parse(data);
      this.runs = saved.runs || [];
      if (saved.config) this.config = { ...DEFAULT_CONFIG, ...saved.config };
    } catch {
      this.runs = [];
    }

    try {
      const permData = await fs.readFile(this.permissionsPath, 'utf-8');
      this.permissions = JSON.parse(permData);
    } catch {
      this.permissions = [];
    }

    // Prune old runs
    this.pruneHistory();
    // Expire old permissions
    this.prunePermissions();

    console.log(
      `${LOG_PREFIX} Initialized — ${this.runs.length} historical runs, ` +
      `${this.permissions.length} standing permissions`
    );
  }

  /* ── Execution Lifecycle ── */

  /**
   * Execute a workflow template with the given parameters.
   * This is the main entry point for both user-triggered and scheduled runs.
   *
   * cLaw Gate: Scheduled runs with destructive actions require a standing permission.
   */
  async executeWorkflow(
    templateId: string,
    parameterValues: Record<string, string> = {},
    triggeredBy: 'user' | 'schedule' | 'api' = 'user',
    scheduledTaskId?: string,
  ): Promise<ExecutionRun> {
    // Fetch the template
    const template = workflowRecorder.getTemplate(templateId);
    if (!template) {
      throw new PersistentError(SOURCE, `Template not found: ${templateId}`);
    }

    // Check concurrent run limit
    if (this.activeRun && this.activeRun.status === 'running') {
      throw new PersistentError(SOURCE, 'Another workflow is currently running');
    }

    // cLaw Gate: Scheduled runs need permission for destructive steps
    if (triggeredBy === 'schedule') {
      const hasDestructive = this.templateHasDestructiveSteps(template);
      if (hasDestructive) {
        const permitted = this.checkStandingPermission(templateId);
        if (!permitted) {
          throw new PersistentError(
            SOURCE,
            `Scheduled workflow "${template.name}" contains destructive actions ` +
            `but has no standing permission. Grant permission through the UI first.`,
            { userMessage: `Workflow "${template.name}" needs permission for destructive actions.` },
          );
        }
      }
    }

    // Resolve parameters: user-provided → template defaults
    const resolvedParams = this.resolveParameters(template, parameterValues);

    // Create the execution run
    const run: ExecutionRun = {
      id: crypto.randomUUID().slice(0, 12),
      templateId: template.id,
      templateName: template.name,
      status: 'running',
      parameters: resolvedParams,
      stepResults: [],
      startedAt: Date.now(),
      triggeredBy,
      scheduledTaskId,
    };

    this.activeRun = run;
    this.runs.push(run);
    this.abortController = new AbortController();

    // Emit start event
    this.emitContextEvent('workflow_start', {
      runId: run.id,
      templateName: template.name,
      triggeredBy,
      parameterCount: Object.keys(resolvedParams).length,
    });

    console.log(
      `${LOG_PREFIX} Starting workflow "${template.name}" (run ${run.id}) — ` +
      `${template.steps.length} steps, triggered by ${triggeredBy}`
    );

    // Execute steps sequentially
    try {
      const sortedSteps = [...template.steps].sort((a: WorkflowStep, b: WorkflowStep) => a.order - b.order);
      for (const step of sortedSteps) {
        if (this.abortController.signal.aborted) {
          run.status = 'cancelled';
          break;
        }

        // Handle pause
        if (run.status === 'paused') {
          await new Promise<void>((resolve) => {
            this.pauseResolve = resolve;
          });
        }

        const result = await this.executeStep(step, template, resolvedParams);
        run.stepResults.push(result);

        if (result.status === 'failed') {
          run.status = 'failed';
          run.error = `Step ${step.order} failed: ${result.error}`;
          break;
        }
      }

      if (run.status === 'running') {
        run.status = 'completed';
      }
    } catch (err) {
      run.status = 'failed';
      run.error = err instanceof Error ? err.message : String(err);
    }

    run.completedAt = Date.now();
    run.totalDurationMs = run.completedAt - run.startedAt;
    this.activeRun = null;
    this.abortController = null;

    // Update standing permission usage
    if (triggeredBy === 'schedule') {
      this.incrementPermissionUsage(templateId);
    }

    // Emit completion event
    this.emitContextEvent('workflow_complete', {
      runId: run.id,
      templateName: template.name,
      status: run.status,
      totalDurationMs: run.totalDurationMs,
      stepsCompleted: run.stepResults.filter((r) => r.status === 'completed').length,
      stepsFailed: run.stepResults.filter((r) => r.status === 'failed').length,
    });

    console.log(
      `${LOG_PREFIX} Workflow "${template.name}" ${run.status} — ` +
      `${run.totalDurationMs}ms, ${run.stepResults.length}/${template.steps.length} steps`
    );

    this.scheduleSave();
    return run;
  }

  /* ── Step Execution (Failure Hierarchy) ── */

  /**
   * Execute a single workflow step with the full failure hierarchy:
   * 1. Recorded method → 2. Retry with variations → 3. Claude resolves intent → 4. Ask user
   */
  private async executeStep(
    step: WorkflowStep,
    template: WorkflowTemplate,
    params: Record<string, string>,
  ): Promise<StepResult> {
    const result: StepResult = {
      stepId: step.id,
      stepOrder: step.order,
      intent: step.intent,
      status: 'running',
      method: 'recorded',
      startedAt: Date.now(),
      completedAt: 0,
      durationMs: 0,
    };

    this.emitContextEvent('step_start', {
      runId: this.activeRun?.id,
      stepOrder: step.order,
      intent: step.intent,
      targetApp: step.targetApp,
    });

    // Substitute parameters in the step method
    const substitutedMethod = this.substituteParameters(step.method, params);

    try {
      // ── Level 1: Execute recorded method ──
      await this.executeStepMethod(substitutedMethod, step, params);
      result.status = 'completed';
      result.method = 'recorded';
    } catch (firstErr) {
      const classified = classifyError(SOURCE, firstErr);

      if (classified instanceof FatalIntegrityError) {
        // Safety boundary — abort immediately
        result.status = 'failed';
        result.error = `Safety boundary: ${classified.message}`;
        this.finishStepResult(result);
        throw classified;
      }

      if (classified instanceof TransientError) {
        // ── Level 2: Retry with variations ──
        result.status = 'retrying';
        try {
          await withRetry(SOURCE, async () => {
            await this.executeStepMethod(substitutedMethod, step, params);
          }, { maxAttempts: this.config.maxRetries });

          result.status = 'completed';
          result.method = 'retry_variation';
        } catch (retryErr) {
          // ── Level 3: Claude resolves intent ──
          result.status = 'claude_resolving';
          try {
            const alternative = await this.claudeResolveStep(step, params, String(retryErr));
            if (alternative) {
              await this.executeStepMethod(alternative, step, params);
              result.status = 'completed';
              result.method = 'claude_resolved';
              result.claudeAlternative = alternative;
            } else {
              result.status = 'failed';
              result.error = `Claude could not resolve: ${retryErr}`;
            }
          } catch (claudeErr) {
            result.status = 'failed';
            result.error = `All recovery attempts failed: ${claudeErr}`;
          }
        }
      } else if (classified instanceof PersistentError) {
        // ── Level 3 directly: Claude resolves intent ──
        result.status = 'claude_resolving';
        try {
          const alternative = await this.claudeResolveStep(step, params, classified.message);
          if (alternative) {
            await this.executeStepMethod(alternative, step, params);
            result.status = 'completed';
            result.method = 'claude_resolved';
            result.claudeAlternative = alternative;
          } else {
            // ── Level 4: Ask user at decision points ──
            if (step.isDecisionPoint) {
              result.status = 'waiting_user';
              const userResponse = await this.waitForUserInput(step, classified.message);
              if (userResponse === '__skip__') {
                result.status = 'skipped';
                result.method = 'skipped';
              } else if (userResponse === '__abort__') {
                result.status = 'failed';
                result.error = 'User aborted';
              } else {
                await this.executeStepMethod(userResponse, step, params);
                result.status = 'completed';
                result.method = 'user_resolved';
              }
            } else {
              result.status = 'failed';
              result.error = classified.message;
            }
          }
        } catch (claudeErr) {
          result.status = 'failed';
          result.error = `Recovery failed: ${claudeErr}`;
        }
      }
    }

    // Capture screenshot on failure for diagnostics
    if (result.status === 'failed' && this.config.screenshotOnFailure) {
      try {
        const screenshot = await takeScreenshot();
        result.screenshotAfter = screenshot.image;
      } catch {
        // Non-critical — don't fail the step result over a screenshot
      }
    }

    this.finishStepResult(result);

    this.emitContextEvent('step_complete', {
      runId: this.activeRun?.id,
      stepOrder: step.order,
      intent: step.intent,
      status: result.status,
      method: result.method,
      durationMs: result.durationMs,
    });

    return result;
  }

  /**
   * Execute a step method string through the appropriate execution channel.
   * Methods are dispatched based on target app and action keywords.
   */
  private async executeStepMethod(
    method: string,
    step: WorkflowStep,
    params: Record<string, string>,
  ): Promise<void> {
    const timeout = this.config.stepTimeoutMs;

    // Route to appropriate execution channel
    const lowerMethod = method.toLowerCase();
    const lowerApp = step.targetApp.toLowerCase();

    if (lowerApp.includes('browser') || lowerApp.includes('chrome') ||
        lowerApp.includes('firefox') || lowerApp.includes('edge') ||
        lowerMethod.includes('navigate to') || lowerMethod.includes('open url')) {
      // Browser-based step
      const result = await Promise.race([
        browserTask(method),
        this.timeoutPromise(timeout, `Browser step timed out: ${step.intent}`),
      ]);
      if (result && typeof result === 'object' && 'completed' in result && !result.completed) {
        throw new TransientError(SOURCE, `Browser step did not complete: ${step.intent}`);
      }
    } else {
      // Desktop automation step
      const result = await Promise.race([
        operateComputer(method),
        this.timeoutPromise(timeout, `Desktop step timed out: ${step.intent}`),
      ]);
      if (result && typeof result === 'object' && 'completed' in result && !result.completed) {
        throw new TransientError(SOURCE, `Desktop step did not complete: ${step.intent}`);
      }
    }

    // Verification: if the step has a verification hint, try to confirm success
    if (step.verificationHint) {
      await this.verifyStepOutcome(step);
    }
  }

  /**
   * Verify a step completed successfully by checking the verification hint.
   * E.g., "verify: page title contains 'Dashboard'"
   */
  private async verifyStepOutcome(step: WorkflowStep): Promise<void> {
    if (!step.verificationHint) return;

    try {
      const screenshot = await takeScreenshot();
      // Use the SOC bridge to check screen content against verification hint
      const result = await operateComputer(
        `Verify that the current screen shows: ${step.verificationHint}. ` +
        `If it does, do nothing. If it doesn't, report what you see instead.`,
        undefined,
        1,
      );
      if (result && !result.completed) {
        console.warn(`${LOG_PREFIX} Verification uncertain for step ${step.order}: ${step.verificationHint}`);
      }
    } catch {
      // Verification is best-effort — don't fail the step
      console.warn(`${LOG_PREFIX} Verification check failed for step ${step.order}`);
    }
  }

  /**
   * Ask Claude to resolve a failed step by interpreting the INTENT
   * and proposing an alternative METHOD.
   */
  private async claudeResolveStep(
    step: WorkflowStep,
    params: Record<string, string>,
    error: string,
  ): Promise<string | null> {
    try {
      // Use operateComputer with a meta-objective: figure out how to do the thing
      const objective =
        `A workflow step failed. The INTENT was: "${step.intent}". ` +
        `The recorded METHOD was: "${step.method}". ` +
        `The target application is: "${step.targetApp}". ` +
        `The error was: "${error}". ` +
        `Current parameters: ${JSON.stringify(params)}. ` +
        `Please find an alternative way to accomplish the intent. ` +
        `Describe the exact steps to take as a single action command.`;

      const result = await Promise.race([
        operateComputer(objective, undefined, 3),
        this.timeoutPromise(this.config.claudeResolveTimeoutMs, 'Claude resolution timed out'),
      ]);

      if (result && typeof result === 'object' && 'summary' in result && result.completed) {
        return result.summary;
      }
      return null;
    } catch {
      return null;
    }
  }

  /* ── Parameter Substitution ── */

  /**
   * Resolve parameters: user-provided values → template defaults → empty string.
   */
  private resolveParameters(
    template: WorkflowTemplate,
    userValues: Record<string, string>,
  ): Record<string, string> {
    const resolved: Record<string, string> = {};

    for (const param of template.parameters) {
      if (userValues[param.name] !== undefined) {
        resolved[param.name] = this.formatParameterValue(
          userValues[param.name],
          param.dataType,
        );
      } else if (userValues[param.id] !== undefined) {
        resolved[param.name] = this.formatParameterValue(
          userValues[param.id],
          param.dataType,
        );
      } else {
        resolved[param.name] = param.defaultValue;
      }
    }

    return resolved;
  }

  /**
   * Format a parameter value based on its data type.
   * E.g., dates may need different formatting for different apps.
   */
  private formatParameterValue(value: string, dataType: WorkflowParameter['dataType']): string {
    switch (dataType) {
      case 'date': {
        // Try to parse and return ISO date format
        const parsed = Date.parse(value);
        if (!isNaN(parsed)) {
          return new Date(parsed).toISOString().split('T')[0]; // YYYY-MM-DD
        }
        return value;
      }
      case 'number': {
        const num = Number(value);
        return isNaN(num) ? value : String(num);
      }
      case 'url': {
        // Ensure URL has protocol
        if (value && !value.includes('://')) {
          return `https://${value}`;
        }
        return value;
      }
      case 'email':
      case 'filepath':
      case 'text':
      default:
        return value;
    }
  }

  /**
   * Substitute {{paramName}} placeholders in a method string.
   */
  private substituteParameters(method: string, params: Record<string, string>): string {
    let result = method;
    for (const [name, value] of Object.entries(params)) {
      // Replace {{name}} and {name} patterns
      result = result.replace(new RegExp(`\\{\\{${name}\\}\\}`, 'gi'), value);
      result = result.replace(new RegExp(`\\{${name}\\}`, 'gi'), value);
    }
    return result;
  }

  /* ── Pause / Resume / Cancel ── */

  pauseExecution(): boolean {
    if (!this.activeRun || this.activeRun.status !== 'running') return false;
    this.activeRun.status = 'paused';
    this.emitContextEvent('workflow_paused', { runId: this.activeRun.id });
    console.log(`${LOG_PREFIX} Workflow paused: ${this.activeRun.id}`);
    return true;
  }

  resumeExecution(): boolean {
    if (!this.activeRun || this.activeRun.status !== 'paused') return false;
    this.activeRun.status = 'running';
    if (this.pauseResolve) {
      this.pauseResolve();
      this.pauseResolve = null;
    }
    this.emitContextEvent('workflow_resumed', { runId: this.activeRun.id });
    console.log(`${LOG_PREFIX} Workflow resumed: ${this.activeRun.id}`);
    return true;
  }

  cancelExecution(): boolean {
    if (!this.activeRun) return false;
    this.abortController?.abort();
    this.activeRun.status = 'cancelled';
    if (this.pauseResolve) {
      this.pauseResolve();
      this.pauseResolve = null;
    }
    this.emitContextEvent('workflow_cancelled', { runId: this.activeRun.id });
    console.log(`${LOG_PREFIX} Workflow cancelled: ${this.activeRun.id}`);
    return true;
  }

  /**
   * Provide a user response for a step waiting on input.
   */
  provideUserResponse(response: string): void {
    if (this.userResponseResolve) {
      this.userResponseResolve(response);
      this.userResponseResolve = null;
    }
  }

  /* ── Standing Permissions (cLaw Gate) ── */

  /**
   * Grant standing permission for a scheduled workflow.
   * cLaw: Must be explicitly granted by user action, never inferred.
   */
  grantStandingPermission(
    templateId: string,
    opts: {
      allowDestructive?: boolean;
      maxRuns?: number;
      expiresInDays?: number;
    } = {},
  ): StandingPermission {
    const template = workflowRecorder.getTemplate(templateId);
    const name = template?.name || templateId;

    // Revoke any existing permission for this template
    this.permissions = this.permissions.filter((p) => p.templateId !== templateId);

    const permission: StandingPermission = {
      id: crypto.randomUUID().slice(0, 8),
      templateId,
      templateName: name,
      allowedActions: ['click', 'type', 'navigate', 'scroll', 'keyboard'],
      allowDestructive: opts.allowDestructive ?? false,
      maxRuns: opts.maxRuns,
      runsUsed: 0,
      grantedAt: Date.now(),
      expiresAt: opts.expiresInDays
        ? Date.now() + opts.expiresInDays * 24 * 60 * 60 * 1000
        : undefined,
      explicitlyGranted: true,
    };

    this.permissions.push(permission);
    this.savePermissions();

    console.log(`${LOG_PREFIX} Standing permission granted for "${name}" (${permission.id})`);
    return permission;
  }

  revokeStandingPermission(templateId: string): boolean {
    const before = this.permissions.length;
    this.permissions = this.permissions.filter((p) => p.templateId !== templateId);
    if (this.permissions.length < before) {
      this.savePermissions();
      return true;
    }
    return false;
  }

  getStandingPermissions(): StandingPermission[] {
    return [...this.permissions];
  }

  /* ── Queries ── */

  getActiveRun(): ExecutionRun | null {
    return this.activeRun ? { ...this.activeRun } : null;
  }

  getRunHistory(limit: number = 20): ExecutionRun[] {
    return [...this.runs]
      .sort((a, b) => b.startedAt - a.startedAt)
      .slice(0, limit);
  }

  getRunById(runId: string): ExecutionRun | null {
    return this.runs.find((r) => r.id === runId) || null;
  }

  isRunning(): boolean {
    return this.activeRun?.status === 'running';
  }

  getConfig(): ExecutorConfig {
    return { ...this.config };
  }

  updateConfig(updates: Partial<ExecutorConfig>): void {
    this.config = { ...this.config, ...updates };
    this.scheduleSave();
  }

  /* ── Internal Helpers ── */

  private checkStandingPermission(templateId: string): boolean {
    return failClosedIntegrity(() => {
      const permission = this.permissions.find(
        (p) => p.templateId === templateId && p.explicitlyGranted,
      );
      if (!permission) return false;

      // Check expiry
      if (permission.expiresAt && Date.now() > permission.expiresAt) {
        return false;
      }

      // Check max runs
      if (permission.maxRuns !== undefined && permission.runsUsed >= permission.maxRuns) {
        return false;
      }

      return true;
    }, 'workflow standing permission check');
  }

  private incrementPermissionUsage(templateId: string): void {
    const permission = this.permissions.find((p) => p.templateId === templateId);
    if (permission) {
      permission.runsUsed++;
      this.savePermissions();
    }
  }

  private templateHasDestructiveSteps(template: WorkflowTemplate): boolean {
    return template.steps.some((step) => {
      const lower = `${step.intent} ${step.method}`.toLowerCase();
      return DESTRUCTIVE_KEYWORDS.some((kw) => lower.includes(kw));
    });
  }

  private async waitForUserInput(step: WorkflowStep, errorContext: string): Promise<string> {
    this.emitContextEvent('step_waiting_user', {
      runId: this.activeRun?.id,
      stepOrder: step.order,
      intent: step.intent,
      error: errorContext,
    });

    return new Promise<string>((resolve) => {
      this.userResponseResolve = resolve;
      // Timeout after 5 minutes of no response
      setTimeout(() => {
        if (this.userResponseResolve === resolve) {
          this.userResponseResolve = null;
          resolve('__abort__');
        }
      }, 5 * 60 * 1000);
    });
  }

  private timeoutPromise(ms: number, message: string): Promise<never> {
    return new Promise((_, reject) => {
      setTimeout(() => reject(new TransientError(SOURCE, message)), ms);
    });
  }

  private finishStepResult(result: StepResult): void {
    result.completedAt = Date.now();
    result.durationMs = result.completedAt - result.startedAt;
  }

  private emitContextEvent(
    action: string,
    details: Record<string, unknown>,
  ): void {
    try {
      contextStream.push({
        type: 'tool-invoke',
        source: 'workflow-executor',
        summary: `workflow:${action}`,
        data: details,
      });
    } catch {
      // Context stream emission is non-critical
    }
  }

  /* ── Persistence ── */

  private pruneHistory(): void {
    if (this.runs.length > this.config.maxRunHistory) {
      this.runs.sort((a, b) => b.startedAt - a.startedAt);
      this.runs = this.runs.slice(0, this.config.maxRunHistory);
    }
  }

  private prunePermissions(): void {
    const now = Date.now();
    this.permissions = this.permissions.filter((p) => {
      if (p.expiresAt && now > p.expiresAt) return false;
      if (p.maxRuns !== undefined && p.runsUsed >= p.maxRuns) return false;
      return true;
    });
  }

  private scheduleSave(): void {
    this.savePromise = this.savePromise
      .then(async () => {
        // Strip screenshots from history to keep file size manageable
        const runsForStorage = this.runs.map((r) => ({
          ...r,
          stepResults: r.stepResults.map((sr) => ({
            ...sr,
            screenshotBefore: undefined,
            screenshotAfter: undefined,
          })),
        }));
        await fs.writeFile(
          this.filePath,
          JSON.stringify({ runs: runsForStorage, config: this.config }, null, 2),
          'utf-8',
        );
      })
      .catch((err) => {
        // Crypto Sprint 17: Sanitize error output.
        console.error(`${LOG_PREFIX} Save failed:`, err instanceof Error ? err.message : 'Unknown error');
      });
  }

  private savePermissions(): void {
    fs.writeFile(
      this.permissionsPath,
      JSON.stringify(this.permissions, null, 2),
      'utf-8',
    ).catch((err) => {
      console.error(`${LOG_PREFIX} Permission save failed:`, err instanceof Error ? err.message : 'Unknown error');
    });
  }
}

// ── Singleton Export ─────────────────────────────────────────────────

export const workflowExecutor = new WorkflowExecutor();
