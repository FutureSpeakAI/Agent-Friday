/**
 * Tests for ExecutionDelegate — Phase B.3 "The Craftsman"
 *
 * The ExecutionDelegate wires together the full tool execution pipeline:
 *   ToolCall → SafetyPipeline.evaluate() → ToolRegistry.resolve() → handler()
 *
 * Validation criteria covered:
 *   1. execute(toolCall) runs the full pipeline: registry → safety → handler
 *   2. Approved (read-only) tools return ToolResult with handler's output
 *   3. Pending tools emit tool:confirm-request and wait for response
 *   4. Denied tools return ToolResult with error
 *   5. Handler errors are caught and returned as ToolResult with is_error
 *   10. Singleton export
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Hoisted mocks ──────────────────────────────────────────────────

const mocks = vi.hoisted(() => {
  return {
    mockResolve: vi.fn(),
    mockGetDefinitions: vi.fn(() => []),
    mockEvaluate: vi.fn(),
    mockConfirm: vi.fn(),
    mockDeny: vi.fn(),
    mockGetDecision: vi.fn(),
  };
});

vi.mock('../../src/main/tool-registry', () => ({
  toolRegistry: {
    resolve: mocks.mockResolve,
    getDefinitions: mocks.mockGetDefinitions,
  },
}));

vi.mock('../../src/main/safety-pipeline', () => ({
  safetyPipeline: {
    evaluate: mocks.mockEvaluate,
    confirm: mocks.mockConfirm,
    deny: mocks.mockDeny,
    getDecision: mocks.mockGetDecision,
  },
}));

// ── Import under test ─────────────────────────────────────────────

import {
  ExecutionDelegate,
  executionDelegate,
} from '../../src/main/execution-delegate';

import type { ToolCall, ToolResult } from '../../src/main/llm-client';

// ── Helpers ──────────────────────────────────────────────────────────

function makeToolCall(name: string, input: unknown = {}): ToolCall {
  return { id: `tc-${name}`, type: 'tool_use', name, input };
}

// ── Tests ──────────────────────────────────────────────────────────

describe('ExecutionDelegate', () => {
  let delegate: ExecutionDelegate;

  beforeEach(() => {
    vi.clearAllMocks();
    delegate = new ExecutionDelegate();
  });

  // ── Criterion 1: full pipeline execution ──

  it('runs registry lookup → safety check → handler invocation', async () => {
    const handler = vi.fn(async () => 'search result');
    mocks.mockResolve.mockReturnValue(handler);
    mocks.mockEvaluate.mockReturnValue({
      id: 'sd-1',
      status: 'approved',
      toolCall: makeToolCall('file_search'),
      createdAt: Date.now(),
    });

    const result = await delegate.execute(makeToolCall('file_search', { query: 'test' }));

    expect(mocks.mockEvaluate).toHaveBeenCalledTimes(1);
    expect(mocks.mockResolve).toHaveBeenCalledWith('file_search');
    expect(handler).toHaveBeenCalledWith({ query: 'test' });
    expect(result.content).toBe('search result');
    expect(result.is_error).toBeFalsy();
  });

  // ── Criterion 2: approved tools return handler output ──

  it('returns ToolResult with handler output for approved tools', async () => {
    mocks.mockResolve.mockReturnValue(async () => JSON.stringify({ found: 3 }));
    mocks.mockEvaluate.mockReturnValue({
      id: 'sd-1',
      status: 'approved',
      toolCall: makeToolCall('system_stats'),
      createdAt: Date.now(),
    });

    const tc = makeToolCall('system_stats');
    const result = await delegate.execute(tc);

    expect(result.tool_use_id).toBe(tc.id);
    expect(result.content).toBe(JSON.stringify({ found: 3 }));
    expect(result.is_error).toBeFalsy();
  });

  // ── Criterion 4: denied tools return ToolResult with error ──

  it('returns ToolResult with error for denied tools', async () => {
    mocks.mockEvaluate.mockReturnValue({
      id: 'sd-1',
      status: 'denied',
      toolCall: makeToolCall('unknown_tool'),
      reason: 'Unknown tool "unknown_tool"',
      createdAt: Date.now(),
    });

    const result = await delegate.execute(makeToolCall('unknown_tool'));

    expect(result.is_error).toBe(true);
    expect(result.content).toContain('denied');
  });

  // ── Criterion 5: handler errors caught ──

  it('catches handler errors and returns them as ToolResult with is_error', async () => {
    mocks.mockResolve.mockReturnValue(async () => {
      throw new Error('Permission denied');
    });
    mocks.mockEvaluate.mockReturnValue({
      id: 'sd-1',
      status: 'approved',
      toolCall: makeToolCall('file_search'),
      createdAt: Date.now(),
    });

    const result = await delegate.execute(makeToolCall('file_search'));

    expect(result.is_error).toBe(true);
    expect(result.content).toContain('Permission denied');
  });

  it('handles non-Error thrown values', async () => {
    mocks.mockResolve.mockReturnValue(async () => {
      throw 'string error';
    });
    mocks.mockEvaluate.mockReturnValue({
      id: 'sd-1',
      status: 'approved',
      toolCall: makeToolCall('file_search'),
      createdAt: Date.now(),
    });

    const result = await delegate.execute(makeToolCall('file_search'));

    expect(result.is_error).toBe(true);
    expect(result.content).toContain('string error');
  });

  // ── Criterion 3: pending tools produce confirmable result ──

  it('returns pending result for write tools that can be resolved later', async () => {
    mocks.mockEvaluate.mockReturnValue({
      id: 'sd-1',
      status: 'pending',
      toolCall: makeToolCall('write_file'),
      message: 'Tool "write_file" wants to modify data. Confirm to proceed.',
      createdAt: Date.now(),
    });

    const result = await delegate.execute(makeToolCall('write_file'));

    expect(result.is_error).toBe(true);
    expect(result.content).toContain('pending');
    expect(result.content).toContain('sd-1');
  });

  // ── Confirmation flow ──

  it('executeAfterConfirmation runs handler when decision is confirmed', async () => {
    const handler = vi.fn(async () => 'written successfully');
    mocks.mockResolve.mockReturnValue(handler);
    mocks.mockGetDecision.mockReturnValue({
      id: 'sd-1',
      status: 'approved',
      toolCall: makeToolCall('write_file', { path: '/test' }),
      createdAt: Date.now(),
    });

    const result = await delegate.executeAfterConfirmation('sd-1');

    expect(mocks.mockGetDecision).toHaveBeenCalledWith('sd-1');
    expect(handler).toHaveBeenCalledWith({ path: '/test' });
    expect(result.content).toBe('written successfully');
    expect(result.is_error).toBeFalsy();
  });

  it('executeAfterConfirmation returns error for denied decision', async () => {
    mocks.mockGetDecision.mockReturnValue({
      id: 'sd-1',
      status: 'denied',
      toolCall: makeToolCall('write_file'),
      reason: 'User denied',
      createdAt: Date.now(),
    });

    const result = await delegate.executeAfterConfirmation('sd-1');

    expect(result.is_error).toBe(true);
    expect(result.content).toContain('denied');
  });

  it('executeAfterConfirmation returns error for unknown decision', async () => {
    mocks.mockGetDecision.mockReturnValue(undefined);

    const result = await delegate.executeAfterConfirmation('sd-nonexistent');

    expect(result.is_error).toBe(true);
    expect(result.content).toContain('not found');
  });

  it('executeAfterConfirmation returns error for still-pending decision', async () => {
    mocks.mockGetDecision.mockReturnValue({
      id: 'sd-1',
      status: 'pending',
      toolCall: makeToolCall('write_file'),
      createdAt: Date.now(),
    });

    const result = await delegate.executeAfterConfirmation('sd-1');

    expect(result.is_error).toBe(true);
    expect(result.content).toContain('pending');
  });

  // ── Criterion 10: singleton export ──

  it('exports a singleton instance', () => {
    expect(executionDelegate).toBeInstanceOf(ExecutionDelegate);
  });
});
