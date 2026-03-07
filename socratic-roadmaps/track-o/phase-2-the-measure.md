# Phase O.2 — The Measure
## TierRecommender: Mapping Hardware to Intelligence

### Hermeneutic Focus
*The system knows its body. Now it must understand what that body is capable of. This phase translates raw hardware specs into actionable recommendations — which models to run, which features to enable, what the user can expect from their specific machine.*

### Current State (Post-O.1)
- HardwareProfiler detects GPU, VRAM, RAM, CPU, disk
- No mapping from hardware to model selection exists
- No tier system is implemented
- Model download sizes and VRAM requirements are known from Sprint 3-5

### Architecture Context
```
TierRecommender (this phase)
├── recommend(profile)       — HardwareProfile → TierRecommendation
├── getTier(profile)         — HardwareProfile → TierName
├── getModelList(tier)       — Tier → specific model names + sizes
├── estimateVRAMUsage(models) — Model list → total VRAM needed
├── canFitModel(model, profile) — Check if model fits available VRAM
└── getUpgradePath(tier)     — What the next tier offers
```

### Validation Criteria (Test-First)
1. 0 VRAM → Whisper tier (CPU-only, cloud LLM)
2. 4 GB VRAM → Light tier (embeddings only)
3. 8 GB VRAM → Standard tier (embeddings + 8B LLM)
4. 12 GB VRAM → Full tier (embeddings + 8B LLM + vision)
5. 24+ GB VRAM → Sovereign tier (all + larger models)
6. `getModelList(tier)` returns specific model names and download sizes
7. `estimateVRAMUsage(models)` sums model VRAM requirements accurately
8. `canFitModel()` checks if adding a model exceeds VRAM budget
9. Recommendation includes disk space check (enough room for model downloads)
10. `getUpgradePath()` describes what the next tier unlocks

### Socratic Inquiry

**Boundary:** *Are tiers rigid categories or a spectrum?*
Rigid tiers with clear boundaries. Users understand "you're at Standard tier" better than "you're at 67% capability." The tier names (Whisper, Light, Standard, Full, Sovereign) give concrete meaning. Within a tier, all features either work or they don't.

**Inversion:** *What if the user has VRAM but not enough disk space?*
Models must be downloaded before loaded. If 12GB VRAM but only 2GB free disk, recommend Light tier with a note: "Free up disk space to unlock Standard tier." Never recommend models that can't be downloaded.

**Constraint Discovery:** *Should we account for VRAM used by the desktop/OS?*
Yes. On Windows, ~0.5-1.5GB VRAM used by the desktop compositor. On Linux with Wayland, similar. Reserve ~1.5GB for system use. A "12GB" card effectively has ~10.5GB available for models.

**Precedent:** *How does the intelligence router already handle model selection?*
The router uses weighted scoring with hard filters (context window, cost, capability). TierRecommender is simpler — pure hardware-based filtering. Its output feeds the router's available model list.

**Synthesis:** *How does the recommendation integrate with the existing system?*
TierRecommender output → ModelOrchestrator (O.3) → which models to load at startup. Also feeds the setup wizard (P.1) UI — "Your machine can run Standard tier. Would you like to download these models?"

### Boundary Constraints
- Creates `src/main/hardware/tier-recommender.ts` (~100-130 lines)
- Creates `tests/sprint-6/hardware/tier-recommender.test.ts`
- Does NOT download or load models (that's O.3 and P.1)
- Pure function: hardware profile → recommendation
- Model VRAM/disk requirements hardcoded as a data table

### Files to Read
1. `src/main/hardware/hardware-profiler.ts` — HardwareProfile type
2. `src/main/intelligence-router.ts` — Model registry pattern

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-o-phase-2.md` before closing.
