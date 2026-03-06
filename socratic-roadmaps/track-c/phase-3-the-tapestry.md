# Phase C.3: "The Tapestry" — Live Context Feed

**Track:** C — The Memory Weaver
**Hermeneutic Focus:** The hermeneutic circle closes. The whole system is now understood through all its parts working together. Context flows from OS events → context graph → briefing pipeline → context injector → apps → back to OS events. The tapestry is complete when every thread connects to every other.

## Current State

Phase C.1 built `useWorkContext()` for renderer-side context subscriptions. Phase C.2 built `ContextInjector` for cross-app context computation. Track A delivers briefings. Track B produces execution results. But there's no live feed that combines everything and pushes enriched context to apps in real time.

## Architecture Context

```
OS Events → ContextStream → ContextGraph → BriefingPipeline → BriefingScoringEngine
                                   ↓                                    ↓
                           ContextInjector  ←───────────────────────────┘
                                   ↓
                    [NEW: LiveContextBridge]
                           ├── subscribes to graph + briefing updates
                           ├── runs injector on each update
                           ├── pushes enriched context to renderer
                           └── feeds execution results back to graph
                                   ↓
                    [NEW: useAppContext(appId) hook]
                           ├── receives per-app enriched context
                           ├── merges with useWorkContext base data
                           └── provides { context, briefing, entities } to app components
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `LiveContextBridge.start()` subscribes to context graph and briefing pipeline events
2. When context or briefings change, bridge runs injector and pushes `app-context:update` IPC event
3. `useAppContext(appId)` hook receives enriched context specific to that app
4. The hook returns `{ context: AppContext, briefing: BriefingSummary | null, entities: EntityRef[] }`
5. Hook re-renders only when its specific app's context changes (not on every global update)
6. Execution results from Track B feed back into the context graph as `tool-execution` events
7. `LiveContextBridge.stop()` cleans up all subscriptions
8. IPC handler `app-context:get` returns current context for a specific app on demand
9. The bridge debounces updates — max one push per 2 seconds per app

## Socratic Inquiry

**Synthesis (the deepest):** Three tracks, nine phases, and the system comes full circle. The Conductor provides intelligence, the Sandbox provides action, and now the Weaver connects them. What is the *minimum viable tapestry* — the smallest set of connections that makes the system feel unified?

**Boundary:** The LiveContextBridge sits at the intersection of every other module. How does it avoid becoming a god object? What are the clear input/output boundaries?

**Inversion:** What if execution results *didn't* feed back into the context graph? The system would lose track of what it has done. What if *everything* fed back? The graph would be overwhelmed with self-referential data. What's the right level of feedback?

**Constraint Discovery:** The bridge pushes to the renderer via IPC. Multiple apps may be mounted simultaneously. How does the bridge know which apps are active? Does it push to all apps, or only mounted ones?

**Precedent:** How does `context-stream-bridge.ts` (the existing bridge) handle its event flow? Follow the same start/stop lifecycle pattern and the same error isolation approach.

**Safety Gate:** The feedback loop (execution → context → briefing → execution) must not create infinite cycles. What circuit breaker prevents runaway loops? Consider a "cooldown" period after execution results feed back.

## Boundary Constraints

- **Max new lines:** 150 (two files: `src/main/live-context-bridge.ts` ~90, `src/renderer/hooks/useAppContext.ts` ~60)
- **Update** `src/main/ipc/index.ts` if new handlers needed
- **Update** `src/main/preload.ts` if new IPC channels exposed
- **Import from** Track A contract (briefing events) and Track B contract (execution results)
- Write interface contract: `contracts/live-context-bridge.md` (system-level integration point)

## Files to Read

- `journals/track-c-phase-2.md` (previous phase journal)
- `contracts/briefing-pipeline.md` (from Track A — briefing push events)
- `contracts/execution-delegate.md` (from Track B — execution result shape)
- `contracts/context-graph.md` (event subscription API)

## Session Journal Reminder

Before closing, write `journals/track-c-phase-3.md` covering:
- The complete data flow from OS → apps → feedback
- How the circuit breaker prevents runaway loops
- The debouncing strategy and performance characteristics
- Write `contracts/live-context-bridge.md` as the system's integration contract
- Write `evolution/sprint-1-review.md` — this completes the first full sprint
