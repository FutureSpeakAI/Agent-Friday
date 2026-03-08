/**
 * Tests for hardware-handlers.ts — IPC layer for HardwareProfiler,
 * TierRecommender (pure functions), and ModelOrchestrator.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock electron ────────────────────────────────────────────────────
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

// ── Mock domain modules ──────────────────────────────────────────────
const mocks = vi.hoisted(() => ({
  detect: vi.fn().mockResolvedValue({ gpu: { name: 'RTX 4090' }, vramMB: 24576 }),
  getProfile: vi.fn().mockReturnValue({ gpu: { name: 'RTX 4090' }, vramMB: 24576 }),
  refresh: vi.fn().mockResolvedValue({ gpu: { name: 'RTX 4090' }, vramMB: 24576 }),
  getEffectiveVRAM: vi.fn().mockReturnValue(24576),
  profilerOn: vi.fn(),
  getTier: vi.fn().mockReturnValue('full'),
  getModelList: vi.fn().mockReturnValue(['llama3:8b', 'nomic-embed-text']),
  estimateVRAMUsage: vi.fn().mockReturnValue(8192),
  recommend: vi.fn().mockReturnValue({ tier: 'full', models: [] }),
  loadTierModels: vi.fn().mockResolvedValue({ loaded: 2 }),
  getLoadedModels: vi.fn().mockReturnValue([]),
  getVRAMUsage: vi.fn().mockReturnValue({ usedMB: 0, totalMB: 24576 }),
  loadModel: vi.fn().mockResolvedValue(true),
  unloadModel: vi.fn().mockResolvedValue(true),
  evictLeastRecent: vi.fn().mockResolvedValue('llama3:8b'),
  getOrchestratorState: vi.fn().mockReturnValue({ models: [], vram: {} }),
  markUsed: vi.fn(),
  orchestratorOn: vi.fn(),
}));

vi.mock('../../src/main/hardware/hardware-profiler', () => ({
  HardwareProfiler: {
    getInstance: () => ({
      detect: mocks.detect,
      getProfile: mocks.getProfile,
      refresh: mocks.refresh,
      getEffectiveVRAM: mocks.getEffectiveVRAM,
      on: mocks.profilerOn,
    }),
  },
}));

vi.mock('../../src/main/hardware/tier-recommender', () => ({
  getTier: mocks.getTier,
  getModelList: mocks.getModelList,
  estimateVRAMUsage: mocks.estimateVRAMUsage,
  recommend: mocks.recommend,
}));

vi.mock('../../src/main/hardware/model-orchestrator', () => ({
  ModelOrchestrator: {
    getInstance: () => ({
      loadTierModels: mocks.loadTierModels,
      getLoadedModels: mocks.getLoadedModels,
      getVRAMUsage: mocks.getVRAMUsage,
      loadModel: mocks.loadModel,
      unloadModel: mocks.unloadModel,
      evictLeastRecent: mocks.evictLeastRecent,
      getOrchestratorState: mocks.getOrchestratorState,
      markUsed: mocks.markUsed,
      on: mocks.orchestratorOn,
    }),
  },
}));

vi.mock('../../src/main/ipc/validate', () => ({
  assertString: vi.fn((val: unknown, label: string) => {
    if (typeof val !== 'string' || val.length === 0) throw new Error(`${label} requires a string`);
  }),
  assertNumber: vi.fn((val: unknown, label: string) => {
    if (typeof val !== 'number') throw new Error(`${label} requires a number`);
  }),
  assertObject: vi.fn((val: unknown, label: string) => {
    if (!val || typeof val !== 'object' || Array.isArray(val)) throw new Error(`${label} requires an object`);
  }),
}));

import { registerHardwareHandlers } from '../../src/main/ipc/hardware-handlers';

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('Hardware Handlers — Sprint 7 IPC', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    registerHardwareHandlers({
      getMainWindow: () => ({ webContents: { send: mockSend } } as any),
    });
  });

  describe('Handler Registration', () => {
    it('registers all expected IPC channels', () => {
      const expected = [
        'hardware:detect', 'hardware:get-profile', 'hardware:refresh',
        'hardware:get-effective-vram', 'hardware:get-tier', 'hardware:get-model-list',
        'hardware:estimate-vram', 'hardware:recommend', 'hardware:load-tier-models',
        'hardware:get-loaded-models', 'hardware:get-vram-usage', 'hardware:load-model',
        'hardware:unload-model', 'hardware:evict-least-recent',
        'hardware:get-orchestrator-state', 'hardware:mark-model-used',
      ];
      for (const ch of expected) {
        expect(handlers.has(ch), `Missing handler: ${ch}`).toBe(true);
      }
    });

    it('registers exactly 16 handlers', () => {
      expect(handlers.size).toBe(16);
    });
  });

  describe('Hardware Profiler', () => {
    it('detect delegates to profiler', async () => {
      const result = await invoke('hardware:detect');
      expect(mocks.detect).toHaveBeenCalled();
      expect(result).toEqual({ gpu: { name: 'RTX 4090' }, vramMB: 24576 });
    });

    it('getProfile returns cached profile', () => {
      const result = invoke('hardware:get-profile');
      expect(mocks.getProfile).toHaveBeenCalled();
      expect(result).toEqual({ gpu: { name: 'RTX 4090' }, vramMB: 24576 });
    });

    it('getEffectiveVRAM returns VRAM in MB', () => {
      const result = invoke('hardware:get-effective-vram');
      expect(result).toBe(24576);
    });
  });

  describe('Tier Recommender', () => {
    it('getTier delegates with profile object', () => {
      const profile = { vramMB: 24576 };
      invoke('hardware:get-tier', profile);
      expect(mocks.getTier).toHaveBeenCalledWith(profile);
    });

    it('getTier rejects non-object', () => {
      expect(() => invoke('hardware:get-tier', 'not-object')).toThrow();
    });

    it('getModelList delegates with tier string', () => {
      invoke('hardware:get-model-list', 'full');
      expect(mocks.getModelList).toHaveBeenCalledWith('full');
    });

    it('getModelList rejects non-string', () => {
      expect(() => invoke('hardware:get-model-list', 42)).toThrow();
    });

    it('estimateVRAM rejects non-array', () => {
      expect(() => invoke('hardware:estimate-vram', 'not-array')).toThrow();
    });
  });

  describe('Model Orchestrator', () => {
    it('loadModel delegates with name', async () => {
      await invoke('hardware:load-model', 'llama3:8b');
      expect(mocks.loadModel).toHaveBeenCalledWith('llama3:8b');
    });

    it('unloadModel delegates with name', async () => {
      await invoke('hardware:unload-model', 'llama3:8b');
      expect(mocks.unloadModel).toHaveBeenCalledWith('llama3:8b');
    });

    it('markModelUsed delegates with name', () => {
      invoke('hardware:mark-model-used', 'llama3:8b');
      expect(mocks.markUsed).toHaveBeenCalledWith('llama3:8b');
    });

    it('evictLeastRecent delegates', async () => {
      const result = await invoke('hardware:evict-least-recent');
      expect(mocks.evictLeastRecent).toHaveBeenCalled();
      expect(result).toBe('llama3:8b');
    });
  });

  describe('Event Forwarding', () => {
    it('registers hardware-detected event listener', () => {
      expect(mocks.profilerOn).toHaveBeenCalledWith('hardware-detected', expect.any(Function));
    });

    it('registers orchestrator event listeners', () => {
      expect(mocks.orchestratorOn).toHaveBeenCalledWith('model-loaded', expect.any(Function));
      expect(mocks.orchestratorOn).toHaveBeenCalledWith('model-unloaded', expect.any(Function));
      expect(mocks.orchestratorOn).toHaveBeenCalledWith('vram-warning', expect.any(Function));
    });
  });
});
