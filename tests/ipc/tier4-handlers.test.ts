/**
 * Tier-4 IPC Handlers — Integration tests for notes, files, weather,
 * and system-monitor IPC wiring.
 *
 * Mocks the four backend singletons and Electron's ipcMain to verify
 * that each handler:
 * 1. Registers the expected IPC channels
 * 2. Validates inputs with assert* helpers
 * 3. Delegates to the correct backend method
 * 4. Returns results from the backend
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mock singletons ─────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  // notes-store
  notesList: vi.fn(),
  notesGet: vi.fn(),
  notesCreate: vi.fn(),
  notesUpdate: vi.fn(),
  notesDelete: vi.fn(),
  notesSearch: vi.fn(),
  // files-manager
  listDirectory: vi.fn(),
  filesOpen: vi.fn(),
  showInFolder: vi.fn(),
  // weather
  getCurrent: vi.fn(),
  getForecast: vi.fn(),
  setLocation: vi.fn(),
  // system-monitor
  getStats: vi.fn(),
  getProcesses: vi.fn(),
}));

vi.mock('../../src/main/notes-store', () => ({
  notesStore: {
    list: mocks.notesList,
    get: mocks.notesGet,
    create: mocks.notesCreate,
    update: mocks.notesUpdate,
    delete: mocks.notesDelete,
    search: mocks.notesSearch,
  },
}));

vi.mock('../../src/main/files-manager', () => ({
  filesManager: {
    listDirectory: mocks.listDirectory,
    open: mocks.filesOpen,
    showInFolder: mocks.showInFolder,
  },
}));

vi.mock('../../src/main/weather', () => ({
  weather: {
    getCurrent: mocks.getCurrent,
    getForecast: mocks.getForecast,
    setLocation: mocks.setLocation,
  },
}));

vi.mock('../../src/main/system-monitor', () => ({
  systemMonitor: {
    getStats: mocks.getStats,
    getProcesses: mocks.getProcesses,
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

// ── Import (after mocks) ──────────────────────────────────────────

import { registerNotesHandlers } from '../../src/main/ipc/notes-handlers';
import { registerFilesHandlers } from '../../src/main/ipc/files-handlers';
import { registerWeatherHandlers } from '../../src/main/ipc/weather-handlers';
import { registerSystemMonitorHandlers } from '../../src/main/ipc/system-monitor-handlers';

// ── Helpers ─────────────────────────────────────────────────────────

async function invoke(channel: string, ...args: unknown[]): Promise<unknown> {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler registered for channel: ${channel}`);
  return handler(null, ...args);
}

// ── Setup ───────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  handlers.clear();
  registerNotesHandlers();
  registerFilesHandlers();
  registerWeatherHandlers();
  registerSystemMonitorHandlers();
});

// ── Channel Registration ────────────────────────────────────────────

describe('Tier-4 IPC — channel registration', () => {
  it('registers all notes channels', () => {
    const expected = [
      'notes:list', 'notes:get', 'notes:create',
      'notes:update', 'notes:delete', 'notes:search',
    ];
    for (const ch of expected) {
      expect(handlers.has(ch), `Missing channel: ${ch}`).toBe(true);
    }
  });

  it('registers all files channels', () => {
    const expected = ['files:list-directory', 'files:open', 'files:show-in-folder'];
    for (const ch of expected) {
      expect(handlers.has(ch), `Missing channel: ${ch}`).toBe(true);
    }
  });

  it('registers all weather channels', () => {
    const expected = ['weather:current', 'weather:forecast', 'weather:set-location'];
    for (const ch of expected) {
      expect(handlers.has(ch), `Missing channel: ${ch}`).toBe(true);
    }
  });

  it('registers all system-monitor channels', () => {
    const expected = ['system:stats', 'system:processes'];
    for (const ch of expected) {
      expect(handlers.has(ch), `Missing channel: ${ch}`).toBe(true);
    }
  });
});

// ── Notes Handlers ─────────────────────────────────────────────────

describe('notes IPC handlers', () => {
  it('notes:list delegates to notesStore.list()', async () => {
    const mockNotes = [{ id: '1', title: 'Test' }];
    mocks.notesList.mockResolvedValue(mockNotes);

    const result = await invoke('notes:list');
    expect(result).toEqual(mockNotes);
    expect(mocks.notesList).toHaveBeenCalledOnce();
  });

  it('notes:get delegates with validated id', async () => {
    const mockNote = { id: 'abc', title: 'Found', content: 'Full text' };
    mocks.notesGet.mockResolvedValue(mockNote);

    const result = await invoke('notes:get', 'abc');
    expect(result).toEqual(mockNote);
    expect(mocks.notesGet).toHaveBeenCalledWith('abc');
  });

  it('notes:get rejects non-string id', async () => {
    await expect(invoke('notes:get', 12345)).rejects.toThrow();
  });

  it('notes:create delegates with validated input', async () => {
    const created = { id: 'new-1', title: 'New', content: 'Body' };
    mocks.notesCreate.mockResolvedValue(created);

    const result = await invoke('notes:create', { title: 'New', content: 'Body' });
    expect(result).toEqual(created);
  });

  it('notes:create rejects non-object input', async () => {
    await expect(invoke('notes:create', 'not an object')).rejects.toThrow();
  });

  it('notes:update delegates with id and patch', async () => {
    const updated = { id: 'abc', title: 'Updated' };
    mocks.notesUpdate.mockResolvedValue(updated);

    const result = await invoke('notes:update', 'abc', { title: 'Updated' });
    expect(result).toEqual(updated);
    expect(mocks.notesUpdate).toHaveBeenCalledWith('abc', { title: 'Updated' });
  });

  it('notes:delete delegates with validated id', async () => {
    mocks.notesDelete.mockResolvedValue(true);

    const result = await invoke('notes:delete', 'abc');
    expect(result).toBe(true);
    expect(mocks.notesDelete).toHaveBeenCalledWith('abc');
  });

  it('notes:search delegates with validated query', async () => {
    const results = [{ id: '1', title: 'Match' }];
    mocks.notesSearch.mockResolvedValue(results);

    const result = await invoke('notes:search', 'test query');
    expect(result).toEqual(results);
    expect(mocks.notesSearch).toHaveBeenCalledWith('test query');
  });

  it('notes:search rejects non-string query', async () => {
    await expect(invoke('notes:search', 42)).rejects.toThrow();
  });
});

// ── Files Handlers ──────────────────────────────────────────────────

describe('files IPC handlers', () => {
  it('files:list-directory delegates with validated path', async () => {
    const entries = [{ name: 'file.txt', isDirectory: false, size: 100 }];
    mocks.listDirectory.mockResolvedValue(entries);

    const result = await invoke('files:list-directory', 'C:\\Users\\test');
    expect(result).toEqual(entries);
    expect(mocks.listDirectory).toHaveBeenCalledWith('C:\\Users\\test');
  });

  it('files:list-directory rejects non-string path', async () => {
    await expect(invoke('files:list-directory', 123)).rejects.toThrow();
  });

  it('files:open delegates with validated path', async () => {
    mocks.filesOpen.mockResolvedValue('');
    await invoke('files:open', 'C:\\Users\\test\\file.txt');
    expect(mocks.filesOpen).toHaveBeenCalledWith('C:\\Users\\test\\file.txt');
  });

  it('files:show-in-folder delegates and returns true', async () => {
    const result = await invoke('files:show-in-folder', 'C:\\Users\\test\\file.txt');
    expect(result).toBe(true);
    expect(mocks.showInFolder).toHaveBeenCalledWith('C:\\Users\\test\\file.txt');
  });
});

// ── Weather Handlers ────────────────────────────────────────────────

describe('weather IPC handlers', () => {
  it('weather:current delegates to weather.getCurrent()', async () => {
    const current = { temp: 72, condition: 'Clear' };
    mocks.getCurrent.mockResolvedValue(current);

    const result = await invoke('weather:current');
    expect(result).toEqual(current);
    expect(mocks.getCurrent).toHaveBeenCalledOnce();
  });

  it('weather:forecast delegates to weather.getForecast()', async () => {
    const forecast = [{ day: 'Mon', high: 80, low: 60, condition: 'Clear' }];
    mocks.getForecast.mockResolvedValue(forecast);

    const result = await invoke('weather:forecast');
    expect(result).toEqual(forecast);
    expect(mocks.getForecast).toHaveBeenCalledOnce();
  });

  it('weather:set-location delegates with validated lat/lon/city', async () => {
    mocks.setLocation.mockResolvedValue(undefined);

    await invoke('weather:set-location', 40.7128, -74.006, 'New York', 'NY');
    expect(mocks.setLocation).toHaveBeenCalledWith(40.7128, -74.006, 'New York', 'NY');
  });

  it('weather:set-location rejects invalid latitude', async () => {
    await expect(invoke('weather:set-location', 91, -74, 'City')).rejects.toThrow();
  });

  it('weather:set-location rejects invalid longitude', async () => {
    await expect(invoke('weather:set-location', 40, -181, 'City')).rejects.toThrow();
  });

  it('weather:set-location rejects non-string city', async () => {
    await expect(invoke('weather:set-location', 40, -74, 12345)).rejects.toThrow();
  });
});

// ── System Monitor Handlers ─────────────────────────────────────────

describe('system-monitor IPC handlers', () => {
  it('system:stats delegates to systemMonitor.getStats()', async () => {
    const stats = { cpuPercent: 25, memUsedMB: 8192, memTotalMB: 16384 };
    mocks.getStats.mockResolvedValue(stats);

    const result = await invoke('system:stats');
    expect(result).toEqual(stats);
    expect(mocks.getStats).toHaveBeenCalledOnce();
  });

  it('system:processes delegates to systemMonitor.getProcesses()', async () => {
    const procs = [{ name: 'chrome', pid: 1234, cpu: 15, mem: 512 }];
    mocks.getProcesses.mockResolvedValue(procs);

    const result = await invoke('system:processes');
    expect(result).toEqual(procs);
  });

  it('system:processes passes limit when provided', async () => {
    mocks.getProcesses.mockResolvedValue([]);
    await invoke('system:processes', 10);
    expect(mocks.getProcesses).toHaveBeenCalledWith(10);
  });

  it('system:processes rejects invalid limit', async () => {
    await expect(invoke('system:processes', 'not a number')).rejects.toThrow();
  });
});
