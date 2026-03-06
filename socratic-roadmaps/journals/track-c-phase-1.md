## Session Journal: Track C, Phase 1 — "The Loom"
**Date:** 2026-03-06
**Commit:** (pending)

### What Was Built
`WorkContextStore` + `useWorkContext()` hook — the renderer's lens into the main process's context graph. Plus `context-push-handlers.ts` which bridges context stream events into IPC push updates.

### Architecture

```
contextStream.on(event)          ← existing observer (main process)
  → contextGraph.getActiveStream()  ← read-only query (no modification)
    → diff lastActiveStreamId       ← change detection
      → webContents.send('context:stream-update', payload)  ← push to renderer
        → WorkContextStore.applyUpdate(payload)             ← external store
          → useSyncExternalStore → React re-render          ← hook
```

### Two-Layer Split
The renderer side is split into a testable core and a thin React wrapper:

1. **WorkContextStore** (pure JS) — manages IPC subscription lifecycle via reference counting. First subscriber activates the IPC listener; last unsubscriber cleans it up. Sorts entities by occurrence count, limits stream history to 5.

2. **useWorkContext()** (React hook) — thin `useSyncExternalStore` wrapper over the singleton store. No logic of its own. Correctness guaranteed by React 19's contract.

### Key Design Choices
1. **Subscribe to `contextStream.on()`, not `contextGraph`**: Phase constraint forbids modifying context-graph.ts. Instead, the push handler subscribes to the context stream externally and reads from contextGraph's public query methods. This adds one level of indirection but respects module boundaries.

2. **Change detection by stream ID diff**: Rather than diffing entire stream state, the handler compares `lastActiveStreamId` against the current active stream's ID. This is O(1) and sufficient — stream content changes don't need push updates (the renderer can poll for details if needed).

3. **Dependency injection for `WorkContextStore`**: First attempt used `require('electron').ipcRenderer` at module scope, which broke vitest mocking. Refactored to accept a `ContextIpcBridge` interface via constructor. Production uses a lazy singleton via `getStore()`. Tests inject a mock bridge directly — no `vi.mock('electron')` needed.

4. **Reference counting over single-subscriber**: Multiple React components may call `useWorkContext()`. Rather than forcing them to coordinate, the store uses `refCount` to manage the IPC subscription lifecycle automatically. Pattern matches `briefingDelivery.onNew()` from Phase A.3.

5. **`useSyncExternalStore` over `useState` + `useEffect`**: React 19 provides this hook specifically for subscribing to external stores. It handles tearing, concurrent mode, and SSR edge cases. The alternative (`useState` + manual `useEffect` subscription) would reimplement what React already solved.

### What Surprised Me
The DI refactor was forced by a test infrastructure mismatch — `vi.mock('electron')` doesn't intercept dynamic `require('electron')` inside a try-catch. The fix (constructor injection) actually improved the design: the store became framework-agnostic and trivially testable. The "failure" in testing revealed a cleaner architecture.

### Test Coverage
22 tests across 2 files, 7 of 9 validation criteria (criteria 2 and 6 overlap with push handler tests):

- `context-push-handlers.test.ts`: 10 tests — handler registration, stream subscription, push on change (null→stream, stream→stream, stream→null), no push on same stream, cleanup, destroyed webContents guard, Set→Array serialization, history limit
- `work-context-store.test.ts`: 12 tests — state shape (criterion 1), null stream (criterion 3), entity sorting (criterion 4), history limiting (criterion 5), IPC cleanup on last consumer (criterion 8), shared subscription with ref counting (criterion 9), subscriber notification and isolation

### Files Created/Modified
- `src/main/ipc/context-push-handlers.ts` (NEW, ~60 lines) — push handler + change detection
- `src/renderer/hooks/useWorkContext.ts` (NEW, ~80 lines) — store + hook
- `src/main/ipc/index.ts` (MODIFIED) — barrel export for context push
- `src/main/preload.ts` (MODIFIED) — `contextGraph.onStreamUpdate()` subscription

### Socratic Reflection
*"The parts (entities, streams) become accessible to the whole (the UI)."* — The Loom weaves raw context into React-consumable state. But the interesting hermeneutic moment was the DI refactor: understanding the test failure required understanding the boundary between Node's `require()` and vitest's module system. The tool (vitest) shaped the architecture (DI) — a case where the method of inquiry influenced what was found.

### Handoff Notes for Phase B.3
Phase B.3 ("The Craftsman" — ExecutionDelegate) sits downstream of SafetyPipeline (B.2). It needs the `ToolRegistry.resolve()` pattern from B.1 and the `SafetyDecision` lifecycle from B.2. The `WorkContextStore` DI pattern may inform how ExecutionDelegate handles async tool execution with testable dependency boundaries.
