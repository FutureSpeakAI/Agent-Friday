/**
 * confidence-assessor.ts — Pure functions that evaluate LLM response quality
 * using structural signals (Phase H.1: "The Mirror").
 *
 * No ML inference is performed here; this module inspects structural
 * properties of the LLMResponse (tool call validity, truncation,
 * emptiness, brevity) to produce a confidence score.
 */

import type { LLMRequest, LLMResponse, ToolDefinition, ToolCall } from './llm-client';

// ── Types ──────────────────────────────────────────────────────────────

export interface ConfidenceSignal {
  /** Signal identifier, e.g. 'malformed-tool-call', 'truncated' */
  name: string;
  /** Impact on score (negative value between -1 and 0) */
  weight: number;
  /** Human-readable explanation */
  detail?: string;
}

export interface ConfidenceResult {
  /** Overall confidence score in [0, 1], where 1 = fully confident */
  score: number;
  /** Individual signals that contributed to the score */
  signals: ConfidenceSignal[];
  /** True when score < threshold, indicating the response may need review */
  escalate: boolean;
}

// ── Signal Weights ─────────────────────────────────────────────────────

const WEIGHTS = {
  MALFORMED_TOOL_CALL: -0.7,
  UNKNOWN_TOOL: -0.5,
  TRUNCATED: -0.3,
  EMPTY_RESPONSE: -0.8,
  UNEXPECTEDLY_BRIEF: -0.2,
} as const;

const DEFAULT_THRESHOLD = 0.5;
const BRIEF_CONTENT_THRESHOLD = 20;

// ── Internal Signal Checkers ───────────────────────────────────────────

function checkToolCallValidity(
  toolCalls: ToolCall[],
  toolDefs?: ToolDefinition[],
): ConfidenceSignal[] {
  const signals: ConfidenceSignal[] = [];
  const knownNames = new Set(toolDefs?.map((t) => t.name) ?? []);

  for (const call of toolCalls) {
    // Check for malformed input — input should be a plain object, not a string or primitive
    if (call.input !== null && call.input !== undefined && typeof call.input !== 'object') {
      signals.push({
        name: 'malformed-tool-call',
        weight: WEIGHTS.MALFORMED_TOOL_CALL,
        detail: `Tool call "${call.name}" has non-object input: ${typeof call.input}`,
      });
    }

    // Check for unknown tool name (only if tool definitions were provided)
    if (toolDefs && toolDefs.length > 0 && !knownNames.has(call.name)) {
      signals.push({
        name: 'unknown-tool',
        weight: WEIGHTS.UNKNOWN_TOOL,
        detail: `Tool "${call.name}" is not in the provided tool definitions`,
      });
    }
  }

  return signals;
}

function checkTruncation(response: LLMResponse): ConfidenceSignal[] {
  if (response.stopReason === 'max_tokens') {
    return [
      {
        name: 'truncated',
        weight: WEIGHTS.TRUNCATED,
        detail: 'Response was truncated due to max_tokens limit',
      },
    ];
  }
  return [];
}

function checkEmptyResponse(response: LLMResponse): ConfidenceSignal[] {
  const hasContent = response.content && response.content.trim().length > 0;
  const hasToolCalls = response.toolCalls && response.toolCalls.length > 0;

  if (!hasContent && !hasToolCalls) {
    return [
      {
        name: 'empty-response',
        weight: WEIGHTS.EMPTY_RESPONSE,
        detail: 'Response contains no content and no tool calls',
      },
    ];
  }
  return [];
}

function checkBrevity(request: LLMRequest, response: LLMResponse): ConfidenceSignal[] {
  // Only check brevity if response has content (tool-use responses can be empty)
  if (!response.content || response.content.trim().length === 0) {
    return [];
  }

  // Skip brevity check if response has tool calls (tool-use is expected to be brief)
  if (response.toolCalls && response.toolCalls.length > 0) {
    return [];
  }

  if (response.content.length < BRIEF_CONTENT_THRESHOLD) {
    return [
      {
        name: 'unexpectedly-brief',
        weight: WEIGHTS.UNEXPECTEDLY_BRIEF,
        detail: `Response is only ${response.content.length} chars (threshold: ${BRIEF_CONTENT_THRESHOLD})`,
      },
    ];
  }

  return [];
}

// ── Main Export ────────────────────────────────────────────────────────

/**
 * Assess the confidence of an LLM response using structural signals.
 *
 * This is a pure function — it has no side effects and always returns
 * the same result for the same inputs.
 *
 * @param request  - The original LLM request
 * @param response - The LLM response to evaluate
 * @param tools    - Optional tool definitions for validating tool calls
 * @param options  - Optional configuration (e.g., escalation threshold)
 * @returns ConfidenceResult with score, signals, and escalation flag
 */
export function assessConfidence(
  request: LLMRequest,
  response: LLMResponse,
  tools?: ToolDefinition[],
  options?: { threshold?: number },
): ConfidenceResult {
  const threshold = options?.threshold ?? DEFAULT_THRESHOLD;

  // Collect all signals
  const signals: ConfidenceSignal[] = [
    ...checkEmptyResponse(response),
    ...checkTruncation(response),
    ...checkToolCallValidity(response.toolCalls ?? [], tools),
    ...checkBrevity(request, response),
  ];

  // Calculate score: start at 1.0, sum negative weights, clamp to [0, 1]
  // Round to 10 decimal places to avoid floating-point artifacts
  const totalPenalty = signals.reduce((sum, signal) => sum + signal.weight, 0);
  const rawScore = 1.0 + totalPenalty;
  const score = Math.max(0, Math.min(1, Math.round(rawScore * 1e10) / 1e10));

  return {
    score,
    signals,
    escalate: score < threshold,
  };
}
