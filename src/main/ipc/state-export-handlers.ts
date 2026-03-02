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
import { assertPassphrase, assertSafePath } from './validate';

export function registerStateExportHandlers(): void {
  // ── Export ─────────────────────────────────────────────────────────

  // Crypto Sprint 8 (CRITICAL): Validate passphrase length + output path.
  ipcMain.handle(
    'persistence:export-state',
    (_event, passphrase: unknown, outputPath?: unknown) => {
      assertPassphrase(passphrase, 'persistence:export-state passphrase');
      if (outputPath !== undefined && outputPath !== null) {
        assertSafePath(outputPath, 'persistence:export-state outputPath');
      }
      return stateExport.exportState(passphrase as string, outputPath as string | undefined);
    },
  );

  // Crypto Sprint 8 (CRITICAL): Validate passphrase length + output path.
  ipcMain.handle(
    'persistence:export-incremental',
    (_event, passphrase: unknown, outputPath?: unknown) => {
      assertPassphrase(passphrase, 'persistence:export-incremental passphrase');
      if (outputPath !== undefined && outputPath !== null) {
        assertSafePath(outputPath, 'persistence:export-incremental outputPath');
      }
      return stateExport.exportIncremental(passphrase as string, outputPath as string | undefined);
    },
  );

  // ── Import ────────────────────────────────────────────────────────

  // Crypto Sprint 8 (CRITICAL): Validate archive path + passphrase.
  ipcMain.handle(
    'persistence:import-state',
    (_event, archivePath: unknown, passphrase: unknown) => {
      assertSafePath(archivePath, 'persistence:import-state archivePath');
      assertPassphrase(passphrase, 'persistence:import-state passphrase');
      return stateExport.importState(archivePath as string, passphrase as string);
    },
  );

  // Crypto Sprint 8 (CRITICAL): Validate archive path + passphrase.
  ipcMain.handle(
    'persistence:validate-archive',
    (_event, archivePath: unknown, passphrase: unknown) => {
      assertSafePath(archivePath, 'persistence:validate-archive archivePath');
      assertPassphrase(passphrase, 'persistence:validate-archive passphrase');
      return stateExport.validateArchive(archivePath as string, passphrase as string);
    },
  );

  // ── Scheduled Backup ──────────────────────────────────────────────

  // Crypto Sprint 8 (HIGH): Validate passphrase length.
  ipcMain.handle(
    'persistence:set-auto-passphrase',
    (_event, passphrase: unknown) => {
      assertPassphrase(passphrase, 'persistence:set-auto-passphrase passphrase');
      return stateExport.setAutoBackupPassphrase(passphrase as string);
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
