/**
 * SetupWizard -- Unit tests for the first-run setup experience.
 *
 * Tests first-run detection, hardware detection triggering, state machine
 * progression, tier confirmation, model download orchestration, progress
 * reporting, skip/complete/reset flows, and file-based persistence.
 *
 * All Electron, fs, HardwareProfiler, TierRecommender, OllamaLifecycle,
 * and ModelOrchestrator APIs are mocked -- no real side effects.
 *
 * Sprint 6 P.1: "The Birth" -- SetupWizard
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { HardwareProfile } from '../../../src/main/hardware/hardware-profiler';
import type { TierRecommendation, ModelRequirement, TierName } from '../../../src/main/hardware/tier-recommender';

// -- Hoisted mocks (required for vi.mock factories) -------------------------

const mocks = vi.hoisted(() => ({
  detect: vi.fn(),
  getProfile: vi.fn(() => null),
  recommend: vi.fn(),
  getModelList: vi.fn(),
  pullModel: vi.fn(),
  loadTierModels: vi.fn(),
  getPath: vi.fn(() => '/fake/userData'),
  readFileSync: vi.fn(),
  writeFileSync: vi.fn(),
  existsSync: vi.fn(() => false),
  mkdirSync: vi.fn(),
}));

// -- Module mocks -----------------------------------------------------------

vi.mock('electron', () => ({
  app: {
    getPath: mocks.getPath,
    isPackaged: false,
    getName: vi.fn(() => 'nexus-test'),
    on: vi.fn(),
    whenReady: vi.fn().mockResolvedValue(undefined),
  },
  ipcMain: { handle: vi.fn(), on: vi.fn(), once: vi.fn() },
}));

vi.mock('node:fs', () => ({
  default: {
    readFileSync: mocks.readFileSync,
    writeFileSync: mocks.writeFileSync,
    existsSync: mocks.existsSync,
    mkdirSync: mocks.mkdirSync,
  },
  readFileSync: mocks.readFileSync,
  writeFileSync: mocks.writeFileSync,
  existsSync: mocks.existsSync,
  mkdirSync: mocks.mkdirSync,
}));

vi.mock('../../../src/main/hardware/hardware-profiler', () => ({
  HardwareProfiler: {
    getInstance: () => ({
      detect: mocks.detect,
      getProfile: mocks.getProfile,
    }),
  },
}));

vi.mock('../../../src/main/hardware/tier-recommender', () => ({
  recommend: mocks.recommend,
  getModelList: mocks.getModelList,
}));

vi.mock('../../../src/main/ollama-lifecycle', () => ({
  OllamaLifecycle: {
    getInstance: () => ({
      pullModel: mocks.pullModel,
    }),
  },
}));

vi.mock('../../../src/main/hardware/model-orchestrator', () => ({
  ModelOrchestrator: {
    getInstance: () => ({
      loadTierModels: mocks.loadTierModels,
    }),
  },
}));

// -- Import after mocks -----------------------------------------------------

import { SetupWizard } from '../../../src/main/setup/setup-wizard';

// -- Test Data ---------------------------------------------------------------

const GB = 1024 * 1024 * 1024;

const fakeProfile: HardwareProfile = {
  gpu: { name: 'NVIDIA RTX 4070', vendor: 'nvidia', driver: '545.0', available: true },
  vram: { total: 12 * GB, available: 10 * GB, systemReserved: 1.5 * GB },
  ram: { total: 32 * GB, available: 16 * GB },
  cpu: { model: 'AMD Ryzen 7 5800X', cores: 8, threads: 16 },
  disk: { modelStoragePath: '/fake/models', totalSpace: 500 * GB, freeSpace: 250 * GB },
  detectedAt: Date.now(),
};

const fakeModels: ModelRequirement[] = [
  { name: 'nomic-embed-text', vramBytes: 0.5 * GB, diskBytes: 0.3 * GB, purpose: 'embeddings', required: true },
  { name: 'llama3.1:8b-instruct-q4_K_M', vramBytes: 5.5 * GB, diskBytes: 4.7 * GB, purpose: 'chat', required: true },
];

const fakeRecommendation: TierRecommendation = {
  tier: 'standard',
  models: fakeModels,
  totalVRAM: 6 * GB,
  totalDisk: 5 * GB,
  diskSufficient: true,
  vramHeadroom: 4 * GB,
  upgradePath: null,
  warnings: [],
};

/** Helper: create an async generator that yields items from an array. */
async function* asyncGen<T>(items: T[]): AsyncGenerator<T> {
  for (const item of items) {
    yield item;
  }
}

// -- Tests -------------------------------------------------------------------

describe('SetupWizard', () => {
  beforeEach(() => {
    SetupWizard.resetInstance();
    vi.clearAllMocks();
    mocks.existsSync.mockReturnValue(false);
  });

  // -- Test 1 ---------------------------------------------------------------
  it('isFirstRun() returns true when no setup marker exists', () => {
    mocks.existsSync.mockReturnValue(false);
    const wizard = SetupWizard.getInstance();
    expect(wizard.isFirstRun()).toBe(true);
  });

  // -- Test 2 ---------------------------------------------------------------
  it('isFirstRun() returns false after completeSetup() called', () => {
    const wizard = SetupWizard.getInstance();

    // Manually advance state so completeSetup is valid
    wizard['state'].confirmedTier = 'whisper';
    wizard['state'].step = 'downloading';
    wizard.completeSetup();

    // Now it should read the persisted file
    mocks.existsSync.mockReturnValue(true);
    mocks.readFileSync.mockReturnValue(JSON.stringify({ completed: true, tier: 'whisper' }));

    expect(wizard.isFirstRun()).toBe(false);
    expect(mocks.writeFileSync).toHaveBeenCalled();
  });

  // -- Test 3 ---------------------------------------------------------------
  it('startSetup() triggers hardware detection automatically', async () => {
    mocks.detect.mockResolvedValue(fakeProfile);
    mocks.recommend.mockReturnValue(fakeRecommendation);

    const wizard = SetupWizard.getInstance();
    await wizard.startSetup();

    expect(mocks.detect).toHaveBeenCalledOnce();
  });

  // -- Test 4 ---------------------------------------------------------------
  it('getSetupState() progresses through detect -> recommend -> confirming', async () => {
    mocks.detect.mockResolvedValue(fakeProfile);
    mocks.recommend.mockReturnValue(fakeRecommendation);

    const wizard = SetupWizard.getInstance();
    const stateChanges: string[] = [];

    wizard.on('setup-state-changed', (s) => {
      stateChanges.push(s.step);
    });

    // Initially idle
    expect(wizard.getSetupState().step).toBe('idle');

    await wizard.startSetup();

    // Should have progressed through detecting -> recommending -> confirming
    expect(stateChanges).toContain('detecting');
    expect(stateChanges).toContain('recommending');
    expect(stateChanges).toContain('confirming');
    expect(wizard.getSetupState().step).toBe('confirming');
    expect(wizard.getSetupState().recommendation).toEqual(fakeRecommendation);
  });

  // -- Test 5 ---------------------------------------------------------------
  it('confirmTier() accepts the recommended tier or a user-chosen lower tier', async () => {
    mocks.detect.mockResolvedValue(fakeProfile);
    mocks.recommend.mockReturnValue(fakeRecommendation);

    const wizard = SetupWizard.getInstance();
    await wizard.startSetup();

    // Confirm with the recommended tier
    wizard.confirmTier('standard');
    expect(wizard.getSetupState().confirmedTier).toBe('standard');

    // Reset and confirm with a lower tier
    SetupWizard.resetInstance();
    vi.clearAllMocks();
    mocks.existsSync.mockReturnValue(false);
    mocks.detect.mockResolvedValue(fakeProfile);
    mocks.recommend.mockReturnValue(fakeRecommendation);

    const wizard2 = SetupWizard.getInstance();
    await wizard2.startSetup();
    wizard2.confirmTier('light');
    expect(wizard2.getSetupState().confirmedTier).toBe('light');
  });

  // -- Test 6 ---------------------------------------------------------------
  it('startModelDownload() initiates downloads for confirmed tier models', async () => {
    mocks.detect.mockResolvedValue(fakeProfile);
    mocks.recommend.mockReturnValue(fakeRecommendation);
    mocks.getModelList.mockReturnValue(fakeModels);
    mocks.loadTierModels.mockResolvedValue([]);

    // Mock pullModel as async generator with progress events
    mocks.pullModel.mockImplementation(() =>
      asyncGen([
        { status: 'pulling manifest' },
        { status: 'downloading', total: 1000, completed: 500 },
        { status: 'downloading', total: 1000, completed: 1000 },
        { status: 'success' },
      ]),
    );

    const wizard = SetupWizard.getInstance();
    await wizard.startSetup();
    wizard.confirmTier('standard');
    await wizard.startModelDownload();

    // pullModel should have been called for each model
    expect(mocks.pullModel).toHaveBeenCalledTimes(fakeModels.length);
    expect(mocks.pullModel).toHaveBeenCalledWith('nomic-embed-text');
    expect(mocks.pullModel).toHaveBeenCalledWith('llama3.1:8b-instruct-q4_K_M');
  });

  // -- Test 7 ---------------------------------------------------------------
  it('getDownloadProgress() reports per-model progress', async () => {
    mocks.detect.mockResolvedValue(fakeProfile);
    mocks.recommend.mockReturnValue(fakeRecommendation);
    mocks.getModelList.mockReturnValue([fakeModels[0]]); // just one model
    mocks.loadTierModels.mockResolvedValue([]);

    mocks.pullModel.mockImplementation(() =>
      asyncGen([
        { status: 'downloading', total: 1000, completed: 500 },
        { status: 'downloading', total: 1000, completed: 1000 },
        { status: 'success' },
      ]),
    );

    const wizard = SetupWizard.getInstance();
    await wizard.startSetup();
    wizard.confirmTier('standard');

    const progressSnapshots: Array<{ status: string; percentComplete: number }[]> = [];
    wizard.on('download-progress', (downloads) => {
      progressSnapshots.push(
        downloads.map((d) => ({ status: d.status, percentComplete: d.percentComplete })),
      );
    });

    await wizard.startModelDownload();

    // Should have emitted progress events
    expect(progressSnapshots.length).toBeGreaterThan(0);

    // Final state: all models complete
    const finalProgress = wizard.getDownloadProgress();
    expect(finalProgress.length).toBe(1);
    expect(finalProgress[0].status).toBe('complete');
    expect(finalProgress[0].percentComplete).toBe(100);
  });

  // -- Test 8 ---------------------------------------------------------------
  it('skipSetup() selects whisper tier and marks complete', () => {
    const wizard = SetupWizard.getInstance();

    const events: string[] = [];
    wizard.on('setup-complete', () => events.push('complete'));

    wizard.skipSetup();

    expect(wizard.getSetupState().confirmedTier).toBe('whisper');
    expect(wizard.getSetupState().step).toBe('complete');
    expect(mocks.writeFileSync).toHaveBeenCalled();
    expect(events).toContain('complete');
  });

  // -- Test 9 ---------------------------------------------------------------
  it('completeSetup() persists tier selection and marks first-run done', async () => {
    mocks.detect.mockResolvedValue(fakeProfile);
    mocks.recommend.mockReturnValue(fakeRecommendation);
    mocks.getModelList.mockReturnValue(fakeModels);
    mocks.loadTierModels.mockResolvedValue([]);

    mocks.pullModel.mockImplementation(() =>
      asyncGen([{ status: 'success' }]),
    );

    const wizard = SetupWizard.getInstance();
    await wizard.startSetup();
    wizard.confirmTier('standard');
    await wizard.startModelDownload();
    wizard.completeSetup();

    expect(mocks.writeFileSync).toHaveBeenCalled();

    // Verify persisted data shape
    const writeCall = mocks.writeFileSync.mock.calls[0];
    const persisted = JSON.parse(writeCall[1] as string);
    expect(persisted.completed).toBe(true);
    expect(persisted.tier).toBe('standard');

    // Verify state
    expect(wizard.getSetupState().step).toBe('complete');
  });

  // -- Test 10 --------------------------------------------------------------
  it('resetSetup() clears the marker so next launch triggers wizard again', () => {
    mocks.existsSync.mockReturnValue(true);
    mocks.readFileSync.mockReturnValue(JSON.stringify({ completed: true, tier: 'standard' }));

    const wizard = SetupWizard.getInstance();
    expect(wizard.isFirstRun()).toBe(false);

    wizard.resetSetup();

    // After reset, isFirstRun should return true
    mocks.existsSync.mockReturnValue(false);
    expect(wizard.isFirstRun()).toBe(true);
    expect(mocks.writeFileSync).toHaveBeenCalledWith(
      expect.any(String),
      expect.stringContaining('"completed":false'),
    );
  });
});
