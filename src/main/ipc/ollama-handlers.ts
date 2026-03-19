/**
 * Sprint 7: IPC handlers for Ollama lifecycle management.
 *
 * Exposes OllamaLifecycle to the renderer via eve.ollama namespace.
 * pullModel uses streaming (AsyncGenerator) — we collect progress events
 * and forward them to the renderer via webContents.send().
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { OllamaLifecycle } from '../ollama-lifecycle';
import { assertString } from './validate';

export interface OllamaHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerOllamaHandlers(deps: OllamaHandlerDeps): void {
  const ollama = OllamaLifecycle.getInstance();

  ipcMain.handle('ollama:start', async () => {
    return ollama.start();
  });

  ipcMain.handle('ollama:stop', () => {
    ollama.stop();
  });

  ipcMain.handle('ollama:get-health', async () => {
    return ollama.getHealthAsync();
  });

  ipcMain.handle('ollama:get-available-models', () => {
    return ollama.getAvailableModels();
  });

  ipcMain.handle('ollama:get-loaded-models', () => {
    return ollama.getLoadedModels();
  });

  ipcMain.handle('ollama:is-model-available', (_event, name: unknown) => {
    assertString(name, 'ollama:is-model-available name', 256);
    return ollama.isModelAvailable(name as string);
  });

  /**
   * Pull a model with streaming progress.
   * Since IPC can't stream AsyncGenerator, we forward progress events
   * to the renderer and return a completion promise.
   */
  ipcMain.handle('ollama:pull-model', async (_event, name: unknown) => {
    assertString(name, 'ollama:pull-model name', 256);
    const win = deps.getMainWindow();
    try {
      for await (const progress of ollama.pullModel(name as string)) {
        win?.webContents.send('ollama:event:pull-progress', {
          modelName: name,
          ...progress,
        });
      }
      return { success: true, modelName: name };
    } catch (err) {
      return {
        success: false,
        modelName: name,
        error: err instanceof Error ? err.message : 'Unknown error',
      };
    }
  });

  // ── Event forwarding to renderer ──────────────────────────────────

  ollama.on('healthy', () => {
    deps.getMainWindow()?.webContents.send('ollama:event:healthy');
  });

  ollama.on('unhealthy', () => {
    deps.getMainWindow()?.webContents.send('ollama:event:unhealthy');
  });

  ollama.on('health-change', (data) => {
    deps.getMainWindow()?.webContents.send('ollama:event:health-change', data);
  });

  ollama.on('model-loaded', (data) => {
    deps.getMainWindow()?.webContents.send('ollama:event:model-loaded', data);
  });

  ollama.on('model-unloaded', (data) => {
    deps.getMainWindow()?.webContents.send('ollama:event:model-unloaded', data);
  });
}
