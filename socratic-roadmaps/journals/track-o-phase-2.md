# Track O Phase 2 -- "The Measure" -- TierRecommender

**Date:** 2026-03-08
**Sprint:** 6
**Phase:** O.2

## What Was Built

TierRecommender -- pure functions (no singleton, no state) that map a
HardwareProfile to a tier recommendation. Takes the hardware detection from
O.1 and produces actionable model lists, VRAM budgets, disk checks, and
upgrade path information.

### Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `src/main/hardware/tier-recommender.ts` | ~130 | Pure tier recommendation functions |
| `tests/sprint-6/hardware/tier-recommender.test.ts` | ~220 | 10 validation tests |

### Public API

| Function | Signature | Description |
|----------|-----------|-------------|
| `recommend` | `(profile: HardwareProfile) -> TierRecommendation` | Full recommendation with models, disk check, warnings |
| `getTier` | `(profile: HardwareProfile) -> TierName` | Just the tier name based on VRAM |
| `getModelList` | `(tier: TierName) -> ModelRequirement[]` | Models required for a tier |
| `estimateVRAMUsage` | `(models: ModelRequirement[]) -> number` | Sum VRAM bytes for models |
| `canFitModel` | `(model, profile, loaded?) -> boolean` | Check if a model fits in VRAM budget |
| `getUpgradePath` | `(tier: TierName) -> UpgradePath \| null` | Next tier info or null |

### Contract Types

- `TierName` -- `'whisper' | 'light' | 'standard' | 'full' | 'sovereign'`
- `TierRecommendation` -- tier, models, totalVRAM, totalDisk, diskSufficient, vramHeadroom, upgradePath, warnings
- `ModelRequirement` -- name, vramBytes, diskBytes, purpose, required
- `UpgradePath` -- nextTier, requiredVRAM, requiredDisk, unlocks

### Tier Thresholds

| Tier | Min VRAM (available) | Models |
|------|---------------------|--------|
| whisper | 0 GB | None (CPU Whisper tiny, cloud LLM) |
| light | >= 2 GB | nomic-embed-text |
| standard | >= 6 GB | embed + llama3.1:8b |
| full | >= 8 GB | embed + 8B LLM + moondream |
| sovereign | >= 16 GB | All + llama3.1:70b |

## Architecture Decisions

1. **Pure functions, no singleton** -- stateless input/output design. No events,
   no caching, no side effects. This makes testing trivial and composition easy.
   The HardwareProfiler (O.1) owns state; this module only transforms data.

2. **Descending threshold matching** -- `getTier()` checks thresholds from
   highest to lowest, returning the first (highest) tier the hardware qualifies
   for. This ensures users get the best tier their hardware supports.

3. **Hardcoded model registry** -- model VRAM/disk requirements are constants.
   These represent known Ollama model sizes at quantization levels we target.
   Future phases can make this dynamic, but static values are correct for
   initial setup guidance.

4. **Disk space as a warning, not a blocker** -- `diskSufficient: false` and a
   warning string are emitted when disk space is too low, but the tier is still
   recommended. The UI layer decides whether to block or warn.

5. **vramHeadroom can be negative** -- in theory, if VRAM detection is slightly
   off, headroom could go negative. We report it honestly rather than clamping,
   letting the UI layer decide how to present edge cases.

6. **Upgrade path includes concrete numbers** -- `requiredVRAM` and
   `requiredDisk` give the next tier's total requirements, not the delta.
   The `unlocks` array provides user-facing descriptions of what improves.

7. **Test values adjusted from spec** -- VC-3 uses 7 GB (not 8 GB) to land in
   the standard tier, since 8 GB meets the full tier threshold (>= 8 GB).
   VC-4 uses 8 GB for full tier. This matches the descending threshold logic.

## Validation Results

All 10 validation criteria passed:

| # | Criterion | Result |
|---|-----------|--------|
| 1 | 0 VRAM -> whisper tier (CPU-only) | PASS |
| 2 | 4 GB VRAM -> light tier (embeddings only) | PASS |
| 3 | 7 GB VRAM -> standard tier (embeddings + 8B LLM) | PASS |
| 4 | 8 GB VRAM -> full tier (embeddings + 8B LLM + vision) | PASS |
| 5 | 24 GB VRAM -> sovereign tier (all models) | PASS |
| 6 | getModelList returns correct models with disk sizes | PASS |
| 7 | estimateVRAMUsage sums VRAM bytes accurately | PASS |
| 8 | canFitModel checks VRAM budget with loaded models | PASS |
| 9 | Recommendation flags insufficient disk space with warning | PASS |
| 10 | getUpgradePath returns next tier info or null for sovereign | PASS |

## Safety Gate

| Check | Result |
|-------|--------|
| `npx tsc --noEmit` | 0 errors |
| `npx vitest run` | 4225 passed, 0 failed (118 files) |
