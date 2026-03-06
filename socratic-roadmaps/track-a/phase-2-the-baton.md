# Phase A.2: "The Baton" — Priority Scoring for Briefings

**Track:** A — The Conductor
**Hermeneutic Focus:** Understanding through the whole — how should the system prioritize intelligence when the user's current activity provides the interpretive frame?

## Current State

Phase A.1 built the `BriefingPipeline` that translates work stream changes into triggers. But triggers are unranked — every stream change produces a trigger of equal weight. The intelligence engine generates briefings, but they lack priority based on *what the user is doing right now*.

## Architecture Context

```
BriefingPipeline.trigger ─→ [NEW: BriefingScoringEngine] ─→ scored trigger
                                      ↑
              user activity patterns ──┘
              (stream duration, entity frequency, time-of-day)
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `scoreTrigger(trigger, history)` returns a priority: 'urgent' | 'relevant' | 'informational'
2. Triggers related to streams the user spent >30 minutes in score higher than brief visits
3. Triggers containing entities that appear across multiple streams score as 'relevant' (cross-cutting concern)
4. Triggers during the user's first session of the day score higher (morning briefing boost)
5. `scoreTrigger` is a pure function — no side effects, fully testable with static input
6. Scoring weights are configurable via a `ScoringConfig` object with sensible defaults
7. Triggers with no entity overlap to any recent stream score as 'informational'
8. The scorer handles edge cases: empty history, no entities, single-event streams

## Socratic Inquiry

**Boundary:** Priority scoring must be deterministic and explainable. What inputs define priority? Duration, entity frequency, recency, time-of-day — which matter most, and how do they compose?

**Inversion:** What if all briefings were scored as 'urgent'? The user would ignore them all. What if all were 'informational'? They'd miss critical updates. What distribution of priorities feels right?

**Precedent:** How does `intelligence-router.ts` score models for task routing? The `scoreModel()` function uses weighted criteria (speed, cost, capability). Follow the same pattern — multiple signals, weighted sum, thresholded into buckets.

**Tension:** Accuracy vs. simplicity. A machine-learning-based scorer would be more accurate but opaque. A weighted heuristic is explainable but rougher. Which serves the user better in an OS they live inside?

**Constraint Discovery:** The scoring function must be pure — it takes a trigger and history as input and returns a priority. No database lookups, no async calls. Why is purity essential here?

**Safety Gate:** Can a malicious or malformed trigger cause the scorer to throw? What if entity arrays are empty? What if timestamps are in the future?

## Boundary Constraints

- **Max new lines:** 150 (one file: `src/main/briefing-scoring.ts`)
- **Pure functions only** — no singletons, no state, no I/O
- **No modifications** to briefing-pipeline.ts yet (wiring happens in A.3)
- **No IPC channels** — this is internal scoring logic

## Files to Read

- `journals/track-a-phase-1.md` (previous phase journal)
- `contracts/context-graph.md` (work stream shape)
- `contracts/intelligence-router.md` (scoring precedent)

## Session Journal Reminder

Before closing, write `journals/track-a-phase-2.md` covering:
- Scoring formula and rationale
- Edge cases discovered during testing
- What Phase A.3 needs to know about integrating the scorer
