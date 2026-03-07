# Session Journal: Track D, Phase 1 — "The Ignition"

## Date
2026-03-06

## What was built
- Modified `src/main/index.ts` — added import of `liveContextBridge` (line 117), `start()` call (line 635), `stop()` call (line 939)
- Created `tests/track-d/wiring-lifecycle.test.ts` (8 tests)

## Key Decisions

### Startup placement: after contextGraph.start()
The bridge subscribes to `contextStream.on()` and reads from `contextGraph`. Both must be active before the bridge starts. Placement order:
1. `startContextStreamBridge()` — raw OS event capture
2. `contextGraph.start()` — event aggregation
3. `liveContextBridge.start(mainWindow!)` — enriched context push

### Shutdown placement: before contextGraph.stop()
The bridge reads from the graph during updates. If the graph stops first, the bridge would read stale data. Shutdown order:
1. `liveContextBridge.stop()` — stop subscribing and pushing
2. `contextGraph.stop()` — stop aggregation
3. `stopContextStreamBridge()` — stop event capture

### Non-null assertion on mainWindow
Used `mainWindow!` because the boot sequence guarantees `mainWindow` is created before `completeBootAfterUnlock()` runs. The Phase A boot creates the window, Phase B (where this code runs) happens after vault unlock.

## Patterns Followed
- Same import location pattern as other singletons (line 117, after `contextGraph`)
- Same lifecycle pattern as `contextGraph.start()`/`.stop()` (symmetric start/stop)
- Test file uses `vi.hoisted()` for mock declarations (Sprint 1 pattern)
- Test file uses `vi.useFakeTimers()` for debounce testing

## Test Count
- Before: 3,945 tests
- After: 3,953 tests (+8)
- TypeScript errors: 0
