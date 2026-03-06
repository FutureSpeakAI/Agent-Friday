# Phase A.3: "The Performance" — Briefing Delivery to Dashboard

**Track:** A — The Conductor
**Hermeneutic Focus:** Synthesis — the whole understood through all parts working together. The pipeline triggers, the scorer prioritizes, and now the delivery system brings it all to the user. The conductor's job is complete when the audience hears the music.

## Current State

Phase A.1 built `BriefingPipeline` (triggers from work stream changes). Phase A.2 built `BriefingScoringEngine` (pure priority scoring). Neither connects to the renderer yet. The intelligence engine has a `getBriefings()` method but no push mechanism.

## Architecture Context

```
BriefingPipeline ──trigger──→ BriefingScoringEngine ──scored──→ [NEW: BriefingDelivery]
                                                                      │
                                                            IPC push to renderer
                                                                      │
                                                               Dashboard widget
```

The delivery system:
1. Receives scored triggers from the pipeline
2. Calls intelligence engine to generate the briefing content
3. Pushes the result to the renderer via IPC

## Validation Criteria

Write failing tests first, then make them pass:

1. `BriefingDelivery.start()` wires pipeline → scorer → delivery chain
2. When a trigger fires, delivery calls `intelligenceEngine.research()` with enriched topic
3. Delivery emits briefings via IPC channel `briefing:new` with `{ id, topic, content, priority, timestamp }`
4. 'urgent' briefings are emitted immediately; 'informational' are batched (max 1 per 10 minutes)
5. `briefing:list` IPC handler returns recent briefings sorted by priority then recency
6. `briefing:dismiss` IPC handler marks a briefing as delivered
7. `BriefingDelivery.stop()` tears down the full chain cleanly
8. IPC handler registration follows the project's `registerXxxHandlers()` pattern
9. All IPC inputs are validated with `assertString` / `assertObject` helpers

## Socratic Inquiry

**Synthesis:** Three systems (trigger, score, deliver) must compose into a single flow. What is the simplest wiring that preserves testability? Can each piece still be unit-tested independently after they're connected?

**Boundary:** The delivery system touches both main process (intelligence engine) and renderer (IPC). Where does the boundary live? What belongs in `briefing-delivery.ts` vs. `ipc/briefing-handlers.ts`?

**Inversion:** What if the intelligence engine is slow (3+ seconds for a Claude call)? The trigger already fired. Does delivery block? Queue? Show a placeholder?

**Constraint Discovery:** The renderer needs to receive briefings without polling. Does the project already use `webContents.send()` for push, or does it rely on `ipcMain.handle()` request-response? What pattern is established?

**Tension:** Real-time push vs. resource efficiency. Pushing every briefing immediately means more IPC traffic. Batching means stale data. How does the priority system resolve this?

**Safety Gate:** Can the delivery chain create a runaway loop? (Briefing generated → context changes → new trigger → new briefing → ...). How is the loop broken?

## Boundary Constraints

- **Max new lines:** 180 (two files: `src/main/briefing-delivery.ts` ~120, `src/main/ipc/briefing-handlers.ts` ~60)
- **Update** `src/main/ipc/index.ts` to export the new handler registration
- **Update** `src/main/preload.ts` to expose briefing IPC channels
- **No renderer components** — this phase is main process + IPC only
- Write interface contract: `contracts/briefing-pipeline.md` (needed by Track C)

## Files to Read

- `journals/track-a-phase-2.md` (previous phase journal)
- `contracts/briefing-pipeline.md` (self — write after completion)
- `contracts/intelligence-engine.md` (research API shape)

## Session Journal Reminder

Before closing, write `journals/track-a-phase-3.md` covering:
- The full pipeline flow as built
- IPC channel names and payload shapes
- How the batching/dedup logic works
- Write `contracts/briefing-pipeline.md` for Track C to consume
