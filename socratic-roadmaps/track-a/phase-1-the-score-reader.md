# Phase A.1: "The Score Reader" — Context-Aware Briefing Triggers

**Track:** A — The Conductor
**Hermeneutic Focus:** Understanding the parts — what does the context graph already know, and how can that knowledge trigger proactive intelligence?

## Current State

The `ContextGraph` (855 lines) clusters OS events into work streams and extracts entities (files, apps, URLs, projects). The `IntelligenceEngine` (238 lines) generates briefings from scheduled research topics via `taskScheduler`. These two systems don't talk to each other — briefings are schedule-driven, not context-driven.

## Architecture Context

```
ContextGraph.on('stream-switch') ─→ [NEW: BriefingPipeline] ─→ IntelligenceEngine.research()
ContextGraph.getActiveStream()   ─→ [NEW: BriefingPipeline] ─→ topic enrichment
```

The BriefingPipeline sits between the context graph and the intelligence engine, translating work stream changes into research triggers.

## Validation Criteria

Write failing tests first, then make them pass:

1. `BriefingPipeline.start()` subscribes to context graph work stream changes
2. When the active work stream changes, a briefing trigger fires within 100ms
3. The trigger includes the new stream's name, task type, and top 3 entities
4. Duplicate triggers for the same stream within a 5-minute window are suppressed
5. `BriefingPipeline.stop()` unsubscribes cleanly — no dangling listeners
6. When no work stream is active, no triggers fire
7. The pipeline exposes `getRecentTriggers(limit)` for debugging
8. `BriefingPipeline` is a singleton exported as `briefingPipeline`

## Socratic Inquiry

**Boundary:** What events constitute a "meaningful" work stream change worth triggering a briefing? A 2-second tab switch isn't the same as a 10-minute coding session. How does the pipeline distinguish signal from noise?

**Precedent:** How does `context-stream-bridge.ts` connect OS events to the context stream? Follow the same observer pattern — subscribe to an existing system's events, transform them, and emit downstream.

**Inversion:** What if the pipeline triggers on *every* stream change? The user would get bombarded with briefings every time they alt-tab. What's the minimum dwell time before a stream change is "real"?

**Constraint Discovery:** The context graph's `getActiveStream()` returns the current stream, but stream changes are internal events. How do you observe stream transitions without modifying the graph? Does the graph emit events, or do you poll?

**Tension:** Responsiveness vs. noise. Triggering fast means the user gets briefings sooner, but also gets more false positives. Where is the equilibrium?

**Safety Gate:** Does adding a subscriber to the context graph affect its performance or memory? Can the pipeline's subscription leak if `stop()` isn't called?

## Boundary Constraints

- **Max new lines:** 130 (one file: `src/main/briefing-pipeline.ts`)
- **No modifications** to context-graph.ts or intelligence.ts in this phase
- **No IPC channels** yet — this is a main-process-only module
- **No AI calls** — this phase is pure event plumbing

## Files to Read

- `contracts/context-graph.md` (interface contract)
- `contracts/intelligence-engine.md` (interface contract)

## Session Journal Reminder

Before closing, write `journals/track-a-phase-1.md` covering:
- What was built, decisions made, patterns established
- What the next agent (Phase A.2) should know
- Any interface changes (new exports, event patterns)
