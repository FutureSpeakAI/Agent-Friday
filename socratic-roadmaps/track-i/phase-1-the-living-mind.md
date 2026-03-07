# Phase I.1: "The Living Mind" — End-to-End Local Intelligence Integration

**Track:** I — The Living Mind
**Hermeneutic Focus:** Sprint 2 proved the hermeneutic circle of *context* — events flowing through the system and returning enriched. Sprint 3 must prove the hermeneutic circle of *intelligence* — thought flowing through local models, being assessed for quality, and either delivered confidently or escalated with consent. The living mind is an intelligence that knows its own limits and asks for help when needed, while keeping its inner thoughts private by default.

## Current State

After phases G.1-G.3 and H.1-H.3, the system has:
- `OllamaProvider` (native Ollama API access)
- `EmbeddingPipeline` (local embedding generation)
- `OllamaLifecycle` (health monitoring + model management)
- `ConfidenceAssessor` (output quality evaluation)
- `CloudGate` (user consent for cloud escalation)
- Local-first router policy (scoring bonus for local models)

Each module is tested in isolation. No test proves the complete intelligence pipeline works end-to-end.

## Architecture Context

```
The Full Intelligence Circle:

User Action (e.g., asks Friday a question)
    ↓
Context injection (Sprint 2 hermeneutic circle)
    ↓
LLMClient.chat() → Router → OllamaProvider (local 8B)
    ↓
LLMResponse (local)
    ↓
ConfidenceAssessor.assess() → score 0-1
    ↓
├── score >= 0.5 → Deliver to user (fast, private)
│
└── score < 0.5 → CloudGate.requestEscalation()
                    ├── Allowed → Cloud provider retry → Deliver
                    ├── Denied → Deliver local result with caveat
                    └── No renderer → Deliver local result
    ↓
Result → ExecutionDelegate (if tool calls)
    ↓
Feedback → ContextStream → ContextGraph (hermeneutic circle continues)
    ↓
Routing decision logged as system event (observability)
```

## The 10 Criteria

| # | Criterion | What It Proves |
|---|-----------|----------------|
| 1 | Local inference | OllamaProvider.complete() returns valid LLMResponse |
| 2 | Embedding generation | EmbeddingPipeline.embed() returns vectors from Ollama |
| 3 | Health awareness | OllamaLifecycle reports correct running/model state |
| 4 | Confident local | High-confidence local response is delivered without cloud |
| 5 | Gated escalation | Low-confidence triggers CloudGate consent request |
| 6 | Consent allowed | User approval routes to cloud, cloud response delivered |
| 7 | Consent denied | User denial returns local result as-is |
| 8 | Policy storage | "Always allow for code" policy skips future consent prompts |
| 9 | Routing observability | Routing decisions appear in context stream |
| 10 | Full circle | Local inference → confidence → gate → cloud → feedback → stream |

## Socratic Inquiry

**Precedent:** How did Sprint 2's F.1 integration test structure its 10 criteria? Follow the same pattern: real module singletons, mock only Electron IPC and HTTP boundaries. What epoch/timing patterns from F.1 should be reused?

**Boundary:** Integration tests mock HTTP (Ollama API, cloud API) and Electron IPC (consent dialog), but use real singletons for everything else. The intelligence router, confidence assessor, cloud gate, and context stream should all be real instances.

**Constraint Discovery:** The local-first pipeline has more async steps than Sprint 2's context pipeline. The confidence assessment happens after inference completes. The gate consent is an async IPC round-trip. How do we test a pipeline with 3+ async hops without flaky timing?

**Tension:** Comprehensive integration tests are slow and brittle. Focused unit tests are fast but miss integration bugs. The 10 criteria should test the *boundaries* between modules (where bugs actually live), not reimplemented module internals.

**Synthesis:** The Sprint 2 hermeneutic circle (context) and Sprint 3 hermeneutic circle (intelligence) should eventually merge — context informs intelligence, intelligence produces context. This integration test should verify at least one connection point: the routing decision logged to the context stream.

**Safety Gate:** These tests must coexist with all 4,017+ existing tests. Mock patterns must not leak singleton state. Follow the monotonically increasing epoch pattern from F.1.

## Boundary Constraints

- **Create:** `tests/sprint-3/integration/local-intelligence-circle.test.ts` (~200-250 lines)
- **Read:** `tests/sprint-2/integration/hermeneutic-circle.test.ts` (integration test precedent)
- **Read:** All 6 new module files (G.1, G.2, G.3, H.1, H.2, H.3 outputs)

## Files to Read

- `socratic-roadmaps/journals/track-h-phase-3.md` (knowledge chain)
- `tests/sprint-2/integration/hermeneutic-circle.test.ts` (integration test pattern)
- `socratic-roadmaps/evolution/sprint-2-review.md` (singleton state insights)

## Session Journal Reminder

Before closing, write `journals/track-i-phase-1.md` covering:
- Which modules are real vs mocked and why
- Async pipeline testing approach
- How the two hermeneutic circles (context + intelligence) connect
- Any singleton state issues discovered

Then write `evolution/sprint-3-review.md` — this completes the third full sprint.
