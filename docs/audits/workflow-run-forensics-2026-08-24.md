# Forensics — the Voice-Triage workflow run, 2026-08-24 11:03–11:30

Written from the ledger (`~/.friday/activity_ledger.jsonl`), `friday.log`,
`chat_history.json`, `~/.friday/workflows/*.json`, `~/.friday/settings.json`
and the residency runtime files — then traced back into the tree. Every claim
below is either a quoted record or a file:line. Where a record does not exist,
this says so rather than inferring.

A prior session diagnosed this transcript **without reading the ledger**. Two of
its conclusions are contradicted here and are marked as such.

---

## 0. What the records actually show

### 0.1 The timeline (ledger, epoch → local)

| Time | Record | Notes |
|---|---|---|
| 11:16:37 | `model_invocation` gemma4:12b, 3.3 s | Friday says "I'm launching `create_workflow` right now". **No tool call in the ledger.** Narration only. |
| 11:17:22 | `tool_call create_workflow ok=true` (3 ms) | No `orb_id`, no `task_id`. |
| 11:17:28 | `model_invocation` gemma4:12b, 28.5 s | Friday says "The workflow is officially in the system. I've just fired it." **No `run_workflow` had been called.** The claim was false at the moment it was made. |
| 11:18:29 | `tool_call create_workflow ok=true` (3 ms) | Same slug — overwrites. `voice-triage-evolution.json` carries `"updated": "2026-08-24T11:18:29.851563"`. |
| 11:18:31 | `subagent_spawn` `934e19da` "Chain 'Voice-Triage-Evolution' · step 1/3" + `run_workflow ok=true` | Pipeline **A** starts. |
| 11:18:43 | `friday.log` — `local seat gemma4:12b failed, falling back to cloud: 500 … {"error":{"code":500,"message":"Context size has been exceeded."}}` from `http://127.0.0.1:8090/v1/chat/completions` | **The chat turn escalates to Anthropic here.** |
| 11:18:56 | `navigate ok=true` (orb `agent-50fa1cdf`, task `934e19da`) | |
| 11:19:06 | `screenshot ok=false` (2 ms) | Ring-3 denial. 2 ms is a gate refusal, not a capture. |
| 11:19:11 | `create_workflow ok=true`, `orb_id agent-bfe78690` | A **third** create, from the cloud turn — a different name, therefore a different slug: `voice-triage-ui-evolution.json`. |
| 11:19:19 | `subagent_spawn` `29289cf7` "Chain 'Voice-Triage UI Evolution' · step 1/3" + `run_workflow` | Pipeline **B** starts. Pipeline A is still running. |
| 11:19:36 | `model_invocation` **claude-sonnet-5 / anthropic / cloud**, 53.4 s, 332,437 tok in | The turn that answered "You failed to fire the workflow." |
| 11:19:37, 11:19:39 | `screenshot ok=false` ×2 (task `29289cf7`) | |
| 11:19:43 | `model_invocation` gemma4:12b, 23.4 s, **125,281 tok in** (task `29289cf7`) | Step 1 of B "completes". |
| 11:19:45 | `subagent_spawn` `ee9cf234` "…step 2/3" | B advances **2 seconds** after its step-1 model returned, with zero successful screenshots. |
| 11:20:41 | `subagent_spawn` `cbf88d38` "…step 3/3" | B finishes all three steps by 11:21:31. |
| 11:25:14–11:26:40 | 7× `run_command ok=true` (task `934e19da`) | Pipeline A's step 1 doing real work. |
| 11:27:21 | `model_invocation` **claude-sonnet-5 / anthropic / cloud**, 518.8 s, **1,435,556 tok in** (task `934e19da`) | |
| 11:27:24 | `subagent_spawn` `d12a820b` "Chain 'Voice-Triage-Evolution' · step 2/3" | |
| 11:28:19 | `subagent_spawn` `648c69b9` "…step 3/3" | |
| 11:29:04 | `model_invocation` gemma4:12b, 44.6 s (task `648c69b9`) | Last record of either pipeline. |
| 11:46:35 / 11:48:19 / 11:49:48 | Server restarts ×3 | `TASKS` is an in-memory dict (`services/agent.py:1995`). **Every task record from this run was destroyed here.** |

### 0.2 Six task IDs, six real tasks

The six "✅ Task complete" cards correspond to six real `subagent_spawn` records
with real `task_id`s and real tool receipts. They were **not** hallucinated
cards. Mapping:

* **Pipeline B** (`voice-triage-ui-evolution`): `29289cf7` (Visual Audit) →
  `ee9cf234` (Architecture Spec) → `cbf88d38` (Build Against Spec)
* **Pipeline A** (`voice-triage-evolution`): `934e19da` (Visual Audit &
  Critique) → `d12a820b` (Opus Architecture Blueprint) → `648c69b9` (Sonnet
  Construction)

So: **two overlapping three-step pipelines**, not one pipeline that ran twice.
Both were created by the model in the same conversation, four minutes apart,
under two different names. Both files are still on disk.

### 0.3 What the cards said is NOT recoverable

`chat_history.json` for this window contains **no** "✅ Task complete" cards and
**no** "Gemma 4 26b" string. That is expected, not missing data: those cards are
rendered client-side by the polling loop at `index.html:39273` (mirror at
`ui_parts/app.html:10507`) and pushed into React state only — they are never
persisted server-side. The card *bodies* came from `t.result`, which lived in
the in-memory `TASKS` dict that the 11:46 restart wiped.

**Not determinable from the ledger:** the exact text of the six cards, whether
each step produced an artifact on disk, and the per-step `verified` flag. What
*is* determinable is the tool trace behind each task_id, which is what §6 uses.

### 0.4 Two prior conclusions the ledger contradicts

1. **"Sonnet 5 was called deliberately."** It was not. Both cloud invocations
   are downstream of failures — the chat turn escalated on a local 500 — and
   the seat label `claude-sonnet-5` on the six spawn records is a settings
   lookup, not the model that ran (§5.2).
2. **"`create_workflow` may never have fired."** It fired three times
   (11:17:22, 11:18:29, 11:19:11). What did not fire on the first two turns was
   `run_workflow` — Friday narrated "I've just fired it" at 11:17:28 with only a
   `create` behind it.

---

## 1. The phantom model — `gemma4:26b`

**It is real config, and the file is on disk.** This is not model-generated
text and not a seed value.

`~/.friday/settings.json`:

```json
"heavy_hitter": {"provider": "llama-cpp-local", "model": "gemma4:26b"}
```

`~/.friday/runtime/residency/gguf_models.json` and
`~/.friday/runtime/models/models.json` both list it, and the weight file exists:

```
-rw-r--r-- 16947541728  Aug 14 14:29  gemma-4-26B-A4B-it-UD-Q4_K_M.gguf
```

**16.95 GB on a 12,282 MiB card.** It has also actually run: the ledger holds
two `model_invocation` records for `gemma4:26b` on 2026-08-15 at 57.9 s and
59.5 s for 92 output tokens — roughly 1.6 tok/s, i.e. mostly on the CPU.

### How it got into the heavy_hitter seat

Not by Stephen choosing it. `friday.log` records the write twice:

```
2026-08-23T15:45:53 friday.seat_binding — seat binding applied:
  heavy_hitter→gemma4:26b, local→gemma4:12b, orchestrator→gemma4:12b,
  reasoning→gemma4:12b, sidekick_fast→gemma4:12b, subagent→gemma4:e4b
2026-08-24T11:46:40 friday.seat_binding — seat binding applied:
  heavy_hitter→gemma4:26b, memory_manager→gemma4:12b, subagent→gemma4:e4b
```

The chain:

* `services/residency_policy.py:661-665` — `_generation_candidates()` sorts
  every installed generation model by parameter count, descending.
* `services/residency_policy.py:723` — `heavy = gen[0] if gen else None`. The
  heavy seat is **always the largest model on disk**. There is no user input to
  this decision.
* `services/residency_policy.py:1011` — the dense-model fit rule
  (`if not heavy.get("is_moe") and budgets: refuse`) **exempts MoE models**.
  `gemma4:26b` is 26B-A4B, i.e. MoE, so it is permitted to expert-offload to
  the CPU and is never refused on size.
* `services/seat_binding.py:251` — `apply()` writes the plan's choice straight
  into `capability_routing[cap]`, unconditionally.

So: the largest GGUF on disk wins the heavy seat by construction, and MoE is
the loophole that lets a 17 GB file win it on a 12 GB card.

**Correction to the premise, stated plainly:** the model *is* installed — as a
llama.cpp GGUF in Friday's own runtime, not as an `ollama pull`. It is not
*served*: `~/.friday/runtime/residency/endpoints.json` lists exactly one live
endpoint, `gemma4:12b` on `:8090`. Installed ≠ served, and every layer below
treats "installed" as sufficient (§3).

---

## 2. The ignored setting — why Opus 5 did not take the heavy seat

### 2.1 Where Intelligence settings live

Plain JSON at `~/.friday/settings.json`. No sqlite, no electron-store. Canonical
key is `capability_routing`; several capabilities also carry a **flat legacy
mirror** (`orchestrator_model`, `subagent_model`, `creative_model`) and
`core/__init__.py:_sync_capability_routing` reconciles the two on every save —
with the flat key winning when it was explicitly set.

Read at orchestration time by, among ~18 sites:
`services/local_seats.py:_configured` (`:141-170`) for the heavy/brain/sidekick
roles, and `routing/model_router.py:_chosen_seat` (`:271-329`) for the
conversational seat.

### 2.2 The defect

`services/seat_binding.py:98-134`, `overrides_from_settings()`:

```python
if _is_cloud_binding(entry, model):
    continue          # :130
out[seat] = model
```

A cloud pick is **filtered out of the overrides** before the planner ever sees
it. That filter is correct on its own terms — a local VRAM planner has no
business refusing `claude-opus-5` for "not being installed", and the docstring
says so.

What is missing is the other half. `cloud_seats_from_settings()` (`:136-154`)
exists precisely to mark those seats as *filled elsewhere* — and
`propose()`/`apply()` (`:194-260`) **never consult it**. `propose()` walks every
role in `SEAT_TO_CAPABILITY`, and if the plan seated *anything* there, it emits
a change. So:

1. Stephen picks Opus 5 for a role.
2. `overrides_from_settings` drops it (it's cloud).
3. The planner, seeing no override, seats its own pick — `gen[0]`, the 26b.
4. `apply()` at `:251` overwrites `capability_routing[heavy_hitter]` with
   `{llama-cpp-local, gemma4:26b}`.
5. Next boot: repeat.

**A cloud pick for any locally-plannable seat is guaranteed to be overwritten at
the next boot.** That is the whole bug. `friday.log` shows it happening again at
11:46:40 on the day in question, and the same line shows `subagent→gemma4:e4b`
— which is why the subagent seat that was `claude-sonnet-5` during the incident
reads `gemma4:e4b` today.

### 2.3 A second, aggravating detail

`services/seat_binding.py:68-72`:

```python
CAPABILITY_TO_FLAT = {
    "reasoning": "orchestrator_model",
    "subagent": "subagent_model",
    "creative_image": "creative_model",
}
```

`heavy_hitter` has **no flat mirror**, and `services/local_seats.py:240` records
this explicitly as `"heavy_hitter": None`. So the heavy seat has no second
surface to disagree with — but it also means the seat-change watcher
(`services/seat_transparency.py:37-42` watches only `orchestrator_model`,
`subagent_model`, `model_routing.mode`, `model_routing.local_model`) **cannot
see the heavy seat change at all**. The 26b was seated on 2026-08-23 and no
system line appeared in chat.

### 2.4 The label ambiguity

`routes/intelligence.py:55` labels `heavy_hitter` **"Heavy thinking"** and
`:53` labels `reasoning` **"Everyday conversation"**. Current settings hold
`reasoning: claude-opus-5` and `heavy_hitter: gemma4:26b`.

**Not determinable from the ledger:** which of the two rows Stephen actually
clicked. Both readings end in the same place — if he set `heavy_hitter`, §2.2
overwrote it; if he set `reasoning`, that value survived (it is cloud), and its
local counterpart `interactive_brain` was overwritten on 08-23 to `gemma4:12b`,
which is exactly what answered every local turn in the transcript.

---

## 3. The VRAM blind spot

### 3.1 Does the router know what is installed?

Yes, and that is part of the problem. `services/local_seats.py:118-121`:

```python
# FRIDAY'S OWN STORE COUNTS AS INSTALLED.
rows = list(_friday_store())
```

`_friday_store()` reads `~/.friday/runtime/models/models.json` and admits any
entry whose `path` exists on disk. `gemma4:26b` qualifies. The Ollama daemon is
then queried only for *additional* names. So `installed()` returns the 26b, and
`resolve("heavy")` — which prefers the **largest** model
(`_WANTS_LARGE = {"heavy"}`, `:46`) — returns it too.

Nothing in that path asks whether the model is *served*. `endpoints.json` is not
consulted at `local_seats` level.

### 3.2 Does it know the VRAM ceiling?

There is a VRAM check, and the 26b slips through it.
`routing/model_router.py`:

```python
v = rc.vram_at(n, fp, 16384)
if v is None:
    continue                  # unmeasured: neither in nor out   :852
...
return fits is None or name not in needs or name in fits          :887
```

`~/.friday/runtime/residency/measurements.json` for this machine's fingerprint
(`NVIDIA GeForce RTX 4070|12282|32620`) holds measurements for `gemma4:12b`,
`gemma4:e2b`, a HauhauCS E4B and `qwen3.5:9b`. **`gemma4:26b` has no
measurement.** `_ok()` therefore returns `True` for it unconditionally — an
unmeasured model is never refused.

The comment at `:848-851` defends this ("Refusing to route on missing data would
be worse"), and for the general case that is defensible. For a 17 GB artifact on
a 12 GB card it produces exactly the wrong answer, and there is no
artifact-size backstop.

### 3.3 The failure mode when it routes to an unserved model

**It silently falls back to the cloud.** Not an error, not a hang.

`services/model_router.py:472-505` — `_call_ollama` asks
`residency_arbiter.owned_provider(model)` for a live port. `gemma4:26b` is not
in `endpoints.json`, so that returns `None`; the descriptor lookup also fails;
control reaches `:507`:

```python
if not ollama.is_available():
    raise RuntimeError("Ollama is not running at " + ollama.base_url)
```

That `RuntimeError` is caught by the attempt ladder in
`services/agent.py:255-273`, and the next rung is Anthropic.

This is on the record verbatim, `friday.log` 2026-08-21:

```
2026-08-21T09:27:26 WARNING friday.local_fallback —
  local seat gemma4:26b failed, falling back to cloud:
  Ollama is not running at http://localhost:11434
```

A second, quieter variant ran ~15 times during the incident window itself. The
judgment gate's seat (`gemma4:e2b`, extracted to llama.cpp, absent from the
daemon) 404s on every probe:

```
2026-08-24T11:19:11 WARNING friday.local_call — local_call HTTP 404 from
  gemma4:e2b: {"error":"model 'gemma4:e2b' not found"}
2026-08-24T11:19:11 WARNING friday.egress — BLOCK provider=anthropic
  field=message[3].content tier=TIER_2 (judgment unavailable
  (malformed verdict JSON after 0.0s) — deterministic outcome kept)
```

So an uninstalled-at-the-daemon model degrades the **privacy gate** into its
deterministic fallback, and the failure is reported as "judgment unavailable",
not as "the model is missing". `services/local_seats.py:9-20` documents this
exact class of defect; it is still live for `e2b`.

---

## 4. The cloud escalation — deliberate policy or unhandled failure?

**Unhandled failure.** Root cause on the record, `friday.log`:

```
2026-08-24T11:18:43 WARNING friday.local_fallback —
  local seat gemma4:12b failed, falling back to cloud:
  500 Internal Server Error from http://127.0.0.1:8090/v1/chat/completions:
  {"error":{"code":500,"message":"Context size has been exceeded.","type":"server_error"}}
```

The local brain's context window (32,768) was exceeded by the accumulated tool
loop. `routes/chat.py:885-908` catches it, logs the warning, sets
`_provider = 'cloud'`, rebuilds and re-gates the prompt, and re-sends. The reply
Stephen saw at 11:19:36 is stamped `model=claude-sonnet-5, seat=cloud` in
`chat_history.json` and matches a `model_invocation` of 53.4 s / 332,437 tokens
in the ledger.

Two things worth separating:

* **The chat escalation is announced.** `routes/chat.py:1334` returns
  `local_fallback: {model, why}` to the client, and the code comment at `:886`
  is emphatic that a silent fallback is unacceptable. Whether the UI renders
  that payload is a separate question — **not determinable from the ledger**.
* **The generic path is not.** `services/agent.py:230-243` builds the attempt
  ladder for *background tasks*:

  ```python
  if provider == 'local':
      attempts = [('local', _via_ollama, routed_model)]
      if not vault_access:
          attempts += [('cloud', _via_claude, None),        # :234
                       ('openai', _via_openai, None)]
  ```

  Any exception from the local leg walks to Anthropic. There is no warning
  payload on this path — only `attribution.note_fallback()` into the badge.
  This is what carried task `934e19da` to a 518-second, 1.43M-token
  claude-sonnet-5 call at 11:27:21.

There **is** a deliberate refusal mode: `routes/chat.py:856-885` — if
`model_routing.mode == "local_only"`, the cloud is refused and the user is told
why. Stephen's mode is `local_preferred`, so the fallback is enabled by
configuration. That is a real choice he made; it is the *silence and the cost*
of the fallback that are not.

---

## 5. The seat label — where "Gemma 4 26b" came from

### 5.1 The model is handed the seat table, and told to name a seat

`services/model_router.py:1796-1800`, inside `_get_friday_system_prompt`:

```python
from agent_friday.services.self_account import describe as _self_account
_acct = _self_account()
if _acct:
    prefix += "\n== WHAT YOU ACTUALLY ARE ==\n" + _acct + "\n"
```

`services/self_account.py:_seats()` (`:30-55`) reads the **residency plan** and
emits one line per seat. With the plan as bound on 08-23, that block contains a
literal line reading approximately:

```
  - heavy_hitter: gemma4:26b, gpu:0+cpu, 24576 ctx, leased
```

And then, `services/self_account.py:184-187`:

```python
parts.append("Name the seat and the actual model when you say which "
             "one is answering. 'Gemma 4' is a brand, not an answer — "
             "the 12b and the 26b are different models with different "
             "speeds, and Stephen can tell.")
```

`services/agent.py:2186` — `_task_worker` calls
`_get_friday_system_prompt(prompt, workspace='task')`, so background tasks get
this block too.

**So the system prompt (a) lists `heavy_hitter → gemma4:26b`, (b) instructs the
model to name the seat that is answering, and (c) never says which seat that
is.** The model was told to state a fact it was not given. It picked the
plausible one off the table. That is not a hallucination in the usual sense; it
is a directive with no ground truth behind it.

### 5.2 There is no resolved-seat variable — and the one that looks like it is wrong

Nothing in the tree injects the *actual* routed model into the prompt. The
closest thing is `services/agent.py:218`, which appends `"\n[SEAT] " + _fit_note`
— but that is a tool-budget trimming notice, not an identity.

Worse, the ledger's own `subagent_spawn.model` field is misleading.
`services/agent.py:2502-2505`:

```python
_al.record("subagent_spawn", task_id=task_id,
           description=(description or name or "")[:200],
           model=_load_settings().get("subagent_model") or ANTHROPIC_MODEL_DEFAULT)
```

It records a **settings lookup**, ignoring the `model=` argument `_spawn_task`
was actually handed (`:2439`, `:2508`). During the incident `subagent_model` was
`claude-sonnet-5`, so all six chain spawns are stamped `claude-sonnet-5` —
while their `model_invocation` records show `gemma4:12b / arbiter-local` for
four of the six. **The ledger's spawn seat label is not the seat that ran.**

The route *is* known and *is* logged, just not to the model:
`services/agent.py:2201-2222` (`_log_route`) writes `Asking %s (%s)…` into the
task log with the real decision. That value is available and unused as prompt
context.

---

## 6. The vacuous checkmark

### 6.1 The card fires on any terminal status, including failure

`index.html:39265`:

```js
const newlyDone = list.filter(t => !t.process && !(t.description||'').startsWith('scheduled:')
  && (t.status === 'complete' || t.status === 'completed_unverified' || t.status === 'failed')
  && !lastNotifiedComplete.has(t.task_id) && prev.some(p => p.task_id === t.task_id && p.status === 'running'));
```

…and `:39273` renders, for all three:

```js
text: `✅ Task complete: **${t.name}**${t.result ? '\n\n' + t.result.substring(0,400) : ''}`
```

A task with `status === 'failed'` renders a green tick reading "Task complete".
The server distinguishes three outcomes; the client collapses them to one.
Mirror bug at `ui_parts/app.html:10507`.

### 6.2 "Verified" counts denied tool calls as evidence

`services/agent.py:2294-2297`:

```python
evidence = [t for t in tool_trace if t.get('name') not in ('spawn_task',)]
verified = len(evidence) > 0
verification_summary = ...
final_status = 'complete' if verified else 'completed_unverified'
```

A denied call is still appended to `tool_trace`. `_execute_tool`
(`services/agent.py:5074-5077`) returns the deny *reason as the result string*:

```python
verdict = _hooks.run_pre_hooks(ctx)
if verdict.action == "deny":
    _receipts.record(name, ok=False, denied=True, detail=verdict.reason)
    return verdict.reason
```

and the caller appends it unconditionally at `:6241` / `:6516`. So the 11:19:06
and 11:19:37 `screenshot ok=false` denials **counted as evidence** and promoted
their tasks to `status='complete'`.

The denial itself is correct and working as designed: `screenshot` is Ring 3
(`services/agent.py:3722`), and `_cc_check` (`:3455-3462`) returns
`"Computer control permission not granted."` because
`services/agent.py:3443-3452` deletes the persisted grant on every launch.
`friday.log` confirms Computer Control only came up at 11:46:35, after the
incident. **The gate held; the accounting did not.**

### 6.3 The card body is the model's prose, ungraded

`services/agent.py:2115-2130` — `_summarize_task_outcome` returns `reply`
verbatim whenever the model produced any prose. The tool trace is consulted
*only* when the reply is empty. So a step that got a Ring-3 refusal and then
wrote a paragraph claiming it captured screenshots delivers that paragraph, in
full, under a green tick.

### 6.4 Step N+1 starts regardless of whether step N produced anything

`services/agent.py:2863-2890` — `_advance_task_chain`:

```python
if t.get('chain'):
    low = result_text.lower()
    if any(sig in low for sig in _CHAIN_FAILURE_SIGNATURES):
        return _retry_chain_step(task_id, result_text[:500])   # :2868
...
chain_slug = t.get('chain')
if chain_slug:
    ...
    return _spawn_task(...)                                    # :2884
```

The **only** gate is a substring match against provider-error signatures in the
result text. It does not read `verified`, does not read `final_status`, and does
not look at the tool trace. A `completed_unverified` step advances. A step whose
only tool call was denied advances.

The ledger shows this at 2-second resolution: task `29289cf7`'s model returned
at 11:19:43 and `ee9cf234` (step 2/3) spawned at **11:19:45**.

### 6.5 On the two Visual Audits

Pipeline B's audit (`29289cf7`, 11:19:36–11:19:43) had three tool calls:
`navigate ok=true`, `screenshot ok=false`, `screenshot ok=false`, then one
23-second gemma4:12b turn with **125,281 tokens in** — against a 32,768-token
seat. Pipeline A's audit (`934e19da`) got a Ring-3 denial at 11:19:06, then went
on to make seven successful `run_command` calls and finished on a cloud
sonnet-5 call at 11:27:21.

That is consistent with the read that the *later*, more capable seat reported
the denial honestly while the earlier local seat wrote a success narrative over
the same denial. **Which card said what is not determinable from the ledger**
(§0.3), but the tool receipts behind each task_id are, and they are above.

---

## 7. The duplicate pipeline

**No idempotency anywhere.** Three findings:

1. `services/agent.py:2578` — `save_workflow_chain` writes `{slug}.json`
   unconditionally. No existence check, no "already exists" return. The 11:17:22
   and 11:18:29 calls both produced `voice-triage-evolution.json`; the second
   silently replaced the first.
2. `services/agent.py:2609-2622` — `run_workflow_chain` spawns step 0 with no
   check for an in-flight run of the same slug. `chain_run_status` (`:2628`) can
   already tell whether a chain is running — it is simply not consulted.
3. Nothing dedupes across *names*. "Voice-Triage-Evolution" and "Voice-Triage UI
   Evolution" slug to different files, so two near-identical 3-step pipelines
   ran concurrently on the same box, contending for the same single 12 GB GPU.
   Both files are still on disk with near-identical step prompts.

Why it happened: the local seat created and launched pipeline A at 11:18:31,
then blew its context two seconds later (§4). The cloud model that took over the
turn had no live view of what had already been launched, was being asked "why
didn't you fire the workflow", and did the sensible thing for a model with no
state — it created and ran one.

Concurrency cost, from the ledger: the two pipelines' local `model_invocation`
records in the window carry `tokens_in` of 125,281 / 25,185 / 27,939 / 25,620 /
27,512 against a 32,768-token seat. That is the same context overflow as §4,
repeated, on a card already fighting itself.

---

## 8. Fix list

### Tier 0 — settings-file / one-line, no design decisions

| # | Fix | Where |
|---|---|---|
| F1 | Set `capability_routing.heavy_hitter` to a model that fits, or to a cloud model — **and expect it to be overwritten at the next boot until F3 lands.** | `~/.friday/settings.json` |
| F2 | Add `heavy_hitter` (and `local`) to `seat_transparency._WATCHED` so a seat rebind produces the system line in chat that every other seat produces. | `services/seat_transparency.py:37-42` |

### Tier 1 — small, contained code fixes

| # | Fix | Where |
|---|---|---|
| F3 | Make `propose()` skip any role in `cloud_seats_from_settings(settings)`. The function already exists and is already called by the arbiter at `residency_arbiter.py:951`; `propose`/`apply` simply don't take it as an argument. This is the fix for "my Opus 5 setting was ignored." | `services/seat_binding.py:194-260` |
| F4 | Record the real seat in the ledger: pass the `model` argument through to `_al.record` instead of re-reading `subagent_model`. | `services/agent.py:2502-2505` |
| F5 | Render a failed task as failed. Split the `newlyDone` branch on `t.status` so `failed` gets a ✗ and `completed_unverified` gets a distinct marker. Edit **both** `index.html` and `ui_parts/app.html` — `index.html` is a build artifact of `ui/build_ui.py`. | `index.html:39265,39273`; `ui_parts/app.html:10507` |
| F6 | Exclude denied and errored calls from the evidence set: filter `tool_trace` on the result not starting with a deny/error prefix, or (better) read `tool_receipts.receipts()`, which already carries `ok` and `denied` per call. | `services/agent.py:2294-2295` |
| F7 | Refuse to re-run a chain that is already in flight: call `chain_run_status(name)` in `_tool_run_workflow` and return "already running, step N/M" instead of spawning. | `services/agent.py:2701-2710` |
| F8 | Make `save_workflow_chain` refuse a slug collision unless an explicit `overwrite` flag is passed, and return the existing definition instead. | `services/agent.py:2578` |
| F9 | Give the model its actual seat. `_log_route` already receives the resolved route; append a one-line `== THIS TURN == answering on <model> (<seat>)` to the system prompt from the same decision, and change the `self_account` directive to say "use the THIS TURN line, never the seat table". | `services/agent.py:2201-2222`, `services/self_account.py:184` |

### Tier 2 — real architectural work

| # | Work | Why it is not a one-liner |
|---|---|---|
| A1 | **Separate "installed" from "servable" throughout.** `local_seats.installed()` (`:118`), `model_router._local_candidates()` (`:403`) and `residency_policy` all count a GGUF on disk as available. Only `local_call` (`:60-90`) actually probes the endpoint. This needs one authority — probably `endpoints.json` plus live `/models` verification — and ~4 call sites converted to it. It is the root of §1, §3 and §5. |
| A2 | **Make the heavy seat pick a policy, not a sort.** `residency_policy.py:723` is `gen[0]` — largest wins, full stop — and `:1011` exempts MoE from the fit rule. A model with no VRAM measurement should be *measured before it is seated*, or refused with a stated reason, not admitted by default (`model_router.py:852,887`). Changing the default from admit-on-unknown to refuse-on-unknown affects other seats and needs the golden residency fixtures (`tests/golden/residency/P1-P6.json`) re-baselined. |
| A3 | **Persist the task registry.** `TASKS = {}` (`services/agent.py:1995`) is in-memory; three restarts erased the entire evidence base for this incident within 20 minutes. Task rows, tool receipts and per-step artifacts need a table alongside `work_log.db` before any of §6 can be verified after the fact. |
| A4 | **Gate chain advancement on an artifact, not on prose.** `_advance_task_chain` (`:2863`) checks a substring blacklist. What a workflow step needs is a declared output contract — a file written, a receipt recorded, a named artifact — and a refusal to advance without it. `services/completion_receipts.py` already exists and is the natural home. This is the difference between a pipeline and six prompts in a row. |
| A5 | **Context budgeting for the tool loop.** The 500 at 11:18:43 and the 125,281-token step-1 call are the same defect: `tool_budget.fit_tools_to_seat` trims the *tool schemas* to the seat, but nothing trims the accumulated tool-result transcript. Every long agentic turn on a 32,768-token seat will eventually 500 and escalate to Anthropic. Highest-cost item on the list — the 11:27:21 fallback billed 1.43M input tokens. |
| A6 | **Make the background-task cloud fallback as loud as the chat one.** `routes/chat.py:897` logs and returns a `local_fallback` payload; `services/agent.py:234` just walks the ladder. Unattended work escalating to a paid provider on a local failure should at minimum be a notification, and arguably should respect a per-task ceiling. |

---

## Appendix — records consulted

* `~/.friday/activity_ledger.jsonl` — 3,236 lines; 77 in the 10:50–12:10 window
* `~/.friday/friday.log` — the 2026-08-21 09:27, 2026-08-23 15:45, and
  2026-08-24 11:18:43–11:46:40 blocks
* `~/.friday/chat_history.json` — 500 messages; the 11:03–12:20 window
* `~/.friday/settings.json`, `~/.friday/seat_state.json`
* `~/.friday/workflows/voice-triage-evolution.json`,
  `~/.friday/workflows/voice-triage-ui-evolution.json`
* `~/.friday/runtime/residency/{endpoints,gguf_models,hardware-profile,measurements}.json`
* `~/.friday/runtime/models/models.json`, `~/.friday/runtime/models/gguf/`

---

## Addendum — 2026-08-24 13:25, during the concurrent Opus 5 run

Three fixes applied, plus one live finding that outranks all of them. Nothing
was restarted; the llama-server on :8090 was not touched.

### A. THE BOM: every setting silently ignored since 12:47:20

`~/.friday/settings.json` gained a UTF-8 BOM (`EF BB BF`). The loader read it
as plain `utf-8`:

```python
data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))   # core/__init__.py:1878
...
except Exception:
    return dict(DEFAULT_SETTINGS)
```

`json.loads` raises `Unexpected UTF-8 BOM`, and the bare `except` returns the
factory defaults **with nothing logged**. The 83 keys on disk were intact and
correct the whole time; the running process was reading none of them.

Verified by reading the same file both ways:

| key | on disk (`utf-8-sig`) | what the process saw |
|---|---|---|
| `orchestrator_model` | `gemma4:12b` | `claude-sonnet-5` |
| `model_routing.mode` | `local_preferred` | `cloud_only` |
| `creative_model` | `sd3.5-medium-fp8` | `gemini-nano-banana-2` |
| `capability_routing.heavy_hitter` | `gemma4:12b` (llama-cpp-local) | `""` (ollama-local) |

Consequence for the self-improvement test: the factory `reasoning` seat is
`claude-sonnet-5`, so **the Opus 5 run has been answering on Sonnet 5 since
12:47:20**. Confirmed in the ledger (`model_invocation claude-sonnet-5` at
13:22:30) and in every orb captured since 13:17.

`settings.json.bak-preheal` is timestamped 12:39, matching commit 8a30831
(12:39:09) — a heal pass rewrote the file, and PowerShell 5.1's `Out-File`/`>`
writes UTF-8 **with** BOM by default.

Fixed at `core/__init__.py:1878` — `utf-8-sig`, plus an ERROR log on the
except so an unparseable settings file can never again be silent. **Takes
effect on next restart.** Stripping the BOM from the file would fix the
running process within the 2-second settings-cache TTL with no restart; not
done, because it mutates live state mid-run.

### B. Fix 1 — KV quantization (`residency_arbiter.py:_spawn`)

`--cache-type-k q8_0 --cache-type-v q8_0` added to the seat launch line
(`residency_arbiter.py:611`). Flash attention, which the quantized V cache
requires, was already passed unconditionally.

Headroom computed from `gemma4-12b.gguf`'s own metadata at the `-c 32768` this
seat runs — not estimated:

```
f16   832 MiB      q8_0   442 MiB      saved 390 MiB (47%)
```

Modest, and worth stating as such. Gemma 4 interleaves five sliding-window
layers (1024-token window, 8 KV heads, 256+256 dims) with one full-attention
layer (1 KV head, 512+512), eight times. Only the eight full layers scale with
`-c`, so the f16 cache was never the multi-gigabyte object it is on a dense
model. For scale: the live plan currently refuses the `sidekick` seat with
"needs 1811 MiB, largest remaining budget is 884 MiB" — 390 MiB does not close
that gap.

A one-shot f16 retry (`_spawn` wraps `_spawn_once`) covers a build that
rejects the flag, because the failure mode without it is the worst available:
no seat, every local call 404s, every turn escalates to the cloud — the exact
defect commit 8a30831 was written to fix. **Takes effect on next restart.**

### C. Fix 2 — the seat overwrite (`seat_binding.py:propose`)

`propose()` now skips any role in `cloud_seats_from_settings(settings)`, the
companion function that already existed and that nothing here called.

One refinement the tests forced, and it is the right rule: DEFAULT_SETTINGS
ships `reasoning` and `subagent` pointed at `claude-sonnet-5`, so "is a cloud
entry" would freeze two seats nobody has touched. `_differs_from_factory()`
draws the line the same way `model_router._chosen_seat` already does — an
untouched default is the absence of a preference; a changed value is an
instruction.

Verified by tracing the real path against live settings, no restart
(pure `residency_policy.plan` → `seat_binding.propose`):

```
=== after re-picking claude-opus-5 for heavy_hitter ===
  plan seats:      heavy_hitter  gemma4:26b
  propose -> skipped:
    SKIP  heavy_hitter  filled by the user with 'claude-opus-5' on anthropic,
                        which this planner does not manage
  VERDICT: heavy_hitter left alone  (FIX WORKS)
```

**This stops the next overwrite; it does not restore a value already lost.**
Any cloud seat the planner has already overwritten has to be re-picked once.

### D. Fix 3 — deferred, and why

Tracing the live run changed the answer. The Opus 5 work is **orb**-based
(`PROCESSES`, `core/__init__.py:1209`), not **task**-based (`TASKS`,
`services/agent.py:1995`). Persisting `TASKS` would not have protected this
run. Both registries are equally volatile; only one was in the fix list.

Covered instead by an append-only snapshotter polling `/api/processes` and
`/api/tasks` every 45s into `~/.friday/forensics/`. As of 13:22 it holds 103
orb records including full step traces (49 steps on one orb) — evidence that
would otherwise die on the next restart.

### E. The `--alias` question — RULED OUT for `gemma4:12b`

The concern was that a llama-server alias absent from `ollama list` would read
as uninstalled. It does not, on the two paths that matter:

* `local_call._serves()` (`local_call.py:60-77`, the comment at `:65` the
  earlier session spotted) treats `endpoints.json` as a hint and the server's
  own `/models` as the authority — so the alias is honoured.
* `local_seats.installed()` as of commit 8a30831 keeps a store-only model only
  while `seat_endpoint(name)` is live. Confirmed in `friday.log` at 12:44:15:
  `3 model(s) on disk have no live seat and no daemon entry, so they are not
  offered: gemma4:26b, gemma4:e2b, gemma4:e4b` — `gemma4:12b` is not in that
  list, because it is served.

**Residual, unfixed, different file:** `model_router._local_candidates()`
(`:403-433`) still unions `model_store.available()` with the Ollama tags and
checks no endpoint, so `_pick_local_model` can still choose an unreachable
store model. Same class of bug, one layer over, in a file nobody was asked to
touch today.

### F. Concurrency notes

* Commits today: `2f8af19` (voice.py, nemo_voice.py), `8a30831` and `554bfdc`
  (local_seats.py). This addendum's changes are in `residency_arbiter.py`,
  `seat_binding.py`, `core/__init__.py`. **No file overlap.**
* `tests/unit/test_local_seats.py::test_both_stores_are_merged_without_duplicates`
  fails at HEAD: 8a30831's reachability filter drops `gemma4:12b` because the
  test does not stub `seat_endpoint`. Their surface, reported not fixed.
* Regression check: 15 unit-test failures with these changes against 18 at
  pristine HEAD, and the 15 are a strict subset. **Zero new failures.** (The
  three that pass here fail at HEAD because `ollama_manager.py` has
  uncommitted working-tree changes.)
* `write_file` returned `ok=false` in 2-3 ms three times during the Opus run —
  that is `[CONFIRMATION REQUIRED]` (`agent.py:5167`), the ask-first gate
  working as designed. But `_PENDING_CONFIRMATIONS` is keyed by `session_id`,
  and `_current_session_id()` returns the **calendar date**
  (`model_router.py:1155`), so there is **one confirmation slot per day**: the
  first two pending writes were overwritten by the third. Approving now runs
  only the most recently recorded write, with no record of the others.
