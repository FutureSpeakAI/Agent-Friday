/**
 * files-manager.ts — File browser backend for Agent Friday.
 *
 * Provides directory listing, file open, and show-in-folder operations.
 * Resolves ~ paths to the user's home directory on all platforms.
 *
 * Contract consumed by FridayFiles.tsx via eve.files namespace.
 */

import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import { shell } from 'electron';

/* ── Types ────────────────────────────────────────────────────────────── */

export interface FileEntry {
  name: string;
  isDirectory: boolean;
  size: number;
  modifiedAt: string;
}

/* ── Helpers ──────────────────────────────────────────────────────────── */

function resolveTilde(p: string): string {
  if (p === '~' || p === '~/') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

/* ── Public API ───────────────────────────────────────────────────────── */

export const filesManager = {
  /**
   * List directory contents, sorted directories-first then alphabetical.
   * Returns FileEntry[] with name, isDirectory, size, modifiedAt.
   */
  async listDirectory(dirPath: string): Promise<FileEntry[]> {
    const resolved = resolveTilde(dirPath);
    const entries = await fs.readdir(resolved, { withFileTypes: true });

    const results: FileEntry[] = [];
    for (const entry of entries) {
      // Skip hidden files/dirs (starting with .)
      if (entry.name.startsWith('.')) continue;

      try {
        const fullPath = path.join(resolved, entry.name);
        const stat = await fs.stat(fullPath);
        results.push({
          name: entry.name,
          isDirectory: entry.isDirectory(),
          size: entry.isDirectory() ? 0 : stat.size,
          modifiedAt: stat.mtime.toISOString(),
        });
      } catch {
        // Skip files we can't stat (permission denied, etc.)
      }
    }

    // Sort: directories first, then alphabetical
    results.sort((a, b) => {
      if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
      return a.name.localeCompare(b.name);
    });

    return results;
  },

  /**
   * Open a file or folder with the system default application.
   */
  async open(filePath: string): Promise<string> {
    const resolved = resolveTilde(filePath);
    return shell.openPath(resolved);
  },

  /**
   * Show a file in its containing folder (Explorer/Finder).
   */
  showInFolder(filePath: string): void {
    const resolved = resolveTilde(filePath);
    shell.showItemInFolder(resolved);
  },
};
