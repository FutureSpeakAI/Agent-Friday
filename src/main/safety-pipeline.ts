/**
 * Track B, Phase 2: "The Guardrails" — Safety Pipeline
 *
 * The conscience of the tool execution system. Sits between LLM tool calls
 * and actual execution, making approve/deny/pending decisions based on
 * each tool's safety classification.
 *
 * Decision flow:
 *   1. LLM emits ToolCall
 *   2. SafetyPipeline.evaluate() checks tool registry for safety level
 *   3. read-only → auto-approve
 *   4. write → pending (requires user confirmation)
 *   5. destructive → pending (requires explicit confirmation + warning)
 *   6. unknown → deny
 *
 * Pending decisions have a 60-second TTL. If not confirmed/denied
 * within that window, they auto-deny (fail-closed).
 *
 * This module is the gate — it does NOT execute tools.
 * Phase B.3 (ExecutionDelegate) handles actual execution after approval.
 *
 * Hermeneutic note: This module understands through the *whole* — the
 * system's responsibility to the user. Every decision is made in the
 * context of what the worst-case outcome would be.
 */

import type { ToolCall } from './llm-client';
import { toolRegistry, type SafetyLevel } from './tool-registry';

// ── Types ─────────────────────────────────────────────────────────────

export type DecisionStatus = 'approved' | 'denied' | 'pending';

export interface SafetyDecision {
  /** Unique identifier for this decision */
  id: string;
  /** Current status */
  status: DecisionStatus;
  /** The original tool call being evaluated */
  toolCall: ToolCall;
  /** Human-readable message (confirmation prompt or warning) */
  message?: string;
  /** Reason for denial */
  reason?: string;
  /** When this decision was created */
  createdAt: number;
}

export interface SafetyPolicy {
  /** Safety levels that are auto-approved */
  autoApprove: SafetyLevel[];
  /** Safety levels that require confirmation */
  requireConfirmation: SafetyLevel[];
  /** Timeout for pending decisions (ms) */
  pendingTimeoutMs: number;
}

// ── Constants ─────────────────────────────────────────────────────────

const PENDING_TIMEOUT_MS = 60_000; // 60 seconds

// ── SafetyPipeline Class ──────────────────────────────────────────────

export class SafetyPipeline {
  private decisions = new Map<string, SafetyDecision>();
  private decisionCounter = 0;
  private timers = new Map<string, ReturnType<typeof setTimeout>>();

  /**
   * Evaluate a tool call and return a safety decision.
   *
   * - read-only → approved immediately
   * - write → pending (needs user confirmation)
   * - destructive → pending (needs explicit confirmation with warning)
   * - unknown tool → denied
   */
  evaluate(toolCall: ToolCall): SafetyDecision {
    const id = `sd-${++this.decisionCounter}`;

    // Check if tool exists in registry
    let safetyLevel: SafetyLevel;
    try {
      toolRegistry.resolve(toolCall.name);
      // Get the safety level from definitions
      const defs = toolRegistry.getDefinitions();
      const def = defs.find(d => d.name === toolCall.name);
      safetyLevel = (def as { safetyLevel: SafetyLevel })?.safetyLevel ?? 'write';
    } catch {
      // Unknown tool — deny
      const decision: SafetyDecision = {
        id,
        status: 'denied',
        toolCall,
        reason: `Unknown tool "${toolCall.name}"`,
        createdAt: Date.now(),
      };
      this.decisions.set(id, decision);
      return decision;
    }

    // Auto-approve read-only tools
    if (safetyLevel === 'read-only') {
      const decision: SafetyDecision = {
        id,
        status: 'approved',
        toolCall,
        createdAt: Date.now(),
      };
      this.decisions.set(id, decision);
      return decision;
    }

    // Pending for write/destructive
    const isDestructive = safetyLevel === 'destructive';
    const decision: SafetyDecision = {
      id,
      status: 'pending',
      toolCall,
      message: isDestructive
        ? `WARNING: Destructive action "${toolCall.name}" requested. This operation cannot be undone. Confirm to proceed.`
        : `Tool "${toolCall.name}" wants to modify data. Confirm to proceed.`,
      createdAt: Date.now(),
    };

    this.decisions.set(id, decision);

    // Set expiry timer — auto-deny after timeout (fail-closed)
    const timer = setTimeout(() => {
      const d = this.decisions.get(id);
      if (d && d.status === 'pending') {
        d.status = 'denied';
        d.reason = 'Confirmation timeout expired';
      }
      this.timers.delete(id);
    }, PENDING_TIMEOUT_MS);

    this.timers.set(id, timer);

    return decision;
  }

  /**
   * Confirm a pending decision, upgrading it to approved.
   * Returns true if the decision was found and upgraded, false otherwise.
   */
  confirm(decisionId: string): boolean {
    const decision = this.decisions.get(decisionId);
    if (!decision || decision.status !== 'pending') return false;

    decision.status = 'approved';
    this.clearTimer(decisionId);
    return true;
  }

  /**
   * Deny a pending decision.
   * Returns true if the decision was found and denied, false otherwise.
   */
  deny(decisionId: string): boolean {
    const decision = this.decisions.get(decisionId);
    if (!decision || decision.status !== 'pending') return false;

    decision.status = 'denied';
    decision.reason = 'User denied';
    this.clearTimer(decisionId);
    return true;
  }

  /**
   * Look up a decision by ID.
   */
  getDecision(decisionId: string): SafetyDecision | undefined {
    return this.decisions.get(decisionId);
  }

  /**
   * Get the current safety policy for inspection.
   */
  getPolicy(): SafetyPolicy {
    return {
      autoApprove: ['read-only'],
      requireConfirmation: ['write', 'destructive'],
      pendingTimeoutMs: PENDING_TIMEOUT_MS,
    };
  }

  // ── Private ─────────────────────────────────────────────────────────

  private clearTimer(decisionId: string): void {
    const timer = this.timers.get(decisionId);
    if (timer) {
      clearTimeout(timer);
      this.timers.delete(decisionId);
    }
  }
}

// ── Singleton ─────────────────────────────────────────────────────────

export const safetyPipeline = new SafetyPipeline();
