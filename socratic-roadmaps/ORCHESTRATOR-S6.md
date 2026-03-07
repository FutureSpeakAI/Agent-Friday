# Orchestrator — Agent Friday v2.2 Sprint 6: "The Body"

## Execution Order

```
O.1 ──→ O.2 ──→ O.3 ──→ P.1 ──→ P.2 ──→ P.3
 │       │       │       │       │       │
 │       │       │       │       │       └─ The Awakening (full integration)
 │       │       │       │       └─ The Identity (profile manager)
 │       │       │       └─ The Birth (setup wizard)
 │       │       └─ The Conductor (model orchestrator)
 │       └─ The Measure (tier recommender)
 └─ The Nerves (hardware profiler)
```

### Rationale for Sequential Order

- **O.1 → O.2 → O.3** strictly sequential: O.1 detects hardware, O.2 maps hardware to model tiers, O.3 coordinates actual model loading/unloading based on the recommendation
- **P.1 → P.2 → P.3** strictly sequential: P.1 creates the setup wizard (uses O.1-O.3), P.2 adds profile management, P.3 integration-tests the complete first-run experience
- **O before P**: The body (hardware awareness) must exist before the birth (setup). You can't recommend models without knowing the hardware.

## Dependency Graph

```
                    ┌──────────┐
                    │   O.1    │  HardwareProfiler
                    │   The    │  GPU, VRAM, RAM,
                    │  Nerves  │  CPU, disk detection
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   O.2    │  TierRecommender
                    │   The    │  Hardware → tier
                    │ Measure  │  mapping + advice
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   O.3    │  ModelOrchestrator
                    │   The    │  Load/unload models
                    │Conductor │  within VRAM budget
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   P.1    │  SetupWizard
                    │   The    │  First-run flow:
                    │  Birth   │  detect → recommend
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   P.2    │  ProfileManager
                    │   The    │  User profiles,
                    │Identity  │  settings, export
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   P.3    │  Full first-run
                    │   The    │  integration: install
                    │Awakening │  → setup → converse
                    └──────────┘
```

## Launch Prompts

### Phase O.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/06-GAP-MAP.md
3. socratic-roadmaps/track-o/phase-1-the-nerves.md
4. socratic-roadmaps/evolution/sprint-5-review.md
5. socratic-roadmaps/contracts/hardware-profiler.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate (npx tsc --noEmit && npx vitest run).
Write a session journal to socratic-roadmaps/journals/track-o-phase-1.md before closing.
```

### Phase O.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/06-GAP-MAP.md
3. socratic-roadmaps/track-o/phase-2-the-measure.md
4. socratic-roadmaps/journals/track-o-phase-1.md
5. socratic-roadmaps/contracts/hardware-profiler.md
6. socratic-roadmaps/contracts/tier-recommender.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-o-phase-2.md before closing.
```

### Phase O.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/06-GAP-MAP.md
3. socratic-roadmaps/track-o/phase-3-the-conductor.md
4. socratic-roadmaps/journals/track-o-phase-2.md
5. socratic-roadmaps/contracts/tier-recommender.md
6. socratic-roadmaps/contracts/model-orchestrator.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-o-phase-3.md before closing.
```

### Phase P.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/06-GAP-MAP.md
3. socratic-roadmaps/track-p/phase-1-the-birth.md
4. socratic-roadmaps/journals/track-o-phase-3.md
5. socratic-roadmaps/contracts/hardware-profiler.md
6. socratic-roadmaps/contracts/tier-recommender.md
7. socratic-roadmaps/contracts/setup-wizard.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-p-phase-1.md before closing.
```

### Phase P.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/06-GAP-MAP.md
3. socratic-roadmaps/track-p/phase-2-the-identity.md
4. socratic-roadmaps/journals/track-p-phase-1.md
5. socratic-roadmaps/contracts/setup-wizard.md
6. socratic-roadmaps/contracts/profile-manager.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-p-phase-2.md before closing.
```

### Phase P.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/06-GAP-MAP.md
3. socratic-roadmaps/track-p/phase-3-the-awakening.md
4. socratic-roadmaps/journals/track-p-phase-2.md
5. socratic-roadmaps/contracts/setup-wizard.md
6. socratic-roadmaps/contracts/profile-manager.md
7. socratic-roadmaps/contracts/model-orchestrator.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-p-phase-3.md before closing.
Write evolution/sprint-6-review.md — this completes the sixth and final sprint.
```

## Context Budget Verification

Each launch prompt reads at most 7 files:

| File | Est. Lines |
|------|-----------|
| Methodology (pruned) | ~80 |
| Gap map (focused) | ~60 |
| Phase file | ~80 |
| Previous journal | ~40 |
| Contract 1 | ~30 |
| Contract 2 | ~30 |
| Contract 3 (if needed) | ~30 |
| **Total** | **~350** |

Within the ~430 line ceiling.

## Verification Checkpoints

After all 6 phases:
1. Full test suite green (~4,500+ tests expected)
2. HardwareProfiler detects GPU, VRAM, RAM, CPU
3. TierRecommender maps hardware to model tier
4. ModelOrchestrator coordinates model loading within VRAM budget
5. SetupWizard guides first-run: detect → recommend → download → configure
6. ProfileManager creates/manages user profiles
7. The Awakening: fresh install → setup → first conversation verified
8. System auto-configures for detected hardware
9. All graceful degradation paths tested
10. No regressions in Sprint 1-5 test suites
11. The Sovereign Mind is complete
