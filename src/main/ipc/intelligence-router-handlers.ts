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

export function registerIntelligenceRouterHandlers(): void {
  // ── Task Classification & Routing ─────────────────────────────────

  ipcMain.handle(
    'router:classify-task',
    (_event, params: {
      messageContent: string;
      toolCount: number;
      hasImages: boolean;
      hasAudio: boolean;
      systemPromptLength: number;
      conversationLength: number;
    }) => {
      return classifyTask(params);
    }
  );

  ipcMain.handle('router:select-model', (_event, task: TaskProfile) => {
    return intelligenceRouter.selectModel(task);
  });

  ipcMain.handle(
    'router:classify-and-route',
    (_event, params: {
      messageContent: string;
      toolCount: number;
      hasImages: boolean;
      hasAudio: boolean;
      systemPromptLength: number;
      conversationLength: number;
    }) => {
      const task = classifyTask(params);
      return intelligenceRouter.selectModel(task);
    }
  );

  // ── Outcome Recording ─────────────────────────────────────────────

  ipcMain.handle(
    'router:record-outcome',
    (_event, decisionId: string, outcome: {
      success: boolean;
      durationMs: number;
      inputTokens?: number;
      outputTokens?: number;
    }) => {
      intelligenceRouter.recordOutcome(decisionId, outcome);
    }
  );

  // ── Model Registry ────────────────────────────────────────────────

  ipcMain.handle('router:get-model', (_event, modelId: string) => {
    return intelligenceRouter.getModel(modelId);
  });

  ipcMain.handle('router:get-all-models', () => {
    return intelligenceRouter.getAllModels();
  });

  ipcMain.handle('router:get-available-models', () => {
    return intelligenceRouter.getAvailableModels();
  });

  ipcMain.handle('router:register-model', (_event, model: ModelCapability) => {
    intelligenceRouter.registerModel(model);
  });

  ipcMain.handle(
    'router:set-model-availability',
    (_event, modelId: string, available: boolean) => {
      intelligenceRouter.setModelAvailability(modelId, available);
    }
  );

  ipcMain.handle('router:reset-model-failures', (_event, modelId: string) => {
    intelligenceRouter.resetModelFailures(modelId);
  });

  // ── Decision History ──────────────────────────────────────────────

  ipcMain.handle('router:get-decision', (_event, id: string) => {
    return intelligenceRouter.getDecision(id);
  });

  ipcMain.handle('router:get-recent-decisions', (_event, limit?: number) => {
    return intelligenceRouter.getRecentDecisions(limit);
  });

  ipcMain.handle(
    'router:get-decisions-for-model',
    (_event, modelId: string, limit?: number) => {
      return intelligenceRouter.getDecisionsForModel(modelId, limit);
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
    (_event, partial: Partial<RoutingConfig>) => {
      return intelligenceRouter.updateConfig(partial);
    }
  );

  ipcMain.handle('router:get-prompt-context', () => {
    return intelligenceRouter.getPromptContext();
  });
}
