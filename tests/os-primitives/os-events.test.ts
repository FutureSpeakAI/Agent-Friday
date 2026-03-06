/**
 * OSEventEngine — Unit tests for event bus, power state, and context string.
 *
 * Tests the internal logic of the OS event engine by accessing private methods
 * through the singleton and mocking Electron + child_process dependencies.
 *
 * Phase B.2: "Sensory Tests" — OS Primitives
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mock dependencies ──────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  execFile: vi.fn(),
  powerMonitorOn: vi.fn(),
  screenOn: vi.fn(),
  screenGetAllDisplays: vi.fn(() => []),
  screenGetPrimaryDisplay: vi.fn(() => ({ id: 1 })),
  webContentsSend: vi.fn(),
}));

vi.mock('child_process', () => ({
  execFile: mocks.execFile,
}));

vi.mock('electron', () => ({
  powerMonitor: {
    on: mocks.powerMonitorOn,
    getSystemIdleState: () => 'active',
  },
  screen: {
    on: mocks.screenOn,
    getAllDisplays: mocks.screenGetAllDisplays,
    getPrimaryDisplay: mocks.screenGetPrimaryDisplay,
  },
  BrowserWindow: class {},
  app: { getPath: () => '' },
}));

vi.mock('../../src/main/settings', () => ({
  getSanitizedEnv: () => ({ PATH: '/usr/bin' }),
}));

// ── Import ─────────────────────────────────────────────────────────

// We need to re-import to get a fresh module with our mocks
let osEventsModule: typeof import('../../src/main/os-events');

beforeEach(async () => {
  vi.clearAllMocks();
  vi.resetModules();
  osEventsModule = await import('../../src/main/os-events');
});

// ── Tests ──────────────────────────────────────────────────────────

describe('OSEventEngine — types', () => {
  it('exports the expected type interfaces', () => {
    // Type-level check — if this compiles, the types exist
    const event: import('../../src/main/os-events').OSEvent = {
      type: 'power:suspend',
      timestamp: Date.now(),
      data: {},
    };
    expect(event.type).toBe('power:suspend');
  });

  it('exports ProcessInfo interface fields', () => {
    const info: import('../../src/main/os-events').ProcessInfo = {
      pid: 1234,
      name: 'test',
      timestamp: Date.now(),
    };
    expect(info.pid).toBe(1234);
  });
});

describe('OSEventEngine — getRecentEvents', () => {
  it('returns empty array before initialization', () => {
    const events = osEventsModule.osEvents.getRecentEvents();
    expect(events).toEqual([]);
  });

  it('respects the limit parameter', () => {
    const events = osEventsModule.osEvents.getRecentEvents(5);
    expect(events).toEqual([]);
    expect(events.length).toBeLessThanOrEqual(5);
  });
});

describe('OSEventEngine — getPowerState', () => {
  it('returns initial power state before initialization', () => {
    const state = osEventsModule.osEvents.getPowerState();
    expect(state).toEqual(expect.objectContaining({
      onBattery: false,
      batteryPercent: 100,
      timeRemaining: -1,
      source: 'unknown',
      isLocked: false,
      isSuspended: false,
    }));
  });

  it('returns a copy of the power state (not the internal reference)', () => {
    const state1 = osEventsModule.osEvents.getPowerState();
    const state2 = osEventsModule.osEvents.getPowerState();
    expect(state1).toEqual(state2);
    expect(state1).not.toBe(state2); // Different objects
  });
});

describe('OSEventEngine — getContextString', () => {
  it('returns empty string when no notable state exists', () => {
    // Default state: not on battery, not locked, no recent events
    mocks.screenGetAllDisplays.mockReturnValue([
      { id: 1, label: 'Main', bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 0, width: 1920, height: 1040 }, scaleFactor: 1, rotation: 0, internal: true },
    ]);
    mocks.screenGetPrimaryDisplay.mockReturnValue({ id: 1 });

    const context = osEventsModule.osEvents.getContextString();
    expect(context).toBe('');
  });
});

describe('OSEventEngine — getDisplays', () => {
  it('returns display info from Electron screen API', () => {
    mocks.screenGetAllDisplays.mockReturnValue([
      {
        id: 1,
        label: 'Main Display',
        bounds: { x: 0, y: 0, width: 1920, height: 1080 },
        workArea: { x: 0, y: 0, width: 1920, height: 1040 },
        scaleFactor: 1,
        rotation: 0,
        internal: true,
      },
    ]);
    mocks.screenGetPrimaryDisplay.mockReturnValue({ id: 1 });

    const displays = osEventsModule.osEvents.getDisplays();
    expect(displays).toHaveLength(1);
    expect(displays[0].id).toBe(1);
    expect(displays[0].isPrimary).toBe(true);
    expect(displays[0].bounds.width).toBe(1920);
    expect(displays[0].scaleFactor).toBe(1);
  });

  it('marks primary display correctly in multi-monitor setup', () => {
    mocks.screenGetAllDisplays.mockReturnValue([
      {
        id: 1,
        label: 'Primary',
        bounds: { x: 0, y: 0, width: 1920, height: 1080 },
        workArea: { x: 0, y: 0, width: 1920, height: 1040 },
        scaleFactor: 1,
        rotation: 0,
        internal: true,
      },
      {
        id: 2,
        label: 'External',
        bounds: { x: 1920, y: 0, width: 2560, height: 1440 },
        workArea: { x: 1920, y: 0, width: 2560, height: 1400 },
        scaleFactor: 1.5,
        rotation: 0,
        internal: false,
      },
    ]);
    mocks.screenGetPrimaryDisplay.mockReturnValue({ id: 1 });

    const displays = osEventsModule.osEvents.getDisplays();
    expect(displays).toHaveLength(2);
    expect(displays[0].isPrimary).toBe(true);
    expect(displays[1].isPrimary).toBe(false);
    expect(displays[1].scaleFactor).toBe(1.5);
  });

  it('defaults internal to false when not provided', () => {
    mocks.screenGetAllDisplays.mockReturnValue([
      {
        id: 1,
        bounds: { x: 0, y: 0, width: 1920, height: 1080 },
        workArea: { x: 0, y: 0, width: 1920, height: 1040 },
        scaleFactor: 1,
        rotation: 0,
        // internal not set
      },
    ]);
    mocks.screenGetPrimaryDisplay.mockReturnValue({ id: 1 });

    const displays = osEventsModule.osEvents.getDisplays();
    expect(displays[0].internal).toBe(false);
  });
});

describe('OSEventEngine — getFileAssociation', () => {
  it('returns null for empty extension', async () => {
    const result = await osEventsModule.osEvents.getFileAssociation('');
    expect(result).toBeNull();
    expect(mocks.execFile).not.toHaveBeenCalled();
  });

  it('sanitizes extension to prevent injection', async () => {
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        cb(null, 'null', '');
      },
    );

    await osEventsModule.osEvents.getFileAssociation('pdf;rm -rf');
    // Should strip non-alphanumeric chars, resulting in 'pdfrmrf'
    const scriptArg = mocks.execFile.mock.calls[0]?.[1]?.join(' ') ?? '';
    // The sanitized extension should appear in the script
    expect(scriptArg).toContain('.pdfrmrf');
    // The original dangerous input should not appear
    expect(scriptArg).not.toContain('pdf;rm');
  });

  it('parses file association from PowerShell output', async () => {
    const assocOutput = JSON.stringify({
      Extension: '.pdf',
      ProgramName: 'AcroExch.Document.DC',
      ExecutablePath: 'C:\\Program Files\\Adobe\\Acrobat\\Acrobat.exe',
    });

    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        cb(null, assocOutput, '');
      },
    );

    const result = await osEventsModule.osEvents.getFileAssociation('pdf');
    expect(result).not.toBeNull();
    expect(result!.extension).toBe('.pdf');
    expect(result!.programName).toBe('AcroExch.Document.DC');
    expect(result!.executablePath).toContain('Acrobat.exe');
  });

  it('returns null when PowerShell returns "null"', async () => {
    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        cb(null, 'null', '');
      },
    );

    const result = await osEventsModule.osEvents.getFileAssociation('xyz');
    expect(result).toBeNull();
  });
});

describe('OSEventEngine — getFileAssociations (batch)', () => {
  it('returns empty array for empty extensions list', async () => {
    const result = await osEventsModule.osEvents.getFileAssociations([]);
    expect(result).toEqual([]);
    expect(mocks.execFile).not.toHaveBeenCalled();
  });

  it('parses multiple associations', async () => {
    const output = JSON.stringify([
      { Extension: '.pdf', ProgramName: 'AcroExch', ExecutablePath: 'acrobat.exe' },
      { Extension: '.docx', ProgramName: 'Word', ExecutablePath: 'winword.exe' },
    ]);

    mocks.execFile.mockImplementation(
      (_cmd: string, _args: string[], _opts: unknown, cb: Function) => {
        cb(null, output, '');
      },
    );

    const result = await osEventsModule.osEvents.getFileAssociations(['pdf', 'docx']);
    expect(result).toHaveLength(2);
    expect(result[0].extension).toBe('.pdf');
    expect(result[1].extension).toBe('.docx');
  });
});

describe('OSEventEngine — stop', () => {
  it('can be called safely before initialization', () => {
    expect(() => osEventsModule.osEvents.stop()).not.toThrow();
  });
});
