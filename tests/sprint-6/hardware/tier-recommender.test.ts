/**
 * TierRecommender -- Unit tests for hardware-to-tier recommendation mapping.
 *
 * Tests pure functions that map a HardwareProfile to a tier recommendation,
 * including tier assignment by VRAM, model lists, VRAM estimation,
 * model fit checking, disk space validation, and upgrade path information.
 *
 * No mocks needed -- all functions are pure (stateless input -> output).
 *
 * Sprint 6 O.2: "The Measure" -- TierRecommender
 */

import { describe, it, expect } from 'vitest';
import type { HardwareProfile } from '../../../src/main/hardware/hardware-profiler';
import {
  recommend,
  getTier,
  getModelList,
  estimateVRAMUsage,
  canFitModel,
  getUpgradePath,
  type TierName,
  type ModelRequirement,
  type TierRecommendation,
  type UpgradePath,
} from '../../../src/main/hardware/tier-recommender';

// -- Helpers ----------------------------------------------------------------

const GB = 1024 * 1024 * 1024;

/** Create a mock HardwareProfile with specified VRAM and disk space. */
function makeProfile(opts: {
  vramAvailable?: number;
  diskFree?: number;
}): HardwareProfile {
  return {
    gpu: {
      name: 'Test GPU',
      vendor: opts.vramAvailable ? 'nvidia' : 'unknown',
      driver: '551.61',
      available: (opts.vramAvailable ?? 0) > 0,
    },
    vram: {
      total: (opts.vramAvailable ?? 0) + 1.5 * GB,
      available: opts.vramAvailable ?? 0,
      systemReserved: 1.5 * GB,
    },
    ram: { total: 32 * GB, available: 16 * GB },
    cpu: { model: 'Test CPU', cores: 8, threads: 16 },
    disk: {
      modelStoragePath: '/tmp/models',
      totalSpace: 500 * GB,
      freeSpace: opts.diskFree ?? 100 * GB,
    },
    detectedAt: Date.now(),
  };
}

// -- Test Suite --------------------------------------------------------------

describe('TierRecommender', () => {
  // VC-1: 0 VRAM -> Whisper tier (CPU-only, cloud LLM)
  it('assigns whisper tier when VRAM is 0', () => {
    const profile = makeProfile({ vramAvailable: 0 });
    const tier = getTier(profile);
    expect(tier).toBe('whisper');

    const rec = recommend(profile);
    expect(rec.tier).toBe('whisper');
    expect(rec.models).toHaveLength(0);
    expect(rec.totalVRAM).toBe(0);
    expect(rec.totalDisk).toBe(0);
  });

  // VC-2: 4 GB VRAM -> Light tier (embeddings only)
  it('assigns light tier when VRAM is 4 GB', () => {
    const profile = makeProfile({ vramAvailable: 4 * GB });
    const tier = getTier(profile);
    expect(tier).toBe('light');

    const rec = recommend(profile);
    expect(rec.tier).toBe('light');
    expect(rec.models.length).toBeGreaterThan(0);
    // Should include embeddings model
    const names = rec.models.map((m) => m.name);
    expect(names).toContain('nomic-embed-text');
    // Should NOT include the 8B LLM (needs >= 6 GB for standard)
    expect(names).not.toContain('llama3.1:8b-instruct-q4_K_M');
  });

  // VC-3: 7 GB VRAM -> Standard tier (embeddings + 8B LLM)
  it('assigns standard tier when VRAM is 7 GB', () => {
    const profile = makeProfile({ vramAvailable: 7 * GB });
    const tier = getTier(profile);
    expect(tier).toBe('standard');

    const rec = recommend(profile);
    expect(rec.tier).toBe('standard');
    const names = rec.models.map((m) => m.name);
    expect(names).toContain('nomic-embed-text');
    expect(names).toContain('llama3.1:8b-instruct-q4_K_M');
    expect(names).not.toContain('moondream:latest');
  });

  // VC-4: 8 GB VRAM -> Full tier (embeddings + 8B LLM + vision)
  it('assigns full tier when VRAM is 8 GB or more', () => {
    const profile = makeProfile({ vramAvailable: 8 * GB });
    const tier = getTier(profile);
    expect(tier).toBe('full');

    const rec = recommend(profile);
    expect(rec.tier).toBe('full');
    const names = rec.models.map((m) => m.name);
    expect(names).toContain('nomic-embed-text');
    expect(names).toContain('llama3.1:8b-instruct-q4_K_M');
    expect(names).toContain('moondream:latest');
    expect(names).not.toContain('llama3.1:70b-instruct-q4_K_M');
  });

  // VC-5: 24+ GB VRAM -> Sovereign tier (all + larger models)
  it('assigns sovereign tier when VRAM is 24 GB or more', () => {
    const profile = makeProfile({ vramAvailable: 24 * GB });
    const tier = getTier(profile);
    expect(tier).toBe('sovereign');

    const rec = recommend(profile);
    expect(rec.tier).toBe('sovereign');
    const names = rec.models.map((m) => m.name);
    expect(names).toContain('nomic-embed-text');
    expect(names).toContain('llama3.1:8b-instruct-q4_K_M');
    expect(names).toContain('moondream:latest');
    expect(names).toContain('llama3.1:70b-instruct-q4_K_M');

    // Upgrade path should be null (top tier)
    expect(rec.upgradePath).toBeNull();
  });

  // VC-6: getModelList(tier) returns specific model names and download sizes
  it('getModelList returns correct models with disk sizes for each tier', () => {
    // whisper has no models
    const whisperModels = getModelList('whisper');
    expect(whisperModels).toHaveLength(0);

    // light has 1 model (nomic-embed-text)
    const lightModels = getModelList('light');
    expect(lightModels).toHaveLength(1);
    expect(lightModels[0].name).toBe('nomic-embed-text');
    expect(lightModels[0].diskBytes).toBe(0.3 * GB);

    // standard has 2 models
    const standardModels = getModelList('standard');
    expect(standardModels).toHaveLength(2);
    const standardNames = standardModels.map((m) => m.name);
    expect(standardNames).toContain('nomic-embed-text');
    expect(standardNames).toContain('llama3.1:8b-instruct-q4_K_M');

    // full has 3 models
    const fullModels = getModelList('full');
    expect(fullModels).toHaveLength(3);

    // sovereign has 4 models
    const sovereignModels = getModelList('sovereign');
    expect(sovereignModels).toHaveLength(4);
    const sovNames = sovereignModels.map((m) => m.name);
    expect(sovNames).toContain('llama3.1:70b-instruct-q4_K_M');
  });

  // VC-7: estimateVRAMUsage sums model VRAM requirements accurately
  it('estimateVRAMUsage sums VRAM bytes from all provided models', () => {
    const models = getModelList('standard');
    const totalVRAM = estimateVRAMUsage(models);
    // nomic-embed-text (0.5 GB) + llama3.1:8b (5.5 GB) = 6 GB
    expect(totalVRAM).toBe(0.5 * GB + 5.5 * GB);

    // Empty list should return 0
    expect(estimateVRAMUsage([])).toBe(0);

    // Full tier: 0.5 + 5.5 + 1.2 = 7.2 GB
    const fullModels = getModelList('full');
    expect(estimateVRAMUsage(fullModels)).toBe(0.5 * GB + 5.5 * GB + 1.2 * GB);
  });

  // VC-8: canFitModel checks if adding a model exceeds VRAM budget
  it('canFitModel returns true/false based on VRAM budget', () => {
    const profile = makeProfile({ vramAvailable: 6 * GB });

    // nomic-embed-text (0.5 GB) should fit alone
    const embed = getModelList('light')[0];
    expect(canFitModel(embed, profile)).toBe(true);

    // llama3.1:8b (5.5 GB) should fit alone
    const llm = getModelList('standard').find(
      (m) => m.name === 'llama3.1:8b-instruct-q4_K_M',
    )!;
    expect(canFitModel(llm, profile)).toBe(true);

    // But both together (6 GB total) should still fit exactly
    expect(canFitModel(llm, profile, [embed])).toBe(true);

    // Add moondream (1.2 GB) on top of both -- 7.2 GB > 6 GB, should NOT fit
    const vision = getModelList('full').find(
      (m) => m.name === 'moondream:latest',
    )!;
    expect(canFitModel(vision, profile, [embed, llm])).toBe(false);
  });

  // VC-9: Recommendation includes disk space check
  it('recommendation flags insufficient disk space with warning', () => {
    // 100 GB free -> standard tier models need ~5 GB disk -> should be fine
    const plenty = makeProfile({ vramAvailable: 8 * GB, diskFree: 100 * GB });
    const recGood = recommend(plenty);
    expect(recGood.diskSufficient).toBe(true);
    expect(recGood.warnings).not.toContain(
      expect.stringContaining('disk'),
    );

    // 1 GB free -> standard tier models need ~5 GB disk -> insufficient
    const tight = makeProfile({ vramAvailable: 8 * GB, diskFree: 1 * GB });
    const recBad = recommend(tight);
    expect(recBad.diskSufficient).toBe(false);
    expect(recBad.warnings.length).toBeGreaterThan(0);
    // At least one warning should mention disk
    const hasDiskWarning = recBad.warnings.some((w) =>
      w.toLowerCase().includes('disk'),
    );
    expect(hasDiskWarning).toBe(true);
  });

  // VC-10: getUpgradePath describes what the next tier unlocks
  it('getUpgradePath returns next tier info or null for sovereign', () => {
    // whisper -> light
    const whisperUp = getUpgradePath('whisper');
    expect(whisperUp).not.toBeNull();
    expect(whisperUp!.nextTier).toBe('light');
    expect(whisperUp!.requiredVRAM).toBeGreaterThan(0);
    expect(whisperUp!.requiredDisk).toBeGreaterThan(0);
    expect(whisperUp!.unlocks.length).toBeGreaterThan(0);

    // light -> standard
    const lightUp = getUpgradePath('light');
    expect(lightUp).not.toBeNull();
    expect(lightUp!.nextTier).toBe('standard');

    // standard -> full
    const standardUp = getUpgradePath('standard');
    expect(standardUp).not.toBeNull();
    expect(standardUp!.nextTier).toBe('full');

    // full -> sovereign
    const fullUp = getUpgradePath('full');
    expect(fullUp).not.toBeNull();
    expect(fullUp!.nextTier).toBe('sovereign');

    // sovereign -> null (top tier)
    const sovereignUp = getUpgradePath('sovereign');
    expect(sovereignUp).toBeNull();
  });
});
