# Phase D.1: "The Ignition" — Wire LiveContextBridge into Lifecycle

**Track:** D — The Wiring
**Hermeneutic Focus:** The parts (modules) exist but the whole (system) doesn't know about them. The ignition bridges that gap — making the system aware of its own intelligence subsystems by wiring them into the startup/shutdown lifecycle.

## Current State

`LiveContextBridge` is built and tested (Sprint 1, C.3) but never started. In `index.ts`:
- `contextGraph.start()` is called at line 633, `contextGraph.stop()` at line 936
- `startContextStreamBridge()` at line 632, `stopContextStreamBridge()` at line 937
- `liveContextBridge` is NOT imported, NOT started, NOT stopped
- The bridge needs `mainWindow` to push IPC events to the renderer

## Architecture Context

```
index.ts createWindow()        index.ts window-all-closed
        ↓                               ↓
  mainWindow created              liveContextBridge.stop()
        ↓                          contextGraph.stop()
  contextGraph.start()              stopContextStreamBridge()
  startContextStreamBridge()
  liveContextBridge.start(mainWindow)  ← NEW
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `liveContextBridge.start(mainWindow)` is called after `contextGraph.start()` in the Phase B boot sequence
2. `liveContextBridge.stop()` is called before `contextGraph.stop()` in the shutdown sequence
3. The start call receives the actual `mainWindow` BrowserWindow instance
4. Starting the bridge when already running is a no-op (idempotent)
5. The bridge receives context stream events after start (integration with existing stream)
6. Shutdown order is correct: bridge stops before graph stops before stream stops

## Socratic Inquiry

**Precedent:** How does `startContextStreamBridge()` integrate into `index.ts`? Follow the same import + call pattern. What lifecycle order does `contextGraph.start()`/`.stop()` use?

**Boundary:** The bridge depends on `contextGraph` and `contextStream` being active. Where in the boot sequence must it start? It must be AFTER both are started. Where in shutdown must it stop? BEFORE both stop.

**Constraint Discovery:** `mainWindow` is created in `createWindow()` but Phase B modules start later in `completeBootAfterUnlock()`. The bridge needs `mainWindow` — should it start in Phase A or Phase B? What if the vault is never unlocked?

**Safety Gate:** Adding a `start()` call to index.ts must not break any existing tests. The bridge subscribes to `contextStream.on()` — does that event source already exist? Will adding a subscriber cause any side effects?

**Inversion:** What if the bridge starts but `mainWindow` is destroyed before it stops? The `pushToRenderer` method already checks `webContents.isDestroyed()` — verify this safety check is sufficient.

## Boundary Constraints

- **Max new lines:** ~20 (only adding import + start/stop calls to index.ts, plus a test file)
- **Modify:** `src/main/index.ts` — add import and lifecycle calls
- **Create:** `tests/sprint-2/wiring-lifecycle.test.ts` — verify boot/shutdown order
- **Read:** `contracts/live-context-bridge.md` (API surface)

## Files to Read

- `socratic-roadmaps/journals/track-c-phase-3.md` (last Sprint 1 journal — knowledge chain)
- `socratic-roadmaps/contracts/live-context-bridge.md` (bridge API)
- `src/main/index.ts` lines 630-634 (context start), lines 920-950 (shutdown)

## Session Journal Reminder

Before closing, write `journals/track-d-phase-1.md` covering:
- Exact lines modified in index.ts
- Boot order rationale
- Shutdown order rationale
- Any discoveries about the Phase A vs Phase B startup timing
