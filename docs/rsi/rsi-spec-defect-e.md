# RSI Spec — Defect E: wire seat_queue + task_watchdog into the dispatch hot path

Status: SPEC — red-first tests written, implementation NOT started.
Ground truth: `tests/unit/test_dispatch_wiring.py` (verified red at import gate).
Implementer: senior seat or Claude Code with human-reviewed diffs. NOT the local
worker seat — this change touches the dispatch hot path and threading semantics.

## Problem

`seat_queue.py` (Defect D) and `task_watchdog.py` (Defect C) exist and are green,
but nothing calls them:

- `_spawn_task` builds the task record then starts the worker thread
  unconditionally. Every spawned task dogpiles the local seat simultaneously;
  the llama-server serializes them invisibly at the HTTP layer with no status,
  no UI, no way to distinguish "queued behind another job" from "wedged".
- `task_watchdog.assess()` judges but nothing enforces: a wedged task ran
  8,869s against an 1800s budget (2026-09-21 incident) with `stalled: false`,
  pinning the GPU until the OS was unusable.

## The three seams

1. **`_spawn_task`** — after the task record is built and before thread
   creation, resolve the task's seat and call `seat_supervisor.wire_spawn()`.
   - verdict `running` → start the thread exactly as today.
   - verdict `queued-for-seat` → do NOT create a thread. The record keeps
     status `queued-for-seat` and carries `user_notice` (seat_queue writes
     both). The task tray reads task records, so the honest label surfaces
     with zero UI work as a first pass.

2. **`_task_worker` teardown** — the `finally` block calls
   `seat_supervisor.on_task_end(task_id)`, which delegates to
   `SeatQueue.complete()` and promotes the next FIFO task by starting its
   deferred thread. Promotion is the only new thread-creation site and reuses
   the same thread construction as a fresh spawn.

3. **Supervisor loop** — one daemon thread, cadence ≤30s, calling
   `SeatQueue.assess_all()` (default assessor: `task_watchdog.assess`).
   A `kill` verdict marks the task `failed:watchdog-kill`, frees the seat,
   and promotes the next queued task.

## Kill enforcement is two-tier (stated honestly)

Python cannot kill a thread blocked on a wedged HTTP call.

- **Tier 1 (always):** mark the record terminal, free the seat, promote next.
  The queue stops being blocked — systemic damage ends even if the zombie
  thread lingers.
- **Tier 2 (setting-gated):** reclaim the resource by terminating the wedged
  llama-server process; the residency arbiter respawns it clean by design
  (observed 2026-09-21, respawn + healthy probe in ~2 min). Gated behind
  `watchdog_kill_reclaims_seat` (default ON for local seats) because it costs
  a cold-cache reload (~50s). The task record must log which tier fired.

## Safety rails

- Cloud seats are exempt from queueing (pass-through) and from tier-2 kills;
  the watchdog still assesses them but `kill` only marks the record.
- Chain steps queue like everything else — a chain never jumps the line.
- `stop_requested` finally works: the supervisor honors it within one cadence.
- The user-facing notice (from seat_queue.USER_NOTICE) must state that local
  AI runs one job at a time and needs time to run.

## Contract pinned by the red tests

- Second spawn to a busy local seat creates NO thread and carries the notice.
- Worker completion promotes FIFO.
- Supervisor `kill` frees the seat, marks `failed:watchdog-kill`, promotes.
- Cloud spawns are unaffected.
- Chain-step tasks queue like ordinary tasks.
- `stop_requested` produces a kill within one assess pass.
- Tier-2 reclaim only fires when `watchdog_kill_reclaims_seat` is enabled and
  the seat is local.
- Supervisor default cadence is ≤30 seconds.

## Out of scope

- UI: task-tray queue positions and richer notice styling (separate track).
- Any change to sensitive subsystems (privacy/, governance/, egress gate,
  credential store) — none is needed and none is authorized.
