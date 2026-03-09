/**
 * IPC handlers for the file manager — directory listing and file operations.
 *
 * Exposes file browsing and management to the renderer process via eve.files namespace.
 * All inputs are validated to prevent path traversal, injection, and other attacks.
 */

import { ipcMain } from 'electron';
import { filesManager } from '../files-manager';
import { assertString, assertSafePath, assertBoolean } from './validate';

/**
 * Validate a filename (no path separators, no traversal).
 */
function assertFileName(value: unknown, name: string): asserts value is string {
  assertString(value, name, 255);
  const s = value as string;
  if (s.includes('/') || s.includes('\\') || s.includes('..') || s.includes('\0')) {
    throw new Error(`${name} contains invalid characters`);
  }
  if (s === '.' || s === '..') {
    throw new Error(`${name} is not a valid file name`);
  }
}

export function registerFilesHandlers(): void {
  /* ── Browse ──────────────────────────────────────────────────────── */

  ipcMain.handle('files:list-directory', async (_event, dirPath: unknown, showHidden?: unknown) => {
    assertSafePath(dirPath, 'files:list-directory dirPath', 1000);
    const hidden = typeof showHidden === 'boolean' ? showHidden : false;
    return filesManager.listDirectory(dirPath as string, hidden);
  });

  ipcMain.handle('files:get-stats', async (_event, filePath: unknown) => {
    assertSafePath(filePath, 'files:get-stats filePath', 1000);
    return filesManager.getStats(filePath as string);
  });

  ipcMain.handle('files:exists', async (_event, filePath: unknown) => {
    assertSafePath(filePath, 'files:exists filePath', 1000);
    return filesManager.exists(filePath as string);
  });

  ipcMain.handle('files:read-text', async (_event, filePath: unknown) => {
    assertSafePath(filePath, 'files:read-text filePath', 1000);
    return filesManager.readText(filePath as string);
  });

  /* ── Open / Reveal ──────────────────────────────────────────────── */

  ipcMain.handle('files:open', async (_event, filePath: unknown) => {
    assertSafePath(filePath, 'files:open filePath', 1000);
    return filesManager.open(filePath as string);
  });

  ipcMain.handle('files:show-in-folder', (_event, filePath: unknown) => {
    assertSafePath(filePath, 'files:show-in-folder filePath', 1000);
    filesManager.showInFolder(filePath as string);
    return true;
  });

  /* ── Rename ─────────────────────────────────────────────────────── */

  ipcMain.handle('files:rename', async (_event, filePath: unknown, newName: unknown) => {
    assertSafePath(filePath, 'files:rename filePath', 1000);
    assertFileName(newName, 'files:rename newName');
    return filesManager.rename(filePath as string, newName as string);
  });

  /* ── Delete (moves to trash by default) ─────────────────────────── */

  ipcMain.handle('files:delete', async (_event, filePath: unknown, useTrash?: unknown) => {
    assertSafePath(filePath, 'files:delete filePath', 1000);
    const trash = typeof useTrash === 'boolean' ? useTrash : true;
    await filesManager.delete(filePath as string, trash);
    return true;
  });

  /* ── Copy / Move ────────────────────────────────────────────────── */

  ipcMain.handle('files:copy', async (_event, srcPath: unknown, destDir: unknown) => {
    assertSafePath(srcPath, 'files:copy srcPath', 1000);
    assertSafePath(destDir, 'files:copy destDir', 1000);
    return filesManager.copy(srcPath as string, destDir as string);
  });

  ipcMain.handle('files:move', async (_event, srcPath: unknown, destDir: unknown) => {
    assertSafePath(srcPath, 'files:move srcPath', 1000);
    assertSafePath(destDir, 'files:move destDir', 1000);
    return filesManager.move(srcPath as string, destDir as string);
  });

  /* ── Create ─────────────────────────────────────────────────────── */

  ipcMain.handle('files:create-folder', async (_event, parentDir: unknown, folderName: unknown) => {
    assertSafePath(parentDir, 'files:create-folder parentDir', 1000);
    assertFileName(folderName, 'files:create-folder folderName');
    return filesManager.createFolder(parentDir as string, folderName as string);
  });

  ipcMain.handle('files:create-file', async (_event, parentDir: unknown, fileName: unknown) => {
    assertSafePath(parentDir, 'files:create-file parentDir', 1000);
    assertFileName(fileName, 'files:create-file fileName');
    return filesManager.createFile(parentDir as string, fileName as string);
  });

  /* ── Clipboard ──────────────────────────────────────────────────── */

  ipcMain.handle('files:copy-path', (_event, filePath: unknown) => {
    assertSafePath(filePath, 'files:copy-path filePath', 1000);
    filesManager.copyPathToClipboard(filePath as string);
    return true;
  });

  /* ── Home directory ─────────────────────────────────────────────── */

  ipcMain.handle('files:home-dir', () => {
    return filesManager.getHomeDir();
  });
}
