# Phase F.1: "The Proof" — End-to-End Integration Testing

**Track:** F — The Circle
**Hermeneutic Focus:** The hermeneutic circle is complete only when every part has been understood through the whole and the whole through every part. This phase tests the circle itself — not individual modules, but the *flow* between them. If context doesn't flow from OS events through intelligence and back to the user, the system is a collection of parts, not a whole.

## Current State

Sprint 1 tested each module in isolation (3,945 unit tests). Sprint 2 Track D wired the modules together. Track E connected apps. But no test verifies the full circle:

```
User action → ContextStream → ContextGraph → BriefingPipeline →
BriefingScoringEngine → BriefingDelivery → ContextInjector →
LiveContextBridge → App (useAppContext) → Tool execution →
ExecutionDelegate → feedExecutionResult → ContextStream (loop)
```

## Validation Criteria

Write integration tests that verify:

1. **Context flow**: Pushing a context event through `contextStream.push()` eventually appears in `contextGraph.getActiveStream()`
2. **Briefing flow**: A high-scoring context pattern triggers a briefing via `briefingDelivery`
3. **Injection flow**: The `ContextInjector` produces different `AppContext` for different app IDs given the same global state
4. **Bridge flow**: `liveContextBridge.start()` causes `app-context:update` IPC events when context changes
5. **Execution flow**: A tool call through `executionDelegate.execute()` produces a `ToolResult`
6. **Feedback flow**: After execution, `feedExecutionResult()` pushes a `tool-invoke` event to the context stream
7. **Circuit breaker**: Rapid execution feedback is throttled (5-second cooldown verified)
8. **Full circle**: An event pushed to the stream eventually influences the AppContext returned by the injector
9. **Shutdown safety**: After `liveContextBridge.stop()`, no more IPC events are sent
10. **Error isolation**: A failing tool handler doesn't break the context pipeline

## Socratic Inquiry

**Synthesis (the deepest):** Two sprints, 6 tracks, and the system comes full circle. What does it mean for the circle to be "complete"? Not that every possible flow works, but that the *essential* flow — context → intelligence → action → reflection — is continuous and unbroken.

**Boundary:** Integration tests are slower than unit tests. How many integration tests are needed? What's the minimal set that proves the circle works without duplicating unit test coverage?

**Inversion:** What if the integration tests pass but the actual UI doesn't work? Integration tests use mocked IPC (no real Electron window). What can't they verify? Identify the gap between integration test coverage and real system behavior.

**Safety Gate:** Integration tests must not break existing unit tests. They should run in a separate test file/directory. Verify that `npx vitest run` still completes in under 60 seconds.

**Constraint Discovery:** Some modules use `Date.now()` for timing (circuit breaker, debounce). Integration tests need `vi.useFakeTimers()` to control time. Does this conflict with any real-timer-dependent modules?

## Boundary Constraints

- **Max new lines:** ~200 (integration test suite)
- **Create:** `tests/sprint-2/integration/hermeneutic-circle.test.ts`
- **Read only:** All Sprint 1 modules (no modifications)
- **Depends on:** D.1 + D.2 + D.3 (all wiring complete)

## Files to Read

- `journals/track-e-phase-3.md` (previous phase)
- `contracts/live-context-bridge.md`
- `contracts/execution-delegate.md`
- `contracts/context-graph.md`

## Session Journal Reminder

Before closing, write `journals/track-f-phase-1.md` AND `evolution/sprint-2-review.md`:
- The complete hermeneutic circle test results
- What the tests proved about system coherence
- Known gaps between integration tests and real system behavior
- Sprint 2 review: all phases, test counts, patterns established, what Sprint 3 needs
