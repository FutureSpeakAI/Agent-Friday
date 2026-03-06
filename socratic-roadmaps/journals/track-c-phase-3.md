# Session Journal: Track C, Phase 3 — "The Tapestry"

## Date
2026-03-06

## What was built
- `src/main/live-context-bridge.ts` (~160 lines) — LiveContextBridge class
- `src/renderer/hooks/useAppContext.ts` (~150 lines) — AppContextStore + useAppContext hook
- `src/main/ipc/app-context-handlers.ts` (~17 lines) — IPC handler for app-context:get
- `tests/track-c/live-context-bridge.test.ts` (11 tests)
- `tests/track-c/use-app-context.test.ts` (11 tests)
- Updated `src/main/preload.ts` — appContext namespace
- Updated `src/main/ipc/index.ts` — barrel export

## Key Decisions

### Eager refresh + debounced push
The injector must always be current for `getContextForApp()` IPC calls, but IPC pushes to the renderer should be throttled. Solved by splitting `scheduleUpdate()` into:
1. `refreshInjector()` — runs synchronously on every stream event
2. `pushToRenderer()` — runs after a 2-second debounce

This is a novel pattern: the computation is always current, but the notification is throttled.

### Serialization boundary at the bridge
`WorkStream.eventTypes` is a `Set<ContextEventType>` in the main process, but the renderer expects `string[]`. The bridge explicitly serializes each field of the active stream rather than passing it through, making the type boundary explicit and safe.

### Circuit breaker for feedback loop
Execution results feed back into the context graph, which could trigger new briefings, which could trigger new executions. The 5-second cooldown (`FEEDBACK_COOLDOWN_MS`) breaks this cycle by dropping feedback events within the window.

### Preload bridge adapter
The `useAppContext` hook's production `getStore()` function creates an adapter bridge that maps `AppContextIpcBridge` interface calls to `window.eve.appContext` preload methods. The `onUpdate` unsubscribe function is captured in a closure for proper cleanup.

## Discoveries

### vi.hoisted() is required for mock variables
Vitest hoists `vi.mock()` calls to the top of the file, but `const` declarations aren't hoisted. The `vi.hoisted()` utility returns values that are available at hoist time, solving the "cannot access before initialization" error.

### Reference counting in stores
Both `WorkContextStore` (C.1) and `AppContextStore` (C.3) use reference counting to activate/deactivate IPC subscriptions. This prevents orphaned listeners when all React components unmount.

## What would change with hindsight
The `ContextEventType` union in `context-graph.ts` should include a discriminated union pattern so new event types can be added without updating every consumer. The `'tool-invoke'` vs `'tool-execution'` naming confusion wasted a cycle.

## Test Count
- Before: 3,923 tests
- After: 3,945 tests (+22)
- TypeScript errors: 0
