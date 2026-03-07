# Phase D.3: "The Feedback Wire" — Connect Execution Delegate to Context Loop

**Track:** D — The Wiring
**Hermeneutic Focus:** Action without reflection is blind. The execution delegate produces results but they vanish — no other subsystem learns from them. The feedback wire closes the action→understanding loop, making the system reflexive: tool results feed back into the context graph, informing future briefings and context injection.

## Current State

`ExecutionDelegate.execute()` returns a `ToolResult` to the caller but never notifies the context system. `LiveContextBridge.feedExecutionResult()` exists and includes a 5-second circuit breaker, but nothing calls it. The `execution-delegate-handlers.ts` IPC layer calls `executionDelegate.execute()` and returns the result but doesn't feed it to the bridge.

## Architecture Context

```
Current:   tool:execute → ExecutionDelegate → ToolResult → renderer (dead end)

Target:    tool:execute → ExecutionDelegate → ToolResult → renderer
                                                    ↓
                                          liveContextBridge.feedExecutionResult()
                                                    ↓
                                          contextStream.push('tool-invoke')
                                                    ↓
                                          contextGraph updates
                                                    ↓
                                          future briefings informed
```

## Validation Criteria

Write failing tests first, then make them pass:

1. After `executionDelegate.execute()` completes, the result is fed to `liveContextBridge.feedExecutionResult()`
2. The feedback call happens for both successful and error results
3. The feedback call happens after the result is returned to the renderer (non-blocking)
4. The circuit breaker suppresses feedback within 5 seconds of a previous feedback
5. The `tool-invoke` event appears in the context stream after execution
6. The feedback does NOT happen for denied tool calls (no execution = no feedback)
7. The feedback does NOT happen for pending tool calls (not yet executed)

## Socratic Inquiry

**Synthesis:** This is the last wire. Once connected, the hermeneutic circle is complete at the infrastructure level: events → understanding → action → feedback → events. How should the system signal that the circle is closed? Logging? A health-check endpoint?

**Inversion:** What if feedback was synchronous and blocking? The renderer would wait for the context graph to update before seeing the tool result. That's wrong — the user sees the result immediately, and the system learns asynchronously.

**Boundary:** Should the feedback wire live in `execution-delegate-handlers.ts` (IPC layer) or in `execution-delegate.ts` (domain layer)? The IPC layer already has the result. Adding it there avoids modifying the domain class.

**Constraint Discovery:** The `feedExecutionResult` method accepts `{ tool_use_id, content, is_error? }` but `ToolResult.content` can be `string | any[]`. How does the bridge handle array content?

**Safety Gate:** The circuit breaker uses `Date.now()` for timing. In tests, can we control time? Use `vi.useFakeTimers()` to test the 5-second cooldown without real delays.

## Boundary Constraints

- **Max new lines:** ~15 (add feedback call in IPC handler, test file)
- **Modify:** `src/main/ipc/execution-delegate-handlers.ts` — add feedback after execute
- **Create:** `tests/sprint-2/feedback-wire.test.ts`
- **Depends on:** D.1 (bridge must be started), D.2 (handler must be registered)

## Files to Read

- `journals/track-d-phase-2.md` (previous phase journal)
- `contracts/execution-delegate.md` (ToolResult shape)
- `contracts/live-context-bridge.md` (feedExecutionResult API)

## Session Journal Reminder

Before closing, write `journals/track-d-phase-3.md` covering:
- Where the feedback wire was added (IPC layer vs domain layer decision)
- How the circuit breaker was tested
- The complete data flow from tool:execute → feedback → context stream
