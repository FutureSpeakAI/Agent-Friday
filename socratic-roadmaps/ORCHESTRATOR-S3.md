# Orchestrator — Agent Friday v2.2 Sprint 3: "The Local Mind"

## Execution Order

```
G.1 ──→ G.2 ──→ G.3 ──→ H.1 ──→ H.2 ──→ H.3 ──→ I.1
 │       │       │       │       │       │       │
 │       │       │       │       │       │       └─ The Living Mind (integration tests)
 │       │       │       │       │       └─ The Inversion (local-first routing)
 │       │       │       │       └─ The Threshold (cloud consent gate)
 │       │       │       └─ The Mirror (confidence assessment)
 │       │       └─ The Caretaker (Ollama health + lifecycle)
 │       └─ The Inner Voice (embedding pipeline)
 └─ The Native Tongue (Ollama provider)
```

### Rationale for Sequential Order

- **G.1 → G.2 → G.3** strictly sequential: G.1 creates OllamaProvider (HTTP layer), G.2 builds EmbeddingPipeline on top of it, G.3 adds health monitoring that watches both
- **H.1 → H.2 → H.3** strictly sequential: H.1 creates ConfidenceAssessor (evaluates output), H.2 creates CloudGate (consent for escalation), H.3 wires both into the router
- **G before H**: The gatekeeper (H) needs the local spine (G) to exist — you can't assess local model confidence without a local model provider
- **I.1** last: Integration tests verify the entire local-first pipeline — must come after all modules are built and wired

## Dependency Graph

```
                    ┌──────────┐
                    │   G.1    │  OllamaProvider
                    │ Native   │  /api/chat, /api/embed
                    │ Tongue   │  /api/tags
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   G.2    │  EmbeddingPipeline
                    │  Inner   │  Local embed() via
                    │  Voice   │  OllamaProvider
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   G.3    │  OllamaLifecycle
                    │  Care-   │  Health poll, model
                    │  taker   │  discovery, VRAM
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   H.1    │  ConfidenceAssessor
                    │  Mirror  │  Score output quality
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   H.2    │  CloudGate
                    │Threshold │  Consent dialog +
                    │          │  policy engine
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   H.3    │  Router policy flip
                    │Inversion │  Local-first + gated
                    │          │  cloud escalation
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   I.1    │  Full local-first
                    │ Living   │  intelligence circle
                    │  Mind    │  integration tests
                    └──────────┘
```

## Three-Tier Architecture Reference

```
┌─────────────────────────────────────────────────────────┐
│ Tier 1: Always Local, Silent                            │
│ ┌─────────────────────┐  ┌───────────────────────────┐  │
│ │ EmbeddingPipeline   │  │ Task Classification       │  │
│ │ nomic-embed-text    │  │ (existing router logic)   │  │
│ │ ~0.5GB VRAM         │  │ Runs on embeddings        │  │
│ └─────────────────────┘  └───────────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│ Tier 2: Local Workhorse                                 │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ OllamaProvider → llama3.1:8b-instruct-q4_K_M       │ │
│ │ ~5.5GB VRAM Q4 │ Briefings, chat, simple tool use  │ │
│ │ Handles ~90% of daily operations                    │ │
│ └─────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│ Tier 3: Gated Cloud (Consent Required)                  │
│ ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│ │ Confidence   │→ │  CloudGate   │→ │ Cloud Provider│  │
│ │ Assessor     │  │ (consent UI) │  │ (Anthropic/   │  │
│ │ score < 0.5  │  │ allow/deny   │  │  OpenRouter)  │  │
│ └──────────────┘  └──────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────┘

Hardware Target: RTX 4070 (12GB VRAM), 16GB System RAM
VRAM Budget: Tier 1 (~0.5GB) + Tier 2 (~5.5GB) = ~6GB, leaving ~6GB headroom
```

## Launch Prompts

### Phase G.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/03-GAP-MAP.md
3. socratic-roadmaps/track-g/phase-1-the-native-tongue.md
4. socratic-roadmaps/evolution/sprint-2-review.md
5. socratic-roadmaps/contracts/llm-client.md
6. src/main/providers/anthropic-provider.ts

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate (npx tsc --noEmit && npx vitest run).
Write a session journal to socratic-roadmaps/journals/track-g-phase-1.md before closing.
```

### Phase G.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/03-GAP-MAP.md
3. socratic-roadmaps/track-g/phase-2-the-inner-voice.md
4. socratic-roadmaps/journals/track-g-phase-1.md
5. socratic-roadmaps/contracts/ollama-provider.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-g-phase-2.md before closing.
```

### Phase G.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/03-GAP-MAP.md
3. socratic-roadmaps/track-g/phase-3-the-caretaker.md
4. socratic-roadmaps/journals/track-g-phase-2.md
5. socratic-roadmaps/contracts/ollama-provider.md
6. socratic-roadmaps/contracts/embedding-pipeline.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-g-phase-3.md before closing.
```

### Phase H.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/03-GAP-MAP.md
3. socratic-roadmaps/track-h/phase-1-the-mirror.md
4. socratic-roadmaps/journals/track-g-phase-3.md
5. socratic-roadmaps/contracts/llm-client.md
6. socratic-roadmaps/contracts/intelligence-router.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-h-phase-1.md before closing.
```

### Phase H.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/03-GAP-MAP.md
3. socratic-roadmaps/track-h/phase-2-the-threshold.md
4. socratic-roadmaps/journals/track-h-phase-1.md
5. socratic-roadmaps/contracts/confidence-assessor.md
6. socratic-roadmaps/contracts/safety-pipeline.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-h-phase-2.md before closing.
```

### Phase H.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/03-GAP-MAP.md
3. socratic-roadmaps/track-h/phase-3-the-inversion.md
4. socratic-roadmaps/journals/track-h-phase-2.md
5. socratic-roadmaps/contracts/confidence-assessor.md
6. socratic-roadmaps/contracts/cloud-gate.md
7. socratic-roadmaps/contracts/intelligence-router.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-h-phase-3.md before closing.
```

### Phase I.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/03-GAP-MAP.md
3. socratic-roadmaps/track-i/phase-1-the-living-mind.md
4. socratic-roadmaps/journals/track-h-phase-3.md
5. socratic-roadmaps/contracts/ollama-provider.md
6. socratic-roadmaps/contracts/confidence-assessor.md
7. socratic-roadmaps/contracts/cloud-gate.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-i-phase-1.md before closing.
Write evolution/sprint-3-review.md — this completes the third full sprint.
```

## Context Budget Verification

Each launch prompt reads at most 7 files:

| File | Est. Lines |
|------|-----------|
| Methodology (pruned) | ~80 |
| Gap map (focused) | ~80 |
| Phase file | ~80 |
| Previous journal | ~40 |
| Contract 1 | ~30 |
| Contract 2 | ~30 |
| Contract 3 (if needed) | ~30 |
| **Total** | **~370** |

Well within the ~430 line ceiling. Leaves ~300+ lines for code reading and test output.

## Verification Checkpoints

After each phase:
1. `npx tsc --noEmit` — 0 errors
2. `npx vitest run` — all tests pass (baseline: 4,017, grows each phase)
3. New tests added by the phase also pass
4. Git commit checkpoint with descriptive message
5. Session journal written

After all 7 phases:
1. Full test suite green (~4,100+ tests expected)
2. OllamaProvider registered and functional (when Ollama running)
3. EmbeddingPipeline generates vectors locally
4. OllamaLifecycle monitors health and VRAM
5. ConfidenceAssessor evaluates all LLM responses
6. CloudGate blocks cloud requests pending user consent
7. Router defaults to local-first with gated cloud escalation
8. Integration tests verify the full local intelligence circle
9. System works fully without Ollama (graceful degradation)
10. No regressions in Sprint 1-2 test suites
