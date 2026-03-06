# Phase B.2: "The Guardrails" — Safety Pipeline

**Track:** B — The Sandbox
**Hermeneutic Focus:** Understanding through the whole — the system's *ethics*. What must never happen? The safety pipeline is the conscience that prevents the hands from doing harm. Its design derives from the whole system's responsibility to the user.

## Current State

Phase B.1 built the `ToolRegistry` with safety-classified tool definitions. Tools are cataloged as `read-only`, `write`, or `destructive`. But classification alone doesn't prevent execution — a gate is needed between "LLM requests action" and "action executes."

## Architecture Context

```
LLM ToolCall ─→ [NEW: SafetyPipeline]
                    ├── check tool exists (registry lookup)
                    ├── check safety level
                    ├── read-only → auto-approve
                    ├── write → require user confirmation
                    ├── destructive → require explicit user confirmation + reason
                    └── emit decision: { approved, denied, pending-confirmation }
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `safetyPipeline.evaluate(toolCall)` returns a `SafetyDecision` with `status: 'approved' | 'denied' | 'pending'`
2. Read-only tools (safetyLevel: 'read-only') are auto-approved
3. Write tools (safetyLevel: 'write') return `status: 'pending'` with a confirmation prompt
4. Destructive tools (safetyLevel: 'destructive') return `status: 'pending'` with a warning message
5. Unknown tool names return `status: 'denied'` with reason "Unknown tool"
6. `safetyPipeline.confirm(decisionId)` upgrades a pending decision to approved
7. `safetyPipeline.deny(decisionId)` upgrades a pending decision to denied
8. Pending decisions expire after 60 seconds and auto-deny
9. `safetyPipeline.getPolicy()` returns current safety policy for inspection
10. Safety decisions include the original tool call for audit logging
11. `safetyPipeline` is a singleton exported as `safetyPipeline`

## Socratic Inquiry

**Inversion:** What if the safety pipeline didn't exist? The LLM could delete files, kill processes, or modify system settings without asking. What's the worst-case scenario for each tool in the registry? That worst case defines the safety level.

**Boundary:** Where does the safety pipeline's authority begin and end? It gates execution, but does it also sanitize inputs? Validate parameters? Or is input validation the tool handler's responsibility?

**Precedent:** How do other systems handle tool-use safety? ChatGPT's code interpreter runs in a sandbox. Claude's computer use requires explicit confirmation. What pattern fits an *OS-level* tool executor?

**Tension:** Security vs. usability. Requiring confirmation for every action makes the system unusable. Auto-approving everything makes it dangerous. The safety level classification from B.1 is the compromise — but is the three-level scheme sufficient?

**Constraint Discovery:** Pending decisions need a unique ID for confirmation/denial. They also need a timeout. How are pending decisions stored? In-memory map with TTL? What happens if the renderer crashes while a decision is pending?

**Safety Gate:** Can the safety pipeline itself be bypassed? Is there any code path that could call a tool handler directly, skipping the pipeline? How do you make the pipeline mandatory?

## Boundary Constraints

- **Max new lines:** 200 (one file: `src/main/safety-pipeline.ts`)
- **No actual execution** — this phase only makes decisions, doesn't invoke tool handlers
- **No IPC channels yet** — confirmation flow is main-process only (IPC added in B.3)
- **Import from** `tool-registry.ts` for safety level lookups

## Files to Read

- `journals/track-b-phase-1.md` (previous phase journal — tool safety classifications)
- `contracts/tool-registry.md` (tool lookup API)
- `contracts/llm-client.md` (ToolCall shape)

## Session Journal Reminder

Before closing, write `journals/track-b-phase-2.md` covering:
- Safety decision flow and timeout behavior
- How pending decisions are tracked
- What Phase B.3 needs to wire up confirmation via IPC
- Write `contracts/safety-pipeline.md` for Track C to reference
