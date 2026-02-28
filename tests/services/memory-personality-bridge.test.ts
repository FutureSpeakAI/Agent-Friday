/**
 * memory-personality-bridge.test.ts — Tests for the Memory-Personality Bridge (Track IX, Phase 3)
 *
 * Coverage:
 * - Default data model integrity (BridgeConfig, BridgeState, ExtractionHints, RelevanceWeights)
 * - Initialization (fresh start, persisted state reload, pruning)
 * - Loop 1: Memory Quality → Personality Style (syncMemoryToPersonality)
 * - Loop 2: User Engagement → Memory Priority (recordEngagement, dedup, priority adjustments, half-life decay)
 * - Loop 3: Personality Calibration → Memory Extraction (recomputeExtractionHints, getExtractionGuidance)
 * - Loop 4: Cross-System Proactivity Arbitration (propose, arbitrate, cooldown, priority ordering, queue cap, expiration)
 * - Anti-Manipulation Boundary Enforcement (flattery drift, urgency, option count, FatalIntegrityError)
 * - Context Generation (getPromptContext)
 * - State management (getState, getConfig, getRelevanceWeights, updateConfig, reset)
 * - Persistence (save queue, serialization)
 * - Context stream subscription and event handling
 * - cLaw compliance boundaries
 * - Edge cases and boundary conditions
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── vi.hoisted: define mocks BEFORE vi.mock hoisting ────────────────

const mocks = vi.hoisted(() => {
  const contextStreamListeners: Array<(event: any) => void> = [];

  return {
    mockWriteFile: vi.fn().mockResolvedValue(undefined),
    mockReadFile: vi.fn().mockRejectedValue(new Error('ENOENT')),

    mockMemoryManager: {
      getLongTerm: vi.fn().mockReturnValue([]),
    },
    mockEpisodicMemory: {
      getRecent: vi.fn().mockReturnValue([]),
    },
    mockPersonalityCalibration: {
      getDimensions: vi.fn().mockReturnValue({
        formality: 0.5,
        verbosity: 0.5,
        humor: 0.5,
        technicalDepth: 0.5,
        emotionalWarmth: 0.5,
        proactivity: 0.5,
      }),
    },
    mockContextStream: {
      push: vi.fn(),
      on: vi.fn((listener: any) => {
        contextStreamListeners.push(listener);
        return () => {
          const idx = contextStreamListeners.indexOf(listener);
          if (idx >= 0) contextStreamListeners.splice(idx, 1);
        };
      }),
    },
    contextStreamListeners,
    mockCommitmentTracker: {
      getActiveCommitments: vi.fn().mockReturnValue([]),
    },
    MockFatalIntegrityError: class extends Error {
      source: string;
      category = 'fatal';
      constructor(source: string, message: string) {
        super(message);
        this.name = 'FatalIntegrityError';
        this.source = source;
      }
    },
  };
});

// ── Mock ESM modules (electron, fs/promises, errors) ─────────────────
// NOTE: The 5 lazy-bound dependencies (memory, episodic-memory, personality-calibration,
// context-stream, commitment-tracker) use CJS require() at runtime, which vi.mock()
// cannot intercept in an ESM-transformed module. We inject those via __test_setDeps().

vi.mock('electron', () => ({
  app: { getPath: vi.fn().mockReturnValue('/mock/userData') },
}));

vi.mock('fs/promises', () => ({
  default: {
    readFile: (...args: any[]) => mocks.mockReadFile(...args),
    writeFile: (...args: any[]) => mocks.mockWriteFile(...args),
  },
}));

vi.mock('../../src/main/errors', () => ({
  FatalIntegrityError: mocks.MockFatalIntegrityError,
}));

// ── Import after mocks ──────────────────────────────────────────────

import {
  memoryPersonalityBridge,
  DEFAULT_BRIDGE_CONFIG,
  __test_setDeps,
  __test_resetDeps,
  type BridgeConfig,
  type BridgeState,
  type ExtractionHints,
  type RelevanceWeights,
  type MemoryEngagement,
  type ProactivityProposal,
  type ManipulationMetrics,
} from '../../src/main/memory-personality-bridge';

// ── Helpers ─────────────────────────────────────────────────────────

function injectMocks(): void {
  __test_setDeps({
    memoryManager: mocks.mockMemoryManager,
    episodicMemory: mocks.mockEpisodicMemory,
    personalityCalibration: mocks.mockPersonalityCalibration,
    contextStream: mocks.mockContextStream,
    commitmentTracker: mocks.mockCommitmentTracker,
  });
}

function fireContextEvent(event: any): void {
  for (const listener of mocks.contextStreamListeners) {
    listener(event);
  }
}

// ═══════════════════════════════════════════════════════════════════════
// 1. DEFAULT DATA MODEL
// ═══════════════════════════════════════════════════════════════════════

describe('Default Data Model', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('DEFAULT_BRIDGE_CONFIG has sensible defaults', () => {
    expect(DEFAULT_BRIDGE_CONFIG.proactivityCooldownMs).toBe(600_000);
    expect(DEFAULT_BRIDGE_CONFIG.maxEngagements).toBe(500);
    expect(DEFAULT_BRIDGE_CONFIG.engagementRetentionMs).toBe(30 * 24 * 60 * 60 * 1000);
    expect(DEFAULT_BRIDGE_CONFIG.flatteryThreshold).toBe(0.7);
    expect(DEFAULT_BRIDGE_CONFIG.urgencyThreshold).toBe(0.6);
    expect(DEFAULT_BRIDGE_CONFIG.optionCountFloor).toBe(2.0);
    expect(DEFAULT_BRIDGE_CONFIG.maxViolations).toBe(3);
    expect(DEFAULT_BRIDGE_CONFIG.windowSize).toBe(20);
  });

  it('config returns current configuration', () => {
    const config = memoryPersonalityBridge.getConfig();
    expect(config.proactivityCooldownMs).toBe(DEFAULT_BRIDGE_CONFIG.proactivityCooldownMs);
    expect(config.maxEngagements).toBe(DEFAULT_BRIDGE_CONFIG.maxEngagements);
  });

  it('fresh state has empty engagements', () => {
    const state = memoryPersonalityBridge.getState();
    expect(state.engagements).toHaveLength(0);
  });

  it('fresh state has default extraction hints (all false)', () => {
    const state = memoryPersonalityBridge.getState();
    expect(state.extractionHints.preferTechnical).toBe(false);
    expect(state.extractionHints.preferFormal).toBe(false);
    expect(state.extractionHints.preferEmotional).toBe(false);
    expect(state.extractionHints.compactExtraction).toBe(false);
  });

  it('fresh state has zero manipulation violations', () => {
    const state = memoryPersonalityBridge.getState();
    expect(state.manipulation.violations).toBe(0);
    expect(state.manipulation.flatteryWindow).toHaveLength(0);
    expect(state.manipulation.urgencyWindow).toHaveLength(0);
    expect(state.manipulation.optionCountWindow).toHaveLength(0);
  });

  it('fresh state has default relevance weights', () => {
    const weights = memoryPersonalityBridge.getRelevanceWeights();
    expect(weights.engagementBoost).toBe(0.3);
    expect(weights.commitmentBoost).toBe(0.2);
    expect(weights.dismissalPenalty).toBe(-0.15);
    expect(weights.workStreamBoost).toBe(0.1);
  });

  it('fresh state has lastProactivityDelivery at 0', () => {
    const state = memoryPersonalityBridge.getState();
    expect(state.lastProactivityDelivery).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 2. INITIALIZATION
// ═══════════════════════════════════════════════════════════════════════

describe('Initialization', () => {
  beforeEach(() => {
    __test_resetDeps();
    injectMocks();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('initializes with empty state when no file exists', async () => {
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    await memoryPersonalityBridge.initialize();
    expect(memoryPersonalityBridge.isInitialized()).toBe(true);
    expect(memoryPersonalityBridge.getEngagements()).toHaveLength(0);
  });

  it('loads persisted state from file', async () => {
    const persisted = {
      engagements: [
        { memoryId: 'm1', type: 'referenced', timestamp: Date.now(), context: 'test' },
      ],
      extractionHints: { preferTechnical: true, preferFormal: false, preferEmotional: false, compactExtraction: false },
      lastProactivityDelivery: 12345,
      manipulation: {
        flatteryWindow: [false, true],
        urgencyWindow: [false],
        optionCountWindow: [3, 4],
        violations: 1,
        lastCheck: 0,
      },
      relevanceWeights: { engagementBoost: 0.4, commitmentBoost: 0.2, dismissalPenalty: -0.15, workStreamBoost: 0.1 },
    };
    mocks.mockReadFile.mockResolvedValue(JSON.stringify(persisted));

    await memoryPersonalityBridge.initialize();
    const state = memoryPersonalityBridge.getState();
    expect(state.engagements).toHaveLength(1);
    expect(state.engagements[0].memoryId).toBe('m1');
    expect(state.lastProactivityDelivery).toBe(12345);
    expect(state.manipulation.violations).toBe(1);
    expect(state.manipulation.flatteryWindow).toEqual([false, true]);
    expect(state.relevanceWeights.engagementBoost).toBe(0.4);
  });

  it('prunes expired engagements on init', async () => {
    const now = Date.now();
    const oldTimestamp = now - 31 * 24 * 60 * 60 * 1000; // 31 days ago
    const persisted = {
      engagements: [
        { memoryId: 'old', type: 'referenced', timestamp: oldTimestamp, context: 'old' },
        { memoryId: 'fresh', type: 'referenced', timestamp: now - 1000, context: 'recent' },
      ],
      extractionHints: {},
      lastProactivityDelivery: 0,
      manipulation: { flatteryWindow: [], urgencyWindow: [], optionCountWindow: [], violations: 0, lastCheck: 0 },
      relevanceWeights: {},
    };
    mocks.mockReadFile.mockResolvedValue(JSON.stringify(persisted));

    await memoryPersonalityBridge.initialize();
    const engagements = memoryPersonalityBridge.getEngagements();
    expect(engagements).toHaveLength(1);
    expect(engagements[0].memoryId).toBe('fresh');
  });

  it('merges partial persisted state gracefully', async () => {
    mocks.mockReadFile.mockResolvedValue(JSON.stringify({ engagements: [] }));
    await memoryPersonalityBridge.initialize();
    const state = memoryPersonalityBridge.getState();
    expect(state.extractionHints.preferTechnical).toBe(false);
    expect(state.manipulation.violations).toBe(0);
    expect(state.relevanceWeights.engagementBoost).toBe(0.3);
  });

  it('handles corrupted JSON gracefully', async () => {
    mocks.mockReadFile.mockResolvedValue('not json at all');
    await memoryPersonalityBridge.initialize();
    expect(memoryPersonalityBridge.isInitialized()).toBe(true);
    expect(memoryPersonalityBridge.getEngagements()).toHaveLength(0);
  });

  it('subscribes to context stream on init', async () => {
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    await memoryPersonalityBridge.initialize();
    expect(mocks.mockContextStream.on).toHaveBeenCalled();
  });

  it('recomputes extraction hints on init', async () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.8,
      verbosity: 0.3,
      humor: 0.5,
      technicalDepth: 0.9,
      emotionalWarmth: 0.7,
      proactivity: 0.5,
    });
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    await memoryPersonalityBridge.initialize();

    const hints = memoryPersonalityBridge.getExtractionHints();
    expect(hints.preferTechnical).toBe(true);
    expect(hints.preferFormal).toBe(true);
    expect(hints.preferEmotional).toBe(true);
    expect(hints.compactExtraction).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 3. LOOP 1: Memory Quality → Personality Style
// ═══════════════════════════════════════════════════════════════════════

describe('Loop 1: syncMemoryToPersonality', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('skips if fewer than 3 long-term memories', () => {
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { category: 'professional', fact: 'fact1' },
      { category: 'professional', fact: 'fact2' },
    ]);
    const weightsBefore = memoryPersonalityBridge.getRelevanceWeights();
    memoryPersonalityBridge.syncMemoryToPersonality();
    const weightsAfter = memoryPersonalityBridge.getRelevanceWeights();
    expect(weightsAfter).toEqual(weightsBefore);
  });

  it('adjusts engagement boost for long sessions', () => {
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { category: 'professional', fact: 'fact1' },
      { category: 'professional', fact: 'fact2' },
      { category: 'professional', fact: 'fact3' },
    ]);
    mocks.mockEpisodicMemory.getRecent.mockReturnValue([
      { turnCount: 25, emotionalTone: 'neutral' },
      { turnCount: 30, emotionalTone: 'focused' },
    ]);

    memoryPersonalityBridge.syncMemoryToPersonality();
    const weights = memoryPersonalityBridge.getRelevanceWeights();
    expect(weights.engagementBoost).toBe(0.4); // avgTurns = 27.5 > 20
  });

  it('adjusts engagement boost for medium sessions', () => {
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { category: 'relationship', fact: 'rel1' },
      { category: 'relationship', fact: 'rel2' },
      { category: 'preference', fact: 'pref1' },
    ]);
    mocks.mockEpisodicMemory.getRecent.mockReturnValue([
      { turnCount: 15, emotionalTone: 'warm' },
      { turnCount: 12, emotionalTone: 'neutral' },
    ]);

    memoryPersonalityBridge.syncMemoryToPersonality();
    const weights = memoryPersonalityBridge.getRelevanceWeights();
    expect(weights.engagementBoost).toBe(0.3); // avgTurns = 13.5 > 10
  });

  it('adjusts engagement boost for short sessions', () => {
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { category: 'professional', fact: 'f1' },
      { category: 'professional', fact: 'f2' },
      { category: 'professional', fact: 'f3' },
    ]);
    mocks.mockEpisodicMemory.getRecent.mockReturnValue([
      { turnCount: 5, emotionalTone: 'neutral' },
      { turnCount: 3, emotionalTone: 'quick' },
    ]);

    memoryPersonalityBridge.syncMemoryToPersonality();
    const weights = memoryPersonalityBridge.getRelevanceWeights();
    expect(weights.engagementBoost).toBe(0.2); // avgTurns = 4 < 10
  });

  it('boosts workStreamBoost when relationship ratio > 30%', () => {
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { category: 'relationship', fact: 'r1' },
      { category: 'relationship', fact: 'r2' },
      { category: 'professional', fact: 'p1' },
      { category: 'preference', fact: 'pf1' },
    ]);
    mocks.mockEpisodicMemory.getRecent.mockReturnValue([
      { turnCount: 10, emotionalTone: 'neutral' },
    ]);

    memoryPersonalityBridge.syncMemoryToPersonality();
    const weights = memoryPersonalityBridge.getRelevanceWeights();
    expect(weights.workStreamBoost).toBe(0.2); // 2/4 = 50% > 30%
  });

  it('does not boost workStreamBoost when relationship ratio ≤ 30%', () => {
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { category: 'professional', fact: 'p1' },
      { category: 'professional', fact: 'p2' },
      { category: 'professional', fact: 'p3' },
      { category: 'relationship', fact: 'r1' },
    ]);
    mocks.mockEpisodicMemory.getRecent.mockReturnValue([
      { turnCount: 10, emotionalTone: 'neutral' },
    ]);

    memoryPersonalityBridge.syncMemoryToPersonality();
    const weights = memoryPersonalityBridge.getRelevanceWeights();
    expect(weights.workStreamBoost).toBe(0.1); // 1/4 = 25% ≤ 30%
  });

  it('handles missing episodic memory gracefully', () => {
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { category: 'professional', fact: 'f1' },
      { category: 'professional', fact: 'f2' },
      { category: 'professional', fact: 'f3' },
    ]);
    mocks.mockEpisodicMemory.getRecent.mockReturnValue([]);
    expect(() => memoryPersonalityBridge.syncMemoryToPersonality()).not.toThrow();
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 4. LOOP 2: User Engagement → Memory Priority
// ═══════════════════════════════════════════════════════════════════════

describe('Loop 2: recordEngagement', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('records a basic engagement', () => {
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', 'User mentioned this fact');
    const engagements = memoryPersonalityBridge.getEngagements();
    expect(engagements).toHaveLength(1);
    expect(engagements[0].memoryId).toBe('mem-1');
    expect(engagements[0].type).toBe('referenced');
    expect(engagements[0].context).toBe('User mentioned this fact');
  });

  it('truncates context to 200 chars', () => {
    const longContext = 'x'.repeat(300);
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', longContext);
    const engagements = memoryPersonalityBridge.getEngagements();
    expect(engagements[0].context).toHaveLength(200);
  });

  it('deduplicates same memory + type within 5 minutes', () => {
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', 'first');
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', 'second');
    const engagements = memoryPersonalityBridge.getEngagements();
    expect(engagements).toHaveLength(1);
  });

  it('allows same memory with different type', () => {
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', 'ref');
    memoryPersonalityBridge.recordEngagement('mem-1', 'corrected', 'cor');
    const engagements = memoryPersonalityBridge.getEngagements();
    expect(engagements).toHaveLength(2);
  });

  it('allows different memories with same type', () => {
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', 'ref1');
    memoryPersonalityBridge.recordEngagement('mem-2', 'referenced', 'ref2');
    const engagements = memoryPersonalityBridge.getEngagements();
    expect(engagements).toHaveLength(2);
  });

  it('emits to context stream', () => {
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', 'test context');
    expect(mocks.mockContextStream.push).toHaveBeenCalledTimes(1);
    const call = mocks.mockContextStream.push.mock.calls[0][0];
    expect(call.source).toBe('memory-personality-bridge');
    expect(call.data.memoryId).toBe('mem-1');
    expect(call.data.engagementType).toBe('referenced');
  });

  it('caps engagements at maxEngagements', () => {
    memoryPersonalityBridge.updateConfig({ maxEngagements: 5 });
    for (let i = 0; i < 8; i++) {
      memoryPersonalityBridge.recordEngagement(`mem-${i}`, 'referenced', `ctx-${i}`);
    }
    const engagements = memoryPersonalityBridge.getEngagements();
    expect(engagements).toHaveLength(5);
    expect(engagements[0].memoryId).toBe('mem-3');
    expect(engagements[4].memoryId).toBe('mem-7');
  });

  it('records all engagement types', () => {
    memoryPersonalityBridge.recordEngagement('m1', 'referenced', 'ref');
    memoryPersonalityBridge.recordEngagement('m2', 'corrected', 'cor');
    memoryPersonalityBridge.recordEngagement('m3', 'asked_about', 'ask');
    memoryPersonalityBridge.recordEngagement('m4', 'dismissed', 'dis');
    const engagements = memoryPersonalityBridge.getEngagements();
    expect(engagements).toHaveLength(4);
    expect(engagements.map(e => e.type)).toEqual(['referenced', 'corrected', 'asked_about', 'dismissed']);
  });
});

describe('Loop 2: getMemoryPriorityAdjustments', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('returns empty map with no engagements', () => {
    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    expect(adjustments.size).toBe(0);
  });

  it('returns positive adjustment for referenced memories', () => {
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', 'test');
    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    expect(adjustments.has('mem-1')).toBe(true);
    expect(adjustments.get('mem-1')!).toBeGreaterThan(0);
  });

  it('returns higher adjustment for corrected than referenced', () => {
    memoryPersonalityBridge.recordEngagement('mem-ref', 'referenced', 'ref');
    memoryPersonalityBridge.recordEngagement('mem-cor', 'corrected', 'cor');
    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    expect(adjustments.get('mem-cor')!).toBeGreaterThan(adjustments.get('mem-ref')!);
  });

  it('returns lower adjustment for asked_about than referenced', () => {
    memoryPersonalityBridge.recordEngagement('mem-ref', 'referenced', 'ref');
    memoryPersonalityBridge.recordEngagement('mem-ask', 'asked_about', 'ask');
    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    expect(adjustments.get('mem-ask')!).toBeLessThan(adjustments.get('mem-ref')!);
    expect(adjustments.get('mem-ask')!).toBeGreaterThan(0);
  });

  it('returns negative adjustment for dismissed memories', () => {
    memoryPersonalityBridge.recordEngagement('mem-dis', 'dismissed', 'dis');
    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    expect(adjustments.get('mem-dis')!).toBeLessThan(0);
  });

  it('accumulates adjustments for multiple engagements with same memory', () => {
    memoryPersonalityBridge.recordEngagement('mem-1', 'referenced', 'ref');
    memoryPersonalityBridge.recordEngagement('mem-1', 'corrected', 'cor');
    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    const combined = adjustments.get('mem-1')!;
    expect(combined).toBeGreaterThan(0);
  });

  it('boosts memories overlapping with active commitments', () => {
    mocks.mockCommitmentTracker.getActiveCommitments.mockReturnValue([
      { description: 'deliver the quarterly report', personName: 'sarah' },
    ]);
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { id: 'mem-match', fact: 'Sarah asked about the quarterly report deadline', category: 'professional' },
      { id: 'mem-no-match', fact: 'Prefers dark mode in editors', category: 'preference' },
    ]);

    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    expect(adjustments.has('mem-match')).toBe(true);
    expect(adjustments.get('mem-match')!).toBeGreaterThan(0);
  });

  it('handles commitment tracker not being ready', () => {
    mocks.mockCommitmentTracker.getActiveCommitments.mockImplementation(() => {
      throw new Error('Not initialized');
    });
    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    expect(adjustments).toBeDefined();
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 5. LOOP 3: Personality Calibration → Memory Extraction
// ═══════════════════════════════════════════════════════════════════════

describe('Loop 3: recomputeExtractionHints', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('sets preferTechnical when technicalDepth > 0.65', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.8, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionHints().preferTechnical).toBe(true);
  });

  it('does NOT set preferTechnical when technicalDepth = 0.65', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.65, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionHints().preferTechnical).toBe(false);
  });

  it('sets preferFormal when formality > 0.65', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.7, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionHints().preferFormal).toBe(true);
  });

  it('sets preferEmotional when emotionalWarmth > 0.65', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.9, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionHints().preferEmotional).toBe(true);
  });

  it('sets compactExtraction when verbosity < 0.35', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.2, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionHints().compactExtraction).toBe(true);
  });

  it('does NOT set compactExtraction when verbosity = 0.35', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.35, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionHints().compactExtraction).toBe(false);
  });

  it('sets all hints for extreme personality', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.9, verbosity: 0.1, humor: 0.5,
      technicalDepth: 0.9, emotionalWarmth: 0.9, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    const hints = memoryPersonalityBridge.getExtractionHints();
    expect(hints.preferTechnical).toBe(true);
    expect(hints.preferFormal).toBe(true);
    expect(hints.preferEmotional).toBe(true);
    expect(hints.compactExtraction).toBe(true);
  });

  it('sets no hints for middle personality', () => {
    memoryPersonalityBridge.recomputeExtractionHints();
    const hints = memoryPersonalityBridge.getExtractionHints();
    expect(hints.preferTechnical).toBe(false);
    expect(hints.preferFormal).toBe(false);
    expect(hints.preferEmotional).toBe(false);
    expect(hints.compactExtraction).toBe(false);
  });

  it('falls back to defaults when calibration throws', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockImplementation(() => {
      throw new Error('Not initialized');
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    const hints = memoryPersonalityBridge.getExtractionHints();
    expect(hints.preferTechnical).toBe(false);
    expect(hints.preferFormal).toBe(false);
  });
});

describe('Loop 3: getExtractionGuidance', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('returns empty string when no hints are active', () => {
    expect(memoryPersonalityBridge.getExtractionGuidance()).toBe('');
  });

  it('includes technical guidance when preferTechnical', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.9, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    const guidance = memoryPersonalityBridge.getExtractionGuidance();
    expect(guidance).toContain('technical details');
    expect(guidance).toContain('EXTRACTION PREFERENCES');
  });

  it('includes formal guidance when preferFormal', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.8, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionGuidance()).toContain('professional context');
  });

  it('includes emotional guidance when preferEmotional', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.8, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionGuidance()).toContain('relational nuance');
  });

  it('includes compact guidance when compactExtraction', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.2, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    expect(memoryPersonalityBridge.getExtractionGuidance()).toContain('highly selective');
  });

  it('combines multiple hints', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.9, verbosity: 0.1, humor: 0.5,
      technicalDepth: 0.9, emotionalWarmth: 0.9, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    const guidance = memoryPersonalityBridge.getExtractionGuidance();
    expect(guidance).toContain('technical details');
    expect(guidance).toContain('professional context');
    expect(guidance).toContain('relational nuance');
    expect(guidance).toContain('highly selective');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 6. LOOP 4: Cross-System Proactivity Arbitration
// ═══════════════════════════════════════════════════════════════════════

describe('Loop 4: Proactivity Arbitration', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('proposeProactivity returns an ID', () => {
    const id = memoryPersonalityBridge.proposeProactivity({
      source: 'commitment-tracker',
      priority: 1,
      reason: 'Test proposal',
      content: 'You have a deadline',
      ttlMs: 60_000,
    });
    expect(id).toBeDefined();
    expect(typeof id).toBe('string');
    expect(id.startsWith('prop-')).toBe(true);
  });

  it('getPendingProposalCount tracks proposals', () => {
    expect(memoryPersonalityBridge.getPendingProposalCount()).toBe(0);
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0,
      reason: 'Morning check-in', content: 'Good morning!', ttlMs: 300_000,
    });
    expect(memoryPersonalityBridge.getPendingProposalCount()).toBe(1);
  });

  it('arbitrate selects highest priority proposal', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Low', content: 'low', ttlMs: 60_000,
    });
    memoryPersonalityBridge.proposeProactivity({
      source: 'commitment-tracker', priority: 2, reason: 'High', content: 'high', ttlMs: 60_000,
    });
    memoryPersonalityBridge.proposeProactivity({
      source: 'personality-calibration', priority: 1, reason: 'Medium', content: 'medium', ttlMs: 60_000,
    });

    const winner = memoryPersonalityBridge.arbitrateProactivity();
    expect(winner).not.toBeNull();
    expect(winner!.priority).toBe(2);
    expect(winner!.content).toBe('high');
  });

  it('enforces cooldown — returns null when in cooldown', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'First', content: 'first', ttlMs: 60_000,
    });
    const first = memoryPersonalityBridge.arbitrateProactivity();
    expect(first).not.toBeNull();

    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Second', content: 'second', ttlMs: 60_000,
    });
    const second = memoryPersonalityBridge.arbitrateProactivity();
    expect(second).toBeNull();
  });

  it('getProactivityCooldownRemaining returns time left', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'test', content: 'test', ttlMs: 60_000,
    });
    memoryPersonalityBridge.arbitrateProactivity();
    const remaining = memoryPersonalityBridge.getProactivityCooldownRemaining();
    expect(remaining).toBeGreaterThan(0);
    expect(remaining).toBeLessThanOrEqual(600_000);
  });

  it('returns null when no proposals exist', () => {
    expect(memoryPersonalityBridge.arbitrateProactivity()).toBeNull();
  });

  it('caps proposal queue at 20', () => {
    for (let i = 0; i < 25; i++) {
      memoryPersonalityBridge.proposeProactivity({
        source: 'daily-briefing', priority: 0,
        reason: `Proposal ${i}`, content: `content-${i}`, ttlMs: 300_000,
      });
    }
    expect(memoryPersonalityBridge.getPendingProposalCount()).toBeLessThanOrEqual(20);
  });

  it('removes winning proposal from queue', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Only one', content: 'only', ttlMs: 300_000,
    });
    expect(memoryPersonalityBridge.getPendingProposalCount()).toBe(1);
    memoryPersonalityBridge.arbitrateProactivity();
    expect(memoryPersonalityBridge.getPendingProposalCount()).toBe(0);
  });

  it('emits to context stream on delivery', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'commitment-tracker', priority: 2,
      reason: 'Deadline', content: 'deadline approaching', ttlMs: 60_000,
    });
    mocks.mockContextStream.push.mockClear();
    memoryPersonalityBridge.arbitrateProactivity();
    expect(mocks.mockContextStream.push).toHaveBeenCalled();
    const call = mocks.mockContextStream.push.mock.calls[0][0];
    expect(call.source).toBe('memory-personality-bridge');
    expect(call.data.source).toBe('commitment-tracker');
    expect(call.data.priority).toBe(2);
  });

  it('breaks priority ties by timestamp (oldest first)', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 1, reason: 'First', content: 'first', ttlMs: 300_000,
    });
    memoryPersonalityBridge.proposeProactivity({
      source: 'personality-calibration', priority: 1, reason: 'Second', content: 'second', ttlMs: 300_000,
    });
    const winner = memoryPersonalityBridge.arbitrateProactivity();
    expect(winner).not.toBeNull();
    expect(winner!.content).toBe('first');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 7. ANTI-MANIPULATION BOUNDARY ENFORCEMENT
// ═══════════════════════════════════════════════════════════════════════

describe('Anti-Manipulation Boundary', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
    // Note: default manipulationCheckIntervalMs = 60000ms means only ONE check fires per test
    // (first check passes because lastCheck=0, subsequent checks skip due to interval)
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('records exchange observations in rolling windows', () => {
    memoryPersonalityBridge.recordExchangeObservation(false, false, 3);
    memoryPersonalityBridge.recordExchangeObservation(true, false, 2);
    const metrics = memoryPersonalityBridge.getManipulationMetrics();
    expect(metrics.flatteryWindow).toEqual([false, true]);
    expect(metrics.urgencyWindow).toEqual([false, false]);
    expect(metrics.optionCountWindow).toEqual([3, 2]);
  });

  it('trims windows to configured size', () => {
    memoryPersonalityBridge.updateConfig({ windowSize: 5 });
    for (let i = 0; i < 8; i++) {
      memoryPersonalityBridge.recordExchangeObservation(false, false, 3);
    }
    const metrics = memoryPersonalityBridge.getManipulationMetrics();
    expect(metrics.flatteryWindow).toHaveLength(5);
    expect(metrics.urgencyWindow).toHaveLength(5);
    expect(metrics.optionCountWindow).toHaveLength(5);
  });

  it('does not check until minimum window size reached', () => {
    memoryPersonalityBridge.updateConfig({ windowSize: 20 });
    for (let i = 0; i < 9; i++) {
      memoryPersonalityBridge.recordExchangeObservation(true, true, 0);
    }
    expect(memoryPersonalityBridge.getManipulationMetrics().violations).toBe(0);
  });

  it('detects flattery drift when ratio >= threshold', () => {
    // manipulationCheckIntervalMs: 0 so every obs after minWindow triggers check.
    // maxViolations high enough to avoid FatalIntegrityError throw during obs loop.
    memoryPersonalityBridge.updateConfig({ windowSize: 10, manipulationCheckIntervalMs: 0, maxViolations: 100 });
    for (let i = 0; i < 7; i++) {
      memoryPersonalityBridge.recordExchangeObservation(true, false, 3);
    }
    for (let i = 0; i < 3; i++) {
      memoryPersonalityBridge.recordExchangeObservation(false, false, 3);
    }
    expect(memoryPersonalityBridge.getManipulationMetrics().violations).toBeGreaterThanOrEqual(1);
  });

  it('detects artificial urgency when ratio >= threshold', () => {
    memoryPersonalityBridge.updateConfig({ windowSize: 10, manipulationCheckIntervalMs: 0, maxViolations: 100 });
    for (let i = 0; i < 6; i++) {
      memoryPersonalityBridge.recordExchangeObservation(false, true, 3);
    }
    for (let i = 0; i < 4; i++) {
      memoryPersonalityBridge.recordExchangeObservation(false, false, 3);
    }
    expect(memoryPersonalityBridge.getManipulationMetrics().violations).toBeGreaterThanOrEqual(1);
  });

  it('detects reduced option presentation when avg < floor', () => {
    memoryPersonalityBridge.updateConfig({ windowSize: 10, manipulationCheckIntervalMs: 0, maxViolations: 100 });
    for (let i = 0; i < 10; i++) {
      memoryPersonalityBridge.recordExchangeObservation(false, false, 1);
    }
    expect(memoryPersonalityBridge.getManipulationMetrics().violations).toBeGreaterThanOrEqual(1);
  });

  it('does NOT violate when all metrics are within bounds', () => {
    // manipulationCheckIntervalMs: 0 so checks actually fire.
    // maxViolations: 100 just in case (should be 0 violations).
    // Interleave observations: flattery 50%, urgency 50%, options avg 3.
    // All below thresholds (flattery 0.7, urgency 0.6, options floor 2.0).
    memoryPersonalityBridge.updateConfig({ windowSize: 10, manipulationCheckIntervalMs: 0, maxViolations: 100 });
    for (let i = 0; i < 10; i++) {
      const flattery = i % 2 === 0;   // 5 true, 5 false → 50% < 70%
      const urgency = i % 3 === 0;    // ~33% < 60%
      memoryPersonalityBridge.recordExchangeObservation(flattery, urgency, 3);
    }
    expect(memoryPersonalityBridge.getManipulationMetrics().violations).toBe(0);
  });

  it('throws FatalIntegrityError after maxViolations', () => {
    // With manipulationCheckIntervalMs: 0, every observation after minWindow triggers a check.
    // windowSize: 10 → minWindow = 5. Observations 5, 6, 7 each trigger a violation.
    // Obs 7 reaches maxViolations=3 → FatalIntegrityError.
    memoryPersonalityBridge.updateConfig({
      windowSize: 10, maxViolations: 3, manipulationCheckIntervalMs: 0,
    });

    expect(() => {
      for (let i = 0; i < 10; i++) {
        memoryPersonalityBridge.recordExchangeObservation(true, false, 3);
      }
    }).toThrow();

    expect(memoryPersonalityBridge.getManipulationMetrics().violations).toBe(3);
  });

  it('FatalIntegrityError includes descriptive message', () => {
    memoryPersonalityBridge.updateConfig({ windowSize: 10, maxViolations: 1, manipulationCheckIntervalMs: 0 });
    try {
      for (let i = 0; i < 8; i++) {
        memoryPersonalityBridge.recordExchangeObservation(true, false, 3);
      }
      for (let i = 0; i < 2; i++) {
        memoryPersonalityBridge.recordExchangeObservation(false, false, 3);
      }
      expect.unreachable('Should have thrown');
    } catch (err: any) {
      expect(err.name).toBe('FatalIntegrityError');
      expect(err.message).toContain('Anti-manipulation boundary breached');
      expect(err.message).toContain('Flattery drift detected');
    }
  });

  it('clamps negative option counts to 0', () => {
    memoryPersonalityBridge.recordExchangeObservation(false, false, -5);
    const metrics = memoryPersonalityBridge.getManipulationMetrics();
    expect(metrics.optionCountWindow[0]).toBe(0);
  });

  it('getManipulationMetrics returns a defensive copy', () => {
    memoryPersonalityBridge.recordExchangeObservation(true, false, 3);
    const m1 = memoryPersonalityBridge.getManipulationMetrics();
    const m2 = memoryPersonalityBridge.getManipulationMetrics();
    expect(m1).toEqual(m2);
    expect(m1.flatteryWindow).not.toBe(m2.flatteryWindow);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 8. CONTEXT GENERATION
// ═══════════════════════════════════════════════════════════════════════

describe('Context Generation (getPromptContext)', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('returns empty string when nothing interesting', () => {
    expect(memoryPersonalityBridge.getPromptContext()).toBe('');
  });

  it('includes memory focus when hints are active', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.9, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    const ctx = memoryPersonalityBridge.getPromptContext();
    expect(ctx).toContain('[MEMORY-PERSONALITY BRIDGE]');
    expect(ctx).toContain('Memory focus:');
    expect(ctx).toContain('technical detail');
  });

  it('includes engagement summary for recent engagements', () => {
    memoryPersonalityBridge.recordEngagement('m1', 'referenced', 'ref');
    memoryPersonalityBridge.recordEngagement('m2', 'corrected', 'cor');
    const ctx = memoryPersonalityBridge.getPromptContext();
    expect(ctx).toContain('1 memories referenced');
    expect(ctx).toContain('1 corrected');
  });

  it('includes proactivity state when proposals are pending', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Test', content: 'test', ttlMs: 300_000,
    });
    const ctx = memoryPersonalityBridge.getPromptContext();
    expect(ctx).toContain('1 proactive nudge');
    expect(ctx).toContain('queued');
  });

  it('includes cooldown info when in cooldown', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Test', content: 'test', ttlMs: 300_000,
    });
    memoryPersonalityBridge.arbitrateProactivity();
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Another', content: 'another', ttlMs: 300_000,
    });
    const ctx = memoryPersonalityBridge.getPromptContext();
    expect(ctx).toContain('cooldown');
  });

  it('combines multiple context parts with pipe separator', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.9, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    memoryPersonalityBridge.recordEngagement('m1', 'referenced', 'test');
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Test', content: 'test', ttlMs: 300_000,
    });
    const ctx = memoryPersonalityBridge.getPromptContext();
    expect(ctx).toContain('Memory focus:');
    expect(ctx).toContain('memories referenced');
    expect(ctx).toContain('proactive nudge');
    expect(ctx).toContain(' | ');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 9. STATE MANAGEMENT
// ═══════════════════════════════════════════════════════════════════════

describe('State Management', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('getState returns a copy', () => {
    const s1 = memoryPersonalityBridge.getState();
    const s2 = memoryPersonalityBridge.getState();
    expect(s1).toEqual(s2);
    expect(s1.engagements).not.toBe(s2.engagements);
  });

  it('getConfig returns a copy', () => {
    const c1 = memoryPersonalityBridge.getConfig();
    const c2 = memoryPersonalityBridge.getConfig();
    expect(c1).toEqual(c2);
  });

  it('getRelevanceWeights returns a copy', () => {
    const w1 = memoryPersonalityBridge.getRelevanceWeights();
    const w2 = memoryPersonalityBridge.getRelevanceWeights();
    expect(w1).toEqual(w2);
  });

  it('getExtractionHints returns a copy', () => {
    const h1 = memoryPersonalityBridge.getExtractionHints();
    const h2 = memoryPersonalityBridge.getExtractionHints();
    expect(h1).toEqual(h2);
  });

  it('updateConfig merges partial updates', () => {
    memoryPersonalityBridge.updateConfig({ proactivityCooldownMs: 30_000 });
    const config = memoryPersonalityBridge.getConfig();
    expect(config.proactivityCooldownMs).toBe(30_000);
    expect(config.maxEngagements).toBe(500);
  });

  it('reset clears all state', async () => {
    memoryPersonalityBridge.recordEngagement('m1', 'referenced', 'test');
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Test', content: 'test', ttlMs: 300_000,
    });
    await memoryPersonalityBridge.reset();
    expect(memoryPersonalityBridge.getEngagements()).toHaveLength(0);
    expect(memoryPersonalityBridge.getPendingProposalCount()).toBe(0);
    const state = memoryPersonalityBridge.getState();
    expect(state.manipulation.violations).toBe(0);
    expect(state.lastProactivityDelivery).toBe(0);
  });

  it('reset clears manipulation violations', async () => {
    // Need manipulationCheckIntervalMs: 0 for checks to fire on every obs.
    // maxViolations high to prevent FatalIntegrityError throw during obs recording.
    memoryPersonalityBridge.updateConfig({ windowSize: 10, manipulationCheckIntervalMs: 0, maxViolations: 100 });
    for (let i = 0; i < 10; i++) {
      memoryPersonalityBridge.recordExchangeObservation(true, false, 3);
    }
    expect(memoryPersonalityBridge.getManipulationMetrics().violations).toBeGreaterThan(0);

    await memoryPersonalityBridge.reset();
    expect(memoryPersonalityBridge.getManipulationMetrics().violations).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 10. PERSISTENCE
// ═══════════════════════════════════════════════════════════════════════

describe('Persistence', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockWriteFile.mockClear();
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('saves after recordEngagement', async () => {
    mocks.mockWriteFile.mockClear();
    memoryPersonalityBridge.recordEngagement('m1', 'referenced', 'test');
    await new Promise((r) => setTimeout(r, 50));
    expect(mocks.mockWriteFile).toHaveBeenCalled();
  });

  it('saves after arbitrateProactivity delivery', async () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'Test', content: 'test', ttlMs: 300_000,
    });
    mocks.mockWriteFile.mockClear();
    memoryPersonalityBridge.arbitrateProactivity();
    await new Promise((r) => setTimeout(r, 50));
    expect(mocks.mockWriteFile).toHaveBeenCalled();
  });

  it('saves after updateConfig', async () => {
    mocks.mockWriteFile.mockClear();
    memoryPersonalityBridge.updateConfig({ proactivityCooldownMs: 5000 });
    await new Promise((r) => setTimeout(r, 50));
    expect(mocks.mockWriteFile).toHaveBeenCalled();
  });

  it('saved data includes all state fields', async () => {
    memoryPersonalityBridge.recordEngagement('m1', 'referenced', 'test');
    await new Promise((r) => setTimeout(r, 50));
    const lastCall = mocks.mockWriteFile.mock.calls[mocks.mockWriteFile.mock.calls.length - 1];
    const saved = JSON.parse(lastCall[1]);
    expect(saved).toHaveProperty('engagements');
    expect(saved).toHaveProperty('extractionHints');
    expect(saved).toHaveProperty('lastProactivityDelivery');
    expect(saved).toHaveProperty('manipulation');
    expect(saved).toHaveProperty('relevanceWeights');
  });

  it('saves to correct file path', async () => {
    await memoryPersonalityBridge.reset();
    await new Promise((r) => setTimeout(r, 50));
    const path = mocks.mockWriteFile.mock.calls[mocks.mockWriteFile.mock.calls.length - 1][0];
    expect(path).toContain('memory-personality-bridge.json');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 11. CONTEXT STREAM EVENT HANDLING
// ═══════════════════════════════════════════════════════════════════════

describe('Context Stream Event Handling', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('recomputes extraction hints on personality-calibration event', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.9, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    fireContextEvent({ source: 'personality-calibration', type: 'system' });
    expect(memoryPersonalityBridge.getExtractionHints().preferFormal).toBe(true);
  });

  it('ignores events from unrelated sources', () => {
    const hintsBefore = memoryPersonalityBridge.getExtractionHints();
    fireContextEvent({ source: 'some-other-system', type: 'system' });
    const hintsAfter = memoryPersonalityBridge.getExtractionHints();
    expect(hintsAfter).toEqual(hintsBefore);
  });

  it('handles null/undefined events gracefully', () => {
    expect(() => fireContextEvent(null)).not.toThrow();
    expect(() => fireContextEvent(undefined)).not.toThrow();
    expect(() => fireContextEvent({})).not.toThrow();
  });

  it('destroy unsubscribes from context stream', () => {
    const before = mocks.contextStreamListeners.length;
    memoryPersonalityBridge.destroy();
    expect(mocks.contextStreamListeners.length).toBeLessThan(before);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 12. CLAW COMPLIANCE
// ═══════════════════════════════════════════════════════════════════════

describe('cLaw Compliance', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
    memoryPersonalityBridge.updateConfig({ manipulationCheckIntervalMs: 0 });
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('anti-manipulation is structural, not aspirational', () => {
    memoryPersonalityBridge.updateConfig({ windowSize: 10, maxViolations: 1 });
    expect(() => {
      for (let i = 0; i < 10; i++) {
        memoryPersonalityBridge.recordExchangeObservation(true, false, 3);
      }
    }).toThrow();
  });

  it('proactivity arbitration prevents bombardment', () => {
    memoryPersonalityBridge.proposeProactivity({
      source: 'daily-briefing', priority: 0, reason: 'First', content: 'first', ttlMs: 60_000,
    });
    expect(memoryPersonalityBridge.arbitrateProactivity()).not.toBeNull();

    for (let i = 0; i < 5; i++) {
      memoryPersonalityBridge.proposeProactivity({
        source: 'daily-briefing', priority: 2, reason: `Flood ${i}`, content: `flood-${i}`, ttlMs: 60_000,
      });
      expect(memoryPersonalityBridge.arbitrateProactivity()).toBeNull();
    }
  });

  it('dismissals reduce priority (engagement for helpfulness not addiction)', () => {
    memoryPersonalityBridge.recordEngagement('mem-dis', 'dismissed', 'User dismissed');
    const adjustments = memoryPersonalityBridge.getMemoryPriorityAdjustments();
    expect(adjustments.get('mem-dis')!).toBeLessThan(0);
  });

  it('extraction hints serve user preference, not engagement maximization', () => {
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.9, verbosity: 0.2, humor: 0.5,
      technicalDepth: 0.9, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    memoryPersonalityBridge.recomputeExtractionHints();
    const hints = memoryPersonalityBridge.getExtractionHints();
    expect(hints.preferFormal).toBe(true);
    expect(hints.preferTechnical).toBe(true);
    expect(hints.compactExtraction).toBe(true);
    expect(hints.preferEmotional).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 13. EDGE CASES AND BOUNDARY CONDITIONS
// ═══════════════════════════════════════════════════════════════════════

describe('Edge Cases', () => {
  beforeEach(async () => {
    __test_resetDeps();
    injectMocks();
    mocks.mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mocks.mockPersonalityCalibration.getDimensions.mockReturnValue({
      formality: 0.5, verbosity: 0.5, humor: 0.5,
      technicalDepth: 0.5, emotionalWarmth: 0.5, proactivity: 0.5,
    });
    await memoryPersonalityBridge.initialize();
  });

  afterEach(() => {
    memoryPersonalityBridge.destroy();
    __test_resetDeps();
    vi.clearAllMocks();
    mocks.contextStreamListeners.length = 0;
  });

  it('empty string context in recordEngagement', () => {
    memoryPersonalityBridge.recordEngagement('m1', 'referenced', '');
    expect(memoryPersonalityBridge.getEngagements()[0].context).toBe('');
  });

  it('syncMemoryToPersonality with empty memories does not throw', () => {
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([]);
    expect(() => memoryPersonalityBridge.syncMemoryToPersonality()).not.toThrow();
  });

  it('getMemoryPriorityAdjustments handles empty long-term memories', () => {
    mocks.mockCommitmentTracker.getActiveCommitments.mockReturnValue([
      { description: 'test', personName: 'alice' },
    ]);
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([]);
    expect(memoryPersonalityBridge.getMemoryPriorityAdjustments().size).toBe(0);
  });

  it('proposalQueue handles rapid submissions', () => {
    for (let i = 0; i < 50; i++) {
      memoryPersonalityBridge.proposeProactivity({
        source: 'daily-briefing', priority: 0,
        reason: `Rapid ${i}`, content: `rapid-${i}`, ttlMs: 300_000,
      });
    }
    expect(memoryPersonalityBridge.getPendingProposalCount()).toBeLessThanOrEqual(20);
  });

  it('cooldown remaining returns 0 when never delivered', () => {
    expect(memoryPersonalityBridge.getProactivityCooldownRemaining()).toBe(0);
  });

  it('multiple resets do not corrupt state', async () => {
    await memoryPersonalityBridge.reset();
    await memoryPersonalityBridge.reset();
    await memoryPersonalityBridge.reset();
    const state = memoryPersonalityBridge.getState();
    expect(state.engagements).toHaveLength(0);
    expect(state.manipulation.violations).toBe(0);
  });

  it('word overlap with only stop words does not match', () => {
    // personName must NOT be a substring of the fact, otherwise fact.includes(person) matches.
    // Use 'zznoname' — a name that won't appear as a substring in the fact string.
    mocks.mockCommitmentTracker.getActiveCommitments.mockReturnValue([
      { description: 'the a is are', personName: 'zznoname' },
    ]);
    mocks.mockMemoryManager.getLongTerm.mockReturnValue([
      { id: 'mem-sw', fact: 'a is the are were', category: 'professional' },
    ]);
    expect(memoryPersonalityBridge.getMemoryPriorityAdjustments().has('mem-sw')).toBe(false);
  });

  it('handles concurrent save operations via queue', async () => {
    memoryPersonalityBridge.recordEngagement('m1', 'referenced', 'r1');
    memoryPersonalityBridge.recordEngagement('m2', 'corrected', 'r2');
    memoryPersonalityBridge.recordEngagement('m3', 'asked_about', 'r3');
    memoryPersonalityBridge.updateConfig({ proactivityCooldownMs: 5000 });
    await new Promise((r) => setTimeout(r, 100));
    expect(mocks.mockWriteFile).toHaveBeenCalled();
  });
});
