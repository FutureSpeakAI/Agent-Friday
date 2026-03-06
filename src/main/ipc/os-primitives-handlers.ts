/**
 * IPC handlers for OS primitives — file search, file watcher, and OS events.
 *
 * Exposes file-search queries, file-watcher management, and OS event
 * state (power, displays, file associations) to the renderer process.
 *
 * Phase B.3: "Wiring the Nerves" — IPC Integration
 */

import { ipcMain } from 'electron';
import { fileSearch } from '../file-search';
import { fileWatcher } from '../file-watcher';
import { osEvents } from '../os-events';
import {
  assertString,
  assertObject,
  assertNumber,
  assertSafePath,
  assertStringArray,
} from './validate';

export function registerOsPrimitivesHandlers(): void {
  // ── File Search ──────────────────────────────────────────────────────

  ipcMain.handle('file-search:search', async (_event, query: unknown) => {
    assertObject(query, 'file-search:search query');
    const q = query as Record<string, unknown>;
    assertString(q.query, 'query.query', 10_000);
    if (q.extensions !== undefined) {
      assertStringArray(q.extensions, 'query.extensions', 100, 20);
    }
    if (q.limit !== undefined) {
      assertNumber(q.limit, 'query.limit', 1, 10_000);
    }
    return fileSearch.search(query as any);
  });

  ipcMain.handle('file-search:recent', async (_event, limit?: unknown, extensions?: unknown) => {
    if (limit !== undefined && limit !== null) {
      assertNumber(limit, 'file-search:recent limit', 1, 10_000);
    }
    if (extensions !== undefined && extensions !== null) {
      assertStringArray(extensions, 'file-search:recent extensions', 100, 20);
    }
    return fileSearch.getRecentFiles(
      limit as number | undefined,
      extensions as string[] | undefined,
    );
  });

  ipcMain.handle('file-search:duplicates', async (_event, dirPath: unknown, mode?: unknown) => {
    assertSafePath(dirPath, 'file-search:duplicates dirPath');
    if (mode !== undefined && mode !== null) {
      assertString(mode, 'file-search:duplicates mode', 20);
    }
    return fileSearch.findDuplicates(
      dirPath as string,
      mode as 'name' | 'size' | undefined,
    );
  });

  // ── File Watcher ─────────────────────────────────────────────────────

  ipcMain.handle('file-watcher:add-watch', (_event, dirPath: unknown) => {
    assertSafePath(dirPath, 'file-watcher:add-watch dirPath');
    return fileWatcher.addWatch(dirPath as string);
  });

  ipcMain.handle('file-watcher:remove-watch', (_event, dirPath: unknown) => {
    assertSafePath(dirPath, 'file-watcher:remove-watch dirPath');
    return fileWatcher.removeWatch(dirPath as string);
  });

  ipcMain.handle('file-watcher:get-watched', () => {
    return fileWatcher.getWatchedPaths();
  });

  ipcMain.handle('file-watcher:get-events', (_event, limit?: unknown) => {
    if (limit !== undefined && limit !== null) {
      assertNumber(limit, 'file-watcher:get-events limit', 1, 10_000);
    }
    return fileWatcher.getRecentEvents(limit as number | undefined);
  });

  ipcMain.handle('file-watcher:context', () => {
    return fileWatcher.getContextString();
  });

  // ── OS Events ────────────────────────────────────────────────────────

  ipcMain.handle('os-events:power-state', () => {
    return osEvents.getPowerState();
  });

  ipcMain.handle('os-events:recent', (_event, limit?: unknown) => {
    if (limit !== undefined && limit !== null) {
      assertNumber(limit, 'os-events:recent limit', 1, 10_000);
    }
    return osEvents.getRecentEvents(limit as number | undefined);
  });

  ipcMain.handle('os-events:displays', () => {
    return osEvents.getDisplays();
  });

  ipcMain.handle('os-events:file-association', async (_event, ext: unknown) => {
    assertString(ext, 'os-events:file-association ext', 50);
    return osEvents.getFileAssociation(ext as string);
  });

  ipcMain.handle('os-events:file-associations', async (_event, extensions: unknown) => {
    assertStringArray(extensions, 'os-events:file-associations extensions', 100, 20);
    return osEvents.getFileAssociations(extensions as string[]);
  });

  ipcMain.handle('os-events:open-with-default', async (_event, filePath: unknown) => {
    assertSafePath(filePath, 'os-events:open-with-default filePath');
    return osEvents.openWithDefault(filePath as string);
  });

  ipcMain.handle('os-events:startup-programs', async () => {
    return osEvents.getStartupPrograms();
  });

  ipcMain.handle('os-events:context', () => {
    return osEvents.getContextString();
  });
}
