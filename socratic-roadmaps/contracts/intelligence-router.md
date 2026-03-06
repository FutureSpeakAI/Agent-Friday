## Interface Contract: Intelligence Router (Scoring Only)
**Generated:** 2026-03-06
**Source:** src/main/intelligence-router.ts (lines 516-720)

### Exports (relevant to Track A scoring precedent)
- `classifyTask(params)` — pure function: `({ message, tools?, images? }) → TaskProfile`
- `scoreModel(model, task, config)` — pure function: `(ModelCapability, TaskProfile, RoutingConfig) → ModelScore`
- `estimateRequestCost(model, tokens)` — pure function: cost in USD
- `buildRoutingExplanation(scores, winner)` — pure function: human-readable explanation

### Scoring Pattern (precedent for Track A.2)
`scoreModel()` uses weighted criteria:
1. Hard filters (disqualifiers) — zero score if capability missing
2. Policy enforcement — local model restrictions by category
3. Weighted sum: speed × speedWeight + cost × costWeight + quality × qualityWeight
4. Threshold buckets: score → tier

This pattern should be followed by `BriefingScoringEngine`:
1. Hard filters (e.g., empty trigger → skip)
2. Policy checks (e.g., minimum dwell time)
3. Weighted heuristic (duration, entity overlap, time-of-day)
4. Threshold into priority buckets: 'urgent' | 'relevant' | 'informational'

### Dependencies
- Required by: llm-client (for routing decisions)
- Pattern used by: briefing-scoring (Track A.2)
