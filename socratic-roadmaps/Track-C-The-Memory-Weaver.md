# Track C: "The Memory Weaver" — Cross-App Context Stitching

## Hermeneutic Position

The Memory Weaver is the system's *connective tissue* — the faculty that makes the whole greater than the sum of its parts. Without cross-app context, each app is an island. The hermeneutic circle completes here: the whole (unified intelligence) is finally understood through all its parts working together; each part is finally understood through its relationship to every other.

This track closes the loop: `App Activity → Context Graph → Other Apps → Enriched Experience`.

## The Circle

```
Phase C.1: "The Loom"
  Understanding the parts: What context does each app produce and consume?
  → Builds renderer-side context subscriptions from the graph

Phase C.2: "The Threads"
  Understanding through the whole: How do briefings and context combine?
  → Builds cross-app context injection using work stream + briefing data

Phase C.3: "The Tapestry"
  Synthesis — the whole understood through all parts:
  → Builds the live context feed where apps react to each other in real time
```

## Estimated Scope

| Phase | New Files | New Lines | New Tests |
|-------|-----------|-----------|-----------|
| C.1 | 2 (hook + IPC) | ~140 | ~10 |
| C.2 | 1 | ~160 | ~12 |
| C.3 | 2 (hook + bridge) | ~150 | ~12 |
| **Total** | **5** | **~450** | **~34** |

## Dependencies

- **Reads from**: context-graph.ts, context-stream.ts, intelligence.ts
- **Reads from Track A**: briefing-pipeline contract (C.2 needs briefing data)
- **Reads from Track B**: execution results shape (C.3 feeds action results back to context)
- **Writes to**: New useWorkContext hook, context-injection module, live-context bridge
- **Contracts needed**: context-graph, context-stream, briefing-pipeline (from A.1)

## Success Criteria (Track-Level)

After all three phases:
1. Any renderer app can subscribe to work stream changes via `useWorkContext()` hook
2. When the user switches from one app to another, the new app receives relevant context
3. Briefings from Track A appear as context hints in relevant apps
4. Execution results from Track B feed back into the context graph
5. The context flow is observable via the FridayMonitor debug panel
6. Existing tests + Track A/B tests still pass
7. `npx tsc --noEmit` reports 0 errors
