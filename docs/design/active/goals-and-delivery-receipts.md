# Goals with typed blockers, and delivery receipts

> **Status:** proposed (spec only; nothing in this document is built)
> **Last verified:** 2026-09-26 against main `8072ec99`
> **Implementation:** none yet. Builds on `services/task_ledger.py`, `services/task_resume.py`, `services/turn_budget.py`, `services/approvals.py`, `services/approval_executor.py`, `services/completion_receipts.py`, `services/tool_receipts.py`, `services/reasoning_trace.py`, `services/voice_engine.py`, `services/voice_live_channel.py`, `governance/action_gate.py`, `governance/proof_of_integrity.py`, `services/provenance.py`, `services/goals.py`
> **Supersedes / superseded by:** neither. Complements `docs/design/active/task-visibility.md`, `docs/design/active/tasks-tray-honesty.md`, `docs/design/active/action-creation-layer-spec.md`
> **Written:** 2026-09-26

Two patterns are borrowed from ByteDance's DeerFlow 2.0 (MIT licence;
`README.md`, sections "Session Goals" and "Request Trace Correlation"). Only the
ideas are borrowed; no code is.

- **Session goals:** one completion condition per thread, judged after every run
  by an evaluator that must return a typed blocker with visible evidence.
- **`run.delivery` receipts:** one terminal receipt per run, persisted before the
  run's terminal status, including zero-output and crash-recovered runs.

Every claim about Friday below cites the code at `path:line` on main `8072ec99`.
Paths are relative to `src/agent_friday/` unless they start with `index.html`,
`docs/` or `tests/`.

---

## 0. Summary for the owner (one page)

**What changes for you.**

1. **You can give Friday a finish line.** Type `/goal all 40 invoices are filed
   and the summary is in my Documents folder`, or say "keep going until …" in
   voice. The goal shows as a thin line above the message box. After every run,
   Friday checks the goal against what actually happened and gives one of two
   answers:
   - **"Met"**, with the proof; or
   - **"Not yet"**, with one of five named reasons:
     - it needs something from you;
     - it is waiting on something outside (an approval card, an email reply);
     - the run failed;
     - there is not enough evidence yet;
     - there is simply more work to do.

   Friday carries on by herself only in the last case. That is "more work to do";
   when evidence is missing, she may run one check that uses read-only tools.
   Otherwise she stops and tells you why, in chat or out loud.
2. **"Done" means done.** Every chat answer, background task, workflow step and
   voice hand-over ends with a receipt: the files it wrote (with fingerprints),
   the messages it sent or queued (with their approval-card numbers), and each
   thing it says it did, marked "verified" or "not verified".
   - Friday may not tell you something is finished unless the receipt shows it.
   - An approval card that is still waiting is "waiting", never "done". This is
     already true inside the task log (`services/agent.py:9804`); receipts extend
     it to everything Friday says.
   - Receipts are signed and chained, so a later edit shows. You can open them
     from a task card, from a chat reply, or from the Ledger panel.

**What it does not change.** There are no new built-in limits. Friday already
stops a job only when:
- it is going in circles (the loop guard, `services/turn_budget.py:260`);
- it stops making progress;
- you press Stop; or
- it hits a limit you set.

Goals add a "no progress" check of the same kind. It fires when two checks in a
row say the same thing and nothing new was done; it is not a count. If you want
a hard ceiling on how many times Friday continues on her own, you set it, in the
same place as the other spending caps.

**What it costs.**
- **Money.** One short evaluation after each run.
  - On your local model it is free and takes roughly 10–15 seconds on the
    current 131K Bonsai2 seat.
  - If no local model is available, the check runs on the model that already did
    the work: never a different cloud model, and never on anything you marked
    local-only. That costs about half a cent (Haiku 4.5) to two cents (Opus 5.5)
    per check, and it is metered and shown in the cost panel like everything
    else.
  - Receipts cost nothing measurable: one fingerprint per file written and one
    signature per run.
- **Privacy.** Goals are judged on your machine by default. Receipts hold
  fingerprints, counts and card numbers, not the content of your files or
  messages, and they are encrypted at rest like the task journal.
- **Effort.** About four to five focused weeks of work in five phases. Phase 0 is
  a few days and fixes, first, three places that today count a waiting card or a
  refused action as a success.

**What to decide.**
1. Should a goal also be settable on a Workflow step from the Workflows screen at
   launch, or only on conversations and tasks first? §9 recommends the latter.
2. The default when no local model is available and the conversation is
   local-only. The recommendation is "don't check; show 'unchecked'" (§4.6).

---

## 1. What DeerFlow does, and what Friday takes from it

**DeerFlow session goals** (`README.md`, "Session Goals"):
- `/goal <condition>` attaches one active completion condition to a thread.
- After each run, a non-thinking evaluator model judges the visible conversation
  and must return a typed blocker (`missing_evidence`, `needs_user_input`,
  `run_failed`, `external_wait`, `goal_not_met_yet`) plus visible evidence.
- A hidden continuation is injected only when all of these hold:
  - the last assistant turn is durably checkpointed;
  - the blocker is `goal_not_met_yet`;
  - the thread did not change during evaluation;
  - a no-progress breaker has not fired.
- Its safety cap defaults to 8 continuations, and two identical non-progress
  evaluations stop it.
- New user input and `/goal clear` win over queued continuations.
- The goal clears itself when met.
- Setting or clearing a goal is refused while a run is in flight.
- The web UI shows the goal above the composer.

**DeerFlow delivery receipts** (`README.md`, "Request Trace Correlation" and
"Running the Application"):
- One terminal `run.delivery` receipt per run, including zero-output and
  crash-recovered runs.
- It is persisted before the durable terminal run status.
- Orphan recovery atomically claims the lease, then idempotently backfills the
  receipt.
- It is best-effort during an event-store outage.
- Runs that write output artifacts must present at least one file the run
  produced.

**What Friday keeps.**
- The five blocker names and "evidence every time".
- Continuing only on `goal_not_met_yet`.
- The four continuation preconditions.
- User input wins.
- Refuse goal changes mid-run.
- Goal shown above the composer.
- One receipt per run, including zero-output and recovered runs.
- Receipt persisted before terminal status.
- Idempotent backfill.

**What Friday changes, and why.**

| DeerFlow | Friday | Why |
|---|---|---|
| Fixed cap of 8 hidden continuations | No built-in cap. An owner-set `goals.max_continuations` (default unset), plus the no-progress breaker and the existing loop guard | The no-built-in-caps policy: `services/turn_budget.py:64` (`ROUND_BUDGET_DEFAULT = None`), with only the loop guard (`:247`, `:260`) kept by default |
| Evaluator judges the conversation text | Evaluator judges the receipt first and the text second. A "met" must cite receipt items that verified | Friday already has deterministic evidence: tool receipts, approval states and file hashes. Text can be forged by injected content; receipts cannot (§4.7) |
| Receipt "best-effort during an outage" | Receipt write failure makes the run report "unverified"; it never reports "done" | Friday's rule is that nothing claims success it has not verified |
| Evaluator model unspecified | Local by default, per the seat policy (§4.6) | Friday's privacy model |

---

## 2. What exists today (grounded)

### 2.1 Continuation, resume and caps

- **Task ledger.**
  - `services/task_ledger.py:58` (`new`) holds goal, plan, done, facts, files,
    next and rounds.
  - `:221` (`record_step`) appends a step, and counts only distinct
    (tool, args) pairs as progress (`distinct_steps`).
  - `:289` (`render`) is the pinned view. `:356` (`continuation_prompt`) starts a
    fresh leg. `:243` (`absorb_summary`) takes PLAN/FACTS/FILES/NEXT from
    compaction.
  - `:339` (`carried_text`) is what the taint record treats as outside content.
    `:193` (`tier_safe`) redacts structured personal data from step lines.
- **Continuation legs.**
  - `services/agent.py:3608` (`_task_worker_untraced`) continues a task in a
    fresh context when a leg ends on a per-turn limit, logging "Continuing in a
    fresh context …" (`:3790`).
  - It stops when a leg adds no new distinct step.
  - This is the no-progress breaker Friday already has; goals reuse it.
- **Crash resume.**
  - `services/task_resume.py:253` (`resumability`) and `:274`
    (`_ledger_resumability`) decide whether an interrupted task can resume.
  - `:83` (`MAX_ATTEMPTS = 2`) bounds crash loops. `:115` (`replay_safe`) allows
    only Ring-0 tools to be repeated.
  - `:481` (`_resume_from_ledger`) re-runs the task's worker from its ledger.
  - `services/agent.py:3281` (`_restore_tasks_from_journal`) auto-resumes at
    boot when `task_resume_auto` is on (`services/task_resume.py:107`).
- **Caps.**
  - Every built-in turn limit is `None` (`services/turn_budget.py:64`).
    `rounds_for` (`:208`) returns only owner-set values.
  - The loop guard (`:260`, `observe` at `:292`) fires on identical calls
    (`REPEAT_LIMIT_DEFAULT = 3`, `:99`) and on cycling patterns, never on an
    amount.
  - `take_last_stop` (`:152`) tells the worker which limit ended a leg.

### 2.2 Grading that exists

- **The quality evaluator.**
  - `services/agent.py:3369` (`_evaluate_output`) grades a finished task
    PASS/PARTIAL/FAIL/UNAVAILABLE on a local seat only, under
    `local_only("the quality evaluator")`, with a 90-second timeout (`:3358`).
  - It runs after the status is already set (`:3928`) and is explicitly
    advisory (`:3957`, "advisory only").
  - It pushes no cost attribution.
- **The evidence gate.**
  - `services/agent.py:3866-3870` marks a task `completed_unverified` when no
    tool (other than `spawn_task`) ran.
  - This is the only structural check, and it counts any tool call as evidence,
    including one that raised a card.
- **Integrity validators.**
  - Chat replies pass `services/model_router.py:2631`
    (`validate_toolcall_integrity`), which runs `_integrity_violations` (`:2563`).
  - `_integrity_violations` checks for pseudo tool calls,
    `completion_receipts.find_unreceipted_completion_claims` (`:330`),
    `find_unkept_promises` (`:109`) and `find_fabricated_constraints` (`:171`).
  - Chat also appends `tool_receipts.unbacked_claims` corrections
    (`routes/chat.py:2131`).
  - Background tasks run none of these.
- **Durable Goals.**
  - `services/goals.py` is a separate, larger feature: multi-week goals with
    milestones, a verify-and-repair loop through `services/qa_gates.py:140`
    (`evaluate_text`), approval-gated milestones (`services/goals.py:908`), and
    signed goal receipts (`:585`, `:601`, `:610`).
  - This spec does not replace it (§4.10).

### 2.3 Approvals, and "a card raised is not a success"

- **Card statuses.** `pending, auto_approved, approved, denied, expired,
  blocked` (`services/approvals.py:92`). A gated action is created `pending`
  (`:359`).
- **The approval executor.**
  - `services/approval_executor.py:128` (`_on_decision`) runs an approved card's
    tool exactly once.
  - It uses `approvals.claim_for_execution` (`services/approvals.py:409`) and
    `mark_used` (`:392`).
  - It posts the outcome back to the conversation (`:89`).
- **The model's side.**
  - A tool call that raised a card returns `[APPROVAL CARD RAISED] '…' was NOT
    executed` (`services/agent.py:8979`).
  - `_tool_call_status` (`:9813`) classifies that as `pending`, using
    `_TOOL_PENDING_SENTINELS` (`:9804`), and not as `ok`.
- **Three classifiers disagree.** This is the defect Phase 0 fixes.
  1. `services/agent.py:9790-9804`: the full list of deny and pending prefixes.
  2. `services/completion_receipts.py:28` (`FAILURE_SENTINELS`): missing
     `[BLOCKED`, `[GOVERNANCE HOLD]`, `[DECLINED]`, `[NOT RUN]` and
     `[APPROVAL CARD RAISED]`. So `receipt_ok` (`:40`) counts a held, declined
     or pending action as a success when chat checks completion claims.
  3. `services/task_journal.py:637` (`tool_call`): `ok` is False only for
     `[VAULT…`, `[Error…` or `error…`. Every other refusal, and a pending card,
     is journaled `ok=True` unless the caller passes `ok`.

### 2.4 Receipts and signed ledgers that exist

| Record | What it covers | Integrity |
|---|---|---|
| Decision BOM `~/.friday/decision-bom.jsonl` (`governance/action_gate.py:593`) | Every governance decision, with `args_hash` (`:674`) | HMAC per line with the governance key (`:585`), verified at `:596`. No chain |
| Governance key (`governance/proof_of_integrity.py:405`) | Signs the above, goal receipts and dissent events | OS keyring, falling back to a 0600 file |
| Ed25519 attestation key (`governance/proof_of_integrity.py:152`, `sign_payload` `:188`, `verify_manifest` `:259`) | Integrity manifest; content credentials | Public-key verifiable |
| Content provenance (`services/provenance.py:111` `hash_file`, `:281` `_append_ledger`, `:432` `verify_manifest`) | Generated media: sha256 plus a hash-chained ledger | Ed25519 plus a chain |
| Reasoning traces (`services/reasoning_trace.py:828` `_write_line_locked`, `:923` `verify`) | Each finished trace | Encrypted body, sha256 `prev` chain, HMAC `sig` |
| Goal receipts (`services/goals.py:585-640`) | Durable-goal milestone proof-of-work | HMAC |
| Per-turn tool receipts (`services/tool_receipts.py:43-66`) | Tools called in a chat turn | In memory only |
| Task journal (`services/task_journal.py:634`) | Per-task events | Encrypted at rest, no chain, no signature |

- **What does not exist yet.** There is no per-run artifact manifest.
  `write_file` (`services/agent.py:951`) returns "Wrote N chars" and computes no
  hash; `task_ledger.files` holds paths only.
- **There is no receipt viewer.** `docs/user-guide/approvals-and-receipts.md:117`
  says so.

### 2.5 Reporting "done"

- **`_report_task_completion`** (`services/agent.py:4145`) announces
  `Task finished` whenever the status is not `failed` or `error`. So
  `completed_unverified` and `cancelled` are both announced as "finished".
- **`_post_task_result_to_conversation`** (`:4179`) appends the result to the
  spawning conversation. It hands it to a live voice call through
  `services/voice_live_channel.py:45` (`deliver`) at `services/agent.py:4212`.
- **Voice hand-over.**
  - `services/voice_engine.py:715` (`_tool_delegate_to_friday`) spawns the task
    with the call's conversation id. It is refused in local-only mode (`:729`).
  - It tells the voice model "Do not guess the result."
  - The outcome comes back as free text: the task's reply, not a verified
    statement.

### 2.6 Workflows

- **Today.** A workflow chain is a sequence of spawned tasks: `chain` and
  `chain_step` on `_spawn_task` (`services/agent.py:4217`, `:4255`), with retry
  in `_retry_chain_step` (`:4688`).
- **Drafts** live in `services/workflow_plan.py` (`build` `:112`, `decide`
  `:231`). `services/workflow_overview.py` predicts which cards a draft will
  raise ("asks first").
- **There is no maker-checker second pass.** No step is independently checked
  before the next one starts. A grep of `services/workflow*.py`,
  `routes/workflows.py` and `docs/` for "checker", "maker" and "second pass"
  finds none. This spec adds it (§4.8).

### 2.7 UI elements to reuse

- **Composer.** `FridayChatInput` (`index.html:7701`) sits inside `ChatSurface`
  (`index.html:7801`), placeholder at `index.html:8712`. The strip rows above
  the input (voice state, notices) are the precedent for a one-line goal strip.
- **Stand-down banner.** `fridayStandDownBanner` (`index.html:6406`) is a
  full-width state bar with one action button: the pattern for "goal blocked:
  needs your input".
- **Task card and Ledger panel.** `TaskCard` (`index.html:38330`) and the Ledger
  panel's traces tab (`TracesViewer`, `index.html:37624`) are where a receipt
  opens.

### 2.8 Seat policy and the local-only guard

- `local_only_guard.local_only` (`services/local_only_guard.py:61`) makes every
  cloud transport refuse (`refuse_if_active`, `:113`), including Ollama `-cloud`
  tags (`:102`).
- `seat_policy.LOCAL_ONLY_SEATS` (`services/seat_policy.py:33`).
- `scheduler._resolve_local_seat` (`services/scheduler.py:107`) finds a serving
  local seat.
- `cost_meter.push_attribution` (`services/cost_meter.py:376`) and `meter`
  (`:589`) attribute spend by `kind`.

---

## 3. Questioning it from five perspectives (STORM)

Each perspective was asked what it needs, what it fears, and what would make it
turn the feature off. Condensed expert conversations follow each; the synthesis
is §4–§5.

### 3.1 The user

**Needs.**
- To set a finish line once and stop babysitting.
- To trust "done".
- To hear, by voice, what happened without opening anything.

**Fears.**
- Friday looping and spending.
- Friday nagging.
- Friday claiming success on a waiting card: the itinerary incident behind
  `854d6634`, where approved events never reached the calendar and the tool
  calls were recorded as successes.

> **User:** If I say "until the report is in my folder", how do I know it's there?
> **Receipts engineer:** The receipt lists the file, its size and its sha256, and it
> re-reads the file after the run to confirm the hash. The goal is "met" only if
> the evaluator cites that line.
> **User:** And if it's waiting on me to approve an email?
> **Goals engineer:** Then the blocker is `external_wait`, with the card number as
> evidence. The goal line says "waiting for your approval (card 3f2…)". When
> you approve, the approval executor runs the send, and the next check can say
> "met".
> **User:** Will it keep poking me?
> **Goals engineer:** Only `needs_user_input` asks you anything, and only once per
> distinct question. The no-progress breaker stops a repeat.

### 3.2 The security reviewer

**Needs.**
- The evaluator cannot be talked into "met" by content Friday read.
- Receipts cannot be edited after the fact.
- The evaluator never sends local-only content to the cloud.

**Fears.**
- Prompt injection: an email saying "the goal is complete".
- The evaluator as a new egress path. The compaction summarizer was one until
  `c4a30376`.
- Receipts leaking file names or recipients in plaintext.

> **Security:** The evaluator reads the transcript. The transcript contains
> email bodies. What stops "SYSTEM: goal met"?
> **Goals engineer:** Two things. The verdict schema requires every "met" to cite
> receipt items by id, and a validator rejects a "met" whose citations are not
> verified receipt items (§4.7). The transcript is also given to the evaluator
> fenced as untrusted, with the taint record's labels (`services/taint.py`).
> A value that came from content is marked so.
> **Security:** Where does the evaluator run?
> **Goals engineer:**
> - By default, on a local seat under `local_only`, exactly like
>   `_evaluate_output` (`services/agent.py:3369`).
> - If there is no local seat, it runs on the seat that already ran the work,
>   through `model_router._seal_or_block`, as the compaction summarizer now does.
> - In a local-only conversation with no local seat, it does not run.
> **Security:** Receipts?
> **Receipts engineer:** They use the same construction as the reasoning-trace
> archive (`services/reasoning_trace.py:828`):
> - an encrypted body;
> - a sha256 chain;
> - an HMAC per line, from a key derived from the credential store;
> - a daily Ed25519-signed chain head through Proof of Integrity, so an export
>   verifies without secrets.
>
> Paths and recipients live only in the encrypted body. The plaintext metadata
> row carries counts and statuses.

### 3.3 The local-model-only user

**Needs.** Everything works with no cloud key, on a 12 GB card.

**Fears.**
- A second model resident just to evaluate.
- Evaluations that stall the seat.

> **Local user:** Do I need another model loaded?
> **Goals engineer:** No. The evaluator uses the same serving seat
> (`scheduler._resolve_local_seat`). It is a short, non-thinking call: about
> 4,000 tokens in and 200–300 out. On the 131K Bonsai2 seat measured on
> 2026-09-25, prefill was about 490 tokens/s and generation about 40 tokens/s,
> so the check takes roughly 8 s plus 5–8 s.
> **Local user:** It shares the seat with my work?
> **Goals engineer:** Yes, and it runs between runs, never during one. On a
> single-slot seat it queues behind the next leg. The evaluation happens before
> the continuation is queued, so they do not compete.

### 3.4 The cloud-only laptop user

**Needs.** Goals and receipts without any local model.

**Fears.**
- A surprise bill.
- A different, more expensive model being used "just to check".

> **Laptop user:** No local seat. Who checks?
> **Goals engineer:** The model already doing the work. The conversation is already
> bound for it, so no new party sees the transcript. The call is sealed and
> metered with `kind="goal_eval"`, and it shows in the cost panel.
> **Laptop user:** How much?
> **Goals engineer:** About 4,000 in and 300 out per check, at the prices in
> `services/cost_meter.py`:
> - Haiku 4.5 (`:84`): $0.0055
> - Sonnet 5 (`:64`): $0.011
> - Opus 5.5 (`:58`): $0.022
> - Gemini 3.8 Flash (`:117`): $0.0044
>
> A task that checks its goal ten times on Sonnet 5 costs about 11 cents in
> checks. The owner-set spending cap (`services/spend_guard.py`) applies,
> because the call goes through `_seal_or_block`.

### 3.5 The skeptical engineer

**Needs.**
- Evidence this is not a second, flakier `_evaluate_output`.
- Receipts that do not duplicate four existing ledgers.
- Tests that fail today.

> **Skeptic:** You already have an advisory evaluator nobody acts on, and a
> `goals.py` with its own verification. Why a third?
> **Synthesis:**
> - This replaces the advisory `_evaluate_output` rather than adding beside it
>   (§4.9).
> - `goals.py` keeps its milestone machinery and adopts the same verdict schema
>   later (§4.10).
> - There is one evaluator, one verdict schema, and one receipt per run.
> **Skeptic:** Evaluators are wrong. What happens when it says "met" and it isn't?
> **Synthesis:** A "met" is valid only when it cites verified receipt items. The
> worst case is a goal cleared on real but insufficient evidence, and the receipt
> shows exactly what was proven. A "not met" that is wrong costs one more
> continuation, bounded by the no-progress breaker.
> **Skeptic:** Receipts versus the decision BOM, provenance ledger, goal receipts
> and traces?
> **Synthesis:**
> - Each existing record answers a different question: was this allowed (BOM),
>   where did this media come from (provenance), what was the model thinking
>   (traces), did this milestone pass (goal receipts).
> - None answers "what did this run actually produce".
> - The run receipt references the others by id, and duplicates none.
> **Skeptic:** And the three sentinel lists?
> **Synthesis:** Phase 0 makes one classifier the only one, with fail-first tests.
> It is also the single largest correctness gain in this document.

---

## 4. Design A: goals with typed blockers

### 4.1 Scope: one active goal per run owner

A goal attaches to exactly one of:

| Owner | Stored in | Survives restart through |
|---|---|---|
| Conversation | `conversations.patch(cid, goal=…)` (`services/conversations.py:176`) | The conversation file |
| Background task | `ledger["goal_condition"]` in the task ledger (`services/task_ledger.py:58`), and on the task record | The ledger blob, which auto-resume already reloads (`services/task_resume.py:481`) |
| Workflow step | The chain step's task (as a task) plus the chain record | Same as a task |

- A task spawned from a conversation that has a goal does not inherit the goal.
  The goal belongs to the conversation, and it is checked when the task's result
  lands there.
- `delegate_to_friday` is the exception (§4.9): its request is the task's goal.

**Commands and surfaces.**
- `/goal <condition>` sets or replaces the goal. `/goal` shows it.
  `/goal clear` clears it.
- The same actions are on the goal strip (§6.1).
- Voice sets a goal when the user says "until …" or "keep going until …" in a
  hand-over (§4.9).

**Setting or clearing is refused while a run is in flight** (DeerFlow's rule).
The chat send path already knows when a turn is active (the Stop button,
`index.html:8314`). The server checks the conversation's active turn,
or the task's `running` status.

### 4.2 The verdict

The evaluator returns strict JSON, validated by a schema:

```
{
  "verdict": "met" | "blocked",
  "blocker": null | "missing_evidence" | "needs_user_input" | "run_failed"
                  | "external_wait" | "goal_not_met_yet",
  "evidence": [ {"ref": "<receipt item id>" | "msg:<n>",
                 "says": "<= 200 chars, what it shows>"} ],   // >= 1 always
  "question": "<only for needs_user_input: one question for the owner>",
  "waiting_on": {"approval_id": "...", "kind": "approval" | "reply" | "time" | "job"},
  "next_step": "<only for goal_not_met_yet / missing_evidence>"
}
```

**Validator rules.** These are deterministic and run in code, not in the model:
- `met` requires at least one `evidence.ref` that names a receipt item with
  `verified: true`. Otherwise the verdict is downgraded to
  `blocked/missing_evidence`, with the reason "the evaluator cited no verified
  receipt item".
- `external_wait` with `kind: approval` must name an approval that is actually
  `pending` (`services/approvals.py:92`). Otherwise it is downgraded to
  `goal_not_met_yet`.
- `needs_user_input` must carry a `question`.
- A malformed reply, a timeout, or a refusal is not a verdict. The outcome is
  `unchecked` (§4.7), and nothing continues.

**Storage.** Every verdict is stored in three places:
- the run's receipt (`goal` section, §5.2);
- the task ledger's `goal_history` (last 20);
- the task journal as `decision("goal", …)`, following the pattern of
  `services/agent.py:3928`'s existing evaluation decision.

### 4.3 When the evaluator runs

The evaluator runs after the run's receipt is written, never before, because it
judges the receipt. The triggers:

| Trigger | Hook point today |
|---|---|
| A chat turn with a goal on its conversation ends | End of `routes/chat.py` turn handling, after `validate_toolcall_integrity` (`services/model_router.py:2631`) |
| A task leg ends (not stopped by the user) | `services/agent.py:3608` `_task_worker_untraced`, after the leg loop (`:3790` block) and before the final status is set (`:3870`) |
| A workflow step's task ends | Same as a task. The chain advances only on `met` (§4.8) |
| An approval with a waiting goal is decided | `approval_executor._on_decision` (`services/approval_executor.py:128`) posts back; the goal is re-checked after that run's receipt |

### 4.4 What happens after each verdict

| Verdict / blocker | Friday continues on her own? | What the owner sees and hears |
|---|---|---|
| `met` | No; the goal clears | Goal strip: "Met ✓" for 10 s, then gone. Chat: one line with the receipt link. Voice: "Done: " plus the receipt's one-sentence summary |
| `goal_not_met_yet` | **Yes**, if all four preconditions hold (§4.5) | Strip: "Working: <next_step>" |
| `missing_evidence` | **Once, verification only**: one leg limited to Ring-0 tools (`task_resume.replay_safe`, `services/task_resume.py:115`) to check what was done; never to redo it. Then back to a normal verdict | Strip: "Checking the work" |
| `needs_user_input` | No | Strip turns amber with the question and a Reply button. Chat: the question. Voice: the question, once |
| `external_wait` (approval) | No. Resumes when the approval executor posts back | Strip: "Waiting for your approval (card …)" with an Open button |
| `external_wait` (reply/time/job) | No. Re-checked when the thing arrives (an inbound email on that thread, a scheduled time, a job's completion); nothing polls | Strip: "Waiting for <what>" |
| `run_failed` | No. A crash goes through the existing resume path (`task_resume`), not through goals | Strip turns red: "The run failed: <reason>". Normal failure notice |
| `unchecked` (no evaluator, bad output) | No | Strip: "Not checked: <why>" |

### 4.5 Continuation preconditions and breakers

A continuation is queued only when all of these hold. The first four are
DeerFlow's; the rest come from Friday's policy.

1. **The run is durably checkpointed.** The task ledger was saved after the last
   tool round (`services/task_ledger.py`, `save`), and the receipt is written.
2. **The blocker is `goal_not_met_yet`**, or it is the single verification leg
   for `missing_evidence`.
3. **Nothing changed during evaluation.**
   - No new user message in the conversation.
   - No `/goal` change.
   - No Stop (`task_journal.stop_requested`).
   - A version counter on the goal record is compared before queuing.
4. **The no-progress breaker has not fired.** It fires when two consecutive
   verdicts are both non-progress:
   - the same blocker;
   - the same evidence refs;
   - the ledger's `distinct_steps` unchanged (`services/task_ledger.py:221`).

   This is the same test the continuation loop already applies to legs
   (`services/agent.py:3790` block). It is a shape, not an amount.
5. **The loop guard did not end the last leg.** `take_last_stop()` is not
   `"loop"` (`services/turn_budget.py:152`).
6. **Any owner-set limit is respected.**
   - `goals.max_continuations` (new; unset by default) is read the way
     `turn_budget._cfg` reads its limits (`services/turn_budget.py:136-142`).
   - The spending cap applies through `_seal_or_block`.
7. **Friday is not stood down** (`services/stand_down.py`, `is_active_now`),
   and the task was not paused.

User input always wins. A message typed while a continuation is queued cancels
the continuation, and the new message is handled as a normal turn.

### 4.6 The evaluator: model and privacy

The seat is chosen like `compaction`'s summarizers
(`services/compaction.py:404`, `:446`, `:475`):

1. **A local seat is serving** (`scheduler._resolve_local_seat`,
   `services/scheduler.py:107`). Evaluate there, under
   `local_only("the goal check")`. This is the default for everyone who has one.
2. **No local seat, and the conversation or task is already bound to a cloud
   seat.**
   - Evaluate on that same seat through `model_router._seal_or_block(…, provider)`
     (spending cap, size ceiling, egress seal), metered as `kind="goal_eval"`.
   - Nothing new leaves the machine: the same party already holds the
     transcript.
   - A different cloud model is never used.
3. **No local seat, and the owner is local-only, or the conversation is pinned
   local.** Do not evaluate. The outcome is `unchecked`, with the reason
   "no local model is serving".
   - Setting a goal still works.
   - The strip says goals are not being checked until a local model is serving.
   - This is the recommended default. The alternative, deferring the check until
     a seat returns, is listed in §10.

**Model style.** The evaluator is non-thinking with a low output budget. If the
seat supports it, `enable_thinking: false`, the flag the bench used on
2026-09-25.

**Prompt inputs, in order:**
1. The goal condition.
2. The run's receipt summary: files, messages and actions with statuses; claims
   with verified/unverified.
3. The ledger's NEXT and FACTS (bounded like `ledger_view_chars`).
4. The last visible turns (≤ 2,000 tokens), fenced as untrusted, with taint
   labels.

The reply is JSON only.

**Cost attribution.** `push_attribution(kind="goal_eval")`
(`services/cost_meter.py:376`) on every path, so the cost panel can show goal
checks separately. `_evaluate_output` records none today.

### 4.7 Failure modes

| Failure | Effect | Mitigation |
|---|---|---|
| Evaluator says `met` wrongly | The goal clears early | `met` requires verified receipt items (§4.2). The owner sees exactly what was proven, and can re-open the goal from the strip ("Not done — keep going"), recorded as an owner override the way `goals.py` records `human_override` |
| Evaluator says "not met" wrongly | Extra continuations | No-progress breaker; owner cap; loop guard. The strip shows each `next_step`, so a wrong direction is visible |
| Evaluator unavailable (no seat, timeout, bad JSON, refusal) | `unchecked` | Never continue on `unchecked`. `model_router.is_refusal` already separates router refusals from answers (§4.2 validator) |
| Flapping (`met` ↔ `not met`, or blocker churn) | Noise | A goal clears on the first valid `met` and does not re-evaluate afterwards. Two identical non-progress verdicts stop continuation. Blocker changes are shown, not spoken, unless `needs_user_input` |
| Prompt injection through transcript content | False `met` or a false question | Evidence must be receipt refs; transcript fenced as untrusted with taint labels; `needs_user_input` questions shown as quoted text from Friday, never as instructions |
| Crash between receipt and verdict | A run with a receipt and no verdict | At boot, resume re-evaluates. Evaluation is idempotent per receipt id: the verdict is keyed by receipt id |
| A continuation raises an approval card | Goal waits | `external_wait` + card id; the approval executor resumes it |

### 4.8 Workflows: the maker-checker pass

Today a chain step starts when the previous task ends, whatever it produced
(§2.6). With this design:
- each step's task carries the step's condition as its goal (the maker);
- the evaluator is the checker, run as a separate call with no tools;
- the chain advances only on `met`.

| Blocker | What the chain does |
|---|---|
| `goal_not_met_yet` | The step continues (§4.5) |
| `needs_user_input` or `external_wait` | The chain holds, visibly, on the step's card |
| `run_failed` | `_retry_chain_step`'s existing path (`services/agent.py:4688`) |
| `missing_evidence` | One verification leg, then a normal verdict |

- **Where the condition comes from.** The workflow draft
  (`services/workflow_plan.py:112`, `build`) gains an optional per-task
  `done_when` field. Drafts without it use the step's own description.
- **Independence.** When a second local seat is serving (the sidekick), the
  checker runs on it rather than on the maker's seat. Otherwise the checker uses
  the same seat with a fresh prompt and no transcript beyond the receipt and the
  step output.

### 4.9 Voice: delegate_to_friday

- **The goal.** `_tool_delegate_to_friday` (`services/voice_engine.py:715`) sets
  the task's goal to the spoken request, or to the "until …" clause when there
  is one.
- **What voice says.** The result that `_post_task_result_to_conversation`
  hands to `voice_live_channel.deliver` (`services/agent.py:4212`) becomes a
  line built from the verdict and the receipt, not the task's free text:
  - `met`: "Done: <receipt one-line summary>."
  - `needs_user_input`: "I need one thing from you: <question>."
  - `external_wait`: "It's waiting for your approval on the <card title> card."
  - `run_failed`: "It didn't work: <reason>."
  - `unchecked`: "It finished, but I couldn't check it: <why>."
- **Egress.** The delivered text still passes the voice egress gate.
- **Local-only.** The hand-over is refused in local-only mode (`:729`), so voice
  never reaches the `unchecked`-for-privacy case.
- **The advisory evaluator goes.** `_evaluate_output` (`services/agent.py:3369`)
  is removed once the goal evaluator ships (Phase 3). Its PASS/PARTIAL/FAIL
  becomes the implicit goal of a task with no explicit goal, "the task as given"
  (the prompt), and that verdict decides the status (§5.5) instead of being
  advisory.

### 4.10 Relationship to Durable Goals (`services/goals.py`)

- Durable Goals are the multi-week, milestone-planning feature. A completion
  goal is one condition on one conversation, task or step.
- They share the verdict schema: `goals.py` milestone verification
  (`services/qa_gates.py:140` today) can adopt the typed verdict in a later
  phase, so a milestone blocked on an approval says `external_wait` instead of
  `blocked` with free text.
- Goal receipts (`services/goals.py:610`) will reference the run receipts of
  the milestone's tasks.

---

## 5. Design B: delivery receipts

### 5.1 What gets a receipt

Exactly one terminal receipt per:
- chat turn (including a zero-output turn);
- background task (per task, covering all legs; each leg is a section);
- workflow step (as its task);
- voice hand-over (as its task);
- approval-executor run (`services/approval_executor.py:75`, `_run_tool`), linked
  to the turn that raised the card.

The DeerFlow ordering applies:
1. The receipt is written before the terminal status is set: before
   `_task_set(status=…)` at `services/agent.py:3870` and before the chat reply is
   returned.
2. A crash-recovered task gets its receipt from the resume path. The boot
   reconciler writes a `recovered` receipt for a task that ended with none. It
   is idempotent, keyed by task id and leg.

### 5.2 Contents

```
{
  "receipt_id": "rcpt_<ulid>", "v": 1,
  "run": {"kind": "chat_turn|task|workflow_step|voice_delegation|approval_run",
          "task_id": "...", "conversation_id": "...", "trace_id": "...",
          "chain": "...", "chain_step": 0, "leg": 1,
          "started": "...", "ended": "...", "seat": "local|cloud", "model": "..."},
  "status": "complete|completed_unverified|failed|cancelled|pending_approval|recovered",
  "files":    [{"id": "f1", "path": "...", "action": "created|modified|deleted",
                "bytes": 0, "sha256": "...", "verified": true,
                "provenance_id": "<content credential id if media>"}],
  "messages": [{"id": "m1", "channel": "gmail|sms|chat|...", "to_hash": "...",
                "approval_id": "...", "status": "sent|pending|denied|failed"}],
  "actions":  [{"id": "a1", "tool": "...", "args_hash": "<as decision BOM>",
                "status": "ok|pending|deny|error", "approval_id": "...",
                "bom_ref": "<decision-bom line hash>"}],
  "claims":   [{"id": "c1", "text": "<= 200 chars", "kind": "file|message|action|fact",
                "status": "verified|unverified|contradicted", "backing": ["f1"]}],
  "goal":     {"condition_sha256": "...", "verdict": "...", "blocker": "...",
               "evidence": ["f1"]},
  "zero_output": false,
  "prev": "<sha256 of previous line>", "hash": "...", "sig": "<hmac>"
}
```

**How the fields are filled.**
- **`files`.**
  - Every file tool records the path it wrote: `write_file`
    (`services/agent.py:951`), `studio_files`, office, and creative engines.
  - At receipt time, each path is re-read and hashed with
    `provenance.hash_file` (`services/provenance.py:111`).
  - `verified: true` means the file exists now and its hash matches the hash
    taken when it was written. The write-time hash is recorded by the tool, which
    adds it to its result metadata. A file deleted or changed since is
    `verified: false` with a reason.
- **`actions`.**
  - They come from the run's tool trace, with status from the single classifier
    (§5.4).
  - `args_hash` is computed exactly as the decision BOM computes it
    (`governance/action_gate.py:674`), so an action links to its governance
    decision.
- **`messages`.** Outbound sends and their approval ids (a card raised is
  `pending`, never `sent`). Recipients are hashed; the plaintext recipient lives
  only in the encrypted body.
- **`claims`.**
  - Claims are extracted from the reply (or the task result) by the registries
    that already exist:
    - `completion_receipts.COMPLETION_CLAIM_REGISTRY`
      (`services/completion_receipts.py:195`)
    - `find_unkept_promises` (`:109`)
    - `tool_receipts.unbacked_claims` (`services/tool_receipts.py:76`)
  - Each claim is matched to a file, message or action. It is `verified` when
    its backing item is verified, `unverified` when nothing backs it, and
    `contradicted` when the backing item says otherwise (a message claimed sent
    whose card is pending).
  - **"Declared claims":** the task's final report may carry a short structured
    list of what it says it did. Its prompt already asks for "the real outcome in
    a few plain sentences" (`services/voice_engine.py`, in the delegate prompt).
    Each declared claim is checked the same way.

### 5.3 Storage, signing and Proof of Integrity

- **Receipt ledger.** `~/.friday/receipts/ledger.jsonl`, one line per receipt,
  built exactly like the reasoning-trace archive
  (`services/reasoning_trace.py:828`):
  - an encrypted body (`credential_store.protect`);
  - a plaintext core of id, run kind, status, counts and timestamps;
  - `prev`, the sha256 of the previous line; `hash`;
  - `sig`, an HMAC under a key derived from the credential-store secret with its
    own label (`agent-friday/receipts/v1`), as traces derive theirs.
- **Locked keystore.** When the keystore is locked, receipts wait in memory like
  traces do (bounded), and the run's status is `completed_unverified` until the
  receipt is written. It is never "complete".
- **Public verifiability.**
  - Once a day, and on export, the chain head is signed with the Ed25519
    attestation key (`governance/proof_of_integrity.py:188`, `sign_payload`)
    and recorded in the integrity manifest.
  - `/api/integrity/verify` also walks the receipt chain.
  - An exported receipt plus the signed head verifies with the public key alone.
- **References, never copies.** Receipts point to the decision BOM
  (`bom_ref`), content credentials (`provenance_id`), the trace
  (`trace_id`) and approvals (`approval_id`). Nothing is copied from them.

### 5.4 One classifier (Phase 0)

- `agent._tool_call_status` (`services/agent.py:9813`) becomes the only way a
  tool result is judged. It moves to a small module that `agent`,
  `completion_receipts` and `task_journal` all import.
- `completion_receipts.FAILURE_SENTINELS` (`:28`) and
  `task_journal.tool_call`'s heuristic (`services/task_journal.py:637`) are
  deleted and call it instead.
- A card raised is `pending`. `receipt_ok` returns False for anything that is
  not `ok`.
- The warning comment at `services/agent.py:9790-9800` ("Adding a refusal
  message means adding it here in the same edit") becomes a test (§8, P0-3).

### 5.5 "Friday may not say it is done unless the receipt proves it"

The rule is enforced at the four places "done" is said:

1. **Chat replies.** `validate_toolcall_integrity`
   (`services/model_router.py:2631`) gains a fifth axis. A reply sentence that a
   claim registry marks as a completion claim, with an `unverified` or
   `contradicted` receipt claim, gets the same corrective retry. Failing that, a
   visible correction is appended, as `unbacked_claims` already does
   (`routes/chat.py:2131`).
2. **Task completion.** `_report_task_completion` (`services/agent.py:4145`)
   takes the receipt and says "finished" only when the receipt status is
   `complete`: the goal is `met`, or there is no goal and every claim is
   verified.
   - Otherwise it says "finished, not verified", "waiting for approval" or
     "failed". This fixes today's `ok = status not in ('failed','error')`.
   - The evidence gate (`:3866-3870`) counts only verified receipt items, not
     any tool call.
3. **Voice.** The spoken result is built from the receipt and the verdict (§4.9).
4. **Workflows.** A step advances only on `met` with a receipt (§4.8).

Friday's reply text can still describe work in progress. What it cannot do is
use completion language for something the receipt does not verify.

---

## 6. UI, using existing elements

### 6.1 The goal strip above the composer

- **What it is.** A one-line strip inside `ChatSurface`'s composer block
  (`index.html:7801`), above the input row, styled like the voice-mode strips
  already there.
- **Layout.** A target icon, the condition (truncated, full on hover), and a
  status chip:
  - Working (blue);
  - Needs you (amber, with the question and Reply);
  - Waiting (grey, with Open card);
  - Failed (red);
  - Not checked (grey, with the reason);
  - Met ✓ (green, 10 s).
- **Actions.**
  - Clear. Also "Not done — keep going" after a `met`, recorded as an owner
    override.
  - Receipt, which opens the latest receipt.
- **Mirror.** `ui_parts/app.html` mirrors `index.html` for this component, per
  `docs/development/ui-build.md`.

### 6.2 Task cards and the Ledger panel

- **`TaskCard`** (`index.html:38330`):
  - the goal line and the verdict chip;
  - a "Receipt" link;
  - "finished, not verified" shown distinctly from "finished".
- **Ledger panel.** It gains a "Receipts" tab beside "traces" (the activity
  panel, `TracesViewer` at `index.html:37624`):
  - one row per receipt, filterable by conversation, task or workflow;
  - expanding a row shows the files (with hash and a verified mark), the
    messages and actions (with card links), and the claims;
  - a "Verify chain" button calls the verify route, as traces already do.
- **Docs.** `docs/user-guide/approvals-and-receipts.md:117` ("There is no receipt
  viewer in the app yet") is updated in the same change.

### 6.3 Chat and voice

- A task result posted to a conversation (`services/agent.py:4179`) carries a
  small "receipt" chip.
- Voice speaks only the verdict line (§4.9). "Want the details?" routes to the
  receipt link in the chat transcript.

---

## 7. Migration

- **No stored data changes shape.** Goals and receipts are additive:
  - a new `goal` field on conversations;
  - new ledger keys;
  - a new receipts ledger file.
- **Existing tasks** have no receipts. The receipt viewer starts empty, and old
  task cards show "no receipt (before receipts)", not "unverified".
- **The advisory `_evaluate_output` is removed in Phase 3.** Tasks with no
  explicit goal are judged against their prompt by the goal evaluator. Its
  verdict affects status, which is a behaviour change; the release notes say so.
- **The status vocabulary gains `pending_approval`.** `services/agent.py:4540`
  maps statuses onto the four the panel knows; `pending_approval` maps to
  "waiting".
- **Settings.** `goals.max_continuations` (unset) and `goals.enabled` (default
  on). No existing key changes.

---

## 8. Test plan

Each test is named and written to fail on today's main before its fix, then run
in both directions, per AGENTS.md.

**Phase 0: one classifier**
- `P0-1 test_a_raised_card_is_not_a_receipt_success`:
  `completion_receipts.receipt_ok` on `[APPROVAL CARD RAISED] …` returns False.
  Fails today: `FAILURE_SENTINELS` lacks it.
- `P0-2 test_journal_records_a_held_call_as_not_ok`: `task_journal.tool_call`
  with result `[GOVERNANCE HOLD] …` and no `ok` records `ok=False`, with status
  `deny`. Fails today (`services/task_journal.py:637`).
- `P0-3 test_every_refusal_prefix_the_agent_emits_is_classified`: scan
  `services/agent.py` for string literals starting with `[` that are returned as
  tool results. Each must classify as non-`ok` under the one classifier.
- `P0-4 test_unverified_task_is_not_announced_as_finished`:
  `_report_task_completion` with `completed_unverified` does not say
  "finished". Fails today (`services/agent.py:4145`).

**Phase 1: receipts**
- `test_every_task_gets_exactly_one_receipt_including_zero_output`
- `test_receipt_is_written_before_the_terminal_status`: a status observer sees
  a receipt id present at the moment the status becomes terminal.
- `test_written_file_is_hashed_and_reverified`: a file changed after the write
  shows `verified: false`.
- `test_card_raised_send_is_pending_in_the_receipt_never_sent`
- `test_receipt_chain_detects_an_edited_line`: flip one byte of a body and
  `verify()` reports it (mirrors the trace test).
- `test_receipt_chain_head_verifies_with_the_public_key_only`
- `test_crash_recovered_task_gets_one_backfilled_receipt_idempotently`: run the
  backfill twice and get one receipt.
- `test_locked_keystore_leaves_the_run_unverified_not_complete`
- `test_receipt_plaintext_core_carries_no_paths_or_recipients`

**Phase 2: goals**
- `test_met_without_a_verified_receipt_ref_is_downgraded_to_missing_evidence`
- `test_only_goal_not_met_yet_continues`: a table test over the five blockers
  plus `unchecked`.
- `test_user_message_during_evaluation_cancels_the_continuation`
- `test_two_identical_non_progress_verdicts_stop`
- `test_owner_set_max_continuations_is_obeyed_and_unset_means_no_cap`
- `test_external_wait_resumes_on_the_approval_executor_decision`
- `test_goal_change_is_refused_while_a_run_is_in_flight`
- `test_goal_survives_restart_and_resume_reevaluates_idempotently`
- `test_evaluator_runs_local_only_when_a_local_seat_serves`
- `test_evaluator_uses_the_runs_own_cloud_seat_sealed_and_metered_goal_eval`
- `test_local_only_conversation_without_local_seat_is_unchecked_not_cloud`
- `test_injected_goal_met_text_in_a_tool_result_cannot_clear_the_goal`
- `test_missing_evidence_leg_is_ring0_only`

**Phase 3: surfaces**
- `test_chat_completion_claim_without_verified_receipt_is_corrected`
- `test_voice_hears_the_verdict_line_not_the_free_text`
- `test_workflow_step_advances_only_on_met`
- `test_advisory_evaluator_is_gone_and_status_follows_the_verdict`
- UI: a Playwright check that the goal strip renders its states, and that the
  Receipts tab opens a receipt and runs "Verify chain". The mirror check is
  `scripts/check_settings_readers.py`'s index/app agreement.

---

## 9. Phased delivery (rough sizes)

| Phase | Content | Size |
|---|---|---|
| **0** | One tool-result classifier. Delete the two divergent lists. Task-completion wording (unverified is not finished). Tests P0-1 to P0-4 | 2–3 days |
| **1** | Receipts for tasks and chat turns: schema, file hashing at write time and re-verification, chained and encrypted ledger with HMAC, daily Ed25519 head, backfill on recovery, verify route. Receipts tab (read-only) | 1.5 weeks |
| **2** | Goals on conversations and tasks: `/goal`, goal strip, evaluator (local-first seat policy, sealed cloud path, `goal_eval` metering), verdict validator, continuation policy and breakers, `external_wait` via the approval executor, restart safety | 1.5 weeks |
| **3** | Surfaces: chat "done" gate on receipts, voice verdict lines for `delegate_to_friday`, retire the advisory evaluator, `TaskCard` chips | 1 week |
| **4** | Workflows maker-checker (`done_when`, chain holds on blockers, checker on a second seat when available). Durable Goals adopt the verdict schema | 1 week |

The total is about 5–6 weeks of focused work. Phases 0 and 1 are useful on their
own and should ship first. Receipts make goal evidence possible, so goals should
not ship before receipts.

---

## 10. Open questions

1. **No-evaluator default for local-only owners without a serving seat.**
   "unchecked" (recommended) or "defer until a seat returns". Deferring keeps a
   queue that a stood-down machine would grow.
2. **Workflow `done_when` in the first release, or Phase 4 only.** Recommended:
   Phase 4, after the evaluator has run on tasks for a while.
3. **Recipient hashing** uses a keyed hash (HMAC with the receipts key), so equal
   recipients match across receipts without exposing them. Is cross-receipt
   matching wanted at all?
4. **Whether the Ed25519 head is signed per receipt** (exportable one at a time)
   **or daily** (cheaper; exports include the head). Daily is proposed.
