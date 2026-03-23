/**
 * voice-fallback-handlers.ts — IPC bridge for VoiceFallbackManager.
 *
 * Phase 6.1, Track 6: Integration Wiring
 *
 * Exposes the fallback manager's public API to the renderer:
 *   - probeAvailability: check which voice paths are usable
 *   - startBestPath: begin the best available voice path
 *   - getCurrentPath: which path is currently active
 *   - switchTo: manually switch to a different path
 *
 * Events pushed to renderer:
 *   - voice-fallback:event:switch-start
 *   - voice-fallback:event:switch-complete
 *   - voice-fallback:event:all-paths-exhausted
 *   - voice-fallback:event:switch-failed
 *
 * Follows the same deps/sendToRenderer pattern as voice-state-handlers.ts.
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { VoiceFallbackManager, type VoicePath, type PathConfig } from '../voice/voice-fallback-manager';

export interface VoiceFallbackHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerVoiceFallbackHandlers(
  deps: VoiceFallbackHandlerDeps,
): void {
  const manager = VoiceFallbackManager.getInstance();

  const sendToRenderer = (channel: string, ...args: unknown[]) => {
    const win = deps.getMainWindow();
    if (win && !win.isDestroyed()) {
      win.webContents.send(channel, ...args);
    }
  };

  // ── Forward events to renderer ──────────────────────────────────────

  manager.on('switch-start', (payload: { from: VoicePath | null; to: VoicePath; reason: string }) => {
    sendToRenderer('voice-fallback:event:switch-start', payload);
  });

  manager.on('switch-complete', (payload: { path: VoicePath; hadContext: boolean }) => {
    sendToRenderer('voice-fallback:event:switch-complete', payload);
  });

  manager.on('all-paths-exhausted', (payload: { errors: Array<{ path: VoicePath; error: string }> }) => {
    sendToRenderer('voice-fallback:event:all-paths-exhausted', payload);
  });

  manager.on('switch-failed', (payload: { path: VoicePath; error: Error }) => {
    sendToRenderer('voice-fallback:event:switch-failed', {
      path: payload.path,
      error: payload.error.message,
    });
  });

  // ── IPC handlers ────────────────────────────────────────────────────

  ipcMain.handle('voice-fallback:probe-availability', async (): Promise<PathConfig[]> => {
    return manager.probeAvailability();
  });

  ipcMain.handle(
    'voice-fallback:start-best-path',
    async (_event, systemPrompt: string, tools: unknown[]): Promise<VoicePath> => {
      return manager.startBestPath(
        systemPrompt,
        tools as Array<{ name: string; description?: string; parameters?: unknown }>,
      );
    },
  );

  ipcMain.handle('voice-fallback:get-current-path', (): VoicePath | null => {
    return manager.getCurrentPath();
  });

  ipcMain.handle(
    'voice-fallback:switch-to',
    async (_event, path: VoicePath, reason: string): Promise<boolean> => {
      return manager.switchTo(path, reason);
    },
  );

  ipcMain.handle(
    'voice-fallback:set-path-priority',
    (_event, path: string, priority: number) => {
      manager.setPathPriority(path as VoicePath, priority);
    },
  );
}
