/**
 * Track B, Phase 3: IPC Handlers for Execution Delegate
 *
 * Channels:
 *   tool:execute          — run a ToolCall through the full pipeline
 *   tool:confirm-response — confirm or deny a pending safety decision
 *   tool:list-tools       — list available tool definitions
 */

import { ipcMain } from 'electron';
import { executionDelegate } from '../execution-delegate';
import { safetyPipeline } from '../safety-pipeline';
import { toolRegistry } from '../tool-registry';
import { liveContextBridge } from '../live-context-bridge';
import { assertString, assertObject } from './validate';

/** Check if a ToolResult came from actual execution (not pending/denied). */
function wasExecuted(result: { content: string | any[]; is_error?: boolean }): boolean {
  if (typeof result.content !== 'string') return true;
  return !result.content.startsWith('Tool execution denied:') &&
         !result.content.startsWith('Tool execution pending');
}

export function registerExecutionDelegateHandlers(): void {
  // ── tool:execute ─────────────────────────────────────────────────
  ipcMain.handle('tool:execute', async (_event, toolCall: unknown) => {
    assertObject(toolCall, 'tool:execute toolCall');
    const tc = toolCall as Record<string, unknown>;
    assertString(tc.name, 'tool:execute toolCall.name');

    const result = await executionDelegate.execute({
      id: typeof tc.id === 'string' ? tc.id : '',
      type: (tc.type as 'function' | 'tool_use') ?? 'tool_use',
      name: tc.name as string,
      input: tc.input ?? {},
    });

    // Feed back to context system (non-blocking, skip pending/denied)
    if (wasExecuted(result)) {
      try { liveContextBridge.feedExecutionResult(result); } catch { /* non-blocking */ }
    }

    return result;
  });

  // ── tool:confirm-response ────────────────────────────────────────
  ipcMain.handle(
    'tool:confirm-response',
    async (_event, payload: unknown) => {
      assertObject(payload, 'tool:confirm-response payload');
      const p = payload as Record<string, unknown>;
      assertString(p.decisionId, 'tool:confirm-response decisionId');

      if (typeof p.approved !== 'boolean') {
        throw new Error(
          'tool:confirm-response: approved must be a boolean',
        );
      }

      if (p.approved) {
        safetyPipeline.confirm(p.decisionId as string);
        const result = await executionDelegate.executeAfterConfirmation(
          p.decisionId as string,
        );

        if (wasExecuted(result)) {
          try { liveContextBridge.feedExecutionResult(result); } catch { /* non-blocking */ }
        }

        return result;
      } else {
        safetyPipeline.deny(p.decisionId as string);
        return {
          tool_use_id: '',
          content: 'Tool execution denied by user',
          is_error: true,
        };
      }
    },
  );

  // ── tool:list-tools ──────────────────────────────────────────────
  ipcMain.handle('tool:list-tools', async () => {
    return toolRegistry.getDefinitions();
  });
}
