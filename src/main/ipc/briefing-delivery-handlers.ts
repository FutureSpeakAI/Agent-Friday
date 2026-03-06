/**
 * IPC handlers for BriefingDelivery — Track A Phase 3.
 * Exposes proactive briefing list and dismiss actions to the renderer.
 *
 * Push events (briefing:new) are sent directly by BriefingDelivery via
 * webContents.send() — no handler registration needed for those.
 */

import { ipcMain } from 'electron';
import { briefingDelivery } from '../briefing-delivery';

export function registerBriefingDeliveryHandlers(): void {
  ipcMain.handle('briefing:list', async () => {
    return briefingDelivery.getRecentBriefings();
  });

  ipcMain.handle('briefing:dismiss', async (_event, id: unknown) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('briefing:dismiss requires a non-empty string id');
    }
    return briefingDelivery.dismissBriefing(id);
  });
}
