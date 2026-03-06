/**
 * Track A, Phase 2: "The Baton" — Priority Scoring for Briefing Triggers
 *
 * Pure scoring functions that rank briefing triggers into priority buckets.
 * Follows the intelligence-router's scoreModel() precedent:
 *   1. Hard filters (empty trigger → informational)
 *   2. Weighted heuristic (duration, entity overlap, morning boost)
 *   3. Threshold into priority buckets: 'urgent' | 'relevant' | 'informational'
 *
 * This module is entirely pure — no singletons, no state, no I/O.
 * Every function takes explicit inputs and returns deterministic outputs.
 *
 * Hermeneutic note: This module understands through the *whole* — it
 * interprets individual triggers in the context of the user's session
 * history, giving each trigger meaning relative to the broader pattern.
 */

import type { BriefingTrigger } from './briefing-pipeline';
import type { EntityRef } from './context-graph';

// ── Types ─────────────────────────────────────────────────────────────

export type BriefingPriority = 'urgent' | 'relevant' | 'informational';

export interface ScoringResult {
  priority: BriefingPriority;
  /** Normalized score 0-1 (higher = more important) */
  score: number;
  /** Human-readable explanation for debugging */
  explanation: string;
}

export interface StreamHistoryEntry {
  streamId: string;
  streamName: string;
  /** How long the user was in this stream */
  durationMs: number;
  /** Entities observed during this stream */
  entities: EntityRef[];
  /** When this stream ended */
  endedAt: number;
}

export interface ScoringInput {
  trigger: BriefingTrigger;
  history: StreamHistoryEntry[];
  currentTimeMs: number;
  isFirstSessionOfDay: boolean;
}

export interface ScoringConfig {
  /** Weight for stream duration signal (0-1) */
  durationWeight: number;
  /** Weight for cross-stream entity overlap signal (0-1) */
  entityOverlapWeight: number;
  /** Weight for morning/first-session boost (0-1) */
  morningBoostWeight: number;
  /** Duration threshold for "high engagement" (ms) */
  highEngagementMs: number;
  /** Score threshold: above this → 'urgent' */
  urgentThreshold: number;
  /** Score threshold: above this → 'relevant' */
  relevantThreshold: number;
}

// ── Defaults ──────────────────────────────────────────────────────────

export const DEFAULT_SCORING_CONFIG: ScoringConfig = {
  durationWeight: 0.4,
  entityOverlapWeight: 0.4,
  morningBoostWeight: 0.2,
  highEngagementMs: 30 * 60 * 1000, // 30 minutes
  urgentThreshold: 0.7,
  relevantThreshold: 0.35,
};

// ── Scoring Function ──────────────────────────────────────────────────

/**
 * Score a briefing trigger against session history to determine priority.
 *
 * Pure function — identical inputs always produce identical outputs,
 * no side effects, no mutations.
 */
export function scoreTrigger(
  input: ScoringInput,
  config: ScoringConfig = DEFAULT_SCORING_CONFIG,
): ScoringResult {
  const { trigger, history, isFirstSessionOfDay } = input;
  const reasons: string[] = [];

  // ── Hard filter: no entities = informational ──────────────────────
  if (trigger.entities.length === 0) {
    return {
      priority: 'informational',
      score: 0,
      explanation: 'No entities in trigger — cannot assess relevance',
    };
  }

  // ── Signal 1: Duration — how long was the user in related streams? ─
  const durationSignal = computeDurationSignal(trigger, history, config, reasons);

  // ── Signal 2: Entity overlap — cross-cutting concern detection ────
  const overlapSignal = computeEntityOverlapSignal(trigger, history, reasons);

  // ── Signal 3: Morning boost — first session of the day ────────────
  const morningSignal = isFirstSessionOfDay ? 1.0 : 0.0;
  if (isFirstSessionOfDay) {
    reasons.push('morning session boost');
  }

  // ── Weighted sum ──────────────────────────────────────────────────
  const rawScore =
    durationSignal * config.durationWeight +
    overlapSignal * config.entityOverlapWeight +
    morningSignal * config.morningBoostWeight;

  // Clamp to [0, 1]
  const score = Math.min(1, Math.max(0, rawScore));

  // ── Threshold into buckets ────────────────────────────────────────
  let priority: BriefingPriority;
  if (score >= config.urgentThreshold) {
    priority = 'urgent';
  } else if (score >= config.relevantThreshold) {
    priority = 'relevant';
  } else {
    priority = 'informational';
  }

  return {
    priority,
    score,
    explanation: reasons.length > 0
      ? `${priority}: ${reasons.join(', ')}`
      : `${priority}: baseline score`,
  };
}

// ── Signal Computation ────────────────────────────────────────────────

/**
 * Duration signal: How long was the user engaged with streams
 * that share entities with the trigger?
 *
 * Returns 0-1 where 1 means the user spent significant time
 * in related context.
 */
function computeDurationSignal(
  trigger: BriefingTrigger,
  history: StreamHistoryEntry[],
  config: ScoringConfig,
  reasons: string[],
): number {
  if (history.length === 0) return 0;

  const triggerEntityValues = new Set(
    trigger.entities.map(e => e.normalizedValue),
  );

  // Find history entries with overlapping entities
  let maxRelatedDuration = 0;
  for (const entry of history) {
    const hasOverlap = entry.entities.some(
      e => triggerEntityValues.has(e.normalizedValue),
    );
    if (hasOverlap && entry.durationMs > maxRelatedDuration) {
      maxRelatedDuration = entry.durationMs;
    }
  }

  if (maxRelatedDuration === 0) return 0;

  // Normalize: 0 at 0 min, 1 at highEngagementMs, capped at 1
  const signal = Math.min(1, maxRelatedDuration / config.highEngagementMs);

  if (maxRelatedDuration >= config.highEngagementMs) {
    reasons.push(`high engagement (${Math.round(maxRelatedDuration / 60_000)}min in related stream)`);
  } else {
    reasons.push(`${Math.round(maxRelatedDuration / 60_000)}min in related stream`);
  }

  return signal;
}

/**
 * Entity overlap signal: How many of the trigger's entities appear
 * across multiple history streams?
 *
 * Returns 0-1 where 1 means all trigger entities are cross-cutting
 * concerns observed across many streams.
 */
function computeEntityOverlapSignal(
  trigger: BriefingTrigger,
  history: StreamHistoryEntry[],
  reasons: string[],
): number {
  if (history.length === 0 || trigger.entities.length === 0) return 0;

  const triggerEntityValues = trigger.entities.map(e => e.normalizedValue);

  // For each trigger entity, count how many history streams contain it
  let totalOverlapStreams = 0;
  let matchingEntities = 0;

  for (const entityValue of triggerEntityValues) {
    const streamCount = history.filter(entry =>
      entry.entities.some(e => e.normalizedValue === entityValue),
    ).length;

    if (streamCount > 0) {
      matchingEntities++;
      totalOverlapStreams += streamCount;
    }
  }

  if (matchingEntities === 0) return 0;

  // Cross-cutting: entity appears in multiple streams → stronger signal
  // Normalize by: (avg streams per matching entity) / history.length
  const avgStreamsPerEntity = totalOverlapStreams / matchingEntities;
  const crossCuttingScore = Math.min(1, avgStreamsPerEntity / Math.max(1, history.length));

  // Entity coverage: what fraction of trigger entities matched
  const coverageScore = matchingEntities / trigger.entities.length;

  // Combined: coverage × cross-cutting depth
  const signal = coverageScore * (0.5 + 0.5 * crossCuttingScore);

  if (matchingEntities > 0) {
    reasons.push(`${matchingEntities}/${trigger.entities.length} entities overlap across ${totalOverlapStreams} stream ref(s)`);
  }

  return Math.min(1, signal);
}
