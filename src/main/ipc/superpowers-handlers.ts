/**
 * Superpowers IPC handlers — manage loaded programs (GitLoader repos),
 * toggle tools, configure permissions, install/uninstall, safety scanning.
 */
import { ipcMain } from 'electron';
import { superpowersRegistry } from '../superpowers-registry';
import type { SuperpowerPermissions } from '../superpowers-registry';

export function registerSuperpowersHandlers(): void {
  // Initialize the registry (loads persisted state, syncs with GitLoader)
  superpowersRegistry.initialize().catch((err) => {
    console.warn('[Superpowers] Registry init failed:', err);
  });

  // ── List / Get ───────────────────────────────────────────────────────
  ipcMain.handle('superpowers:list', () => {
    return superpowersRegistry.listAll();
  });

  ipcMain.handle('superpowers:get', (_event, id: string) => {
    if (!id || typeof id !== 'string') {
      throw new Error('superpowers:get requires a string id');
    }
    return superpowersRegistry.get(id);
  });

  // ── Toggle superpower on/off ────────────────────────────────────────
  ipcMain.handle('superpowers:toggle', async (_event, id: string, enabled: boolean) => {
    if (!id || typeof id !== 'string') {
      throw new Error('superpowers:toggle requires a string id');
    }
    if (typeof enabled !== 'boolean') {
      throw new Error('superpowers:toggle requires a boolean enabled');
    }
    return superpowersRegistry.setEnabled(id, enabled);
  });

  // ── Toggle individual tool within a superpower ──────────────────────
  ipcMain.handle(
    'superpowers:toggle-tool',
    (_event, superpowerId: string, toolName: string, enabled: boolean) => {
      if (!superpowerId || typeof superpowerId !== 'string') {
        throw new Error('superpowers:toggle-tool requires a string superpowerId');
      }
      if (!toolName || typeof toolName !== 'string') {
        throw new Error('superpowers:toggle-tool requires a string toolName');
      }
      if (typeof enabled !== 'boolean') {
        throw new Error('superpowers:toggle-tool requires a boolean enabled');
      }
      return superpowersRegistry.setToolEnabled(superpowerId, toolName, enabled);
    },
  );

  // ── Update permissions ──────────────────────────────────────────────
  ipcMain.handle(
    'superpowers:update-permissions',
    (_event, id: string, perms: Partial<SuperpowerPermissions>) => {
      if (!id || typeof id !== 'string') {
        throw new Error('superpowers:update-permissions requires a string id');
      }
      if (!perms || typeof perms !== 'object') {
        throw new Error('superpowers:update-permissions requires a permissions object');
      }
      return superpowersRegistry.updatePermissions(id, perms);
    },
  );

  // ── Install from URL ────────────────────────────────────────────────
  ipcMain.handle('superpowers:install', async (_event, repoUrl: string) => {
    if (!repoUrl || typeof repoUrl !== 'string') {
      throw new Error('superpowers:install requires a string repoUrl');
    }
    // Basic URL validation
    if (!repoUrl.includes('github.com') && !repoUrl.includes('gitlab.com') && !repoUrl.includes('bitbucket.org')) {
      // Allow it but warn — could be a raw git URL
      console.warn('[Superpowers] Non-standard repo URL:', repoUrl);
    }
    try {
      const superpower = await superpowersRegistry.install(repoUrl);
      return superpower;
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  // ── Uninstall ───────────────────────────────────────────────────────
  ipcMain.handle('superpowers:uninstall', async (_event, id: string) => {
    if (!id || typeof id !== 'string') {
      throw new Error('superpowers:uninstall requires a string id');
    }
    return superpowersRegistry.uninstall(id);
  });

  // ── Usage statistics ────────────────────────────────────────────────
  ipcMain.handle('superpowers:usage-stats', (_event, id: string) => {
    if (!id || typeof id !== 'string') {
      throw new Error('superpowers:usage-stats requires a string id');
    }
    return superpowersRegistry.getUsageStats(id);
  });

  // ── Get all enabled tools (for tool registration in sessions) ──────
  ipcMain.handle('superpowers:enabled-tools', () => {
    return superpowersRegistry.getAllEnabledTools();
  });

  // ── Record tool invocation (called by tool execution pipeline) ─────
  ipcMain.handle(
    'superpowers:record-invocation',
    (_event, superpowerId: string, toolName: string, latencyMs: number, success: boolean) => {
      if (!superpowerId || typeof superpowerId !== 'string') return;
      if (!toolName || typeof toolName !== 'string') return;
      superpowersRegistry.recordInvocation(superpowerId, toolName, latencyMs, success);
    },
  );

  // ── Flush pending saves ─────────────────────────────────────────────
  ipcMain.handle('superpowers:flush', async () => {
    await superpowersRegistry.flush();
  });
}
