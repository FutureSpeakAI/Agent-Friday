# Track P -- Phase 3: "The Awakening" -- First-Run Integration

**Date:** 2026-03-08
**Phase:** P.3
**Status:** Complete

## What Was Built

Integration test at `tests/sprint-6/integration/the-awakening.test.ts` (~240 lines).

Validates the complete first-run experience end-to-end:
- Fresh install detection (no setup-state.json)
- Hardware detection via HardwareProfiler
- Tier recommendation via TierRecommender
- Model download with progress tracking via OllamaLifecycle.pullModel()
- Model loading via ModelOrchestrator.loadTierModels()
- Profile creation via ProfileManager
- First conversation via mocked Ollama chat endpoint
- Skip setup (whisper/cloud-only) path
- Full tier with vision/TTS models
- Graceful degradation without GPU
- Second launch behavior (wizard does not re-trigger)

## Key Design Decisions

### Mock Boundary: Module Interface Level

Following the vision-circle.test.ts pattern from Sprint 5, all mocks are at module boundaries using vi.hoisted() + vi.mock(). The REAL SetupWizard and ProfileManager classes are imported and tested against mocked dependencies (HardwareProfiler, TierRecommender, OllamaLifecycle, ModelOrchestrator, node:fs, node:crypto, electron).

### No Circular Dependencies

The integration test imports only SetupWizard and ProfileManager as real modules. All their dependencies are mocked before import. This avoids the circular dependency traps that can occur when testing interconnected singletons.

### Deterministic UUIDs

Mocking `node:crypto.randomUUID` ensures profile IDs are predictable in assertions. An incrementing counter pattern (`test-uuid-1`, `test-uuid-2`, ...) provides unique but deterministic IDs across test runs.

### Two Hardware Profiles

Two distinct hardware profiles cover the extremes:
- `standardProfile`: NVIDIA RTX 4070, 12 GB VRAM, 32 GB RAM -- represents a typical local-first user
- `noGpuProfile`: No GPU, 0 VRAM, 16 GB RAM -- represents graceful degradation to cloud-only

### Simulated Second Launch

Test 10 validates the "warm start" path by resetting the SetupWizard singleton, then configuring mocked persistence to return `{ completed: true, tier: 'standard' }`. This proves `isFirstRun()` returns false and the wizard stays idle on subsequent launches.

## Tests (10/10 passing)

1. Fresh install (no setup-state.json) -> isFirstRun() true -> wizard starts
2. Wizard detects hardware -> displays tier recommendation
3. User confirms tier -> model download begins with progress tracking
4. Download completes -> ModelOrchestrator.loadTierModels() succeeds
5. Profile creation -> getActiveProfile() returns valid profile
6. First chat message -> local LLM generates response (mocked Ollama)
7. Skip setup -> whisper tier selected -> cloud-only LLM works
8. Tier with full models -> TTS available after setup
9. Tier without GPU -> all local features degrade gracefully
10. Second launch after setup -> wizard does NOT trigger, models auto-load

## Safety Gate

- `npx tsc --noEmit` -- clean, no errors
- `npx vitest run` -- 4,265 tests passing (122 test files)

## Files Changed

- `tests/sprint-6/integration/the-awakening.test.ts` (new) -- 10 integration tests
- `socratic-roadmaps/journals/track-p-phase-3.md` (new) -- this journal
- `socratic-roadmaps/evolution/sprint-6-review.md` (new) -- Sprint 6 review
