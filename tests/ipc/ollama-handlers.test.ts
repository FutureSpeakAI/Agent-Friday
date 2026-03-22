/**
 * Tests for ollama-handlers.ts — IPC layer for OllamaLifecycle.
 * Validates streaming pull-model progress forwarding.
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
}));

const mocks = vi.hoisted(() => ({
  start: vi.fn().mockResolvedValue(undefined),
  stop: vi.fn(),
  getHealth: vi.fn().mockReturnValue({ status: 'healthy' }),
  getHealthAsync: vi.fn().mockResolvedValue({ status: 'healthy' }),
  getAvailableModels: vi.fn().mockReturnValue([]),
  getLoadedModels: vi.fn().mockReturnValue([]),
  isModelAvailable: vi.fn().mockReturnValue(false),
  pullModel: vi.fn(),
  on: vi.fn().mockReturnValue(() => {}),
}));

vi.mock('../../src/main/ollama-lifecycle', () => ({
  OllamaLifecycle: {
    getInstance: () => ({
      start: mocks.start,
      stop: mocks.stop,
      getHealth: mocks.getHealth,
      getHealthAsync: mocks.getHealthAsync,
      getAvailableModels: mocks.getAvailableModels,
      getLoadedModels: mocks.getLoadedModels,
      isModelAvailable: mocks.isModelAvailable,
      pullModel: mocks.pullModel,
      on: mocks.on,
    }),
  },
}));

vi.mock('../../src/main/ipc/validate', () => ({
  assertString: vi.fn((val: unknown, label: string) => {
    if (typeof val !== 'string' || val.length === 0) throw new Error(`${label} requires a string`);
  }),
}));

import { registerOllamaHandlers } from '../../src/main/ipc/ollama-handlers';

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('Ollama Handlers — Sprint 7 IPC', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    registerOllamaHandlers({
      getMainWindow: () => ({ webContents: { send: mockSend } } as any),
    });
  });

  describe('Handler Registration', () => {
    it('registers all expected channels', () => {
      const expected = [
        'ollama:start', 'ollama:stop', 'ollama:get-health',
        'ollama:get-available-models', 'ollama:get-loaded-models',
        'ollama:is-model-available', 'ollama:pull-model',
      ];
      for (const ch of expected) {
        expect(handlers.has(ch), `Missing handler: ${ch}`).toBe(true);
      }
    });

    it('registers exactly 7 handlers', () => {
      expect(handlers.size).toBe(7);
    });
  });

  describe('Lifecycle', () => {
    it('start delegates', async () => {
      await invoke('ollama:start');
      expect(mocks.start).toHaveBeenCalled();
    });

    it('stop delegates', () => {
      invoke('ollama:stop');
      expect(mocks.stop).toHaveBeenCalled();
    });

    it('getHealth returns status', async () => {
      const result = await invoke('ollama:get-health');
      expect(result).toEqual({ status: 'healthy' });
    });
  });

  describe('Model Management', () => {
    it('isModelAvailable delegates with name', () => {
      invoke('ollama:is-model-available', 'llama3:8b');
      expect(mocks.isModelAvailable).toHaveBeenCalledWith('llama3:8b');
    });

    it('isModelAvailable rejects non-string', () => {
      expect(() => invoke('ollama:is-model-available', 42)).toThrow();
    });
  });

  describe('Pull Model Streaming', () => {
    it('forwards progress events and returns success', async () => {
      // Simulate async generator yielding progress events
      async function* fakeProgress() {
        yield { status: 'downloading', completed: 50, total: 100 };
        yield { status: 'downloading', completed: 100, total: 100 };
      }
      mocks.pullModel.mockReturnValue(fakeProgress());

      const result = await invoke('ollama:pull-model', 'llama3:8b');

      expect(mocks.pullModel).toHaveBeenCalledWith('llama3:8b');
      expect(mockSend).toHaveBeenCalledTimes(2);
      expect(mockSend).toHaveBeenCalledWith('ollama:event:pull-progress', expect.objectContaining({
        modelName: 'llama3:8b',
        status: 'downloading',
      }));
      expect(result).toEqual({ success: true, modelName: 'llama3:8b' });
    });

    it('returns error on pull failure', async () => {
      async function* failingProgress(): AsyncGenerator<never, never, unknown> {
        throw new Error('Network timeout');
      }
      mocks.pullModel.mockReturnValue(failingProgress());

      const result = await invoke('ollama:pull-model', 'bad-model');
      expect(result).toEqual({
        success: false,
        modelName: 'bad-model',
        error: 'Network timeout',
      });
    });

    it('rejects non-string model name', async () => {
      await expect(invoke('ollama:pull-model', 42)).rejects.toThrow();
    });
  });

  describe('Event Forwarding', () => {
    it('registers lifecycle events', () => {
      expect(mocks.on).toHaveBeenCalledWith('healthy', expect.any(Function));
      expect(mocks.on).toHaveBeenCalledWith('unhealthy', expect.any(Function));
      expect(mocks.on).toHaveBeenCalledWith('health-change', expect.any(Function));
      expect(mocks.on).toHaveBeenCalledWith('model-loaded', expect.any(Function));
      expect(mocks.on).toHaveBeenCalledWith('model-unloaded', expect.any(Function));
    });
  });
});
