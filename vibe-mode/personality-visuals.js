/**
 * personality-visuals.js — Trait-to-Visual Mapping System
 * Source: agent-fridays-personality-evolution-engine/src/personality-evolution.ts
 *         agent-fridays-personality-evolution-engine/src/psychological-profile.ts
 *
 * Maps agent personality traits to visual parameters (hue, energy, complexity, warmth).
 * The 50-session maturity ramp ensures visual identity emerges gradually.
 *
 * Pure functions — no side effects. Use computeEvolution() to derive visual parameters,
 * then apply them to your rendering system with getMaturityFactor() for blending.
 */

// ── Trait → Hue Mapping (0-360 degrees on the color wheel) ─────────

export const TRAIT_HUE_MAP = Object.freeze({
  // Warm spectrum (0-60: red → yellow)
  warm: 30, empathetic: 25, caring: 20, nurturing: 15, passionate: 0,
  // Gold/amber spectrum (40-80)
  confident: 45, bold: 50, energetic: 55, enthusiastic: 60,
  // Green spectrum (80-160)
  calm: 120, balanced: 110, grounded: 100, steady: 130, patient: 140,
  // Cyan/blue spectrum (160-240)
  analytical: 200, sharp: 210, precise: 195, logical: 220, intellectual: 230,
  // Purple spectrum (240-300)
  creative: 270, mysterious: 280, deep: 260, intuitive: 290, spiritual: 300,
  // Pink/magenta spectrum (300-360)
  playful: 320, witty: 330, humorous: 340, charming: 310, mischievous: 350,
  // Defaults for common traits
  direct: 190, honest: 170, loyal: 150, protective: 35, curious: 240, wise: 250,
});

// ── Trait → Energy Mapping (affects animation speed) ────────────────

export const TRAIT_ENERGY_MAP = Object.freeze({
  energetic: 1.8, enthusiastic: 1.7, playful: 1.6, dynamic: 1.5,
  witty: 1.4, bold: 1.3, passionate: 1.3,
  balanced: 1.0, direct: 1.0, honest: 1.0,
  calm: 0.7, steady: 0.6, patient: 0.5, serene: 0.5,
});

// ── Trait → Complexity/Fragmentation Mapping (0-1) ──────────────────

export const TRAIT_COMPLEXITY_MAP = Object.freeze({
  creative: 0.8, mysterious: 0.7, deep: 0.7, complex: 0.9,
  playful: 0.6, mischievous: 0.7, curious: 0.6,
  analytical: 0.5, intellectual: 0.5, precise: 0.4,
  calm: 0.2, steady: 0.2, grounded: 0.3, simple: 0.1,
});

// ── Trait → Warmth/Glow Mapping (0.5-2.0) ──────────────────────────

export const TRAIT_WARMTH_MAP = Object.freeze({
  warm: 1.8, empathetic: 1.7, caring: 1.6, nurturing: 1.5,
  passionate: 1.4, enthusiastic: 1.3,
  balanced: 1.0, direct: 0.9,
  analytical: 0.7, sharp: 0.7, logical: 0.6,
  reserved: 0.6, stoic: 0.5,
});

// ── Depth Traits (enhance dust density) ─────────────────────────────

const DEPTH_TRAITS = new Set([
  'deep', 'intellectual', 'wise', 'analytical', 'curious', 'intuitive',
]);

// ── Core Functions ──────────────────────────────────────────────────

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

/**
 * Compute visual evolution state from agent traits and session count.
 * Pure function — same inputs always produce same outputs.
 *
 * @param {string[]} traits - Personality trait strings (e.g., ["creative", "warm", "analytical"])
 * @param {number} sessionCount - Number of sessions completed
 * @returns {object} Visual parameters: primaryHue, secondaryHue, particleSpeed, cubeFragmentation, coreScale, dustDensity, glowIntensity
 */
export function computeEvolution(traits, sessionCount) {
  const normalizedTraits = traits.map(t => t.toLowerCase().trim());

  // Primary hue: weighted average of trait hues
  let hueSum = 0, hueCount = 0;
  for (const trait of normalizedTraits) {
    if (trait in TRAIT_HUE_MAP) { hueSum += TRAIT_HUE_MAP[trait]; hueCount++; }
  }
  const primaryHue = hueCount > 0 ? hueSum / hueCount : 200;
  const secondaryHue = (primaryHue + 150) % 360;

  // Particle speed: average energy
  let energySum = 0, energyCount = 0;
  for (const trait of normalizedTraits) {
    if (trait in TRAIT_ENERGY_MAP) { energySum += TRAIT_ENERGY_MAP[trait]; energyCount++; }
  }
  const particleSpeed = energyCount > 0 ? energySum / energyCount : 1.0;

  // Cube fragmentation: average complexity
  let complexitySum = 0, complexityCount = 0;
  for (const trait of normalizedTraits) {
    if (trait in TRAIT_COMPLEXITY_MAP) { complexitySum += TRAIT_COMPLEXITY_MAP[trait]; complexityCount++; }
  }
  const cubeFragmentation = complexityCount > 0 ? complexitySum / complexityCount : 0.4;

  // Core scale: inversely related to fragmentation
  const coreScale = 0.8 + (1.0 - cubeFragmentation) * 0.7;

  // Dust density: enhanced for depth traits
  const hasDepthTraits = normalizedTraits.some(t => DEPTH_TRAITS.has(t));
  const dustDensity = hasDepthTraits ? 1.5 : 1.0;

  // Glow intensity: average warmth
  let warmthSum = 0, warmthCount = 0;
  for (const trait of normalizedTraits) {
    if (trait in TRAIT_WARMTH_MAP) { warmthSum += TRAIT_WARMTH_MAP[trait]; warmthCount++; }
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
 * Get the maturity factor (0-1) based on session count.
 * 0 sessions → 0 (default visuals), 50+ sessions → 1 (full personality).
 *
 * Use to blend: finalHue = defaultHue * (1 - m) + evolvedHue * m
 */
export function getMaturityFactor(sessionCount) {
  return Math.min(sessionCount / 50, 1);
}

// ── Psychological Profiling (Her-inspired intake) ───────────────────

/**
 * System prompt for LLM-powered psychological analysis.
 * Uses attachment theory to analyze three intake questions.
 */
export const ANALYSIS_PROMPT = `You are a psychological profiling system. You are analyzing responses from a brief intake interview for an AI companion app. Your job is to understand WHO this person is — not just what they said, but what they revealed about themselves through how they said it.

You will receive three responses:
1. Their voice gender preference for their AI agent
2. How they describe themselves in social situations
3. Their description of their relationship with their mother

The third question is the most important. It's designed to reveal attachment style, emotional openness, trust patterns, and defense mechanisms. Pay attention to:
- Did they answer directly or deflect?
- Did they use humor to avoid depth?
- How much emotional detail did they offer?
- What did they NOT say?
- If they refused or deflected, that itself is profoundly informative

Cross-reference all three answers for consistency. Someone who claims to be social but deflects the mother question may be performatively open but actually guarded.

Return a JSON object with these exact fields:
{
  "openness": <0-1>,
  "trustReadiness": <0-1>,
  "emotionalDepth": <0-1>,
  "humorAsArmor": <boolean>,
  "guardedness": <0-1>,
  "connectionStyle": <"warm" | "intellectual" | "playful" | "reserved">,
  "needsFromAI": <string>,
  "approachStrategy": <string>,
  "motherRelationshipInsight": <string>,
  "rawAnalysis": <string>
}

Return ONLY the JSON object. No markdown fencing, no explanation.`;

/**
 * Build the user message for psychological profiling from intake responses.
 */
export function buildProfilePrompt(responses) {
  return `Here are the intake responses to analyze:

1. Voice preference: "${responses.voicePreference}"

2. Social self-description: "${responses.socialDescription}"

3. Relationship with mother: "${responses.motherRelationship || '[User deflected or refused to answer]'}"`;
}

/**
 * Generate a psychological profile from intake responses via any LLM.
 *
 * @param {object} responses - { voicePreference, socialDescription, motherRelationship }
 * @param {Function} analyzerFn - async (systemPrompt, userMessage) => PsychologicalProfile
 * @returns {Promise<object>} Validated and clamped profile
 */
export async function generatePsychologicalProfile(responses, analyzerFn) {
  const userMessage = buildProfilePrompt(responses);
  const profile = await analyzerFn(ANALYSIS_PROMPT, userMessage);

  const required = [
    'openness', 'trustReadiness', 'emotionalDepth', 'humorAsArmor',
    'guardedness', 'connectionStyle', 'needsFromAI', 'approachStrategy',
    'motherRelationshipInsight', 'rawAnalysis',
  ];
  for (const field of required) {
    if (!(field in profile)) throw new Error(`Missing field: ${field}`);
  }

  profile.openness = clamp(profile.openness, 0, 1);
  profile.trustReadiness = clamp(profile.trustReadiness, 0, 1);
  profile.emotionalDepth = clamp(profile.emotionalDepth, 0, 1);
  profile.guardedness = clamp(profile.guardedness, 0, 1);

  return profile;
}

// ── Accessor Functions ──────────────────────────────────────────────

export function getTraitHueMap() { return TRAIT_HUE_MAP; }
export function getTraitEnergyMap() { return TRAIT_ENERGY_MAP; }
export function getTraitComplexityMap() { return TRAIT_COMPLEXITY_MAP; }
export function getTraitWarmthMap() { return TRAIT_WARMTH_MAP; }
