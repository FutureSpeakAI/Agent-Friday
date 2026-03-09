/**
 * files-manager.ts — File browser backend for Agent Friday.
 *
 * Provides directory listing, file open, and show-in-folder operations.
 * Resolves ~ paths to the user's home directory on all platforms.
 *
 * Contract consumed by FridayFiles.tsx via eve.files namespace.
 */

import fs from 'fs/promises';
import fsCb from 'fs';
import path from 'path';
import os from 'os';
import { shell, clipboard } from 'electron';

/* ── Types ────────────────────────────────────────────────────────────── */

export interface FileEntry {
  name: string;
  isDirectory: boolean;
  size: number;
  modifiedAt: string;
  createdAt?: string;
  extension?: string;
}

export interface FileStats {
  name: string;
  path: string;
  isDirectory: boolean;
  size: number;
  createdAt: string;
  modifiedAt: string;
  accessedAt: string;
  extension: string;
  permissions: string;
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
  async listDirectory(dirPath: string, showHidden = false): Promise<FileEntry[]> {
    const resolved = resolveTilde(dirPath);
    const entries = await fs.readdir(resolved, { withFileTypes: true });

    const results: FileEntry[] = [];
    for (const entry of entries) {
      // Skip hidden files/dirs (starting with .) unless requested
      if (!showHidden && entry.name.startsWith('.')) continue;

      try {
        const fullPath = path.join(resolved, entry.name);
        const stat = await fs.stat(fullPath);
        results.push({
          name: entry.name,
          isDirectory: entry.isDirectory(),
          size: entry.isDirectory() ? 0 : stat.size,
          modifiedAt: stat.mtime.toISOString(),
          createdAt: stat.birthtime.toISOString(),
          extension: entry.isDirectory() ? '' : path.extname(entry.name).toLowerCase(),
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

  /**
   * Get detailed stats for a file or directory.
   */
  async getStats(filePath: string): Promise<FileStats> {
    const resolved = resolveTilde(filePath);
    const stat = await fs.stat(resolved);
    const parsed = path.parse(resolved);
    return {
      name: parsed.base,
      path: resolved,
      isDirectory: stat.isDirectory(),
      size: stat.size,
      createdAt: stat.birthtime.toISOString(),
      modifiedAt: stat.mtime.toISOString(),
      accessedAt: stat.atime.toISOString(),
      extension: parsed.ext.toLowerCase(),
      permissions: (stat.mode & 0o777).toString(8),
    };
  },

  /**
   * Rename a file or directory. Returns the new full path.
   */
  async rename(filePath: string, newName: string): Promise<string> {
    const resolved = resolveTilde(filePath);
    const dir = path.dirname(resolved);
    const newPath = path.join(dir, newName);

    // Safety: don't overwrite existing files
    if (fsCb.existsSync(newPath)) {
      throw new Error(`A file or folder named "${newName}" already exists`);
    }
    await fs.rename(resolved, newPath);
    return newPath;
  },

  /**
   * Delete a file or empty directory. Moves to trash by default.
   */
  async delete(filePath: string, useTrash = true): Promise<void> {
    const resolved = resolveTilde(filePath);
    if (useTrash) {
      await shell.trashItem(resolved);
    } else {
      const stat = await fs.stat(resolved);
      if (stat.isDirectory()) {
        await fs.rm(resolved, { recursive: true });
      } else {
        await fs.unlink(resolved);
      }
    }
  },

  /**
   * Copy a file or directory to a destination folder.
   * Returns the destination path.
   */
  async copy(srcPath: string, destDir: string): Promise<string> {
    const resolvedSrc = resolveTilde(srcPath);
    const resolvedDest = resolveTilde(destDir);
    const baseName = path.basename(resolvedSrc);
    let destPath = path.join(resolvedDest, baseName);

    // Auto-rename on conflict: "file (2).txt", "file (3).txt", etc.
    destPath = await getUniqueDestPath(destPath);

    const stat = await fs.stat(resolvedSrc);
    if (stat.isDirectory()) {
      await copyDirRecursive(resolvedSrc, destPath);
    } else {
      await fs.copyFile(resolvedSrc, destPath);
    }
    return destPath;
  },

  /**
   * Move a file or directory to a destination folder.
   * Returns the destination path.
   */
  async move(srcPath: string, destDir: string): Promise<string> {
    const resolvedSrc = resolveTilde(srcPath);
    const resolvedDest = resolveTilde(destDir);
    const baseName = path.basename(resolvedSrc);
    let destPath = path.join(resolvedDest, baseName);

    // Auto-rename on conflict
    destPath = await getUniqueDestPath(destPath);

    await fs.rename(resolvedSrc, destPath).catch(async () => {
      // rename() fails across filesystems — fall back to copy + delete
      const stat = await fs.stat(resolvedSrc);
      if (stat.isDirectory()) {
        await copyDirRecursive(resolvedSrc, destPath);
      } else {
        await fs.copyFile(resolvedSrc, destPath);
      }
      await fs.rm(resolvedSrc, { recursive: true });
    });

    return destPath;
  },

  /**
   * Create a new directory. Returns the created path.
   */
  async createFolder(parentDir: string, folderName: string): Promise<string> {
    const resolved = resolveTilde(parentDir);
    const newPath = path.join(resolved, folderName);
    await fs.mkdir(newPath, { recursive: false });
    return newPath;
  },

  /**
   * Create a new empty file. Returns the created path.
   */
  async createFile(parentDir: string, fileName: string): Promise<string> {
    const resolved = resolveTilde(parentDir);
    const newPath = path.join(resolved, fileName);

    if (fsCb.existsSync(newPath)) {
      throw new Error(`A file named "${fileName}" already exists`);
    }
    await fs.writeFile(newPath, '', 'utf-8');
    return newPath;
  },

  /**
   * Read file contents as text (for preview). Limited to 1MB.
   */
  async readText(filePath: string, maxBytes = 1_048_576): Promise<string> {
    const resolved = resolveTilde(filePath);
    const stat = await fs.stat(resolved);
    if (stat.size > maxBytes) {
      // Read only the first maxBytes
      const fd = await fs.open(resolved, 'r');
      const buf = Buffer.alloc(maxBytes);
      await fd.read(buf, 0, maxBytes, 0);
      await fd.close();
      return buf.toString('utf-8') + '\n… [truncated]';
    }
    return fs.readFile(resolved, 'utf-8');
  },

  /**
   * Copy a file path to clipboard.
   */
  copyPathToClipboard(filePath: string): void {
    const resolved = resolveTilde(filePath);
    clipboard.writeText(resolved);
  },

  /**
   * Get the user's home directory.
   */
  getHomeDir(): string {
    return os.homedir();
  },

  /**
   * Check if a path exists.
   */
  async exists(filePath: string): Promise<boolean> {
    try {
      const resolved = resolveTilde(filePath);
      await fs.access(resolved);
      return true;
    } catch {
      return false;
    }
  },
};

/* ── Internal helpers ────────────────────────────────────────────────── */

/**
 * Generate a unique destination path when a conflict exists.
 * "file.txt" → "file (2).txt" → "file (3).txt", etc.
 */
async function getUniqueDestPath(destPath: string): Promise<string> {
  if (!fsCb.existsSync(destPath)) return destPath;

  const dir = path.dirname(destPath);
  const ext = path.extname(destPath);
  const base = path.basename(destPath, ext);

  for (let i = 2; i <= 999; i++) {
    const candidate = path.join(dir, `${base} (${i})${ext}`);
    if (!fsCb.existsSync(candidate)) return candidate;
  }
  throw new Error('Too many files with the same name');
}

/**
 * Recursively copy a directory.
 */
async function copyDirRecursive(src: string, dest: string): Promise<void> {
  await fs.mkdir(dest, { recursive: true });
  const entries = await fs.readdir(src, { withFileTypes: true });

  for (const entry of entries) {
    const srcChild = path.join(src, entry.name);
    const destChild = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      await copyDirRecursive(srcChild, destChild);
    } else {
      await fs.copyFile(srcChild, destChild);
    }
  }
}
