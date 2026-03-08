/**
 * tier-recommender.ts -- Pure functions that map a HardwareProfile to a tier.
 *
 * Stateless input -> output. No singleton, no events, no side effects.
 * Consumes the HardwareProfile produced by HardwareProfiler (O.1) and
 * recommends a tier with model requirements, disk checks, and upgrade paths.
 *
 * Sprint 6 O.2: "The Measure" -- TierRecommender
 */

import type { HardwareProfile } from './hardware-profiler';

// -- Constants ---------------------------------------------------------------

const GB = 1024 * 1024 * 1024;

// -- Contract Types ----------------------------------------------------------

export type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

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
}

export interface UpgradePath {
  nextTier: TierName;
  requiredVRAM: number;      // bytes needed for next tier
  requiredDisk: number;      // bytes needed for next tier downloads
  unlocks: string[];         // e.g., ["Local 8B LLM", "Offline chat"]
}

// -- Model Registry ----------------------------------------------------------

const MODEL_REGISTRY: Record<string, ModelRequirement> = {
  'nomic-embed-text': {
    name: 'nomic-embed-text',
    vramBytes: 0.5 * GB,
    diskBytes: 0.3 * GB,
    purpose: 'Text embeddings for semantic search',
    required: true,
  },
  'llama3.1:8b-instruct-q4_K_M': {
    name: 'llama3.1:8b-instruct-q4_K_M',
    vramBytes: 5.5 * GB,
    diskBytes: 4.7 * GB,
    purpose: 'Local language model for chat and reasoning',
    required: true,
  },
  'moondream:latest': {
    name: 'moondream:latest',
    vramBytes: 1.2 * GB,
    diskBytes: 0.8 * GB,
    purpose: 'Vision-language model for image understanding',
    required: true,
  },
  'llama3.1:70b-instruct-q4_K_M': {
    name: 'llama3.1:70b-instruct-q4_K_M',
    vramBytes: 40 * GB,
    diskBytes: 40 * GB,
    purpose: 'Large language model for complex reasoning',
    required: false,
  },
};

// -- Tier -> Model Mapping ---------------------------------------------------

const TIER_MODELS: Record<TierName, string[]> = {
  whisper: [],
  light: ['nomic-embed-text'],
  standard: ['nomic-embed-text', 'llama3.1:8b-instruct-q4_K_M'],
  full: ['nomic-embed-text', 'llama3.1:8b-instruct-q4_K_M', 'moondream:latest'],
  sovereign: [
    'nomic-embed-text',
    'llama3.1:8b-instruct-q4_K_M',
    'moondream:latest',
    'llama3.1:70b-instruct-q4_K_M',
  ],
};

// -- Tier VRAM Thresholds (ascending order for matching) ----------------------

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
    requiredDisk: 0.3 * GB,
    unlocks: ['Local embeddings', 'Semantic search'],
  },
  light: {
    nextTier: 'standard',
    requiredVRAM: 6 * GB,
    requiredDisk: 0.3 * GB + 4.7 * GB,
    unlocks: ['Local 8B LLM', 'Offline chat'],
  },
  standard: {
    nextTier: 'full',
    requiredVRAM: 8 * GB,
    requiredDisk: 0.3 * GB + 4.7 * GB + 0.8 * GB,
    unlocks: ['Vision model', 'Image understanding'],
  },
  full: {
    nextTier: 'sovereign',
    requiredVRAM: 16 * GB,
    requiredDisk: 0.3 * GB + 4.7 * GB + 0.8 * GB + 40 * GB,
    unlocks: ['Larger models', 'Better quality reasoning'],
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
 * Sum the VRAM bytes required by an array of models.
 */
export function estimateVRAMUsage(models: ModelRequirement[]): number {
  return models.reduce((sum, m) => sum + m.vramBytes, 0);
}

/**
 * Check whether a model fits within the profile's VRAM budget,
 * accounting for already-loaded models.
 */
export function canFitModel(
  model: ModelRequirement,
  profile: HardwareProfile,
  loaded: ModelRequirement[] = [],
): boolean {
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
  const totalDisk = models.reduce((sum, m) => sum + m.diskBytes, 0);
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
