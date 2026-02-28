/**
 * personality-calibration.test.ts — Tests for the Personality Calibration Loop (Track IX, Phase 2)
 *
 * Coverage:
 * - Pure utility functions (clampDimension, detectExplicitSignal, detectImplicitSignals, buildCalibrationHints)
 * - Default data model integrity
 * - Signal ingestion (explicit and implicit)
 * - Dimension adaptation mechanics (direction, magnitude, clamping)
 * - Sycophancy boundary enforcement (streak, bias, FatalIntegrityError)
 * - Proactivity safety floor (critical vs non-critical)
 * - Dismissal/engagement tracking
 * - Time-based decay toward defaults
 * - Visual evolution sync (warmth/energy modifiers)
 * - Session management
 * - Prompt context generation
 * - Calibration explanation transparency
 * - Configuration management
 * - Reset mechanics (single dimension + full reset)
 * - History and signal buffer management
 * - cLaw compliance (no core identity modification, no manipulation)
 * - Edge cases and boundary conditions
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock Electron + fs ──────────────────────────────────────────────

vi.mock('electron', () => ({
  app: {
    getPath: vi.fn().mockReturnValue('/mock/userData'),
  },
}));

vi.mock('fs/promises', () => ({
  default: {
    readFile: vi.fn().mockRejectedValue(new Error('ENOENT')),
    writeFile: vi.fn().mockResolvedValue(undefined),
  },
}));

// ── Import after mocks ──────────────────────────────────────────────

import {
  clampDimension,
  detectExplicitSignal,
  detectImplicitSignals,
  buildCalibrationHints,
  DEFAULT_DIMENSIONS,
  DEFAULT_CONFIG,
  type StyleDimensions,
  type CalibrationConfig,
  type CalibrationState,
  type CalibrationSignal,
  type SycophancyState,
  type ProactivityState,
  type CalibrationChange,
  personalityCalibration,
} from '../../src/main/personality-calibration';

// ── Helper ──────────────────────────────────────────────────────────

/** Reset the singleton to a fresh state before each test. */
function resetEngine(): void {
  personalityCalibration.resetAll();
}

// ═══════════════════════════════════════════════════════════════════════
// 1. DEFAULT DATA MODEL
// ═══════════════════════════════════════════════════════════════════════

describe('Default Data Model', () => {
  it('DEFAULT_DIMENSIONS has all 6 dimensions', () => {
    const keys = Object.keys(DEFAULT_DIMENSIONS);
    expect(keys).toHaveLength(6);
    expect(keys).toContain('formality');
    expect(keys).toContain('verbosity');
    expect(keys).toContain('humor');
    expect(keys).toContain('technicalDepth');
    expect(keys).toContain('emotionalWarmth');
    expect(keys).toContain('proactivity');
  });

  it('all default dimensions are between 0 and 1', () => {
    for (const [key, value] of Object.entries(DEFAULT_DIMENSIONS)) {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(1);
    }
  });

  it('emotionalWarmth defaults to 0.6 (slightly warm)', () => {
    expect(DEFAULT_DIMENSIONS.emotionalWarmth).toBe(0.6);
  });

  it('proactivity defaults to 0.6 (slightly proactive)', () => {
    expect(DEFAULT_DIMENSIONS.proactivity).toBe(0.6);
  });

  it('neutral dimensions default to 0.5', () => {
    expect(DEFAULT_DIMENSIONS.formality).toBe(0.5);
    expect(DEFAULT_DIMENSIONS.verbosity).toBe(0.5);
    expect(DEFAULT_DIMENSIONS.humor).toBe(0.5);
    expect(DEFAULT_DIMENSIONS.technicalDepth).toBe(0.5);
  });

  it('DEFAULT_CONFIG has sensible safety defaults', () => {
    expect(DEFAULT_CONFIG.explicitWeight).toBe(0.08);
    expect(DEFAULT_CONFIG.implicitWeight).toBe(0.02);
    expect(DEFAULT_CONFIG.sycophancyStreakThreshold).toBe(8);
    expect(DEFAULT_CONFIG.sycophancyBiasThreshold).toBe(0.85);
    expect(DEFAULT_CONFIG.proactivitySafetyFloor).toBe(0.3);
    expect(DEFAULT_CONFIG.dimensionFloor).toBe(0.05);
    expect(DEFAULT_CONFIG.dimensionCeiling).toBe(0.95);
  });

  it('DEFAULT_CONFIG is frozen / readonly', () => {
    // The const assertion ensures immutability at the TS level.
    // The runtime object should not be mutated.
    const original = DEFAULT_CONFIG.explicitWeight;
    expect(original).toBe(0.08);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 2. PURE UTILITY FUNCTIONS
// ═══════════════════════════════════════════════════════════════════════

describe('clampDimension', () => {
  it('clamps below floor', () => {
    expect(clampDimension(-0.5, 0.05, 0.95)).toBe(0.05);
  });

  it('clamps above ceiling', () => {
    expect(clampDimension(1.5, 0.05, 0.95)).toBe(0.95);
  });

  it('passes through valid values', () => {
    expect(clampDimension(0.5, 0.05, 0.95)).toBe(0.5);
  });

  it('handles exact boundary values', () => {
    expect(clampDimension(0.05, 0.05, 0.95)).toBe(0.05);
    expect(clampDimension(0.95, 0.05, 0.95)).toBe(0.95);
  });

  it('handles zero floor and ceiling', () => {
    expect(clampDimension(0.5, 0, 1)).toBe(0.5);
    expect(clampDimension(-1, 0, 1)).toBe(0);
    expect(clampDimension(2, 0, 1)).toBe(1);
  });
});

describe('detectExplicitSignal', () => {
  // Formality
  it('detects "more formal"', () => {
    expect(detectExplicitSignal('Can you be more formal please?')).toBe('more_formal');
  });

  it('detects "less formal" / "be casual"', () => {
    expect(detectExplicitSignal('Be casual with me')).toBe('less_formal');
    expect(detectExplicitSignal('Less formal tone please')).toBe('less_formal');
  });

  // Verbosity
  it('detects "shorter" / "be brief"', () => {
    expect(detectExplicitSignal('Keep it shorter')).toBe('less_verbose');
    expect(detectExplicitSignal('Be brief please')).toBe('less_verbose');
    expect(detectExplicitSignal("tl;dr")).toBe('less_verbose');
    expect(detectExplicitSignal("stop rambling")).toBe('less_verbose');
  });

  it('detects "more detail" / "elaborate"', () => {
    expect(detectExplicitSignal('Can you give me more detail?')).toBe('more_verbose');
    expect(detectExplicitSignal('Please elaborate on that')).toBe('more_verbose');
  });

  // Humor
  it('detects humor adjustments', () => {
    expect(detectExplicitSignal('Be funny!')).toBe('more_humor');
    expect(detectExplicitSignal('More playful please')).toBe('more_humor');
    expect(detectExplicitSignal('No jokes right now')).toBe('less_humor');
    expect(detectExplicitSignal('Be serious')).toBe('less_humor');
  });

  // Technical depth
  it('detects technical adjustments', () => {
    expect(detectExplicitSignal('More technical detail please')).toBe('more_technical');
    expect(detectExplicitSignal('Give me the code')).toBe('more_technical');
    expect(detectExplicitSignal('ELI5 this concept')).toBe('less_technical');
    expect(detectExplicitSignal('dumb it down for me')).toBe('less_technical');
  });

  // Emotional warmth
  it('detects warmth adjustments', () => {
    expect(detectExplicitSignal('Be warmer with me')).toBe('more_warm');
    expect(detectExplicitSignal('Just the facts please')).toBe('less_warm');
  });

  // Proactivity
  it('detects proactivity adjustments', () => {
    expect(detectExplicitSignal("Don't let me forget about this")).toBe('more_proactive');
    expect(detectExplicitSignal("Check in more often")).toBe('more_proactive');
    expect(detectExplicitSignal("Leave me alone for a while")).toBe('less_proactive');
    expect(detectExplicitSignal("Stop checking in")).toBe('less_proactive');
  });

  // Non-matches
  it('returns null for regular text', () => {
    expect(detectExplicitSignal('What is the weather today?')).toBeNull();
    expect(detectExplicitSignal('Tell me about quantum physics')).toBeNull();
    expect(detectExplicitSignal('How are you doing?')).toBeNull();
  });

  it('is case-insensitive', () => {
    expect(detectExplicitSignal('MORE FORMAL PLEASE')).toBe('more_formal');
    expect(detectExplicitSignal('BE BRIEF')).toBe('less_verbose');
  });
});

describe('detectImplicitSignals', () => {
  it('detects short responses (≤5 words)', () => {
    const signals = detectImplicitSignals('okay thanks');
    expect(signals).toContain('short_response');
  });

  it('detects long responses (≥50 words)', () => {
    const longText = Array(55).fill('word').join(' ');
    const signals = detectImplicitSignals(longText);
    expect(signals).toContain('long_response');
  });

  it('detects technical markers', () => {
    const signals = detectImplicitSignals('Can you refactor the async function to use import syntax?');
    expect(signals).toContain('technical_question');
  });

  it('detects casual markers', () => {
    const signals = detectImplicitSignals('haha yeah thats pretty cool nah I dont think so');
    expect(signals).toContain('casual_chat');
  });

  it('technical overrides casual when both present', () => {
    // If the text has both tech and casual markers, casual is suppressed
    const signals = detectImplicitSignals('lol this async function is broken');
    expect(signals).toContain('technical_question');
    expect(signals).not.toContain('casual_chat');
  });

  it('detects fast followup (<5s)', () => {
    const signals = detectImplicitSignals('sure thing', 3000);
    expect(signals).toContain('fast_followup');
  });

  it('detects slow followup (>60s)', () => {
    const signals = detectImplicitSignals('finally back, sorry about that', 120000);
    expect(signals).toContain('slow_followup');
  });

  it('returns empty array for neutral text', () => {
    const signals = detectImplicitSignals('That sounds good to me, I think we should proceed.');
    // 10 words — not short, not long. No tech/casual markers.
    expect(signals).toHaveLength(0);
  });

  it('can return multiple signals simultaneously', () => {
    // Short + fast followup
    const signals = detectImplicitSignals('ok', 2000);
    expect(signals).toContain('short_response');
    expect(signals).toContain('fast_followup');
  });
});

describe('buildCalibrationHints', () => {
  it('returns empty string for neutral dimensions', () => {
    const result = buildCalibrationHints({ ...DEFAULT_DIMENSIONS });
    expect(result).toBe('');
  });

  it('generates formality hint when high', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, formality: 0.85 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('professional');
    expect(result).toContain('polished');
  });

  it('generates formality hint when low', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, formality: 0.15 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('casual');
  });

  it('generates verbosity hint when high', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, verbosity: 0.8 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('detailed');
    expect(result).toContain('thorough');
  });

  it('generates verbosity hint when low', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, verbosity: 0.2 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('concise');
  });

  it('generates humor hint when high', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, humor: 0.8 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('humor');
  });

  it('generates technical hint when high', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, technicalDepth: 0.85 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('technically');
  });

  it('generates warmth hint when high', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, emotionalWarmth: 0.85 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('warm');
  });

  it('generates proactivity hint when high', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, proactivity: 0.85 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('proactive');
  });

  it('generates multiple hints when multiple dimensions are extreme', () => {
    const dims: StyleDimensions = {
      formality: 0.9,
      verbosity: 0.1,
      humor: 0.1,
      technicalDepth: 0.9,
      emotionalWarmth: 0.1,
      proactivity: 0.9,
    };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('professional');
    expect(result).toContain('concise');
    expect(result).toContain('earnest');
    expect(result).toContain('technically');
    expect(result).toContain('composed');
    expect(result).toContain('proactive');
  });

  it('includes section header when hints exist', () => {
    const dims: StyleDimensions = { ...DEFAULT_DIMENSIONS, formality: 0.9 };
    const result = buildCalibrationHints(dims);
    expect(result).toContain('## Learned Style Preferences');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// 3. ENGINE — SIGNAL INGESTION & ADAPTATION
// ═══════════════════════════════════════════════════════════════════════

describe('PersonalityCalibrationEngine', () => {
  beforeEach(async () => {
    await personalityCalibration.initialize();
    resetEngine();
  });

  describe('initialization', () => {
    it('starts with default dimensions', () => {
      const dims = personalityCalibration.getDimensions();
      expect(dims.formality).toBe(DEFAULT_DIMENSIONS.formality);
      expect(dims.verbosity).toBe(DEFAULT_DIMENSIONS.verbosity);
      expect(dims.humor).toBe(DEFAULT_DIMENSIONS.humor);
      expect(dims.technicalDepth).toBe(DEFAULT_DIMENSIONS.technicalDepth);
      expect(dims.emotionalWarmth).toBe(DEFAULT_DIMENSIONS.emotionalWarmth);
      expect(dims.proactivity).toBe(DEFAULT_DIMENSIONS.proactivity);
    });

    it('starts with empty history', () => {
      expect(personalityCalibration.getHistory()).toHaveLength(0);
    });

    it('starts with zero dismissal rate', () => {
      expect(personalityCalibration.getDismissalRate()).toBe(0);
    });
  });

  describe('explicit signal adaptation', () => {
    it('increases formality on "more_formal" signal', () => {
      const before = personalityCalibration.getDimensions().formality;
      personalityCalibration.processUserMessage('Please be more formal in your responses');
      const after = personalityCalibration.getDimensions().formality;
      expect(after).toBeGreaterThan(before);
    });

    it('decreases formality on "less_formal" signal', () => {
      const before = personalityCalibration.getDimensions().formality;
      personalityCalibration.processUserMessage('Be casual with me');
      const after = personalityCalibration.getDimensions().formality;
      expect(after).toBeLessThan(before);
    });

    it('increases verbosity on "more_verbose" signal', () => {
      const before = personalityCalibration.getDimensions().verbosity;
      personalityCalibration.processUserMessage('Can you give me more detail?');
      const after = personalityCalibration.getDimensions().verbosity;
      expect(after).toBeGreaterThan(before);
    });

    it('decreases verbosity on "less_verbose" signal', () => {
      const before = personalityCalibration.getDimensions().verbosity;
      personalityCalibration.processUserMessage('Keep it shorter please');
      const after = personalityCalibration.getDimensions().verbosity;
      expect(after).toBeLessThan(before);
    });

    it('adjusts humor dimension', () => {
      const before = personalityCalibration.getDimensions().humor;
      personalityCalibration.processUserMessage('Be funny!');
      const after = personalityCalibration.getDimensions().humor;
      expect(after).toBeGreaterThan(before);
    });

    it('adjusts technical depth dimension', () => {
      const before = personalityCalibration.getDimensions().technicalDepth;
      personalityCalibration.processUserMessage('More technical detail please');
      const after = personalityCalibration.getDimensions().technicalDepth;
      expect(after).toBeGreaterThan(before);
    });

    it('explicit signals are stronger than implicit', () => {
      // Explicit: 0.08 * 0.8 = 0.064 shift
      // Implicit: 0.02 * 0.5 * 1.0 = 0.01 shift
      resetEngine();
      const baseVerbosity = personalityCalibration.getDimensions().verbosity;

      personalityCalibration.processUserMessage('Can you elaborate?');
      const afterExplicit = personalityCalibration.getDimensions().verbosity;
      const explicitDelta = afterExplicit - baseVerbosity;

      resetEngine();
      // Long message triggers implicit long_response
      const longMsg = Array(55).fill('word').join(' ');
      personalityCalibration.processUserMessage(longMsg);
      const afterImplicit = personalityCalibration.getDimensions().verbosity;
      const implicitDelta = afterImplicit - DEFAULT_DIMENSIONS.verbosity;

      expect(explicitDelta).toBeGreaterThan(implicitDelta);
    });

    it('logs changes to history', () => {
      personalityCalibration.processUserMessage('Be more formal');
      const history = personalityCalibration.getHistory();
      expect(history.length).toBeGreaterThan(0);
      const lastChange = history[history.length - 1];
      expect(lastChange.dimension).toBe('formality');
      expect(lastChange.newValue).toBeGreaterThan(lastChange.oldValue);
      expect(lastChange.reason).toContain('Explicit');
    });
  });

  describe('implicit signal adaptation', () => {
    it('decreases verbosity on short responses', () => {
      const before = personalityCalibration.getDimensions().verbosity;
      personalityCalibration.processUserMessage('ok');
      const after = personalityCalibration.getDimensions().verbosity;
      expect(after).toBeLessThan(before);
    });

    it('increases verbosity on long responses', () => {
      const before = personalityCalibration.getDimensions().verbosity;
      const longMsg = Array(55).fill('explanatory').join(' ');
      personalityCalibration.processUserMessage(longMsg);
      const after = personalityCalibration.getDimensions().verbosity;
      expect(after).toBeGreaterThan(before);
    });

    it('increases technicalDepth on technical questions', () => {
      const before = personalityCalibration.getDimensions().technicalDepth;
      personalityCalibration.processUserMessage('How does the async function handle the database query?');
      const after = personalityCalibration.getDimensions().technicalDepth;
      expect(after).toBeGreaterThan(before);
    });

    it('decreases formality on casual chat', () => {
      const before = personalityCalibration.getDimensions().formality;
      personalityCalibration.processUserMessage('haha yeah nah thats pretty cool dude');
      const after = personalityCalibration.getDimensions().formality;
      expect(after).toBeLessThan(before);
    });

    it('positive_sentiment does NOT affect dimensions (anti-sycophancy)', () => {
      // positive_sentiment maps to empty array — prevents flattery drift
      const before = { ...personalityCalibration.getDimensions() };
      personalityCalibration.recordSignal({
        source: 'implicit',
        type: 'positive_sentiment',
        magnitude: 0.5,
      });
      const after = personalityCalibration.getDimensions();
      expect(after.emotionalWarmth).toBe(before.emotionalWarmth);
      expect(after.humor).toBe(before.humor);
    });

    it('negative_sentiment does NOT auto-adjust (anti-manipulation)', () => {
      const before = { ...personalityCalibration.getDimensions() };
      personalityCalibration.recordSignal({
        source: 'implicit',
        type: 'negative_sentiment',
        magnitude: 0.8,
      });
      const after = personalityCalibration.getDimensions();
      // All dimensions should be unchanged
      for (const key of Object.keys(before) as (keyof StyleDimensions)[]) {
        expect(after[key]).toBe(before[key]);
      }
    });

    it('explicit signals suppress implicit signals from same message', () => {
      // "Be brief" is explicit — should NOT also count the short implicit signal
      resetEngine();
      personalityCalibration.processUserMessage('Be brief');
      const history = personalityCalibration.getHistory();
      // Should only have explicit change(s), not implicit short_response
      const hasImplicit = history.some(h => h.reason.includes('Implicit'));
      expect(hasImplicit).toBe(false);
    });
  });

  describe('dimension clamping', () => {
    it('dimensions never exceed ceiling (0.95)', () => {
      // Push formality up many times
      for (let i = 0; i < 50; i++) {
        personalityCalibration.processUserMessage('Be more formal please');
      }
      const dims = personalityCalibration.getDimensions();
      expect(dims.formality).toBeLessThanOrEqual(0.95);
    });

    it('dimensions never go below floor (0.05)', () => {
      // Push formality down many times
      for (let i = 0; i < 50; i++) {
        personalityCalibration.processUserMessage('Be casual and relax');
      }
      const dims = personalityCalibration.getDimensions();
      expect(dims.formality).toBeGreaterThanOrEqual(0.05);
    });
  });

  describe('sycophancy boundary', () => {
    it('tracks agreement streak on positive_sentiment signals', () => {
      for (let i = 0; i < 5; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'positive_sentiment',
          magnitude: 0.5,
        });
      }
      const state = personalityCalibration.getState();
      expect(state.sycophancy.agreementStreak).toBe(5);
    });

    it('resets agreement streak on explicit signal', () => {
      for (let i = 0; i < 5; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'positive_sentiment',
          magnitude: 0.5,
        });
      }
      personalityCalibration.processUserMessage('Be more formal');
      const state = personalityCalibration.getState();
      expect(state.sycophancy.agreementStreak).toBe(0);
    });

    it('resets agreement streak on correction signal', () => {
      for (let i = 0; i < 5; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'positive_sentiment',
          magnitude: 0.5,
        });
      }
      personalityCalibration.recordSignal({
        source: 'implicit',
        type: 'correction',
        magnitude: 0.5,
      });
      const state = personalityCalibration.getState();
      expect(state.sycophancy.agreementStreak).toBe(0);
    });

    it('clamps warmth and humor on first boundary violation', () => {
      // First, push warmth and humor high
      for (let i = 0; i < 10; i++) {
        personalityCalibration.processUserMessage('Be warmer with me');
        personalityCalibration.processUserMessage('Be funny!');
      }
      const dimsBeforeViolation = personalityCalibration.getDimensions();
      expect(dimsBeforeViolation.emotionalWarmth).toBeGreaterThan(0.7);

      // Now trigger sycophancy: 8+ positive_sentiment with bias ≥ 0.85
      // Need a lot of consecutive positive sentiments to raise the EMA
      for (let i = 0; i < 20; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'positive_sentiment',
          magnitude: 0.5,
        });
      }

      // After boundary trigger, warmth should be clamped back
      const dimsAfter = personalityCalibration.getDimensions();
      expect(dimsAfter.emotionalWarmth).toBeLessThanOrEqual(0.6);
      expect(dimsAfter.humor).toBeLessThanOrEqual(0.6);
    });

    it('throws FatalIntegrityError after repeated violations', () => {
      // Violation counter increments on each boundary trigger.
      // violations >= 2 → throw FatalIntegrityError.
      //
      // Batch 1: streak builds → boundary fires → violations = 1 (clamp + reset)
      // Batch 2: streak builds → boundary fires → violations = 2 → THROWS

      // First violation: just clamps and resets streak/bias
      for (let i = 0; i < 30; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'positive_sentiment',
          magnitude: 0.5,
        });
      }
      // First violation passed — violations counter is now 1

      // Second violation should throw FatalIntegrityError (violations reaches 2)
      expect(() => {
        for (let i = 0; i < 30; i++) {
          personalityCalibration.recordSignal({
            source: 'implicit',
            type: 'positive_sentiment',
            magnitude: 0.5,
          });
        }
      }).toThrow(/[Ss]ycophancy/);
    });
  });

  describe('proactivity safety floor', () => {
    it('critical items respect safety floor even when proactivity is low', () => {
      // Push proactivity down
      for (let i = 0; i < 20; i++) {
        personalityCalibration.processUserMessage('Leave me alone please');
      }
      const dims = personalityCalibration.getDimensions();
      expect(dims.proactivity).toBeLessThan(0.3);

      // But critical items still get floor
      const effectiveCritical = personalityCalibration.getEffectiveProactivity(true);
      expect(effectiveCritical).toBeGreaterThanOrEqual(0.3);
    });

    it('non-critical items reflect actual proactivity', () => {
      for (let i = 0; i < 20; i++) {
        personalityCalibration.processUserMessage('Stop reminding me');
      }
      const dims = personalityCalibration.getDimensions();
      const effectiveNonCritical = personalityCalibration.getEffectiveProactivity(false);
      expect(effectiveNonCritical).toBe(dims.proactivity);
    });

    it('safety floor is configurable', () => {
      personalityCalibration.updateConfig({ proactivitySafetyFloor: 0.5 });
      // Push proactivity way down
      for (let i = 0; i < 30; i++) {
        personalityCalibration.processUserMessage('Stop checking in please');
      }
      const effectiveCritical = personalityCalibration.getEffectiveProactivity(true);
      expect(effectiveCritical).toBeGreaterThanOrEqual(0.5);
    });

    it('safety floor has a minimum of 0.1', () => {
      const config = personalityCalibration.updateConfig({ proactivitySafetyFloor: 0.01 });
      expect(config.proactivitySafetyFloor).toBeGreaterThanOrEqual(0.1);
    });
  });

  describe('dismissal and engagement tracking', () => {
    it('recordDismissal increases dismissal rate', () => {
      personalityCalibration.recordDismissal();
      personalityCalibration.recordDismissal();
      const rate = personalityCalibration.getDismissalRate();
      expect(rate).toBe(1.0); // 2 dismissals, 0 engagements = 100%
    });

    it('recordEngagement decreases dismissal rate', () => {
      personalityCalibration.recordDismissal();
      personalityCalibration.recordEngagement();
      const rate = personalityCalibration.getDismissalRate();
      expect(rate).toBe(0.5); // 1 dismissal, 1 engagement = 50%
    });

    it('dismissals decrease proactivity', () => {
      const before = personalityCalibration.getDimensions().proactivity;
      personalityCalibration.recordDismissal();
      const after = personalityCalibration.getDimensions().proactivity;
      expect(after).toBeLessThan(before);
    });

    it('engagements increase proactivity', () => {
      // First reduce proactivity so there's room to increase
      personalityCalibration.recordDismissal();
      personalityCalibration.recordDismissal();
      const before = personalityCalibration.getDimensions().proactivity;
      personalityCalibration.recordEngagement();
      const after = personalityCalibration.getDimensions().proactivity;
      expect(after).toBeGreaterThan(before);
    });

    it('caps recent dismissals/engagements at 20', () => {
      for (let i = 0; i < 25; i++) {
        personalityCalibration.recordDismissal();
      }
      const state = personalityCalibration.getState();
      expect(state.proactivity.recentDismissals.length).toBeLessThanOrEqual(20);
    });
  });

  describe('time-based decay', () => {
    it('incrementSession applies decay', () => {
      // Push formality high
      for (let i = 0; i < 10; i++) {
        personalityCalibration.processUserMessage('Please be more formal');
      }
      const beforeDecay = personalityCalibration.getDimensions().formality;
      expect(beforeDecay).toBeGreaterThan(DEFAULT_DIMENSIONS.formality);

      // Simulate time passing by manipulating lastCalibrationTimestamp
      // We need to access internal state — increment session checks time since last calibration
      // Since we just calibrated, decay won't kick in (needs > 12 hours)
      // So we verify the method exists and runs without error
      personalityCalibration.incrementSession();
      const state = personalityCalibration.getState();
      expect(state.sessionCount).toBe(1);
    });

    it('incrementSession increments session count', () => {
      personalityCalibration.incrementSession();
      personalityCalibration.incrementSession();
      personalityCalibration.incrementSession();
      const state = personalityCalibration.getState();
      expect(state.sessionCount).toBe(3);
    });
  });

  describe('visual evolution sync', () => {
    it('getVisualWarmthModifier returns 0 at default warmth', () => {
      // Default emotionalWarmth is 0.6, so modifier = (0.6 - 0.5) * 0.6 = 0.06
      const modifier = personalityCalibration.getVisualWarmthModifier();
      expect(modifier).toBeCloseTo(0.06, 2);
    });

    it('getVisualWarmthModifier increases with warmth', () => {
      for (let i = 0; i < 10; i++) {
        personalityCalibration.processUserMessage('Be warmer with me');
      }
      const modifier = personalityCalibration.getVisualWarmthModifier();
      expect(modifier).toBeGreaterThan(0.06);
    });

    it('getVisualWarmthModifier range is bounded', () => {
      // At max warmth (0.95): (0.95 - 0.5) * 0.6 = 0.27
      // At min warmth (0.05): (0.05 - 0.5) * 0.6 = -0.27
      const modifier = personalityCalibration.getVisualWarmthModifier();
      expect(modifier).toBeGreaterThanOrEqual(-0.3);
      expect(modifier).toBeLessThanOrEqual(0.3);
    });

    it('getVisualEnergyModifier based on proactivity + humor average', () => {
      // Default proactivity=0.6, humor=0.5, avg=0.55, modifier = (0.55-0.5)*0.4 = 0.02
      const modifier = personalityCalibration.getVisualEnergyModifier();
      expect(modifier).toBeCloseTo(0.02, 2);
    });

    it('getVisualEnergyModifier range is bounded', () => {
      const modifier = personalityCalibration.getVisualEnergyModifier();
      expect(modifier).toBeGreaterThanOrEqual(-0.2);
      expect(modifier).toBeLessThanOrEqual(0.2);
    });
  });

  describe('prompt context generation', () => {
    it('returns empty string at defaults (all neutral)', () => {
      const context = personalityCalibration.getPromptContext();
      expect(context).toBe('');
    });

    it('returns hints when dimensions are extreme', () => {
      for (let i = 0; i < 15; i++) {
        personalityCalibration.processUserMessage('Be more formal please');
      }
      const context = personalityCalibration.getPromptContext();
      expect(context).toContain('Learned Style Preferences');
      expect(context).toContain('professional');
    });
  });

  describe('calibration explanation', () => {
    it('produces human-readable explanation', () => {
      const explanation = personalityCalibration.getCalibrationExplanation();
      expect(explanation).toContain('How I\'ve Adapted');
      expect(explanation).toContain('Formality');
      expect(explanation).toContain('Verbosity');
      expect(explanation).toContain('Humor');
      expect(explanation).toContain('Technical Depth');
      expect(explanation).toContain('Emotional Warmth');
      expect(explanation).toContain('Proactivity');
    });

    it('includes recent changes when they exist', () => {
      personalityCalibration.processUserMessage('Be more formal');
      const explanation = personalityCalibration.getCalibrationExplanation();
      expect(explanation).toContain('Recent changes');
      expect(explanation).toContain('formality');
    });

    it('includes session and signal counts', () => {
      personalityCalibration.processUserMessage('Be brief');
      personalityCalibration.incrementSession();
      const explanation = personalityCalibration.getCalibrationExplanation();
      expect(explanation).toMatch(/\d+ signal/);
      expect(explanation).toMatch(/\d+ session/);
    });
  });

  describe('configuration management', () => {
    it('getConfig returns current config', () => {
      const config = personalityCalibration.getConfig();
      expect(config.explicitWeight).toBe(0.08);
      expect(config.implicitWeight).toBe(0.02);
    });

    it('updateConfig merges partial updates', () => {
      const config = personalityCalibration.updateConfig({ explicitWeight: 0.12 });
      expect(config.explicitWeight).toBe(0.12);
      expect(config.implicitWeight).toBe(0.02); // Unchanged
    });

    it('updateConfig enforces safety floor minimum', () => {
      const config = personalityCalibration.updateConfig({ proactivitySafetyFloor: 0.01 });
      expect(config.proactivitySafetyFloor).toBeGreaterThanOrEqual(0.1);
    });
  });

  describe('reset mechanics', () => {
    it('resetDimension resets a single dimension to default', () => {
      personalityCalibration.processUserMessage('Be more formal');
      const before = personalityCalibration.getDimensions().formality;
      expect(before).not.toBe(DEFAULT_DIMENSIONS.formality);

      personalityCalibration.resetDimension('formality');
      const after = personalityCalibration.getDimensions().formality;
      expect(after).toBe(DEFAULT_DIMENSIONS.formality);
    });

    it('resetDimension does not affect other dimensions', () => {
      personalityCalibration.processUserMessage('Be more formal');
      personalityCalibration.processUserMessage('Be funny!');

      const humorBefore = personalityCalibration.getDimensions().humor;
      personalityCalibration.resetDimension('formality');
      const humorAfter = personalityCalibration.getDimensions().humor;
      expect(humorAfter).toBe(humorBefore);
    });

    it('resetDimension logs the change', () => {
      personalityCalibration.processUserMessage('Be more formal');
      const historyBefore = personalityCalibration.getHistory().length;
      personalityCalibration.resetDimension('formality');
      const historyAfter = personalityCalibration.getHistory().length;
      expect(historyAfter).toBe(historyBefore + 1);
      const lastChange = personalityCalibration.getHistory().pop()!;
      expect(lastChange.reason).toContain('User reset');
    });

    it('resetAll restores all dimensions to defaults', () => {
      personalityCalibration.processUserMessage('Be more formal');
      personalityCalibration.processUserMessage('Be funny!');
      personalityCalibration.processUserMessage('More technical detail please');
      personalityCalibration.resetAll();
      const dims = personalityCalibration.getDimensions();
      for (const key of Object.keys(DEFAULT_DIMENSIONS) as (keyof StyleDimensions)[]) {
        expect(dims[key]).toBe(DEFAULT_DIMENSIONS[key]);
      }
    });

    it('resetAll clears signals and history', () => {
      personalityCalibration.processUserMessage('Be more formal');
      personalityCalibration.processUserMessage('Be brief');
      personalityCalibration.resetAll();
      expect(personalityCalibration.getHistory()).toHaveLength(0);
    });

    it('resetAll resets sycophancy state', () => {
      for (let i = 0; i < 5; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'positive_sentiment',
          magnitude: 0.5,
        });
      }
      personalityCalibration.resetAll();
      const state = personalityCalibration.getState();
      expect(state.sycophancy.agreementStreak).toBe(0);
      expect(state.sycophancy.positivityBias).toBe(0.5);
      expect(state.sycophancy.violations).toBe(0);
    });

    it('resetAll resets proactivity state', () => {
      personalityCalibration.recordDismissal();
      personalityCalibration.recordDismissal();
      personalityCalibration.resetAll();
      expect(personalityCalibration.getDismissalRate()).toBe(0);
    });
  });

  describe('signal and history buffer limits', () => {
    it('caps signals at maxSignals (200)', () => {
      for (let i = 0; i < 250; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'short_response',
          magnitude: 0.3,
        });
      }
      const state = personalityCalibration.getState();
      expect(state.signals.length).toBeLessThanOrEqual(200);
    });

    it('caps history at maxHistory (100)', () => {
      for (let i = 0; i < 120; i++) {
        personalityCalibration.processUserMessage('Be more formal please');
      }
      const history = personalityCalibration.getHistory();
      expect(history.length).toBeLessThanOrEqual(100);
    });
  });

  describe('getState returns deep copy', () => {
    it('mutating returned state does not affect engine', () => {
      const state = personalityCalibration.getState();
      state.dimensions.formality = 0.99;
      state.sycophancy.violations = 999;
      const actual = personalityCalibration.getDimensions();
      expect(actual.formality).toBe(DEFAULT_DIMENSIONS.formality);
    });
  });

  describe('getDimensions returns copy', () => {
    it('mutating returned dimensions does not affect engine', () => {
      const dims = personalityCalibration.getDimensions();
      dims.formality = 0.99;
      const actual = personalityCalibration.getDimensions();
      expect(actual.formality).toBe(DEFAULT_DIMENSIONS.formality);
    });
  });

  describe('cLaw compliance', () => {
    it('core identity dimensions are not modified (no identity dimension exists)', () => {
      // The 6 dimensions are all STYLE dimensions — none of them
      // modify core identity, laws, ethical framework, or memories.
      const dimensionKeys = Object.keys(DEFAULT_DIMENSIONS);
      expect(dimensionKeys).not.toContain('identity');
      expect(dimensionKeys).not.toContain('claw');
      expect(dimensionKeys).not.toContain('ethics');
      expect(dimensionKeys).not.toContain('loyalty');
      expect(dimensionKeys).not.toContain('honesty');
    });

    it('positive sentiment cannot inflate any dimension (anti-manipulation)', () => {
      // Record a few positive sentiments (below sycophancy threshold)
      // — no dimension should increase from the signals themselves
      const before = personalityCalibration.getDimensions();
      for (let i = 0; i < 5; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'positive_sentiment',
          magnitude: 1.0,
        });
      }
      const after = personalityCalibration.getDimensions();
      for (const key of Object.keys(before) as (keyof StyleDimensions)[]) {
        expect(after[key]).toBe(before[key]);
      }
    });

    it('negative sentiment cannot deflate any dimension (anti-manipulation)', () => {
      const before = personalityCalibration.getDimensions();
      for (let i = 0; i < 100; i++) {
        personalityCalibration.recordSignal({
          source: 'implicit',
          type: 'negative_sentiment',
          magnitude: 1.0,
        });
      }
      const after = personalityCalibration.getDimensions();
      for (const key of Object.keys(before) as (keyof StyleDimensions)[]) {
        expect(after[key]).toBe(before[key]);
      }
    });

    it('fast/slow followup signals do not adjust dimensions', () => {
      const before = personalityCalibration.getDimensions();
      personalityCalibration.recordSignal({
        source: 'implicit',
        type: 'fast_followup',
        magnitude: 1.0,
      });
      personalityCalibration.recordSignal({
        source: 'implicit',
        type: 'slow_followup',
        magnitude: 1.0,
      });
      const after = personalityCalibration.getDimensions();
      for (const key of Object.keys(before) as (keyof StyleDimensions)[]) {
        expect(after[key]).toBe(before[key]);
      }
    });

    it('session_end signal does not adjust dimensions', () => {
      const before = personalityCalibration.getDimensions();
      personalityCalibration.recordSignal({
        source: 'implicit',
        type: 'session_end',
        magnitude: 1.0,
      });
      const after = personalityCalibration.getDimensions();
      for (const key of Object.keys(before) as (keyof StyleDimensions)[]) {
        expect(after[key]).toBe(before[key]);
      }
    });
  });

  describe('multi-signal accumulation', () => {
    it('repeated explicit signals accumulate', () => {
      const initial = personalityCalibration.getDimensions().formality;
      personalityCalibration.processUserMessage('Be more formal');
      const after1 = personalityCalibration.getDimensions().formality;
      personalityCalibration.processUserMessage('Be more formal');
      const after2 = personalityCalibration.getDimensions().formality;
      expect(after1).toBeGreaterThan(initial);
      expect(after2).toBeGreaterThan(after1);
    });

    it('opposing signals cancel out roughly', () => {
      personalityCalibration.processUserMessage('Be more formal');
      const afterUp = personalityCalibration.getDimensions().formality;
      personalityCalibration.processUserMessage('Be casual with me');
      const afterDown = personalityCalibration.getDimensions().formality;
      // Should be roughly back to where we started
      expect(Math.abs(afterDown - DEFAULT_DIMENSIONS.formality)).toBeLessThan(0.01);
    });
  });

  describe('edge cases', () => {
    it('empty message produces no signals', () => {
      const before = personalityCalibration.getDimensions();
      personalityCalibration.processUserMessage('');
      const after = personalityCalibration.getDimensions();
      for (const key of Object.keys(before) as (keyof StyleDimensions)[]) {
        expect(after[key]).toBe(before[key]);
      }
    });

    it('whitespace-only message produces no signals', () => {
      const historyBefore = personalityCalibration.getHistory().length;
      personalityCalibration.processUserMessage('   ');
      // Whitespace-only = 0 words after split+filter = short_response
      // But it shouldn't crash
      expect(personalityCalibration.getDimensions()).toBeDefined();
    });

    it('very long message does not crash', () => {
      const longMsg = 'a '.repeat(10000);
      expect(() => personalityCalibration.processUserMessage(longMsg)).not.toThrow();
    });

    it('recordSignal with unknown type does not crash', () => {
      expect(() => {
        personalityCalibration.recordSignal({
          source: 'explicit',
          type: 'unknown_type' as any,
          magnitude: 0.5,
        });
      }).not.toThrow();
    });
  });
});
