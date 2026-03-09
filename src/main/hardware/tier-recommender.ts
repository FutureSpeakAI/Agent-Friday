/**
 * tier-recommender.ts -- Pure functions that map a HardwareProfile to a tier.
 *
 * Stateless input -> output. No singleton, no events, no side effects.
 * Consumes the HardwareProfile produced by HardwareProfiler (O.1) and
 * recommends a tier with model requirements, disk checks, and upgrade paths.
 *
 * Sprint 6 O.2 + ForgeMap Track A Phase 3: "The Measure" — TierRecommender
 *
 * MODEL CATEGORIES:
 *   - LLM:       Language models (Llama, etc.)
 *   - Embedding:  Embedding models (nomic, etc.)
 *   - Vision:     Vision-language models (Moondream, etc.)
 *   - TTS:        Text-to-speech (Kokoro, Piper)        ← NEW
 *   - STT:        Speech-to-text (Whisper)               ← NEW
 *   - Diffusion:  Image generation (Stable Diffusion)    ← NEW (reserved)
 *   - Audio:      Audio/music generation (AudioCraft)    ← NEW (reserved)
 */

import type { HardwareProfile } from './hardware-profiler';

// -- Constants ---------------------------------------------------------------

const GB = 1024 * 1024 * 1024;
const MB = 1024 * 1024;

// -- Contract Types ----------------------------------------------------------

export type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

export type ModelCategory =
  | 'llm'
  | 'embedding'
  | 'vision'
  | 'tts'
  | 'stt'
  | 'diffusion'
  | 'audio';

export interface TierRecommendation {
  tier: TierName;
  models: ModelRequirement[];
  totalVRAM: number;        // bytes needed for all models
  totalDisk: number;        // bytes needed for all downloads
  diskSufficient: boolean;
  vramHeadroom: number;     // bytes remaining after models
  upgradePath: UpgradePath | null;
  warnings: string[];       // e.g., "Low disk space"
}

export interface ModelRequirement {
  name: string;              // e.g., "nomic-embed-text"
  vramBytes: number;
  diskBytes: number;
  purpose: string;           // e.g., "embeddings"
  required: boolean;         // false = optional for this tier
  category?: ModelCategory;  // model category for filtering
  cpuOnly?: boolean;         // true = runs on CPU, not counted in VRAM budget
}

export interface UpgradePath {
  nextTier: TierName;
  requiredVRAM: number;      // bytes needed for next tier
  requiredDisk: number;      // bytes needed for next tier downloads
  unlocks: string[];         // e.g., ["Local 8B LLM", "Offline chat"]
}

// -- Model Registry ----------------------------------------------------------
//
// All models the system can recommend. Each has VRAM/disk requirements and
// a category for filtering. CPU-only models (TTS, STT) have cpuOnly=true
// and vramBytes=0 so they don't affect GPU-based tier calculations.
//

const MODEL_REGISTRY: Record<string, ModelRequirement> = {
  // -- Embedding models ------------------------------------------------------
  'nomic-embed-text': {
    name: 'nomic-embed-text',
    vramBytes: 0.5 * GB,
    diskBytes: 0.3 * GB,
    purpose: 'Text embeddings for semantic search',
    required: true,
    category: 'embedding',
  },

  // -- Language models -------------------------------------------------------
  'llama3.1:8b-instruct-q4_K_M': {
    name: 'llama3.1:8b-instruct-q4_K_M',
    vramBytes: 5.5 * GB,
    diskBytes: 4.7 * GB,
    purpose: 'Local language model for chat and reasoning',
    required: true,
    category: 'llm',
  },
  'llama3.1:70b-instruct-q4_K_M': {
    name: 'llama3.1:70b-instruct-q4_K_M',
    vramBytes: 40 * GB,
    diskBytes: 40 * GB,
    purpose: 'Large language model for complex reasoning',
    required: false,
    category: 'llm',
  },

  // -- Vision models ---------------------------------------------------------
  'moondream:latest': {
    name: 'moondream:latest',
    vramBytes: 1.2 * GB,
    diskBytes: 0.8 * GB,
    purpose: 'Vision-language model for image understanding',
    required: true,
    category: 'vision',
  },

  // -- TTS models (CPU-only) ------------------------------------------------
  'piper-en-us-lessac-medium': {
    name: 'piper-en-us-lessac-medium',
    vramBytes: 0,
    diskBytes: 63 * MB,
    purpose: 'Local text-to-speech (English, medium quality)',
    required: true,
    category: 'tts',
    cpuOnly: true,
  },
  'kokoro-v1.0': {
    name: 'kokoro-v1.0',
    vramBytes: 0,
    diskBytes: 350 * MB,
    purpose: 'Local text-to-speech (multilingual, high quality)',
    required: false,
    category: 'tts',
    cpuOnly: true,
  },

  // -- STT models (CPU-only) ------------------------------------------------
  'whisper-ggml-tiny': {
    name: 'whisper-ggml-tiny',
    vramBytes: 0,
    diskBytes: 75 * MB,
    purpose: 'Local speech-to-text (fast, lower accuracy)',
    required: true,
    category: 'stt',
    cpuOnly: true,
  },
  'whisper-ggml-base': {
    name: 'whisper-ggml-base',
    vramBytes: 0,
    diskBytes: 142 * MB,
    purpose: 'Local speech-to-text (balanced speed/accuracy)',
    required: false,
    category: 'stt',
    cpuOnly: true,
  },
  'whisper-ggml-small': {
    name: 'whisper-ggml-small',
    vramBytes: 0,
    diskBytes: 466 * MB,
    purpose: 'Local speech-to-text (good accuracy)',
    required: false,
    category: 'stt',
    cpuOnly: true,
  },
  'whisper-ggml-medium': {
    name: 'whisper-ggml-medium',
    vramBytes: 0,
    diskBytes: 1.5 * GB,
    purpose: 'Local speech-to-text (high accuracy)',
    required: false,
    category: 'stt',
    cpuOnly: true,
  },

  // -- Diffusion models (reserved for Track B) -------------------------------
  'sd-1.5-q8': {
    name: 'sd-1.5-q8',
    vramBytes: 4 * GB,
    diskBytes: 2 * GB,
    purpose: 'Local image generation (Stable Diffusion 1.5)',
    required: false,
    category: 'diffusion',
  },
  'sdxl-turbo-q4': {
    name: 'sdxl-turbo-q4',
    vramBytes: 6 * GB,
    diskBytes: 3.5 * GB,
    purpose: 'Local image generation (SDXL Turbo, fast)',
    required: false,
    category: 'diffusion',
  },
};

// -- Tier -> Model Mapping ---------------------------------------------------
//
// Each tier lists the models it requires. TTS/STT models are included at
// all tiers >= light (they're CPU-only, so no VRAM impact). Diffusion
// models require "full" tier or above.
//

const TIER_MODELS: Record<TierName, string[]> = {
  whisper: [],
  light: [
    'nomic-embed-text',
    'piper-en-us-lessac-medium',   // CPU-only TTS
    'whisper-ggml-tiny',           // CPU-only STT
  ],
  standard: [
    'nomic-embed-text',
    'llama3.1:8b-instruct-q4_K_M',
    'piper-en-us-lessac-medium',
    'whisper-ggml-base',           // Upgraded STT for standard tier
  ],
  full: [
    'nomic-embed-text',
    'llama3.1:8b-instruct-q4_K_M',
    'moondream:latest',
    'kokoro-v1.0',                 // Premium TTS at full tier
    'whisper-ggml-small',          // Better STT at full tier
    'sd-1.5-q8',                   // Image gen at full tier
  ],
  sovereign: [
    'nomic-embed-text',
    'llama3.1:8b-instruct-q4_K_M',
    'moondream:latest',
    'llama3.1:70b-instruct-q4_K_M',
    'kokoro-v1.0',
    'whisper-ggml-medium',         // Best local STT
    'sdxl-turbo-q4',               // Best local image gen
  ],
};

// -- Tier VRAM Thresholds (descending order for matching) --------------------

const TIER_THRESHOLDS: { tier: TierName; minVRAM: number }[] = [
  { tier: 'sovereign', minVRAM: 16 * GB },
  { tier: 'full',      minVRAM: 8 * GB },
  { tier: 'standard',  minVRAM: 6 * GB },
  { tier: 'light',     minVRAM: 2 * GB },
  { tier: 'whisper',   minVRAM: 0 },
];

// -- Upgrade Path Data -------------------------------------------------------

const UPGRADE_PATHS: Record<TierName, UpgradePath | null> = {
  whisper: {
    nextTier: 'light',
    requiredVRAM: 2 * GB,
    requiredDisk: 0.3 * GB + 63 * MB + 75 * MB,
    unlocks: ['Local embeddings', 'Semantic search', 'Local TTS/STT voice'],
  },
  light: {
    nextTier: 'standard',
    requiredVRAM: 6 * GB,
    requiredDisk: 0.3 * GB + 4.7 * GB + 63 * MB + 142 * MB,
    unlocks: ['Local 8B LLM', 'Offline chat', 'Better speech recognition'],
  },
  standard: {
    nextTier: 'full',
    requiredVRAM: 8 * GB,
    requiredDisk: 0.3 * GB + 4.7 * GB + 0.8 * GB + 350 * MB + 466 * MB + 2 * GB,
    unlocks: [
      'Vision model',
      'Image understanding',
      'Multilingual TTS (Kokoro)',
      'Local image generation',
    ],
  },
  full: {
    nextTier: 'sovereign',
    requiredVRAM: 16 * GB,
    requiredDisk: 0.3 * GB + 4.7 * GB + 0.8 * GB + 40 * GB + 350 * MB + 1.5 * GB + 3.5 * GB,
    unlocks: [
      'Larger LLM models',
      'Better quality reasoning',
      'Best local STT accuracy',
      'SDXL Turbo image generation',
    ],
  },
  sovereign: null,
};

// -- Public API (pure functions) ---------------------------------------------

/**
 * Determine the tier name based on available VRAM.
 * Uses profile.vram.available (systemReserved already subtracted by profiler).
 */
export function getTier(profile: HardwareProfile): TierName {
  const availableVRAM = profile.vram.available;
  for (const { tier, minVRAM } of TIER_THRESHOLDS) {
    if (availableVRAM >= minVRAM) {
      return tier;
    }
  }
  return 'whisper';
}

/**
 * Return the list of ModelRequirements for a given tier.
 */
export function getModelList(tier: TierName): ModelRequirement[] {
  const modelNames = TIER_MODELS[tier];
  return modelNames.map((name) => MODEL_REGISTRY[name]);
}

/**
 * Return models for a tier filtered by category.
 */
export function getModelsByCategory(
  tier: TierName,
  category: ModelCategory,
): ModelRequirement[] {
  return getModelList(tier).filter((m) => m.category === category);
}

/**
 * Sum the VRAM bytes required by an array of models.
 * CPU-only models (cpuOnly=true) are excluded from VRAM calculations.
 */
export function estimateVRAMUsage(models: ModelRequirement[]): number {
  return models
    .filter((m) => !m.cpuOnly)
    .reduce((sum, m) => sum + m.vramBytes, 0);
}

/**
 * Sum the disk bytes required by an array of models (all models, including CPU-only).
 */
export function estimateDiskUsage(models: ModelRequirement[]): number {
  return models.reduce((sum, m) => sum + m.diskBytes, 0);
}

/**
 * Check whether a model fits within the profile's VRAM budget,
 * accounting for already-loaded models.
 * CPU-only models always fit (they don't use VRAM).
 */
export function canFitModel(
  model: ModelRequirement,
  profile: HardwareProfile,
  loaded: ModelRequirement[] = [],
): boolean {
  if (model.cpuOnly) return true;
  const usedVRAM = estimateVRAMUsage(loaded);
  return model.vramBytes + usedVRAM <= profile.vram.available;
}

/**
 * Get the upgrade path for a tier, or null if already at top tier.
 */
export function getUpgradePath(tier: TierName): UpgradePath | null {
  return UPGRADE_PATHS[tier];
}

/**
 * Full tier recommendation: tier, models, disk check, warnings, upgrade path.
 */
export function recommend(profile: HardwareProfile): TierRecommendation {
  const tier = getTier(profile);
  const models = getModelList(tier);
  const totalVRAM = estimateVRAMUsage(models);
  const totalDisk = estimateDiskUsage(models);
  const diskSufficient = profile.disk.freeSpace >= totalDisk;
  const vramHeadroom = profile.vram.available - totalVRAM;
  const upgradePath = getUpgradePath(tier);

  const warnings: string[] = [];

  if (!diskSufficient) {
    const neededGB = (totalDisk / GB).toFixed(1);
    const freeGB = (profile.disk.freeSpace / GB).toFixed(1);
    warnings.push(
      `Low disk space: ${neededGB} GB needed for model downloads, only ${freeGB} GB free`,
    );
  }

  if (vramHeadroom < 0) {
    warnings.push('VRAM may be insufficient for all recommended models');
  }

  return {
    tier,
    models,
    totalVRAM,
    totalDisk,
    diskSufficient,
    vramHeadroom,
    upgradePath,
    warnings,
  };
}

/**
 * Look up a specific model by name from the registry.
 */
export function getModelInfo(name: string): ModelRequirement | undefined {
  return MODEL_REGISTRY[name];
}

/**
 * Get all registered model names.
 */
export function getAllModelNames(): string[] {
  return Object.keys(MODEL_REGISTRY);
}
