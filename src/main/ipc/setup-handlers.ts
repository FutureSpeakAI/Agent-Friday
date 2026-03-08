/**
 * Sprint 7: IPC handlers for setup wizard and profile management.
 *
 * Exposes SetupWizard and ProfileManager to the renderer via
 * eve.setup and eve.profile namespaces.
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { SetupWizard } from '../setup/setup-wizard';
import { ProfileManager } from '../setup/profile-manager';
import { assertString, assertObject } from './validate';
import type { TierName } from '../hardware/tier-recommender';

export interface SetupHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerSetupHandlers(deps: SetupHandlerDeps): void {
  const wizard = SetupWizard.getInstance();
  const profiles = ProfileManager.getInstance();

  // ── Setup Wizard ──────────────────────────────────────────────────

  ipcMain.handle('setup:is-first-run', () => {
    return wizard.isFirstRun();
  });

  ipcMain.handle('setup:get-state', () => {
    return wizard.getSetupState();
  });

  ipcMain.handle('setup:start', async () => {
    return wizard.startSetup();
  });

  ipcMain.handle('setup:skip', () => {
    wizard.skipSetup();
  });

  ipcMain.handle('setup:confirm-tier', (_event, tier: unknown) => {
    assertString(tier, 'setup:confirm-tier tier', 50);
    wizard.confirmTier(tier as TierName);
  });

  ipcMain.handle('setup:start-download', async () => {
    return wizard.startModelDownload();
  });

  ipcMain.handle('setup:get-download-progress', () => {
    return wizard.getDownloadProgress();
  });

  ipcMain.handle('setup:complete', () => {
    wizard.completeSetup();
  });

  ipcMain.handle('setup:reset', () => {
    wizard.resetSetup();
  });

  // ── Profile Manager ───────────────────────────────────────────────

  ipcMain.handle('profile:create', (_event, opts: unknown) => {
    assertObject(opts, 'profile:create opts');
    const o = opts as Record<string, unknown>;
    assertString(o.name, 'profile:create opts.name', 200);
    return profiles.createProfile(opts as any);
  });

  ipcMain.handle('profile:get', (_event, id: unknown) => {
    assertString(id, 'profile:get id', 100);
    return profiles.getProfile(id as string);
  });

  ipcMain.handle('profile:get-active', () => {
    return profiles.getActiveProfile();
  });

  ipcMain.handle('profile:set-active', (_event, id: unknown) => {
    assertString(id, 'profile:set-active id', 100);
    profiles.setActiveProfile(id as string);
  });

  ipcMain.handle('profile:update', (_event, id: unknown, data: unknown) => {
    assertString(id, 'profile:update id', 100);
    assertObject(data, 'profile:update data');
    return profiles.updateProfile(id as string, data as any);
  });

  ipcMain.handle('profile:delete', (_event, id: unknown) => {
    assertString(id, 'profile:delete id', 100);
    profiles.deleteProfile(id as string);
  });

  ipcMain.handle('profile:export', (_event, id: unknown) => {
    assertString(id, 'profile:export id', 100);
    return profiles.exportProfile(id as string);
  });

  ipcMain.handle('profile:import', (_event, json: unknown) => {
    assertString(json, 'profile:import json', 100_000);
    return profiles.importProfile(json as string);
  });

  ipcMain.handle('profile:list', () => {
    return profiles.listProfiles();
  });

  // ── Event forwarding to renderer ──────────────────────────────────

  wizard.on('setup-state-changed', (data) => {
    deps.getMainWindow()?.webContents.send('setup:event:state-changed', data);
  });

  wizard.on('download-progress', (data) => {
    deps.getMainWindow()?.webContents.send('setup:event:download-progress', data);
  });

  wizard.on('setup-complete', (data) => {
    deps.getMainWindow()?.webContents.send('setup:event:complete', data);
  });

  wizard.on('setup-error', (data) => {
    deps.getMainWindow()?.webContents.send('setup:event:error', data);
  });

  profiles.on('profile-changed', (data) => {
    deps.getMainWindow()?.webContents.send('profile:event:changed', data);
  });

  profiles.on('profile-created', (data) => {
    deps.getMainWindow()?.webContents.send('profile:event:created', data);
  });

  profiles.on('profile-deleted', (data) => {
    deps.getMainWindow()?.webContents.send('profile:event:deleted', data);
  });
}
