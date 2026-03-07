# Phase H.3: "The Inversion" — Local-First Router Policy

**Track:** H — The Gatekeeper
**Hermeneutic Focus:** The name "inversion" is literal: we invert the routing default from cloud-first to local-first. This changes the system's fundamental orientation — intelligence *begins* locally and only reaches outward when the local mind acknowledges its limits. The whole (system architecture) is reshaped by changing the default behavior of one part (the router), demonstrating how a single policy change propagates through the hermeneutic circle.

## Current State

The intelligence router's default flow:
1. `classifyTask()` → TaskProfile
2. `selectModel()` → scores all models → picks highest score
3. Cloud models score higher by default (better capability ratings)
4. `localModelPolicy: 'conservative'` → local only for background/simple tasks
5. Result: cloud is the default for everything non-trivial

After this phase, the flow becomes:
1. `classifyTask()` → TaskProfile
2. Route to local model (Tier 1 or 2) based on task complexity
3. Execute locally → `ConfidenceAssessor.assess()`
4. If confident → deliver result
5. If not confident → `CloudGate.requestEscalation()` → user consent → cloud retry

## Architecture Context

```
BEFORE (cloud-first):                AFTER (local-first):
Request → Router → Cloud API         Request → Router → Local (Ollama)
         ↓ (fallback)                          ↓
         Local                        ConfidenceAssessor
                                               ↓
                                      Score >= threshold → Deliver
                                      Score < threshold → CloudGate
                                               ↓
                                      User consents → Cloud retry
                                      User denies → Deliver local result
```

## Validation Criteria

Write failing tests first, then make them pass:

1. Default `localModelPolicy` is changed from `'conservative'` to `'preferred'` (new value)
2. With policy `'preferred'`, local models get a scoring bonus (+0.3) in `scoreModel()`
3. For `TaskProfile.complexity === 'trivial' | 'simple'`, local model is selected exclusively (no cloud)
4. For `complexity === 'moderate'`, local model is tried first; result goes through `ConfidenceAssessor`
5. For `complexity === 'complex' | 'expert'`, local model is tried but confidence threshold is higher (0.7)
6. When confidence is below threshold, `CloudGate.requestEscalation()` is called
7. When CloudGate returns `{ allowed: true }`, request is retried with cloud provider
8. When CloudGate returns `{ allowed: false }`, local result is returned as-is
9. The complete flow (local → assess → gate → cloud) is wired through `llmClient.chat()` or a wrapper
10. Routing decision is logged to context stream as a `system` event (for observability)

## Socratic Inquiry

**Precedent:** How does `selectModel()` currently compute scores? The local-first bonus should integrate naturally into the existing weighted scoring system. What existing scoring weights need to change?

**Boundary:** This phase modifies *routing logic*, not provider implementations. The OllamaProvider (G.1), ConfidenceAssessor (H.1), and CloudGate (H.2) are consumed, not modified. What's the minimal change to the router that achieves local-first behavior?

**Constraint Discovery:** The retry-with-cloud path doubles latency for escalated requests (local attempt + cloud attempt). Is this acceptable? For `complexity: 'expert'`, should we skip local entirely? Or is the local attempt valuable even when it fails (the confidence score provides signal)?

**Tension:** Adding a scoring bonus for local models is a blunt instrument — it might cause local selection for vision or audio tasks that local models can't handle. The bonus should be gated by capability: only apply when the local model's `strengths[category]` is above a minimum (e.g., 0.4).

**Synthesis:** The routing decision itself is valuable context. Logging "chose local, confidence 0.72, no escalation" or "chose local, confidence 0.31, escalated to cloud, user approved" to the context stream feeds the hermeneutic circle — the system's intelligence decisions become part of its self-understanding.

**Safety Gate:** Changing the default policy must not break existing tests that assume cloud routing. Ensure the router's behavior is policy-driven so tests can set their own policy.

## Boundary Constraints

- **Modify:** `src/main/intelligence-router.ts` (~30-50 lines changed — scoring, policy, flow)
- **Modify:** `src/main/llm-client.ts` (~20-30 lines — confidence+gate integration)
- **Create:** `tests/sprint-3/local-first-routing.test.ts` (~10 tests)
- **Read:** `src/main/confidence-assessor.ts` (H.1 output)
- **Read:** `src/main/cloud-gate.ts` (H.2 output)

## Files to Read

- `socratic-roadmaps/journals/track-h-phase-2.md` (knowledge chain — gate decisions)
- `socratic-roadmaps/contracts/intelligence-router.md` (scoring weights)
- `socratic-roadmaps/contracts/llm-client.md` (chat flow)
- `src/main/intelligence-router.ts` lines 600-720 (selectModel implementation)

## Session Journal Reminder

Before closing, write `journals/track-h-phase-3.md` covering:
- Scoring bonus value and capability gate rationale
- Which complexity levels skip local entirely (if any)
- How the retry flow integrates with llmClient
- Context stream event format for routing decisions
