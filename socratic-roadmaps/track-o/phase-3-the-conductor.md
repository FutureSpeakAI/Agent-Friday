# Phase O.3 — The Conductor
## ModelOrchestrator: Coordinating Model Loading Within VRAM Budget

### Hermeneutic Focus
*The system knows its body and its measure. Now it must conduct — loading the right models at the right time, never exceeding the VRAM budget. This is the intelligence that turns hardware awareness into running models.*

### Current State (Post-O.2)
- HardwareProfiler detects GPU, VRAM, RAM, CPU, disk
- TierRecommender maps hardware to tier with model lists
- No coordination of actual model loading/unloading exists
- OllamaLifecycle (S3) manages Ollama process health
- OllamaProvider (S3) handles individual model inference

### Architecture Context
```
ModelOrchestrator (this phase)
├── loadTierModels(tier)     — Load all models for a tier
├── getLoadedModels()        — List currently loaded models
├── getVRAMUsage()           — Current VRAM consumption estimate
├── canLoadModel(model)      — Check if model fits in remaining VRAM
├── loadModel(model)         — Load single model, evict if needed
├── unloadModel(model)       — Unload specific model
├── evictLeastRecent()       — Free VRAM by unloading LRU model
└── getOrchestratorState()   — Full state snapshot for UI
```

### Validation Criteria (Test-First)
1. `loadTierModels('standard')` loads embeddings + 8B LLM
2. `loadTierModels('light')` loads only embeddings
3. `getLoadedModels()` returns empty array before any loads
4. `getVRAMUsage()` tracks estimated VRAM after model loads
5. `canLoadModel()` returns false when model would exceed budget
6. `loadModel()` triggers `evictLeastRecent()` when VRAM full
7. `unloadModel()` reduces reported VRAM usage
8. `evictLeastRecent()` unloads the model used longest ago
9. Vision model loads on-demand (not at startup) for Full/Sovereign tiers
10. Orchestrator state includes tier, loaded models, VRAM usage, available headroom

### Socratic Inquiry

**Boundary:** *Does ModelOrchestrator talk to Ollama directly or through OllamaProvider?*
Through OllamaProvider for inference, but it needs direct Ollama API access for model management (load/unload). OllamaProvider handles chat/embed requests. ModelOrchestrator handles the fleet — which models are warm, which should be evicted.

**Inversion:** *What if Ollama loads a model outside our control?*
Track what we've loaded. On startup, query Ollama's loaded model list and reconcile. If an unknown model is consuming VRAM, report it in state but don't unload it — the user may have loaded it manually.

**Constraint Discovery:** *How do we track VRAM without direct GPU queries?*
Estimate based on known model sizes from the tier data table. Cross-check with nvidia-smi periodically (every 60s). If estimated and actual diverge by >1GB, log a warning and use the actual value.

**Precedent:** *How does the intelligence router already select models?*
The router uses weighted scoring for capability. ModelOrchestrator is orthogonal — it ensures the models the router might want are actually loaded. Router selects from available models; orchestrator ensures models become available.

**Tension:** *Eager loading vs. lazy loading?*
Eager for the tier's core models (embeddings + LLM at startup). Lazy for expensive optional models (vision loads only when first vision request arrives). This balances startup time against first-use latency.

### Boundary Constraints
- Creates `src/main/hardware/model-orchestrator.ts` (~150-180 lines)
- Creates `tests/sprint-6/hardware/model-orchestrator.test.ts`
- Does NOT start model downloads (that's P.1 SetupWizard)
- Does NOT modify the intelligence router's selection logic
- Assumes models are already downloaded — only manages loading/unloading
- Uses OllamaProvider for health checks, direct Ollama REST for model management

### Files to Read
1. `src/main/hardware/hardware-profiler.ts` — HardwareProfile type
2. `src/main/hardware/tier-recommender.ts` — Tier data + model lists
3. `src/main/providers/ollama-provider.ts` — Existing model interaction
4. `src/main/ollama-lifecycle.ts` — Process management pattern

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-o-phase-3.md` before closing.
