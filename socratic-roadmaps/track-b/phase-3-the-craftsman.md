# Phase B.3: "The Craftsman" — Execution Delegate

**Track:** B — The Sandbox
**Hermeneutic Focus:** Synthesis — the whole understood through all parts working together. The registry catalogs capabilities, the pipeline gates them, and now the craftsman *does the work*. This is where understanding becomes action.

## Current State

Phase B.1 built `ToolRegistry` (tool catalog with safety levels). Phase B.2 built `SafetyPipeline` (approve/deny/pending decisions). Neither actually executes anything. The tool handlers are registered but uncalled.

## Architecture Context

```
LLM ToolCall ─→ SafetyPipeline.evaluate()
                    │
                    ├── approved ──→ [NEW: ExecutionDelegate.execute()] ──→ ToolResult
                    ├── pending ───→ IPC to renderer for confirmation ──→ confirm/deny
                    └── denied ────→ ToolResult with error
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `executionDelegate.execute(toolCall)` runs the full pipeline: registry lookup → safety check → handler invocation
2. For approved (read-only) tools, execution returns a `ToolResult` with the handler's output
3. For pending tools, execution emits `tool:confirm-request` IPC event and waits for `tool:confirm-response`
4. For denied tools, execution returns a `ToolResult` with `error: "Tool execution denied"`
5. Handler errors are caught and returned as `ToolResult` with error, never thrown
6. `tool:execute` IPC handler accepts a `ToolCall` and returns a `ToolResult`
7. `tool:confirm-response` IPC handler accepts `{ decisionId, approved: boolean }`
8. `tool:list-tools` IPC handler returns available tool definitions
9. All IPC inputs are validated with `assertString` / `assertObject` helpers
10. IPC handler registration follows the project's `registerXxxHandlers()` pattern

## Socratic Inquiry

**Synthesis:** Three modules (registry, pipeline, delegate) must compose into one flow. What is the simplest orchestration? Does the delegate call the pipeline, or does the pipeline call the delegate? Who owns the control flow?

**Boundary:** The execution delegate transforms LLM-shaped inputs (ToolCall) into backend-shaped calls (handler functions). What data transformations happen at this boundary? Are ToolCall arguments always valid handler inputs?

**Inversion:** What if the handler throws? What if it hangs for 30 seconds? What if it returns unexpected output? The delegate must be resilient to every failure mode from the handler layer.

**Constraint Discovery:** The confirmation flow crosses the process boundary (main → renderer → main). This means the execution is async and may never complete. How does the delegate handle renderer disconnection?

**Precedent:** How do existing IPC handlers in the project handle async operations? Look at `intelligence-router-handlers.ts` for the pattern of handling long-running operations.

**Safety Gate:** Can a ToolCall be crafted to bypass the safety pipeline? What if `toolCall.name` is modified between the safety check and execution? Use the pipeline's decision, not the original call, for execution.

## Boundary Constraints

- **Max new lines:** 180 (two files: `src/main/execution-delegate.ts` ~110, `src/main/ipc/tool-handlers.ts` ~70)
- **Update** `src/main/ipc/index.ts` to export tool handler registration
- **Update** `src/main/preload.ts` to expose tool IPC channels
- **No renderer components** — confirmation UI is a future Track C concern
- Write interface contract: `contracts/execution-delegate.md` (needed by Track C)

## Files to Read

- `journals/track-b-phase-2.md` (previous phase journal — safety decision flow)
- `contracts/tool-registry.md` (resolve API)
- `contracts/safety-pipeline.md` (evaluate/confirm/deny API)
- `contracts/llm-client.md` (ToolCall, ToolResult shapes)

## Session Journal Reminder

Before closing, write `journals/track-b-phase-3.md` covering:
- The full execute() flow as built
- IPC channel names and payload shapes
- How confirmation timeout works across the IPC boundary
- Write `contracts/execution-delegate.md` for Track C to consume
