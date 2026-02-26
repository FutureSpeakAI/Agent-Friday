/**
 * Integrity IPC handlers — exposes integrity state to the renderer.
 */

import { ipcMain } from 'electron';
import { integrityManager } from '../integrity';
import { memoryManager } from '../memory';
import { settingsManager } from '../settings';

export function registerIntegrityHandlers(): void {
  ipcMain.handle('integrity:get-state', () => {
    return integrityManager.getState();
  });

  ipcMain.handle('integrity:is-safe-mode', () => {
    return integrityManager.isInSafeMode();
  });

  ipcMain.handle('integrity:acknowledge-memory-changes', () => {
    integrityManager.acknowledgeMemoryChanges();
    return { success: true };
  });

  /** Run a full verification check and return summary */
  ipcMain.handle('integrity:verify', () => {
    // Verify identity
    const config = settingsManager.getAgentConfig();
    const identityJson = JSON.stringify(config, Object.keys(config).sort());
    integrityManager.verifyIdentity(identityJson);

    // Verify memories
    const longTerm = memoryManager.getLongTerm();
    const mediumTerm = memoryManager.getMediumTerm();
    integrityManager.checkMemories(longTerm, mediumTerm);

    const state = integrityManager.getState();
    return {
      lawsIntact: state.lawsIntact,
      identityIntact: state.identityIntact,
      memoriesIntact: state.memoriesIntact,
      safeMode: state.safeMode,
    };
  });
}
