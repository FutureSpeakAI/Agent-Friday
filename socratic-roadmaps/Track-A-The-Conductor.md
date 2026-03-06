# Track A: "The Conductor" — Proactive Intelligence Loop

## Hermeneutic Position

The Conductor is the system's *voice* — the faculty that transforms passive observation into proactive insight. Without it, Agent Friday sees everything but says nothing. The whole (an intelligent OS) cannot be understood without the part that speaks; the speaking part cannot be understood without the whole it serves.

This track closes the loop: `ContextGraph → IntelligenceEngine → Dashboard → User`.

## The Circle

```
Phase A.1: "The Score Reader"
  Understanding the parts: What does the context graph already know?
  → Builds a bridge from work streams to briefing triggers

Phase A.2: "The Baton"
  Understanding through the whole: How should intelligence prioritize?
  → Builds priority scoring informed by user activity patterns

Phase A.3: "The Performance"
  Synthesis — the whole understood through all parts:
  → Delivers contextual briefings to the dashboard in real time
```

## Estimated Scope

| Phase | New Files | New Lines | New Tests |
|-------|-----------|-----------|-----------|
| A.1 | 1 | ~120 | ~10 |
| A.2 | 1 | ~150 | ~12 |
| A.3 | 2 (main + IPC) | ~180 | ~14 |
| **Total** | **4** | **~450** | **~36** |

## Dependencies

- **Reads from**: context-graph.ts, intelligence.ts, scheduler.ts
- **Writes to**: New briefing-pipeline module, new IPC channels
- **Contracts needed**: context-graph, intelligence-engine, scheduler

## Success Criteria (Track-Level)

After all three phases:
1. When the user switches work streams, Friday generates a contextual briefing within 5 seconds
2. Briefings are prioritized: urgent > relevant > informational
3. The dashboard displays live briefings without polling
4. Existing 3,769+ tests still pass
5. `npx tsc --noEmit` reports 0 errors
