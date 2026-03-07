# Track H, Phase 3: The Inversion -- Local-First Router Policy

## Date: 2026-03-07

## Summary

Inverted the default routing model from cloud-first to local-first. Local models
now receive a +0.3 scoring bonus in the intelligence router when they have
sufficient capability (strength >= 0.4) for a task category. Cloud escalation
requires passing through the ConfidenceAssessor and CloudGate consent system.

## Changes

### src/main/intelligence-router.ts
- Added 'preferred' to localModelPolicy type union
- Added 'preferred' policy case in scoreModel(): +0.3 localBonus when catStrength >= 0.4
- Applied localBonus to weighted composite totalScore
- Changed default localModelPolicy from 'conservative' to 'preferred'
- Updated doc comments to reflect new default and policy option

### src/main/llm-client.ts
- Added imports for assessConfidence and CloudGate
- Added routeLocalFirst() wrapper function that integrates:
  1. Local provider execution
  2. ConfidenceAssessor evaluation
  3. CloudGate escalation gating
  4. Cloud provider retry on approval
- Added exported types: LocalFirstOptions, RoutingEvent
- Added CONFIDENCE_THRESHOLDS by complexity (trivial/simple: 0.3, moderate: 0.5, complex/expert: 0.7)

### tests/sprint-3/local-first-routing.test.ts (NEW)
- 16 tests covering all validation criteria:
  1. Default policy is 'preferred'
  2. +0.3 scoring bonus applied when category strength >= 0.4
  3. No bonus when category strength < 0.4
  4. No complexity restrictions under preferred policy
  5. Local model wins over cloud for trivial tasks
  6. Moderate complexity confidence threshold (0.5)
  7. Complex/expert confidence threshold (0.7)
  8. CloudGate denies when no renderer
  9. CloudGate allows when policy set to allow
  10. CloudGate denies when policy set to deny
  11. End-to-end: local -> assess -> gate allow -> cloud retry
  12. No escalation when confidence is sufficient
  13. Local response returned when gate denies
  14. Routing events have timestamps and metadata
  15. Backward compatibility for existing policies
  16. Cloud models unaffected by preferred policy

## Safety Gate

- tsc: CLEAN (zero errors)
- Tests: 4,085 passed (104 files), up from 4,069 (+16)
- No existing tests broken

## Key Design Decisions

1. **localBonus is additive, not multiplicative**: The +0.3 is added to the
   weighted composite score, not multiplied. This ensures predictable behavior.

2. **Strength threshold (0.4) for bonus**: Prevents weak local models from
   getting a bonus they cannot back up with quality.

3. **routeLocalFirst() is opt-in**: Does not modify existing chat()/complete()
   behavior. Callers explicitly choose local-first routing.

4. **Confidence thresholds by complexity**: Higher-stakes tasks require higher
   confidence to avoid unnecessary cloud escalation.

5. **Event callback pattern**: onRoutingEvent allows callers to log routing
   decisions to the context stream without coupling to a specific logging system.

## Architecture Insight

The Inversion is philosophically significant: Agent Friday now defaults to
keeping computation local. Cloud is the exception, not the rule. This aligns
with the sovereignty-first principle throughout the codebase.