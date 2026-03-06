# Phase C.1: "The Loom" — Renderer-Side Context Subscriptions

**Track:** C — The Memory Weaver
**Hermeneutic Focus:** Understanding the parts — what context does each app produce and consume? Before we can stitch apps together, we must understand what threads each app offers and what threads each app needs.

## Current State

The `ContextGraph` in the main process clusters events into work streams and tracks entities. The renderer has no way to subscribe to this data. Apps like FridayNotes, FridayFiles, and FridayWeather operate in isolation — each fetches its own data independently, unaware of what the user is doing in other apps.

IPC channels exist for `context-graph:get-streams`, `context-graph:get-entities`, `context-graph:get-active-stream`, but there's no reactive subscription — apps would have to poll.

## Architecture Context

```
Main: ContextGraph ──stream-change──→ IPC push via webContents.send()
                                           │
Renderer: [NEW: useWorkContext() hook]  ←──┘
           ├── activeStream: WorkStream | null
           ├── recentEntities: EntityRef[]
           ├── streamHistory: WorkStream[]  (last 5)
           └── onStreamChange: callback
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `useWorkContext()` hook returns `{ activeStream, recentEntities, streamHistory }`
2. When the main process pushes a `context:stream-update` event, the hook re-renders with new data
3. `activeStream` is null when no stream is active
4. `recentEntities` returns entities from the active stream, sorted by occurrence count
5. `streamHistory` returns the last 5 work streams in reverse chronological order
6. IPC handler `context:subscribe` registers the renderer for push updates
7. IPC handler `context:unsubscribe` deregisters the renderer
8. The hook cleans up its subscription on unmount (no memory leaks)
9. Multiple components using `useWorkContext()` share the same subscription (no duplicates)

## Socratic Inquiry

**Boundary:** A React hook must be synchronous on first render. The context data comes from the main process asynchronously. How does the hook handle the initial empty state before the first push arrives?

**Precedent:** Does the project already use `webContents.send()` for push notifications to the renderer? Check how other systems handle main→renderer push. If not, what pattern does the preload bridge use?

**Inversion:** What if apps received *all* context data — every stream, every entity, every event? The renderer would drown in data. What's the minimum context an app needs to be "aware"?

**Constraint Discovery:** React hooks re-render their component on state change. If context updates are frequent (every few seconds), will this cause performance problems? How can the hook debounce or batch updates?

**Tension:** Granularity vs. performance. Fine-grained subscriptions (per-entity) are more useful but more expensive. Coarse subscriptions (whole-stream) are cheaper but may trigger unnecessary re-renders.

**Safety Gate:** Can the context subscription leak across app unmount/remount cycles? What happens if the renderer crashes and reconnects — does the subscription auto-restore?

## Boundary Constraints

- **Max new lines:** 140 (two files: `src/renderer/hooks/useWorkContext.ts` ~80, `src/main/ipc/context-push-handlers.ts` ~60)
- **Update** `src/main/ipc/index.ts` to export new handler registration
- **Update** `src/main/preload.ts` to expose context push channels
- **No modifications** to context-graph.ts (read from it, don't change it)

## Files to Read

- `contracts/context-graph.md` (work stream + entity shapes)
- `contracts/briefing-pipeline.md` (from Track A — briefing event shape)

## Session Journal Reminder

Before closing, write `journals/track-c-phase-1.md` covering:
- The push subscription pattern used
- How the hook manages state and cleanup
- What Phase C.2 needs to know about the subscription shape
