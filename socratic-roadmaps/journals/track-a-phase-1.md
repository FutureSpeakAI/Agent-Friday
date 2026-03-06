# Session Journal: Track A, Phase 1 — "The Score Reader"

**Date:** 2026-03-06
**Tests added:** 20 (total: 3,789 across 79 files)
**New lines:** 130 (`src/main/briefing-pipeline.ts`)

## What Was Built

`BriefingPipeline` — a context-aware trigger module that bridges work stream changes in the `ContextGraph` to downstream intelligence consumers.

### Architecture Decision: Observer via Shared Subscription

The `ContextGraph` does not emit events when work streams change — it processes `contextStream.on()` events internally and updates `this.activeStreamId`. The BriefingPipeline subscribes to the *same* `contextStream.on()` event bus. Since the graph subscribes first (via `contextGraph.start()` before `briefingPipeline.start()`), JavaScript's single-threaded execution guarantees the graph processes each event before the pipeline's callback fires. The pipeline then reads `contextGraph.getActiveStream()` and compares against its tracked `lastStreamId` to detect transitions.

This avoids modifying `context-graph.ts` and follows the observer pattern precedent set by `context-stream-bridge.ts`.

### Key Design Choices

1. **5-minute dedup window** — prevents briefing spam when users alt-tab rapidly between two apps. The window is stream-ID-based, not name-based, so genuinely new streams with similar names still trigger.

2. **Top 3 entities** — triggers carry the stream's first 3 entities for topic enrichment. This is a deliberate cap: downstream consumers (Phase A.2 scorer) can request more context from the graph if needed.

3. **Null → stream transitions trigger, stream → null transitions don't** — going idle shouldn't produce a briefing. Only gaining a new active stream matters.

4. **No callback/event emitter yet** — the pipeline stores triggers in an array. Phase A.3 (BriefingDelivery) will subscribe to these triggers and call `intelligenceEngine.runResearch()`. This decoupling means Phase A.2 (scoring) can be built as a pure function that reads the trigger list without any wiring changes.

## Patterns Established

- **Hoisted mock pattern**: `vi.hoisted()` for mocks referenced inside `vi.mock()` factories. Used getter/setter pattern for the listener capture since closures in hoisted blocks can't be directly reassigned.
- **Stream change detection**: Compare `getActiveStream()?.id` against tracked `lastStreamId` on every context event.
- **Trigger pruning**: `MAX_TRIGGERS = 50` in-memory cap with FIFO eviction.

## What Phase A.2 Should Know

1. `BriefingTrigger` type is exported from `briefing-pipeline.ts` — import it directly.
2. `getRecentTriggers(limit)` returns triggers in reverse chronological order (most recent first).
3. The scorer should be a **pure function**: `scoreTrigger(trigger, history) → priority`. It should NOT subscribe to events — Phase A.3 will wire pipeline → scorer → delivery.
4. The `entities` array in each trigger is already capped at 3. The scorer can use `contextGraph.getTopEntities()` for richer context if needed.
5. Follow the `scoreModel()` pattern from `intelligence-router.ts`: hard filters → policy checks → weighted heuristic → threshold buckets.

## Interface Changes

### New Exports (src/main/briefing-pipeline.ts)
- `BriefingPipeline` — class
- `briefingPipeline` — singleton instance
- `BriefingTrigger` — interface `{ id, streamId, streamName, task, entities, triggeredAt }`

### No IPC Channels (deferred to Phase A.3)
### No Modifications to Existing Files
