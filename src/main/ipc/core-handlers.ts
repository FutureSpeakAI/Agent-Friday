/**
 * Core IPC handlers — API port, API keys, system instruction, settings, MCP, window controls.
 */
import { ipcMain, BrowserWindow } from 'electron';
import { settingsManager } from '../settings';
import { buildGeminiLiveSystemInstruction } from '../personality';
import { mcpClient } from '../mcp-client';
import { memoryManager } from '../memory';

export interface CoreHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
  serverPort: number;
}

export function registerCoreHandlers(deps: CoreHandlerDeps): void {
  // ── Core ────────────────────────────────────────────────────────────
  ipcMain.handle('get-api-port', () => deps.serverPort);
  ipcMain.handle('get-gemini-api-key', () => settingsManager.getGeminiApiKey());
  ipcMain.handle('get-live-system-instruction', async () => buildGeminiLiveSystemInstruction());

  // ── Settings ────────────────────────────────────────────────────────
  ipcMain.handle('settings:get', () => settingsManager.getMasked());

  ipcMain.handle('settings:set', async (_event, key: string, value: unknown) => {
    await settingsManager.setSetting(key, value);
  });

  ipcMain.handle('settings:set-auto-launch', async (_event, enabled: boolean) => {
    await settingsManager.setAutoLaunch(enabled);
  });

  ipcMain.handle('settings:set-auto-screen-capture', async (_event, enabled: boolean) => {
    await settingsManager.setAutoScreenCapture(enabled);
  });

  ipcMain.handle(
    'settings:set-obsidian-vault-path',
    async (_event, vaultPath: string) => {
      await settingsManager.setObsidianVaultPath(vaultPath);
      if (vaultPath) {
        const { syncLongTermToVault, syncMediumTermToVault, ensureVaultStructure } =
          require('../obsidian-memory');
        try {
          await ensureVaultStructure(vaultPath);
          await syncLongTermToVault(vaultPath, memoryManager.getLongTerm());
          await syncMediumTermToVault(vaultPath, memoryManager.getMediumTerm());
          console.log('[EVE] Synced existing memories to Obsidian vault');
        } catch (err) {
          console.warn('[EVE] Failed to sync memories to vault:', err);
        }
      }
    },
  );

  ipcMain.handle(
    'settings:set-api-key',
    async (
      _event,
      key: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl',
      value: string,
    ) => {
      await settingsManager.setApiKey(key, value);
    },
  );

  // ── MCP ─────────────────────────────────────────────────────────────
  ipcMain.handle('mcp:list-tools', async () => mcpClient.listTools());

  ipcMain.handle(
    'mcp:call-tool',
    async (_event, toolName: string, args: Record<string, unknown>) => {
      return mcpClient.callTool(toolName, args);
    },
  );

  ipcMain.handle('mcp:get-status', () => mcpClient.getStatus());

  ipcMain.handle('mcp:add-server', async (_event, config: any) => {
    await mcpClient.addServer(config);
    return mcpClient.getStatus();
  });

  // ── Window controls ─────────────────────────────────────────────────
  ipcMain.handle('window:minimize', () => deps.getMainWindow()?.minimize());

  ipcMain.handle('window:maximize', () => {
    const win = deps.getMainWindow();
    if (win?.isMaximized()) {
      win.unmaximize();
    } else {
      win?.maximize();
    }
  });

  ipcMain.handle('window:close', () => deps.getMainWindow()?.close());
}
