/**
 * IPC handlers for the Memory Quality Assessment engine (Track IX, Phase 1).
 *
 * All handlers are prefixed with 'memquality:' and follow the same
 * pattern as other handler modules in the ipc/ directory.
 */

import { ipcMain } from 'electron';
import {
  memoryQuality,
  type MemoryQualityConfig,
  type ExtractionResult,
  type RetrievalResult,
  type ConsolidationResult,
} from '../memory-quality';

export function registerMemoryQualityHandlers(): void {
  // ── Assessment ──────────────────────────────────────────────────────

  ipcMain.handle(
    'memquality:assess-extraction',
    (_event, results: ExtractionResult[]) => {
      return memoryQuality.assessExtractionQuality(results);
    },
  );

  ipcMain.handle(
    'memquality:assess-retrieval',
    (_event, results: RetrievalResult[]) => {
      return memoryQuality.assessRetrievalQuality(results);
    },
  );

  ipcMain.handle(
    'memquality:assess-consolidation',
    (_event, results: ConsolidationResult[]) => {
      return memoryQuality.assessConsolidationQuality(results);
    },
  );

  ipcMain.handle(
    'memquality:assess-person-mentions',
    (_event, results: ExtractionResult[]) => {
      return memoryQuality.assessPersonMentionQuality(results);
    },
  );

  ipcMain.handle(
    'memquality:build-report',
    (
      _event,
      extractionResults: ExtractionResult[],
      retrievalResults: RetrievalResult[],
      consolidationResults: ConsolidationResult[],
    ) => {
      return memoryQuality.buildReport(
        extractionResults,
        retrievalResults,
        consolidationResults,
      );
    },
  );

  // ── Benchmarks ─────────────────────────────────────────────────────

  ipcMain.handle('memquality:get-extraction-benchmarks', () => {
    return memoryQuality.getExtractionBenchmarks();
  });

  ipcMain.handle('memquality:get-retrieval-benchmarks', () => {
    return memoryQuality.getRetrievalBenchmarks();
  });

  ipcMain.handle('memquality:get-consolidation-benchmarks', () => {
    return memoryQuality.getConsolidationBenchmarks();
  });

  // ── Reports & History ──────────────────────────────────────────────

  ipcMain.handle('memquality:get-latest-report', () => {
    return memoryQuality.getLatestReport();
  });

  ipcMain.handle('memquality:get-quality-history', () => {
    return memoryQuality.getQualityHistory();
  });

  ipcMain.handle('memquality:get-quality-trend', (_event, count?: number) => {
    return memoryQuality.getQualityTrend(count);
  });

  // ── Config ─────────────────────────────────────────────────────────

  ipcMain.handle('memquality:get-config', () => {
    return memoryQuality.getConfig();
  });

  ipcMain.handle(
    'memquality:update-config',
    (_event, partial: Partial<MemoryQualityConfig>) => {
      return memoryQuality.updateConfig(partial);
    },
  );

  // ── Context ────────────────────────────────────────────────────────

  ipcMain.handle('memquality:get-prompt-context', () => {
    return memoryQuality.getPromptContext();
  });
}
