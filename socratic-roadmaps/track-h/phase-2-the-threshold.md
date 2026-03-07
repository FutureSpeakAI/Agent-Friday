# Phase H.2: "The Threshold" — CloudGate Consent System

**Track:** H — The Gatekeeper
**Hermeneutic Focus:** The threshold between local and cloud intelligence is not just technical — it's ethical. Every cloud request exposes the user's thought to an external system. The CloudGate stands at this threshold as guardian: it only opens when the local mind admits its limits, and only with the user's informed consent. This is the philosophical heart of local-first AI — intelligence that respects the boundary between self and other.

## Current State

The existing provider fallback chain in `llmClient` silently falls back to cloud providers when the preferred provider fails. There is no user notification, no consent mechanism, no policy storage. The user has no awareness of when their data leaves the machine.

Settings have `localModelPolicy` (disabled/conservative/all/background) but this controls *selection*, not *escalation*. There's no concept of "local tried and failed, do you want cloud?"

## Architecture Context

```
ConfidenceAssessor says escalate=true
    ↓
CloudGate.requestEscalation(context)
    ↓
┌──────────────────────────────┐
│  Check stored policies:      │
│  ├── Category 'code' → allow │  → Skip dialog, escalate
│  ├── Category 'chat' → deny  │  → Return denied, use local result
│  └── No policy → prompt user │
└──────────────────────────────┘
    ↓ (no policy match)
IPC → Renderer: 'cloud-gate:request-consent'
    ↓
┌──────────────────────────────┐
│  Consent Dialog              │
│  "Friday's local model isn't │
│   confident about this task. │
│   Send to cloud?"            │
│                              │
│  [Allow Once] [Allow Always  │
│   for code tasks] [Deny]     │
└──────────────────────────────┘
    ↓
IPC → Main: 'cloud-gate:consent-response'
    ↓
CloudGate stores policy, returns decision
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `CloudGate` is a singleton with `start()` / `stop()` lifecycle
2. `requestEscalation(context)` returns `Promise<GateDecision>` with `allowed: boolean`
3. When no policy exists, gate emits IPC event `cloud-gate:request-consent` to renderer
4. When policy exists for task category with `allow`, returns `{ allowed: true }` without IPC
5. When policy exists for task category with `deny`, returns `{ allowed: false }` without IPC
6. `setPolicy(category, decision, scope)` stores policy — scope: `once` | `session` | `always`
7. `once` policy is consumed after single use (not reusable)
8. `session` policy persists until `stop()` is called
9. `always` policy persists to disk (survives restart)
10. `getEscalationStats()` returns count of local, escalated, denied decisions

## Socratic Inquiry

**Precedent:** How does the safety pipeline handle tool execution approval? Follow the same pattern of "check policy → prompt if needed → store decision." How do existing IPC handlers in `ipc/` manage request-response patterns?

**Boundary:** The CloudGate makes consent decisions — it doesn't execute the cloud request. It returns a decision object; the caller (router or LLMClient) handles retry. What metadata should the consent dialog show? The task category, a preview of the prompt (without sensitive data), and which cloud provider would be used.

**Constraint Discovery:** The consent dialog must be non-blocking — if the user is idle, the dialog waits. If the user is actively working, delayed consent could feel sluggish. Should there be a timeout? What happens if the renderer isn't available (headless mode)?

**Tension:** Too many consent prompts → user fatigue → they'll just click "allow always" → defeats the purpose. Too few → cloud is never used → complex tasks fail silently. The `allow always for [category]` option balances this: the user makes a considered policy decision once per task type.

**Safety Gate:** Stored policies must be encrypted or at least stored in the vault. "Always allow cloud for code tasks" is a privacy-relevant preference. Policy storage must survive vault re-lock.

**Inversion:** What if the cloud provider is also unavailable (no API key, no internet)? The gate should detect this and skip the consent dialog entirely — no point asking permission for something impossible.

## Boundary Constraints

- **Create:** `src/main/cloud-gate.ts` (~130-170 lines)
- **Create:** `tests/sprint-3/cloud-gate.test.ts` (~10 tests)
- **Create:** `src/main/ipc/cloud-gate-handlers.ts` (~30 lines — IPC registration)
- **Read:** `src/main/safety-pipeline.ts` lines 1-50 (approval pattern precedent)
- **Read:** `src/main/ipc/` directory (IPC handler pattern)

## Files to Read

- `socratic-roadmaps/journals/track-h-phase-1.md` (knowledge chain — confidence signals)
- `socratic-roadmaps/contracts/safety-pipeline.md` (approval flow precedent)
- `socratic-roadmaps/contracts/llm-client.md` (provider interface for cloud check)

## Session Journal Reminder

Before closing, write `journals/track-h-phase-2.md` covering:
- Policy storage approach (memory vs disk vs vault)
- Consent dialog data contract (what info is shown to user)
- Timeout/headless behavior decisions
- How escalation stats connect to the context graph
