/**
 * Tests for Execution Delegate IPC Handlers — Phase B.3 "The Craftsman"
 *
 * Validation criteria covered:
 *   6. tool:execute IPC handler accepts ToolCall and returns ToolResult
 *   7. tool:confirm-response IPC handler accepts { decisionId, approved }
 *   8. tool:list-tools IPC handler returns available tool definitions
 *   9. All IPC inputs are validated
 *   10. Handler registration follows registerXxxHandlers() pattern
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Hoisted mocks ──────────────────────────────────────────────────

const mocks = vi.hoisted(() => {
  const handlers = new Map<string, (...args: any[]) => any>();
  return {
    handlers,
    mockIpcMain: {
      handle: vi.fn((channel: string, handler: (...args: any[]) => any) => {
        handlers.set(channel, handler);
      }),
    },
    mockExecute: vi.fn(),
    mockExecuteAfterConfirmation: vi.fn(),
    mockConfirm: vi.fn(),
    mockDeny: vi.fn(),
    mockGetDefinitions: vi.fn(() => []),
  };
});

vi.mock('electron', () => ({
  ipcMain: mocks.mockIpcMain,
}));

vi.mock('../../src/main/execution-delegate', () => ({
  executionDelegate: {
    execute: mocks.mockExecute,
    executeAfterConfirmation: mocks.mockExecuteAfterConfirmation,
  },
}));

vi.mock('../../src/main/safety-pipeline', () => ({
  safetyPipeline: {
    confirm: mocks.mockConfirm,
    deny: mocks.mockDeny,
  },
}));

vi.mock('../../src/main/tool-registry', () => ({
  toolRegistry: {
    getDefinitions: mocks.mockGetDefinitions,
  },
}));

// ── Import under test ─────────────────────────────────────────────

import { registerExecutionDelegateHandlers } from '../../src/main/ipc/execution-delegate-handlers';

// ── Tests ──────────────────────────────────────────────────────────

describe('ExecutionDelegate IPC Handlers', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.handlers.clear();
    registerExecutionDelegateHandlers();
  });

  // ── Criterion 10: registration pattern ──

  it('registers expected IPC channels', () => {
    expect(mocks.handlers.has('tool:execute')).toBe(true);
    expect(mocks.handlers.has('tool:confirm-response')).toBe(true);
    expect(mocks.handlers.has('tool:list-tools')).toBe(true);
  });

  // ── Criterion 6: tool:execute ──

  it('tool:execute delegates to executionDelegate.execute()', async () => {
    const mockResult = { tool_use_id: 'tc-1', content: 'ok', is_error: false };
    mocks.mockExecute.mockResolvedValue(mockResult);

    const handler = mocks.handlers.get('tool:execute')!;
    const result = await handler(
      {},
      { id: 'tc-1', type: 'tool_use', name: 'file_search', input: { query: 'test' } },
    );

    expect(mocks.mockExecute).toHaveBeenCalledWith({
      id: 'tc-1',
      type: 'tool_use',
      name: 'file_search',
      input: { query: 'test' },
    });
    expect(result).toEqual(mockResult);
  });

  // ── Criterion 9: input validation for tool:execute ──

  it('tool:execute rejects non-object input', async () => {
    const handler = mocks.handlers.get('tool:execute')!;
    await expect(handler({}, 'not-an-object')).rejects.toThrow();
  });

  it('tool:execute rejects missing toolCall name', async () => {
    const handler = mocks.handlers.get('tool:execute')!;
    await expect(
      handler({}, { id: 'tc-1', type: 'tool_use', input: {} }),
    ).rejects.toThrow();
  });

  // ── Criterion 7: tool:confirm-response ──

  it('tool:confirm-response confirms decision when approved=true', async () => {
    mocks.mockConfirm.mockReturnValue(true);
    mocks.mockExecuteAfterConfirmation.mockResolvedValue({
      tool_use_id: 'tc-1',
      content: 'done',
    });

    const handler = mocks.handlers.get('tool:confirm-response')!;
    const result = await handler({}, { decisionId: 'sd-1', approved: true });

    expect(mocks.mockConfirm).toHaveBeenCalledWith('sd-1');
    expect(mocks.mockExecuteAfterConfirmation).toHaveBeenCalledWith('sd-1');
    expect(result.content).toBe('done');
  });

  it('tool:confirm-response denies decision when approved=false', async () => {
    mocks.mockDeny.mockReturnValue(true);

    const handler = mocks.handlers.get('tool:confirm-response')!;
    const result = await handler({}, { decisionId: 'sd-1', approved: false });

    expect(mocks.mockDeny).toHaveBeenCalledWith('sd-1');
    expect(result.is_error).toBe(true);
    expect(result.content).toContain('denied');
  });

  // ── Criterion 9: input validation for tool:confirm-response ──

  it('tool:confirm-response rejects non-string decisionId', async () => {
    const handler = mocks.handlers.get('tool:confirm-response')!;
    await expect(
      handler({}, { decisionId: 123, approved: true }),
    ).rejects.toThrow();
  });

  it('tool:confirm-response rejects missing approved field', async () => {
    const handler = mocks.handlers.get('tool:confirm-response')!;
    await expect(
      handler({}, { decisionId: 'sd-1' }),
    ).rejects.toThrow();
  });

  // ── Criterion 8: tool:list-tools ──

  it('tool:list-tools returns tool definitions', async () => {
    const defs = [
      { name: 'file_search', description: 'Search files', safetyLevel: 'read-only' },
      { name: 'write_file', description: 'Write file', safetyLevel: 'write' },
    ];
    mocks.mockGetDefinitions.mockReturnValue(defs);

    const handler = mocks.handlers.get('tool:list-tools')!;
    const result = await handler({});

    expect(mocks.mockGetDefinitions).toHaveBeenCalled();
    expect(result).toEqual(defs);
  });
});
