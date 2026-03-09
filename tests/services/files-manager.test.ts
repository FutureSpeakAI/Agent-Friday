/**
 * files-manager.ts — Unit tests for file browser backend.
 *
 * Tests directory listing, tilde resolution, sorting, and shell operations
 * by mocking fs, os, and Electron shell.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mocks ──────────────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  readdir: vi.fn(),
  stat: vi.fn(),
  openPath: vi.fn(),
  showItemInFolder: vi.fn(),
  homedir: vi.fn(),
}));

vi.mock('fs/promises', () => ({
  default: {
    readdir: mocks.readdir,
    stat: mocks.stat,
  },
  readdir: mocks.readdir,
  stat: mocks.stat,
}));

vi.mock('os', () => ({
  default: { homedir: mocks.homedir },
  homedir: mocks.homedir,
}));

vi.mock('electron', () => ({
  shell: {
    openPath: mocks.openPath,
    showItemInFolder: mocks.showItemInFolder,
  },
}));

// ── Import (after mocks) ──────────────────────────────────────────

import { filesManager } from '../../src/main/files-manager';

// ── Helpers ────────────────────────────────────────────────────────

interface MockDirent {
  name: string;
  isDirectory: () => boolean;
}

function makeDirent(name: string, isDir: boolean): MockDirent {
  return { name, isDirectory: () => isDir };
}

function mockStat(size: number, mtime: Date): void {
  mocks.stat.mockResolvedValueOnce({ size, mtime, birthtime: mtime });
}

// ── Tests ──────────────────────────────────────────────────────────

describe('filesManager.listDirectory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.homedir.mockReturnValue('C:\\Users\\test');
  });

  it('lists directory entries sorted dirs-first then alphabetical', async () => {
    mocks.readdir.mockResolvedValue([
      makeDirent('zebra.txt', false),
      makeDirent('Projects', true),
      makeDirent('alpha.md', false),
      makeDirent('Apps', true),
    ]);

    const mtime = new Date('2024-06-01T12:00:00Z');
    // stat is called per entry, in order of readdir results
    mockStat(100, mtime);   // zebra.txt
    mockStat(0, mtime);     // Projects
    mockStat(200, mtime);   // alpha.md
    mockStat(0, mtime);     // Apps

    const results = await filesManager.listDirectory('C:\\Users\\test\\Documents');
    expect(results).toHaveLength(4);

    // Directories first (alphabetical)
    expect(results[0].name).toBe('Apps');
    expect(results[0].isDirectory).toBe(true);
    expect(results[1].name).toBe('Projects');
    expect(results[1].isDirectory).toBe(true);

    // Files next (alphabetical)
    expect(results[2].name).toBe('alpha.md');
    expect(results[2].isDirectory).toBe(false);
    expect(results[3].name).toBe('zebra.txt');
    expect(results[3].isDirectory).toBe(false);
  });

  it('skips hidden files (names starting with dot)', async () => {
    mocks.readdir.mockResolvedValue([
      makeDirent('.gitconfig', false),
      makeDirent('.hidden', true),
      makeDirent('visible.txt', false),
    ]);

    mockStat(50, new Date());

    const results = await filesManager.listDirectory('/some/path');
    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('visible.txt');
  });

  it('skips entries that fail to stat', async () => {
    mocks.readdir.mockResolvedValue([
      makeDirent('good.txt', false),
      makeDirent('broken.txt', false),
    ]);

    mockStat(100, new Date());                      // good.txt succeeds
    mocks.stat.mockRejectedValueOnce(new Error('EACCES')); // broken.txt fails

    const results = await filesManager.listDirectory('/path');
    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('good.txt');
  });

  it('reports size as 0 for directories', async () => {
    mocks.readdir.mockResolvedValue([
      makeDirent('MyFolder', true),
    ]);
    mockStat(4096, new Date());

    const results = await filesManager.listDirectory('/path');
    expect(results[0].size).toBe(0);
    expect(results[0].isDirectory).toBe(true);
  });

  it('includes modifiedAt as ISO string', async () => {
    const mtime = new Date('2024-08-15T09:30:00.000Z');
    mocks.readdir.mockResolvedValue([
      makeDirent('report.pdf', false),
    ]);
    mockStat(1024, mtime);

    const results = await filesManager.listDirectory('/path');
    expect(results[0].modifiedAt).toBe('2024-08-15T09:30:00.000Z');
  });
});

describe('filesManager.listDirectory — tilde resolution', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.homedir.mockReturnValue('C:\\Users\\test');
    mocks.readdir.mockResolvedValue([]);
  });

  it('resolves ~ to home directory', async () => {
    await filesManager.listDirectory('~');
    expect(mocks.readdir).toHaveBeenCalledWith(
      'C:\\Users\\test',
      expect.any(Object),
    );
  });

  it('resolves ~/ prefix to home directory subpath', async () => {
    await filesManager.listDirectory('~/Documents');
    expect(mocks.readdir).toHaveBeenCalledWith(
      expect.stringContaining('Documents'),
      expect.any(Object),
    );
  });
});

describe('filesManager.open', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.homedir.mockReturnValue('C:\\Users\\test');
    mocks.openPath.mockResolvedValue('');
  });

  it('calls shell.openPath with resolved path', async () => {
    await filesManager.open('C:\\Users\\test\\file.txt');
    expect(mocks.openPath).toHaveBeenCalledWith('C:\\Users\\test\\file.txt');
  });

  it('resolves tilde before opening', async () => {
    await filesManager.open('~/Desktop/notes.md');
    expect(mocks.openPath).toHaveBeenCalledWith(
      expect.stringContaining('Desktop'),
    );
  });
});

describe('filesManager.showInFolder', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.homedir.mockReturnValue('C:\\Users\\test');
  });

  it('calls shell.showItemInFolder with resolved path', () => {
    filesManager.showInFolder('C:\\Users\\test\\file.txt');
    expect(mocks.showItemInFolder).toHaveBeenCalledWith('C:\\Users\\test\\file.txt');
  });

  it('resolves tilde before showing in folder', () => {
    filesManager.showInFolder('~/Downloads/archive.zip');
    expect(mocks.showItemInFolder).toHaveBeenCalledWith(
      expect.stringContaining('Downloads'),
    );
  });
});
