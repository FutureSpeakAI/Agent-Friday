/**
 * IPC handlers for the Personality Calibration engine (Track IX, Phase 2).
 *
 * All handlers are prefixed with 'calibration:' and follow the same
 * pattern as other handler modules in the ipc/ directory.
 */

import { ipcMain } from 'electron';
import {
  personalityCalibration,
  type CalibrationConfig,
  type StyleDimensions,
} from '../personality-calibration';

export function registerPersonalityCalibrationHandlers(): void {
  // ── Signal Processing ─────────────────────────────────────────────

  ipcMain.handle(
    'calibration:process-message',
    (_event, text: string, responseTimeMs?: number) => {
      personalityCalibration.processUserMessage(text, responseTimeMs);
    },
  );

  ipcMain.handle('calibration:record-dismissal', () => {
    personalityCalibration.recordDismissal();
  });

  ipcMain.handle('calibration:record-engagement', () => {
    personalityCalibration.recordEngagement();
  });

  ipcMain.handle('calibration:increment-session', () => {
    personalityCalibration.incrementSession();
  });

  // ── Queries ───────────────────────────────────────────────────────

  ipcMain.handle('calibration:get-dimensions', () => {
    return personalityCalibration.getDimensions();
  });

  ipcMain.handle('calibration:get-state', () => {
    return personalityCalibration.getState();
  });

  ipcMain.handle('calibration:get-dismissal-rate', () => {
    return personalityCalibration.getDismissalRate();
  });

  ipcMain.handle(
    'calibration:get-effective-proactivity',
    (_event, isCritical: boolean) => {
      return personalityCalibration.getEffectiveProactivity(isCritical);
    },
  );

  ipcMain.handle('calibration:get-history', () => {
    return personalityCalibration.getHistory();
  });

  ipcMain.handle('calibration:get-explanation', () => {
    return personalityCalibration.getCalibrationExplanation();
  });

  ipcMain.handle('calibration:get-prompt-context', () => {
    return personalityCalibration.getPromptContext();
  });

  // ── Visual Evolution Sync ─────────────────────────────────────────

  ipcMain.handle('calibration:get-visual-warmth-modifier', () => {
    return personalityCalibration.getVisualWarmthModifier();
  });

  ipcMain.handle('calibration:get-visual-energy-modifier', () => {
    return personalityCalibration.getVisualEnergyModifier();
  });

  // ── Config ────────────────────────────────────────────────────────

  ipcMain.handle('calibration:get-config', () => {
    return personalityCalibration.getConfig();
  });

  ipcMain.handle(
    'calibration:update-config',
    (_event, partial: Partial<CalibrationConfig>) => {
      return personalityCalibration.updateConfig(partial);
    },
  );

  // ── Reset ─────────────────────────────────────────────────────────

  ipcMain.handle(
    'calibration:reset-dimension',
    (_event, dimension: keyof StyleDimensions) => {
      personalityCalibration.resetDimension(dimension);
    },
  );

  ipcMain.handle('calibration:reset-all', () => {
    personalityCalibration.resetAll();
  });
}
