/**
 * file-watcher.ts — Real-time Filesystem Watcher.
 * Monitors user-configurable directories for file create/modify/delete events.
 * Uses Node.js fs.watch (recursive) on Windows for low-overhead native change detection.
 * Sends events to renderer and builds a context string for AI prompt injection.
 */

import * as fs from 'fs';
import * as path from 'path';
import { BrowserWindow } from 'electron';
import { settingsManager } from './settings';

// ── Types ───────────────────────────────────────────────────────────────────

export interface FileEvent {
  /** Absolute path of the changed file */
  filePath: string;
  /** What happened */
  action: 'created' | 'modified' | 'deleted' | 'renamed';
  /** File extension (lowercase, no dot) */
  extension: string;
  /** File size in bytes (-1 if deleted / unavailable) */
  size: number;
  /** Epoch ms */
  timestamp: number;
  /** Containing directory relative to watch root */
  relativePath: string;
}

interface WatchEntry {
  rootPath: string;
  watcher: fs.FSWatcher | null;
  /** AbortController for graceful teardown */
  abort: AbortController;
}

// Directories that should never be watched (noisy, huge, or OS-internal)
const IGNORED_DIRS = new Set([
  'node_modules', '.git', '.svn', '__pycache__', '.next', '.nuxt',
  'dist', 'build', '.cache', '.tmp', 'temp', 'Temp',
  '$Recycle.Bin', 'System Volume Information', 'AppData',
  '.vscode', '.idea', 'coverage', '.tsbuildinfo',
]);

const IGNORED_EXTENSIONS = new Set([
  'tmp', 'log', 'lock', 'swp', 'swo', 'pyc', 'pyo',
  'DS_Store', 'thumbs.db', 'desktop.ini',
]);

// Debounce window — many editors write in multiple steps
const DEBOUNCE_MS = 300;
// Max recent events to keep in memory
const MAX_RECENT = 100;
// Max events per second before throttling (prevents runaway builds from flooding)
const THROTTLE_MAX_PER_SEC = 20;

// ── Implementation ──────────────────────────────────────────────────────────

class FileWatcher {
  private watchers: WatchEntry[] = [];
  private recentEvents: FileEvent[] = [];
  private mainWindow: BrowserWindow | null = null;
  private debounceMap = new Map<string, ReturnType<typeof setTimeout>>();
  private eventCountThisSecond = 0;
  private throttleResetInterval: ReturnType<typeof setInterval> | null = null;

  // ── Public API ──────────────────────────────────────────────────────

  initialize(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow;
    this.throttleResetInterval = setInterval(() => {
      this.eventCountThisSecond = 0;
    }, 1000);

    // Start watching configured paths
    const paths = this.getWatchPaths();
    for (const p of paths) {
      this.addWatch(p);
    }
    console.log(`[FileWatcher] Initialized — watching ${this.watchers.length} paths`);
  }

  stop(): void {
    for (const entry of this.watchers) {
      entry.abort.abort();
      if (entry.watcher) {
        try { entry.watcher.close(); } catch { /* ignore */ }
      }
    }
    this.watchers = [];
    for (const timer of this.debounceMap.values()) clearTimeout(timer);
    this.debounceMap.clear();
    if (this.throttleResetInterval) {
      clearInterval(this.throttleResetInterval);
      this.throttleResetInterval = null;
    }
    console.log('[FileWatcher] Stopped');
  }

  /** Add a new directory to watch at runtime */
  addWatch(dirPath: string): boolean {
    // Normalize and validate
    const resolved = path.resolve(dirPath);
    if (this.watchers.some(w => w.rootPath === resolved)) return false; // already watching

    try {
      fs.accessSync(resolved, fs.constants.R_OK);
    } catch {
      console.warn(`[FileWatcher] Cannot access "${resolved}", skipping`);
      return false;
    }

    const abort = new AbortController();
    let watcher: fs.FSWatcher | null = null;

    try {
      // Windows supports recursive fs.watch natively (uses ReadDirectoryChangesW)
      watcher = fs.watch(resolved, {
        recursive: true,
        signal: abort.signal,
      }, (eventType, filename) => {
        if (!filename) return;
        this.handleFsEvent(resolved, eventType, filename);
      });

      watcher.on('error', (err) => {
        console.warn(`[FileWatcher] Watcher error for "${resolved}":`, err.message);
      });
    } catch (err) {
      console.warn(`[FileWatcher] Failed to watch "${resolved}":`, err instanceof Error ? err.message : err);
      return false;
    }

    this.watchers.push({ rootPath: resolved, watcher, abort });
    console.log(`[FileWatcher] Now watching: ${resolved}`);
    return true;
  }

  /** Remove a watched directory */
  removeWatch(dirPath: string): boolean {
    const resolved = path.resolve(dirPath);
    const idx = this.watchers.findIndex(w => w.rootPath === resolved);
    if (idx === -1) return false;

    const entry = this.watchers[idx];
    entry.abort.abort();
    if (entry.watcher) {
      try { entry.watcher.close(); } catch { /* ignore */ }
    }
    this.watchers.splice(idx, 1);
    console.log(`[FileWatcher] Stopped watching: ${resolved}`);
    return true;
  }

  /** Get list of currently watched directories */
  getWatchedPaths(): string[] {
    return this.watchers.map(w => w.rootPath);
  }

  /** Get recent file events */
  getRecentEvents(limit = 20): FileEvent[] {
    return this.recentEvents.slice(-limit);
  }

  /** Build context string for AI prompt injection */
  getContextString(): string {
    const cutoff = Date.now() - 5 * 60_000; // Last 5 minutes
    const recent = this.recentEvents.filter(e => e.timestamp > cutoff);
    if (recent.length === 0) return '';

    const lines: string[] = ['## Recent File Activity'];

    // Group by action
    const created = recent.filter(e => e.action === 'created');
    const modified = recent.filter(e => e.action === 'modified');
    const deleted = recent.filter(e => e.action === 'deleted');

    if (created.length > 0) {
      lines.push(`**Created (${created.length}):** ${created.slice(-5).map(e => path.basename(e.filePath)).join(', ')}`);
    }
    if (modified.length > 0) {
      lines.push(`**Modified (${modified.length}):** ${modified.slice(-5).map(e => path.basename(e.filePath)).join(', ')}`);
    }
    if (deleted.length > 0) {
      lines.push(`**Deleted (${deleted.length}):** ${deleted.slice(-5).map(e => path.basename(e.filePath)).join(', ')}`);
    }

    return lines.join('\n');
  }

  // ── Internals ───────────────────────────────────────────────────────

  private getWatchPaths(): string[] {
    const settings = settingsManager.get();
    const paths: string[] = [];

    // Default watch paths — user's common directories
    const home = process.env.USERPROFILE || process.env.HOME || '';
    if (home) {
      const defaults = ['Desktop', 'Documents', 'Downloads'];
      for (const d of defaults) {
        const full = path.join(home, d);
        try {
          fs.accessSync(full, fs.constants.R_OK);
          paths.push(full);
        } catch { /* skip if inaccessible */ }
      }
    }

    // User-configured additional paths
    const custom = settings.fileWatchPaths;
    if (Array.isArray(custom)) {
      for (const p of custom) {
        if (typeof p === 'string' && p.trim()) paths.push(p.trim());
      }
    }

    // Obsidian vault
    if (settings.obsidianVaultPath) {
      paths.push(settings.obsidianVaultPath);
    }

    return [...new Set(paths)]; // deduplicate
  }

  private handleFsEvent(rootPath: string, eventType: string, filename: string): void {
    const fullPath = path.join(rootPath, filename);

    // Filter ignored directories
    const parts = filename.split(path.sep);
    if (parts.some(p => IGNORED_DIRS.has(p))) return;

    // Filter ignored extensions
    const ext = path.extname(filename).slice(1).toLowerCase();
    if (IGNORED_EXTENSIONS.has(ext)) return;

    // Throttle — prevent flood from build tools
    if (this.eventCountThisSecond >= THROTTLE_MAX_PER_SEC) return;

    // Debounce — same file within 300ms collapses to one event
    const debounceKey = fullPath;
    const existing = this.debounceMap.get(debounceKey);
    if (existing) clearTimeout(existing);

    this.debounceMap.set(debounceKey, setTimeout(() => {
      this.debounceMap.delete(debounceKey);
      this.emitEvent(rootPath, fullPath, ext);
    }, DEBOUNCE_MS));
  }

  private emitEvent(rootPath: string, fullPath: string, ext: string): void {
    // Determine action by checking if file exists
    let action: FileEvent['action'];
    let size = -1;

    try {
      const stat = fs.statSync(fullPath);
      size = stat.size;
      // If file was recently created (within last 2 seconds) and is small, likely "created"
      const age = Date.now() - stat.birthtimeMs;
      action = age < 2000 ? 'created' : 'modified';
    } catch {
      action = 'deleted';
    }

    const event: FileEvent = {
      filePath: fullPath,
      action,
      extension: ext,
      size,
      timestamp: Date.now(),
      relativePath: path.relative(rootPath, fullPath),
    };

    this.eventCountThisSecond++;

    // Store
    this.recentEvents.push(event);
    if (this.recentEvents.length > MAX_RECENT) {
      this.recentEvents = this.recentEvents.slice(-MAX_RECENT);
    }

    // Forward to renderer
    if (this.mainWindow && !this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send('file:modified', {
        path: event.filePath,
        action: event.action,
        size: event.size,
        timestamp: event.timestamp,
      });
    }
  }
}

export const fileWatcher = new FileWatcher();
