/**
 * voice-state-handlers.ts — IPC bridge between the renderer and
 * the main-process VoiceStateMachine singleton.
 *
 * Phase 1.2, Track 1: VoiceStateMachine IPC Bridge
 *
 * ─────────────────────────────────────────────────────────────────
 * HERMENEUTIC CIRCLE — Boundary & Inversion
 * ─────────────────────────────────────────────────────────────────
 *
 * BOUNDARY: "What must the preload bridge expose vs. hide?"
 *   → Expose: current state (read-only), transition log, health metrics,
 *     state-change events. These are what the renderer needs to render
 *     the correct UI (status indicator, error messages, health badge).
 *   → Hide: transition(), setGuard(), onEnterState(), destroy(). The
 *     renderer is untrusted — it must NOT be able to drive the state
 *     machine. Only main-process voice components call transition().
 *
 * INVERSION: "If a malicious renderer could call any IPC channel,
 *   what damage could it do?"
 *   → With the current surface: nothing. It can read state but cannot
 *     mutate it. No channel accepts a target state or reason string.
 *     The worst a compromised renderer can do is spam getState() calls,
 *     which are O(1) property reads.
 *
 * CONSTRAINT: "What is the minimal IPC surface?"
 *   → Three invoke channels (state, log, health) + one event channel
 *     (state-change pushed from main → renderer). This is the minimum
 *     the renderer needs: current state for UI, log for debug panel,
 *     health for status badge, events for reactivity.
 *
 * PRECEDENT: Follows the local-conversation-handlers.ts pattern exactly:
 *   - deps object with getMainWindow()
 *   - sendToRenderer helper for safely pushing events
 *   - ipcMain.handle() for request-response
 *   - EventEmitter listener for forwarding main → renderer events
 *
 * Channels:
 *   voice-state:get-state          → Current VoiceState string
 *   voice-state:get-transition-log → Full transition log array
 *   voice-state:get-health         → Current HealthMetrics snapshot
 *   voice-state:event:state-change → Pushed on every state transition
 * ─────────────────────────────────────────────────────────────────
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { VoiceStateMachine } from '../voice/voice-state-machine';
import type {
  VoiceState,
  TransitionLogEntry,
  HealthMetrics,
} from '../voice/voice-state-machine';

export interface VoiceStateHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerVoiceStateHandlers(
  deps: VoiceStateHandlerDeps,
): void {
  const machine = VoiceStateMachine.getInstance();

  // Helper to safely send events to renderer (same pattern as local-conversation-handlers)
  const sendToRenderer = (channel: string, ...args: unknown[]) => {
    const win = deps.getMainWindow();
    if (win && !win.isDestroyed()) {
      win.webContents.send(channel, ...args);
    }
  };

  // ── Forward state-change events to renderer ──────────────────────

  machine.on(
    'state-change',
    (payload: { from: VoiceState; to: VoiceState; reason: string }) => {
      sendToRenderer('voice-state:event:state-change', payload);
    },
  );

  // ── IPC handlers (read-only — no mutation channels) ──────────────

  ipcMain.handle('voice-state:get-state', (): VoiceState => {
    return machine.getState();
  });

  ipcMain.handle(
    'voice-state:get-transition-log',
    (): ReadonlyArray<TransitionLogEntry> => {
      return machine.getTransitionLog();
    },
  );

  ipcMain.handle('voice-state:get-health', (): HealthMetrics => {
    return machine.getHealth();
  });
}
