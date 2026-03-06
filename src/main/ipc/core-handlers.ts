/**
 * Core IPC handlers — API port, API keys, system instruction, settings, MCP, window controls.
 */
import { ipcMain, BrowserWindow, shell } from 'electron';
import { settingsManager } from '../settings';
import { buildGeminiLiveSystemInstruction } from '../personality';
import { mcpClient } from '../mcp-client';
import { memoryManager } from '../memory';
import { assertToolCallArgs, assertString, assertSafePath } from './validate';

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

  // Crypto Sprint 8 (HIGH): Validate key length and cap serialized value size.
  ipcMain.handle('settings:set', async (_event, key: unknown, value: unknown) => {
    assertString(key, 'settings:set key', 256);
    // Cap serialized value size to prevent memory exhaustion
    const serialized = JSON.stringify(value);
    if (serialized && serialized.length > 100_000) {
      throw new Error('settings:set value too large (max 100KB serialized)');
    }
    await settingsManager.setSetting(key as string, value);
  });

  ipcMain.handle('settings:set-auto-launch', async (_event, enabled: boolean) => {
    await settingsManager.setAutoLaunch(enabled);
  });

  ipcMain.handle('settings:set-auto-screen-capture', async (_event, enabled: boolean) => {
    await settingsManager.setAutoScreenCapture(enabled);
  });

  // Crypto Sprint 8 (HIGH): Validate vault path is a safe filesystem path.
  ipcMain.handle(
    'settings:set-obsidian-vault-path',
    async (_event, vaultPath: unknown) => {
      if (vaultPath !== '' && vaultPath !== null && vaultPath !== undefined) {
        assertSafePath(vaultPath, 'settings:set-obsidian-vault-path vaultPath');
      }
      await settingsManager.setObsidianVaultPath(vaultPath as string);
      if (vaultPath) {
        const { syncLongTermToVault, syncMediumTermToVault, ensureVaultStructure } =
          require('../obsidian-memory');
        try {
          await ensureVaultStructure(vaultPath);
          await syncLongTermToVault(vaultPath, memoryManager.getLongTerm());
          await syncMediumTermToVault(vaultPath, memoryManager.getMediumTerm());
          console.log('[Friday] Synced existing memories to Obsidian vault');
        } catch (err) {
          // Crypto Sprint 16: Sanitize — Obsidian sync errors could contain file paths with secrets.
          console.warn('[Friday] Failed to sync memories to vault:', err instanceof Error ? err.message : 'Unknown error');
        }
      }
    },
  );

  ipcMain.handle(
    'settings:set-api-key',
    async (
      _event,
      key: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl' | 'perplexity' | 'openai' | 'openrouter' | 'huggingface',
      value: string,
    ) => {
      await settingsManager.setApiKey(key, value);
    },
  );

  // ── MCP ─────────────────────────────────────────────────────────────
  ipcMain.handle('mcp:list-tools', async () => mcpClient.listTools());

  // Crypto Sprint 8 (CRITICAL): Validate tool name and args before dispatching.
  ipcMain.handle(
    'mcp:call-tool',
    async (_event, toolName: unknown, args: unknown) => {
      const { validatedName, validatedArgs } = assertToolCallArgs(toolName, args, 'mcp:call-tool');
      return mcpClient.callTool(validatedName, validatedArgs);
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
  // Crypto Sprint 16: Upgraded to assertSafePath (adds null-byte check, traversal check,
  // and uses the shared validator from validate.ts instead of ad-hoc regexes).
  ipcMain.handle('shell:show-in-folder', (_event, filePath: unknown) => {
    assertSafePath(filePath, 'shell:show-in-folder filePath');
    shell.showItemInFolder(filePath as string);
  });

  ipcMain.handle('shell:open-path', async (_event, filePath: unknown) => {
    assertSafePath(filePath, 'shell:open-path filePath');
    return shell.openPath(filePath as string);
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
