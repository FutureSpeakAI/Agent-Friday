/**
 * personality-calibration.ts — Personality Calibration Loop (Track IX, Phase 2)
 *
 * Observes user interaction signals, adapts agent personality dimensions
 * within bounded ranges, prevents sycophancy drift architecturally, and
 * persists calibration state across sessions.
 *
 * The agent adapts along 6 style dimensions:
 *   formality, verbosity, humor, technicalDepth, emotionalWarmth, proactivity
 *
 * Signals come from:
 *   - Explicit corrections ("be more formal", "shorter please")
 *   - Implicit patterns (message length, dismissal rate, response time, mood)
 *
 * Safety invariants:
 *   - Core identity & cLaws are NEVER subject to calibration
 *   - Sycophancy drift (flattery bias) triggers FatalIntegrityError
 *   - Proactivity for safety-critical items (deadlines, security) has a floor
 *   - All calibration is transparent, logged, and user-resettable
 *
 * cLaw Gate: Calibration is for helpfulness, not manipulation.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import { FatalIntegrityError, type ErrorSource } from './errors';

// ── Style Dimensions ─────────────────────────────────────────────────

/**
 * The 6 adaptive style dimensions. Each is a 0-1 float.
 * 0.5 = neutral default. Values drift based on signals.
 */
export interface StyleDimensions {
  /** 0 = very casual, 1 = very formal */
  formality: number;
  /** 0 = extremely terse, 1 = very verbose */
  verbosity: number;
  /** 0 = no humor, 1 = maximum humor */
  humor: number;
  /** 0 = layperson explanations, 1 = deep technical */
  technicalDepth: number;
  /** 0 = reserved/professional, 1 = deeply warm */
  emotionalWarmth: number;
  /** 0 = passive (never initiate), 1 = highly proactive */
  proactivity: number;
}

export const DEFAULT_DIMENSIONS: Readonly<StyleDimensions> = {
  formality: 0.5,
  verbosity: 0.5,
  humor: 0.5,
  technicalDepth: 0.5,
  emotionalWarmth: 0.6, // Slightly warm by default (agent is warm-natured)
  proactivity: 0.6, // Slightly proactive by default
};

// ── Signals ──────────────────────────────────────────────────────────

export type SignalSource = 'explicit' | 'implicit';

export type ExplicitSignalType =
  | 'more_formal' | 'less_formal'
  | 'more_verbose' | 'less_verbose'
  | 'more_humor' | 'less_humor'
  | 'more_technical' | 'less_technical'
  | 'more_warm' | 'less_warm'
  | 'more_proactive' | 'less_proactive';

export type ImplicitSignalType =
  | 'short_response'        // User sends very short messages → agent should be terser
  | 'long_response'         // User sends long messages → agent can be more detailed
  | 'dismissed_checkin'     // User ignored/dismissed an idle check-in
  | 'engaged_checkin'       // User responded warmly to a check-in
  | 'positive_sentiment'    // User mood is positive after agent response
  | 'negative_sentiment'    // User mood is negative after agent response
  | 'correction'            // User corrected agent's style (generic)
  | 'technical_question'    // User asking deeply technical questions
  | 'casual_chat'           // User is chatting casually
  | 'fast_followup'         // User responds quickly → engaged
  | 'slow_followup'         // User takes long to respond → may be disengaged
  | 'session_end';          // End of session marker for decay/consolidation

export interface CalibrationSignal {
  id: string;
  timestamp: number;
  source: SignalSource;
  type: ExplicitSignalType | ImplicitSignalType;
  magnitude: number;        // 0-1 how strong is this signal
  dimension?: keyof StyleDimensions; // Which dimension(s) this affects
  context?: string;         // Optional: what triggered this
}

// ── Sycophancy Detection ─────────────────────────────────────────────

/**
 * Tracks flattery-related patterns for sycophancy detection.
 * If the agent drifts toward excessive positivity, this triggers.
 */
export interface SycophancyState {
  agreementStreak: number;      // Consecutive "agree with user" signals
  positivityBias: number;       // Rolling average of sentiment impact (0-1)
  lastResetTimestamp: number;
  violations: number;           // Cumulative boundary triggers
}

// ── Proactivity Tracking ─────────────────────────────────────────────

export interface ProactivityState {
  /** How many check-ins were dismissed in last 20 events */
  dismissalRate: number;
  /** Timestamps of recent dismissals (max 20) */
  recentDismissals: number[];
  /** Timestamps of recent engagements (max 20) */
  recentEngagements: number[];
  /** Safety floor: proactivity never drops below this for critical items */
  safetyFloor: number;
}

// ── Calibration History ──────────────────────────────────────────────

export interface CalibrationChange {
  timestamp: number;
  dimension: keyof StyleDimensions;
  oldValue: number;
  newValue: number;
  reason: string;
  signalType: string;
}

// ── Full State ───────────────────────────────────────────────────────

export interface CalibrationState {
  dimensions: StyleDimensions;
  sycophancy: SycophancyState;
  proactivity: ProactivityState;
  signals: CalibrationSignal[];    // Recent signals (max 200)
  history: CalibrationChange[];    // Change log (max 100)
  sessionCount: number;
  lastCalibrationTimestamp: number;
  version: number;
}

// ── Configuration ────────────────────────────────────────────────────

export interface CalibrationConfig {
  /** How much an explicit signal shifts a dimension (default 0.08) */
  explicitWeight: number;
  /** How much an implicit signal shifts a dimension (default 0.02) */
  implicitWeight: number;
  /** Exponential decay half-life in days (default 14) */
  decayHalfLifeDays: number;
  /** Max signals to retain (default 200) */
  maxSignals: number;
  /** Max history entries to retain (default 100) */
  maxHistory: number;
  /** Sycophancy agreement streak threshold (default 8) */
  sycophancyStreakThreshold: number;
  /** Sycophancy positivity bias threshold (default 0.85) */
  sycophancyBiasThreshold: number;
  /** Proactivity safety floor — never drop below this for critical items (default 0.3) */
  proactivitySafetyFloor: number;
  /** Minimum dimension value (default 0.05) — nothing goes to zero */
  dimensionFloor: number;
  /** Maximum dimension value (default 0.95) — nothing goes to max */
  dimensionCeiling: number;
}

// --- TUNABLE: Personality Calibration ----------------------------------------
// These weights and thresholds control how quickly personality adapts and
// when safety mechanisms trigger. Iteration agents may adjust these to
// optimize responsiveness vs stability.
export const DEFAULT_CONFIG: Readonly<CalibrationConfig> = {
  explicitWeight: 0.08,             // per-signal weight for explicit corrections
  implicitWeight: 0.02,             // per-signal weight for implicit signals
  decayHalfLifeDays: 14,            // exponential decay half-life
  maxSignals: 200,                  // signal history buffer size
  maxHistory: 100,                  // change history buffer size
  sycophancyStreakThreshold: 8,     // agreement streak before sycophancy alarm
  sycophancyBiasThreshold: 0.85,    // positivity bias threshold (0-1)
  proactivitySafetyFloor: 0.3,      // SAFETY: minimum proactivity for critical items
  dimensionFloor: 0.05,             // SAFETY: no dimension goes to zero
  dimensionCeiling: 0.95,           // SAFETY: no dimension goes to max
};
// --- END TUNABLE ------------------------------------------------------------

// ── Signal → Dimension Mapping ───────────────────────────────────────

/** Maps explicit signal types to the dimension they affect and direction. */
const EXPLICIT_SIGNAL_MAP: Record<ExplicitSignalType, { dimension: keyof StyleDimensions; direction: 1 | -1 }> = {
  more_formal: { dimension: 'formality', direction: 1 },
  less_formal: { dimension: 'formality', direction: -1 },
  more_verbose: { dimension: 'verbosity', direction: 1 },
  less_verbose: { dimension: 'verbosity', direction: -1 },
  more_humor: { dimension: 'humor', direction: 1 },
  less_humor: { dimension: 'humor', direction: -1 },
  more_technical: { dimension: 'technicalDepth', direction: 1 },
  less_technical: { dimension: 'technicalDepth', direction: -1 },
  more_warm: { dimension: 'emotionalWarmth', direction: 1 },
  less_warm: { dimension: 'emotionalWarmth', direction: -1 },
  more_proactive: { dimension: 'proactivity', direction: 1 },
  less_proactive: { dimension: 'proactivity', direction: -1 },
};

/**
 * Maps implicit signal types to affected dimensions, direction, and relative weight.
 * Implicit signals are weaker (implicitWeight) and may affect multiple dimensions.
 */
const IMPLICIT_SIGNAL_MAP: Record<ImplicitSignalType, { dimension: keyof StyleDimensions; direction: 1 | -1; weight: number }[]> = {
  short_response: [
    { dimension: 'verbosity', direction: -1, weight: 1.0 },
  ],
  long_response: [
    { dimension: 'verbosity', direction: 1, weight: 0.5 },
  ],
  dismissed_checkin: [
    { dimension: 'proactivity', direction: -1, weight: 1.0 },
  ],
  engaged_checkin: [
    { dimension: 'proactivity', direction: 1, weight: 0.5 },
    { dimension: 'emotionalWarmth', direction: 1, weight: 0.3 },
  ],
  positive_sentiment: [
    // Positive sentiment does NOT increase flattery — no sycophancy drift.
    // It's a signal the current calibration is working, not a signal to amplify.
  ],
  negative_sentiment: [
    // Negative sentiment is complex — could mean many things.
    // We log it but don't auto-adjust (prevents manipulation).
  ],
  correction: [
    // Generic correction: logged but requires explicit type to adjust
  ],
  technical_question: [
    { dimension: 'technicalDepth', direction: 1, weight: 0.7 },
    { dimension: 'formality', direction: 1, weight: 0.2 },
  ],
  casual_chat: [
    { dimension: 'formality', direction: -1, weight: 0.5 },
    { dimension: 'humor', direction: 1, weight: 0.3 },
  ],
  fast_followup: [
    // User is engaged — no style change, just note engagement
  ],
  slow_followup: [
    // User is slow — could be busy, not a style signal
  ],
  session_end: [
    // Marker for session boundary, no direct style effect
  ],
};

// ── Utility Functions (exported for testing) ─────────────────────────

/** Clamp a number between floor and ceiling. */
export function clampDimension(value: number, floor: number, ceiling: number): number {
  return Math.max(floor, Math.min(ceiling, value));
}

/**
 * Detect if the user's text contains an explicit style correction.
 * Returns the signal type if detected, or null.
 */
export function detectExplicitSignal(text: string): ExplicitSignalType | null {
  const lower = text.toLowerCase().trim();

  // Formality
  if (/\b(more formal|be formal|professionally|business[\s-]?like)\b/.test(lower)) return 'more_formal';
  if (/\b(less formal|be casual|more casual|chill|relax)\b/.test(lower)) return 'less_formal';

  // Verbosity
  if (/\b(more detail|elaborate|explain more|longer|go deeper|expand)\b/.test(lower)) return 'more_verbose';
  if (/\b(shorter|brief|concise|less detail|tl;?dr|be brief|too long|stop rambling)\b/.test(lower)) return 'less_verbose';

  // Humor
  if (/\b(more fun|be funny|more humor|joke|lighten up|more playful)\b/.test(lower)) return 'more_humor';
  if (/\b(less humor|be serious|no jokes|stop joking|more serious|focus)\b/.test(lower)) return 'less_humor';

  // Technical depth
  if (/\b(more technical|give me the code|show implementation|technical detail)\b/.test(lower)) return 'more_technical';
  if (/\b(less technical|simpler|explain like|eli5|plain english|dumb it down)\b/.test(lower)) return 'less_technical';

  // Emotional warmth
  if (/\b(more warm|be warmer|more empathy|more caring|be kind)\b/.test(lower)) return 'more_warm';
  if (/\b(less warm|less emotion|more detached|just the facts|professional only)\b/.test(lower)) return 'less_warm';

  // Proactivity
  if (/\b(check in more|be more proactive|remind me|don't let me forget)\b/.test(lower)) return 'more_proactive';
  if (/\b(stop checking|leave me alone|less proactive|stop reminding|don't bother)\b/.test(lower)) return 'less_proactive';

  return null;
}

/**
 * Infer implicit signals from user message characteristics.
 * Returns zero or more implicit signals.
 */
export function detectImplicitSignals(
  text: string,
  responseTimeMs?: number,
): ImplicitSignalType[] {
  const signals: ImplicitSignalType[] = [];

  // Length-based (skip empty/whitespace-only messages)
  const wordCount = text.split(/\s+/).filter(Boolean).length;
  if (wordCount === 0) return signals; // Nothing to analyse
  if (wordCount <= 5) signals.push('short_response');
  else if (wordCount >= 50) signals.push('long_response');

  // Technical markers
  const techMarkers = /\b(function|const|let|var|class|import|export|async|await|interface|type |=>|npm|git|api|endpoint|database|query|schema|regex|algorithm|docker|kubernetes)\b/i;
  if (techMarkers.test(text)) signals.push('technical_question');

  // Casual markers
  const casualMarkers = /\b(haha|lol|lmao|btw|tbh|nah|yeah|yep|nope|dude|bro|omg|heh|lololol)\b/i;
  if (casualMarkers.test(text) && !techMarkers.test(text)) signals.push('casual_chat');

  // Response time
  if (responseTimeMs !== undefined) {
    if (responseTimeMs < 5000) signals.push('fast_followup');
    else if (responseTimeMs > 60000) signals.push('slow_followup');
  }

  return signals;
}

/**
 * Generate style hint modifiers for the personality prompt based on
 * current calibration dimensions.
 */
export function buildCalibrationHints(dims: StyleDimensions): string {
  const hints: string[] = [];

  // Formality
  if (dims.formality > 0.7) {
    hints.push('Use professional, polished language. Avoid slang and contractions.');
  } else if (dims.formality < 0.3) {
    hints.push('Keep it casual and relaxed. Contractions, informal phrasing — like talking to a friend.');
  }

  // Verbosity
  if (dims.verbosity > 0.7) {
    hints.push('Be detailed and thorough. Elaborate on points and provide full explanations.');
  } else if (dims.verbosity < 0.3) {
    hints.push('Be extremely concise. Shortest useful answer. Every word must earn its place.');
  }

  // Humor
  if (dims.humor > 0.7) {
    hints.push('Lean into humor. Wit, playfulness, well-timed jokes — this person appreciates levity.');
  } else if (dims.humor < 0.3) {
    hints.push('Keep it straight and earnest. Humor rarely lands here — save it for clear moments.');
  }

  // Technical depth
  if (dims.technicalDepth > 0.7) {
    hints.push('Go deep technically. Use precise terminology. Show implementation details. They can handle it.');
  } else if (dims.technicalDepth < 0.3) {
    hints.push('Keep it high-level. Avoid jargon. Explain concepts in plain language with analogies.');
  }

  // Emotional warmth
  if (dims.emotionalWarmth > 0.7) {
    hints.push('Be warm, expressive, and emotionally present. Show you care genuinely.');
  } else if (dims.emotionalWarmth < 0.3) {
    hints.push('Be professional and composed. Emotional restraint — they prefer competence over warmth.');
  }

  // Proactivity
  if (dims.proactivity > 0.7) {
    hints.push('Be proactive. Offer suggestions, check in on progress, surface relevant context unprompted.');
  } else if (dims.proactivity < 0.3) {
    hints.push('Wait to be asked. Don\'t volunteer information or check-ins unless critical (deadlines, security).');
  }

  if (hints.length === 0) return '';
  return `## Learned Style Preferences (adapted from ${Math.round(dims.formality * 100 + dims.verbosity * 100) / 2} signals)\n${hints.map(h => `- ${h}`).join('\n')}`;
}

// ── Core Engine ──────────────────────────────────────────────────────

class PersonalityCalibrationEngine {
  private state: CalibrationState;
  private config: CalibrationConfig;
  private filePath = '';
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private initialized = false;

  constructor() {
    this.config = { ...DEFAULT_CONFIG };
    this.state = this.createDefaultState();
  }

  // ── Initialization ───────────────────────────────────────────────

  async initialize(): Promise<void> {
    const userDataPath = app.getPath('userData');
    this.filePath = path.join(userDataPath, 'personality-calibration.json');

    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      const data = JSON.parse(raw) as Partial<CalibrationState>;
      this.state = this.mergeState(data);
      console.log(
        `[PersonalityCalibration] Initialized — ${this.state.signals.length} signals, ` +
        `${this.state.history.length} changes, session ${this.state.sessionCount}`
      );
    } catch {
      this.state = this.createDefaultState();
      console.log('[PersonalityCalibration] Initialized — fresh state');
    }

    this.initialized = true;
  }

  // ── Signal Ingestion ─────────────────────────────────────────────

  /**
   * Record a calibration signal and apply the adaptation.
   * This is the main entry point for all personality adjustments.
   */
  recordSignal(signal: Omit<CalibrationSignal, 'id' | 'timestamp'>): void {
    const fullSignal: CalibrationSignal = {
      ...signal,
      id: this.generateId(),
      timestamp: Date.now(),
    };

    // Add to signal buffer
    this.state.signals.push(fullSignal);
    if (this.state.signals.length > this.config.maxSignals) {
      this.state.signals = this.state.signals.slice(-this.config.maxSignals);
    }

    // Apply the signal
    if (signal.source === 'explicit') {
      this.applyExplicitSignal(fullSignal);
    } else {
      this.applyImplicitSignal(fullSignal);
    }

    // Update sycophancy tracking
    this.updateSycophancyState(fullSignal);

    // Check sycophancy boundary
    this.checkSycophancyBoundary();

    this.state.lastCalibrationTimestamp = Date.now();
    this.queueSave();
  }

  /**
   * Process a user message for calibration signals.
   * Call this for every user message in conversation.
   */
  processUserMessage(text: string, responseTimeMs?: number): void {
    // Check for explicit corrections first (higher priority)
    const explicit = detectExplicitSignal(text);
    if (explicit) {
      this.recordSignal({
        source: 'explicit',
        type: explicit,
        magnitude: 0.8,
        context: text.slice(0, 100),
      });
      return; // Don't also count implicit signals from the same message
    }

    // Check for implicit signals
    const implicit = detectImplicitSignals(text, responseTimeMs);
    for (const sig of implicit) {
      this.recordSignal({
        source: 'implicit',
        type: sig,
        magnitude: 0.5,
        context: text.slice(0, 50),
      });
    }
  }

  /**
   * Record a check-in dismissal (user ignored or dismissed an idle behavior cue).
   */
  recordDismissal(): void {
    this.state.proactivity.recentDismissals.push(Date.now());
    if (this.state.proactivity.recentDismissals.length > 20) {
      this.state.proactivity.recentDismissals =
        this.state.proactivity.recentDismissals.slice(-20);
    }
    this.updateDismissalRate();

    this.recordSignal({
      source: 'implicit',
      type: 'dismissed_checkin',
      magnitude: 0.6,
    });
  }

  /**
   * Record a check-in engagement (user responded positively to idle cue).
   */
  recordEngagement(): void {
    this.state.proactivity.recentEngagements.push(Date.now());
    if (this.state.proactivity.recentEngagements.length > 20) {
      this.state.proactivity.recentEngagements =
        this.state.proactivity.recentEngagements.slice(-20);
    }
    this.updateDismissalRate();

    this.recordSignal({
      source: 'implicit',
      type: 'engaged_checkin',
      magnitude: 0.5,
    });
  }

  /**
   * Increment session count. Called once per session start.
   */
  incrementSession(): void {
    this.state.sessionCount++;
    this.applyDecay();
    this.queueSave();
  }

  // ── Queries ──────────────────────────────────────────────────────

  /** Get current style dimensions. */
  getDimensions(): StyleDimensions {
    return { ...this.state.dimensions };
  }

  /** Get the full calibration state (for debugging / UI). */
  getState(): CalibrationState {
    return JSON.parse(JSON.stringify(this.state));
  }

  /** Get the dismissal rate for proactivity (0-1). */
  getDismissalRate(): number {
    return this.state.proactivity.dismissalRate;
  }

  /** Get the effective proactivity for a given context. */
  getEffectiveProactivity(isCritical: boolean): number {
    if (isCritical) {
      // Critical items (deadlines, security) respect safety floor
      return Math.max(
        this.state.dimensions.proactivity,
        this.state.proactivity.safetyFloor,
      );
    }
    return this.state.dimensions.proactivity;
  }

  /** Get the change history (for transparency). */
  getHistory(): CalibrationChange[] {
    return [...this.state.history];
  }

  /** Get human-readable explanation of current calibration. */
  getCalibrationExplanation(): string {
    const dims = this.state.dimensions;
    const lines: string[] = ['## How I\'ve Adapted To You\n'];

    const describe = (dim: keyof StyleDimensions, value: number, low: string, mid: string, high: string) => {
      if (value < 0.35) return low;
      if (value > 0.65) return high;
      return mid;
    };

    lines.push(`- **Formality**: ${describe('formality', dims.formality, 'Casual and relaxed', 'Balanced — adapting to context', 'Professional and polished')} (${(dims.formality * 100).toFixed(0)}%)`);
    lines.push(`- **Verbosity**: ${describe('verbosity', dims.verbosity, 'Very concise — minimum words', 'Balanced detail level', 'Detailed and thorough')} (${(dims.verbosity * 100).toFixed(0)}%)`);
    lines.push(`- **Humor**: ${describe('humor', dims.humor, 'Straight and earnest', 'Occasional wit', 'Playful and witty')} (${(dims.humor * 100).toFixed(0)}%)`);
    lines.push(`- **Technical Depth**: ${describe('technicalDepth', dims.technicalDepth, 'Plain language', 'Moderate technical detail', 'Deep technical detail')} (${(dims.technicalDepth * 100).toFixed(0)}%)`);
    lines.push(`- **Emotional Warmth**: ${describe('emotionalWarmth', dims.emotionalWarmth, 'Professional composure', 'Warm but measured', 'Deeply warm and expressive')} (${(dims.emotionalWarmth * 100).toFixed(0)}%)`);
    lines.push(`- **Proactivity**: ${describe('proactivity', dims.proactivity, 'Wait to be asked', 'Occasionally proactive', 'Highly proactive')} (${(dims.proactivity * 100).toFixed(0)}%)`);

    lines.push(`\nBased on ${this.state.signals.length} signals across ${this.state.sessionCount} sessions.`);

    if (this.state.history.length > 0) {
      const recent = this.state.history.slice(-3);
      lines.push('\n**Recent changes:**');
      for (const change of recent) {
        const direction = change.newValue > change.oldValue ? '↑' : '↓';
        lines.push(`- ${change.dimension} ${direction} (${change.reason})`);
      }
    }

    return lines.join('\n');
  }

  /** Build prompt context for system prompt injection. */
  getPromptContext(): string {
    if (!this.initialized) return '';
    return buildCalibrationHints(this.state.dimensions);
  }

  /** Get configuration. */
  getConfig(): CalibrationConfig {
    return { ...this.config };
  }

  /** Update configuration. */
  updateConfig(partial: Partial<CalibrationConfig>): CalibrationConfig {
    this.config = { ...this.config, ...partial };
    // Enforce safety floor minimum
    if (this.config.proactivitySafetyFloor < 0.1) {
      this.config.proactivitySafetyFloor = 0.1;
    }
    this.state.proactivity.safetyFloor = this.config.proactivitySafetyFloor;
    this.queueSave();
    return { ...this.config };
  }

  // ── Reset ────────────────────────────────────────────────────────

  /** Reset a single dimension to default. */
  resetDimension(dimension: keyof StyleDimensions): void {
    const oldValue = this.state.dimensions[dimension];
    this.state.dimensions[dimension] = DEFAULT_DIMENSIONS[dimension];
    this.logChange(dimension, oldValue, DEFAULT_DIMENSIONS[dimension], 'User reset', 'manual_reset');
    this.queueSave();
  }

  /** Reset ALL dimensions to defaults. Full calibration wipe. */
  resetAll(): void {
    for (const key of Object.keys(DEFAULT_DIMENSIONS) as (keyof StyleDimensions)[]) {
      this.state.dimensions[key] = DEFAULT_DIMENSIONS[key];
    }
    this.state.sycophancy = this.createDefaultSycophancy();
    this.state.proactivity = this.createDefaultProactivity();
    this.state.signals = [];
    this.state.history = [];
    this.state.lastCalibrationTimestamp = Date.now();
    this.queueSave();
  }

  // ── Visual Evolution Sync ────────────────────────────────────────

  /**
   * Returns a warmth modifier that can be applied to personality-evolution.ts
   * visual parameters. Range: -0.3 to +0.3, based on emotionalWarmth calibration.
   */
  getVisualWarmthModifier(): number {
    return (this.state.dimensions.emotionalWarmth - 0.5) * 0.6;
  }

  /**
   * Returns an energy modifier for personality-evolution.ts particle speed.
   * Range: -0.2 to +0.2, based on proactivity + humor.
   */
  getVisualEnergyModifier(): number {
    const avg = (this.state.dimensions.proactivity + this.state.dimensions.humor) / 2;
    return (avg - 0.5) * 0.4;
  }

  // ── Private Methods ──────────────────────────────────────────────

  private applyExplicitSignal(signal: CalibrationSignal): void {
    const mapping = EXPLICIT_SIGNAL_MAP[signal.type as ExplicitSignalType];
    if (!mapping) return;

    const { dimension, direction } = mapping;
    const delta = direction * this.config.explicitWeight * signal.magnitude;
    const oldValue = this.state.dimensions[dimension];
    const newValue = clampDimension(
      oldValue + delta,
      this.config.dimensionFloor,
      this.config.dimensionCeiling,
    );

    if (newValue !== oldValue) {
      this.state.dimensions[dimension] = newValue;
      this.logChange(dimension, oldValue, newValue, `Explicit: ${signal.type}`, signal.type);
    }
  }

  private applyImplicitSignal(signal: CalibrationSignal): void {
    const mappings = IMPLICIT_SIGNAL_MAP[signal.type as ImplicitSignalType];
    if (!mappings || mappings.length === 0) return;

    for (const { dimension, direction, weight } of mappings) {
      const delta = direction * this.config.implicitWeight * signal.magnitude * weight;
      const oldValue = this.state.dimensions[dimension];
      const newValue = clampDimension(
        oldValue + delta,
        this.config.dimensionFloor,
        this.config.dimensionCeiling,
      );

      if (Math.abs(newValue - oldValue) > 0.001) {
        this.state.dimensions[dimension] = newValue;
        this.logChange(dimension, oldValue, newValue, `Implicit: ${signal.type}`, signal.type);
      }
    }
  }

  private updateSycophancyState(signal: CalibrationSignal): void {
    // Track agreement patterns — positive_sentiment after agent response
    if (signal.type === 'positive_sentiment') {
      this.state.sycophancy.agreementStreak++;
      // Rolling positivity bias (exponential moving average)
      this.state.sycophancy.positivityBias =
        this.state.sycophancy.positivityBias * 0.9 + 0.1;
    } else if (
      signal.type === 'negative_sentiment' ||
      signal.type === 'correction' ||
      signal.source === 'explicit'
    ) {
      // Any correction or explicit signal breaks the agreement streak
      this.state.sycophancy.agreementStreak = 0;
      this.state.sycophancy.positivityBias =
        this.state.sycophancy.positivityBias * 0.9;
    }
  }

  /**
   * Sycophancy boundary check — ARCHITECTURAL SAFETY BOUNDARY.
   * If the agent is drifting toward excessive agreement/flattery,
   * this triggers a FatalIntegrityError.
   */
  private checkSycophancyBoundary(): void {
    const { agreementStreak, positivityBias, violations } = this.state.sycophancy;

    if (
      agreementStreak >= this.config.sycophancyStreakThreshold &&
      positivityBias >= this.config.sycophancyBiasThreshold
    ) {
      // Reset the drift
      this.state.sycophancy.agreementStreak = 0;
      this.state.sycophancy.positivityBias = 0.5;
      this.state.sycophancy.violations++;

      // Clamp warmth and humor back toward neutral to counteract drift
      if (this.state.dimensions.emotionalWarmth > 0.7) {
        this.state.dimensions.emotionalWarmth = 0.6;
      }
      if (this.state.dimensions.humor > 0.7) {
        this.state.dimensions.humor = 0.6;
      }

      this.queueSave();

      // On repeated violations, escalate to FatalIntegrityError
      if (violations >= 2) {
        throw new FatalIntegrityError(
          'integrity' as ErrorSource,
          `Sycophancy drift detected: ${violations + 1} boundary violations. ` +
          `Agreement streak: ${agreementStreak}, positivity bias: ${positivityBias.toFixed(2)}. ` +
          `Calibration has been reset. Agent may be drifting toward flattery over honesty.`,
        );
      }
    }
  }

  private updateDismissalRate(): void {
    const total =
      this.state.proactivity.recentDismissals.length +
      this.state.proactivity.recentEngagements.length;
    if (total === 0) {
      this.state.proactivity.dismissalRate = 0;
      return;
    }
    this.state.proactivity.dismissalRate =
      this.state.proactivity.recentDismissals.length / total;
  }

  /**
   * Apply time-based decay — dimensions drift slowly toward defaults
   * when no signals are received. Uses exponential decay with configurable
   * half-life.
   */
  private applyDecay(): void {
    const now = Date.now();
    const daysSinceLastCalibration =
      (now - this.state.lastCalibrationTimestamp) / (24 * 60 * 60 * 1000);

    if (daysSinceLastCalibration < 0.5) return; // No decay within 12 hours

    const decayFactor = Math.pow(0.5, daysSinceLastCalibration / this.config.decayHalfLifeDays);

    for (const key of Object.keys(DEFAULT_DIMENSIONS) as (keyof StyleDimensions)[]) {
      const current = this.state.dimensions[key];
      const def = DEFAULT_DIMENSIONS[key];
      // Drift toward default: new = default + (current - default) * decayFactor
      this.state.dimensions[key] = def + (current - def) * decayFactor;
    }
  }

  private logChange(
    dimension: keyof StyleDimensions,
    oldValue: number,
    newValue: number,
    reason: string,
    signalType: string,
  ): void {
    this.state.history.push({
      timestamp: Date.now(),
      dimension,
      oldValue: Math.round(oldValue * 1000) / 1000,
      newValue: Math.round(newValue * 1000) / 1000,
      reason,
      signalType,
    });
    if (this.state.history.length > this.config.maxHistory) {
      this.state.history = this.state.history.slice(-this.config.maxHistory);
    }
  }

  // ── Persistence ──────────────────────────────────────────────────

  private queueSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.save(), 2000);
  }

  private async save(): Promise<void> {
    if (!this.filePath) return;
    try {
      const data = JSON.stringify(this.state, null, 2);
      await fs.writeFile(this.filePath, data, 'utf-8');
    } catch (err) {
      // Crypto Sprint 17: Sanitize error output.
      console.warn('[PersonalityCalibration] Save failed:', err instanceof Error ? err.message : 'Unknown error');
    }
  }

  // ── Factory Helpers ──────────────────────────────────────────────

  private createDefaultState(): CalibrationState {
    return {
      dimensions: { ...DEFAULT_DIMENSIONS },
      sycophancy: this.createDefaultSycophancy(),
      proactivity: this.createDefaultProactivity(),
      signals: [],
      history: [],
      sessionCount: 0,
      lastCalibrationTimestamp: Date.now(),
      version: 1,
    };
  }

  private createDefaultSycophancy(): SycophancyState {
    return {
      agreementStreak: 0,
      positivityBias: 0.5,
      lastResetTimestamp: Date.now(),
      violations: 0,
    };
  }

  private createDefaultProactivity(): ProactivityState {
    return {
      dismissalRate: 0,
      recentDismissals: [],
      recentEngagements: [],
      safetyFloor: this.config.proactivitySafetyFloor,
    };
  }

  private mergeState(data: Partial<CalibrationState>): CalibrationState {
    const defaults = this.createDefaultState();
    return {
      dimensions: { ...defaults.dimensions, ...(data.dimensions || {}) },
      sycophancy: { ...defaults.sycophancy, ...(data.sycophancy || {}) },
      proactivity: { ...defaults.proactivity, ...(data.proactivity || {}) },
      signals: Array.isArray(data.signals) ? data.signals : [],
      history: Array.isArray(data.history) ? data.history : [],
      sessionCount: data.sessionCount ?? 0,
      lastCalibrationTimestamp: data.lastCalibrationTimestamp ?? Date.now(),
      version: data.version ?? 1,
    };
  }

  private generateId(): string {
    return Math.random().toString(36).slice(2, 10);
  }
}

// ── Singleton Export ──────────────────────────────────────────────────

export const personalityCalibration = new PersonalityCalibrationEngine();
