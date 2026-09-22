# Seat scheduling, the durable queue, and asking before spending

Status: PART BUILT (2026-09-22). This note says which parts, what was already
here, what was broken, and what is deliberately left.

The ask, verbatim:

> "We were also working on queueing new tasks and resuming tasks that got
> interrupted by server crashes or unexpected restarts. We've lost a lot of
> token spend on partially completed tasks... I want Friday to know when the
> local model is occupied and ask the user, either with UI or in chat, if it's
> ok to assign a task out to a cloud model when local seat is busy. We need to
> juggle these models appropriately for the user's hardware."

---

## 0. The hardware, measured today, not recalled

Every fact below was read off this machine on 2026-09-22, because two of the
remembered ones were wrong and the design depends on them.

| claim | verdict |
| --- | --- |
| 12GB RTX 4070 | **confirmed** — `NVIDIA GeForce RTX 4070, 12282 MiB` |
| the brain occupies ~11.6GB | **confirmed** — 11,600 MiB used, **413 MiB free** |
| it cannot be co-resident with the Ollama sidekicks | **confirmed** by the 413 MiB |
| the brain is `qwen3.6-35b-a3b-iq4nl` | **wrong** — it is `bonsai2:27b`, `Ternary-Bonsai-2-27B-PTQ1_0.gguf`, pinned by `llama-server.exe` on port 8090 since 08:51:51 |
| current mode is sidekick-resident, brain summoned on demand | **wrong, and inverted** — the brain is PINNED and owns the card. There are no resident sidekicks: Ollama is not running at all (`friday.log` repeats "no Ollama daemon listening at http://localhost:11434"), and `capability_routing` points every non-reasoning role at cloud |

That last row changes the problem. The contention this scheduler has to manage
today is **not** brain-versus-sidekick over VRAM. It is **many tasks wanting
the one `bonsai2:27b` seat**, with cloud as the only alternative. The
brain/sidekick standoff is a real failure mode for a configuration this machine
is not currently in; the single-seat queue is the failure mode it is in right
now.

I could not find the recorded brain-load-versus-sidekick standoff incident in
the repo. It is not contradicted — it is simply not written down anywhere I
could find, so nothing here is built on it.

---

## 1. What already existed (and mostly works)

Substantial prior work, all live:

* `services/seat_queue` — one job per local seat, FIFO, with an honest
  `queued-for-seat` status instead of invisible serialisation at the HTTP
  layer. Built 2026-09-21 for exactly the regression the maintainer hit.
* `services/seat_supervisor` — the glue: admission at spawn, promotion on
  completion, watchdog enforcement from outside the worker thread.
* `services/seat_admission` — a task queues under the seat it will really run
  on; a faulting resolver fails LOCAL so a fault cannot exempt anything.
* `services/residency_arbiter`, `services/arbiter` — GPU ownership, leases,
  pinned llama-server processes the Arbiter spawns rather than models Ollama
  may evict on its own criteria.
* `services/work_queue` — accumulating work into one lease, because waking the
  heavy model costs ~53s before its first token.
* `services/approvals` — the human gate, with policy classes, dissent checks,
  idempotency per subject, and decision hooks.
* `services/reconcile` — boot reconciliation into the owning conversation.

So (b) "seat-aware scheduling" was not missing. It was **built and broken in
one specific place**, and the durable and consent halves were missing.

---

## 2. What was broken: promotion never happened

`SeatSupervisor._start` calls `thread_factory(record)`. `agent.
_start_pending_task_thread` took a task **id** and did
`_PENDING_TASK_THREADS.pop(task_or_id, None)`.

A dict is unhashable. `dict.pop` only swallows that on an *empty* dict — and
the dict is non-empty **exactly when a task is parked waiting for a seat**,
which is the only situation promotion ever happens in.

Reproduced against the real supervisor and the real factory:

```
probe-1: admitted as running
probe-2: admitted as queued-for-seat
-- first task ends; the queued one should now be promoted --
PROMOTION RAISED: TypeError unhashable type: 'dict'
```

So: the second task aimed at the busy local seat was admitted, queued, shown a
correct "waiting for the seat" status — and then **never started when the seat
freed**. The traceback surfaced in the finishing worker's `finally` block and
in the cancel route, nowhere near the task it stranded.

Every existing seat test injects its own thread factory, so none of them could
see it: they tested the supervisor's side of the contract, and the defect was
on the other side. `tests/unit/test_seat_promotion.py` drives the real pair;
6 of its 7 cases fail against the old signature.

This is a direct cause of lost work, and it is fixed.

---

## 3. (a) Durable queue and resume

Three different kinds of interrupted work, three different correct answers.
Conflating them is what made the old behaviour lossy.

**Mid-flight with a checkpoint.** `services/task_resume` (built earlier today)
saves the agent loop's transcript at every tool boundary. A restored
transcript carries every completed tool's *result*, so resuming re-buys
nothing and re-runs nothing. Boot now offers it.

**Mid-flight with a tool in flight.** The process died between dispatch and
result. Nothing on disk can say whether the side effect landed. Gated unless
the tool is Ring 0 — the line is at Ring 0, not Ring 2, because the question
is not "how dangerous is this tool" but "does running it twice differ from
running it once". Ring 1 is a local write, so it gates.

**Waiting for a seat.** It never started. Nothing was spent and no side effect
was taken, so re-queuing is lossless. New `reconcile.readmit_queued()`.

These needed their own path because `_spawn_task` parks the worker **Thread
object** in `_PENDING_TASK_THREADS` while a task waits, and a thread object
does not survive its process — so re-admission means spawning again from the
recorded prompt, not re-wiring.

They were also the worst-off case: `task_journal.reconcile_on_boot` only looks
at `running`/`queued`, so a `queued-for-seat` record was not even marked
interrupted. It kept a status saying it was about to start, forever, with
nothing left in the process that could start it.

### A dead function found on the way

`reconcile_tasks()` imported `TASKS`/`TASKS_LOCK` from `agent_friday.core`.
They live in `services/agent`. The ImportError was caught and the function
returned `{"interrupted": []}` — so **it never marked a single task
interrupted in production**, on any boot, ever. It reported "0 item(s)
adopted" and that was accepted as good news.

Its test was green because it did
`monkeypatch.setattr(core, "TASKS", {...}, raising=False)`: `raising=False` on
a name that does not exist *creates* it, so the test built the world the code
wanted instead of the one it ran in. Both tests now patch what production
reads, and reverting the import fails 8 of them.

---

## 4. (c) Asking before cloud spend

`services/cloud_spill`. When a task has waited on the busy local seat for more
than `cloud_spill_min_wait_s` (60s), one approval card is raised through the
existing `approvals` queue — not a second consent path.

Three rules it is built around, all standing ones:

* **Never silently substitute a model.** The card names the seat it is waiting
  for *and* the one it would move to. The answer is recorded, and the seat
  change is reported into the conversation that asked for the work.
* **Never nag, never block.** The task is *not* parked pending an answer. It
  keeps its place, and it starts locally the moment the seat frees whether or
  not anyone ever opens the card — at which point the card is **withdrawn**,
  because a stale yes answered later would pay a cloud provider to redo work
  already running. One card per task, ever. Saying no changes nothing at all.
* **Money is the only reason to interrupt.** A spill between two local seats
  would not be asked about. This fires only when the alternative is paid.

**The budget informs this, and no longer kills anything.**
`max_task_input_tokens` used to terminate a running task over a token count
that was 96.4% cache reads and $3.14 of real spend
(`docs/audits/2026-09-22-token-ceiling-forensics.md`). It is advisory now, and
this card is the moment that advice was always for: today's measured spend
from `costs.db` appears on the question, *before* the money is spent, instead
of a guillotine after. The numbers inform the answer; they never decide it.

Settings: `cloud_spill_ask` (default on), `cloud_spill_min_wait_s` (60).

---

## 5. What is NOT built, and should be its own piece of work

Stated plainly rather than half-done.

1. **Co-residency arbitration between the brain and the sidekicks.** The
   failure the maintainer described — a brain load and a sidekick tool-turn
   fighting over the card and neither finishing — is a *residency* decision,
   and `residency_arbiter` is the module that owns it. Nothing here touches
   it. It also does not arise in the current configuration, where the brain is
   pinned and Ollama is not running. This needs its own session, starting from
   whether sidekick-resident mode is still wanted at all.

2. **The seat queue's own state is still in memory.** `seat_queue.SeatQueue`
   is pure functions over dicts. Queued tasks now survive a restart via the
   task ledger and `readmit_queued`, but the queue's *ordering* does not —
   re-admitted tasks rejoin at the back in whatever order boot finds them.
   FIFO across a restart needs the queue itself persisted.

3. **`_oai_agentic_loop` is not checkpointed.** Every local seat and every
   OpenAI-compatible provider runs through it, so the resume work currently
   covers the Anthropic path only. Its transcript has the same append-only
   property, so this is unbuilt rather than impossible — and given the
   maintainer's complaint explicitly includes "work done by the local model",
   **this is the highest-value remaining item.**

4. **Two boot reconcilers.** `task_journal.reconcile_on_boot` (via
   `agent._restore_tasks_from_journal`) and `reconcile.run_at_boot` both walk
   interrupted work with different rules and different status vocabularies.
   They agree today by luck, not by construction.

5. **No UI for the resume offer or the spill card.** Both reach the user as
   notifications and as conversation messages. The task tray shows
   `resumable`/`resume_reason` on the record but has no button; the endpoints
   (`/api/tasks/<id>/resumable`, `/api/tasks/<id>/resume`) exist and are
   unwired in the front end.

6. **20+ task records on this machine cannot be decrypted** ("GCM auth tag
   mismatch"), written under a vault passphrase since rotated. Resume
   checkpoints inherit that exposure: rotate again and every checkpoint on
   disk becomes unreadable. `resumability()` now says so rather than
   reporting lost work as absent work, but nothing recovers them and other
   `read_state` callers still conflate the two cases.
