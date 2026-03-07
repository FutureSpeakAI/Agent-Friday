# Orchestrator — Agent Friday v2.2 Sprint 2: "The Wiring"

## Execution Order

```
D.1 ──→ D.2 ──→ D.3 ──→ E.1 ──→ E.2 ──→ E.3 ──→ F.1
 │       │       │       │       │       │       │
 │       │       │       │       │       │       └─ The Proof (integration tests)
 │       │       │       │       │       └─ Peripheral Nervous System
 │       │       │       │       └─ The Synapses (intelligence apps)
 │       │       │       └─ The Nerve Endings (productivity apps)
 │       │       └─ The Feedback Wire (execution → context loop)
 │       └─ The Switchboard (IPC handler registration)
 └─ The Ignition (lifecycle wiring)
```

### Rationale for Sequential Order

- **D.1 → D.2 → D.3** strictly sequential: D.1 starts the bridge, D.2 registers handlers that depend on the bridge, D.3 connects the feedback wire that flows through both
- **E.1 → E.2 → E.3** sequential by complexity: E.1 establishes the visual pattern on simpler apps, E.2 applies to complex apps, E.3 extends to peripherals
- **F.1** last: Integration tests verify the entire wired system — must come after all wiring and mesh are complete

## Dependency Graph

```
                    ┌──────────┐
                    │   D.1    │  Wire LiveContextBridge
                    │ Ignition │  into index.ts lifecycle
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   D.2    │  Register 4 missing
                    │Switchbrd │  IPC handler groups
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   D.3    │  Connect execution
                    │Feedback  │  delegate → bridge
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   E.1    │  Notes, Tasks,
                    │  Nerves  │  Calendar, Files
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   E.2    │  Browser, Code,
                    │ Synapses │  Forge, Comms
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   E.3    │  Monitor, Weather,
                    │Periph NS │  Gallery, Media...
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   F.1    │  Full hermeneutic
                    │  Proof   │  circle integration
                    └──────────┘
```

## Launch Prompts

### Phase D.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/02-GAP-MAP.md
3. socratic-roadmaps/track-d/phase-1-the-ignition.md
4. socratic-roadmaps/journals/track-c-phase-3.md
5. socratic-roadmaps/contracts/live-context-bridge.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate (npx tsc --noEmit && npx vitest run).
Write a session journal to socratic-roadmaps/journals/track-d-phase-1.md before closing.
```

### Phase D.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/02-GAP-MAP.md
3. socratic-roadmaps/track-d/phase-2-the-switchboard.md
4. socratic-roadmaps/journals/track-d-phase-1.md
5. socratic-roadmaps/contracts/live-context-bridge.md
6. socratic-roadmaps/contracts/execution-delegate.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-d-phase-2.md before closing.
```

### Phase D.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/02-GAP-MAP.md
3. socratic-roadmaps/track-d/phase-3-the-feedback-wire.md
4. socratic-roadmaps/journals/track-d-phase-2.md
5. socratic-roadmaps/contracts/execution-delegate.md
6. socratic-roadmaps/contracts/live-context-bridge.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-d-phase-3.md before closing.
```

### Phase E.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/02-GAP-MAP.md
3. socratic-roadmaps/track-e/phase-1-the-nerve-endings.md
4. socratic-roadmaps/journals/track-d-phase-3.md
5. socratic-roadmaps/contracts/live-context-bridge.md
6. src/renderer/hooks/useAppContext.ts

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-e-phase-1.md before closing.
```

### Phase E.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/02-GAP-MAP.md
3. socratic-roadmaps/track-e/phase-2-the-synapses.md
4. socratic-roadmaps/journals/track-e-phase-1.md
5. socratic-roadmaps/contracts/live-context-bridge.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-e-phase-2.md before closing.
```

### Phase E.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/02-GAP-MAP.md
3. socratic-roadmaps/track-e/phase-3-the-peripheral-nervous-system.md
4. socratic-roadmaps/journals/track-e-phase-2.md
5. socratic-roadmaps/contracts/live-context-bridge.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-e-phase-3.md before closing.
```

### Phase F.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/02-GAP-MAP.md
3. socratic-roadmaps/track-f/phase-1-the-proof.md
4. socratic-roadmaps/journals/track-e-phase-3.md
5. socratic-roadmaps/contracts/live-context-bridge.md
6. socratic-roadmaps/contracts/execution-delegate.md
7. socratic-roadmaps/contracts/context-graph.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-f-phase-1.md before closing.
Write evolution/sprint-2-review.md — this completes the second full sprint.
```

## Context Budget Verification

Each launch prompt reads at most 7 files:

| File | Est. Lines |
|------|-----------|
| Methodology (pruned) | ~80 |
| Gap map (focused) | ~70 |
| Phase file | ~80 |
| Previous journal | ~40 |
| Contract 1 | ~30 |
| Contract 2 | ~30 |
| **Total** | **~330** |

Well within the ~430 line ceiling. Leaves ~300+ lines for code reading and test output.

## Verification Checkpoints

After each phase:
1. `npx tsc --noEmit` — 0 errors
2. `npx vitest run` — all tests pass (baseline: 3,945, grows each phase)
3. New tests added by the phase also pass
4. Git commit checkpoint with descriptive message
5. Session journal written

After all 7 phases:
1. Full test suite green (~4,100+ tests expected)
2. `npm run build` succeeds
3. LiveContextBridge starts with mainWindow, stops on shutdown
4. All 4 missing IPC handlers registered
5. Execution results feed back to context graph
6. 10+ apps consume useAppContext
7. Integration tests verify the full hermeneutic circle
8. No runaway feedback loops (circuit breaker verified)
