/**
 * IPC handlers for session persistence — exposes JSONL DAG session
 * management to the renderer process.
 */

import { ipcMain } from 'electron';
import { sessionManager } from '../session-persistence';

export function registerSessionHandlers(): void {
  ipcMain.handle('session:start', (_event, options?: Record<string, unknown>) => {
    return sessionManager.startSession(options as any);
  });

  ipcMain.handle('session:load', (_event, sessionId: string) => {
    return sessionManager.loadSession(sessionId);
  });

  ipcMain.handle('session:list', () => {
    return sessionManager.listSessions();
  });

  ipcMain.handle('session:stats', () => {
    return sessionManager.getStats();
  });

  ipcMain.handle('session:context', () => {
    return sessionManager.buildContext();
  });

  ipcMain.handle('session:compact', (_event, contextWindow: number) => {
    return sessionManager.compactIfNeeded(contextWindow);
  });

  ipcMain.handle('session:close', () => {
    return sessionManager.close();
  });
}
