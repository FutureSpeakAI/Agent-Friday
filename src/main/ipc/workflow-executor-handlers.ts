/**
 * IPC handlers for Workflow Executor — Track V Phase 2.
 * Exposes execution control, standing permissions, run queries,
 * and user interaction APIs to the renderer.
 *
 * cLaw Gate: Scheduled workflows with destructive actions MUST have
 * a standing permission explicitly granted by the user. The executor
 * never infers consent. Standing permission grants flow through here
 * and are always user-initiated.
 */

import { ipcMain } from 'electron';
import { workflowExecutor } from '../workflow-executor';
import type { ExecutorConfig } from '../workflow-executor';

export function registerWorkflowExecutorHandlers(): void {
  // ── Execution Control ────────────────────────────────────────────

  ipcMain.handle(
    'wf-exec:execute',
    (_event, templateId: string, params?: Record<string, string>, triggeredBy?: string) => {
      if (typeof templateId !== 'string' || !templateId.trim()) {
        throw new Error('wf-exec:execute requires a non-empty templateId');
      }
      const trigger = (['user', 'schedule', 'api'].includes(triggeredBy || '')
        ? triggeredBy
        : 'user') as 'user' | 'schedule' | 'api';
      return workflowExecutor.executeWorkflow(
        templateId.trim(),
        params || {},
        trigger,
      );
    },
  );

  ipcMain.handle('wf-exec:pause', () => {
    return workflowExecutor.pauseExecution();
  });

  ipcMain.handle('wf-exec:resume', () => {
    return workflowExecutor.resumeExecution();
  });

  ipcMain.handle('wf-exec:cancel', () => {
    return workflowExecutor.cancelExecution();
  });

  ipcMain.handle('wf-exec:provide-user-response', (_event, response: string) => {
    if (typeof response !== 'string') {
      throw new Error('wf-exec:provide-user-response requires a string response');
    }
    workflowExecutor.provideUserResponse(response);
    return true;
  });

  // ── Standing Permissions (cLaw Gate) ─────────────────────────────

  ipcMain.handle(
    'wf-exec:grant-permission',
    (_event, templateId: string, opts?: {
      allowDestructive?: boolean;
      maxRuns?: number;
      expiresInDays?: number;
    }) => {
      if (typeof templateId !== 'string' || !templateId.trim()) {
        throw new Error('wf-exec:grant-permission requires a non-empty templateId');
      }
      return workflowExecutor.grantStandingPermission(templateId.trim(), opts || {});
    },
  );

  ipcMain.handle('wf-exec:revoke-permission', (_event, templateId: string) => {
    if (typeof templateId !== 'string' || !templateId.trim()) {
      throw new Error('wf-exec:revoke-permission requires a non-empty templateId');
    }
    return workflowExecutor.revokeStandingPermission(templateId.trim());
  });

  ipcMain.handle('wf-exec:get-permissions', () => {
    return workflowExecutor.getStandingPermissions();
  });

  // ── Queries ──────────────────────────────────────────────────────

  ipcMain.handle('wf-exec:active-run', () => {
    return workflowExecutor.getActiveRun();
  });

  ipcMain.handle('wf-exec:is-running', () => {
    return workflowExecutor.isRunning();
  });

  ipcMain.handle('wf-exec:run-history', (_event, limit?: number) => {
    return workflowExecutor.getRunHistory(
      typeof limit === 'number' ? limit : 20,
    );
  });

  ipcMain.handle('wf-exec:get-run', (_event, runId: string) => {
    if (typeof runId !== 'string' || !runId.trim()) {
      throw new Error('wf-exec:get-run requires a non-empty runId');
    }
    return workflowExecutor.getRunById(runId.trim());
  });

  // ── Configuration ────────────────────────────────────────────────

  ipcMain.handle('wf-exec:get-config', () => {
    return workflowExecutor.getConfig();
  });

  ipcMain.handle('wf-exec:update-config', (_event, updates: Partial<ExecutorConfig>) => {
    if (typeof updates !== 'object' || updates === null) {
      throw new Error('wf-exec:update-config requires an object');
    }
    workflowExecutor.updateConfig(updates);
    return workflowExecutor.getConfig();
  });
}
