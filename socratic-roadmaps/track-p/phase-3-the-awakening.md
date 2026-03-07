# Phase P.3 — The Awakening
## Full First-Run Integration: Install → Setup → Converse

### Hermeneutic Focus
*All the parts exist. Now we verify the whole. The Awakening tests the complete first-run experience — from fresh install through hardware detection, tier recommendation, model download, profile creation, to the first conversation. This is the hermeneutic circle completing its final revolution: the whole understood through all its parts.*

### Current State (Post-P.2)
- HardwareProfiler detects GPU, VRAM, RAM, CPU, disk (O.1)
- TierRecommender maps hardware to tiers (O.2)
- ModelOrchestrator coordinates model loading (O.3)
- SetupWizard orchestrates first-run flow (P.1)
- ProfileManager manages user identity (P.2)
- All Sprint 1-5 systems functional

### Architecture Context
```
The Awakening (this phase) — Integration Test Suite
├── Fresh install → setup wizard triggers
├── Hardware detection → tier recommendation displayed
├── User confirms tier → model downloads begin
├── Downloads complete → models loaded
├── Profile created → system personalized
├── First conversation → local LLM responds
├── Voice available (if models present)
├── Vision available (if VRAM sufficient)
└── Graceful degradation paths verified
```

### Validation Criteria (Test-First)
1. Fresh install (no settings) → `isFirstRun()` true → wizard starts
2. Wizard detects hardware → displays tier recommendation
3. User confirms tier → model download begins with progress tracking
4. Download completes → `ModelOrchestrator.loadTierModels()` succeeds
5. Profile creation → `getActiveProfile()` returns valid profile
6. First chat message → local LLM generates response (mocked Ollama)
7. Skip setup → Whisper tier selected → cloud-only LLM works
8. Tier with voice models → TTS available after setup
9. Tier without GPU → all local features degrade gracefully
10. Second launch after setup → wizard does NOT trigger, models auto-load

### Socratic Inquiry

**Boundary:** *Is this phase only integration tests or does it create new code?*
Primarily integration tests (~200-250 lines). May create a thin `FirstRunCoordinator` (~30-50 lines) if wiring between SetupWizard, ModelOrchestrator, and ProfileManager needs orchestration. But prefer testing the existing modules' integration directly.

**Inversion:** *What if the integration test reveals gaps in previous phases?*
Fix forward, not backward. If SetupWizard doesn't emit the right event for ModelOrchestrator, add the missing event in this phase. Integration tests exist precisely to find these seams.

**Constraint Discovery:** *How do we test model downloads without actually downloading?*
Mock Ollama's pull API. Mock whisper.cpp model file existence. The integration test verifies the flow and state transitions, not the actual network operations.

**Precedent:** *How did L.1 (Voice Circle) handle integration testing?*
L.1 mocked all native dependencies (mic, speakers, whisper.cpp) and tested the event flow: hear → think → speak. Same pattern here: mock hardware detection, mock downloads, test the state machine.

**Synthesis:** *What does "The Sovereign Mind is complete" mean concretely?*
All perception (voice, vision), cognition (local LLM, cloud fallback), and self-awareness (hardware, tiers) work together. A user can install, set up, and converse — with the system running primarily on their hardware, cloud used only when consented.

### Boundary Constraints
- Creates `tests/sprint-6/integration/the-awakening.test.ts` (~200-250 lines)
- May create `src/main/setup/first-run-coordinator.ts` (~30-50 lines) if needed
- All external dependencies mocked (Ollama, hardware APIs, file system)
- Tests verify state transitions, not UI rendering
- This is the final phase — writes `evolution/sprint-6-review.md`

### Files to Read
1. `src/main/setup/setup-wizard.ts` — Setup flow
2. `src/main/setup/profile-manager.ts` — Profile creation
3. `src/main/hardware/model-orchestrator.ts` — Model loading
4. `src/main/hardware/hardware-profiler.ts` — Detection
5. `src/main/hardware/tier-recommender.ts` — Tier mapping

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-p-phase-3.md` before closing.
Write `socratic-roadmaps/evolution/sprint-6-review.md` — this completes the sixth and final sprint.
