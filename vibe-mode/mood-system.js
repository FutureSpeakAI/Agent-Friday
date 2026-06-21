/**
 * mood-system.js — Mood Configuration & Adjustment Logic
 * Source: Agent-Friday/src/renderer/components/desktop-viz/types.ts
 *         Agent-Friday/src/main/personality-calibration.ts
 *         anti-sycophancy/index.js
 *
 * Combines three systems:
 * 1. Visual mood configs (baseColor, accentColor, rotationSpeed, bloom, particles, grain)
 * 2. Six adaptive style dimensions from personality calibration
 * 3. Anti-sycophancy circuit breaker
 */

// ── Visual Mood Configs ─────────────────────────────────────────────
// Each mood defines the visual atmosphere of the scene

export const MOODS = {
  LISTENING:  { baseColor: 0x00d2ff, accentColor: 0x8a2be2, rotationSpeed: 0.001, bloomStrength: 0.8, particleSpeedScale: 1.0, grain: 0.035 },
  REASONING:  { baseColor: 0x4b0082, accentColor: 0x00ffff, rotationSpeed: 0.003, bloomStrength: 0.6, particleSpeedScale: 0.5, grain: 0.02 },
  EXECUTING:  { baseColor: 0xffaa00, accentColor: 0xff3300, rotationSpeed: 0.008, bloomStrength: 1.2, particleSpeedScale: 1.8, grain: 0.05 },
  SUB_AGENTS: { baseColor: 0xffaa00, accentColor: 0xff3300, rotationSpeed: 0.008, bloomStrength: 1.2, particleSpeedScale: 1.8, grain: 0.05 },
  EXCITED:    { baseColor: 0xffffff, accentColor: 0x00e5ff, rotationSpeed: 0.015, bloomStrength: 1.8, particleSpeedScale: 2.5, grain: 0.08 },
  CALM:       { baseColor: 0x001133, accentColor: 0x0055aa, rotationSpeed: 0.0002, bloomStrength: 0.4, particleSpeedScale: 0.2, grain: 0.05 },
};

// ── Six Adaptive Style Dimensions ───────────────────────────────────
// From personality-calibration.ts / anti-sycophancy engine

export const DEFAULT_DIMENSIONS = {
  formality: 0.5,       // 0=casual, 1=professional
  verbosity: 0.5,       // 0=terse, 1=thorough
  humor: 0.5,           // 0=earnest, 1=playful
  technicalDepth: 0.5,  // 0=plain language, 1=implementation detail
  emotionalWarmth: 0.6, // 0=composed, 1=expressive (slightly warm default)
  proactivity: 0.6,     // 0=reactive, 1=anticipatory (slightly proactive default)
};

// ── Anti-Sycophancy Circuit Breaker ─────────────────────────────────

export const SYCOPHANCY_THRESHOLDS = {
  agreementStreak: 8,     // Consecutive agreements before alarm
  positivityBias: 0.85,   // Positivity ratio before alarm
  proactivityFloor: 0.3,  // Minimum proactivity for critical items
  explicitWeight: 0.08,   // How much explicit signals move dimensions
  implicitWeight: 0.02,   // How much implicit signals move dimensions
  decayHalfLifeDays: 14,  // Days for calibration to decay toward defaults
  dimensionFloor: 0.05,   // Minimum dimension value
  dimensionCeiling: 0.95, // Maximum dimension value
};

// ── Explicit Signal Detection ───────────────────────────────────────

export function detectExplicitSignal(text) {
  const lower = text.toLowerCase().trim();

  if (/\b(more formal|be formal|professionally|business[\s-]?like)\b/.test(lower)) return 'more_formal';
  if (/\b(less formal|be casual|more casual|chill|relax)\b/.test(lower)) return 'less_formal';
  if (/\b(more detail|elaborate|explain more|longer|go deeper|expand)\b/.test(lower)) return 'more_verbose';
  if (/\b(shorter|brief|concise|less detail|tl;?dr|be brief|too long|stop rambling)\b/.test(lower)) return 'less_verbose';
  if (/\b(more fun|be funny|more humor|joke|lighten up|more playful)\b/.test(lower)) return 'more_humor';
  if (/\b(less humor|be serious|no jokes|stop joking|more serious|focus)\b/.test(lower)) return 'less_humor';
  if (/\b(more technical|give me the code|show implementation|technical detail)\b/.test(lower)) return 'more_technical';
  if (/\b(less technical|simpler|explain like|eli5|plain english|dumb it down)\b/.test(lower)) return 'less_technical';
  if (/\b(more warm|be warmer|more empathy|more caring|be kind)\b/.test(lower)) return 'more_warm';
  if (/\b(less warm|less emotion|more detached|just the facts|professional only)\b/.test(lower)) return 'less_warm';
  if (/\b(check in more|be more proactive|remind me|don't let me forget)\b/.test(lower)) return 'more_proactive';
  if (/\b(stop checking|leave me alone|less proactive|stop reminding|don't bother)\b/.test(lower)) return 'less_proactive';

  return null;
}

// ── Implicit Signal Detection ───────────────────────────────────────

export function detectImplicitSignals(text, responseTimeMs) {
  const signals = [];
  const wordCount = text.split(/\s+/).filter(Boolean).length;
  if (wordCount === 0) return signals;

  if (wordCount <= 5) signals.push('short_response');
  else if (wordCount >= 50) signals.push('long_response');

  const techMarkers = /\b(function|const|let|var|class|import|export|async|await|interface|type |=>|npm|git|api|endpoint|database|query|schema|regex|algorithm|docker|kubernetes)\b/i;
  if (techMarkers.test(text)) signals.push('technical_question');

  const casualMarkers = /\b(haha|lol|lmao|btw|tbh|nah|yeah|yep|nope|dude|bro|omg|heh|lololol)\b/i;
  if (casualMarkers.test(text) && !techMarkers.test(text)) signals.push('casual_chat');

  if (responseTimeMs !== undefined) {
    if (responseTimeMs < 5000) signals.push('fast_followup');
    else if (responseTimeMs > 60000) signals.push('slow_followup');
  }

  return signals;
}

// ── Style Hint Builder ──────────────────────────────────────────────

export function buildCalibrationHints(dims) {
  const hints = [];
  if (dims.formality > 0.7) hints.push('Use professional, polished language.');
  else if (dims.formality < 0.3) hints.push('Keep it casual and relaxed.');
  if (dims.verbosity > 0.7) hints.push('Be detailed and thorough.');
  else if (dims.verbosity < 0.3) hints.push('Be extremely concise.');
  if (dims.humor > 0.7) hints.push('Lean into humor and wit.');
  else if (dims.humor < 0.3) hints.push('Keep it straight and earnest.');
  if (dims.technicalDepth > 0.7) hints.push('Go deep technically.');
  else if (dims.technicalDepth < 0.3) hints.push('Keep it high-level.');
  if (dims.emotionalWarmth > 0.7) hints.push('Be warm and emotionally present.');
  else if (dims.emotionalWarmth < 0.3) hints.push('Be professional and composed.');
  if (dims.proactivity > 0.7) hints.push('Be proactive. Offer suggestions.');
  else if (dims.proactivity < 0.3) hints.push("Wait to be asked.");
  return hints.length ? `## Learned Style Preferences\n${hints.map(h => `- ${h}`).join('\n')}` : '';
}
