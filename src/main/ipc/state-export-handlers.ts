/**
 * IPC handlers for the State Export / Agent Persistence engine (Track VII, Phase 4).
 *
 * All handlers are prefixed with 'persistence:' and follow the same
 * pattern as other handler modules in the ipc/ directory.
 */

import { ipcMain } from 'electron';
import {
  stateExport,
  type PersistenceConfig,
} from '../state-export';

export function registerStateExportHandlers(): void {
  // ── Export ─────────────────────────────────────────────────────────

  ipcMain.handle(
    'persistence:export-state',
    (_event, passphrase: string, outputPath?: string) => {
      return stateExport.exportState(passphrase, outputPath);
    },
  );

  ipcMain.handle(
    'persistence:export-incremental',
    (_event, passphrase: string, outputPath?: string) => {
      return stateExport.exportIncremental(passphrase, outputPath);
    },
  );

  // ── Import ────────────────────────────────────────────────────────

  ipcMain.handle(
    'persistence:import-state',
    (_event, archivePath: string, passphrase: string) => {
      return stateExport.importState(archivePath, passphrase);
    },
  );

  ipcMain.handle(
    'persistence:validate-archive',
    (_event, archivePath: string, passphrase: string) => {
      return stateExport.validateArchive(archivePath, passphrase);
    },
  );

  // ── Scheduled Backup ──────────────────────────────────────────────

  ipcMain.handle(
    'persistence:set-auto-passphrase',
    (_event, passphrase: string) => {
      return stateExport.setAutoBackupPassphrase(passphrase);
    },
  );

  ipcMain.handle('persistence:clear-auto-passphrase', () => {
    return stateExport.clearAutoBackupPassphrase();
  });

  ipcMain.handle('persistence:run-scheduled-backup', () => {
    return stateExport.runScheduledBackup();
  });

  // ── Queries ───────────────────────────────────────────────────────

  ipcMain.handle('persistence:get-state-files', () => {
    return stateExport.getStateFilePaths();
  });

  ipcMain.handle('persistence:enumerate-state', () => {
    return stateExport.enumerateState();
  });

  ipcMain.handle('persistence:get-backup-history', () => {
    return stateExport.getBackupHistory();
  });

  ipcMain.handle('persistence:get-last-backup', () => {
    return stateExport.getLastBackup();
  });

  // ── Config ────────────────────────────────────────────────────────

  ipcMain.handle('persistence:get-config', () => {
    return stateExport.getConfig();
  });

  ipcMain.handle(
    'persistence:update-config',
    (_event, partial: Partial<PersistenceConfig>) => {
      return stateExport.updateConfig(partial);
    },
  );

  // ── Context & Continuity ──────────────────────────────────────────

  ipcMain.handle('persistence:get-prompt-context', () => {
    return stateExport.getPromptContext();
  });

  ipcMain.handle('persistence:check-continuity', () => {
    return stateExport.checkContinuityReadiness();
  });
}
