/**
 * Tool IPC handlers — desktop tools, browser, screen capture, scheduler, ambient, sentiment, predictor.
 */
import { ipcMain, BrowserWindow } from 'electron';
import { DESKTOP_TOOL_DECLARATIONS, callDesktopTool } from '../desktop-tools';
import { BROWSER_TOOL_DECLARATIONS, executeBrowserTool } from '../browser';
import { screenCapture } from '../screen-capture';
import { taskScheduler, SCHEDULER_TOOL_DECLARATIONS } from '../scheduler';
import { ambientEngine } from '../ambient';
import { sentimentEngine } from '../sentiment';
import { predictor } from '../predictor';

export interface ToolHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerToolHandlers(deps: ToolHandlerDeps): void {
  // ── Desktop tools ───────────────────────────────────────────────────
  ipcMain.handle('desktop:list-tools', () => DESKTOP_TOOL_DECLARATIONS);

  ipcMain.handle(
    'desktop:call-tool',
    async (_event, toolName: string, args: Record<string, unknown>) => {
      return callDesktopTool(toolName, args);
    },
  );

  ipcMain.handle('desktop:focus-window', async (_event, target: string) => {
    return callDesktopTool('focus_window', { target });
  });

  ipcMain.handle('desktop:confirm-response', (_event, id: string, approved: boolean) => {
    const { handleConfirmationResponse } = require('../desktop-tools');
    handleConfirmationResponse(id, approved);
  });

  // ── Browser tools ───────────────────────────────────────────────────
  ipcMain.handle('browser:list-tools', () => BROWSER_TOOL_DECLARATIONS);

  ipcMain.handle(
    'browser:call-tool',
    async (_event, toolName: string, args: Record<string, unknown>) => {
      return executeBrowserTool(toolName, args);
    },
  );

  // ── Screen capture ──────────────────────────────────────────────────
  ipcMain.handle('screen-capture:start', () => {
    const win = deps.getMainWindow();
    if (win) screenCapture.start(win);
  });

  ipcMain.handle('screen-capture:stop', () => screenCapture.stop());

  // ── Scheduler ───────────────────────────────────────────────────────
  ipcMain.handle('scheduler:list-tools', () => SCHEDULER_TOOL_DECLARATIONS);

  ipcMain.handle(
    'scheduler:create-task',
    async (_event, params: Record<string, unknown>) => {
      return taskScheduler.createTask(params as any);
    },
  );

  ipcMain.handle('scheduler:list-tasks', () => taskScheduler.listTasks());

  ipcMain.handle('scheduler:delete-task', async (_event, id: string) => {
    return taskScheduler.deleteTask(id);
  });

  // ── Ambient context ─────────────────────────────────────────────────
  ipcMain.handle('ambient:get-state', () => ambientEngine.getState());
  ipcMain.handle('ambient:get-context-string', () => ambientEngine.getContextString());

  // ── Sentiment ───────────────────────────────────────────────────────
  ipcMain.handle('sentiment:analyse', (_event, text: string) => sentimentEngine.analyse(text));
  ipcMain.handle('sentiment:get-state', () => sentimentEngine.getState());
  ipcMain.handle('sentiment:get-mood-log', () => sentimentEngine.getMoodLog());

  // ── Predictor ───────────────────────────────────────────────────────
  ipcMain.handle('predictor:record-interaction', () => predictor.recordInteraction());
}
