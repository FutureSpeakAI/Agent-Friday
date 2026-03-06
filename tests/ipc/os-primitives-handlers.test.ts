/**
 * OS Primitives IPC Handlers — Integration tests for file-search, file-watcher,
 * and os-events IPC wiring.
 *
 * Mocks the three OS primitive singletons and Electron's ipcMain to verify
 * that registerOsPrimitivesHandlers() correctly:
 * 1. Registers the expected IPC channels
 * 2. Validates inputs with assert* helpers
 * 3. Delegates to the correct singleton method
 * 4. Returns results from the singleton
 *
 * Phase B.3: "Wiring the Nerves" — IPC Integration Tests
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mock singletons ─────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  // file-search
  search: vi.fn(),
  getRecentFiles: vi.fn(),
  findDuplicates: vi.fn(),
  // file-watcher
  addWatch: vi.fn(),
  removeWatch: vi.fn(),
  getWatchedPaths: vi.fn(),
  getRecentEvents: vi.fn(),
  watcherGetContextString: vi.fn(),
  // os-events
  getPowerState: vi.fn(),
  osGetRecentEvents: vi.fn(),
  getDisplays: vi.fn(),
  getFileAssociation: vi.fn(),
  getFileAssociations: vi.fn(),
  openWithDefault: vi.fn(),
  getStartupPrograms: vi.fn(),
  osGetContextString: vi.fn(),
}));

vi.mock('../../src/main/file-search', () => ({
  fileSearch: {
    search: mocks.search,
    getRecentFiles: mocks.getRecentFiles,
    findDuplicates: mocks.findDuplicates,
  },
}));

vi.mock('../../src/main/file-watcher', () => ({
  fileWatcher: {
    addWatch: mocks.addWatch,
    removeWatch: mocks.removeWatch,
    getWatchedPaths: mocks.getWatchedPaths,
    getRecentEvents: mocks.getRecentEvents,
    getContextString: mocks.watcherGetContextString,
  },
}));

vi.mock('../../src/main/os-events', () => ({
  osEvents: {
    getPowerState: mocks.getPowerState,
    getRecentEvents: mocks.osGetRecentEvents,
    getDisplays: mocks.getDisplays,
    getFileAssociation: mocks.getFileAssociation,
    getFileAssociations: mocks.getFileAssociations,
    openWithDefault: mocks.openWithDefault,
    getStartupPrograms: mocks.getStartupPrograms,
    getContextString: mocks.osGetContextString,
  },
}));

// ── Mock Electron ipcMain ───────────────────────────────────────────

type IpcHandler = (event: unknown, ...args: unknown[]) => unknown;

const handlers = new Map<string, IpcHandler>();

vi.mock('electron', () => ({
  ipcMain: {
    handle: (channel: string, handler: IpcHandler) => {
      handlers.set(channel, handler);
    },
  },
  app: { getPath: () => '' },
}));

// ── Import (after mocks) ───────────────────────────────────────────

import { registerOsPrimitivesHandlers } from '../../src/main/ipc/os-primitives-handlers';

// ── Helpers ─────────────────────────────────────────────────────────

/** Simulate IPC invoke — calls the registered handler with a null event. */
async function invoke(channel: string, ...args: unknown[]): Promise<unknown> {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler registered for channel: ${channel}`);
  return handler(null, ...args);
}

// ── Setup ───────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  handlers.clear();
  registerOsPrimitivesHandlers();
});

// ── Tests ───────────────────────────────────────────────────────────

describe('OS Primitives IPC — channel registration', () => {
  it('registers all expected channels', () => {
    const expected = [
      'file-search:search', 'file-search:recent', 'file-search:duplicates',
      'file-watcher:add-watch', 'file-watcher:remove-watch', 'file-watcher:get-watched',
      'file-watcher:get-events', 'file-watcher:context',
      'os-events:power-state', 'os-events:recent', 'os-events:displays',
      'os-events:file-association', 'os-events:file-associations',
      'os-events:open-with-default', 'os-events:startup-programs', 'os-events:context',
    ];
    for (const ch of expected) {
      expect(handlers.has(ch), `Missing handler: ${ch}`).toBe(true);
    }
  });
});

describe('OS Primitives IPC — file-search handlers', () => {
  it('file-search:search delegates to fileSearch.search()', async () => {
    const mockResult = { results: [], totalFound: 0, searchMethod: 'powershell-scan', durationMs: 10 };
    mocks.search.mockResolvedValue(mockResult);

    const result = await invoke('file-search:search', { query: 'test.pdf' });
    expect(mocks.search).toHaveBeenCalledWith({ query: 'test.pdf' });
    expect(result).toBe(mockResult);
  });

  it('file-search:search rejects non-object input', async () => {
    await expect(invoke('file-search:search', 'not-an-object')).rejects.toThrow('must be a plain object');
  });

  it('file-search:search rejects missing query field', async () => {
    await expect(invoke('file-search:search', { limit: 5 })).rejects.toThrow('must be a string');
  });

  it('file-search:recent delegates with optional params', async () => {
    mocks.getRecentFiles.mockResolvedValue([]);
    await invoke('file-search:recent', 10, ['pdf', 'docx']);
    expect(mocks.getRecentFiles).toHaveBeenCalledWith(10, ['pdf', 'docx']);
  });

  it('file-search:recent works without optional params', async () => {
    mocks.getRecentFiles.mockResolvedValue([]);
    await invoke('file-search:recent');
    expect(mocks.getRecentFiles).toHaveBeenCalledWith(undefined, undefined);
  });

  it('file-search:duplicates validates dirPath', async () => {
    await expect(invoke('file-search:duplicates', '../../../etc/passwd')).rejects.toThrow('dangerous path');
  });

  it('file-search:duplicates delegates with valid path', async () => {
    mocks.findDuplicates.mockResolvedValue([]);
    await invoke('file-search:duplicates', 'C:\\Users\\test\\Documents', 'name');
    expect(mocks.findDuplicates).toHaveBeenCalledWith('C:\\Users\\test\\Documents', 'name');
  });
});

describe('OS Primitives IPC — file-watcher handlers', () => {
  it('file-watcher:add-watch delegates to fileWatcher.addWatch()', async () => {
    mocks.addWatch.mockReturnValue(true);
    const result = await invoke('file-watcher:add-watch', 'C:\\Users\\test\\Documents');
    expect(mocks.addWatch).toHaveBeenCalledWith('C:\\Users\\test\\Documents');
    expect(result).toBe(true);
  });

  it('file-watcher:add-watch rejects traversal paths', async () => {
    await expect(invoke('file-watcher:add-watch', '..\\..\\secret')).rejects.toThrow('dangerous path');
  });

  it('file-watcher:remove-watch delegates correctly', async () => {
    mocks.removeWatch.mockReturnValue(true);
    await invoke('file-watcher:remove-watch', 'C:\\Users\\test\\Documents');
    expect(mocks.removeWatch).toHaveBeenCalledWith('C:\\Users\\test\\Documents');
  });

  it('file-watcher:get-watched returns watched paths', async () => {
    const paths = ['C:\\Users\\test\\Documents', 'C:\\Users\\test\\Downloads'];
    mocks.getWatchedPaths.mockReturnValue(paths);
    const result = await invoke('file-watcher:get-watched');
    expect(result).toEqual(paths);
  });

  it('file-watcher:get-events delegates with limit', async () => {
    mocks.getRecentEvents.mockReturnValue([]);
    await invoke('file-watcher:get-events', 5);
    expect(mocks.getRecentEvents).toHaveBeenCalledWith(5);
  });

  it('file-watcher:get-events rejects invalid limit', async () => {
    await expect(invoke('file-watcher:get-events', -1)).rejects.toThrow('must be between');
  });

  it('file-watcher:context returns context string', async () => {
    mocks.watcherGetContextString.mockReturnValue('Recent: file.txt modified');
    const result = await invoke('file-watcher:context');
    expect(result).toBe('Recent: file.txt modified');
  });
});

describe('OS Primitives IPC — os-events handlers', () => {
  it('os-events:power-state returns power state', async () => {
    const state = { onBattery: false, batteryPercent: 100, source: 'ac' };
    mocks.getPowerState.mockReturnValue(state);
    const result = await invoke('os-events:power-state');
    expect(result).toBe(state);
  });

  it('os-events:recent delegates with optional limit', async () => {
    mocks.osGetRecentEvents.mockReturnValue([]);
    await invoke('os-events:recent', 20);
    expect(mocks.osGetRecentEvents).toHaveBeenCalledWith(20);
  });

  it('os-events:displays returns display info', async () => {
    const displays = [{ id: 1, isPrimary: true, bounds: { width: 1920, height: 1080 } }];
    mocks.getDisplays.mockReturnValue(displays);
    const result = await invoke('os-events:displays');
    expect(result).toBe(displays);
  });

  it('os-events:file-association validates extension', async () => {
    await expect(invoke('os-events:file-association', 123)).rejects.toThrow('must be a string');
  });

  it('os-events:file-association delegates with valid ext', async () => {
    const assoc = { extension: '.pdf', programName: 'Acrobat', executablePath: 'acrobat.exe' };
    mocks.getFileAssociation.mockResolvedValue(assoc);
    const result = await invoke('os-events:file-association', 'pdf');
    expect(mocks.getFileAssociation).toHaveBeenCalledWith('pdf');
    expect(result).toBe(assoc);
  });

  it('os-events:file-associations validates array input', async () => {
    await expect(invoke('os-events:file-associations', 'not-an-array')).rejects.toThrow('must be an array');
  });

  it('os-events:file-associations delegates correctly', async () => {
    mocks.getFileAssociations.mockResolvedValue([]);
    await invoke('os-events:file-associations', ['pdf', 'docx']);
    expect(mocks.getFileAssociations).toHaveBeenCalledWith(['pdf', 'docx']);
  });

  it('os-events:open-with-default rejects traversal paths', async () => {
    await expect(invoke('os-events:open-with-default', '..\\..\\etc\\hosts')).rejects.toThrow('dangerous path');
  });

  it('os-events:open-with-default delegates with safe path', async () => {
    mocks.openWithDefault.mockResolvedValue(true);
    const result = await invoke('os-events:open-with-default', 'C:\\Users\\test\\file.pdf');
    expect(mocks.openWithDefault).toHaveBeenCalledWith('C:\\Users\\test\\file.pdf');
    expect(result).toBe(true);
  });

  it('os-events:startup-programs delegates correctly', async () => {
    const programs = [{ name: 'Discord', command: 'discord.exe', location: 'registry-user', enabled: true }];
    mocks.getStartupPrograms.mockResolvedValue(programs);
    const result = await invoke('os-events:startup-programs');
    expect(result).toBe(programs);
  });

  it('os-events:context returns context string', async () => {
    mocks.osGetContextString.mockReturnValue('');
    const result = await invoke('os-events:context');
    expect(result).toBe('');
  });
});
