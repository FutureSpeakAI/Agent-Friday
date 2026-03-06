# Phase C.2: "The Threads" — Cross-App Context Injection

**Track:** C — The Memory Weaver
**Hermeneutic Focus:** Understanding through the whole — how do independent pieces of information (briefings from Track A, work streams from the context graph) combine to create a unified experience that is greater than either alone?

## Current State

Phase C.1 built `useWorkContext()` so apps can observe work stream changes. Track A built the briefing pipeline that generates prioritized intelligence. But these are separate channels — an app that knows about the work stream doesn't know about relevant briefings, and vice versa.

## Architecture Context

```
useWorkContext()    ─→  activeStream, entities
                         │
briefing:new event  ─→   ├── [NEW: ContextInjector]
                         │     ├── merges stream + briefing data
                         │     ├── computes relevance per app
                         │     └── provides getContextForApp(appId)
                         │
Apps call:               └── useAppContext(appId) → enriched context
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `contextInjector.ingest(streamData, briefings)` merges work stream and briefing data
2. `contextInjector.getContextForApp(appId)` returns context relevant to that app's domain
3. FridayNotes gets: recent entities of type 'file' or 'project', relevant briefings about writing/documentation
4. FridayFiles gets: current working directory from stream, recent file entities
5. FridayWeather gets: location entity if present in stream, time-of-day context
6. FridayMonitor gets: process entities from stream, system-related briefings
7. Apps not in the registry get generic context (active stream + highest-priority briefing)
8. `contextInjector` updates when new stream or briefing data arrives
9. The injector is a pure computation — takes input, returns output, no side effects
10. Context is lightweight: max 5 entities + 1 briefing summary per app

## Socratic Inquiry

**Synthesis:** Work streams capture *what the user is doing*. Briefings capture *what the system thinks is important*. How do these two perspectives merge into a single "context" object that apps can use?

**Boundary:** Each app has a different domain. FridayNotes cares about text and projects. FridayFiles cares about paths. The injector must understand app domains without importing app code. How is the mapping defined?

**Precedent:** How does `intelligence-router.ts` classify tasks by type? The router maps task descriptions to capability requirements. Follow a similar pattern — map app IDs to relevant entity types and briefing categories.

**Inversion:** What if every app received every piece of context? Information overload makes context useless. What if apps received no context? They're back to being isolated. The injector's value is *curation*.

**Tension:** Correctness vs. latency. Computing perfect relevance takes time. Computing approximate relevance is fast but may show irrelevant context. Where's the acceptable tradeoff for a desktop OS?

**Safety Gate:** Can context injection leak sensitive data across app boundaries? If a work stream contains credentials or secrets (file paths with tokens), does the injector strip or pass them through?

## Boundary Constraints

- **Max new lines:** 160 (one file: `src/main/context-injector.ts`)
- **No renderer changes** — the injector is a main-process computation
- **Read from Track A** contract: `contracts/briefing-pipeline.md`
- **Pure functions** — no singletons needed, this is a computation module

## Files to Read

- `journals/track-c-phase-1.md` (previous phase journal — subscription shape)
- `contracts/context-graph.md` (stream + entity shapes)
- `contracts/briefing-pipeline.md` (from Track A — briefing shape)

## Session Journal Reminder

Before closing, write `journals/track-c-phase-2.md` covering:
- The app-domain mapping strategy
- How context is curated per app
- What Phase C.3 needs to know about the context shape
