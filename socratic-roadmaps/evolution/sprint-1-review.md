# Sprint 1 Review: The Hermeneutic Foundation

## Sprint Summary
**Duration**: 9 phases across multiple sessions
**Goal**: Build the three foundational tracks — proactive intelligence (A), safe tool execution (B), and cross-app context weaving (C) — that close the hermeneutic circle.

## Phase Completion

| Phase | Track | Name | Commit | Tests Added |
|-------|-------|------|--------|-------------|
| A.1 | Briefing Pipeline | "First Words" | 011c252 | +14 |
| A.2 | Briefing Scoring | "The Chooser" | 3716a5f | +20 |
| B.1 | Tool Registry | "The Toolbox" | 04c0ef8 | +16 |
| B.2 | Safety Pipeline | "The Guardian" | fad6d75 | +16 |
| A.3 | Briefing Delivery | "Many Tongues" | c464ac3 | +18 |
| C.1 | useWorkContext | "The Loom" | 5c436fe | +12 |
| B.3 | Execution Delegate | "The Craftsman" | 5884bc2 | +16 |
| C.2 | Context Injector | "The Threads" | 825e61d | +17 |
| C.3 | LiveContextBridge | "The Tapestry" | (pending) | +22 |

**Total new tests**: ~151
**Final test count**: 3,945 (all passing)
**TypeScript errors**: 0

## Architecture Established

### Track A: Proactive Intelligence
```
Context events → BriefingPipeline → BriefingScoringEngine → BriefingDelivery → Dashboard
```
The system watches what the user is doing, identifies patterns worth briefing about, scores them by priority, and delivers them proactively.

### Track B: Safe Tool Execution
```
ToolCall → SafetyPipeline → ToolRegistry → Handler → ToolResult
            ├── approved → execute
            ├── pending → confirm dialog → execute
            └── denied → error result
```
Every tool call passes through safety evaluation before execution. Two-phase pending flow with TOCTOU protection.

### Track C: Cross-App Context Weaving
```
OS events → ContextStream → ContextGraph → ContextInjector → per-app context
                                    ↑                              ↓
                                    └── execution results ← LiveContextBridge
```
The hermeneutic circle closes: events become understanding, understanding informs action, action generates new events.

## Patterns Established

1. **Singleton + DI**: Main process modules are singletons; renderer stores use constructor-injected IPC bridges for testability.

2. **Test-first with vi.hoisted()**: All vitest mocks use `vi.hoisted()` to avoid reference-before-init issues with `vi.mock()` hoisting.

3. **Eager computation + debounced notification**: The ContextInjector is refreshed synchronously on every event, but IPC pushes are debounced to 2 seconds.

4. **Circuit breakers**: Both feedback loop (5s cooldown) and debounce (2s window) prevent system overwhelm.

5. **Serialization boundaries**: Main process types (with Sets, Maps) are explicitly serialized to renderer-safe types (arrays, plain objects) at bridge boundaries.

6. **Reference counting**: Renderer stores track subscriber count and only activate IPC listeners when components are mounted.

7. **Pure computation modules**: The ContextInjector has no singletons or side effects — it's a pure function wrapped in a class for state management.

## Risks Mitigated

- **Context overflow**: Strict ~500-670 line budget per phase prevented the context window exhaustion that killed the previous sprint.
- **Type mismatches**: Discovered and fixed `WorkStream` vs `SerializedStream` type divergence at the bridge boundary.
- **Event type confusion**: `ContextEventType` union must be maintained consistently across modules; `'tool-invoke'` not `'tool-execution'`.
- **Feedback loops**: Circuit breaker in `feedExecutionResult()` prevents runaway context → briefing → execution cycles.

## What the Next Sprint Needs

1. **Wire LiveContextBridge into main process startup** — call `liveContextBridge.start(mainWindow)` after window creation and `liveContextBridge.stop()` on shutdown.
2. **Register app-context IPC handler** — call `registerAppContextHandlers()` during IPC initialization.
3. **Connect execution delegate feedback** — after tool execution, call `liveContextBridge.feedExecutionResult(result)`.
4. **App Shell (Track C continued)** — wire the 23 app components to the launchpad, connect them to the context injection system.
5. **Integration testing** — end-to-end test of the full hermeneutic circle: user action → context → briefing → tool execution → feedback.
