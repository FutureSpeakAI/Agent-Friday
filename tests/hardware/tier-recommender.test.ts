/**
 * TierRecommender -- Unit tests for hardware-to-tier recommendation mapping.
 *
 * Tests pure functions that map a HardwareProfile to a tier recommendation,
 * including tier assignment by VRAM, model lists, VRAM estimation,
 * model fit checking, disk space validation, upgrade path information,
 * CPU-only model handling, model categories, and utility lookups.
 *
 * No mocks needed -- all functions are pure (stateless input -> output).
 *
 * Sprint 6 O.2 + Track A Phase 3: "The Measure" -- TierRecommender
 */

import { describe, it, expect } from 'vitest';
import type { HardwareProfile } from '../../../src/main/hardware/hardware-profiler';
import {
  recommend,
  getTier,
  getModelList,
  getModelsByCategory,
  estimateVRAMUsage,
  estimateDiskUsage,
  canFitModel,
  getUpgradePath,
  getModelInfo,
  getAllModelNames,
  type TierName,
  type ModelRequirement,
  type ModelCategory,
  type TierRecommendation,
  type UpgradePath,
} from '../../../src/main/hardware/tier-recommender';

// -- Helpers ----------------------------------------------------------------

const GB = 1024 * 1024 * 1024;
const MB = 1024 * 1024;

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
  // =========================================================================
  //  TIER ASSIGNMENT BY VRAM
  // =========================================================================

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

  // VC-2: 4 GB VRAM -> Light tier (embeddings + CPU-only TTS/STT)
  it('assigns light tier when VRAM is 4 GB', () => {
    const profile = makeProfile({ vramAvailable: 4 * GB });
    const tier = getTier(profile);
    expect(tier).toBe('light');

    const rec = recommend(profile);
    expect(rec.tier).toBe('light');
    expect(rec.models.length).toBeGreaterThan(0);
    const names = rec.models.map((m) => m.name);
    // GPU models
    expect(names).toContain('nomic-embed-text');
    // CPU-only models
    expect(names).toContain('piper-en-us-lessac-medium');
    expect(names).toContain('whisper-ggml-tiny');
    // Should NOT include the 8B LLM (needs >= 6 GB for standard)
    expect(names).not.toContain('llama3.1:8b-instruct-q4_K_M');
  });

  // VC-3: 7 GB VRAM -> Standard tier (embeddings + 8B LLM + TTS/STT)
  it('assigns standard tier when VRAM is 7 GB', () => {
    const profile = makeProfile({ vramAvailable: 7 * GB });
    const tier = getTier(profile);
    expect(tier).toBe('standard');

    const rec = recommend(profile);
    expect(rec.tier).toBe('standard');
    const names = rec.models.map((m) => m.name);
    expect(names).toContain('nomic-embed-text');
    expect(names).toContain('llama3.1:8b-instruct-q4_K_M');
    expect(names).toContain('piper-en-us-lessac-medium');
    expect(names).toContain('whisper-ggml-base');
    expect(names).not.toContain('moondream:latest');
  });

  // VC-4: 8 GB VRAM -> Full tier (embeddings + 8B LLM + vision + TTS/STT + diffusion)
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
    expect(names).toContain('kokoro-v1.0');
    expect(names).toContain('whisper-ggml-small');
    expect(names).toContain('sd-1.5-q8');
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
    expect(names).toContain('kokoro-v1.0');
    expect(names).toContain('whisper-ggml-medium');
    expect(names).toContain('sdxl-turbo-q4');

    // Upgrade path should be null (top tier)
    expect(rec.upgradePath).toBeNull();
  });

  // =========================================================================
  //  MODEL LIST PER TIER
  // =========================================================================

  // VC-6: getModelList(tier) returns specific model names and download sizes
  it('getModelList returns correct model counts and names for each tier', () => {
    // whisper has no models
    const whisperModels = getModelList('whisper');
    expect(whisperModels).toHaveLength(0);

    // light: nomic-embed-text + piper TTS + whisper-tiny STT = 3
    const lightModels = getModelList('light');
    expect(lightModels).toHaveLength(3);
    const lightNames = lightModels.map((m) => m.name);
    expect(lightNames).toContain('nomic-embed-text');
    expect(lightNames).toContain('piper-en-us-lessac-medium');
    expect(lightNames).toContain('whisper-ggml-tiny');

    // standard: nomic + llama8b + piper + whisper-base = 4
    const standardModels = getModelList('standard');
    expect(standardModels).toHaveLength(4);
    const standardNames = standardModels.map((m) => m.name);
    expect(standardNames).toContain('nomic-embed-text');
    expect(standardNames).toContain('llama3.1:8b-instruct-q4_K_M');
    expect(standardNames).toContain('piper-en-us-lessac-medium');
    expect(standardNames).toContain('whisper-ggml-base');

    // full: nomic + llama8b + moondream + kokoro + whisper-small + sd-1.5 = 6
    const fullModels = getModelList('full');
    expect(fullModels).toHaveLength(6);
    const fullNames = fullModels.map((m) => m.name);
    expect(fullNames).toContain('kokoro-v1.0');
    expect(fullNames).toContain('whisper-ggml-small');
    expect(fullNames).toContain('sd-1.5-q8');

    // sovereign: nomic + llama8b + moondream + llama70b + kokoro + whisper-medium + sdxl = 7
    const sovereignModels = getModelList('sovereign');
    expect(sovereignModels).toHaveLength(7);
    const sovNames = sovereignModels.map((m) => m.name);
    expect(sovNames).toContain('llama3.1:70b-instruct-q4_K_M');
    expect(sovNames).toContain('whisper-ggml-medium');
    expect(sovNames).toContain('sdxl-turbo-q4');
  });

  // =========================================================================
  //  VRAM ESTIMATION (CPU-ONLY EXCLUSION)
  // =========================================================================

  // VC-7: estimateVRAMUsage sums model VRAM requirements accurately
  it('estimateVRAMUsage sums VRAM bytes excluding CPU-only models', () => {
    // Standard tier: nomic (0.5) + llama8b (5.5) + piper (0, cpuOnly) + whisper-base (0, cpuOnly) = 6 GB
    const standardModels = getModelList('standard');
    const standardVRAM = estimateVRAMUsage(standardModels);
    expect(standardVRAM).toBe(0.5 * GB + 5.5 * GB);

    // Empty list should return 0
    expect(estimateVRAMUsage([])).toBe(0);

    // Full tier: nomic (0.5) + llama8b (5.5) + moondream (1.2) + sd-1.5 (4)
    //   + kokoro (0, cpuOnly) + whisper-small (0, cpuOnly) = 11.2 GB
    const fullModels = getModelList('full');
    expect(estimateVRAMUsage(fullModels)).toBe(
      0.5 * GB + 5.5 * GB + 1.2 * GB + 4 * GB,
    );

    // Light tier: nomic (0.5) + piper (0, cpuOnly) + whisper-tiny (0, cpuOnly) = 0.5 GB
    const lightModels = getModelList('light');
    expect(estimateVRAMUsage(lightModels)).toBe(0.5 * GB);
  });

  // =========================================================================
  //  DISK ESTIMATION (ALL MODELS INCLUDED)
  // =========================================================================

  it('estimateDiskUsage sums disk bytes for all models including CPU-only', () => {
    // Light tier: nomic (0.3GB) + piper (63MB) + whisper-tiny (75MB)
    const lightModels = getModelList('light');
    const lightDisk = estimateDiskUsage(lightModels);
    expect(lightDisk).toBe(0.3 * GB + 63 * MB + 75 * MB);

    // Empty list should return 0
    expect(estimateDiskUsage([])).toBe(0);

    // Standard tier: nomic (0.3GB) + llama8b (4.7GB) + piper (63MB) + whisper-base (142MB)
    const standardModels = getModelList('standard');
    expect(estimateDiskUsage(standardModels)).toBe(
      0.3 * GB + 4.7 * GB + 63 * MB + 142 * MB,
    );
  });

  // =========================================================================
  //  MODEL FIT CHECK
  // =========================================================================

  // VC-8: canFitModel checks if adding a model exceeds VRAM budget
  it('canFitModel returns true/false based on VRAM budget', () => {
    const profile = makeProfile({ vramAvailable: 6 * GB });

    // nomic-embed-text (0.5 GB) should fit alone
    const embed = getModelList('light').find(
      (m) => m.name === 'nomic-embed-text',
    )!;
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

  // CPU-only models always fit regardless of VRAM budget
  it('canFitModel always returns true for CPU-only models', () => {
    const tinyProfile = makeProfile({ vramAvailable: 0 });

    // Even with 0 VRAM, CPU-only models should fit
    const piperModel = getModelInfo('piper-en-us-lessac-medium')!;
    expect(piperModel.cpuOnly).toBe(true);
    expect(canFitModel(piperModel, tinyProfile)).toBe(true);

    const whisperModel = getModelInfo('whisper-ggml-tiny')!;
    expect(whisperModel.cpuOnly).toBe(true);
    expect(canFitModel(whisperModel, tinyProfile)).toBe(true);
  });

  // =========================================================================
  //  DISK SPACE VALIDATION
  // =========================================================================

  // VC-9: Recommendation includes disk space check
  it('recommendation flags insufficient disk space with warning', () => {
    // 100 GB free -> full tier models need disk -> should be fine
    const plenty = makeProfile({ vramAvailable: 8 * GB, diskFree: 100 * GB });
    const recGood = recommend(plenty);
    expect(recGood.diskSufficient).toBe(true);
    expect(recGood.warnings).not.toContain(
      expect.stringContaining('disk'),
    );

    // 1 GB free -> full tier models need substantial disk -> insufficient
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

  // =========================================================================
  //  UPGRADE PATHS
  // =========================================================================

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

  it('upgrade paths mention TTS/STT and diffusion unlocks', () => {
    const whisperUp = getUpgradePath('whisper');
    const hasVoiceUnlock = whisperUp!.unlocks.some(
      (u) => u.toLowerCase().includes('tts') || u.toLowerCase().includes('voice'),
    );
    expect(hasVoiceUnlock).toBe(true);

    const standardUp = getUpgradePath('standard');
    const hasDiffusionUnlock = standardUp!.unlocks.some(
      (u) => u.toLowerCase().includes('image'),
    );
    expect(hasDiffusionUnlock).toBe(true);
  });

  // =========================================================================
  //  MODEL CATEGORIES
  // =========================================================================

  it('getModelsByCategory filters models by category', () => {
    // Full tier has TTS models
    const fullTTS = getModelsByCategory('full', 'tts');
    expect(fullTTS.length).toBeGreaterThan(0);
    expect(fullTTS.every((m) => m.category === 'tts')).toBe(true);
    expect(fullTTS.some((m) => m.name === 'kokoro-v1.0')).toBe(true);

    // Full tier has STT models
    const fullSTT = getModelsByCategory('full', 'stt');
    expect(fullSTT.length).toBeGreaterThan(0);
    expect(fullSTT.every((m) => m.category === 'stt')).toBe(true);

    // Full tier has diffusion models
    const fullDiffusion = getModelsByCategory('full', 'diffusion');
    expect(fullDiffusion.length).toBeGreaterThan(0);
    expect(fullDiffusion.every((m) => m.category === 'diffusion')).toBe(true);

    // Light tier has embedding but no LLM
    const lightEmbedding = getModelsByCategory('light', 'embedding');
    expect(lightEmbedding.length).toBe(1);
    expect(lightEmbedding[0].name).toBe('nomic-embed-text');

    const lightLLM = getModelsByCategory('light', 'llm');
    expect(lightLLM.length).toBe(0);

    // Whisper tier has nothing
    const whisperTTS = getModelsByCategory('whisper', 'tts');
    expect(whisperTTS.length).toBe(0);
  });

  // =========================================================================
  //  CPU-ONLY MODEL FLAGS
  // =========================================================================

  it('TTS and STT models have cpuOnly flag set to true', () => {
    const allModels = getAllModelNames();
    for (const name of allModels) {
      const model = getModelInfo(name)!;
      if (model.category === 'tts' || model.category === 'stt') {
        expect(model.cpuOnly).toBe(true);
        expect(model.vramBytes).toBe(0);
      }
    }
  });

  it('LLM, embedding, vision, and diffusion models are not cpuOnly', () => {
    const gpuCategories: ModelCategory[] = ['llm', 'embedding', 'vision', 'diffusion'];
    const allModels = getAllModelNames();
    for (const name of allModels) {
      const model = getModelInfo(name)!;
      if (gpuCategories.includes(model.category!)) {
        expect(model.cpuOnly).toBeFalsy();
        expect(model.vramBytes).toBeGreaterThan(0);
      }
    }
  });

  // =========================================================================
  //  MODEL REGISTRY LOOKUPS
  // =========================================================================

  it('getModelInfo returns model details or undefined', () => {
    const nomic = getModelInfo('nomic-embed-text');
    expect(nomic).toBeDefined();
    expect(nomic!.name).toBe('nomic-embed-text');
    expect(nomic!.category).toBe('embedding');
    expect(nomic!.vramBytes).toBe(0.5 * GB);
    expect(nomic!.diskBytes).toBe(0.3 * GB);

    const piper = getModelInfo('piper-en-us-lessac-medium');
    expect(piper).toBeDefined();
    expect(piper!.category).toBe('tts');
    expect(piper!.cpuOnly).toBe(true);
    expect(piper!.diskBytes).toBe(63 * MB);

    const sdxl = getModelInfo('sdxl-turbo-q4');
    expect(sdxl).toBeDefined();
    expect(sdxl!.category).toBe('diffusion');
    expect(sdxl!.vramBytes).toBe(6 * GB);

    // Non-existent model
    const missing = getModelInfo('nonexistent-model');
    expect(missing).toBeUndefined();
  });

  it('getAllModelNames returns all registered models', () => {
    const names = getAllModelNames();
    expect(names.length).toBeGreaterThanOrEqual(12); // At least 12 models registered
    expect(names).toContain('nomic-embed-text');
    expect(names).toContain('llama3.1:8b-instruct-q4_K_M');
    expect(names).toContain('piper-en-us-lessac-medium');
    expect(names).toContain('kokoro-v1.0');
    expect(names).toContain('whisper-ggml-tiny');
    expect(names).toContain('whisper-ggml-base');
    expect(names).toContain('whisper-ggml-small');
    expect(names).toContain('whisper-ggml-medium');
    expect(names).toContain('sd-1.5-q8');
    expect(names).toContain('sdxl-turbo-q4');
  });

  // =========================================================================
  //  VRAM HEADROOM
  // =========================================================================

  it('recommendation includes correct vramHeadroom', () => {
    // 4 GB VRAM → light tier → nomic (0.5GB VRAM) → headroom = 3.5 GB
    const lightRec = recommend(makeProfile({ vramAvailable: 4 * GB }));
    expect(lightRec.vramHeadroom).toBe(4 * GB - 0.5 * GB);

    // 7 GB VRAM → standard tier → nomic+llama8b (6GB VRAM) → headroom = 1 GB
    const stdRec = recommend(makeProfile({ vramAvailable: 7 * GB }));
    expect(stdRec.vramHeadroom).toBe(7 * GB - 6 * GB);

    // 24 GB → sovereign → all GPU models → check headroom is positive (enough VRAM)
    const sovRec = recommend(makeProfile({ vramAvailable: 24 * GB }));
    // Sovereign total GPU VRAM: 0.5 + 5.5 + 1.2 + 40 + 6 = 53.2 GB
    // With only 24 GB available, headroom should be negative
    expect(sovRec.vramHeadroom).toBeLessThan(0);
    // Should have a VRAM warning
    const hasVRAMWarning = sovRec.warnings.some((w) =>
      w.toLowerCase().includes('vram'),
    );
    expect(hasVRAMWarning).toBe(true);
  });
});
