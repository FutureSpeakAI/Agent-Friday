# Task visibility — the transparency rule pointed inward

> **Status:** partially-implemented
> **Last verified:** 2026-09-06
> **Implementation:** phase 1 built — `services/task_journal.py`, the journal-backed `_spawn_task` / `_task_set` / `_task_log` / `_restore_tasks_from_journal` in `services/agent.py`, `routes/tasks.py` (journal read, state-aware delete, retention), the boot hook in `server.py`, the `task_journal` settings block; phases 2–5 not built. Specification for everything else: Builds on `services/agent.py` (TASKS, `_task_set`, `_task_log`, the two agentic loops), `routes/tasks.py`, `services/activity_ledger.py`, `services/cost_meter.py`, `services/spend_guard.py`, `services/approvals.py`
> **Supersedes / superseded by:** —
> **Written:** 2026-09-06

## Implementation notes

**Phase 1 is built** (durable journal, state snapshot, index, boot
reconciliation, encryption at rest, user delete, retention setting). Phases
2–5 (required emission at loop checkpoints, decisions, the query surface, the
tray, orchestrator credentials) are not. The maintainer's rulings on the §7
questions, each built as a reversible setting or route rather than a baked-in
assumption: retention is a user setting defaulting to keep-forever with a
visible delete; model-reasoning capture defaults on (`capture_reasoning`,
consumed in phase 2); the user sees everything, an orchestrator's digest
carries decisions/status/model/cost and gets reasoning only on explicit
request through the sealed gate with a ledger row; interrupted tasks are
marked and offered for re-run, never resumed; orchestrators get a scoped
read-only credential (phase 5); journals are encrypted under the vault key.

The maintainer's question, verbatim: *"Astra and Fable
were unable to see any of the running tasks. It's like they were unable to
check in on those agents. I also don't have full visibility on what they are
doing, what they are reasoning, etc. How should we implement full visibility
to both our orchestrator and our user?"*

The rule this document holds itself to is Friday's own transparency rule
turned inward: **anything the system does that nobody can reconstruct
afterward is a defect.** Under that standard the current tree has one defect
with four faces, and this document specifies one record and two surfaces to
close all four.

Method: STORM. §1 is ground truth read from the code at `4fae602`; §2
interrogates the question from five perspectives; §3 argues the case against
at full strength; §4 is the synthesis, with rules **TV1–TV14**; §5 says how
each rule is proven on the real path; §6 phases the build; §7 names the
questions that stay open.

---

## 1. Ground truth (VERIFIED at `4fae602`)

**The task registry is process memory and nothing is behind it.**
`services/agent.py:2289` declares `TASKS = {}`. `_spawn_task` (`:2790`)
creates a record with `task_id, name, description, prompt, status, created,
started, ended, log, result, on_complete, chain, chain_step, model`.
`_task_set` (`:2308`) mutates it in place; `_task_log` (`:2297`) appends
strings to `log`; `_task_snapshot` (`:2316`) copies it for the API. No write
to disk occurs at any of those points. `routes/tasks.py:47` (`GET /api/tasks`)
and `:160` (`GET /api/tasks/<id>`) read the dict. A restart empties it.
Measured today: after a restart, `/api/tasks` returned an empty list and nine
records, including an in-flight ad-campaign job, were gone; only the files the
job had already written survived.

**Orbs are the same shape.** `core/__init__.py:1426` `PROCESSES = {}`, record
at `:1441` (`id, name, label, category, icon, model, color, status, progress,
steps, log, task_id, eta_s, started`). `GET /api/processes` (`routes/tasks.py:268`)
serves it; the UI polls it every few seconds (`index.html:41019`) and draws the
orbs. Same erasure on restart.

**What is durable today, and what it lacks.**
- `services/activity_ledger.py` — append-only JSONL under `~/.friday/`, three
  event kinds only (`model_invocation`, `tool_call`, `subagent_spawn`), each
  metadata-only by schema (`_ALLOWED_FIELDS` whitelist, text capped), read by
  `GET /api/activity` with a `task_id` filter. It records *that* a model or
  tool was called, never *what was decided* or *why*, and nothing in the UI
  reads it per task.
- `services/cost_meter.py` — every model call lands in `costs.db` with an
  attribution (`register_task_attribution`, `:254`; `lookup_task_attribution`,
  `:266`) so spend per task is already recoverable. The task record never
  carries it.
- `~/.friday/work_queue/queue.json` (`services/work_queue.py:90`) and
  `~/.friday/workflows/*.json` (`agent.py:2803`) — chains and queued work
  survive a restart; the tasks that *execute* them do not.
- `services/spend_guard.py` — `~/.friday/spend_halts.jsonl` plus a
  high-priority notification per halt: the one place today where "what
  stopped, why, and what it had spent" is written as it happens.
- `ops/forensics-snapshot.py` — an *external* scheduled task that polls
  `/api/tasks` and `/api/processes` and copies them to
  `~/.friday/forensics/` because nothing inside the process does. Its own
  docstring records the 2026-08-24 loss of a six-task run to three restarts.
  Its existence is the clearest evidence of the gap: the capture lives
  outside the thing it captures.

**What the loops know and throw away.** Both agentic loops append to a
`tool_trace` per tool call (`agent.py:6758–6787` Anthropic, `:7046–7066`
OpenAI-compatible) — name, input, result (capped). That trace is returned to
the caller, consumed once by the fabrication detector
(`services/completion_receipts.py`, `tool_receipts.py`) and by
`_summarize_task_outcome` (`:2406`), then dropped. The assistant prose between
tool calls, the seat/model each call actually ran on, the egress-gate verdict
on each payload, the fallback-ladder leg taken, the retry count, the approval
or spend-cap decision — every one of these is computed at a known line and
none is written anywhere a reader can find it later.

**How a user or an external agent can act on a task today.**
`POST /api/agent/steer` (`routes/tasks.py:232`) queues a follow-up prompt
into a running task; `POST /api/processes/<pid>/cancel` (`:344`) cancels.
There is no pause, no "stop after this step", and no per-task view beyond a
400-character result preview (widened to 4,000 today with a visible cut
marker). Authentication: loopback is trusted (`FRIDAY_TRUST_LOOPBACK`, default
on); the API token in `X-Friday-Token` rotates every restart
(`core/__init__.py:317`); a remote caller needs `FRIDAY_REMOTE_KEY`. An
external agent on the same machine can already call `/api/tasks`; what it
gets is a list that is empty after every restart and says nothing about
what a running task is doing.

**The nearest existing patterns, both landed today.**
- The Approvals card (`6f86d9e`): a backend queue that had no surface,
  given a card in the System workspace, a deep-link target the notification
  tray actually navigates on, and a test that pins the tab handler in both
  HTML files so the surface cannot silently disappear again.
- The hard stop (`5180f5a`): a ledger row plus a notification per event,
  deduplicated per thing-per-period, naming what stopped, why, spend versus
  limit, and how to resume; a guard that falls back loudly when it cannot
  evaluate.

---

## 2. The question, from five seats (STORM interrogation)

**The user, mid-flight.** What is Friday doing *right now*, in one line? Is
it stuck? What has it cost so far? Can I tell it something without killing
it? If I restart, do I lose the record? Today: an orb with a label and a
progress bar without a scale; a result preview at the end; steer and cancel;
and yes, a restart loses everything.

**The orchestrating agent (Fable or Astra).** Give me the list of running
jobs, a compact digest of one, and a way to tail its events from where I
left off — without a human relaying. Today: `/api/tasks` (in-memory, empty
after restart), a 4,000-character result string, no events, no digest, and
an auth token that changes every restart.

**Friday as the emitter.** I decide things at known lines: which seat, which
ladder leg, whether the gate withheld a span, whether the cap tripped,
whether an approval is pending, whether to retry. Each decision is a
present-tense fact with a reason attached in a variable that goes out of
scope. Writing it costs one append; not writing it is what makes the next
forensics session cost a day.

**The privacy boundary.** A journal of what a task did contains prompts,
tool results and vault-derived text. On disk under `~/.friday/` it is the
same class of material as `chat_history.json`. But the moment it is served
to Fable or Astra it *leaves the machine* — those agents are cloud-backed.
A query surface for external agents is an egress path and must be treated
as one: sealed through the same gate as any tool result, never bypassed
because "it's just status".

**The operator after a crash.** What was running when the process died?
What had each task done? The 2026-09-04 stack-overflow crash was
diagnosed from `friday.log`, `orbs.jsonl` and `tasks.jsonl` going silent
at 08:01 — files the forensics snapshotter had written from outside, not
anything the process kept for itself.

The five seats want the same thing under different names: **a durable,
append-only, per-task record written at the moment each fact is known,
with named readers for every field.** Design that once and neither
consumer is a special case.

---

## 3. The case against, at full strength

**"This is logging. We have `friday.log`."** A log is a stream of strings
without identity; you cannot ask it "what is task 7 doing" or "what did the
gate withhold on the third call". The record here is keyed by task and
sequenced, with typed fields a program can read. The log stays; this is not
that.

**"Writing every step to disk slows the loop."** One JSON line per
checkpoint on a local SSD is microseconds against a model call measured in
seconds. `activity_ledger.record` already does exactly this on every model
and tool call and nobody has noticed it. The spend ledger does it per halt.
The cost is not the write; it is the discipline of emitting.

**"An optional trace API keeps the hot path clean."** This is the trap.
Left to goodwill, coverage goes uneven: the Anthropic loop emits, the
OpenAI-compatible loop forgets, chains half-report. A trace that *looks*
complete while being partial is worse than no trace — a reader trusts it
and reconstructs a false story. Emission is therefore required at defined
checkpoints and the checkpoint is part of the loop's contract (TV3), and
the proof is a coverage test that counts iterations against checkpoints
(§5), not an inspection.

**"Reasoning capture is a privacy hazard and a cost."** True on both
counts, and that is why *model* reasoning capture is an open question for
the maintainer (§7) rather than a default picked here. What this document
requires is the *system's* reasoning — decisions Friday's own code made,
with the reason it already holds in a variable. That costs nothing extra
and reveals nothing the code did not already act on.

**"Restart-durability is a separate feature."** It is not; it falls out.
If the record is written as facts become known, the only thing a restart
can lose is the fact that was being computed at the instant of death. The
boot-time reconciliation (TV8) then has everything it needs to say
"interrupted at step 4 of 9, last checkpoint 08:01:12".

**"External agents should get a purpose-built API."** A second record for a
second consumer is how two sources of truth are born. The orchestrator
reads the same journal the tray reads; it gets a cursor and a digest
because those are the two things a program needs that a human does not.

---

## 4. Synthesis

### 4.1 One record: the task journal

Per task, a directory `~/.friday/tasks/<task_id>/` holding:

- `journal.jsonl` — append-only, one event per line, monotonically
  sequenced (`seq`), never rewritten. This is the source of truth.
- `state.json` — the current materialised snapshot (what `_task_snapshot`
  returns today, plus §4.3's summary fields), rewritten atomically
  (temp + rename, the `work_queue.py:113` shape) on every change. This is
  the read-fast view; if it and the journal ever disagree, the journal wins
  and a rebuild from it is a function, not a procedure.
- `~/.friday/tasks/index.jsonl` — one line per task at creation and at
  terminal status, so listing does not require opening every directory.

Event shape: `{"seq", "ts", "task_id", "kind", ...fields}`. Kinds and the
**reader of each field** (a field with no reader is dropped — TV5):

| kind | fields | who reads it, when |
|---|---|---|
| `created` | name, description, prompt (capped), chain, chain_step, requested_by, model | tray card title; orchestrator listing; boot reconciliation |
| `started` | seat, model, provider, worker (thread/pid) | tray "running on"; orchestrator digest |
| `checkpoint` | iteration, phase (`model_call` / `tool_call` / `steer` / `chain_step`), summary (one line, ≤200 chars) | **the tray's "now:" line** (the glanceable field); orchestrator tail; crash reconstruction |
| `model_call` | seat, model, provider, tokens_in/out, cost_usd, gate_verdict (`allowed` / `redacted:n` / `withheld`), cache_hit | cost-so-far in the card and digest (joined with `costs.db`); privacy panel "what left the machine"; post-hoc audit |
| `tool_call` | name, args (capped, scrubbed), ok, duration_ms, result_summary | timeline in the task drawer; fabrication detector (replaces the transient `tool_trace`); orchestrator tail |
| `decision` | point (`seat_select` / `ladder_fallback` / `retry` / `gate` / `approval` / `spend_cap` / `chain_advance` / `evaluate`), chosen, alternatives (list), reason | **the "why" a human asks for**; the drawer's decision list; orchestrator digest; audits |
| `steer` | message (capped), source (`user` / `agent:<name>`) | drawer timeline; orchestrator sees that its own steer landed |
| `halt` | cause (`spend_cap` / `approval_pending` / `cancelled` / `error` / `timeout` / `interrupted`), detail, spend_usd, resume_hint | tray + notification (the spend-guard pattern, TV9); boot reconciliation; orchestrator |
| `ended` | status, result (full), summary, cost_usd, iterations, duration_s | tray result; orchestrator; cost panel per-task total |
| `heartbeat` | — (ts only) | liveness: "last seen 12 s ago" in the tray; orchestrator's stuck-detection; boot reconciliation's "died at" |

Assistant prose between tool calls is recorded as a `checkpoint` summary
(first 200 characters) — that is what a reader mid-flight wants. The full
prose, and any provider-reported thinking (Anthropic extended thinking,
Gemini thoughts), is the **model reasoning** whose capture is §7 Q2.

### 4.2 Required emission at defined checkpoints (TV3)

Emission is a step of the loop, not a hook a caller may forget:

- `_spawn_task` → `created`. `_task_worker` start → `started`. Each
  iteration of `_call_claude_agent` and `_oai_agentic_loop` → `checkpoint`
  before the model call, `model_call` after it, one `tool_call` per tool,
  `decision` at each of the seven named points. Steer dequeue → `steer`.
  Any exit → `ended` or `halt`. A heartbeat thread per running task →
  `heartbeat` every 10 s (the `hang_watchdog` interval).
- The seven decision points already exist as code paths with a reason in
  hand: seat selection (`local_seats` / `residency_arbiter`), the
  fallback ladder in `model_router`, retry in `_run_task` and chain
  retry, `_seal_or_block`'s gate outcome, `approvals.gate_action`,
  `spend_guard.check`, `_advance_task_chain`, `_evaluate_output`. Each
  gains one `journal.decision(...)` call at the line where the choice is
  made; the reason string is the one the code already computes.
- `_task_set` and `_task_log` become thin writers over the journal
  (`state.json` update + `checkpoint`), so the ~40 existing `_task_log`
  call sites emit for free and the in-memory `TASKS` dict becomes a cache
  of `state.json`, rebuilt from disk at boot.

### 4.3 The state summary (what `state.json` adds)

`now` (last checkpoint summary), `last_seen` (last heartbeat), `cost_usd`,
`iterations`, `seat`, `model`, `provider`, `decisions` (count, and the last
three), `halted` (cause + resume hint, if any), `journal_seq` (for cursors).

### 4.4 Surface one: the live view for the user

Extends what exists rather than adding a workspace:

- **Glanceable**: the orb keeps its label; the Task Tray row gains the
  `now:` line, `last seen`, and cost so far. A task whose heartbeat is
  older than 30 s is drawn as *stalled*, not *running* — the tray must
  never show a green orb for a dead thread.
- **Readable mid-flight**: opening a task shows a timeline built from the
  journal — checkpoints, tool calls, decisions with their reasons, halts —
  tailed live over `GET /api/tasks/<id>/events?since=<seq>` (SSE, with the
  polling fallback the tray already uses).
- **Interruptible**: the existing steer box and cancel button move into
  the drawer, plus **stop after this step** (a flag the loop checks at each
  checkpoint; it ends with `halt: cancelled` and a complete record, unlike
  a hard cancel). Every steer is journaled with its source.
- **After a restart**: the tray lists interrupted tasks with their last
  checkpoint and a *Re-run* affordance; nothing resumes on its own (§7 Q4).

Built the way the Approvals card was: same card conventions, a deep-link
target the tray navigates on, and a structural test pinning both HTML files.

### 4.5 Surface two: the query surface for an orchestrator

Same journal, three reads:

- `GET /api/tasks?status=running|interrupted|all` — from `index.jsonl` +
  `state.json`, survives restarts.
- `GET /api/tasks/<id>/digest` — `state.json` plus the last N checkpoints
  and decisions, sized for a prompt (≤ 2 KB by default; `?full=1` for the
  whole journal).
- `GET /api/tasks/<id>/events?since=<seq>` — cursor tail, JSON or SSE.

Plus write access an orchestrator already has: `POST /api/agent/steer`
(journaled with `source: agent:<name>`), cancel, stop-after-step.

**Egress.** Every response to a non-loopback or token-authenticated
principal that is not the user's own browser passes through
`egress_gate.seal_outbound` as a tool result would (TV11). A journal line
that carries TIER_2/3 material is redacted in the response and the
redaction is itself journaled as a `decision` of point `gate`. The
orchestrator sees that something was withheld; it does not see what.

**Authentication.** Loopback callers are trusted today and the per-restart
token is embedded in the served UI; an external agent on the same machine
can already read `/api/tasks`. Whether Fable/Astra should hold a durable,
scoped credential (read-only journal access) rather than loopback trust is
§7 Q5.

### 4.6 Boot reconciliation (TV8)

On start, before the scheduler runs: read `index.jsonl`; for every task
whose `state.json` says `running` or `queued`, append
`halt: interrupted` with the last checkpoint and heartbeat, rewrite
`state.json`, and push one notification listing them (deduplicated per
boot) — the spend-guard voice: what was interrupted, where it got to, what
it had spent, and that *Re-run* is available. Chains and queued work in
`work_queue` and `workflows/` are reconciled against the same list so a
chain step whose task died is marked, not silently re-queued.

### 4.7 Rules

- **TV1 — One record.** The journal is the only source of truth for task
  state and history; `TASKS`/`PROCESSES` are caches of it.
- **TV2 — Written as it changes.** Every state change is on disk before
  the loop proceeds; completion writes nothing that was not already there
  except `ended`.
- **TV3 — Required emission.** Checkpoints are steps of the loop. A loop
  path that can advance an iteration without a checkpoint is a bug, and
  the coverage test in §5 fails on it.
- **TV4 — System decisions are journaled with their reason** at the seven
  named points, using the reason the code already computes.
- **TV5 — No unread field.** Every field names its reader in §4.1; a field
  without one is removed, not kept "for later".
- **TV6 — Two surfaces, one record.** The tray and the query surface read
  the same journal through the same routes; the digest is a view, not a
  second store.
- **TV7 — Liveness is measured, never assumed.** A task is *running* only
  while heartbeats are fresh; a stale heartbeat renders as stalled.
- **TV8 — Restart reconciles, never erases.** Interrupted tasks are marked
  and announced; the record is complete up to the last checkpoint.
- **TV9 — Halts are loud.** Every halt is a journal row plus a
  notification in the spend-guard shape: what, why, spend, how to resume.
- **TV10 — Interruption is journaled.** Steer, cancel and stop-after-step
  are events with a source; a task can be stopped without losing its record.
- **TV11 — The query surface is egress.** Responses to external principals
  are sealed through the gate; withholding is journaled.
- **TV12 — Journal writes never break work.** A failed append marks the
  task `unrecorded` in `state.json` and notifies once; it does not raise
  into the loop. A task that runs unrecorded is visibly so.
- **TV13 — Retention is a policy, not an accident** (§7 Q1); until ruled,
  nothing is deleted.
- **TV14 — Behavioural proof.** Every rule is proven by a test that drives
  the real path; a static check that a field is written does not count.

---

## 5. Proof (TV14)

- **Coverage**: run a task through each agentic loop against a fake
  provider that makes N tool calls; assert the journal has exactly N
  `tool_call`, N+1 `checkpoint`, N+1 `model_call` and the expected
  `decision` points. Red on any loop path that skips emission.
- **Durability**: start a task in a subprocess, kill the process mid-loop,
  assert `journal.jsonl` ends at a checkpoint and `state.json` is
  consistent; boot the app, assert the task reads `interrupted` with that
  checkpoint and one notification was pushed.
- **Liveness**: stop the heartbeat thread, assert the tray API reports
  `stalled` within 30 s.
- **Surface**: structural pins on both HTML files for the drawer, the
  `now:` line and the deep-link handler (the Approvals-card test shape);
  an API test tails `events?since=` across a restart.
- **Egress**: journal a `tool_call` result containing TIER_3 text; assert
  the digest served to a token-authenticated caller is redacted and a
  `gate` decision was journaled; assert the same digest to the loopback
  browser is intact.
- **Placebo guard**: the settings-readers check cannot see enforcement
  (documented today), so every control this adds — stop-after-step,
  reasoning capture if enabled — has a test that drives the real loop.

---

## 6. Phases

1. **Journal + state** (`services/task_journal.py`), `_spawn_task` /
   `_task_set` / `_task_log` rewired, boot reconciliation, index. The
   forensics snapshotter becomes redundant for tasks and is retired from
   `ops/`.
2. **Required emission** in both loops and at the seven decision points;
   heartbeats; coverage test.
3. **Routes**: listing from disk, digest, events tail (SSE + poll), sealed
   for external principals.
4. **Tray**: `now:` line, stalled state, drawer with timeline, steer /
   cancel / stop-after-step, interrupted list with re-run.
5. **Orchestrator use**: document the three reads for Fable/Astra; add
   `source` on steer; decide Q5.

---

## 7. Open questions for the maintainer

These are decisions, not defaults picked here.

- **Q1 — Retention.** How long does a task journal live? Options: forever
  (disk grows with use; tasks are small), N days, or until the task's
  outputs are deleted. Until ruled, nothing is deleted (TV13).
- **Q2 — Model reasoning capture.** System decisions (TV4) are always
  journaled. Capturing the model's own reasoning — provider thinking blocks
  and full assistant prose between tool calls — costs tokens on providers
  that bill for it, stores the most sensitive text a task handles, and is
  the part the user asked about most directly. On by default, opt-in per
  task, or off?
- **Q3 — Shown versus stored.** The drawer proposes checkpoints, tool
  calls and decisions at full detail with reasoning behind a "show
  reasoning" toggle. Should the live view show less (summaries only) than
  the journal stores, and should the digest served to an orchestrator
  include reasoning at all?
- **Q4 — Interrupted tasks.** Mark and offer *Re-run* (proposed), or
  attempt automatic resume from the last checkpoint? Resume means
  re-running with the journal as context; it can repeat side effects.
- **Q5 — Who may query.** Loopback trust (today's model) means any local
  process can read every journal. Should Fable/Astra hold a scoped,
  durable read-only credential instead, with the per-restart token
  reserved for the browser?
- **Q6 — Journal encryption.** Store journals under the vault key when a
  passphrase is set (they carry the same class of material as chat
  history), or plaintext under `~/.friday/` like `chat_history.json`
  today?
