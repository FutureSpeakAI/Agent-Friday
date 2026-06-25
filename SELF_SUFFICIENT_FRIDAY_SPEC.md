# Self-Sufficient Friday — Implementation Spec

**Status:** Draft for review · **Owner:** Stephen Webster · **Date:** 2026-06-25
**Goal:** Make Agent Friday run *all* of her recurring work herself — no dependency on Claude Desktop / Cowork's scheduled-task system — and fold in three governance/efficiency patterns learned from the OpenHarness comparison: **lifecycle hooks**, **auto-compaction**, and **cost metering**.

---

## 0. TL;DR for the greenlight decision

Four workstreams, independently shippable, dependency-ordered:

| # | Workstream | What it buys | Effort | Risk |
|---|-----------|-------------|--------|------|
| **A** | Internal Task Scheduler | Friday owns her own cron. Cowork can be deleted. User-editable schedules, hourly + daily + weekly, run history, retries. | ~6–8 dev-days | Low–Med |
| **B** | PreToolUse / PostToolUse hooks | One clean governance seam. Audit, rate-limit, PII, veto — all without touching tool code. | ~2–3 dev-days | Low |
| **C** | Auto-Compaction | Long voice/chat sessions stop overflowing context. | ~2–3 dev-days | Med |
| **D** | Cost Metering | Every model call costed; `/api/costs` + Settings dashboard + budget alerts. | ~3–4 dev-days | Low |

**Total: ~13–18 dev-days.** Recommended order: **B → A → D → C** (hooks first because A and D both attach to them; compaction last because it's the most isolated).

### The single most important finding

Friday is **not actually dependent on Cowork's scheduler today.** She already has a self-contained, timezone-aware, in-process scheduler:

- `register_daily_job(name, hour, minute, fn)` — `services/notifications.py:295`
- `_daily_scheduler_loop()` — `services/notifications.py:322` (ticks every 60s, Central time, idempotent via `~/.friday/daily_scheduler_state.json`)
- Started as a daemon thread at boot — `server.py:146`

What's missing is everything that makes it a *product*: **hourly granularity, user editing, run history, retries, a UI, and persistence of user-defined schedules.** The Cowork "friday-heartbeat / friday-daily-creation / …" tasks are a parallel, external mirror that we want to retire. So Part A is **"promote the existing scheduler to first-class,"** not "build a scheduler from scratch." This materially lowers the risk and effort.

Likewise, a `CostTracker` class **already exists** (`model_router.py:77`) — but only the OpenAI/Ollama path records into it; the Anthropic path (`_call_claude_agent`, `_call_claude`) throws `resp.usage` away. Part D is mostly "wire up the call sites + persist + expose," not "invent cost tracking."

---

## 1. Current-state map (ground truth)

### 1.1 Scheduling (already in-process — Central time)

```
server.py:145   _register_default_daily_jobs()          # registers built-ins
server.py:146   Thread(_daily_scheduler_loop).start()    # daemon, 60s tick

services/notifications.py
  :295  register_daily_job(name, hour, minute, fn)        # PUBLIC api
  :305  _daily_state_read()                               # ~/.friday/daily_scheduler_state.json
  :312  _daily_state_mark(name, date_str)                 # idempotency
  :322  _daily_scheduler_loop()                           # the tick loop
  :507  _register_default_daily_jobs()                    # the built-in roster
```

Current built-in roster (all **daily**, Central; no hourly support today):

| Registered name | Time | Fn | Source |
|---|---|---|---|
| `daily-creation` | 08:00¹ | `generate_daily_creation()` | `services/creations.py` |
| `front-page-morning` | 07:00 | `_run_front_page_job("morning")` | `services/news_engine.py` |
| `front-page-evening` | 18:00 | `_run_front_page_job("evening")` | `services/news_engine.py` |
| `friday-weekly-digest` | 08:00 (Sun only) | `_run_weekly_digest_job()` | `services/news_engine.py` |
| `friday-weekly-editorial` | 19:00 (Fri only) | `_run_weekly_editorial_job()` | `services/news_engine.py` |
| `friday-self-improvement` | 09:00¹ (Sun only) | `_run_self_improvement_job()` | `services/notifications.py` |
| `session-summary` | 23:30 | `_run_session_summary_job()` | `services/notifications.py` |

¹ Hour configurable via `settings.json` (`daily_creation_hour`, `self_improvement_hour`).

> **Gap vs. the Cowork roster Stephen wants migrated:** `friday-heartbeat` (hourly), `daily-afternoon-briefing`, `daily-job-intelligence`, `daily-repo-sync-github`, `daily-news-briefing`. Some have backing functions already (news/briefing/self-improvement); heartbeat, job-intelligence, and repo-sync need either new task functions or a generic "run this agent prompt on a schedule" path (see §A.3).

### 1.2 Task execution

```
services/agent.py
  :1290 TASKS / TASKS_LOCK                # in-memory registry (lost on restart)
  :1420 _task_worker(task_id,name,prompt,description)   # runs _generate_agent to completion
  :1584 Thread(_task_worker).start()      # spawn
  :1594 load_workflow_chain / run_workflow_chain        # ~/.friday/workflows/<slug>.json
core.py:753  process_register/update/remove            # holographic orb registry
routes/tasks.py  /api/tasks, /api/processes
```

A scheduled job is just a Python callable today. To get **orbs + run history + agentic execution + notifications** "for free," scheduled jobs should funnel into the existing task/agent machinery rather than calling raw functions inline.

### 1.3 Model-call & tool layer

```
services/model_router.py
  :54   _call_claude()                    # single-shot Anthropic — DISCARDS resp.usage
  :110  _generate_text()                  # unified single-shot dispatch
services/agent.py
  :73   _generate_agent()                 # unified agentic dispatch
  :3192 _call_claude_agent()              # Anthropic tool loop — DISCARDS resp.usage
  :3450 _oai_agentic_loop()               # OpenAI/Ollama loop — RECORDS cost (:3479)
  :2865 _execute_tool(name,input,...)     # ← THE single tool-execution choke point
  :2673 _governance_check()               # ring 0–3 policy
  :2290 TOOL_RINGS                         # governance taxonomy
model_router.py:77  CostTracker          # already exists; in-memory only
```

`_execute_tool` (`services/agent.py:2865`) is the **one** place every native and MCP tool actually runs. Its current internal order is:

```
handler lookup → confirmation gate → _governance_check → _sandbox_policy → handler(input) → log + PII scrub → return
```

This is exactly where PreToolUse/PostToolUse hooks attach (Part B).

### 1.4 Context / conversation

```
conversation_memory.py:87   ConversationMemory (ChromaDB, ~/.friday/memory/conversations/)
services/model_router.py:494 _get_context_pruner()        # turn-based, embedding relevance
services/model_router.py:521 _get_context_compressor()    # Headroom token compression
core.py  CHAT_HISTORY / CHAT_HISTORY_FILE                 # ~/.friday/chat_history.json
```

There is **turn-based pruning and tool-output compression, but no transcript auto-compaction** (summarize-the-middle). That's Part C.

### 1.5 Settings / routes / UI patterns

- `DEFAULT_SETTINGS` — `core.py:822`; `_load_settings()` `:1087`; `_save_settings()` `:1100`.
- Blueprint pattern — one file per domain in `routes/`, registered in `server.py:59`. Adding `/api/schedules` and `/api/costs` = two new blueprints.
- Frontend is a single built `index.html` from `ui_parts/*` via `build_ui.py`. Settings = collapsible sections keyed in `openSections` useState (`ui_parts/app.html:4478`). Orbs poll `/api/processes` @2s; notifications poll `/api/notifications` @30s; `useModelCatalog()` shows the data-fetch-and-cache hook pattern.

---

# Part A — Internal Task Scheduler

## A.1 Architecture

**Decision: extend the existing custom-threading scheduler. Do NOT add APScheduler.**

Rationale:
- The 60s-tick daemon already works, is timezone-correct, and is `FRIDAY_TESTING`-aware. APScheduler adds a dependency, a second persistence story, and a second mental model for ~zero benefit at this cadence (we never need sub-minute precision).
- Custom loop already coexists with Flask without blocking (daemon thread, jobs spawn their own threads at `notifications.py:351`).

We generalize the loop from **daily-only** to a **schedule registry** that supports `interval` (e.g. hourly heartbeat), `daily`, and `weekly` (day-of-week) triggers, all evaluated on the same 60s tick.

```
┌──────────────────────────────────────────────────────────────────┐
│ Flask server (server.py)                                           │
│                                                                    │
│   boot: scheduler.start()  ── daemon thread, 60s tick ───────┐     │
│                                                              │     │
│   ┌────────────────────────────────────────────────────┐    │     │
│   │ services/scheduler.py  (NEW)                        │◄───┘     │
│   │                                                     │          │
│   │  ScheduleStore  ── ~/.friday/schedules.json         │          │
│   │    [{id,name,trigger,spec,task,enabled,...}]        │          │
│   │                                                     │          │
│   │  tick():                                            │          │
│   │    for s in store.due(now_central):                │          │
│   │       run_history.mark_started(s)                   │          │
│   │       dispatch(s) ──┐                               │          │
│   └─────────────────────┼───────────────────────────────┘          │
│                         │                                          │
│        ┌────────────────▼─────────────────┐                       │
│        │ dispatch():                       │                       │
│        │   builtin fn  ── call directly    │  (news, creation…)    │
│        │   agent_prompt ── spawn_task() ───┼──► services/agent.py  │
│        └───────────────────────────────────┘    _task_worker       │
│                         │                            │             │
│                  process orb (core.py)        notification push    │
│                         │                            │             │
│                         ▼                            ▼             │
│                   /api/processes              /api/notifications    │
│                         │                            │             │
└─────────────────────────┼────────────────────────────┼────────────┘
                          ▼                            ▼
                    React orbs (2s poll)        Bell + toast (30s poll)
```

**Concurrency / non-blocking:** unchanged from today — the tick thread never runs a job inline; each due job is dispatched to its own thread (built-in fn) or to the existing `_task_worker` thread pool (agent prompt). A slow job cannot delay the tick or the Flask request path.

## A.2 Task definitions — JSON registry, edited from the UI

**Storage format: JSON** (`~/.friday/schedules.json`). Not YAML (no parser dep, and we already standardize on JSON for all `~/.friday` state), not Python classes (users must edit from the UI without a redeploy).

Schedule record schema:

```jsonc
{
  "id": "sch_heartbeat",            // stable id; built-ins use fixed ids
  "name": "Hourly heartbeat",
  "trigger": "interval",            // "interval" | "daily" | "weekly"
  "spec": {                         // shape depends on trigger
    "every_minutes": 60             //   interval: every N minutes
    // daily:  { "hour": 9, "minute": 0 }
    // weekly: { "weekday": 6, "hour": 9, "minute": 0 }   // 0=Mon … 6=Sun
  },
  "task": {
    "kind": "builtin",              // "builtin" | "agent_prompt"
    "ref": "friday_heartbeat"       //   builtin: registry key
    // agent_prompt: { "prompt": "...", "model": "subagent", "workspace": "research" }
  },
  "enabled": true,
  "notify": "on_complete",          // "on_complete" | "on_change" | "silent"
  "retry": { "max": 2, "backoff_seconds": 300 },
  "timeout_seconds": 1800,
  "source": "builtin",              // "builtin" | "user"
  "created": 1750000000.0,
  "updated": 1750000000.0
}
```

Two task kinds:

- **`builtin`** — `ref` looks up a registered Python callable in a `BUILTIN_TASKS` dict (e.g. `generate_daily_creation`, `_run_self_improvement_job`). These ship with Friday; the user can re-time/enable/disable but not delete.
- **`agent_prompt`** — `prompt` is fed to `_generate_agent` via a spawned task. This is the **escape hatch** that lets a user (or Friday herself) add *any* recurring agentic job from the UI without code — "every morning at 7, scan these job boards and update my top-25 list" becomes a schedule, not a code change. This is also how `friday-heartbeat`, `daily-job-intelligence`, and `daily-repo-sync-github` are implemented if we don't want dedicated functions (see §A.3).

**Built-in registration API** (generalizes the current `register_daily_job`):

```python
# services/scheduler.py
def register_builtin_task(ref, fn, *, label, default_trigger, default_spec,
                          notify="on_complete", weekday_only=None):
    """Register a built-in task fn under `ref`. The scheduler seeds a default
    schedule for it on first run; thereafter the user's edits in schedules.json win."""

def register_schedule(record):      # user-defined, from the API
def update_schedule(id, patch):
def delete_schedule(id):            # source=="user" only
def list_schedules():
```

`register_daily_job()` is kept as a thin back-compat shim that calls `register_builtin_task(..., default_trigger="daily")` so nothing else in the tree breaks during migration.

## A.3 The seven tasks to migrate

For each: trigger, what it does, tools/APIs it calls, and how it reports back. Tasks marked **(exists)** already have a backing function; **(new)** needs one (or use `agent_prompt`).

| Task | Trigger | Backing | What it does → how it reports |
|---|---|---|---|
| **friday-heartbeat** | interval 60m | **new** (`agent_prompt` recommended) | Check Gmail/Calendar for new items + completed background tasks; surface anything actionable. Calls calendar_engine + gmail tools (Ring-2). Reports: notification *only on change* (`notify:"on_change"`) so it's silent when nothing's new. Orb is low-priority/monitoring category. |
| **friday-daily-creation** | daily 08:00 | **exists** `generate_daily_creation()` (`services/creations.py`) | Generates an art/code/writing piece → `~/.friday/creations/<date>.json` + materialized Desktop file. Reports: notification with "Open Creation" deep-link action (already implemented). |
| **daily-afternoon-briefing** | daily 16:00 | **exists** `_run_front_page_job` family / briefing generator | HTML news briefing → wiki `meta/daily-briefing-*.md`. Reports: derived "briefing ready" notification (existing pattern in `services/notifications.py`). |
| **daily-job-intelligence** | daily 07:30 | **new** (`agent_prompt`) | Scan configured job boards, maintain a top-25 list in the career/pipeline vault file. Tools: `search_web`/`browse_web` (Ring-2). Reports: notification summarizing deltas (new roles, dropped roles). |
| **daily-repo-sync-github** | daily 06:00 | **new** (`builtin`, small) | `git pull` across the FutureSpeakAI repos (configurable list). Tools: `run_command` (Ring-2). Reports: notification on conflict/failure only; silent on clean pull. |
| **friday-self-improvement** | weekly Sun 09:00 | **exists** `_run_self_improvement_job()` | Epistemic calibration + sycophancy/personality review → `~/.friday/self_improvement/`. Reports: notification (existing). |
| **daily-news-briefing** | daily 07:00 | **exists** `_run_front_page_job("morning")` | Morning news edition. Reports: front-page notification (existing). |

**Recommendation:** implement `friday-heartbeat`, `daily-job-intelligence` as `agent_prompt` schedules (no new Python, maximally user-tweakable), and `daily-repo-sync-github` as a tiny built-in (`services/repo_sync.py`) because it's deterministic and shouldn't burn agent tokens. Everything else maps to existing functions.

## A.4 Execution model

A due schedule is **dispatched**, never run inline:

```python
def dispatch(sch):
    run = run_history.start(sch)               # status=running, ts, orb id
    def _body():
        try:
            if sch["task"]["kind"] == "builtin":
                fn = BUILTIN_TASKS[sch["task"]["ref"]]
                result = fn()                  # may itself spawn agent work
            else:  # agent_prompt
                # Reuse the task machinery: own conversation context, orbs,
                # verification, context-log — all for free.
                tid = spawn_task(
                    name=sch["name"],
                    prompt=sch["task"]["prompt"],
                    description=f"scheduled:{sch['id']}",
                    model=sch["task"].get("model"),
                    workspace=sch["task"].get("workspace"),
                )
                result = await_task(tid, timeout=sch["timeout_seconds"])
            run_history.finish(run, "complete", result)
            _notify_for(sch, result)
        except Exception as e:
            _maybe_retry(sch, run, e)
    threading.Thread(target=_body, daemon=True).start()
```

- **Own conversation context:** `agent_prompt` tasks go through `_task_worker` → `_generate_agent`, which builds a *fresh* system prompt + vault context per run (`_build_context_prompt`). Scheduled runs are **not** glued onto the user's live chat history — they get a clean, purpose-built context. This is the correct isolation boundary.
- **Errors & retries:** `retry.max` attempts with `backoff_seconds`. A failed run is recorded in history with the traceback tail; after exhausting retries it emits a high-priority failure notification. The 60s tick never retries inline — a retry re-enqueues with a `not_before` timestamp.
- **Idempotency:** keep today's mechanism for daily/weekly (mark-before-run, date-keyed). For `interval`, store `last_run_ts` per schedule and fire when `now - last_run_ts >= every_minutes*60` (so a server restart doesn't double-fire, and a missed window catches up once, not N times).

## A.5 UI surface — Settings → "Scheduled Tasks"

A new collapsible Settings section (follow the exact pattern at `ui_parts/app.html:4478` + section-render block). It shows the schedule registry as editable rows:

```
┌─ Scheduled Tasks ──────────────────────────────────── ▾ ─┐
│  ● Hourly heartbeat        every 60 min     [on ▣]  ⋯    │
│      last run 14:02 ✓ · next 15:02                       │
│  ● Daily creation          daily 08:00      [on ▣]  ⋯    │
│      last run today 08:00 ✓ "Generated: "Tide Tables""   │
│  ● Job intelligence        daily 07:30      [on ▣]  ⋯    │
│      last run 07:31 ✓ · 3 new roles                      │
│  ● Repo sync               daily 06:00      [off □] ⋯    │
│      last run failed ✕ — merge conflict in sage-core     │
│  …                                                        │
│  [ + Add scheduled task ]                                 │
└──────────────────────────────────────────────────────────┘
```

- Row = name, human-readable schedule, enable toggle, last-run status chip, next-run time, and a `⋯` menu (Edit / Run now / View history / Delete[user-only]).
- **Add task** opens a small form: name, trigger (interval/daily/weekly + spec pickers), task kind (built-in dropdown *or* free-text agent prompt + model picker reusing `useModelCatalog`), notify mode.
- Data fetched from `GET /api/schedules` on Settings open + a light 5s poll while open (matches existing "refresh on open" pattern). Edits `POST /api/schedules/<id>`.
- **"Run now"** posts `/api/schedules/<id>/run` → immediate dispatch, so the user can test a schedule without waiting for its window.

## A.6 Notifications

Reuse the existing notification engine (`_notif_engine.push(...)`, `routes/notifications.py`) and orb registry — no new channel.

- **While running:** scheduled job registers a process orb (`core.process_register`, category `monitoring`, low-key so it doesn't animate aggressively — consistent with the "animate only when speaking" rule). Frontend already renders it via `/api/processes` @2s.
- **On complete:** `notify` mode decides:
  - `on_complete` → always push a notification (with deep-link `target` where relevant, e.g. creation/briefing).
  - `on_change` → push only if the run produced a delta (heartbeat with new mail, job-intel with new roles). The task fn returns a `changed` flag.
  - `silent` → orb only, no bell.
- **On failure (after retries):** always push `priority:"high"` with the error tail and a "View history" action.

So: **orb for in-flight, toast/bell for terminal state** — both, gated by `notify`.

## A.7 Persistence

Two files under `~/.friday/`:

- **`schedules.json`** — the registry (user-editable schedules + materialized built-in defaults). Survives restart. This is the source of truth the UI reads/writes.
- **`schedule_runs.jsonl`** — append-only run history: `{id, run_id, started, ended, status, summary, error}`. Capped/rotated (keep last ~500 runs or 90 days). Powers the "last run / View history" UI and feeds Part D cost attribution (a run_id correlates to cost rows).

The legacy `daily_scheduler_state.json` (date-keyed idempotency) stays for daily/weekly triggers; `last_run_ts` for interval triggers lives in `schedules.json` per record. Settings keys (`daily_creation_hour`, etc.) are migrated into `schedules.json` on first boot of the new code (see A.8) and then deprecated.

## A.8 First-run defaults & migration

```
on scheduler.start():
  if schedules.json missing:
     seed defaults from BUILTIN_TASKS registry:
        heartbeat        interval 60m   on_change
        daily-creation   daily 08:00    on_complete   (honor legacy daily_creation_hour if set)
        news-morning     daily 07:00    on_complete
        afternoon-brief  daily 16:00    on_complete
        job-intel        daily 07:30    on_change
        repo-sync        daily 06:00    on_change
        self-improve     weekly Sun 09  on_complete   (honor legacy self_improvement_hour)
        session-summary  daily 23:30    silent
     write schedules.json
  else:
     load schedules.json
     reconcile: add any NEW built-in refs not present (so upgrades pick up new tasks)
               never overwrite a user-edited record
```

- **New users** get the sensible roster above out of the box.
- **Stephen (existing user):** seed honors his current `*_hour` settings, and his Cowork-defined tasks become redundant — he disables/deletes them in Cowork once parity is confirmed. The migration is additive and non-destructive; if `schedules.json` already exists we only *add* missing built-ins.

---

# Part B — PreToolUse / PostToolUse Lifecycle Hooks

## B.1 Architecture

A hook registry sits **inside `_execute_tool` (`services/agent.py:2865`)** — the single choke point through which every native and MCP tool already passes. Today's hard-coded gate sequence (confirmation → governance → sandbox) is refactored so those become *built-in PreToolUse hooks*, and a clean registration point is exposed for new ones.

```
_execute_tool(name, input, …):
    ctx = HookContext(name, input, session_ctx, pii_lookup)

    ── PreToolUse chain (ordered by priority) ──
    for hook in PRE_HOOKS:
        verdict = hook(ctx)            # ALLOW | MODIFY(new_input) | DENY(reason)
        if DENY:   return f"[{hook.name} DENY] {reason}"
        if MODIFY: ctx.input = new_input

    result = handler(ctx.input)        # the existing actual execution (line 2906)

    ── PostToolUse chain ──
    for hook in POST_HOOKS:
        result = hook(ctx, result)     # transform / log / trigger follow-up

    return result
```

**Hook signature & registration:**

```python
# services/hooks.py  (NEW)

@dataclass
class HookContext:
    tool_name: str
    input: dict
    session_ctx: dict | None
    pii_lookup: dict | None
    meta: dict            # scratch space shared pre→post (e.g. start time)

class PreVerdict:   # ALLOW / MODIFY(new_input) / DENY(reason)
    ...

def register_pre_hook(fn, *, name, priority=100, tools=None):
    """tools=None → global; tools={'write_file',...} → scoped to those tools."""
def register_post_hook(fn, *, name, priority=100, tools=None):
```

- **Both global and per-tool.** `tools=None` registers a global hook; passing a set scopes it. Internally one ordered list, filtered by `tool_name` at call time. Lower `priority` runs first; DENY short-circuits.
- **Pre hooks** return a verdict (allow / modify-input / deny). **Post hooks** receive `(ctx, result)` and return a (possibly transformed) result; they may also fire-and-forget side effects (logging, spawning a follow-up task).
- Hooks must be cheap and exception-isolated: a hook that throws is logged and treated as ALLOW (fail-open for pre) / passthrough (post), so a buggy hook can never brick tool execution. *(Exception: the governance/vault built-ins are marked `critical=True` and fail-closed.)*

## B.2 Built-in hooks (ship by default)

Refactor existing logic into named hooks + add the new governance ones:

| Hook | Phase | Priority | Behavior |
|---|---|---|---|
| `confirmation_gate` | pre | 10 | Existing ask-first gate (`TOOL_REQUIRES_CONFIRMATION`). |
| `governance_rings` | pre | 20 | Existing `_governance_check` (Ring 0–3). `critical`, fail-closed. |
| `vault_zt` | pre | 25 | Existing vault zero-trust check (currently inline in the loops). |
| `sandbox_policy` | pre | 30 | Existing `_sandbox_policy`. |
| `rate_limiter` | pre | 40 | **new** — token-bucket per (tool, ring). Caps e.g. Ring-2 network calls/min to stop a runaway loop from hammering an API or spending. |
| `audit_log` | post | 90 | Replaces the inline context-log write at `:2918`; structured, signed entry to `decision-bom.jsonl`. |
| `pii_scrub` | post | 95 | Existing `_scrub_pii` / `_pii_redact` at `:2928`. |
| `cost_attribution` | post | 80 | **new (Part D seam)** — stamps the current run/workspace onto cost rows so per-workspace breakdowns work. |

This refactor is **behavior-preserving** — same checks, same order — but now they're a list you can reorder, disable per-settings, or insert between. PII detection becomes a first-class post-hook rather than buried code.

## B.3 User-extensible hooks

Yes — via the existing skills/recipe system. A skill can ship a `hooks.py` declaring `register_pre_hook` / `register_post_hook`; the skill loader imports it under the same governance the MCP/skill system already enforces. Constraints:

- User hooks are **always non-critical / fail-open**, run *after* all built-in critical hooks (priority ≥ 100), and **cannot weaken** a built-in DENY (the chain already short-circuited). They can only add *additional* DENY/MODIFY or post-processing — i.e. they tighten, never loosen, governance.
- Surfaced in Settings → a read-only "Active Hooks" list (name, phase, scope, source) so the user can see what's intercepting tools. v1 is view + enable/disable; authoring stays code/skill-level.

> **Scope note:** v1 implements hooks for the *internal* tool loop (`_execute_tool`). It is **not** the Claude Code `settings.json` shell-hook system — same concept, Friday-native surface. Worth stating in the doc so reviewers don't expect CLI-hook parity.

---

# Part C — Auto-Compaction

## C.1 Trigger

Compaction triggers on **estimated token count of the assembled transcript**, not turn count (turn count is a poor proxy — one long tool dump can blow the budget). Reuse the Headroom estimator already in the tree (`context_compressor.py`, `_CHARS_PER_TOKEN=4`) for a cheap estimate; upgrade to the Anthropic token-counting endpoint (see Part D §16) when available for accuracy.

- **Threshold:** compact when assembled context exceeds **~70% of the model's context window** (configurable `compaction.trigger_ratio`, default 0.70). For Opus's large window this rarely fires in chat but *will* fire in long voice sessions and long-running `agent_prompt` tasks — which is the point.
- Evaluated where the message list is built for a model call (`_build_context_prompt` / the agent loops), before dispatch.

## C.2 What gets compacted

**Preserve the head and the tail; summarize the middle.**

```
[ system + task framing ]      ← NEVER compacted (the original task/intent)
[ turn 1 .. turn k    ]        ← preserve first k (default k=2: the opening exchange)
[ turn k+1 .. turn n-m ]       ← COMPACT → one synthetic "[Earlier in this session: …]" note
[ turn n-m+1 .. turn n ]       ← preserve last m (default m=6 recent turns verbatim)
```

- Defaults: keep first **2** and last **6** turns; compact everything between into a single summary message inserted in place. Configurable (`compaction.keep_head`, `compaction.keep_tail`).
- The synthetic note is tagged so it's idempotent — a second compaction summarizes *(previous summary + newly-aged turns)*, never re-summarizes already-summarized content.
- Tool-result-heavy middle turns are the highest-value targets (biggest token sink, lowest recency value).

## C.3 How the summary is generated

A **dedicated cheap summarizer**, not the conversation's own (possibly Opus) model — summarization is a bounded, non-creative task and shouldn't cost orchestrator rates.

- Use `subagent_model` if cloud, or the local model when routing is local-preferred (`_generate_text`, no tools, low temperature, tight `max_tokens`). This keeps compaction nearly free and offline-capable.
- Prompt: "Summarize the following exchange into a compact factual note preserving decisions, open questions, entities, and any state the assistant must remember. ≤200 words." Output becomes the `[Earlier in this session: …]` message.
- The compaction call itself is metered (Part D) and tagged `kind:"compaction"` so it's visible in cost breakdowns.

## C.4 Full history retention

**Yes — compaction is lossy only for the *live context window*, never for the record.**

- The **full transcript stays in `CHAT_HISTORY` / `chat_history.json`** (compaction operates on a *copy* assembled for the model call, not on the stored history).
- Every turn is **already embedded into ChromaDB** (`conversation_memory.py`) on its own thread, independent of compaction. So even after the middle is summarized in-context, the original turns remain semantically retrievable — and `_build_context_prompt` already pulls relevant past turns back in via RAG when they're germane. This gives us "compact but recoverable": the model sees a short context, but anything important can be re-surfaced on demand.

This layering (verbatim store + ChromaDB recall + in-context compaction) means we lose nothing durable.

---

# Part D — Cost Metering

## D.1 What's tracked per call

Extend the existing `CostTracker` row (`model_router.py:82`) from `{provider, model, prompt_tokens, completion_tokens, total_tokens, cost, ts}` to:

```jsonc
{
  "ts": 1750000000.0,
  "provider": "anthropic",
  "model": "claude-opus-4-8",
  "input_tokens": 4210,
  "output_tokens": 880,
  "cost_usd": 0.3818,           // input/output priced separately (see D.1a)
  "duration_ms": 5120,          // NEW: wall-clock latency of the call
  "workspace": "research",       // NEW: from session_ctx / task context
  "kind": "chat",                // NEW: chat | task | scheduled | compaction | briefing | voice
  "schedule_id": "sch_jobintel", // NEW: set when kind==scheduled (cost per scheduled job)
  "run_id": "..."                // NEW: correlates to schedule_runs.jsonl
}
```

**D.1a — fix the pricing model.** Today `CostTracker.record` charges one blended rate × total tokens. Real pricing is **input ≠ output** (output is ~5× input). Replace `CLOUD_COST_PER_1K` with per-direction rates sourced from `provider_registry` `cost_per_1k`/`model_meta` (single source of truth, already exists), e.g.:

```python
PRICING = {  # USD per 1K, {in, out}
  "claude-opus-4-8":  {"in": 0.015, "out": 0.075},
  "claude-sonnet-4-6":{"in": 0.003, "out": 0.015},
  ...
}
cost = in_tok/1000*p["in"] + out_tok/1000*p["out"]
```

**D.1b — fix the capture gaps.** The Anthropic paths discard `resp.usage`. Add capture at:
- `_call_claude_agent` (`services/agent.py:3334`, after `client.messages.create`) → `resp.usage.input_tokens/output_tokens`.
- `_call_claude` (`model_router.py:81`) → same.
- Verify `_oai_agentic_loop` (`:3479`) maps `prompt_tokens→input_tokens`, `completion_tokens→output_tokens` under the new schema.

Best done via the **`cost_attribution` post-hook (Part B)** for tool-loop calls, plus a small `meter(provider, model, usage, duration, ctx)` helper called at each `messages.create`/`chat_completion` site for the non-tool single-shot calls. Hooks give us workspace/kind/run_id for free from `HookContext`.

## D.2 Storage

**SQLite** (`~/.friday/costs.db`), with the in-memory `CostTracker` ring buffer kept as a hot cache.

Why SQLite over JSONL: cost queries are *aggregations over time ranges, grouped by provider/workspace/model* — exactly what SQL indexes do well and what JSONL forces a full scan for. One table, three indexes (ts, workspace, provider). stdlib `sqlite3`, no new dependency.

```sql
CREATE TABLE cost_calls (
  id INTEGER PRIMARY KEY, ts REAL, provider TEXT, model TEXT,
  input_tokens INT, output_tokens INT, cost_usd REAL, duration_ms INT,
  workspace TEXT, kind TEXT, schedule_id TEXT, run_id TEXT
);
CREATE INDEX idx_cost_ts ON cost_calls(ts);
CREATE INDEX idx_cost_ws ON cost_calls(workspace);
CREATE INDEX idx_cost_prov ON cost_calls(provider);
```

Writes are buffered and flushed every ~10s (or 50 rows) off the hot path — metering must never add latency to a model call. `FRIDAY_TESTING` uses a temp/in-memory DB.

## D.3 API — `routes/costs.py` (new blueprint)

```
GET  /api/costs/summary?range=today|7d|month|custom&from=&to=
       → { total_usd, total_calls, input_tokens, output_tokens,
           by_provider:{...}, by_workspace:{...}, by_model:{...}, by_kind:{...} }
GET  /api/costs/timeseries?range=month&bucket=day
       → [{date, usd, calls}, …]                  # for the chart
GET  /api/costs/scheduled
       → per-schedule cost (joins schedule_id)    # "what does job-intel cost me?"
GET  /api/costs/budget    /    POST /api/costs/budget
       → read/set daily & monthly thresholds
```

## D.4 UI — Settings → "Cost & Usage"

New collapsible Settings section (same pattern as A.5). Contents:

```
┌─ Cost & Usage ─────────────────────────────────── ▾ ─┐
│  This month         $14.82   ▁▂▃▅▂▁▃  (daily)         │
│  Today              $0.91 · 23 calls                  │
│                                                       │
│  By provider     Anthropic $13.10 · Local $0 · …      │
│  By workspace    Research $6.2 · Studio $4.1 · …      │
│  By model        opus $11.9 · sonnet $2.1 · …         │
│                                                       │
│  Scheduled jobs  job-intel $1.40/mo · briefing $0.80  │
│                                                       │
│  Budget alerts   Daily $5 [▣]   Monthly $50 [▣]       │
│     ⚠ 87% of today's budget used                      │
└───────────────────────────────────────────────────────┘
```

- Monthly sparkline from `/api/costs/timeseries`; breakdown bars from `/api/costs/summary`. Fetch-on-open + the standard pattern. The existing model-savings figures (`CostTracker.stats` "estimated_savings" from local routing) fold in as a "saved vs. all-cloud" line — a nice reinforcement of the local-first story.

## D.5 Budget alerts

- Settings let the user set **daily** and **monthly** USD thresholds (stored in `settings.json` `cost_budget:{daily, monthly}`).
- The cost flush path checks rolling spend after each flush; crossing **80%** emits a `medium` notification, crossing **100%** emits a `high` one (deduped per day/month via `dedupe_key`). Optional hard stop is **out of scope for v1** (alert only — never silently block Friday from working), but the `rate_limiter` pre-hook (Part B) is the natural place to add an opt-in "pause cloud calls when over budget" later.

---

# Phased implementation plan

Dependency graph:

```
        ┌─────────────────────────────────────────┐
        │ Phase 0: Foundations                     │
        │  • services/hooks.py registry            │
        │  • refactor _execute_tool to hook chain  │  (behavior-preserving)
        └───────────────┬─────────────────────────┘
                        │ enables clean seams for A + D
          ┌─────────────┼──────────────┐
          ▼             ▼              ▼
   ┌────────────┐ ┌────────────┐ ┌────────────┐
   │ Phase 1: A │ │ Phase 2: D │ │ Phase 3: C │
   │ Scheduler  │ │ Cost meter │ │ Compaction │
   │ (uses task │ │ (uses post-│ │ (independ- │
   │  machinery)│ │  hook seam)│ │  ent)      │
   └─────┬──────┘ └─────┬──────┘ └────────────┘
         │  D attributes cost per schedule (join)
         └──────────────┘
```

| Phase | Scope | Deliverables | Effort | Depends on |
|---|---|---|---|---|
| **0 — Hook foundation (Part B)** | `services/hooks.py`; refactor `_execute_tool` so confirmation/governance/vault/sandbox/audit/pii become registered built-in hooks (no behavior change); add `rate_limiter`. Settings "Active Hooks" read-only list. Tests assert identical gate behavior pre/post refactor. | Hook registry + 8 built-in hooks + UI list | **2–3 d** | — |
| **1 — Internal Scheduler (Part A)** | `services/scheduler.py` (generalize the daily loop to interval/daily/weekly); `schedules.json` + `schedule_runs.jsonl`; `register_builtin_task` + back-compat `register_daily_job` shim; migrate the 7 tasks (3 new: heartbeat/job-intel as `agent_prompt`, repo-sync built-in); `routes/schedules.py`; Settings "Scheduled Tasks" panel + Add/Edit/Run-now/History; first-run seed + migration. | Full scheduler, UI, parity with Cowork roster | **6–8 d** | Phase 0 (cost attribution is nicer with hooks, but A can ship without D) |
| **2 — Cost Metering (Part D)** | Per-direction `PRICING`; capture `usage` at all 3 model-call sites; `cost_attribution` post-hook; `costs.db` (SQLite) + buffered writer; `routes/costs.py`; Settings "Cost & Usage" dashboard; budget alerts. | Every call costed + dashboard + alerts | **3–4 d** | Phase 0 (post-hook seam); richer with Phase 1 (per-schedule cost) |
| **3 — Auto-Compaction (Part C)** | Token-estimate trigger in context assembly; head/tail-preserve middle-summarize; dedicated cheap summarizer; idempotent re-compaction; verify ChromaDB recall path intact. | Long sessions stop overflowing | **2–3 d** | — (independent; ship anytime after 0) |

**Recommended sequence: 0 → 1 → 2 → 3.** Phase 0 is the keystone (small, de-risks A and D). Phase 1 is the headline deliverable (kills the Cowork dependency). Phase 2 rides Phase 1 for per-schedule cost. Phase 3 is independent and can be parallelized or deferred.

**Parity gate before deleting Cowork tasks:** run Friday's internal scheduler alongside Cowork for ~1 week; confirm each migrated task fires at the right time, reports correctly, and shows in run history. Then disable the Cowork mirrors. (This is the only "outward-facing" change — retiring the external tasks — so do it explicitly after observed parity, not on faith.)

---

# Testing & rollout notes

- **Offline test suite (~1,870 tests, `FRIDAY_TESTING=1`)** must stay green. New daemons (scheduler) must be inert under `FRIDAY_TESTING` like the existing ones (`server.py:129` pattern). New storage (`costs.db`, `schedules.json`) must honor the temp-home redirect in `conftest`.
- **New unit tests:** scheduler trigger math (interval/daily/weekly + DST boundary in Central), idempotency across restart, retry/backoff; hook chain ordering + DENY short-circuit + fail-open isolation; cost capture on all 3 provider paths + per-direction pricing; compaction idempotency + head/tail preservation.
- **No new heavy deps:** scheduler = stdlib threading; costs = stdlib `sqlite3`; hooks = pure Python; compaction reuses existing summarizer + Headroom estimator. (APScheduler explicitly rejected — see §A.1.)
- **CRLF caution:** `server.py` and other touched files have mixed line endings; verify diffs with `git diff -w` and preserve `\r\n` (per repo convention) to avoid phantom churn. Concurrent agents may touch the same files — verify committed state via `git show HEAD:`.

---

# Open questions for Stephen

1. **Repo-sync scope:** which exact FutureSpeakAI repos, and where do they live on disk? (Drives the `daily-repo-sync-github` built-in config.)
2. **Heartbeat reach:** should the hourly heartbeat be allowed to *act* (e.g. auto-draft a reply, create a todo) or strictly *observe-and-notify*? Recommend observe-only for v1.
3. **Budget hard-stop:** alert-only for v1 (recommended), or do you want an opt-in "pause cloud calls when over monthly budget"?
4. **Job-intelligence sources:** which job boards / search queries seed `daily-job-intelligence`, and where does the top-25 list live (vault file? wiki page)?
5. **Cowork cutover:** keep the Cowork tasks as a fallback indefinitely, or hard-delete after the 1-week parity window?
