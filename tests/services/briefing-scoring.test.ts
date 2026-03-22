/**
 * Track A, Phase 2: "The Baton" — BriefingScoringEngine Test Suite
 *
 * Tests the pure scoring function that ranks briefing triggers into
 * priority buckets: 'urgent' | 'relevant' | 'informational'.
 *
 * Follows the intelligence-router scoreModel() precedent:
 *   hard filters → weighted heuristic → threshold buckets
 *
 * Validation Criteria:
 *   1. scoreTrigger returns 'urgent' | 'relevant' | 'informational'
 *   2. Streams >30 min score higher than brief visits
 *   3. Cross-stream entity overlap scores as 'relevant'
 *   4. First session of the day gets morning boost
 *   5. Pure function — no side effects
 *   6. Configurable weights via ScoringConfig
 *   7. No entity overlap → 'informational'
 *   8. Edge cases: empty history, no entities, single-event streams
 */
import { describe, it, expect } from 'vitest';

import {
  scoreTrigger,
  DEFAULT_SCORING_CONFIG,
  type ScoringConfig,
  type ScoringInput,
  type StreamHistoryEntry,
} from '../../src/main/briefing-scoring';
import type { BriefingTrigger } from '../../src/main/briefing-pipeline';

// ── Helpers ───────────────────────────────────────────────────────────

function makeTrigger(overrides: Partial<BriefingTrigger> = {}): BriefingTrigger {
  return {
    id: 'bt-1',
    streamId: 'ws-1',
    streamName: 'Coding VS Code',
    task: 'coding',
    entities: [],
    triggeredAt: Date.now(),
    ...overrides,
  };
}

function makeEntity(type: string, value: string) {
  return {
    type,
    value,
    normalizedValue: value.toLowerCase(),
    firstSeen: Date.now(),
    lastSeen: Date.now(),
    occurrences: 1,
    sourceStreamIds: ['ws-1'],
  };
}

function makeHistoryEntry(overrides: Partial<StreamHistoryEntry> = {}): StreamHistoryEntry {
  const now = Date.now();
  return {
    streamId: 'ws-h1',
    streamName: 'Previous Stream',
    durationMs: 10 * 60 * 1000, // 10 minutes default
    entities: [],
    endedAt: now - 60_000,
    ...overrides,
  };
}

function makeInput(overrides: Partial<ScoringInput> = {}): ScoringInput {
  return {
    trigger: makeTrigger(),
    history: [],
    currentTimeMs: Date.now(),
    isFirstSessionOfDay: false,
    ...overrides,
  };
}

// ── Test Suite ─────────────────────────────────────────────────────────

describe('BriefingScoringEngine — Track A Phase 2', () => {

  // ── Criterion 1: Returns priority bucket ─────────────────────────

  describe('Criterion 1: returns valid priority bucket', () => {
    it('should return one of the three priority levels', () => {
      const result = scoreTrigger(makeInput());
      expect(['urgent', 'relevant', 'informational']).toContain(result.priority);
    });

    it('should include a numeric score', () => {
      const result = scoreTrigger(makeInput());
      expect(result.score).toBeTypeOf('number');
      expect(result.score).toBeGreaterThanOrEqual(0);
      expect(result.score).toBeLessThanOrEqual(1);
    });

    it('should include an explanation string', () => {
      const result = scoreTrigger(makeInput());
      expect(result.explanation).toBeTypeOf('string');
      expect(result.explanation.length).toBeGreaterThan(0);
    });
  });

  // ── Criterion 2: Duration weighting ──────────────────────────────

  describe('Criterion 2: stream duration affects score', () => {
    it('should score a 45-min stream higher than a 2-min stream', () => {
      const longStreamHistory = [
        makeHistoryEntry({
          streamId: 'ws-1',
          durationMs: 45 * 60 * 1000, // 45 minutes
          entities: [makeEntity('project', 'nexus-os')],
        }),
      ];
      const shortStreamHistory = [
        makeHistoryEntry({
          streamId: 'ws-1',
          durationMs: 2 * 60 * 1000, // 2 minutes
          entities: [makeEntity('project', 'nexus-os')],
        }),
      ];

      const trigger = makeTrigger({
        streamId: 'ws-new',
        entities: [makeEntity('project', 'nexus-os')],
      });

      const longResult = scoreTrigger(makeInput({
        trigger,
        history: longStreamHistory,
      }));
      const shortResult = scoreTrigger(makeInput({
        trigger,
        history: shortStreamHistory,
      }));

      expect(longResult.score).toBeGreaterThan(shortResult.score);
    });

    it('should treat streams >30 min as high-engagement context', () => {
      const history = [
        makeHistoryEntry({
          streamId: 'ws-1',
          durationMs: 35 * 60 * 1000, // 35 min
          entities: [makeEntity('project', 'nexus-os')],
        }),
      ];
      const trigger = makeTrigger({
        streamId: 'ws-new',
        entities: [makeEntity('project', 'nexus-os')],
      });

      const result = scoreTrigger(makeInput({ trigger, history }));
      // With entity overlap + long duration, should be at least 'relevant'
      expect(['urgent', 'relevant']).toContain(result.priority);
    });
  });

  // ── Criterion 3: Cross-stream entity overlap ─────────────────────

  describe('Criterion 3: entity overlap boosts priority', () => {
    it('should score higher when trigger entities appear in multiple history streams', () => {
      const sharedEntity = makeEntity('project', 'nexus-os');

      const history = [
        makeHistoryEntry({
          streamId: 'ws-h1',
          entities: [sharedEntity],
        }),
        makeHistoryEntry({
          streamId: 'ws-h2',
          entities: [sharedEntity],
        }),
      ];

      const withOverlap = scoreTrigger(makeInput({
        trigger: makeTrigger({ entities: [sharedEntity] }),
        history,
      }));

      const withoutOverlap = scoreTrigger(makeInput({
        trigger: makeTrigger({ entities: [makeEntity('topic', 'unrelated')] }),
        history,
      }));

      expect(withOverlap.score).toBeGreaterThan(withoutOverlap.score);
    });

    it('should mark cross-cutting concerns as at least relevant', () => {
      const sharedEntity = makeEntity('project', 'nexus-os');
      const history = [
        makeHistoryEntry({ streamId: 'ws-h1', entities: [sharedEntity] }),
        makeHistoryEntry({ streamId: 'ws-h2', entities: [sharedEntity] }),
        makeHistoryEntry({ streamId: 'ws-h3', entities: [sharedEntity] }),
      ];

      const result = scoreTrigger(makeInput({
        trigger: makeTrigger({ entities: [sharedEntity] }),
        history,
      }));

      expect(['urgent', 'relevant']).toContain(result.priority);
    });
  });

  // ── Criterion 4: Morning briefing boost ──────────────────────────

  describe('Criterion 4: first session of day boost', () => {
    it('should score higher for first session of the day', () => {
      const trigger = makeTrigger({ entities: [makeEntity('project', 'nexus-os')] });
      const history = [makeHistoryEntry({ entities: [makeEntity('project', 'nexus-os')] })];

      const morningResult = scoreTrigger(makeInput({
        trigger,
        history,
        isFirstSessionOfDay: true,
      }));

      const afternoonResult = scoreTrigger(makeInput({
        trigger,
        history,
        isFirstSessionOfDay: false,
      }));

      expect(morningResult.score).toBeGreaterThan(afternoonResult.score);
    });
  });

  // ── Criterion 5: Pure function ───────────────────────────────────

  describe('Criterion 5: pure function — no side effects', () => {
    it('should return identical results for identical inputs', () => {
      const input = makeInput({
        trigger: makeTrigger({ entities: [makeEntity('file', 'test.ts')] }),
        history: [makeHistoryEntry({ entities: [makeEntity('file', 'test.ts')] })],
        currentTimeMs: 1000000000000,
      });

      const result1 = scoreTrigger(input);
      const result2 = scoreTrigger(input);

      expect(result1.priority).toBe(result2.priority);
      expect(result1.score).toBe(result2.score);
    });

    it('should not mutate the input', () => {
      const input = makeInput({
        trigger: makeTrigger({ entities: [makeEntity('file', 'a.ts')] }),
        history: [makeHistoryEntry({ entities: [makeEntity('file', 'b.ts')] })],
      });

      const origTrigger = JSON.stringify(input.trigger);
      const origHistory = JSON.stringify(input.history);

      scoreTrigger(input);

      expect(JSON.stringify(input.trigger)).toBe(origTrigger);
      expect(JSON.stringify(input.history)).toBe(origHistory);
    });
  });

  // ── Criterion 6: Configurable weights ────────────────────────────

  describe('Criterion 6: configurable scoring weights', () => {
    it('should use default config when none provided', () => {
      const result = scoreTrigger(makeInput());
      expect(result).toBeDefined();
    });

    it('should allow overriding weights via config', () => {
      // Create asymmetric signals: low duration (~0.33), moderate entity overlap (~0.5)
      // so shifting weight between them produces different total scores.
      const input = makeInput({
        trigger: makeTrigger({
          entities: [
            makeEntity('project', 'nexus-os'),
            makeEntity('file', 'unmatched.ts'), // no match in history
          ],
        }),
        history: [makeHistoryEntry({
          durationMs: 10 * 60 * 1000, // 10 min → signal ≈ 0.33 (vs 30min threshold)
          entities: [makeEntity('project', 'nexus-os')],
        })],
      });

      // Heavy duration weight
      const durationHeavy: ScoringConfig = {
        ...DEFAULT_SCORING_CONFIG,
        durationWeight: 0.9,
        entityOverlapWeight: 0.05,
        morningBoostWeight: 0.05,
      };

      // Heavy entity weight
      const entityHeavy: ScoringConfig = {
        ...DEFAULT_SCORING_CONFIG,
        durationWeight: 0.05,
        entityOverlapWeight: 0.9,
        morningBoostWeight: 0.05,
      };

      const r1 = scoreTrigger(input, durationHeavy);
      const r2 = scoreTrigger(input, entityHeavy);

      // Different configs should produce different scores
      // Duration signal ≈ 0.33, entity signal ≈ 0.375
      // durationHeavy: 0.9*0.33 + 0.05*0.375 ≈ 0.316
      // entityHeavy:   0.05*0.33 + 0.9*0.375 ≈ 0.354
      expect(r1.score).not.toBe(r2.score);
    });

    it('should export DEFAULT_SCORING_CONFIG with valid weights', () => {
      const total = DEFAULT_SCORING_CONFIG.durationWeight
        + DEFAULT_SCORING_CONFIG.entityOverlapWeight
        + DEFAULT_SCORING_CONFIG.morningBoostWeight;
      expect(total).toBeCloseTo(1.0, 1);
    });
  });

  // ── Criterion 7: No overlap → informational ─────────────────────

  describe('Criterion 7: no entity overlap defaults to informational', () => {
    it('should score as informational when trigger has no entity overlap with history', () => {
      const result = scoreTrigger(makeInput({
        trigger: makeTrigger({
          entities: [makeEntity('topic', 'cooking')],
        }),
        history: [
          makeHistoryEntry({
            entities: [makeEntity('project', 'nexus-os')],
          }),
        ],
      }));

      expect(result.priority).toBe('informational');
    });

    it('should score as informational with no entities at all', () => {
      const result = scoreTrigger(makeInput({
        trigger: makeTrigger({ entities: [] }),
        history: [makeHistoryEntry()],
      }));

      expect(result.priority).toBe('informational');
    });
  });

  // ── Criterion 8: Edge cases ──────────────────────────────────────

  describe('Criterion 8: edge case handling', () => {
    it('should handle empty history gracefully', () => {
      const result = scoreTrigger(makeInput({
        trigger: makeTrigger(),
        history: [],
      }));

      expect(['urgent', 'relevant', 'informational']).toContain(result.priority);
    });

    it('should handle trigger with no entities', () => {
      const result = scoreTrigger(makeInput({
        trigger: makeTrigger({ entities: [] }),
        history: [],
      }));

      expect(result.priority).toBe('informational');
    });

    it('should handle history entries with zero duration', () => {
      const result = scoreTrigger(makeInput({
        trigger: makeTrigger({ entities: [makeEntity('file', 'x.ts')] }),
        history: [makeHistoryEntry({
          durationMs: 0,
          entities: [makeEntity('file', 'x.ts')],
        })],
      }));

      expect(['urgent', 'relevant', 'informational']).toContain(result.priority);
    });

    it('should handle single-event streams in history', () => {
      const result = scoreTrigger(makeInput({
        trigger: makeTrigger({ entities: [makeEntity('app', 'Notepad')] }),
        history: [makeHistoryEntry({
          durationMs: 500, // half a second
          entities: [makeEntity('app', 'Notepad')],
        })],
      }));

      expect(['urgent', 'relevant', 'informational']).toContain(result.priority);
    });
  });
});
