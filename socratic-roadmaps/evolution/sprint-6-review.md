# Sprint 6 Review -- "The Body"

**Sprint period:** 2026-03-08
**Test count:** 4,205 (Sprint 5 end) -> 4,265 (Sprint 6 end) = **+60 tests**
**Test files:** 122 (all passing)
**Safety Gate:** PASSED on every phase

## Sprint Goal

Give Agent Friday a body: hardware awareness, tiered capability, first-run setup,
and user identity. Sprint 6 builds the infrastructure that maps a user's physical
machine to the right AI configuration, guides them through first-run setup with
model downloads, and gives them a persistent identity. After Sprint 6, Agent Friday
knows what hardware it runs on, selects models accordingly, and remembers who it
is talking to.

## Phase Summary

| Phase | Track | Name | Tests Added | Key Deliverable |
|-------|-------|------|-------------|-----------------|
| O.1 | O | The Pulse | +10 | HardwareProfiler -- GPU/VRAM/RAM/CPU/disk detection |
| O.2 | O | The Measure | +10 | TierRecommender -- pure function mapping hardware to tiers |
| O.3 | O | The Conductor | +10 | ModelOrchestrator -- VRAM-budgeted model fleet management |
| P.1 | P | The Birth | +10 | SetupWizard -- first-run state machine |
| P.2 | P | The Identity | +10 | ProfileManager -- user profiles and preferences |
| P.3 | P | The Awakening | +10 | Integration test: full first-run flow |

**Total: 6 phases, 5 modules built, 1 integration suite, +60 tests**

## Architecture Overview

```
First Launch (no setup-state.json)
    |
    v
SetupWizard.isFirstRun() --> true
    |
    v
SetupWizard.startSetup()
    |
    +---> HardwareProfiler.detect()
    |         |
    |         v
    |     HardwareProfile { gpu, vram, ram, cpu, disk }
    |
    +---> TierRecommender.recommend(profile)
    |         |
    |         v
    |     TierRecommendation { tier, models, diskCheck, upgradePath }
    |
    v
User confirms tier (or skipSetup --> whisper)
    |
    v
SetupWizard.startModelDownload()
    |
    +---> OllamaLifecycle.pullModel(name) [sequential, per model]
    |         |
    |         v
    |     AsyncGenerator<PullProgress> --> download-progress events
    |
    +---> ModelOrchestrator.loadTierModels(tier)
    |         |
    |         v
    |     Warm-up via POST /api/generate { keep_alive: "24h" }
    |     Lazy models (moondream) excluded from eager load
    |
    v
SetupWizard.completeSetup()
    |
    +---> persist setup-state.json { completed: true, tier }
    |
    v
ProfileManager.createProfile({ name })
    |
    +---> persist profiles.json { profiles, activeProfileId }
    |
    v
Ready for first conversation

--- Second Launch ---
SetupWizard.isFirstRun() --> false (setup-state.json exists)
    |
    v
ModelOrchestrator.loadTierModels(savedTier) --> auto-load
    |
    v
ProfileManager.getActiveProfile() --> returning user
```

## Tier System

Agent Friday maps hardware capabilities to five tiers:

| Tier | Min VRAM | Models | Capabilities |
|------|----------|--------|--------------|
| whisper | 0 GB | none | Cloud-only, API-dependent |
| light | 2 GB | nomic-embed-text | Local embeddings, semantic search |
| standard | 6 GB | embed + llama3.1:8b | Local LLM, offline chat |
| full | 8 GB | embed + llama + moondream | Vision, image understanding |
| sovereign | 16 GB | embed + llama + moondream + 70b | Full local reasoning |

The tier system degrades gracefully: a user without a GPU gets whisper tier
and falls back to cloud APIs. A user with 12 GB VRAM gets standard tier with
a clear upgrade path to full tier.

## Architecture Decisions

### 1. Separation of Detection, Recommendation, and Orchestration

Hardware detection (HardwareProfiler) is isolated from tier mapping (TierRecommender)
which is isolated from model lifecycle (ModelOrchestrator). This three-layer separation
means:
- Detection can be tested with mocked system APIs
- Recommendation is pure functions (no mocks needed)
- Orchestration mocks only the Ollama fetch calls

### 2. Pure Functions for Tier Mapping

TierRecommender exports pure functions (`getTier`, `recommend`, `getModelList`,
`canFitModel`) with zero side effects. No singleton, no events, no persistence.
This made it the simplest module to test and the most reliable in production --
the same input always produces the same output.

### 3. State Machine for Setup Flow

SetupWizard uses an explicit state machine (idle -> detecting -> recommending ->
confirming -> downloading -> loading -> complete) rather than a sequence of
promises. This makes the UI straightforward: render a different screen for each
state, subscribe to `setup-state-changed` events for transitions.

### 4. Sequential Model Downloads

Models download one at a time via `for await (const progress of pullModel(name))`.
This is simpler than parallel downloads, avoids bandwidth contention, and gives
clear per-model progress reporting. Since Ollama processes pulls sequentially
server-side anyway, parallelism would only add queue depth.

### 5. File-Based Persistence Over Settings

Both SetupWizard and ProfileManager use direct file I/O rather than the existing
`settingsManager`. The settings system rejects unknown keys, making it unsuitable
for structured data like profiles and setup state. Direct JSON files give full
control over the data shape.

### 6. Soft Delete for Profiles

ProfileManager uses soft delete (`deleted: true` flag) rather than array removal.
This preserves referential integrity -- if a profile ID is referenced in chat
history or voice profiles, the profile record still exists for lookup even after
"deletion."

## Technical Insights

### VRAM Budget as Architecture Constraint

The tier system is fundamentally a VRAM budget allocator. Each model declares its
VRAM requirement in the MODEL_REGISTRY. The tier thresholds map available VRAM to
the set of models that fit. ModelOrchestrator tracks estimated VRAM consumption
and can evict least-recently-used models when the budget is exceeded. This
constraint-based approach avoids OOM crashes and gives users predictable performance.

### Lazy vs Eager Model Loading

Core models (embed, LLM) load eagerly at startup because they are needed for every
interaction. Vision models (moondream) load lazily on first image request because
most interactions are text-only. This reduces steady-state VRAM from ~7.2 GB to
~6.0 GB on a standard setup, leaving more headroom for KV cache.

### Integration Test as Acceptance Criteria

The P.3 integration test serves as the acceptance test for the entire sprint.
It validates the full user journey from fresh install through first conversation,
covering the happy path (standard GPU), skip path (whisper/cloud), full path
(with vision), degraded path (no GPU), and warm start (second launch). If this
test passes, the sprint goal is met.

## What Sprint 6 Proved

Agent Friday now has a body. The hardware awareness and setup pipeline enables:

1. **Hardware detection** -- GPU, VRAM, RAM, CPU, and disk profiled at startup
2. **Tier mapping** -- Hardware capabilities mapped to 5 capability tiers
3. **VRAM budgeting** -- Model fleet managed within GPU memory constraints
4. **First-run experience** -- Guided setup from detection through download to ready
5. **Progress tracking** -- Per-model download progress with event-driven updates
6. **User identity** -- Persistent profiles with preferences and soft delete
7. **Graceful degradation** -- Full functionality at every tier, including cloud-only
8. **Warm start** -- Second launch skips setup, auto-loads saved tier models
9. **Skip path** -- Users can bypass setup entirely and use cloud APIs
10. **Export/import** -- Profile portability via JSON serialization

## Metrics

- **60 new tests** across 6 phases
- **0 regressions** -- all 4,205 pre-existing tests remained green
- **5 source files created** (main process modules)
- **7 test files created** (6 unit + 1 integration)
- **10 integration criteria** validated the full first-run flow
- **2 tracks advanced** (O: Hardware Pipeline, P: Setup + Identity)
- **5 tiers defined** -- whisper, light, standard, full, sovereign
- **4 models registered** -- nomic-embed-text, llama3.1:8b, moondream, llama3.1:70b
