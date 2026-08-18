# Conversations — the seat belongs to the chat, the work belongs to the task

**Date:** 2026-08-18
**Status:** design. **No implementation code exists for this document — it lands first, by
instruction.** Written for a fresh-context session with no priors: every fact needed is in
this file or at a cited file:line.
**Commission, verbatim (Stephen):** *"I want to trigger background tasks, go to a new
chat, talk with a different model, then go back to the other chat to get an update from
the other model while the other other model does something in the background too. By
juggling cloud and local processing I can become more efficient."*
**The prompting incident:** he clicked "+ New Chat" while an Opus 5 research commission
ran in the background; the new chat showed "Friday is thinking" forever and never
answered, though a free local model sat idle. §2.1 names the exact mechanism.
**Build on:** the current default working branch (at spec time `higgsfield-integration`,
HEAD `d3a2f8a`; this doc lands there because it is the tree's active branch — cherry-pick
freely). Several sessions are live in this repo; check `git status` first, commit only
your own files. **The Higgsfield build session is editing `index.html` and
`services/agent.py` in this tree right now** — §6 says how to sequence around that.

**Evidence registers:**
- **VERIFIED** — the cited line, commit, file on disk, or runtime-state file was read
  during the audit runs for this document (2026-08-18).
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 0. What this layer is for, in one paragraph

Friday has exactly one conversation. Not "one visible conversation" — one, structurally:
a single module-level history list serialized to a single JSON file, fed to every model
call; a "session id" that is the calendar date; a confirmation slot keyed by that date, so
a "yes" typed anywhere approves whatever was pending anywhere else; a model seat stored in
global settings, so picking a model in one chat repoints every chat; and one boolean in
the UI meaning "Friday is busy," whose stuckness is the exact hang Stephen hit. Background
work, meanwhile, belongs to nobody: task records carry no owner, completions are delivered
only to a browser tab that personally witnessed the finish, and the channel built for
unprompted delivery has produced 43 messages that no client has ever read (**VERIFIED**,
§2.6). This document specifies the three structural changes that make his described
workflow real — **a conversation is a first-class object that owns its seat; background
work belongs to the task and reports to its owning conversation; admission to compute is
per-resource, with every contention resolving to progress or a stated choice, never a
hang** — and answers the seven questions the commission requires (§8).

---

## 1. The repository you just landed in

Orientation for a stranger; each item has burned a session before. All **VERIFIED**
2026-08-18.

- **Package root is `src/agent_friday/`.** Relative paths below are under it unless
  absolute. Runtime state lives in `~/.friday/` (`FRIDAY_DIR`).
- **Python:** `venv\Scripts\python.exe` — bare `python` resolves to an unrelated venv.
  Tests: `venv/Scripts/python.exe -m pytest` (~1,900 offline tests; `FRIDAY_TESTING=1`
  must be set **before** `import server` — it makes import inert and sandboxes the home
  dir). LLM entry points are autouse-stubbed; a route reaching an unmocked model path
  fails loudly.
- **No Flask reloader** (`server.py:659`, `debug=False`, `threaded=True`): Python changes
  need a full server restart; the server holds a single-instance lock on port 3000.
- **Never edit `index.html` directly** — it is the built, served file. Source lives in
  `ui_parts/` (`app.html` is the main React component, ~917 KB); stage changes through
  `scripts/ui_stage.py`'s `stage()` context manager (syntax-checked atomic swap).
- **Pre-commit secret scanner** (`.githooks/security_scan.py`, active): generic
  key/token/password patterns, emails, `C:\Users\<name>\` paths. False positives:
  `# pragma: allowlist secret`. The repo is **public** — no real keys, no personal paths.
- **House test convention:** real provider/response bodies, fake transport. **CRLF:** use
  `git diff -w`; verify committed state via `git show HEAD:<path>`.

### House rules the implementing session is held to

Standing law, each earned by an incident; §4 maps them to mechanisms:

1. **Never claim an action not taken.** 2. **Never invent a technical constraint.**
3. **A capability the tools can't express is disclosed, not silently substituted.**
4. **Warn before going silent, with a real estimate.** 5. **Report conclusions into the
conversation unprompted.** 6. **A subsystem that runs and produces nothing is a failure
even when it exits zero.** And from the judgment-gate work, now law:
7. **Stephen's explicit instruction outranks the classifier** at the cloud-egress gate —
   this spec must not weaken that, and §3.2's per-conversation seats must not create a
   path around the vault rules (they don't: MC8).

---

## 2. The verified ground

### 2.1 The hang: one global flag, client-side, already mitigated — not solved

**VERIFIED** (`ui_parts/app.html`, the pre-fix source): the entire app has one busy flag —
`const [chatLoad, setChatLoad] = useState(false)` (`app.html:8632`). The send path
renders the user's message optimistically, **then** hits a global one-turn guard:
`if(chatLoad && !opts.skipForecast) return;` (`app.html:10596`) — no fetch, no error, the
message is swallowed after appearing on screen. The "Friday is thinking" indicator is
bound to the same flag (`app.html:11144`). "+ New Chat" (`app.html:11094`) clears the
message array and POSTs `/api/chat/clear` — it never resets `chatLoad`. Sequence: long
Opus turn sets the flag → New Chat leaves it set → the old turn's indicator renders in
the empty new chat → every subsequent send is silently swallowed. The fetch itself has no
timeout and no `AbortController`; an unsent message is visually indistinguishable from a
slow one.

**The server was never the problem**: `/api/chat` takes no lock; Flask is threaded; a
background task holds `TASKS_LOCK` only for dict mutation; the Anthropic client is a
thread-safe singleton. A second POST would have been served —

**— and would have been corrupted.** Two concurrent turns share every piece of state in
§2.2–2.4. The hang is the symptom; the shared state is the disease.

**Already landed in this tree** (**VERIFIED**, working-tree `index.html:34735-36109`,
another session's in-flight work): an epoch-scoped guard (`chatEpoch`/`inFlightEpoch`), a
15-minute stall release, and a flag reset on New Chat. That is a correct *mitigation* —
it un-sticks the single conversation. It is not per-conversation state, and this spec's
client work (§3.1) supersedes it; coordinate with whatever has landed at build time.

### 2.2 There is no conversation

**VERIFIED:** `grep -rn "conversation_id|conversationId"` across `src/`, `index.html`,
`ui_parts/` returns **zero hits**. What exists instead:

- **One global transcript**: `CHAT_HISTORY` (`core/__init__.py:2250`), a module-level
  list persisted to one file (`chat_history.json`, `core/__init__.py:2216`). Every turn
  reads `CHAT_HISTORY[-100:]` as context (`routes/chat.py:244`) and appends to it
  (`chat.py:828-829`); a global prune keeps 500 messages total (`chat.py:904`). Two
  concurrent conversations would interleave into one transcript and feed each other's
  turns to their models.
- **"+ New Chat" is a global wipe**: `/api/chat/clear` (`chat.py:1163-1180`) deletes all
  non-pinned history — including the *running* conversation's context, mid-turn.
- **The session id is the calendar date**: `_current_session_id()`
  (`services/model_router.py:1115-1119`) returns `strftime("%Y-%m-%d")`. Everything
  "per-session" is actually per-day.
- **Client state is global**: `chatMsgs`, `chatIn`, `chatLoad`, `pausePending` — one set
  for the app (`app.html:8632`, `:10576`). History is server-owned; the client sends only
  `{message, voice_mode, cite_sources, route_mode?, workspace?, image?}`.

The one per-entity chat precedent in the tree: the workspace studio's per-`ws_id` chat
(`routes/workspace_studio.py:31-69`, `services/workspace_studio.py:399-420`) — the shape
to generalize.

### 2.3 The confirmation hazard

`_PENDING_CONFIRMATIONS` is keyed by session id — the date (`services/agent.py:4466`).
**One pending destructive-action slot per day, shared by everything.** Under
multi-conversation, a "yes" typed in chat B would approve the action chat A queued.
**VERIFIED**; this is a correctness-of-consent bug the moment two chats exist, and §3.9
fixes it as a precondition, not a nicety.

### 2.4 The seat is global, by design that must now grow a second layer

**VERIFIED** end to end: the model picker writes `{orchestrator_model: id}` into global
settings (`app.html:10755`, `saveAgentSettings` → `POST /api/settings`);
`_sync_capability_routing` (`core/__init__.py:1690`) mirrors it into
`capability_routing.reasoning`; routing reads it back globally
(`routing/model_router.py:291`, `chat.py:298` et al.). Commit `92fd44c` ("the model you
pick is the model that answers") made interactive chat *honor* this — the right fix for
one conversation, and precisely why switching models in one tab switches every tab,
persisted to disk. The only per-turn input today is `route_mode` (`chat.py:361-370`), a
one-turn cloud escape hatch that the vault rule can refuse.

Two more module-level singletons that corrupt under concurrency (**VERIFIED**):
`services/attribution.py` (reset at turn start `chat.py:326`, read at end `chat.py:803` —
two concurrent turns cross-badge "which model answered"), and the Computer-Control
events (`agent.py:3033-3034`) which force-route *any* turn to cloud while CC is on.

### 2.5 Background work: who owns it, what survives — the audit table

**VERIFIED**, including against live `~/.friday/` state:

| Kind | Survives restart? | Owner | Reports where |
|---|---|---|---|
| `spawn_task` (chat background task) | **No** — `TASKS` is a bare in-process dict (`agent.py:1969`), daemon threads, no serialization, no boot recovery; chain *position* lost even though chain *definitions* persist in `~/.friday/workflows/` | none — no owner field of any kind on the record (`agent.py:2360-2374`) | bell notification + a chat line only if the browser tab personally watched the running→done transition (§2.6) |
| `work_queue` item | **Partially** — `queue.json` is atomic-persisted; a `queued` item is picked up by the next drain, but a **`running` item strands forever** (nothing resets it; `purge_finished` preserves it) | global; `workflow_id` only | nowhere — `drain()` returns to its caller, pushes nothing |
| Research commission | **Storage yes, execution no.** Directory + atomic `commission.json` + append-only `findings.jsonl` all survive. But `run()` never resumes (`services/research/__init__.py:68` starts at `scope()` unconditionally), `run_async` is a bare daemon thread, and **no boot hook scans `~/.friday/research/`**. The module docstring *claims* "a restart resumes from the last recorded finding" (`objects.py:5-9`) — **that resume does not exist**; the docstring is aspirational | global; no owner field; **also no UI, no orb, no chat tool** — startable only via raw HTTP | bell notification only |
| Higgsfield job | N/A — spec only at audit time (build in flight in this tree); its spec *does* mandate boot re-adoption | per its spec | per its spec |
| Scheduled run | **Yes, by design** — `schedules.json` + due-ness from persisted `last_run_ts`; in-memory `_RUNNING` clears rather than strands | global (`schedule_id`/`run_id`) | self-updating bell entry; deliberately filtered out of chat |

**Stephen's three surviving commissions, adjudicated (his Q6): design at the storage
layer, luck at the execution layer.** Live proof on disk right now (**VERIFIED**,
`~/.friday/research/`): the server restarted at 16:44:47 (`friday_server.pid`,
`friday.log`); commission `7a2fdcb4f468` is frozen at `grinding` (3 findings) and
`b81e25eab743` at `synthesizing` (26 findings); **zero `friday.research` log lines exist
after the restart**. Their state survived beautifully; their execution died silently, and
because research has no orb and no UI, nothing anywhere shows them stuck. Also found: two
empty orphan commission dirs (`objects.py:157` creates the directory in `__init__`,
before first save; failures in between leak dirs that `list_commissions` silently skips).

**The restart clean-slate is total silence** (**VERIFIED** `server.py:220-289`): boot
starts the scheduler, residency, monitors — and scans for *no* orphaned work of any kind.
`PROCESSES` and `TASKS` are empty dicts at import, so a task in flight at restart
produces no orb, no tray row, no notification, no chat line. It simply never happened —
the house's runs-and-produces-nothing rule, violated by omission at every restart.

### 2.6 Delivery: one dead channel, one fragile one

**VERIFIED:** the unprompted-delivery channel is fully built server-side and has *never
fired once* client-side. Producers set `proactive_chat=True` (tasks `agent.py:2311-2320`,
research `deliver.py:268-272`); `pending_chat_injections` serves them
(`notifications_engine.py:295-311`) — and no UI file references `chat-injections` at all.
Of 200 live notifications, **43 are `proactive_chat: true`; `chat_injected` is true on
exactly 0.** (`docs/ACTION_CREATION_SPEC.md:120` suspected this; it is now confirmed.)

What the chat *does* show comes from a different mechanism: the `/api/tasks` poller
(`index.html:38299-38342`) appends "✅ Task complete" to whatever chat is on screen — but
only if this browser session `prev.some(...status === 'running')` — i.e. **the client
must personally witness the running→done transition**. Reload the page, or restart the
server, and the completion is never announced. Research never appears at all (not in
`TASKS`, not in `PROCESSES`). The envelope has no addressee field anywhere
(`target` is a workspace deep-link, not a conversation).

### 2.7 Foundations that are already right

- **The orb/process registry is the cross-everything view in embryo** (**VERIFIED**
  `core/__init__.py:1140-1174`): 17 modules register into it (chat turns, image jobs,
  scheduled runs, model pulls, background tasks); `/api/tasks` merges `TASKS` +
  `PROCESSES` with computed visibility lifetimes; within-session orphan reaping exists
  (`routes/tasks.py:447-483`). It lacks exactly two things: an **owner field**, and
  **research registering at all**.
- **The arbiter's refusal shape is the right admission primitive**: `grant()` returns
  `{"ok": False, "error": "lease X already held"}` immediately — a decision, never a
  block (`residency_arbiter.py:604-607`). §3.3 applies the same shape one level up.
- **Cost metering has the right seams**: `cost_calls` has no conversation column
  (**VERIFIED** schema `cost_meter.py:184-189`), but `_resolve_attr`'s merge order and
  `register_task_attribution` (the cross-thread seam, `cost_meter.py:124-165`) are
  exactly where a `conversation_id` rides.
- **`model_store` is honest about deleted models** — `available()` returns only
  models whose file exists; `missing()` names the rest with the reason
  (`model_store.py:195-209`). But **`capability_router.resolve` never checks the model
  field at all** (`capability_router.py:63-77` — `available` is provider-level, and
  hardcoded `True` for local), and the boot rebind repairs a dangling pointer **only
  when a replacement seat exists** — `seat_binding.propose` `continue`s on a missing
  seat, leaving the stale entry in place. Delete the last model that could fill a role
  and the dead pointer stays. This is Q7's mechanism, and §3.8 closes it.
- **No streaming exists**; a chat turn is one blocking POST with up to 999 tool
  iterations (`agent.py:5315`) and no server or client timeout. This spec does not build
  streaming (out of scope) — but §3.3's admission states and §3.1's per-conversation
  in-flight tracking are designed so streaming can land later without rework.

### 2.8 Findings that change the design

**(a)** The hang was client-side, but fixing only the client ships a data race: the
server's global transcript, confirmations, and attribution corrupt under the very
concurrency the client fix permits. The conversation object is not UI polish — it is the
correctness fix.
**(b)** Ownership cannot be retrofitted at delivery time. The owner must be stamped **at
spawn**, in every work record, or completions have nowhere to go (§2.6's dead channel
died precisely because "the chat panel, singular" was a valid address).
**(c)** Delivery must be server-side into the conversation's persistent record. Any
design that requires a live browser to witness a transition (§2.6) re-creates tonight's
silent losses.
**(d)** Restart adoption must distinguish *resumable* work (structured, stage-checkpointed:
research commissions, queue items) from *non-resumable* work (a free-form agentic turn
mid-flight). Resume the first; honestly report the second. Never silence.
**(e)** The global seat setting is correct as a *default* and wrong as the *binding*.
Layer, don't replace: conversation binding over global default, with per-turn overrides
(`route_mode`) and the vault law above both.

---

## 3. The design

### 3.1 The Conversation object

**Store:** `~/.friday/conversations/<conversation_id>/` — `conversation.json` (atomic
tmp+replace, the house pattern) + `messages.jsonl` (append-only). One id format:
`conv-<8hex>`.

```
Conversation
  id, title            # title auto-derived from the first user message; renamable
  created_at, last_active_at
  seat                 {provider, model} | null    # null = follow the global default
                                                   # at dispatch time (live, not snapshot)
  status               active | archived           # archived ≠ deleted; §3.5
  pinned               [message ids]               # pins are per-conversation
  totals               {turns, cost_usd, tokens}   # denormalized for the list view

Message (one JSONL line)
  id, role (user|friday|system_report), ts
  text
  meta                 {model, provider, seat, attribution, task_id?, kind?}
                       # kind: turn | task_report | interruption_notice | ...
```

**API:** `POST /api/conversations` (create → id), `GET /api/conversations` (list with
totals + running-work counts), `GET/PATCH /api/conversations/<id>` (rename, archive,
seat), `GET /api/conversations/<id>/messages`. **`POST /api/chat` gains a required
`conversation_id`** (absent → the designated *main* conversation, §6, so voice, channels,
and the heartbeat keep working unchanged). Context for a turn is that conversation's last
N messages plus its pins — never another conversation's.

**Per-conversation clear/prune:** `/api/chat/clear` becomes
`POST /api/conversations/<id>/clear` (same pinned-preserving semantics, scoped); the
500-message prune applies per conversation.

**Client:** `chatMsgs`/`chatIn`/`chatLoad`/`pausePending` become per-conversation state —
a `Map<conversation_id, {...}>` behind the existing component, with a conversation list
in the left rail (the workspace-studio chat already renders a scoped transcript; reuse
its shape). "+ New Chat" = `POST /api/conversations` + switch — it touches nothing
belonging to any other conversation. The in-flight guard survives **scoped**: one turn in
flight *per conversation* is correct UX; across conversations, sends are independent.
The epoch/stall mitigation in the working tree is superseded by this but its 15-minute
stall release is kept per-conversation (a fetch with no timeout still needs a floor).

**What stays shared (his Q2, answered):** long-term memory (ChromaDB, wiki, knowledge
graph, vault context via the system prompt) is Friday's memory and remains global —
what she *knows* does not fork per chat. What is isolated is the **working transcript**:
turns in conversation A are never context for conversation B. No automatic cross-chat
bleed; if he wants Friday to consult another chat, that is an explicit ask Friday can
serve by reading the other conversation's store — a tool, not an ambient behavior.
**INFERRED** this matches how he described working (different models, different threads,
different jobs); Q-S2 lets him veto.

### 3.2 The seat belongs to the conversation

- `conversation.seat = null` (default) means: resolve the global
  `capability_routing.reasoning` **at each turn** — new conversations track the global
  default, so today's behavior is the degenerate single-conversation case.
- **The in-conversation model picker writes `conversation.seat`, not global settings.**
  The global default moves to Settings, where a default belongs. This is the fix for
  "switching models in one tab switches every tab" — and it must be stated in the UI:
  the picker's label shows *"for this conversation"*.
- Per-turn `route_mode` survives as-is (one-turn override above the binding).
- **Precedence, absolute (MC8):** vault-forced local routing and the judgment gate
  outrank the conversation binding, exactly as they outrank the global seat today — a
  conversation bound to Opus still runs its vault-touching turns locally, with the
  existing explanation shapes. And Stephen's explicit instruction outranks the
  classifier at the gate, unchanged: binding a conversation to a cloud model **is not**
  an explicit egress instruction for vault content; the gate's existing rules decide,
  per payload, as now.
- Attribution becomes turn-scoped: `attribution.py`'s module singleton is replaced by a
  per-turn context object created in the route and threaded through dispatch (the
  audit's cross-badging defect, §2.4). The reply badge must name the model that answered
  *this* turn — a house-rule surface, not cosmetics.

### 3.3 Admission is per-resource: progress or a stated choice, never a hang

No global turn gate exists anywhere after this build — not in the client (§3.1), not in
the server (none exists today, **VERIFIED**). Instead, each resource admits work by its
own nature, and **every contention resolves within 5 seconds to visible progress or a
stated choice**:

| Resource | Nature | Admission |
|---|---|---|
| Cloud seats (Anthropic, etc.) | concurrent by provider design | no local gate; provider rate-limit errors surface per-conversation with the provider's words |
| Pinned local seats (12b brain, e2b sidekick — llama-server processes) | **serialized per seat process** (one generation at a time; a second HTTP request queues inside llama-server) — **INFERRED** from `-np` default; **UNKNOWN** the actual queue behavior under 2 concurrent requests: the implementer's first check is two simultaneous curls against a seat port, measuring whether the second queues, errors, or interleaves | a per-seat in-process semaphore + FIFO wait list, owned by dispatch. A turn that must wait registers its orb as `waiting: gemma4:12b busy (1 ahead, ~40s)` — the estimate from the seat's measured tok/s and the running turn's progress |
| Heavy lease / image lease | exclusive, arbiter-owned | unchanged — `grant()` refuses with a reason; the refusal surfaces **in the requesting conversation** with the existing choice shape (wait / different seat / cloud), instead of being swallowed |
| Background task threads | cheap OS threads | bounded by a `max_concurrent_tasks` setting (default 4) so a runaway spawn loop cannot fork-bomb; excess queue with visible position |

**The waiting choice, concretely:** when conversation B's turn wants the 12b and it is
busy with conversation A's turn, B's chat shows — within the 5-second budget — *"The
brain is answering another conversation (~40 s left). Wait, use the sidekick now, or use
[cloud model] now?"* Wait is the default and queues FIFO; the alternatives re-dispatch
the turn under the existing routing law (a vault turn only offers local alternatives).
This is `warn before going silent` fused with the arbiter's refusal shape, one level up.
A conversation whose seat is the *sidekick* almost never waits — which is the measured
architecture working as designed (the sidekick survives every lease precisely to keep
Friday answerable).

### 3.4 The work belongs to the task

**Every work record gains owner fields at spawn** — the five insertion points, from the
audit: `TASKS[task_id]` (`agent.py:2360`), the work-queue item (`work_queue.py:176`),
`Commission.save` (`research/objects.py:178`), `PROCESSES[pid]`
(`core/__init__.py:1155`), and a nullable `conversation_id` column on `cost_calls` fed
through `_resolve_attr` + `register_task_attribution` (`cost_meter.py:124-165`).

```
owner = {conversation_id, spawned_by: chat_turn | schedule | api | pipeline}
```

**`spawn_task` gains persistence** — the piece whose absence tonight's restart exposed:
a task ledger at `~/.friday/tasks/<task_id>.json` (atomic), written at spawn and on
every status/log transition, mirroring the in-memory record (which remains the hot
copy). Chain position (`chain`, `chain_step`) is in the ledger, so a chain interrupted at
step 2 of 5 is *known* to be at step 2 of 5.

**Boot adoption — the restart contract (MC5), replacing today's total silence:**

At boot, one reconciliation pass scans all four persistent stores:

- **Research commissions** in a non-terminal status: **resume** — the structured
  pipeline is checkpointed by design (`findings.jsonl` is append-only; stage is in
  `commission.json`), so `run()` gains the resume the docstring already promises:
  re-enter at the recorded stage, skipping completed sub-questions (grind) or
  re-synthesizing from banked findings (synthesize). The two commissions frozen on disk
  right now (`7a2fdcb4f468`, `b81e25eab743`) are the build's live acceptance test:
  after this lands, booting the server **must** unfreeze both — one to delivery, one
  through its remaining grind. Also fixed here: the empty-dir leak (`objects.py:157` —
  create the dir at first save), and research finally registers a PROCESS orb.
- **Work-queue items** stuck in `running`: reset to `queued` with a log note; the next
  drain takes them.
- **Task ledger entries** in `queued`/`running`: **not resumed** — a free-form agentic
  turn cannot be safely replayed (its side effects are unknown; replay risks doubled
  actions, the same reasoning as Higgsfield's no-resubmit rule). Instead each is marked
  `interrupted` and an **interruption notice is delivered into its owning conversation**
  (§3.5): *"This task was running when the server restarted at HH:MM. It did not
  complete. Restart it?"* with the restart being a fresh spawn carrying the same prompt
  and owner. Honest, actionable, never silent.
- **Higgsfield jobs**: per that spec's own re-adoption design (polling resumes from the
  persisted store) — no change, but this pass is where its scan naturally lives.

Chains: an interrupted chain reports its position ("step 2 of 5 — steps 3–5 did not
run") and offers resume-from-step, which *is* safe (the chain definition is declarative
and each step is a fresh spawn).

### 3.5 Delivery to the owning conversation — and the death of witnessing

**One channel, server-side, into the record.** When owned work reaches a terminal state,
the completion path **appends a message directly into the owning conversation's
`messages.jsonl`** (`role: system_report`, `meta.kind: task_report`, carrying the
result lede, the artifact links, cost, and the actual model that served). Delivery is a
disk write — it happens whether or not any browser is open, and any future render of
that conversation shows it. The bell notification remains as the *attention* layer
(unchanged producers), now carrying `conversation_id` in its target so clicking it jumps
to the right chat; the conversation list shows an unread badge.

The two current mechanisms collapse into this: the never-consumed `chat-injections`
endpoint is **deleted** (43 produced, 0 consumed — a subsystem that runs and produces
nothing, retired by name), and the `/api/tasks` witnessed-transition poller
(`index.html:38299`) is retired in favor of rendering `task_report` messages from the
conversation store. Nothing about delivery depends on a client having been awake.

**His Q1 — closing or deleting the spawning chat:** conversations **archive** rather
than hard-delete (the store is cheap; his history is journalism). Archiving changes
nothing for running work — the task runs on, the report lands in the archived
conversation, the bell + overview (§3.6) still surface it, and opening the archive shows
it. **Hard delete** is offered only when the conversation has no running work; if work
is running, the delete dialog states it and offers: cancel the work and delete, or
archive instead. A task can therefore never be orphaned by UI action — its owner always
exists. (Owner-of-record for schedule-spawned work is the *main* conversation.)

### 3.6 The overview: everything running, across all chats

One surface, built on the registry that already sees almost everything (§2.7): the task
tray grows into **an all-conversations view** — every running/waiting/queued item
(chat turns, background tasks, research commissions, image jobs, Higgsfield jobs,
scheduled runs) with: owner conversation chip (click → jump), seat/provider and the
actual model, state (`running 2m · gemma4:12b` / `waiting: brain busy, 1 ahead` /
`queued`), cost so far for cloud work, and the existing cancel affordance. Server side
this is `/api/tasks` plus the new owner field plus research/Higgsfield registration —
assembly, not architecture. The orb dock stays as-is (it is this view's ambient form);
the tray panel becomes its full form. **His Q4 answered: he sees everything in one
place, and every row knows its way home.**

### 3.7 Cost, per conversation (his Q5)

`conversation_id` column on `cost_calls` (§3.4); the conversation header shows its
running total (`$0.43 this conversation`); the overview shows today's total across all
conversations with a per-conversation breakdown on tap — served by the existing
`/api/costs` aggregation plus one GROUP BY. The existing daily-budget machinery gains no
new policy here (budget *caps* stay global; Q-S3 asks if he wants per-conversation
caps). What changes is visibility: three cloud chats open means three visible meters and
one visible sum, before the bill, not after.

### 3.8 A conversation whose model is gone (his Q7)

At dispatch, the conversation's resolved seat is validated against reality:
`model_store.available()` for local models (the honest store, §2.7), provider
availability for cloud. On a miss, the turn does not dispatch and does not silently
fall back — the conversation gets the stated choice: *"This conversation was bound to
<model>, which is no longer installed (deleted 2026-08-18). Use the current default
<model>, pick another, or reinstall?"* Choosing rebinds (`conversation.seat` updated,
noted in the transcript). The same validation runs when rendering the conversation list
(a dead binding shows a warning chip before he even sends).

Two upstream repairs land with this: `capability_router.resolve()` gains the
model-existence check it lacks (**VERIFIED** gap, §2.7 — `available: True` is currently
hardcoded for local providers regardless of the model field), and `seat_binding.propose`
stops leaving a stale `capability_routing` entry when the planner produces no seat for a
role — the entry is cleared with an explained refusal, so the dangling pointer the boot
rebind can't fix stops surviving boots.

### 3.9 Consent and confirmation, scoped

`_PENDING_CONFIRMATIONS` is re-keyed from the date to `conversation_id`
(`agent.py:4466`); a confirmation prompt and its "yes" live and die in one conversation.
The turn-scoped attribution object (§3.2) carries the confirmation context so
concurrent turns in different conversations each hold their own pending slot. The
Computer-Control force-cloud events stay global (CC is a machine-level mode), but the
force-route is *disclosed* in any conversation it redirects.

---

## 4. Rules as data

| id | Rule |
|---|---|
| **MC1** | A conversation owns its seat. The global setting is only the default for `seat: null` conversations; the in-chat picker writes the conversation, never the globe |
| **MC2** | Background work belongs to the task: owner stamped at spawn in every work record; work survives its owner's archiving; hard delete of a conversation with running work is refused with choices |
| **MC3** | Admission is per-resource. No global turn gate exists, server or client. Every contention resolves within 5 s to visible progress or a stated choice — a queue position with an estimate is progress; silence is neither |
| **MC4** | Delivery is a server-side append into the owning conversation's record. No delivery path may require a client to have witnessed anything |
| **MC5** | Boot reconciles every persistent store: structured work resumes from its checkpoint; free-form work is reported `interrupted` into its owning conversation with a restart offer. A restart may never silently erase in-flight work from the record |
| **MC6** | A missing bound model produces a stated choice at dispatch, never a silent fallback (disclosed substitution law) |
| **MC7** | Confirmations are scoped to the conversation that asked. A "yes" approves only what its own conversation queued |
| **MC8** | Vault law and the judgment gate outrank any conversation binding and any per-turn override, unchanged — and Stephen's explicit instruction outranks the classifier, unchanged. Per-conversation seats create no new egress path |
| **MC9** | Every cloud call is attributed to its conversation in the cost ledger; the sum is visible before the bill |
| **MC10** | The reply badge names the model that answered *this* turn, from turn-scoped attribution — never from a module global |

---

## 5. Migration and backcompat

- **`chat_history.json` becomes the first conversation**: imported once at boot into
  `conversations/conv-main/` (id fixed: `conv-main`, title "Main"), pins preserved. The
  old file is left in place, renamed `.migrated`, never re-read. `conv-main` is the
  designated **main conversation**: `POST /api/chat` without a `conversation_id`, voice
  turns, channel bridges, and schedule-spawned work all target it — so every existing
  caller works unchanged on day one.
- **`/api/chat/clear`** keeps working as an alias for clearing `conv-main` (the UI stops
  calling it; external callers degrade gracefully).
- The `session_id` date string survives where it means "today" (ChromaDB grouping, the
  dossier route) but loses its two abusive jobs: confirmations (→ conversation id,
  §3.9) and any implication of conversation identity.
- **Sequencing with the live Higgsfield build:** that session owns `index.html` +
  `services/agent.py` right now. This build's server work (conversation store, owner
  fields, boot adoption) is additive and mostly in new files plus `routes/chat.py` /
  `core/__init__.py`; the client work touches the same `app.html` chat component the
  epoch patch just landed in. **Land after the Higgsfield build merges, rebase the
  client work over their epoch patch, and delete the epoch mitigation in the same
  commit that ships per-conversation state** — two half-fixes to the same flag must not
  coexist.

---

## 6. Build order

One commit each, offline-testable; the test convention is real bodies / fake transport;
UI changes through `ui_stage.py`.

1. **Conversation store + routes + migration** — CRUD, append, atomic writes, the
   `conv-main` import. Test: two conversations hold disjoint transcripts; a turn's
   context never contains the other's messages; clear is scoped.
2. **`conversation_id` through the chat path** — payload → route → context build →
   history append; `session_ctx` carries it. Test: concurrent POSTs to two
   conversations produce no interleaving (assert on both stores).
3. **Turn-scoped attribution + scoped confirmations** — the singleton retired; pending
   confirmations keyed by conversation. Test: two pending confirmations coexist; a
   "yes" in B leaves A's pending; concurrent turns badge their own models.
4. **Per-conversation seats** — `conversation.seat`, picker rewrite, precedence
   (vault > route_mode > seat > global). Test: conversation A on Opus and B on the 12b
   answer concurrently, each badged correctly; a vault turn in A runs local with the
   existing explanation.
5. **Per-seat admission** — the semaphore + wait list + the 5-second choice surface;
   first measure the **UNKNOWN** (two concurrent requests against one llama-server
   seat) and encode what is found. Test: B's turn against a busy seat yields the choice
   payload, not a hang; FIFO order holds.
6. **Owner fields everywhere + task ledger** — the five insertion points + persisted
   spawn_task records with chain position. Test: every work record round-trips its
   owner; cost rows carry conversation_id.
7. **Server-side delivery** — `task_report` appends; bell targets carry
   conversation_id; the witnessed-transition poller and the dead `chat-injections`
   endpoint removed. Test: a task completing with zero clients connected produces the
   message in the store; reload renders it; unread badge counts it.
8. **Boot reconciliation** — research resume (+ orb registration + empty-dir fix),
   queue `running`→`queued`, ledger `interrupted` notices. **Live acceptance: booting
   this build on Stephen's machine unfreezes `7a2fdcb4f468` and `b81e25eab743`** — the
   frozen commissions are the fixture reality provided. Test (offline): a synthetic
   frozen commission resumes at its recorded stage; an interrupted task's notice
   appears in its owner's store.
9. **The overview** — owner chips, states, costs, jump-to-conversation; research and
   Higgsfield rows present. Test: rows for every kind, each with a resolvable owner.
10. **Model-gone handling** — dispatch validation + the two upstream repairs
    (`capability_router` existence check; `seat_binding` stale-entry clear). Test: a
    conversation bound to a deleted model yields the choice; rebinding writes the seat
    and a transcript note.

---

## 7. The commission's seven questions, answered

1. **Closing/deleting the spawning chat:** conversations archive, not delete; work runs
   on and reports into the archive; hard delete with running work is refused with
   cancel-or-archive choices (§3.5). A task can never be orphaned by the UI.
2. **Context between conversations:** transcripts isolated, long-term memory shared —
   Friday's knowledge is one; her working threads are many (§3.1). Cross-chat reading
   only as an explicit ask.
3. **Two conversations, one local seat:** per-seat FIFO admission with a visible queue
   position and estimate, plus an immediate offer of sidekick/cloud alternatives under
   the routing law — a decision within 5 seconds, never a hang (§3.3).
4. **Seeing everything at once:** the tray grows into the all-conversations overview —
   every running item, its owner chip, seat, state, and cost, one click from its home
   (§3.6).
5. **Cost with several cloud chats:** per-conversation attribution in the ledger,
   running total in each header, today-sum with breakdown in the overview (§3.7).
6. **Restart survival:** tonight was design at storage, luck at execution — commissions
   persisted but nothing resumed them; two are frozen on disk now. The restart contract
   (MC5) makes survival design at both layers: structured work resumes, free-form work
   is honestly reported interrupted with a restart offer (§3.4, §2.5).
7. **Bound model removed:** validated at dispatch and in the list view; stated choice to
   rebind, never silent fallback; the two upstream dangling-pointer holes closed (§3.8).

---

## 8. Open questions for Stephen

Each answerable in a sentence.

**Q-S1 — The main conversation.** Voice, channels, and scheduled work all report into
one designated "Main" conversation (§5) — right call, or should voice get its own
conversation per session?

**Q-S2 — Memory sharing.** §3.1 keeps long-term memory shared and transcripts isolated —
does that match how you work, or do you want an explicit "carry context from this chat
into a new one" action at creation?

**Q-S3 — Budget caps.** Cost becomes visible per conversation; do you also want a
per-conversation spend *cap* (refuse past it, arithmetic shown), or is the existing
global daily view enough?

**Q-S4 — New-chat default seat.** A new conversation follows the global default model —
or would you rather it inherit the seat of the conversation you spawned it from?

**Q-S5 — Interrupted free-form tasks.** After a restart they are reported with a
"restart?" offer rather than auto-restarted (§3.4) — acceptable, or should tasks marked
somehow (e.g. "safe to rerun") auto-restart?

**Q-S6 — Archive retention.** Archived conversations keep everything forever by
default — fine on disk, or do you want an auto-archive/cleanup policy after N months?

---

## 9. Sources

- Two repo audits, 2026-08-18, file:line cites inline throughout — chat/dispatch/UI
  (`routes/chat.py`, `services/agent.py`, `core/__init__.py`, `ui_parts/app.html`,
  working-tree `index.html`), and ownership/survival (`services/{work_queue,scheduler,
  notifications_engine as notifications root module,cost_meter,model_store,
  capability_router,seat_binding,residency_arbiter,attribution}.py`,
  `services/research/*`, `routes/tasks.py`, `server.py`), plus live runtime state in
  `~/.friday/` (research commissions, queue, notifications, pid file, log).
- Commits read: `92fd44c` (the model you pick is the model that answers), `05076cb`
  (orb dismissal + model list truth), `7c4a64f` (a stopped daemon has no models),
  `d13b7cc` (model list truth), and the in-flight epoch patch in this tree's
  `index.html`.
- Prior design docs this composes with: [`residency-policy.md`](residency-policy.md)
  (the arbiter whose refusal shape §3.3 generalizes),
  [`symphony-of-intelligence.md`](symphony-of-intelligence.md) (seat economics; the
  sidekick that keeps Friday answerable), [`deep-research.md`](deep-research.md) (the
  commissions §3.4 finally resumes), and
  [`higgsfield-integration.md`](higgsfield-integration.md) (whose job store §3.4's boot
  scan adopts).
