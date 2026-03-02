/**
 * Superpowers IPC handlers — manage loaded programs (GitLoader repos),
 * toggle tools, configure permissions, install/uninstall, safety scanning.
 *
 * Track II, Phase 4: Unified handler for both v1 (GitLoader-backed) and
 * v2 (Adaptation Engine-backed) superpowers. The v2 store provides
 * consent-gated installation of adapted connectors.
 */
import { ipcMain } from 'electron';
import { superpowersRegistry } from '../superpowers-registry';
import type { SuperpowerPermissions } from '../superpowers-registry';
import { superpowerStore } from '../superpower-store';
import { assertSafeUrl, assertString } from './validate';

export function registerSuperpowersHandlers(): void {
  // Initialize the registry (loads persisted state, syncs with GitLoader)
  superpowersRegistry.initialize().catch((err) => {
    // Crypto Sprint 17: Sanitize error output.
    console.warn('[Superpowers] Registry init failed:', err instanceof Error ? err.message : 'Unknown error');
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
    // Check v2 store first (sp- prefix), fall back to v1 registry
    if (id.startsWith('sp-')) {
      if (enabled) {
        superpowerStore.enableSuperpower(id);
      } else {
        superpowerStore.disableSuperpower(id);
      }
      return superpowerStore.get(id);
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
  // Crypto Sprint 18: Validate URL protocol at IPC boundary (defense-in-depth).
  // Blocks file://, ssh://, data://, javascript: schemes before reaching gitLoader.
  ipcMain.handle('superpowers:install', async (_event, repoUrl: unknown) => {
    assertString(repoUrl, 'superpowers:install repoUrl', 2_000);
    assertSafeUrl(repoUrl, 'superpowers:install repoUrl', 'git');
    if (
      !(repoUrl as string).includes('github.com') &&
      !(repoUrl as string).includes('gitlab.com') &&
      !(repoUrl as string).includes('bitbucket.org')
    ) {
      // Allow it but warn — could be a raw git URL
      console.warn('[Superpowers] Non-standard repo URL');
    }
    try {
      const superpower = await superpowersRegistry.install(repoUrl as string);
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
    // v2 adapted superpowers
    if (id.startsWith('sp-')) {
      superpowerStore.uninstallSuperpower(id);
      return true;
    }
    return superpowersRegistry.uninstall(id);
  });

  // ── Uninstall preview (cLaw: enumerate what gets removed) ──────────
  ipcMain.handle('superpowers:uninstall-preview', (_event, id: string) => {
    if (!id || typeof id !== 'string') {
      throw new Error('superpowers:uninstall-preview requires a string id');
    }

    // v2 adapted superpowers
    if (id.startsWith('sp-')) {
      const sp = superpowerStore.get(id);
      if (!sp) return null;
      return {
        id: sp.id,
        name: sp.name,
        toolsRemoved: sp.tools.map(t => t.name),
        toolCount: sp.tools.length,
        usageCount: sp.usageCount,
        hasSourceCode: !!sp.sourceCode,
        hasBridgeScript: !!sp.bridgeScript,
        dependencyCount: sp.dependencies.length,
      };
    }

    // v1 GitLoader superpowers
    const sp = superpowersRegistry.get(id);
    if (!sp) return null;
    return {
      id: sp.id,
      name: sp.name,
      toolsRemoved: sp.tools.map((t: { name: string }) => t.name),
      toolCount: sp.tools.length,
      usageCount: sp.totalInvocations,
      hasSourceCode: false,
      hasBridgeScript: false,
      dependencyCount: 0,
    };
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
      // v2 adapted superpowers
      if (superpowerId.startsWith('sp-')) {
        superpowerStore.recordUsage(superpowerId);
        if (!success) {
          superpowerStore.recordError(superpowerId, `Tool ${toolName} failed`);
        }
        return;
      }
      superpowersRegistry.recordInvocation(superpowerId, toolName, latencyMs, success);
    },
  );

  // ── Flush pending saves ─────────────────────────────────────────────
  ipcMain.handle('superpowers:flush', async () => {
    await superpowersRegistry.flush();
  });

  // ═══════════════════════════════════════════════════════════════════
  // v2 Adapted Superpower Store — Consent-gated installation
  // ═══════════════════════════════════════════════════════════════════

  // ── Store: List all v2 superpowers ──────────────────────────────────
  ipcMain.handle('superpowers:store-list', () => {
    return superpowerStore.getAll();
  });

  // ── Store: Get a single v2 superpower ──────────────────────────────
  ipcMain.handle('superpowers:store-get', (_event, id: string) => {
    if (!id || typeof id !== 'string') {
      throw new Error('superpowers:store-get requires a string id');
    }
    return superpowerStore.get(id);
  });

  // ── Store: Confirm consent and complete installation ───────────────
  ipcMain.handle(
    'superpowers:store-confirm',
    (_event, id: string, consentToken: string) => {
      if (!id || typeof id !== 'string') {
        throw new Error('superpowers:store-confirm requires a string id');
      }
      if (!consentToken || typeof consentToken !== 'string') {
        throw new Error('superpowers:store-confirm requires a non-empty consent token');
      }
      superpowerStore.confirmInstall(id, consentToken);
      return superpowerStore.get(id);
    },
  );

  // ── Store: Get enabled tools from v2 superpowers ───────────────────
  ipcMain.handle('superpowers:store-enabled-tools', () => {
    return superpowerStore.getEnabledTools();
  });

  // ── Store: Get status ──────────────────────────────────────────────
  ipcMain.handle('superpowers:store-status', () => {
    return superpowerStore.getStatus();
  });

  // ── Store: Get prompt context (for system prompt injection) ────────
  ipcMain.handle('superpowers:store-prompt-context', () => {
    return superpowerStore.getPromptContext();
  });

  // ── Store: Superpowers needing attention ───────────────────────────
  ipcMain.handle('superpowers:store-needs-attention', () => {
    return superpowerStore.getNeedingAttention();
  });
}
