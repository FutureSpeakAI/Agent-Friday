/**
 * Core IPC handlers — API port, API keys, system instruction, settings, MCP, window controls.
 */
import { ipcMain, BrowserWindow, shell } from 'electron';
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
    // Validate key is a non-empty string
    if (!key || typeof key !== 'string') {
      throw new Error('settings:set requires a string key');
    }
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
          console.log('[Friday] Synced existing memories to Obsidian vault');
        } catch (err) {
          console.warn('[Friday] Failed to sync memories to vault:', err);
        }
      }
    },
  );

  ipcMain.handle(
    'settings:set-api-key',
    async (
      _event,
      key: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl' | 'perplexity' | 'openai' | 'openrouter',
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
    // Validate config is an object with required fields
    if (!config || typeof config !== 'object') {
      throw new Error('mcp:add-server requires a config object');
    }
    if (!config.name || typeof config.name !== 'string') {
      throw new Error('mcp:add-server config must include a string "name"');
    }
    if (!config.command || typeof config.command !== 'string') {
      throw new Error('mcp:add-server config must include a string "command"');
    }
    await mcpClient.addServer(config);
    return mcpClient.getStatus();
  });

  // ── Shell ──────────────────────────────────────────────────────────
  ipcMain.handle('shell:show-in-folder', (_event, filePath: string) => {
    shell.showItemInFolder(filePath);
  });

  ipcMain.handle('shell:open-path', async (_event, filePath: string) => {
    // Validate path is a string and doesn't contain shell metacharacters
    if (!filePath || typeof filePath !== 'string') {
      throw new Error('shell:open-path requires a valid file path');
    }
    // Block obviously dangerous patterns (command injection via path)
    if (/[;&|`$]/.test(filePath)) {
      throw new Error('shell:open-path rejected: path contains shell metacharacters');
    }
    return shell.openPath(filePath);
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
