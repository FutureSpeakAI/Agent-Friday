# Phase H.1: "The Mirror" — ConfidenceAssessor

**Track:** H — The Gatekeeper
**Hermeneutic Focus:** A mind that cannot judge the quality of its own thinking is dangerous. The ConfidenceAssessor holds a mirror up to local model output — examining tool call validity, response coherence, and completion signals to determine whether the local model's answer is trustworthy or whether the question exceeds its capacity. This self-reflection is what separates autonomous intelligence from blind execution.

## Current State

The intelligence router scores models *before* inference based on static capability tables. But it never evaluates *after* inference whether the model actually succeeded. A local 8B model might:
- Produce malformed JSON for tool calls
- Hallucinate tool names that don't exist
- Generate incoherent or contradictory responses
- Produce extremely short answers for complex questions
- Loop or repeat itself

None of these are caught. The result goes straight to the user or execution pipeline.

## Architecture Context

```
User Request
    ↓
Intelligence Router → selects local 8B model (Tier 2)
    ↓
OllamaProvider.complete()
    ↓
LLMResponse { content, toolCalls, usage, stopReason }
    ↓
ConfidenceAssessor.assess(request, response)  ← NEW
    ↓
ConfidenceResult { score: 0-1, signals: string[], escalate: boolean }
    ↓
If escalate → CloudGate (H.2) → user consent → cloud retry
If !escalate → deliver result normally
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `ConfidenceAssessor` is a stateless utility (pure functions, no singleton needed)
2. `assess(request, response)` returns `ConfidenceResult` with score 0-1
3. Valid tool calls with matching names → high confidence (> 0.8)
4. Malformed tool call JSON → low confidence (< 0.3), signal: 'malformed-tool-call'
5. Tool call referencing non-existent tool name → low confidence, signal: 'unknown-tool'
6. Response with `stopReason: 'max_tokens'` → medium confidence, signal: 'truncated'
7. Empty content with no tool calls → low confidence, signal: 'empty-response'
8. Response significantly shorter than expected for complexity → medium confidence, signal: 'unexpectedly-brief'
9. `escalate` is true when score < configurable threshold (default 0.5)
10. Threshold is configurable via constructor or parameter

## Socratic Inquiry

**Precedent:** How does `BriefingScoringEngine` (Track A.2) implement weighted scoring? Follow the same pattern: hard filters (disqualifiers) → weighted signals → threshold buckets.

**Boundary:** The assessor evaluates output quality, not model capability. It doesn't decide *which* model to use (that's the router). It answers: "was this output good enough?" Where's the line between "low confidence, retry locally" vs "low confidence, escalate to cloud"?

**Constraint Discovery:** Without logprobs (Ollama doesn't expose them for all models), confidence must be inferred from structural signals. What signals are available? Tool call validity, response length relative to input complexity, stop reason, JSON parsability, and semantic coherence (if embeddings are available from G.2).

**Tension:** Too sensitive → constant cloud escalation (defeats local-first purpose). Too lenient → bad local outputs reach the user. The threshold must be tuned per-task-category: tool-use needs higher confidence than conversational responses.

**Synthesis:** Can the confidence assessor use the embedding pipeline (G.2) to check semantic coherence? If the response embedding is wildly distant from the request embedding, that's a signal. But this adds latency — is it worth it for Tier 2 responses?

**Inversion:** What if the cloud model also produces low confidence? Should there be a "both failed" path? The assessor should be model-agnostic — it evaluates any LLMResponse, not just local ones.

## Boundary Constraints

- **Create:** `src/main/confidence-assessor.ts` (~100-130 lines)
- **Create:** `tests/sprint-3/confidence-assessor.test.ts` (~10 tests)
- **Read:** `src/main/intelligence-router.ts` lines 516-600 (scoring precedent)
- **Read:** `socratic-roadmaps/contracts/intelligence-router.md` (scoring pattern)

## Files to Read

- `socratic-roadmaps/journals/track-g-phase-3.md` (knowledge chain)
- `socratic-roadmaps/contracts/intelligence-router.md` (scoring pattern precedent)
- `socratic-roadmaps/contracts/llm-client.md` (LLMResponse shape)

## Session Journal Reminder

Before closing, write `journals/track-h-phase-1.md` covering:
- Confidence signals chosen and their weights
- Threshold values per task category
- Decision on embedding-based coherence checking
- How this connects to CloudGate (H.2)
