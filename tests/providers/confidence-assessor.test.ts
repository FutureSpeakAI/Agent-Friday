/**
 * confidence-assessor.test.ts — Tests for ConfidenceAssessor (Phase H.1)
 *
 * Validates the pure-function confidence assessment of LLM responses
 * using structural signals (no ML inference involved).
 */

import { describe, it, expect } from 'vitest';
import {
  assessConfidence,
  ConfidenceResult,
  ConfidenceSignal,
} from '../../src/main/confidence-assessor';
import type {
  LLMRequest,
  LLMResponse,
  ToolDefinition,
  ToolCall,
} from '../../src/main/llm-client';

// ── Helpers ────────────────────────────────────────────────────────────

function makeRequest(overrides: Partial<LLMRequest> = {}): LLMRequest {
  return {
    messages: [{ role: 'user', content: 'What is the weather today?' }],
    ...overrides,
  };
}

function makeResponse(overrides: Partial<LLMResponse> = {}): LLMResponse {
  return {
    content: 'The weather today is sunny with a high of 72F.',
    toolCalls: [],
    usage: { inputTokens: 20, outputTokens: 40 },
    model: 'test-model',
    provider: 'ollama' as LLMResponse['provider'],
    stopReason: 'end_turn',
    latencyMs: 100,
    ...overrides,
  };
}

function makeToolDefs(names: string[]): ToolDefinition[] {
  return names.map((name) => ({
    name,
    description: `Tool: ${name}`,
  }));
}

function makeToolCall(overrides: Partial<ToolCall> = {}): ToolCall {
  return {
    id: 'call_001',
    type: 'function',
    name: 'get_weather',
    input: { location: 'NYC' },
    ...overrides,
  };
}

// ── Tests ──────────────────────────────────────────────────────────────

describe('ConfidenceAssessor', () => {
  // 1. assessConfidence is a pure function (stateless, no singleton)
  it('is a pure function — same inputs always produce same outputs', () => {
    const req = makeRequest();
    const res = makeResponse();
    const result1 = assessConfidence(req, res);
    const result2 = assessConfidence(req, res);
    expect(result1).toEqual(result2);
    expect(typeof assessConfidence).toBe('function');
  });

  // 2. assess(request, response) returns ConfidenceResult with score 0-1
  it('returns a ConfidenceResult with score in [0, 1]', () => {
    const result = assessConfidence(makeRequest(), makeResponse());
    expect(result).toHaveProperty('score');
    expect(result).toHaveProperty('signals');
    expect(result).toHaveProperty('escalate');
    expect(result.score).toBeGreaterThanOrEqual(0);
    expect(result.score).toBeLessThanOrEqual(1);
    expect(Array.isArray(result.signals)).toBe(true);
    expect(typeof result.escalate).toBe('boolean');
  });

  // 3. Valid tool calls with matching names -> high confidence (> 0.8)
  it('returns high confidence for valid tool calls matching definitions', () => {
    const tools = makeToolDefs(['get_weather', 'search']);
    const req = makeRequest({ tools });
    const res = makeResponse({
      content: '',
      toolCalls: [makeToolCall({ name: 'get_weather', input: { location: 'NYC' } })],
      stopReason: 'tool_use',
    });
    const result = assessConfidence(req, res, tools);
    expect(result.score).toBeGreaterThan(0.8);
    expect(result.escalate).toBe(false);
  });

  // 4. Malformed tool call JSON -> low confidence (<= 0.3), signal: 'malformed-tool-call'
  it('returns low confidence for malformed tool call arguments', () => {
    const tools = makeToolDefs(['get_weather']);
    const req = makeRequest({ tools });
    const badToolCall: ToolCall = {
      id: 'call_002',
      type: 'function',
      name: 'get_weather',
      input: 'not valid json {{{' as unknown as Record<string, unknown>,
    };
    const res = makeResponse({
      content: '',
      toolCalls: [badToolCall],
      stopReason: 'tool_use',
    });
    const result = assessConfidence(req, res, tools);
    expect(result.score).toBeLessThanOrEqual(0.3);
    const malformedSignal = result.signals.find((s) => s.name === 'malformed-tool-call');
    expect(malformedSignal).toBeDefined();
    expect(malformedSignal!.weight).toBe(-0.7);
  });

  // 5. Tool call referencing non-existent tool name -> low confidence, signal: 'unknown-tool'
  it('returns low confidence for tool call with unknown tool name', () => {
    const tools = makeToolDefs(['search', 'calculate']);
    const req = makeRequest({ tools });
    const res = makeResponse({
      content: '',
      toolCalls: [makeToolCall({ name: 'nonexistent_tool' })],
      stopReason: 'tool_use',
    });
    const result = assessConfidence(req, res, tools);
    expect(result.score).toBeLessThanOrEqual(0.5);
    const unknownSignal = result.signals.find((s) => s.name === 'unknown-tool');
    expect(unknownSignal).toBeDefined();
    expect(unknownSignal!.weight).toBe(-0.5);
  });

  // 6. Response with stopReason: 'max_tokens' -> medium confidence, signal: 'truncated'
  it('flags truncated responses with stopReason max_tokens', () => {
    const req = makeRequest();
    const res = makeResponse({ stopReason: 'max_tokens' });
    const result = assessConfidence(req, res);
    expect(result.score).toBeLessThanOrEqual(0.7);
    const truncatedSignal = result.signals.find((s) => s.name === 'truncated');
    expect(truncatedSignal).toBeDefined();
    expect(truncatedSignal!.weight).toBe(-0.3);
  });

  // 7. Empty content with no tool calls -> low confidence, signal: 'empty-response'
  it('returns low confidence for empty response with no tool calls', () => {
    const req = makeRequest();
    const res = makeResponse({ content: '', toolCalls: [] });
    const result = assessConfidence(req, res);
    expect(result.score).toBeLessThan(0.3);
    const emptySignal = result.signals.find((s) => s.name === 'empty-response');
    expect(emptySignal).toBeDefined();
    expect(emptySignal!.weight).toBe(-0.8);
  });

  // 8. Response shorter than expected -> medium confidence, signal: 'unexpectedly-brief'
  it('flags unexpectedly brief responses for non-trivial requests', () => {
    const req = makeRequest({
      messages: [{ role: 'user', content: 'Explain the theory of relativity in detail.' }],
    });
    const res = makeResponse({ content: 'E=mc2' });
    const result = assessConfidence(req, res);
    expect(result.score).toBeLessThanOrEqual(0.8);
    const briefSignal = result.signals.find((s) => s.name === 'unexpectedly-brief');
    expect(briefSignal).toBeDefined();
    expect(briefSignal!.weight).toBe(-0.2);
  });

  // 9. escalate is true when score < configurable threshold (default 0.5)
  it('sets escalate=true when score falls below default threshold of 0.5', () => {
    const req = makeRequest();
    const res = makeResponse({ content: '', toolCalls: [] });
    const result = assessConfidence(req, res);
    expect(result.score).toBeLessThan(0.5);
    expect(result.escalate).toBe(true);
  });

  // 10. Threshold is configurable via options parameter
  it('allows configurable threshold via options parameter', () => {
    const req = makeRequest();
    const res = makeResponse({ stopReason: 'max_tokens' });

    const highThreshold = assessConfidence(req, res, undefined, { threshold: 0.9 });
    expect(highThreshold.escalate).toBe(true);

    const lowThreshold = assessConfidence(req, res, undefined, { threshold: 0.1 });
    expect(lowThreshold.escalate).toBe(false);
  });
});
