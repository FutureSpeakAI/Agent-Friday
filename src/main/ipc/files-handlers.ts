/**
 * IPC handlers for the file manager — directory listing and file operations.
 *
 * Exposes file browsing and management to the renderer process via eve.files namespace.
 * All inputs are validated to prevent path traversal, injection, and other attacks.
 */

import { ipcMain } from 'electron';
import os from 'os';
import { filesManager } from '../files-manager';
import { assertString, assertSafePath, assertConfinedPath } from './validate';

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
  // File system confinement: all paths must resolve within the user's home directory.
  // assertConfinedPath calls assertSafePath internally (blocks traversal, UNC, shell chars)
  // then verifies the resolved path stays within the base directory.
  const homeDir = os.homedir();

  /* ── Browse ──────────────────────────────────────────────────────── */

  ipcMain.handle('files:list-directory', async (_event, dirPath: unknown, showHidden?: unknown) => {
    const confined = assertConfinedPath(dirPath, 'files:list-directory dirPath', homeDir, 1000);
    const hidden = typeof showHidden === 'boolean' ? showHidden : false;
    return filesManager.listDirectory(confined, hidden);
  });

  ipcMain.handle('files:get-stats', async (_event, filePath: unknown) => {
    const confined = assertConfinedPath(filePath, 'files:get-stats filePath', homeDir, 1000);
    return filesManager.getStats(confined);
  });

  ipcMain.handle('files:exists', async (_event, filePath: unknown) => {
    const confined = assertConfinedPath(filePath, 'files:exists filePath', homeDir, 1000);
    return filesManager.exists(confined);
  });

  ipcMain.handle('files:read-text', async (_event, filePath: unknown) => {
    const confined = assertConfinedPath(filePath, 'files:read-text filePath', homeDir, 1000);
    return filesManager.readText(confined);
  });

  /* ── Open / Reveal ──────────────────────────────────────────────── */

  ipcMain.handle('files:open', async (_event, filePath: unknown) => {
    const confined = assertConfinedPath(filePath, 'files:open filePath', homeDir, 1000);
    return filesManager.open(confined);
  });

  ipcMain.handle('files:show-in-folder', (_event, filePath: unknown) => {
    const confined = assertConfinedPath(filePath, 'files:show-in-folder filePath', homeDir, 1000);
    filesManager.showInFolder(confined);
    return true;
  });

  /* ── Rename ─────────────────────────────────────────────────────── */

  ipcMain.handle('files:rename', async (_event, filePath: unknown, newName: unknown) => {
    const confined = assertConfinedPath(filePath, 'files:rename filePath', homeDir, 1000);
    assertFileName(newName, 'files:rename newName');
    return filesManager.rename(confined, newName as string);
  });

  /* ── Delete (moves to trash by default) ─────────────────────────── */

  ipcMain.handle('files:delete', async (_event, filePath: unknown, useTrash?: unknown) => {
    const confined = assertConfinedPath(filePath, 'files:delete filePath', homeDir, 1000);
    const trash = typeof useTrash === 'boolean' ? useTrash : true;
    await filesManager.delete(confined, trash);
    return true;
  });

  /* ── Copy / Move ────────────────────────────────────────────────── */

  ipcMain.handle('files:copy', async (_event, srcPath: unknown, destDir: unknown) => {
    const confinedSrc = assertConfinedPath(srcPath, 'files:copy srcPath', homeDir, 1000);
    const confinedDest = assertConfinedPath(destDir, 'files:copy destDir', homeDir, 1000);
    return filesManager.copy(confinedSrc, confinedDest);
  });

  ipcMain.handle('files:move', async (_event, srcPath: unknown, destDir: unknown) => {
    const confinedSrc = assertConfinedPath(srcPath, 'files:move srcPath', homeDir, 1000);
    const confinedDest = assertConfinedPath(destDir, 'files:move destDir', homeDir, 1000);
    return filesManager.move(confinedSrc, confinedDest);
  });

  /* ── Create ─────────────────────────────────────────────────────── */

  ipcMain.handle('files:create-folder', async (_event, parentDir: unknown, folderName: unknown) => {
    const confined = assertConfinedPath(parentDir, 'files:create-folder parentDir', homeDir, 1000);
    assertFileName(folderName, 'files:create-folder folderName');
    return filesManager.createFolder(confined, folderName as string);
  });

  ipcMain.handle('files:create-file', async (_event, parentDir: unknown, fileName: unknown) => {
    const confined = assertConfinedPath(parentDir, 'files:create-file parentDir', homeDir, 1000);
    assertFileName(fileName, 'files:create-file fileName');
    return filesManager.createFile(confined, fileName as string);
  });

  /* ── Clipboard ──────────────────────────────────────────────────── */

  ipcMain.handle('files:copy-path', (_event, filePath: unknown) => {
    const confined = assertConfinedPath(filePath, 'files:copy-path filePath', homeDir, 1000);
    filesManager.copyPathToClipboard(confined);
    return true;
  });

  /* ── Home directory ─────────────────────────────────────────────── */

  ipcMain.handle('files:home-dir', () => {
    return filesManager.getHomeDir();
  });
}
