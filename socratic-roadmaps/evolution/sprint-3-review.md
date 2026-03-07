# Sprint 3 Review -- "The Living Mind"

**Sprint period:** Sessions across multiple days
**Test count:** 4,017 (Sprint 2 end) -> 4,095 (Sprint 3 end) = **+78 tests**
**Test files:** 105 (all passing)
**Safety Gate:** PASSED on every phase

## Sprint Goal

Build the local-first intelligence pipeline: native Ollama integration,
embedding generation, health monitoring, confidence-based response quality
assessment, consent-gated cloud escalation, and sovereign-first routing.
Agent Friday thinks locally first and only reaches for the cloud when the
user explicitly allows it.

## Phase Summary

| Phase | Track | Name | Tests Added | Key Deliverable |
|-------|-------|------|-------------|-----------------|
| G.1 | G | The Native Tongue | +12 | OllamaProvider -- native /api/chat integration |
| G.2 | G | The Inner Voice | +8 | EmbeddingPipeline -- local vector embeddings via /api/embed |
| G.3 | G | The Caretaker | +12 | OllamaLifecycle -- health monitoring, model awareness, VRAM tracking |
| H.1 | H | The Mirror | +12 | ConfidenceAssessor -- structural signal evaluation (pure function) |
| H.2 | H | The Threshold | +10 | CloudGate -- consent-based cloud escalation with policy storage |
| H.3 | H | The Inversion | +16 | routeLocalFirst() -- local-first routing with preferred policy |
| I.1 | I | The Living Mind | +10 | Integration test: full local intelligence circle (10 criteria) |

**Total: 7 phases, 7 modules built, 1 integration suite, +78 tests**

## Architecture Overview

```
User Request
    |
    v
routeLocalFirst()
    |
    +---> llmClient.complete('local')
    |         |
    |         v
    |     OllamaProvider.complete()
    |         |
    |         v
    |     fetch(/api/chat) --> Ollama
    |
    +---> assessConfidence(request, response, tools)
    |         |
    |         v
    |     ConfidenceResult { score, signals, escalate }
    |
    +---> [if score < threshold]
    |         |
    |         v
    |     CloudGate.requestEscalation(context)
    |         |
    |         +---> Check stored policy
    |         +---> Request user consent (IPC)
    |         +---> Apply policy (once/session/always)
    |         |
    |         v
    |     GateDecision { allowed, reason }
    |
    +---> [if allowed] llmClient.complete('anthropic')
    |
    v
Final LLMResponse (local or cloud)
```

**Parallel subsystems:**
- EmbeddingPipeline: local vector embeddings via /api/embed (nomic-embed-text)
- OllamaLifecycle: health polling (/api/tags + /api/ps), model load events

## Architecture Decisions

### 1. Native Ollama API over OpenAI-Compat Layer
The OllamaProvider talks to Ollama's native /api/chat endpoint rather than
the OpenAI-compatible layer. This gives full access to Ollama-specific
features including native tool calling and model-specific parameters,
without introducing an unnecessary abstraction layer.

### 2. Confidence-Gated Escalation
Instead of always falling back to cloud on error, we assess response
quality using structural signals (empty response, truncation, malformed
tool calls, unexpected brevity). Only responses below a configurable
threshold trigger the escalation path. This prevents unnecessary cloud
requests while catching genuinely broken responses.

### 3. Sovereign-First Consent
CloudGate enforces a strict rule: nothing leaves the machine without
explicit user consent. When no renderer window exists, escalation is
automatically denied. Policies can be scoped to once, session, or always,
and persistent policies are stored via settingsManager.

### 4. Local-First as Default Policy
The intelligence router defaults to "preferred" policy, giving local models
a +0.3 scoring bonus when they have sufficient capability (strength >= 0.4)
for a task category. This means Agent Friday naturally prefers local models
and only considers cloud when local capability is genuinely insufficient.

### 5. Pure Confidence Assessment
assessConfidence() is a pure function with no side effects. It inspects
structural properties of the response (not semantic quality) to produce
a confidence score. This makes it testable, predictable, and composable.

## Technical Insights

### Singleton Reset Pattern
Sprint 3 modules (CloudGate, OllamaLifecycle) use a static resetInstance()
method that stops the instance and nullifies the singleton reference. This
allows clean test isolation without module-level state leakage between tests.

### Confidence Threshold Subtlety
The interaction between response content and confidence scoring revealed
an important design property: a brief response ("Brief." at 6 chars)
scores 0.8 (only -0.2 penalty), while an empty response scores 0.2
(-0.8 penalty). The default 0.5 threshold correctly separates these cases,
ensuring that mediocre responses are delivered while genuinely broken
responses trigger escalation.

### Mock Fetch Architecture
Integration tests mock fetch globally to simulate Ollama HTTP endpoints.
The mock dispatches based on URL path (/api/chat, /api/embed, /api/tags,
/api/ps), returning realistic Ollama-format responses. This validates the
full request/response parsing without requiring a running Ollama instance.

## What Sprint 3 Proved

The local intelligence pipeline is complete. Agent Friday can:

1. **Think locally** -- Generate responses using local Ollama models
2. **Understand meaning** -- Create vector embeddings for semantic operations
3. **Know its state** -- Monitor Ollama health, available models, and VRAM usage
4. **Evaluate quality** -- Assess response confidence using structural signals
5. **Guard boundaries** -- Enforce consent before any data leaves the machine
6. **Route intelligently** -- Prefer local models, escalate only when necessary

The Living Mind is not just a local LLM wrapper -- it is a sovereign
intelligence system that respects user agency at every decision point.

## What Sprint 4 Builds On

Sprint 4 will connect the intelligence pipeline to the context system:
- Context-aware prompting using the hermeneutic circle from Sprint 2
- Tool execution with confidence-assessed results feeding back into context
- Semantic search using EmbeddingPipeline vectors over the context graph
- Voice interface integrating with the local-first routing pipeline

## Metrics

- **78 new tests** across 7 phases
- **0 regressions** -- all 4,017 pre-existing tests remained green
- **7 source files created** (main process modules)
- **7 test files created** (unit + integration)
- **10 integration criteria** validated the full pipeline
- **3 tracks advanced** (G: Ollama, H: Confidence/Gate, I: Integration)
