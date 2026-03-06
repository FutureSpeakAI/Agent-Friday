/**
 * IPC handlers for system monitoring — stats and process listing.
 *
 * Exposes system metrics to the renderer process via eve.system namespace.
 */

import { ipcMain } from 'electron';
import { systemMonitor } from '../system-monitor';
import { assertNumber } from './validate';

export function registerSystemMonitorHandlers(): void {
  ipcMain.handle('system:stats', async () => {
    return systemMonitor.getStats();
  });

  ipcMain.handle('system:processes', async (_event, limit?: unknown) => {
    if (limit !== undefined && limit !== null) {
      assertNumber(limit, 'system:processes limit', 1, 200);
    }
    return systemMonitor.getProcesses(limit as number | undefined);
  });
}
