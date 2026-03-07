# Socratic Forge — Master Plan of Plans
## Agent Friday v2.2: The Sovereign Mind

> *"The hermeneutic circle is not a methodological circle, but describes*
> *an element of the ontological structure of understanding."*
> — Hans-Georg Gadamer, Truth and Method

### The Vision

Agent Friday becomes a sovereign intelligence — thinking, hearing, speaking,
and seeing — running primarily on the user's own hardware. Cloud providers
exist as a gated escape hatch for complex reasoning, never as a dependency.

### The Hermeneutic Architecture

Understanding flows in circles at every level:

```
Sprint Level:    S3 ──→ S4 ──→ S5 ──→ S6 ──→ (whole understood through parts)
                  │      │      │      │
Track Level:     G,H,I  J,K,L  M,N    O,P    (parts understood through whole)
                  │      │      │      │
Phase Level:     Socratic Questions ←──→ Failing Tests ←──→ Passing Code
                  │
Method Level:    Read Context → Write Tests → Ask Questions → Build → Journal
```

Each sprint reveals the next sprint's true requirements. Each phase journal
feeds forward into the next phase's "What Exists" section. The circle never
closes — it spirals upward.

### Sprint Map

```
S1 "The Foundation"     ✅ COMPLETE   Tracks A-C (Core OS infrastructure)
S2 "The Awakening"      ✅ COMPLETE   Tracks D-F (Intelligence + Memory + Safety)
S3 "The Local Mind"     📋 PLANNED    Tracks G-I (Ollama + Embeddings + Gating)
S4 "The Voice"          📋 PLANNED    Tracks J-L (Whisper STT + Kokoro TTS)
S5 "The Eyes"           📋 PLANNED    Tracks M-N (Moondream Vision)
S6 "The Body"           📋 PLANNED    Tracks O-P (Hardware Detection + Setup)
```

### The Sovereign-First Principle

```
┌──────────────────────────────────────────────────────────┐
│                    ALWAYS LOCAL                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │ Whisper  │ │  Kokoro  │ │ Nomic    │ │ Moondream │  │
│  │ STT      │ │ TTS      │ │ Embed    │ │ Vision    │  │
│  │ (CPU)    │ │ (CPU)    │ │ (0.5GB)  │ │ (1.2GB)   │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
├──────────────────────────────────────────────────────────┤
│                   LOCAL WORKHORSE                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Llama 3.1 8B Q4 — Chat, Briefings, Tool Use     │   │
│  │ ~5.5GB VRAM — Handles ~90% of daily operations   │   │
│  └──────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────┤
│              GATED CLOUD (Consent Required)               │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐   │
│  │Confidence│ →  │CloudGate │ →  │ Anthropic /       │   │
│  │Assessor  │    │(consent) │    │ OpenRouter        │   │
│  └──────────┘    └──────────┘    └──────────────────┘   │
└──────────────────────────────────────────────────────────┘
Hardware Target: RTX 4070 (12GB VRAM), 16GB System RAM
Full Stack: Embed(0.5) + LLM(5.5) + Vision(1.2) = 7.2GB, ~4.8GB headroom
Voice: CPU only — 0GB VRAM consumed
```

### Context Budget Protocol

Every sub-agent session respects a hard ceiling:

| Component | Max Lines | Purpose |
|-----------|-----------|---------|
| Methodology | ~80 | Socratic method reference |
| Gap map | ~80 | Sprint-level "What Exists" |
| Phase file | ~80 | Socratic questions + validation |
| Previous journal | ~40 | Hermeneutic circle forward-feed |
| Contracts (1-3) | ~30 each | Interface boundaries |
| **Total read** | **~370** | Leaves ~300+ for code + output |
| **Hard ceiling** | **~430** | Never exceeded |

### Fractal Discovery Pattern

The same Socratic cycle applies at every scale:

```
System Level:   What exists? → What's missing? → What must we build?
Sprint Level:   Gap Map → Track definition → Phase sequencing
Track Level:    Phase files → Socratic questions → Validation criteria
Phase Level:    Read context → Write failing tests → Make them pass
Test Level:     What should happen? → What actually happens? → Why the gap?
```

Each level's output becomes the next level's input. The gap map feeds the
orchestrator, the orchestrator feeds the launch prompts, the launch prompts
feed the sub-agent, the sub-agent writes journals that feed the next phase.

### Sprint Execution Order

```
S3: G.1 → G.2 → G.3 → H.1 → H.2 → H.3 → I.1     (7 phases)
S4: J.1 → J.2 → J.3 → K.1 → K.2 → K.3 → L.1     (7 phases)
S5: M.1 → M.2 → M.3 → N.1                          (4 phases)
S6: O.1 → O.2 → O.3 → P.1 → P.2 → P.3             (6 phases)
                                              Total: 24 phases
```

### Cross-Sprint Dependencies

```
S3 produces:
  → OllamaProvider        (used by S4 for Whisper model loading)
  → EmbeddingPipeline     (used by S5 for vision-text similarity)
  → OllamaLifecycle       (used by S6 for hardware profiling)
  → CloudGate             (used by S4/S5 for gated cloud fallback)
  → ConfidenceAssessor    (used by S4/S5 for output quality checks)

S4 produces:
  → WhisperProvider       (used by S6 for setup wizard voice commands)
  → TTSEngine             (used by S6 for setup wizard spoken guidance)
  → AudioCapture          (standalone — no downstream dependencies)

S5 produces:
  → VisionProvider        (used by S6 for hardware detection screenshots)
  → ScreenContext          (standalone — feeds UI understanding)

S6 produces:
  → HardwareProfiler      (terminal — configures all prior modules)
  → SetupWizard           (terminal — first-run experience)
  → TierRecommender       (terminal — maps hardware to model selection)
```

### Verification Protocol

After each sprint:
1. `npx tsc --noEmit` — 0 type errors
2. `npx vitest run` — all tests pass, no regressions
3. New tests added by the sprint also pass
4. Git commit checkpoint with descriptive message
5. Sprint review journal written
6. Next sprint's gap map reflects actual state (hermeneutic update)

Test count trajectory:
- S1 complete: ~2,000 tests
- S2 complete: ~4,017 tests
- S3 target:   ~4,100+ tests
- S4 target:   ~4,250+ tests
- S5 target:   ~4,350+ tests
- S6 target:   ~4,500+ tests

### The Hermeneutic Checkpoints

Between each sprint, before launching the next orchestrator:

1. **Read the whole**: Review the sprint review journal
2. **Question the parts**: Do interface contracts still match reality?
3. **Update the circle**: Revise the next gap map if needed
4. **Feed forward**: Previous journal becomes next phase's context
5. **Never assume**: The gap map written before execution may need revision

This is the hermeneutic circle in practice — each sprint execution
reveals truths that refine the understanding of what comes next.

### File Index

| Sprint | Gap Map | Orchestrator | Tracks |
|--------|---------|-------------|--------|
| S3 | `03-GAP-MAP.md` | `ORCHESTRATOR-S3.md` | G, H, I |
| S4 | `04-GAP-MAP.md` | `ORCHESTRATOR-S4.md` | J, K, L |
| S5 | `05-GAP-MAP.md` | `ORCHESTRATOR-S5.md` | M, N |
| S6 | `06-GAP-MAP.md` | `ORCHESTRATOR-S6.md` | O, P |

### Contract Index

| Module | Contract | Sprint | Track |
|--------|----------|--------|-------|
| OllamaProvider | `contracts/ollama-provider.md` | S3 | G.1 |
| EmbeddingPipeline | `contracts/embedding-pipeline.md` | S3 | G.2 |
| OllamaLifecycle | `contracts/ollama-lifecycle.md` | S3 | G.3 |
| ConfidenceAssessor | `contracts/confidence-assessor.md` | S3 | H.1 |
| CloudGate | `contracts/cloud-gate.md` | S3 | H.2 |
| WhisperProvider | `contracts/whisper-provider.md` | S4 | J.1 |
| AudioCapture | `contracts/audio-capture.md` | S4 | J.2 |
| TranscriptionPipeline | `contracts/transcription-pipeline.md` | S4 | J.3 |
| TTSEngine | `contracts/tts-engine.md` | S4 | K.1 |
| VoiceProfileManager | `contracts/voice-profile.md` | S4 | K.2 |
| VoiceCircle | `contracts/voice-circle.md` | S4 | K.3 |
| VisionProvider | `contracts/vision-provider.md` | S5 | M.1 |
| ScreenContext | `contracts/screen-context.md` | S5 | M.2 |
| ImageUnderstanding | `contracts/image-understanding.md` | S5 | M.3 |
| VisionCircle | `contracts/vision-circle.md` | S5 | N.1 |
| HardwareProfiler | `contracts/hardware-profiler.md` | S6 | O.1 |
| TierRecommender | `contracts/tier-recommender.md` | S6 | O.2 |
| ModelOrchestrator | `contracts/model-orchestrator.md` | S6 | O.3 |
| SetupWizard | `contracts/setup-wizard.md` | S6 | P.1 |
| ProfileManager | `contracts/profile-manager.md` | S6 | P.2 |
