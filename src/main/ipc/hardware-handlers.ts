/**
 * Sprint 7: IPC handlers for hardware detection, tier recommendation, and model orchestration.
 *
 * Exposes HardwareProfiler, TierRecommender (pure functions), and ModelOrchestrator
 * to the renderer via eve.hardware namespace.
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { HardwareProfiler } from '../hardware/hardware-profiler';
import { getTier, getModelList, estimateVRAMUsage, recommend } from '../hardware/tier-recommender';
import { ModelOrchestrator } from '../hardware/model-orchestrator';
import { assertString, assertNumber, assertObject } from './validate';
import type { TierName } from '../hardware/tier-recommender';

export interface HardwareHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerHardwareHandlers(deps: HardwareHandlerDeps): void {
  const profiler = HardwareProfiler.getInstance();
  const orchestrator = ModelOrchestrator.getInstance();

  // ── Hardware Profiler ─────────────────────────────────────────────

  ipcMain.handle('hardware:detect', async () => {
    return profiler.detect();
  });

  ipcMain.handle('hardware:get-profile', () => {
    return profiler.getProfile();
  });

  ipcMain.handle('hardware:refresh', async () => {
    return profiler.refresh();
  });

  ipcMain.handle('hardware:get-effective-vram', () => {
    return profiler.getEffectiveVRAM();
  });

  // ── Tier Recommender (pure functions) ─────────────────────────────

  ipcMain.handle('hardware:get-tier', (_event, profile: unknown) => {
    assertObject(profile, 'hardware:get-tier profile');
    return getTier(profile as any);
  });

  ipcMain.handle('hardware:get-model-list', (_event, tier: unknown) => {
    assertString(tier, 'hardware:get-model-list tier', 50);
    return getModelList(tier as TierName);
  });

  ipcMain.handle('hardware:estimate-vram', (_event, models: unknown) => {
    if (!Array.isArray(models)) {
      throw new Error('hardware:estimate-vram models must be an array');
    }
    return estimateVRAMUsage(models as any);
  });

  ipcMain.handle('hardware:recommend', (_event, profile: unknown) => {
    assertObject(profile, 'hardware:recommend profile');
    return recommend(profile as any);
  });

  // ── Model Orchestrator ────────────────────────────────────────────

  ipcMain.handle('hardware:load-tier-models', async (_event, tier: unknown) => {
    assertString(tier, 'hardware:load-tier-models tier', 50);
    return orchestrator.loadTierModels(tier as TierName);
  });

  ipcMain.handle('hardware:get-loaded-models', () => {
    return orchestrator.getLoadedModels();
  });

  ipcMain.handle('hardware:get-vram-usage', () => {
    return orchestrator.getVRAMUsage();
  });

  ipcMain.handle('hardware:load-model', async (_event, name: unknown) => {
    assertString(name, 'hardware:load-model name', 256);
    return orchestrator.loadModel(name as string);
  });

  ipcMain.handle('hardware:unload-model', async (_event, name: unknown) => {
    assertString(name, 'hardware:unload-model name', 256);
    return orchestrator.unloadModel(name as string);
  });

  ipcMain.handle('hardware:evict-least-recent', async () => {
    return orchestrator.evictLeastRecent();
  });

  ipcMain.handle('hardware:get-orchestrator-state', () => {
    return orchestrator.getOrchestratorState();
  });

  ipcMain.handle('hardware:mark-model-used', (_event, name: unknown) => {
    assertString(name, 'hardware:mark-model-used name', 256);
    orchestrator.markUsed(name as string);
  });

  // ── Event forwarding to renderer ──────────────────────────────────

  profiler.on('hardware-detected', (profile) => {
    deps.getMainWindow()?.webContents.send('hardware:event:detected', profile);
  });

  orchestrator.on('model-loaded', (data) => {
    deps.getMainWindow()?.webContents.send('hardware:event:model-loaded', data);
  });

  orchestrator.on('model-unloaded', (data) => {
    deps.getMainWindow()?.webContents.send('hardware:event:model-unloaded', data);
  });

  orchestrator.on('vram-warning', (data) => {
    deps.getMainWindow()?.webContents.send('hardware:event:vram-warning', data);
  });
}
