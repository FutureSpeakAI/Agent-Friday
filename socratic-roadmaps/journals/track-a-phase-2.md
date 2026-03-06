# Session Journal: Track A, Phase 2 — "The Baton"

**Date:** 2026-03-06
**Tests added:** 19 (total: 3,808 across 80 files)
**New lines:** ~237 (`src/main/briefing-scoring.ts`)

## What Was Built

`scoreTrigger()` — a pure scoring function that ranks briefing triggers into priority buckets (`urgent` | `relevant` | `informational`) using a weighted heuristic over three signals: duration engagement, cross-stream entity overlap, and morning session boost.

### Architecture Decision: Pure Function, No Wiring

Following Phase A.1's journal recommendation (#3: "The scorer should be a **pure function**"), `scoreTrigger(input, config?)` takes explicit inputs and returns deterministic outputs. It has zero imports from singletons, zero state, zero I/O. Phase A.3 (BriefingDelivery) will wire pipeline → scorer → delivery.

This follows the `scoreModel()` pattern from `intelligence-router.ts`: hard filters → weighted heuristic → threshold buckets.

### Key Design Choices

1. **Three-signal weighted sum** — duration (0.4), entity overlap (0.4), morning boost (0.2). The weights are configurable via `ScoringConfig` to allow tuning without code changes.

2. **Duration signal** — finds the longest history stream sharing entities with the trigger, normalizes against `highEngagementMs` (30min default). A 45-min coding session in the same project scores higher than a 2-min alt-tab.

3. **Entity overlap signal** — combines two factors: coverage (what fraction of trigger entities appear in history) and cross-cutting depth (does the entity appear across multiple streams?). Formula: `coverage × (0.5 + 0.5 × crossCuttingScore)`. This rewards entities that are cross-cutting concerns observed across many work streams.

4. **Hard filter: no entities → informational** — triggers without entities can't be scored for relevance, so they short-circuit to `informational` with score 0.

5. **Threshold buckets** — score ≥ 0.7 → `urgent`, score ≥ 0.35 → `relevant`, else `informational`. Thresholds are configurable.

## Bug Found and Fixed

**Signal saturation in weight config test**: The initial test for configurable weights used a 1-hour history entry with entity overlap. Both duration signal (1.0) and entity signal (~1.0) saturated, so shifting weight between them (`0.9*1.0 + 0.05*1.0` vs `0.05*1.0 + 0.9*1.0`) produced identical scores. Fixed by creating asymmetric signals: 10-min duration (signal ≈ 0.33) with partial entity coverage (2 trigger entities, only 1 matching history), producing observably different weighted sums.

## Patterns Established

- **Hermeneutic whole-vs-parts**: Phase A.1 understands individual stream transitions (the *parts*). Phase A.2 interprets triggers in the context of session history (the *whole*), giving each trigger meaning relative to the broader pattern.
- **Asymmetric test fixtures**: When testing configurable weights, ensure the underlying signals produce different magnitudes so weight shifts are observable.
- **ScoringResult.explanation**: Every result carries a human-readable explanation for debugging, following the pattern from intelligence-router diagnostic output.

## What Phase A.3 Should Know

1. `scoreTrigger(input, config?)` is pure — import and call directly.
2. `ScoringInput` requires: `trigger` (BriefingTrigger), `history` (StreamHistoryEntry[]), `currentTimeMs`, `isFirstSessionOfDay`.
3. `StreamHistoryEntry` must be constructed from ContextGraph data — the scorer doesn't query the graph itself.
4. Phase A.3 is responsible for: (a) building `StreamHistoryEntry[]` from graph state, (b) calling `scoreTrigger()`, (c) routing results to `intelligenceEngine.runResearch()`.
5. The `DEFAULT_SCORING_CONFIG` weights sum to 1.0 — custom configs should maintain this invariant.

## Interface Changes

### New Exports (src/main/briefing-scoring.ts)
- `scoreTrigger(input, config?)` — pure scoring function
- `DEFAULT_SCORING_CONFIG` — default weight/threshold configuration
- `BriefingPriority` — type alias `'urgent' | 'relevant' | 'informational'`
- `ScoringResult` — interface `{ priority, score, explanation }`
- `ScoringInput` — interface `{ trigger, history, currentTimeMs, isFirstSessionOfDay }`
- `ScoringConfig` — interface with 6 configurable fields
- `StreamHistoryEntry` — interface `{ streamId, streamName, durationMs, entities, endedAt }`

### No IPC Channels (deferred to Phase A.3)
### No Modifications to Existing Files
