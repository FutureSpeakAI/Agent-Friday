# Track P Phase 1 -- "The Birth" (SetupWizard)

## Date: 2026-03-08
## Sprint: 6 (Phase 4/6)

## What was built
SetupWizard singleton orchestrating the first-run setup experience for Agent Friday.
State machine drives the flow: idle -> detecting -> recommending -> confirming -> downloading -> loading -> complete.

## Architecture decisions

### File-based persistence over settings system
The existing `settingsManager.setSetting()` rejects unknown keys (`if (!(key in this.settings)) return`), making it hostile to ad hoc additions. Rather than modifying the settings contract, the wizard uses its own JSON file (`setup-state.json`) in `app.getPath('userData')`. This keeps persistence self-contained and testable via simple `fs` mocks.

### State machine over event soup
Each step transition is explicit and sequential. The wizard emits `setup-state-changed` events on every transition, giving the UI a single subscription point rather than a web of per-action listeners. Four event types total: `setup-state-changed`, `download-progress`, `setup-complete`, `setup-error`.

### Download progress tracking
Each model gets a `DownloadProgress` object tracking `pending -> downloading -> complete | failed`. The wizard iterates models sequentially (not parallel) because Ollama's `pullModel` streams progress for one model at a time. Failed downloads are marked and skipped, not fatal.

### skipSetup() for zero-friction onboarding
Users who want to skip setup entirely get the `whisper` tier (cloud-only, no local models). This writes the completion marker immediately and emits `setup-complete`.

## Dependencies consumed
- `HardwareProfiler.getInstance().detect()` -- hardware detection
- `recommend(profile)` -- tier recommendation from hardware profile
- `getModelList(tier)` -- model requirements for a tier
- `OllamaLifecycle.getInstance().pullModel(name)` -- async generator for model downloads
- `ModelOrchestrator.getInstance().loadTierModels(tier)` -- VRAM loading after download

## Files created
- `src/main/setup/setup-wizard.ts` (~230 lines) -- the implementation
- `tests/sprint-6/setup/setup-wizard.test.ts` (~250 lines) -- 10 tests

## Test results
- 10/10 tests passing
- Safety Gate: `tsc --noEmit` clean, `vitest run` 120 files / 4245 tests all green

## Socratic reflection
The wizard is a thin orchestrator -- it owns the state machine and delegates all real work. This is the right shape: it composes HardwareProfiler, TierRecommender, OllamaLifecycle, and ModelOrchestrator without duplicating their logic. The async generator pattern for `pullModel` maps cleanly to progress tracking. The biggest risk is the sequential download approach (one model at a time), but this matches Ollama's actual behavior and keeps progress reporting simple.
