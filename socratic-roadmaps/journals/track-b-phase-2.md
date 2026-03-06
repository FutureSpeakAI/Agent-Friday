## Session Journal: Track B, Phase 2 — "The Guardrails"
**Date:** 2026-03-06
**Commit:** (pending)

### What Was Built
`SafetyPipeline` — the gate between LLM tool calls and actual execution. Sits downstream of the ToolRegistry (B.1) and upstream of the ExecutionDelegate (B.3).

### Decision Flow
```
LLM emits ToolCall
  → evaluate() checks tool registry for safety level
    → read-only   → auto-approve (immediate)
    → write       → pending (user confirmation prompt)
    → destructive → pending (WARNING + explicit confirmation)
    → unknown     → denied (fail-closed)
```

Pending decisions carry a 60-second TTL via `setTimeout`. If the user doesn't confirm or deny within that window, the decision auto-denies. This is the fail-closed principle — ambiguity defaults to safety.

### Key Design Choices
1. **Separate from execution**: SafetyPipeline only makes approve/deny/pending decisions. It does NOT execute tools. Phase B.3 (ExecutionDelegate) handles that after approval.
2. **Decision IDs**: Sequential `sd-1`, `sd-2`, etc. for easy lookup and UI binding.
3. **Timer cleanup**: `confirm()` and `deny()` both clear the expiry timer via `clearTimer()`. The timer map prevents memory leaks from abandoned decisions.
4. **Policy inspection**: `getPolicy()` returns the current policy shape for UI display — which levels auto-approve, which need confirmation, and the timeout duration.

### Signals & Weights
No weighted scoring here — binary classification based on safety level. The scoring complexity lives in the tool registry's `safetyLevel` assignment (a human-authored classification, not a computed one).

### What Surprised Me
The simplicity. After the multi-signal weighted scoring of Phase A.2, this module is refreshingly straightforward — a switch statement with timeouts. The complexity budget went to correctness guarantees (fail-closed, timer cleanup, decision immutability after resolution) rather than algorithmic sophistication.

### Test Coverage
18 tests across 11 validation criteria:
- Auto-approve for read-only tools
- Pending status for write/destructive with appropriate messages
- Deny for unknown tools
- Confirm/deny lifecycle with timer cleanup
- Timeout auto-denial (fail-closed)
- Decision lookup and policy inspection
- Singleton export

### Handoff Notes for Phase A.3
Phase A.3 ("The Performance" — BriefingDelivery + IPC) bridges the scoring engine to the renderer. It needs to:
- Accept `ScoringResult` from Phase A.2 and format it for IPC delivery
- Register IPC handlers in the main process
- Expose through preload contextBridge
- The safety pipeline (this phase) provides the gating pattern that execution-delegate (B.3) will follow

### Socratic Reflection
*"What is the worst that could happen?"* — This is the question every `evaluate()` call implicitly answers. The safety pipeline embodies the precautionary principle: when in doubt, ask the user. The 60-second timeout ensures that "ask and forget" doesn't become "silently approve."
