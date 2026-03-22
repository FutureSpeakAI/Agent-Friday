/**
 * connection-stage-handlers.ts — IPC bridge for ConnectionStageMonitor.
 *
 * Phase 6.1, Track 6: Integration Wiring
 *
 * Exposes connection stage progress to the renderer:
 *   - getCurrentStage: which stage is active right now
 *
 * Events pushed to renderer:
 *   - connection-stage:event:stage-enter
 *   - connection-stage:event:stage-complete
 *   - connection-stage:event:stage-timeout
 *   - connection-stage:event:all-complete
 *
 * Follows the same deps/sendToRenderer pattern as voice-state-handlers.ts.
 */

import { ipcMain, type BrowserWindow } from 'electron';
import {
  ConnectionStageMonitor,
  type ConnectionStage,
} from '../voice/connection-stage-monitor';

export interface ConnectionStageHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerConnectionStageHandlers(
  deps: ConnectionStageHandlerDeps,
): void {
  const monitor = ConnectionStageMonitor.getInstance();

  const sendToRenderer = (channel: string, ...args: unknown[]) => {
    const win = deps.getMainWindow();
    if (win && !win.isDestroyed()) {
      win.webContents.send(channel, ...args);
    }
  };

  // ── Forward events to renderer ──────────────────────────────────────

  monitor.on(
    'stage-enter',
    (payload: { stage: ConnectionStage; userMessage: string }) => {
      sendToRenderer('connection-stage:event:stage-enter', payload);
    },
  );

  monitor.on(
    'stage-complete',
    (payload: { stage: ConnectionStage; durationMs: number }) => {
      sendToRenderer('connection-stage:event:stage-complete', payload);
    },
  );

  monitor.on(
    'stage-timeout',
    (payload: {
      stage: ConnectionStage;
      failureMessage: string;
      failureAction?: string;
    }) => {
      sendToRenderer('connection-stage:event:stage-timeout', payload);
    },
  );

  monitor.on('all-complete', () => {
    sendToRenderer('connection-stage:event:all-complete');
  });

  // ── IPC handlers ────────────────────────────────────────────────────

  ipcMain.handle(
    'connection-stage:get-current',
    (): ConnectionStage | null => {
      return monitor.getCurrentStage();
    },
  );
}
