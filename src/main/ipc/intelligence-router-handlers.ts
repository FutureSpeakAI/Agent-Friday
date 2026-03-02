/**
 * IPC handlers for the Intelligence Routing Layer (Track VII, Phase 1).
 *
 * All handlers are prefixed with 'router:' and follow the same
 * pattern as other handler modules in the ipc/ directory.
 */

import { ipcMain } from 'electron';
import {
  intelligenceRouter,
  classifyTask,
  type TaskProfile,
  type RoutingConfig,
  type ModelCapability,
} from '../intelligence-router';
import { assertString, assertNumber, assertBoolean, assertObject } from './validate';

export function registerIntelligenceRouterHandlers(): void {
  // ── Task Classification & Routing ─────────────────────────────────

  // Crypto Sprint 20: Validate IPC inputs.
  ipcMain.handle(
    'router:classify-task',
    (_event, params: unknown) => {
      assertObject(params, 'router:classify-task params');
      const p = params as Record<string, unknown>;
      assertString(p.messageContent, 'params.messageContent', 100_000);
      assertNumber(p.toolCount, 'params.toolCount', 0, 1_000);
      assertBoolean(p.hasImages, 'params.hasImages');
      assertBoolean(p.hasAudio, 'params.hasAudio');
      assertNumber(p.systemPromptLength, 'params.systemPromptLength', 0, 10_000_000);
      assertNumber(p.conversationLength, 'params.conversationLength', 0, 10_000_000);
      return classifyTask(params as any);
    }
  );

  ipcMain.handle('router:select-model', (_event, task: unknown) => {
    assertObject(task, 'router:select-model task');
    return intelligenceRouter.selectModel(task as unknown as TaskProfile);
  });

  ipcMain.handle(
    'router:classify-and-route',
    (_event, params: unknown) => {
      assertObject(params, 'router:classify-and-route params');
      const p = params as Record<string, unknown>;
      assertString(p.messageContent, 'params.messageContent', 100_000);
      assertNumber(p.toolCount, 'params.toolCount', 0, 1_000);
      assertBoolean(p.hasImages, 'params.hasImages');
      assertBoolean(p.hasAudio, 'params.hasAudio');
      assertNumber(p.systemPromptLength, 'params.systemPromptLength', 0, 10_000_000);
      assertNumber(p.conversationLength, 'params.conversationLength', 0, 10_000_000);
      const task = classifyTask(params as any);
      return intelligenceRouter.selectModel(task);
    }
  );

  // ── Outcome Recording ─────────────────────────────────────────────

  ipcMain.handle(
    'router:record-outcome',
    (_event, decisionId: unknown, outcome: unknown) => {
      assertString(decisionId, 'router:record-outcome decisionId', 500);
      assertObject(outcome, 'router:record-outcome outcome');
      intelligenceRouter.recordOutcome(decisionId as string, outcome as any);
    }
  );

  // ── Model Registry ────────────────────────────────────────────────

  ipcMain.handle('router:get-model', (_event, modelId: unknown) => {
    assertString(modelId, 'router:get-model modelId', 200);
    return intelligenceRouter.getModel(modelId as string);
  });

  ipcMain.handle('router:get-all-models', () => {
    return intelligenceRouter.getAllModels();
  });

  ipcMain.handle('router:get-available-models', () => {
    return intelligenceRouter.getAvailableModels();
  });

  ipcMain.handle('router:register-model', (_event, model: unknown) => {
    assertObject(model, 'router:register-model model');
    intelligenceRouter.registerModel(model as unknown as ModelCapability);
  });

  ipcMain.handle(
    'router:set-model-availability',
    (_event, modelId: unknown, available: unknown) => {
      assertString(modelId, 'router:set-model-availability modelId', 200);
      assertBoolean(available, 'router:set-model-availability available');
      intelligenceRouter.setModelAvailability(modelId as string, available as boolean);
    }
  );

  ipcMain.handle('router:reset-model-failures', (_event, modelId: unknown) => {
    assertString(modelId, 'router:reset-model-failures modelId', 200);
    intelligenceRouter.resetModelFailures(modelId as string);
  });

  // ── Decision History ──────────────────────────────────────────────

  ipcMain.handle('router:get-decision', (_event, id: unknown) => {
    assertString(id, 'router:get-decision id', 500);
    return intelligenceRouter.getDecision(id as string);
  });

  ipcMain.handle('router:get-recent-decisions', (_event, limit?: unknown) => {
    if (limit !== undefined && limit !== null) {
      assertNumber(limit, 'router:get-recent-decisions limit', 1, 10_000);
    }
    return intelligenceRouter.getRecentDecisions(limit as number | undefined);
  });

  ipcMain.handle(
    'router:get-decisions-for-model',
    (_event, modelId: unknown, limit?: unknown) => {
      assertString(modelId, 'router:get-decisions-for-model modelId', 200);
      if (limit !== undefined && limit !== null) {
        assertNumber(limit, 'router:get-decisions-for-model limit', 1, 10_000);
      }
      return intelligenceRouter.getDecisionsForModel(modelId as string, limit as number | undefined);
    }
  );

  // ── Stats & Config ────────────────────────────────────────────────

  ipcMain.handle('router:get-stats', () => {
    return intelligenceRouter.getStats();
  });

  ipcMain.handle('router:get-config', () => {
    return intelligenceRouter.getConfig();
  });

  ipcMain.handle(
    'router:update-config',
    (_event, partial: unknown) => {
      assertObject(partial, 'router:update-config partial');
      return intelligenceRouter.updateConfig(partial as Partial<RoutingConfig>);
    }
  );

  ipcMain.handle('router:get-prompt-context', () => {
    return intelligenceRouter.getPromptContext();
  });
}
