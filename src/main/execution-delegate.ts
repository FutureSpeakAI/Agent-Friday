/**
 * Track B, Phase 3: "The Craftsman" — Execution Delegate
 *
 * Wires together the full tool execution pipeline:
 *   ToolCall → SafetyPipeline.evaluate() → ToolRegistry.resolve() → handler()
 *
 * Three outcomes:
 *   - approved → execute handler, return ToolResult with output
 *   - pending  → return pending ToolResult with decisionId for later confirmation
 *   - denied   → return ToolResult with error
 *
 * Handler errors are always caught and returned as ToolResult with is_error.
 * The delegate never throws — the caller always gets a ToolResult.
 *
 * Hermeneutic note: The craftsman is the synthesis. The registry catalogs
 * capabilities (parts), the pipeline gates them (context), and here
 * understanding becomes action — the whole working through all parts.
 */

import type { ToolCall, ToolResult } from './llm-client';
import { toolRegistry } from './tool-registry';
import { safetyPipeline } from './safety-pipeline';

// ── ExecutionDelegate Class ────────────────────────────────────────────

export class ExecutionDelegate {
  /**
   * Execute a tool call through the full pipeline.
   *
   * Flow: evaluate safety → if approved, resolve handler → invoke handler
   *       if pending, return pending result with decisionId
   *       if denied, return error result
   */
  async execute(toolCall: ToolCall): Promise<ToolResult> {
    // 1. Safety check
    const decision = safetyPipeline.evaluate(toolCall);

    // 2. Branch on decision status
    if (decision.status === 'denied') {
      return {
        tool_use_id: toolCall.id,
        content: `Tool execution denied: ${decision.reason ?? 'Safety policy violation'}`,
        is_error: true,
      };
    }

    if (decision.status === 'pending') {
      return {
        tool_use_id: toolCall.id,
        content: `Tool execution pending confirmation (decisionId: ${decision.id}). ${decision.message ?? ''}`,
        is_error: true,
      };
    }

    // 3. Approved — resolve and execute
    return this.runHandler(toolCall);
  }

  /**
   * Execute a tool call after confirmation has been received.
   *
   * Looks up the decision by ID. If approved, resolves and runs the handler.
   * If denied or still pending, returns an error result.
   */
  async executeAfterConfirmation(decisionId: string): Promise<ToolResult> {
    const decision = safetyPipeline.getDecision(decisionId);

    if (!decision) {
      return {
        tool_use_id: '',
        content: `Decision "${decisionId}" not found`,
        is_error: true,
      };
    }

    if (decision.status === 'pending') {
      return {
        tool_use_id: decision.toolCall.id,
        content: `Decision "${decisionId}" is still pending confirmation`,
        is_error: true,
      };
    }

    if (decision.status === 'denied') {
      return {
        tool_use_id: decision.toolCall.id,
        content: `Tool execution denied: ${decision.reason ?? 'User denied'}`,
        is_error: true,
      };
    }

    // Approved — run the handler
    return this.runHandler(decision.toolCall);
  }

  // ── Private ──────────────────────────────────────────────────────────

  private async runHandler(toolCall: ToolCall): Promise<ToolResult> {
    try {
      const handler = toolRegistry.resolve(toolCall.name);
      const output = await handler(toolCall.input);
      return {
        tool_use_id: toolCall.id,
        content: output,
      };
    } catch (err) {
      return {
        tool_use_id: toolCall.id,
        content: `Tool execution error: ${err instanceof Error ? err.message : String(err)}`,
        is_error: true,
      };
    }
  }
}

// ── Singleton ──────────────────────────────────────────────────────────

export const executionDelegate = new ExecutionDelegate();
