/**
 * IPC handlers for the file manager — directory listing and file operations.
 *
 * Exposes file browsing to the renderer process via eve.files namespace.
 */

import { ipcMain } from 'electron';
import { filesManager } from '../files-manager';
import { assertString } from './validate';

export function registerFilesHandlers(): void {
  ipcMain.handle('files:list-directory', async (_event, dirPath: unknown) => {
    assertString(dirPath, 'files:list-directory dirPath', 1000);
    return filesManager.listDirectory(dirPath as string);
  });

  ipcMain.handle('files:open', async (_event, filePath: unknown) => {
    assertString(filePath, 'files:open filePath', 1000);
    return filesManager.open(filePath as string);
  });

  ipcMain.handle('files:show-in-folder', (_event, filePath: unknown) => {
    assertString(filePath, 'files:show-in-folder filePath', 1000);
    filesManager.showInFolder(filePath as string);
    return true;
  });
}
