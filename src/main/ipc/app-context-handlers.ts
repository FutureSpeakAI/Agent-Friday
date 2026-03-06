/**
 * Track C, Phase 3: IPC Handler for App Context
 *
 * Channels:
 *   app-context:get — fetch current enriched context for a specific app
 */

import { ipcMain } from 'electron';
import { liveContextBridge } from '../live-context-bridge';
import { assertString } from './validate';

export function registerAppContextHandlers(): void {
  ipcMain.handle('app-context:get', async (_event, appId: unknown) => {
    assertString(appId, 'app-context:get appId');
    return liveContextBridge.getContextForApp(appId as string);
  });
}
