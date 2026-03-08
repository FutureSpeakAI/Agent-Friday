# Track O Phase 3: ModelOrchestrator -- "The Conductor"

## Summary
Implemented `ModelOrchestrator`, a singleton that coordinates model loading and
unloading within a VRAM budget. This is the fleet manager for Agent Friday's
local AI models -- it decides which models are in GPU memory at any given time,
tracks estimated VRAM consumption, and evicts least-recently-used models when
the budget is exceeded.

## What Was Built
- **`src/main/hardware/model-orchestrator.ts`** (~180 lines)
  - Exported class: `ModelOrchestrator` (singleton)
  - Exported types: `LoadedModel`, `OrchestratorState`
  - Core methods: `loadTierModels()`, `loadModel()`, `unloadModel()`,
    `evictLeastRecent()`, `getLoadedModels()`, `getVRAMUsage()`,
    `canLoadModel()`, `getOrchestratorState()`, `markUsed()`
  - Events: `model-loaded`, `model-unloaded`, `vram-warning`
  - Ollama REST API integration via `fetch` (POST /api/generate with keep_alive)

- **`tests/sprint-6/hardware/model-orchestrator.test.ts`** (10 tests)

## Loading Strategy
| Strategy | Models | Behavior |
|----------|--------|----------|
| Eager | Embeddings, LLM | Loaded at startup via `loadTierModels()` |
| Lazy | Vision (moondream) | Excluded from tier loading, loaded on-demand via `loadModel()` |
| Eviction | Any loaded model | LRU eviction when VRAM budget would be exceeded |

## Design Decisions
1. **Singleton pattern** -- Matches HardwareProfiler and OllamaLifecycle patterns
   already established in the codebase. Only one orchestrator should manage the
   GPU at a time.

2. **Vision models are lazy** -- The `LAZY_MODELS` set excludes moondream from
   eager loading during `loadTierModels('full')`. Vision requests are infrequent
   enough that loading on first use is the right tradeoff vs. consuming VRAM
   at startup for a model that may never be needed.

3. **Estimated VRAM, not actual** -- The orchestrator uses the MODEL_REGISTRY
   vramBytes values for tracking rather than querying nvidia-smi in real time.
   This keeps the implementation simple and deterministic. The `actualVRAMUsage`
   field in `OrchestratorState` is reserved for future nvidia-smi integration
   (returns null for now).

4. **LRU eviction** -- When a new model cannot fit in the remaining VRAM budget,
   the orchestrator evicts the model with the oldest `lastUsedAt` timestamp.
   The `markUsed()` method allows the intelligence router to update timestamps
   on inference, ensuring frequently-used models stay resident.

5. **Ollama keep_alive protocol** -- Loading a model sends
   `POST /api/generate { model, prompt: "", keep_alive: "24h" }` to warm it up.
   Unloading sends `keep_alive: "0"` to immediately release VRAM. Errors are
   silently caught since Ollama may not be running during tests or initial setup.

6. **Model registry population** -- `loadTierModels()` populates an internal
   registry so that `loadModel(name)` can look up VRAM requirements for any
   model in the tier. For models not in the current tier, a fallback searches
   all tiers to find the model definition.

## Test Approach
All 10 tests mock the Ollama REST API via `globalThis.fetch` and mock
`HardwareProfiler` and `tier-recommender` via `vi.hoisted()` + `vi.mock()`.
Key testing challenges solved:
- **LRU timing**: Used `vi.spyOn(Date, 'now')` to control timestamps and
  ensure deterministic eviction ordering.
- **Mock queue exhaustion**: Used `mockReset()` in `beforeEach` to clear
  `mockReturnValueOnce` queues from prior tests that used per-call mocking.
- **Model registry lookup**: Tests that call `loadModel()` for models outside
  the current tier set up `getModelList` to return different results per call.

## Safety Gate
- `npx tsc --noEmit` -- 0 errors
- `npx vitest run` -- 119 test files, 4235 tests, all passing

## Dependencies
- Consumes: `HardwareProfiler` (O.1) for VRAM budget, `tier-recommender` (O.2)
  for model lists
- Consumed by: IntelligenceRouter (for model selection), SetupWizard (P.1, for
  initial model loading), UI state (for VRAM dashboard)
