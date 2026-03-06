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
import { assertString, assertObject } from './validate';

export function registerExecutionDelegateHandlers(): void {
  // ── tool:execute ─────────────────────────────────────────────────
  ipcMain.handle('tool:execute', async (_event, toolCall: unknown) => {
    assertObject(toolCall, 'tool:execute toolCall');
    const tc = toolCall as Record<string, unknown>;
    assertString(tc.name, 'tool:execute toolCall.name');

    return executionDelegate.execute({
      id: typeof tc.id === 'string' ? tc.id : '',
      type: (tc.type as 'function' | 'tool_use') ?? 'tool_use',
      name: tc.name as string,
      input: tc.input ?? {},
    });
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
        return executionDelegate.executeAfterConfirmation(
          p.decisionId as string,
        );
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
