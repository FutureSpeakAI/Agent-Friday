/**
 * Track D, Phase 3: "The Feedback Wire" — Execution → Context Loop Tests
 *
 * Validates that tool execution results are fed back into the context
 * system via liveContextBridge.feedExecutionResult(), closing the
 * action→understanding loop of the hermeneutic circle.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  ipcMainHandle: vi.fn(),
  executionDelegateExecute: vi.fn(),
  executionDelegateExecuteAfterConfirmation: vi.fn(),
  safetyPipelineConfirm: vi.fn(),
  safetyPipelineDeny: vi.fn(),
  toolRegistryGetDefinitions: vi.fn(() => []),
  feedExecutionResult: vi.fn(),
}));

vi.mock('electron', () => ({
  ipcMain: {
    handle: mocks.ipcMainHandle,
  },
}));

vi.mock('../../src/main/execution-delegate', () => ({
  executionDelegate: {
    execute: mocks.executionDelegateExecute,
    executeAfterConfirmation: mocks.executionDelegateExecuteAfterConfirmation,
  },
}));

vi.mock('../../src/main/safety-pipeline', () => ({
  safetyPipeline: {
    confirm: mocks.safetyPipelineConfirm,
    deny: mocks.safetyPipelineDeny,
  },
}));

vi.mock('../../src/main/tool-registry', () => ({
  toolRegistry: {
    getDefinitions: mocks.toolRegistryGetDefinitions,
  },
}));

vi.mock('../../src/main/live-context-bridge', () => ({
  liveContextBridge: {
    feedExecutionResult: mocks.feedExecutionResult,
  },
}));

describe('Feedback Wire — Execution → Context Loop', () => {
  let executeHandler: (...args: any[]) => Promise<any>;
  let confirmHandler: (...args: any[]) => Promise<any>;

  beforeEach(async () => {
    vi.clearAllMocks();

    const { registerExecutionDelegateHandlers } = await import(
      '../../src/main/ipc/execution-delegate-handlers'
    );
    registerExecutionDelegateHandlers();

    // Extract the registered handlers by channel name
    const calls = mocks.ipcMainHandle.mock.calls;
    executeHandler = calls.find((c: any[]) => c[0] === 'tool:execute')![1];
    confirmHandler = calls.find(
      (c: any[]) => c[0] === 'tool:confirm-response',
    )![1];
  });

  const mockEvent = {} as any;
  const toolCall = { id: 'tc-1', type: 'tool_use', name: 'test-tool', input: {} };

  // D.3 Validation Criterion 1: successful result feeds back
  it('feeds successful execution result to liveContextBridge', async () => {
    const result = { tool_use_id: 'tc-1', content: 'Success output' };
    mocks.executionDelegateExecute.mockResolvedValue(result);

    await executeHandler(mockEvent, toolCall);

    expect(mocks.feedExecutionResult).toHaveBeenCalledWith(result);
  });

  // D.3 Validation Criterion 2: error result (handler threw) feeds back
  it('feeds execution error result to liveContextBridge', async () => {
    const result = {
      tool_use_id: 'tc-1',
      content: 'Tool execution error: something broke',
      is_error: true,
    };
    mocks.executionDelegateExecute.mockResolvedValue(result);

    await executeHandler(mockEvent, toolCall);

    expect(mocks.feedExecutionResult).toHaveBeenCalledWith(result);
  });

  // D.3 Validation Criterion 6: denied result does NOT feed back
  it('does NOT feed denied result to liveContextBridge', async () => {
    const result = {
      tool_use_id: 'tc-1',
      content: 'Tool execution denied: Safety policy violation',
      is_error: true,
    };
    mocks.executionDelegateExecute.mockResolvedValue(result);

    await executeHandler(mockEvent, toolCall);

    expect(mocks.feedExecutionResult).not.toHaveBeenCalled();
  });

  // D.3 Validation Criterion 7: pending result does NOT feed back
  it('does NOT feed pending result to liveContextBridge', async () => {
    const result = {
      tool_use_id: 'tc-1',
      content: 'Tool execution pending confirmation (decisionId: sd-1). Please review.',
      is_error: true,
    };
    mocks.executionDelegateExecute.mockResolvedValue(result);

    await executeHandler(mockEvent, toolCall);

    expect(mocks.feedExecutionResult).not.toHaveBeenCalled();
  });

  // D.3: Confirmation approved → execution → feedback
  it('feeds confirmed execution result to liveContextBridge', async () => {
    const result = { tool_use_id: 'tc-1', content: 'Confirmed output' };
    mocks.executionDelegateExecuteAfterConfirmation.mockResolvedValue(result);

    await confirmHandler(mockEvent, {
      decisionId: 'sd-1',
      approved: true,
    });

    expect(mocks.feedExecutionResult).toHaveBeenCalledWith(result);
  });

  // D.3: Confirmation denied → no execution → no feedback
  it('does NOT feed user-denied result to liveContextBridge', async () => {
    await confirmHandler(mockEvent, {
      decisionId: 'sd-1',
      approved: false,
    });

    expect(mocks.feedExecutionResult).not.toHaveBeenCalled();
  });

  // D.3 Validation Criterion 3: feedback is non-blocking (result returned regardless)
  it('returns result to renderer even if feedback fails', async () => {
    const result = { tool_use_id: 'tc-1', content: 'Success output' };
    mocks.executionDelegateExecute.mockResolvedValue(result);
    mocks.feedExecutionResult.mockImplementation(() => {
      throw new Error('feedback exploded');
    });

    const returned = await executeHandler(mockEvent, toolCall);

    expect(returned).toEqual(result);
  });
});
