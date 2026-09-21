# Defect D Verdict — Per-Seat Task Queue (seat_queue)

**Status: SHIPPED**
**Date: 2026-09-21**

## What was built

`src/agent_friday/services/seat_queue.py` (7,834 bytes) — per-seat FIFO task
queue with honest statuses and watchdog integration:

- One task per local seat; concurrent submissions receive `queued-for-seat`
  instead of silently stacking on the inference server
- Queued records carry `USER_NOTICE` informing the user that local AI runs one
  job at a time and must be given time to run
- FIFO promotion when the running task completes or is killed
- Local seats serialize independently of each other; cloud seats are exempt
- `task_watchdog.assess` is the DEFAULT_ASSESS reaper: a `kill` verdict
  terminates the wedged task and frees the seat for the next in line

## Attribution

- Module implementation: bonsai2:27b worker seat (chain `rsi-defect-d-seat-queue`)
- Test contract (`tests/unit/test_seat_queue.py`, 11 tests, red-first verified)
  and this verdict: supervising seat

## Verification (independent of the implementer)

- Red-first: contract verified failing at the import gate (module absent)
  before the implement run fired
- Post-implement: seat-queue 11/11 green; combined run with watchdog +
  receipts suites: 33 passed, 1 skipped (documented Phase 0 dependency),
  0 failed — run through the project venv by the supervising seat
- All five AGENTS.md guards clean: import smoke, ruff fatal rules,
  gated-prompt callers, settings readers, stale model names
- Scope check: `git status --porcelain` shows no tracked file modified;
  the implementer touched only its assigned greenfield file

## Honest caveats

1. The chain's own verify step never executed — the implement step was
   marked "interrupted" mid-write (the file landed whole regardless). This
   verdict is the manual independent pass run by the supervising seat.
2. The module is engine-side only. Nothing calls it yet: `_spawn_task`
   still dispatches unconditionally. Integration into the dispatch hot path
   is tracked as follow-up work, deliberately not assigned to the worker
   seat (non-greenfield, high-risk surface).
3. UI surfacing of queue position and the local-AI-is-slower notice is a
   separate front-end follow-up.
