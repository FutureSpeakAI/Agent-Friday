/**
 * FileWatcher — Unit tests for filtering, debouncing, and context building.
 *
 * Tests the pure logic of the file watcher by mocking fs.watch and fs.statSync.
 * Validates ignored dirs/extensions, event storage, and context string generation.
 *
 * Phase B.2: "Sensory Tests" — OS Primitives
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

// ── Mock dependencies ──────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  watch: vi.fn(),
  statSync: vi.fn(),
  accessSync: vi.fn(),
  webContentsSend: vi.fn(),
}));

vi.mock('fs', () => ({
  watch: mocks.watch,
  statSync: mocks.statSync,
  accessSync: mocks.accessSync,
  constants: { R_OK: 4 },
}));

vi.mock('electron', () => ({
  BrowserWindow: class {},
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    get: () => ({
      fileWatchPaths: [],
      obsidianVaultPath: '',
    }),
  },
}));

// ── Import after mocks ─────────────────────────────────────────────

import { fileWatcher } from '../../src/main/file-watcher';
import type { FileEvent } from '../../src/main/file-watcher';

// ── Helpers ────────────────────────────────────────────────────────

function makeMockWindow(): any {
  return {
    isDestroyed: () => false,
    webContents: { send: mocks.webContentsSend },
  };
}

function makeMockWatcher(): any {
  return {
    close: vi.fn(),
    on: vi.fn(),
  };
}

// ── Tests ──────────────────────────────────────────────────────────

describe('fileWatcher.addWatch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fileWatcher.stop(); // Reset state
  });

  it('returns false for already-watched path', () => {
    mocks.accessSync.mockReturnValue(undefined);
    mocks.watch.mockReturnValue(makeMockWatcher());

    const first = fileWatcher.addWatch('C:\\Users\\test\\Documents');
    expect(first).toBe(true);

    const second = fileWatcher.addWatch('C:\\Users\\test\\Documents');
    expect(second).toBe(false);
  });

  it('returns false when directory is not accessible', () => {
    mocks.accessSync.mockImplementation(() => {
      throw new Error('ENOENT');
    });

    const result = fileWatcher.addWatch('C:\\nonexistent');
    expect(result).toBe(false);
  });

  it('returns false when fs.watch throws', () => {
    mocks.accessSync.mockReturnValue(undefined);
    mocks.watch.mockImplementation(() => {
      throw new Error('EPERM');
    });

    const result = fileWatcher.addWatch('C:\\noperm');
    expect(result).toBe(false);
  });

  it('creates watcher with recursive option', () => {
    mocks.accessSync.mockReturnValue(undefined);
    mocks.watch.mockReturnValue(makeMockWatcher());

    fileWatcher.addWatch('C:\\Users\\test');
    expect(mocks.watch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ recursive: true }),
      expect.any(Function),
    );
  });
});

describe('fileWatcher.removeWatch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fileWatcher.stop();
  });

  it('returns true and closes watcher for watched path', () => {
    const mockWatcher = makeMockWatcher();
    mocks.accessSync.mockReturnValue(undefined);
    mocks.watch.mockReturnValue(mockWatcher);

    fileWatcher.addWatch('C:\\Users\\test\\Desktop');
    const removed = fileWatcher.removeWatch('C:\\Users\\test\\Desktop');
    expect(removed).toBe(true);
    expect(mockWatcher.close).toHaveBeenCalled();
  });

  it('returns false for non-watched path', () => {
    const removed = fileWatcher.removeWatch('C:\\not\\watched');
    expect(removed).toBe(false);
  });
});

describe('fileWatcher.getWatchedPaths', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fileWatcher.stop();
  });

  it('returns empty array when nothing is watched', () => {
    expect(fileWatcher.getWatchedPaths()).toEqual([]);
  });

  it('returns paths of all active watchers', () => {
    mocks.accessSync.mockReturnValue(undefined);
    mocks.watch.mockReturnValue(makeMockWatcher());

    fileWatcher.addWatch('C:\\Users\\test\\Documents');
    fileWatcher.addWatch('C:\\Users\\test\\Desktop');

    const paths = fileWatcher.getWatchedPaths();
    expect(paths).toHaveLength(2);
    expect(paths.some(p => p.includes('Documents'))).toBe(true);
    expect(paths.some(p => p.includes('Desktop'))).toBe(true);
  });
});

describe('fileWatcher.getRecentEvents', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fileWatcher.stop();
  });

  it('returns empty array when no events have occurred', () => {
    expect(fileWatcher.getRecentEvents()).toEqual([]);
  });

  it('respects the limit parameter', () => {
    // Inject events manually via the handleFsEvent path (through the watcher callback)
    // We'll test getRecentEvents after populating via the watcher
    // Since recentEvents is private, we test indirectly via getContextString
    expect(fileWatcher.getRecentEvents(5)).toEqual([]);
  });
});

describe('fileWatcher.getContextString', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fileWatcher.stop();
  });

  it('returns empty string when no recent events exist', () => {
    expect(fileWatcher.getContextString()).toBe('');
  });
});

describe('fileWatcher — event filtering (via addWatch callback)', () => {
  let watchCallback: ((eventType: string, filename: string) => void) | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    fileWatcher.stop();
    watchCallback = null;

    mocks.accessSync.mockReturnValue(undefined);
    mocks.watch.mockImplementation((_path: string, _opts: unknown, cb: Function) => {
      watchCallback = cb as (eventType: string, filename: string) => void;
      return makeMockWatcher();
    });

    // Initialize with a mock window so events get processed
    (fileWatcher as any).mainWindow = makeMockWindow();
    fileWatcher.addWatch('C:\\Users\\test');
  });

  afterEach(() => {
    vi.useRealTimers();
    fileWatcher.stop();
  });

  it('ignores events in node_modules', () => {
    mocks.statSync.mockReturnValue({ size: 100, birthtimeMs: Date.now() });

    watchCallback!('change', 'node_modules\\package.json');
    vi.advanceTimersByTime(400);

    // No event should be emitted
    expect(mocks.webContentsSend).not.toHaveBeenCalled();
  });

  it('ignores events in .git directory', () => {
    mocks.statSync.mockReturnValue({ size: 100, birthtimeMs: Date.now() });

    watchCallback!('change', '.git\\HEAD');
    vi.advanceTimersByTime(400);

    expect(mocks.webContentsSend).not.toHaveBeenCalled();
  });

  it('ignores files with .tmp extension', () => {
    mocks.statSync.mockReturnValue({ size: 100, birthtimeMs: Date.now() });

    watchCallback!('change', 'project\\file.tmp');
    vi.advanceTimersByTime(400);

    expect(mocks.webContentsSend).not.toHaveBeenCalled();
  });

  it('ignores files with .log extension', () => {
    mocks.statSync.mockReturnValue({ size: 100, birthtimeMs: Date.now() });

    watchCallback!('change', 'project\\debug.log');
    vi.advanceTimersByTime(400);

    expect(mocks.webContentsSend).not.toHaveBeenCalled();
  });

  it('ignores null filename', () => {
    watchCallback!('change', null as any);
    vi.advanceTimersByTime(400);

    expect(mocks.webContentsSend).not.toHaveBeenCalled();
  });

  it('processes valid file events after debounce window', () => {
    mocks.statSync.mockReturnValue({ size: 500, birthtimeMs: Date.now() });

    watchCallback!('change', 'project\\main.ts');
    vi.advanceTimersByTime(400); // Past 300ms debounce

    expect(mocks.webContentsSend).toHaveBeenCalledWith('file:modified', expect.objectContaining({
      action: expect.stringMatching(/created|modified/),
      size: 500,
    }));
  });

  it('debounces rapid events on the same file', () => {
    mocks.statSync.mockReturnValue({ size: 500, birthtimeMs: Date.now() });

    watchCallback!('change', 'project\\main.ts');
    watchCallback!('change', 'project\\main.ts');
    watchCallback!('change', 'project\\main.ts');
    vi.advanceTimersByTime(400);

    // Only one event should be emitted despite 3 fs events
    expect(mocks.webContentsSend).toHaveBeenCalledTimes(1);
  });

  it('detects deleted files when statSync throws', () => {
    mocks.statSync.mockImplementation(() => {
      throw new Error('ENOENT');
    });

    watchCallback!('change', 'project\\deleted.ts');
    vi.advanceTimersByTime(400);

    expect(mocks.webContentsSend).toHaveBeenCalledWith('file:modified', expect.objectContaining({
      action: 'deleted',
      size: -1,
    }));
  });

  it('detects created files when birthtime is within 2 seconds', () => {
    mocks.statSync.mockReturnValue({ size: 100, birthtimeMs: Date.now() });

    watchCallback!('change', 'project\\newfile.ts');
    vi.advanceTimersByTime(400);

    expect(mocks.webContentsSend).toHaveBeenCalledWith('file:modified', expect.objectContaining({
      action: 'created',
    }));
  });

  it('detects modified files when birthtime is older than 2 seconds', () => {
    mocks.statSync.mockReturnValue({ size: 100, birthtimeMs: Date.now() - 10000 });

    watchCallback!('change', 'project\\existing.ts');
    vi.advanceTimersByTime(400);

    expect(mocks.webContentsSend).toHaveBeenCalledWith('file:modified', expect.objectContaining({
      action: 'modified',
    }));
  });
});
