# Orchestrator — Agent Friday v2.2 Sprint

## Execution Order

```
A.1 ──→ A.2 ──→ B.1 ──→ B.2 ──→ A.3 ──→ C.1 ──→ B.3 ──→ C.2 ──→ C.3
 │       │       │       │       │       │       │       │       │
 │       │       │       │       │       │       │       │       └─ Tapestry (synthesis)
 │       │       │       │       │       │       │       └─ Cross-app context
 │       │       │       │       │       │       └─ Execution delegate
 │       │       │       │       │       └─ Context subscriptions
 │       │       │       │       └─ Briefing delivery + IPC
 │       │       │       └─ Safety pipeline
 │       │       └─ Tool registry
 │       └─ Priority scoring
 └─ Briefing triggers
```

### Rationale for Interleaving

- **A.1 → A.2** first: Build the intelligence pipeline core before anything else depends on it
- **B.1 → B.2** next: Tool registry + safety pipeline are self-contained, can be built while A digests
- **A.3** after B.2: Briefing delivery needs the scoring engine ready (A.2), and benefits from seeing B.2's patterns
- **C.1** after A.3: Context subscriptions need the briefing push pattern established in A.3
- **B.3** after C.1: Execution delegate benefits from the subscription pattern established in C.1
- **C.2** after B.3: Cross-app injection reads from Track A (briefings) AND Track B (execution contract)
- **C.3** last: The tapestry is the synthesis — it needs every other piece in place

## Dependency Graph

```
                    ┌──────────┐
                    │   A.1    │  Briefing triggers
                    │  Score   │  (context-graph → pipeline)
                    │  Reader  │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   A.2    │  Priority scoring
                    │  Baton   │  (pure functions)
                    └────┬─────┘
                         │         ┌──────────┐
                         │         │   B.1    │  Tool registry
                         │         │ Workbench│  (catalog tools)
                         │         └────┬─────┘
                         │              │
                         │         ┌────▼─────┐
                         │         │   B.2    │  Safety pipeline
                         │         │Guardrails│  (approve/deny/pending)
                         │         └────┬─────┘
                    ┌────▼─────┐        │
                    │   A.3    │        │
                    │Performanc│        │
                    │   e      │        │
                    └────┬─────┘        │
                         │              │
                    ┌────▼─────┐        │
                    │   C.1    │        │
                    │   Loom   │        │
                    └────┬─────┘   ┌────▼─────┐
                         │         │   B.3    │
                         │         │Craftsman │
                         │         └────┬─────┘
                    ┌────▼─────────────▼──┐
                    │        C.2          │
                    │      Threads        │
                    └─────────┬───────────┘
                         ┌────▼─────┐
                         │   C.3    │
                         │ Tapestry │
                         └──────────┘
```

## Launch Prompts

### Phase A.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-a/phase-1-the-score-reader.md
4. socratic-roadmaps/contracts/context-graph.md
5. socratic-roadmaps/contracts/intelligence-engine.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate (npx tsc --noEmit && npx vitest run).
Write a session journal to socratic-roadmaps/journals/track-a-phase-1.md before closing.
```

### Phase A.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-a/phase-2-the-baton.md
4. socratic-roadmaps/journals/track-a-phase-1.md
5. socratic-roadmaps/contracts/context-graph.md
6. socratic-roadmaps/contracts/intelligence-router.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-a-phase-2.md before closing.
```

### Phase B.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-b/phase-1-the-workbench.md
4. socratic-roadmaps/contracts/llm-client.md
5. socratic-roadmaps/contracts/file-search.md
6. socratic-roadmaps/contracts/system-monitor.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-b-phase-1.md before closing.
Write contracts/tool-registry.md after completion.
```

### Phase B.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-b/phase-2-the-guardrails.md
4. socratic-roadmaps/journals/track-b-phase-1.md
5. socratic-roadmaps/contracts/tool-registry.md
6. socratic-roadmaps/contracts/llm-client.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-b-phase-2.md before closing.
Write contracts/safety-pipeline.md after completion.
```

### Phase A.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-a/phase-3-the-performance.md
4. socratic-roadmaps/journals/track-a-phase-2.md
5. socratic-roadmaps/contracts/intelligence-engine.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-a-phase-3.md before closing.
Write contracts/briefing-pipeline.md after completion (needed by Track C).
```

### Phase C.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-c/phase-1-the-loom.md
4. socratic-roadmaps/contracts/context-graph.md
5. socratic-roadmaps/contracts/briefing-pipeline.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-c-phase-1.md before closing.
```

### Phase B.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-b/phase-3-the-craftsman.md
4. socratic-roadmaps/journals/track-b-phase-2.md
5. socratic-roadmaps/contracts/tool-registry.md
6. socratic-roadmaps/contracts/safety-pipeline.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-b-phase-3.md before closing.
Write contracts/execution-delegate.md after completion (needed by Track C).
```

### Phase C.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-c/phase-2-the-threads.md
4. socratic-roadmaps/journals/track-c-phase-1.md
5. socratic-roadmaps/contracts/context-graph.md
6. socratic-roadmaps/contracts/briefing-pipeline.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-c-phase-2.md before closing.
```

### Phase C.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/01-GAP-MAP.md
3. socratic-roadmaps/track-c/phase-3-the-tapestry.md
4. socratic-roadmaps/journals/track-c-phase-2.md
5. socratic-roadmaps/contracts/briefing-pipeline.md
6. socratic-roadmaps/contracts/execution-delegate.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-c-phase-3.md before closing.
Write contracts/live-context-bridge.md after completion.
Write evolution/sprint-1-review.md — this completes the full sprint.
```

## Context Budget Verification

Each launch prompt reads at most 6 files:

| File | Est. Lines |
|------|-----------|
| Methodology (pruned) | ~80 |
| Gap map (focused) | ~60 |
| Phase file | ~120 |
| Previous journal | ~40 |
| Contract 1 | ~30 |
| Contract 2 | ~30 |
| **Total** | **~360** |

Well within the ~500-670 line ceiling. Leaves ~300+ lines of headroom for code reading and test output.

## Verification Checkpoints

After each phase:
1. `npx tsc --noEmit` — 0 errors
2. `npx vitest run` — all tests pass (baseline: 3,769, grows each phase)
3. New tests added by the phase also pass
4. Git commit checkpoint with descriptive message
5. Session journal written
6. Interface contract written (if applicable)

After all 9 phases:
1. Full test suite green (~3,880+ tests expected)
2. `npm run build` succeeds
3. Context flows: OS → graph → briefing → dashboard
4. Tool execution flows: LLM request → safety → execute → result
5. Cross-app context flows: App A activity → enriched context → App B
6. No runaway feedback loops
