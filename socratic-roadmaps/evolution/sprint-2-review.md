# Sprint 2 Review — "The Living Context"

**Sprint period:** Sessions across multiple days
**Test count:** 3,945 (Sprint 1 end) → 4,017 (Sprint 2 end) = **+72 tests**
**Test files:** 98 (all passing)
**Safety Gate:** PASSED on every phase

## Sprint Goal

Close the hermeneutic circle: wire the context pipeline so that OS-level
ambient events flow through the graph, get enriched into per-app context,
reach the renderer, and feed execution results back into the stream.

## Phase Summary

| Phase | Track | Name | Tests Added | Key Deliverable |
|-------|-------|------|-------------|-----------------|
| D.1 | D | The Loom | +16 | LiveContextBridge wired into boot/shutdown lifecycle |
| D.2 | D | The Loom | +7 | Missing IPC handlers: app-context, briefing, tool-definitions |
| D.3 | D | The Loom | +7 | Execution delegate feedback loop (feedExecutionResult) |
| E.1 | E | The Lens | +9 | ContextBar in productivity apps (Notes, Calendar, Files) |
| E.2 | E | The Lens | +23 | ContextBar in intelligence apps (Friday, Orchestrator, Terminal, Gateway) |
| E.3 | E | The Lens | 0* | ContextBar in peripheral apps (Settings, Vault, Store, About) |
| F.1 | F | The Proof | +10 | End-to-end integration test of the full hermeneutic circle |

*E.3 added ContextBar to 4 apps with 0 new test files (covered by existing component tests).

## Architecture Decisions

### 1. Bridge as Pipeline Hub
`LiveContextBridge` became the central hub connecting all context modules:
- Subscribes to `contextStream` events
- Reads from `contextGraph` (active stream, entities)
- Reads from `briefingDelivery` (recent briefings)
- Feeds through `ContextInjector` for per-app enrichment
- Pushes to renderer via IPC (debounced 2s)
- Accepts execution feedback with circuit breaker (5s cooldown)

### 2. Two-Phase Boot Integration
Context pipeline starts in Phase B (vault unlocked):
- `contextGraph.start()` → `liveContextBridge.start(mainWindow)`
Stops in reverse on shutdown:
- `liveContextBridge.stop()` → `contextGraph.stop()`

### 3. ContextBar as Universal Context Surface
Every renderer app now has a `<ContextBar appId="..." />` that shows:
- Active work stream (task + app)
- Entity count
- Briefing summary
Mounted via `app-context:get` IPC, updated via `app-context:update` push.

### 4. Feedback Wire with Safety Filter
Execution results feed back into context stream, but only successful and
error results — denied and pending results are excluded. This prevents
safety decisions from polluting the context graph.

## Technical Insights

### Singleton State in Tests
The biggest testing challenge was singleton state persistence. Modules like
`contextStream`, `contextGraph`, and `liveContextBridge` maintain internal
state (throttle timestamps, listeners, timers) that persists across tests
even with `vi.useFakeTimers()`. The monotonically increasing epoch pattern
(`testEpoch += 60_000` per test) proved robust.

### Event Data Contract
The context graph expects `event.data.activeApp` for ambient events, not
the more intuitive `event.data.app`. This contract is enforced by the
`handleAmbientEvent` method and must be respected by all event producers.

### Bridge Start Ordering
The bridge must be started before events are pushed for the internal
`refreshInjector` to fire. This reflects the real boot sequence where
`liveContextBridge.start()` is called before ambient sensors begin emitting.

## What Sprint 2 Proved

The hermeneutic circle is closed. A user action (opening VS Code) generates
an ambient event that flows through the context graph, creates a work stream,
gets enriched with briefings and entities, reaches every renderer app via
ContextBar, informs tool execution, and feeds the result back into the stream
— completing the understanding→action→understanding loop that is the
philosophical foundation of Agent Friday.

## Metrics

- **72 new tests** across 7 phases
- **0 regressions** — all 3,945 pre-existing tests remained green
- **13 source files modified** (main process + renderer)
- **1 new test file** (integration suite)
- **10 integration criteria** validated the full pipeline
