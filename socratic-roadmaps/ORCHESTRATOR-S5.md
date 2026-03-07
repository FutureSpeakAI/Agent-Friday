# Orchestrator — Agent Friday v2.2 Sprint 5: "The Eyes"

## Execution Order

```
M.1 ──→ M.2 ──→ M.3 ──→ N.1
 │       │       │       │
 │       │       │       └─ The Sight (vision circle integration)
 │       │       └─ The Focus (image understanding pipeline)
 │       └─ The Glance (screen context capture)
 └─ The Gaze (Moondream vision provider)
```

### Rationale for Sequential Order

- **M.1 → M.2 → M.3** strictly sequential: M.1 creates VisionProvider (model loading + image→text), M.2 builds ScreenContext on top (screenshot capture + UI analysis), M.3 adds user image input pipeline (clipboard/file/drag-drop)
- **N.1** last: Integration test verifies the complete see→understand→respond circle

## Dependency Graph

```
                    ┌──────────┐
                    │   M.1    │  VisionProvider
                    │   The    │  Moondream model
                    │   Gaze   │  load + describe
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   M.2    │  ScreenContext
                    │   The    │  Screenshot capture,
                    │  Glance  │  UI element detection
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   M.3    │  ImageUnderstanding
                    │   The    │  Clipboard, file,
                    │  Focus   │  drag-drop input
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   N.1    │  Full vision circle
                    │   The    │  see → understand →
                    │  Sight   │  respond integration
                    └──────────┘
```

## Launch Prompts

### Phase M.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/05-GAP-MAP.md
3. socratic-roadmaps/track-m/phase-1-the-gaze.md
4. socratic-roadmaps/evolution/sprint-4-review.md
5. socratic-roadmaps/contracts/vision-provider.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate (npx tsc --noEmit && npx vitest run).
Write a session journal to socratic-roadmaps/journals/track-m-phase-1.md before closing.
```

### Phase M.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/05-GAP-MAP.md
3. socratic-roadmaps/track-m/phase-2-the-glance.md
4. socratic-roadmaps/journals/track-m-phase-1.md
5. socratic-roadmaps/contracts/vision-provider.md
6. socratic-roadmaps/contracts/screen-context.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-m-phase-2.md before closing.
```

### Phase M.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/05-GAP-MAP.md
3. socratic-roadmaps/track-m/phase-3-the-focus.md
4. socratic-roadmaps/journals/track-m-phase-2.md
5. socratic-roadmaps/contracts/vision-provider.md
6. socratic-roadmaps/contracts/image-understanding.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-m-phase-3.md before closing.
```

### Phase N.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/05-GAP-MAP.md
3. socratic-roadmaps/track-n/phase-1-the-sight.md
4. socratic-roadmaps/journals/track-m-phase-3.md
5. socratic-roadmaps/contracts/vision-provider.md
6. socratic-roadmaps/contracts/vision-circle.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-n-phase-1.md before closing.
Write evolution/sprint-5-review.md — this completes the fifth full sprint.
```

## Context Budget Verification

Each launch prompt reads at most 6 files:

| File | Est. Lines |
|------|-----------|
| Methodology (pruned) | ~80 |
| Gap map (focused) | ~60 |
| Phase file | ~80 |
| Previous journal | ~40 |
| Contract 1 | ~30 |
| Contract 2 (if needed) | ~30 |
| **Total** | **~320** |

Well within the ~430 line ceiling.

## Verification Checkpoints

After all 4 phases:
1. Full test suite green (~4,350+ tests expected)
2. VisionProvider loads Moondream and describes images
3. ScreenContext captures and analyzes screenshots
4. ImageUnderstanding processes clipboard/file/drag-drop images
5. Vision circle integration: see → understand → respond verified
6. System works fully without vision model (graceful degradation)
7. VRAM budget respected: total < 8GB with vision loaded
8. No regressions in Sprint 1-4 test suites
