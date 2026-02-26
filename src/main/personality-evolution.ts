/**
 * personality-evolution.ts — Maps agent personality traits and session history
 * to NexusCore visual parameters, making each agent's desktop unique over time.
 *
 * The evolution is gradual: early sessions look mostly standard, but over weeks
 * of use the colors, particle behavior, cube geometry, and ambient effects drift
 * toward a configuration unique to this specific agent's personality.
 *
 * Maturity factor: Math.min(sessionCount / 50, 1) — full uniqueness at ~50 sessions.
 */

import type { PersonalityEvolutionState } from './settings';
import { settingsManager } from './settings';

/* ── Trait → Visual Mapping Tables ── */

/** Maps individual traits to a hue offset (0-360) */
const TRAIT_HUE_MAP: Record<string, number> = {
  // Warm spectrum (0-60: red → yellow)
  warm: 30,
  empathetic: 25,
  caring: 20,
  nurturing: 15,
  passionate: 0,

  // Gold/amber spectrum (40-80)
  confident: 45,
  bold: 50,
  energetic: 55,
  enthusiastic: 60,

  // Green spectrum (80-160)
  calm: 120,
  balanced: 110,
  grounded: 100,
  steady: 130,
  patient: 140,

  // Cyan/blue spectrum (160-240)
  analytical: 200,
  sharp: 210,
  precise: 195,
  logical: 220,
  intellectual: 230,

  // Purple spectrum (240-300)
  creative: 270,
  mysterious: 280,
  deep: 260,
  intuitive: 290,
  spiritual: 300,

  // Pink/magenta spectrum (300-360)
  playful: 320,
  witty: 330,
  humorous: 340,
  charming: 310,
  mischievous: 350,

  // Defaults for common traits
  direct: 190,
  honest: 170,
  loyal: 150,
  protective: 35,
  curious: 240,
  wise: 250,
};

/** Maps traits to energy level (affects particle speed) */
const TRAIT_ENERGY_MAP: Record<string, number> = {
  energetic: 1.8, enthusiastic: 1.7, playful: 1.6, dynamic: 1.5,
  witty: 1.4, bold: 1.3, passionate: 1.3,
  balanced: 1.0, direct: 1.0, honest: 1.0,
  calm: 0.7, steady: 0.6, patient: 0.5, serene: 0.5,
};

/** Maps traits to complexity/fragmentation (0-1) */
const TRAIT_COMPLEXITY_MAP: Record<string, number> = {
  creative: 0.8, mysterious: 0.7, deep: 0.7, complex: 0.9,
  playful: 0.6, mischievous: 0.7, curious: 0.6,
  analytical: 0.5, intellectual: 0.5, precise: 0.4,
  calm: 0.2, steady: 0.2, grounded: 0.3, simple: 0.1,
};

/** Maps traits to warmth/glow (0.5-2.0) */
const TRAIT_WARMTH_MAP: Record<string, number> = {
  warm: 1.8, empathetic: 1.7, caring: 1.6, nurturing: 1.5,
  passionate: 1.4, enthusiastic: 1.3,
  balanced: 1.0, direct: 0.9,
  analytical: 0.7, sharp: 0.7, logical: 0.6,
  reserved: 0.6, stoic: 0.5,
};

/**
 * Computes the visual evolution state from agent traits and session count.
 */
export function computeEvolution(
  traits: string[],
  sessionCount: number
): PersonalityEvolutionState {
  const normalizedTraits = traits.map((t) => t.toLowerCase().trim());

  // Compute primary hue as weighted average of trait hues
  let hueSum = 0;
  let hueCount = 0;
  for (const trait of normalizedTraits) {
    if (trait in TRAIT_HUE_MAP) {
      hueSum += TRAIT_HUE_MAP[trait];
      hueCount++;
    }
  }
  const primaryHue = hueCount > 0 ? hueSum / hueCount : 200; // Default: cyan-ish

  // Secondary hue: complementary (180 degrees offset)
  const secondaryHue = (primaryHue + 150) % 360; // Offset slightly for visual interest

  // Particle speed: average energy of traits
  let energySum = 0;
  let energyCount = 0;
  for (const trait of normalizedTraits) {
    if (trait in TRAIT_ENERGY_MAP) {
      energySum += TRAIT_ENERGY_MAP[trait];
      energyCount++;
    }
  }
  const particleSpeed = energyCount > 0 ? energySum / energyCount : 1.0;

  // Cube fragmentation: average complexity
  let complexitySum = 0;
  let complexityCount = 0;
  for (const trait of normalizedTraits) {
    if (trait in TRAIT_COMPLEXITY_MAP) {
      complexitySum += TRAIT_COMPLEXITY_MAP[trait];
      complexityCount++;
    }
  }
  const cubeFragmentation = complexityCount > 0 ? complexitySum / complexityCount : 0.4;

  // Core scale: inversely related to fragmentation (more fragmented = smaller core)
  const coreScale = 0.8 + (1.0 - cubeFragmentation) * 0.7;

  // Dust density: related to thoughtfulness/depth traits
  const hasDepthTraits = normalizedTraits.some((t) =>
    ['deep', 'intellectual', 'wise', 'analytical', 'curious', 'intuitive'].includes(t)
  );
  const dustDensity = hasDepthTraits ? 1.5 : 1.0;

  // Glow intensity: average warmth
  let warmthSum = 0;
  let warmthCount = 0;
  for (const trait of normalizedTraits) {
    if (trait in TRAIT_WARMTH_MAP) {
      warmthSum += TRAIT_WARMTH_MAP[trait];
      warmthCount++;
    }
  }
  const glowIntensity = warmthCount > 0 ? warmthSum / warmthCount : 1.0;

  return {
    sessionCount,
    primaryHue,
    secondaryHue,
    particleSpeed: clamp(particleSpeed, 0.5, 2.0),
    cubeFragmentation: clamp(cubeFragmentation, 0, 1),
    coreScale: clamp(coreScale, 0.8, 1.5),
    dustDensity: clamp(dustDensity, 0.5, 2.0),
    glowIntensity: clamp(glowIntensity, 0.5, 2.0),
  };
}

/**
 * Returns the maturity factor (0-1) based on session count.
 * At 0 sessions, evolution effects are invisible.
 * At 50+ sessions, evolution effects are fully applied.
 */
export function getMaturityFactor(sessionCount: number): number {
  return Math.min(sessionCount / 50, 1);
}

/**
 * Increments the session count and recomputes the evolution state.
 * Call this once per session start.
 */
export async function incrementSession(): Promise<PersonalityEvolutionState> {
  const settings = settingsManager.get();
  const config = settingsManager.getAgentConfig();
  const traits = config.agentTraits || [];

  const currentCount = settings.personalityEvolution?.sessionCount ?? 0;
  const newState = computeEvolution(traits, currentCount + 1);

  await settingsManager.setSetting('personalityEvolution', newState);
  console.log(
    `[Evolution] Session ${newState.sessionCount}: ` +
    `hue=${newState.primaryHue.toFixed(0)}°, ` +
    `maturity=${getMaturityFactor(newState.sessionCount).toFixed(2)}`
  );

  return newState;
}

/**
 * Gets the current evolution state, or computes initial if none exists.
 */
export function getEvolutionState(): PersonalityEvolutionState | null {
  return settingsManager.get().personalityEvolution;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
