# Phase P.1 — The Birth
## SetupWizard: First-Run Experience

### Hermeneutic Focus
*The body is known, measured, and conducted. Now the system must be born — guiding a new user through detection, recommendation, model download, and initial configuration. This is the first conversation between human and machine.*

### Current State (Post-O.3)
- HardwareProfiler detects GPU, VRAM, RAM, CPU, disk
- TierRecommender maps hardware to tier with model lists
- ModelOrchestrator coordinates model loading within VRAM budget
- No first-run detection or setup flow exists
- Electron IPC infrastructure exists from S1-S2

### Architecture Context
```
SetupWizard (this phase)
├── isFirstRun()             — Check if setup has been completed
├── getSetupState()          — Current step in the wizard
├── startSetup()             — Begin the setup flow
├── skipSetup()              — Use defaults, mark complete
├── confirmTier(tier)        — User accepts recommended tier
├── startModelDownload()     — Begin downloading tier models
├── getDownloadProgress()    — Track download status
├── completeSetup()          — Mark setup finished, persist
└── resetSetup()             — Re-run setup (settings menu)
```

### Validation Criteria (Test-First)
1. `isFirstRun()` returns true when no setup marker exists
2. `isFirstRun()` returns false after `completeSetup()` called
3. `startSetup()` triggers hardware detection automatically
4. `getSetupState()` progresses through: detect → recommend → confirm → download → complete
5. `confirmTier()` accepts the recommended tier or a user-chosen lower tier
6. `startModelDownload()` initiates downloads for confirmed tier's models
7. `getDownloadProgress()` reports per-model and overall progress
8. `skipSetup()` selects Whisper tier (CPU-only) and marks complete
9. `completeSetup()` persists tier selection and marks first-run done
10. `resetSetup()` clears the marker so next launch triggers wizard again

### Socratic Inquiry

**Boundary:** *Is the setup wizard a main-process service or a renderer UI?*
Both. Main-process `SetupWizard` service orchestrates the flow (detection, downloads, state). Renderer displays progress. Communication via IPC channels: `setup:state`, `setup:progress`, `setup:confirm`. This phase builds the service; UI is a thin layer.

**Inversion:** *What if the user closes the app mid-download?*
Persist download state. On next launch, `isFirstRun()` returns true (setup incomplete), `getSetupState()` returns 'download' with partial progress. Resume downloads, don't restart them.

**Constraint Discovery:** *How do we download models?*
Use Ollama's `ollama pull` for LLM/embedding/vision models. For whisper.cpp models, download from Hugging Face. Track each model's download state independently. If one fails, others continue.

**Precedent:** *How does the existing settings system persist data?*
`settings.get()`/`settings.set()` backed by electron-store. Setup marker: `settings.get('setup.completed')`. Tier selection: `settings.get('setup.tier')`. Download state: `settings.get('setup.downloads')`.

**Tension:** *Voice guidance during setup?*
Tempting but circular — can't use TTS before its model is downloaded. Text-only setup flow. Voice becomes available after setup completes and models are loaded.

### Boundary Constraints
- Creates `src/main/setup/setup-wizard.ts` (~160-200 lines)
- Creates `tests/sprint-6/setup/setup-wizard.test.ts`
- Does NOT create renderer UI components (that's a thin IPC layer)
- Does NOT implement `ollama pull` — wraps existing Ollama CLI
- Download management is sequential per-model, not parallel
- All state persisted via existing settings system

### Files to Read
1. `src/main/hardware/hardware-profiler.ts` — Detection integration
2. `src/main/hardware/tier-recommender.ts` — Tier recommendation
3. `src/main/hardware/model-orchestrator.ts` — Model loading after download
4. `src/main/settings.ts` — Settings persistence pattern

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-p-phase-1.md` before closing.
