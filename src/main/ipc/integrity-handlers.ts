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

  /**
   * Reset Asimov's cLaws — re-sign everything and exit safe mode.
   * This is the user-facing "Reset Integrity" function for corrupted agents.
   * It re-establishes all cryptographic signatures using the current state
   * and clears any false-positive safe mode triggers.
   */
  ipcMain.handle('integrity:reset', async () => {
    const config = settingsManager.getAgentConfig();
    const identityJson = JSON.stringify(config, Object.keys(config).sort());
    const longTerm = memoryManager.getLongTerm();
    const mediumTerm = memoryManager.getMediumTerm();
    const ltJson = JSON.stringify(longTerm, null, 2);
    const mtJson = JSON.stringify(mediumTerm, null, 2);
    const ltSnap = longTerm.map((e: any) => ({ id: e.id, fact: e.fact }));
    const mtSnap = mediumTerm.map((e: any) => ({ id: e.id, observation: e.observation }));

    return integrityManager.resetIntegrity(identityJson, ltSnap, mtSnap, ltJson, mtJson);
  });
}
