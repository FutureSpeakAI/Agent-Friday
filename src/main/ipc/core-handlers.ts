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

  // Validate API keys from the main process (avoids renderer CORS blocks).
  ipcMain.handle(
    'settings:validate-api-key',
    async (_event, keyType: unknown, value: unknown) => {
      assertString(keyType, 'settings:validate-api-key keyType', 50);
      assertString(value, 'settings:validate-api-key value', 500);
      const type = keyType as string;
      const key = (value as string).trim();
      if (!key) return { valid: false, error: 'Key is empty' };

      try {
        if (type === 'gemini') {
          if (!key.startsWith('AIza')) return { valid: false, error: 'Gemini keys start with "AIza"' };
          const resp = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(key)}`,
            { method: 'GET', signal: AbortSignal.timeout(8000) },
          );
          if (resp.ok) return { valid: true };
          if (resp.status === 400 || resp.status === 403 || resp.status === 401)
            return { valid: false, error: 'API key is invalid or has been revoked' };
          return { valid: false, error: `Unexpected response (${resp.status})` };
        }

        if (type === 'anthropic') {
          if (!key.startsWith('sk-ant-')) return { valid: false, error: 'Anthropic keys start with "sk-ant-"' };
          const resp = await fetch('https://api.anthropic.com/v1/messages', {
            method: 'POST',
            headers: {
              'x-api-key': key,
              'anthropic-version': '2023-06-01',
              'content-type': 'application/json',
            },
            body: JSON.stringify({
              model: 'claude-haiku-4-5-20251001',
              max_tokens: 1,
              messages: [{ role: 'user', content: 'hi' }],
            }),
            signal: AbortSignal.timeout(8000),
          });
          if (resp.ok || resp.status === 400 || resp.status === 429) return { valid: true };
          if (resp.status === 401) return { valid: false, error: 'API key is invalid or has been revoked' };
          if (resp.status === 403) return { valid: false, error: 'API key lacks required permissions' };
          return { valid: false, error: `Unexpected response (${resp.status})` };
        }

        if (type === 'openrouter') {
          const resp = await fetch('https://openrouter.ai/api/v1/auth/key', {
            headers: { Authorization: `Bearer ${key}` },
            signal: AbortSignal.timeout(8000),
          });
          if (resp.ok) return { valid: true };
          if (resp.status === 401 || resp.status === 403)
            return { valid: false, error: 'API key is invalid' };
          return { valid: false, error: `Unexpected response (${resp.status})` };
        }

        // No validator for this key type — accept it
        return { valid: true };
      } catch (err: any) {
        if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
          return { valid: false, error: 'Validation timed out — check your network' };
        }
        return { valid: false, error: 'Could not reach API servers — check your connection' };
      }
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
