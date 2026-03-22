/**
 * Telemetry IPC handlers — read-only access to local telemetry data from renderer.
 */
import { ipcMain } from 'electron';
import { telemetryEngine, type TelemetryCategory } from '../telemetry';

export function registerTelemetryHandlers(): void {
  ipcMain.handle('telemetry:get-aggregates', (_event, category?: TelemetryCategory) => {
    return telemetryEngine.getAggregates(category);
  });

  ipcMain.handle('telemetry:get-recent-events', (_event, count?: number, category?: TelemetryCategory) => {
    return telemetryEngine.getRecentEvents(count, category);
  });

  ipcMain.handle('telemetry:clear', async () => {
    await telemetryEngine.clear();
  });

  ipcMain.handle('telemetry:app-launched', (_event, appId: string) => {
    if (typeof appId === 'string' && appId.length > 0 && appId.length < 100) {
      telemetryEngine.record('app-launch', appId);
    }
  });

  ipcMain.handle('telemetry:record-error', (_event, errorName: string, errorMessage?: string) => {
    if (typeof errorName === 'string' && errorName.length > 0 && errorName.length < 200) {
      telemetryEngine.record('renderer-error', errorName, errorMessage?.slice(0, 500));
    }
  });
}
