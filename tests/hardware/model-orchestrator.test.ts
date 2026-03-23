/**
 * ModelOrchestrator -- Unit tests for model loading/unloading within VRAM budget.
 *
 * Tests the orchestrator's ability to load tier models, track VRAM usage,
 * enforce VRAM budget constraints, evict LRU models, handle lazy vision
 * model loading, and provide full state snapshots for the UI.
 *
 * All Ollama REST API calls are mocked via global fetch. HardwareProfiler
 * and TierRecommender are mocked to provide deterministic test data.
 *
 * Sprint 6 O.3: "The Conductor" -- ModelOrchestrator
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// -- Constants ---------------------------------------------------------------

const GB = 1024 * 1024 * 1024;

// -- Hoisted mocks (required for vi.mock factories) -------------------------

const mocks = vi.hoisted(() => ({
  getModelList: vi.fn(),
  estimateVRAMUsage: vi.fn(),
  canFitModel: vi.fn(),
  getProfile: vi.fn(),
  getEffectiveVRAM: vi.fn(),
  getInstance: vi.fn(),
  fetch: vi.fn(),
}));

// -- Module mocks -----------------------------------------------------------

vi.mock('../../src/main/hardware/tier-recommender', () => ({
  getModelList: mocks.getModelList,
  estimateVRAMUsage: mocks.estimateVRAMUsage,
  canFitModel: mocks.canFitModel,
}));

vi.mock('../../src/main/hardware/hardware-profiler', () => ({
  HardwareProfiler: {
    getInstance: mocks.getInstance,
  },
}));

// -- Import after mocks -----------------------------------------------------

import { ModelOrchestrator } from '../../src/main/hardware/model-orchestrator';
import type { OrchestratorState, LoadedModel } from '../../src/main/hardware/model-orchestrator';

// -- Helpers ----------------------------------------------------------------

/** Standard model definitions matching the registry. */
const EMBED_MODEL = {
  name: 'nomic-embed-text',
  vramBytes: 0.5 * GB,
  diskBytes: 0.3 * GB,
  purpose: 'Text embeddings for semantic search',
  required: true,
};

const LLM_MODEL = {
  name: 'llama3.1:8b-instruct-q4_K_M',
  vramBytes: 5.5 * GB,
  diskBytes: 4.7 * GB,
  purpose: 'Local language model for chat and reasoning',
  required: true,
};

const VISION_MODEL = {
  name: 'moondream:latest',
  vramBytes: 1.2 * GB,
  diskBytes: 0.8 * GB,
  purpose: 'Vision-language model for image understanding',
  required: true,
};

const LARGE_LLM_MODEL = {
  name: 'llama3.1:70b-instruct-q4_K_M',
  vramBytes: 40 * GB,
  diskBytes: 40 * GB,
  purpose: 'Large language model for complex reasoning',
  required: false,
};

/** Configure mocks for a standard hardware profile with 8 GB effective VRAM. */
function setupStandardProfile(effectiveVRAM: number = 8 * GB): void {
  const profilerInstance = {
    getProfile: mocks.getProfile,
    getEffectiveVRAM: mocks.getEffectiveVRAM,
  };
  mocks.getInstance.mockReturnValue(profilerInstance);
  mocks.getEffectiveVRAM.mockReturnValue(effectiveVRAM);
  mocks.getProfile.mockReturnValue({
    gpu: { name: 'Test GPU', vendor: 'nvidia', driver: '551.61', available: true },
    vram: {
      total: effectiveVRAM + 1.5 * GB,
      available: effectiveVRAM,
      systemReserved: 1.5 * GB,
    },
    ram: { total: 32 * GB, available: 16 * GB },
    cpu: { model: 'Test CPU', cores: 8, threads: 16 },
    disk: {
      modelStoragePath: '/tmp/models',
      totalSpace: 500 * GB,
      freeSpace: 100 * GB,
    },
    detectedAt: Date.now(),
  });
}

/** Mock fetch to simulate successful Ollama API responses. */
function mockFetchSuccess(): void {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
  }) as unknown as typeof fetch;
}

/** Mock fetch to simulate failed Ollama API responses. */
function mockFetchFailure(): void {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: false,
    status: 500,
    statusText: 'Internal Server Error',
  }) as unknown as typeof fetch;
}

// -- Test Suite --------------------------------------------------------------

describe('ModelOrchestrator', () => {
  beforeEach(() => {
    ModelOrchestrator.resetInstance();
    vi.clearAllMocks();
    // Reset any mockReturnValueOnce queues by re-setting defaults
    mocks.getModelList.mockReset();
    mocks.estimateVRAMUsage.mockReset();
    mocks.canFitModel.mockReset();
    mocks.getProfile.mockReset();
    mocks.getEffectiveVRAM.mockReset();
    mocks.getInstance.mockReset();
    mockFetchSuccess();
  });

  // VC-1: loadTierModels('standard') loads embeddings + 8B LLM
  it('loadTierModels("standard") loads embeddings + 8B LLM', async () => {
    setupStandardProfile(8 * GB);
    mocks.getModelList.mockReturnValue([EMBED_MODEL, LLM_MODEL]);

    const orch = ModelOrchestrator.getInstance();
    const loaded = await orch.loadTierModels('standard');

    expect(loaded).toHaveLength(2);
    const names = loaded.map((m: LoadedModel) => m.name);
    expect(names).toContain('nomic-embed-text');
    expect(names).toContain('llama3.1:8b-instruct-q4_K_M');

    // Verify fetch was called to warm up each model
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  });

  // VC-2: loadTierModels('light') loads only embeddings
  it('loadTierModels("light") loads only embeddings', async () => {
    setupStandardProfile(4 * GB);
    mocks.getModelList.mockReturnValue([EMBED_MODEL]);

    const orch = ModelOrchestrator.getInstance();
    const loaded = await orch.loadTierModels('light');

    expect(loaded).toHaveLength(1);
    expect(loaded[0].name).toBe('nomic-embed-text');
    expect(loaded[0].vramBytes).toBe(0.5 * GB);
    expect(loaded[0].purpose).toBe('Text embeddings for semantic search');
  });

  // VC-3: getLoadedModels() returns empty array before any loads
  it('getLoadedModels() returns empty array before any loads', () => {
    setupStandardProfile();

    const orch = ModelOrchestrator.getInstance();
    const loaded = orch.getLoadedModels();

    expect(loaded).toEqual([]);
    expect(loaded).toHaveLength(0);
  });

  // VC-4: getVRAMUsage() tracks estimated VRAM after model loads
  it('getVRAMUsage() tracks estimated VRAM after model loads', async () => {
    setupStandardProfile(8 * GB);
    mocks.getModelList.mockReturnValue([EMBED_MODEL, LLM_MODEL]);

    const orch = ModelOrchestrator.getInstance();

    // Before loading, VRAM usage should be 0
    expect(orch.getVRAMUsage()).toBe(0);

    await orch.loadTierModels('standard');

    // After loading, VRAM usage should be sum of both models
    // 0.5 GB + 5.5 GB = 6 GB
    expect(orch.getVRAMUsage()).toBe(0.5 * GB + 5.5 * GB);
  });

  // VC-5: canLoadModel() returns false when model would exceed budget
  it('canLoadModel() returns false when model would exceed budget', async () => {
    setupStandardProfile(7 * GB);
    mocks.getModelList.mockReturnValue([EMBED_MODEL, LLM_MODEL]);

    const orch = ModelOrchestrator.getInstance();
    await orch.loadTierModels('standard');

    // After loading embeddings (0.5GB) + LLM (5.5GB) = 6GB used
    // Budget is 7 GB, so 1 GB remaining
    // Vision model needs 1.2 GB -> should not fit
    expect(orch.canLoadModel(VISION_MODEL)).toBe(false);

    // A small model that fits in remaining headroom should return true
    const tinyModel = { ...EMBED_MODEL, name: 'tiny-model', vramBytes: 0.5 * GB };
    expect(orch.canLoadModel(tinyModel)).toBe(true);
  });

  // VC-6: loadModel() triggers evictLeastRecent() when VRAM full
  it('loadModel() triggers eviction when VRAM is full', async () => {
    setupStandardProfile(6.5 * GB);

    // First call (loadTierModels) returns standard models
    mocks.getModelList.mockReturnValueOnce([EMBED_MODEL, LLM_MODEL]);
    // Subsequent calls from loadModel lookup should return full tier models
    mocks.getModelList
      .mockReturnValueOnce([EMBED_MODEL, LLM_MODEL, VISION_MODEL, LARGE_LLM_MODEL])
      .mockReturnValueOnce([EMBED_MODEL, LLM_MODEL, VISION_MODEL])
      .mockReturnValueOnce([EMBED_MODEL, LLM_MODEL])
      .mockReturnValueOnce([EMBED_MODEL])
      .mockReturnValueOnce([]);

    // Control timestamps for deterministic LRU ordering
    let now = 1000;
    const dateSpy = vi.spyOn(Date, 'now').mockImplementation(() => now);

    const orch = ModelOrchestrator.getInstance();
    await orch.loadTierModels('standard');
    // Both models loaded at timestamp 1000

    // Currently loaded: embeddings (0.5 GB) + LLM (5.5 GB) = 6 GB
    // Budget: 6.5 GB, headroom: 0.5 GB
    // Loading vision (1.2 GB) should trigger eviction of LRU

    // Advance time and mark embeddings as recently used so LLM is the LRU
    now = 2000;
    orch.markUsed('nomic-embed-text');
    // embeddings lastUsedAt=2000, LLM lastUsedAt=1000

    now = 3000;
    const visionLoaded = await orch.loadModel('moondream:latest');

    expect(visionLoaded.name).toBe('moondream:latest');

    // LLM should have been evicted (it was least recently used)
    const currentModels = orch.getLoadedModels();
    const currentNames = currentModels.map((m: LoadedModel) => m.name);
    expect(currentNames).not.toContain('llama3.1:8b-instruct-q4_K_M');
    expect(currentNames).toContain('nomic-embed-text');
    expect(currentNames).toContain('moondream:latest');

    dateSpy.mockRestore();
  });

  // VC-7: unloadModel() reduces reported VRAM usage
  it('unloadModel() reduces reported VRAM usage', async () => {
    setupStandardProfile(8 * GB);
    mocks.getModelList.mockReturnValue([EMBED_MODEL, LLM_MODEL]);

    const orch = ModelOrchestrator.getInstance();
    await orch.loadTierModels('standard');

    const usageBefore = orch.getVRAMUsage();
    expect(usageBefore).toBe(0.5 * GB + 5.5 * GB);

    await orch.unloadModel('llama3.1:8b-instruct-q4_K_M');

    const usageAfter = orch.getVRAMUsage();
    expect(usageAfter).toBe(0.5 * GB);
    expect(usageAfter).toBeLessThan(usageBefore);

    // Model should be gone from loaded list
    const names = orch.getLoadedModels().map((m: LoadedModel) => m.name);
    expect(names).not.toContain('llama3.1:8b-instruct-q4_K_M');
    expect(names).toContain('nomic-embed-text');
  });

  // VC-8: evictLeastRecent() unloads the model used longest ago
  it('evictLeastRecent() unloads the model used longest ago', async () => {
    setupStandardProfile(8 * GB);
    mocks.getModelList.mockReturnValue([EMBED_MODEL, LLM_MODEL]);

    // Control timestamps so models have distinct lastUsedAt values
    let now = 1000;
    const dateSpy = vi.spyOn(Date, 'now').mockImplementation(() => now);

    const orch = ModelOrchestrator.getInstance();
    await orch.loadTierModels('standard');
    // Both models loaded at timestamp 1000

    // Advance time and mark embeddings as recently used
    now = 2000;
    orch.markUsed('nomic-embed-text');
    // Now: embeddings lastUsedAt=2000, LLM lastUsedAt=1000

    const evictedName = await orch.evictLeastRecent();

    // LLM should have been evicted (least recently used, lastUsedAt=1000)
    expect(evictedName).toBe('llama3.1:8b-instruct-q4_K_M');

    const names = orch.getLoadedModels().map((m: LoadedModel) => m.name);
    expect(names).not.toContain('llama3.1:8b-instruct-q4_K_M');
    expect(names).toContain('nomic-embed-text');

    dateSpy.mockRestore();
  });

  // VC-9: Vision model loads on-demand, not at startup for full/sovereign tiers
  it('loadTierModels("full") does NOT load vision model (lazy loading)', async () => {
    setupStandardProfile(12 * GB);
    // full tier includes embeddings + LLM + vision, but vision should be lazy
    mocks.getModelList.mockReturnValue([EMBED_MODEL, LLM_MODEL, VISION_MODEL]);

    const orch = ModelOrchestrator.getInstance();
    const loaded = await orch.loadTierModels('full');

    // Should only load embeddings + LLM at startup, NOT vision
    const names = loaded.map((m: LoadedModel) => m.name);
    expect(names).toContain('nomic-embed-text');
    expect(names).toContain('llama3.1:8b-instruct-q4_K_M');
    expect(names).not.toContain('moondream:latest');

    // Vision model should be available for lazy loading
    const allLoaded = orch.getLoadedModels();
    expect(allLoaded).toHaveLength(2);

    // Now explicitly load the vision model on demand
    const visionLoaded = await orch.loadModel('moondream:latest');
    expect(visionLoaded.name).toBe('moondream:latest');
    expect(orch.getLoadedModels()).toHaveLength(3);
  });

  // VC-10: Orchestrator state includes tier, loaded models, VRAM usage, headroom
  it('getOrchestratorState() returns full state snapshot', async () => {
    setupStandardProfile(8 * GB);
    mocks.getModelList.mockReturnValue([EMBED_MODEL, LLM_MODEL]);

    const orch = ModelOrchestrator.getInstance();
    await orch.loadTierModels('standard');

    const state: OrchestratorState = orch.getOrchestratorState();

    expect(state.tier).toBe('standard');
    expect(state.loadedModels).toHaveLength(2);
    expect(state.estimatedVRAMUsage).toBe(0.5 * GB + 5.5 * GB);
    expect(state.vramBudget).toBe(8 * GB);
    expect(state.vramHeadroom).toBe(8 * GB - (0.5 * GB + 5.5 * GB));
    // actualVRAMUsage may be null (no nvidia-smi in tests)
    expect(state.actualVRAMUsage).toBeNull();

    // Each loaded model should have correct structure
    for (const model of state.loadedModels) {
      expect(model).toHaveProperty('name');
      expect(model).toHaveProperty('vramBytes');
      expect(model).toHaveProperty('loadedAt');
      expect(model).toHaveProperty('lastUsedAt');
      expect(model).toHaveProperty('purpose');
      expect(model.loadedAt).toBeGreaterThan(0);
      expect(model.lastUsedAt).toBeGreaterThan(0);
    }
  });
});
