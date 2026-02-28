/**
 * IPC handlers for the Memory-Personality Bridge (Track IX, Phase 3).
 *
 * All handlers are prefixed with 'bridge:' and follow the same
 * pattern as other handler modules in the ipc/ directory.
 */

import { ipcMain } from 'electron';
import {
  memoryPersonalityBridge,
  type MemoryEngagement,
  type ProactivityProposal,
  type BridgeConfig,
} from '../memory-personality-bridge';

export function registerMemoryPersonalityBridgeHandlers(): void {
  // ── Memory Engagement ─────────────────────────────────────────────

  ipcMain.handle(
    'bridge:record-engagement',
    (_event, memoryId: string, type: MemoryEngagement['type'], context: string) => {
      memoryPersonalityBridge.recordEngagement(memoryId, type, context);
    },
  );

  ipcMain.handle('bridge:get-engagements', () => {
    return memoryPersonalityBridge.getEngagements();
  });

  ipcMain.handle('bridge:get-priority-adjustments', () => {
    const map = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    // Convert Map to plain object for IPC serialization
    const result: Record<string, number> = {};
    for (const [key, value] of map.entries()) {
      result[key] = value;
    }
    return result;
  });

  // ── Extraction Guidance ───────────────────────────────────────────

  ipcMain.handle('bridge:get-extraction-guidance', () => {
    return memoryPersonalityBridge.getExtractionGuidance();
  });

  ipcMain.handle('bridge:get-extraction-hints', () => {
    return memoryPersonalityBridge.getExtractionHints();
  });

  ipcMain.handle('bridge:recompute-extraction-hints', () => {
    memoryPersonalityBridge.recomputeExtractionHints();
  });

  // ── Proactivity Arbitration ───────────────────────────────────────

  ipcMain.handle(
    'bridge:propose-proactivity',
    (_event, proposal: Omit<ProactivityProposal, 'id' | 'timestamp'>) => {
      return memoryPersonalityBridge.proposeProactivity(proposal);
    },
  );

  ipcMain.handle('bridge:arbitrate-proactivity', () => {
    return memoryPersonalityBridge.arbitrateProactivity();
  });

  ipcMain.handle('bridge:get-proactivity-cooldown', () => {
    return memoryPersonalityBridge.getProactivityCooldownRemaining();
  });

  ipcMain.handle('bridge:get-pending-proposals', () => {
    return memoryPersonalityBridge.getPendingProposalCount();
  });

  // ── Anti-Manipulation ─────────────────────────────────────────────

  ipcMain.handle(
    'bridge:record-exchange',
    (_event, flattery: boolean, urgency: boolean, options: number) => {
      memoryPersonalityBridge.recordExchangeObservation(flattery, urgency, options);
    },
  );

  ipcMain.handle('bridge:get-manipulation-metrics', () => {
    return memoryPersonalityBridge.getManipulationMetrics();
  });

  // ── Context / Status ──────────────────────────────────────────────

  ipcMain.handle('bridge:get-prompt-context', () => {
    return memoryPersonalityBridge.getPromptContext();
  });

  ipcMain.handle('bridge:get-state', () => {
    return memoryPersonalityBridge.getState();
  });

  ipcMain.handle('bridge:get-config', () => {
    return memoryPersonalityBridge.getConfig();
  });

  ipcMain.handle('bridge:get-relevance-weights', () => {
    return memoryPersonalityBridge.getRelevanceWeights();
  });

  // ── Sync / Memory-to-Personality ──────────────────────────────────

  ipcMain.handle('bridge:sync-memory-to-personality', () => {
    memoryPersonalityBridge.syncMemoryToPersonality();
  });

  // ── Configuration ─────────────────────────────────────────────────

  ipcMain.handle('bridge:update-config', (_event, updates: Partial<BridgeConfig>) => {
    memoryPersonalityBridge.updateConfig(updates);
  });

  // ── Reset ─────────────────────────────────────────────────────────

  ipcMain.handle('bridge:reset', async () => {
    await memoryPersonalityBridge.reset();
  });
}
