/**
 * IPC handlers for cost tracking — exposes session costs, savings,
 * and daily stats to the renderer process.
 */

import { ipcMain } from 'electron';
import { costTracker } from '../cost-tracker';

export function registerCostHandlers(): void {
  ipcMain.handle('cost:session', () => {
    return costTracker.getSession();
  });

  ipcMain.handle('cost:savings', () => {
    return costTracker.getSavings();
  });

  ipcMain.handle('cost:daily', (_event, days?: number) => {
    return costTracker.getDailyStats(days ?? 30);
  });

  ipcMain.handle('cost:monthly-spend', () => {
    return costTracker.getMonthlySpend();
  });

  ipcMain.handle('cost:reset-session', () => {
    costTracker.resetSession();
    return { ok: true };
  });
}
