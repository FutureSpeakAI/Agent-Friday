/**
 * Tests for core-handlers.ts's FR-1 integration — the orchestrator seat
 * conformance gate must re-run automatically whenever `localModelId`
 * changes (dev/friday-orchestrator-integrity-spec.md, FR-1).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

type IpcHandler = (...args: unknown[]) => unknown;
const handlers = new Map<string, IpcHandler>();
const mockSend = vi.fn();

vi.mock('electron', () => ({
  ipcMain: {
    handle: vi.fn((channel: string, handler: IpcHandler) => {
      handlers.set(channel, handler);
    }),
  },
  BrowserWindow: class {},
  shell: { openExternal: vi.fn() },
}));

const mocks = vi.hoisted(() => ({
  setSetting: vi.fn().mockResolvedValue(undefined),
  getMasked: vi.fn().mockReturnValue({}),
  getGeminiApiKey: vi.fn().mockReturnValue(''),
  runConformance: vi.fn(),
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    setSetting: mocks.setSetting,
    getMasked: mocks.getMasked,
    getGeminiApiKey: mocks.getGeminiApiKey,
  },
}));

vi.mock('../../src/main/personality', () => ({
  buildGeminiLiveSystemInstruction: vi.fn().mockResolvedValue(''),
}));

vi.mock('../../src/main/mcp-client', () => ({
  mcpClient: { isConnected: vi.fn().mockReturnValue(false), listTools: vi.fn().mockResolvedValue([]), callTool: vi.fn() },
}));

vi.mock('../../src/main/memory', () => ({
  memoryManager: {},
}));

vi.mock('../../src/main/desktop-tools', () => ({
  DESKTOP_TOOL_DECLARATIONS: [
    { name: 'get_active_window', description: 'x', parameters: { type: 'object', properties: {} } },
  ],
}));

vi.mock('../../src/main/ipc/validate', () => ({
  assertString: vi.fn((val: unknown, label: string) => {
    if (typeof val !== 'string' || val.length === 0) throw new Error(`${label} requires a string`);
  }),
  assertToolCallArgs: vi.fn(),
  assertSafePath: vi.fn((p: string) => p),
  assertBoolean: vi.fn((v: unknown) => v),
}));

vi.mock('../../src/main/toolcall-conformance-core', () => ({
  runConformance: mocks.runConformance,
}));

import { registerCoreHandlers } from '../../src/main/ipc/core-handlers';

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

async function flushMicrotasks() {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

describe('core-handlers — FR-1 conformance auto-trigger', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    mocks.setSetting.mockResolvedValue(undefined);
    registerCoreHandlers({
      getMainWindow: () => ({ isDestroyed: () => false, webContents: { send: mockSend } } as any),
      serverPort: 0,
    });
  });

  it('runs the conformance gate and sends the result when localModelId changes', async () => {
    mocks.runConformance.mockResolvedValueOnce({ model: 'gemma4:latest', pass: true, passCount: 10, totalCount: 10, results: [] });

    await invoke('settings:set', 'localModelId', 'gemma4:latest');
    await flushMicrotasks();

    expect(mocks.runConformance).toHaveBeenCalledWith(
      expect.objectContaining({ model: 'gemma4:latest', tools: expect.any(Array) }),
    );
    expect(mockSend).toHaveBeenCalledWith(
      'toolcall-conformance:result',
      expect.objectContaining({ model: 'gemma4:latest', pass: true }),
    );
  });

  it('does not run the conformance gate for unrelated settings keys', async () => {
    await invoke('settings:set', 'theme', 'dark');
    await flushMicrotasks();

    expect(mocks.runConformance).not.toHaveBeenCalled();
    expect(mockSend).not.toHaveBeenCalled();
  });

  it('does not run the conformance gate when localModelId is set to an empty string', async () => {
    await invoke('settings:set', 'localModelId', '');
    await flushMicrotasks();

    expect(mocks.runConformance).not.toHaveBeenCalled();
  });

  it('still saves the setting even if the conformance gate later fails to run (fire-and-forget)', async () => {
    mocks.runConformance.mockRejectedValueOnce(new Error('Ollama unreachable'));

    await expect(invoke('settings:set', 'localModelId', 'gemma3:4b')).resolves.toBeUndefined();
    expect(mocks.setSetting).toHaveBeenCalledWith('localModelId', 'gemma3:4b');
  });

  it('toolcall-conformance:check runs the gate on demand and returns the report', async () => {
    mocks.runConformance.mockResolvedValueOnce({ model: 'gemma3:4b', pass: false, passCount: 0, totalCount: 10, results: [] });

    const report: any = await invoke('toolcall-conformance:check', 'gemma3:4b');

    expect(report.pass).toBe(false);
    expect(report.model).toBe('gemma3:4b');
  });
});
