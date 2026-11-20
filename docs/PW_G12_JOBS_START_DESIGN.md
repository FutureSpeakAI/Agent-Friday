# PW-G12: `POST /tv/v1/jobs` (start a background job from a device): design note before any code

Status: design only (BURN-DOWN, 2026-11-17). Spec: phone workspaces parity section 12.3 ("Starting a job ... explicitly with
'Run in the background' on the composer: `POST /tv/v1/jobs {prompt, conversation_id?, workspace?}` -> `{job_id}`. `pc_act`.
The orb appears from the stream, not from the phone's own guess").

## What exists (checked in the code)

* The only way a background task starts is `services/agent.py::_spawn_task(name, prompt, ..., conversation_id, principal,
  inherited_policy, ...)`, called from inside an agent turn (the `spawn_task` tool) or by the `rerun` route
  (`routes/tasks.py`, which rebuilds the options of an existing task with `_descendant_options`).
* There is **no desktop route that starts a task from a prompt.** The desktop's own "Run in the background" is the
  owner turn asking Friday to spawn it.
* A task record has no `origin` field. The spec's `origin: "pc|device:<id>"` has no place to live yet.
* `agent.py` is a hot file (claim first). `routes/tasks.py` is not.

## Why no route is built yet

A gateway route that calls `_spawn_task` directly would be a second spawn path with its own idea of tools, model, cloud or
local policy, principal and taint. That is the second dispatcher the build brief forbids, and `_spawn_task` has 25
parameters whose safe defaults for a phone-started job are a decision, not a detail.

## Proposal

1. **Add one desktop route**, `POST /api/tasks/start` in `routes/tasks.py` (login-required, user-only, like `rerun`), that
   takes `{prompt, conversation_id?, workspace?, origin}` and calls `_spawn_task` with exactly what an owner turn would
   give a task the owner asked for: principal `owner`, no tools list (the default set, every one still behind the action
   gate and approval cards), the conversation's cloud/local policy, no model pin. The route records the origin as a
   journal decision (`task_journal.append(task_id, "decision", point="origin", chosen="device:<id>")`, the same way
   `rerun` records `rerun_of`), so no `agent.py` change is needed.
2. **The gateway route** (`pc_act`, owner phone or tablet) validates `{prompt (1 to 4000 characters), conversation_id?
   (id rule), workspace? (id rule)}`, calls that route in process with the PC's token, and answers `{job_id}`. A `409`
   from the desktop (busy, stopped everything, a seat that will not run it) is passed on in the PC's own words.
3. A `device_act` receipt names the job id and the device, never the prompt. The prompt is task data like any other.
4. `GET /tv/v1/jobs` and the journal already show it (#837, #838); `origin` is read from the journal decision for the
   job row (the spec's "origin and receipt fields on a finished job").
5. **Limits the PC enforces, not the phone:** at most N device-started jobs running at once (N decided by the owner,
   default 3); the global stop refuses; a held or locked seat answers plainly.

## Tests (each with a red twin)

Without `pc_act` or from a non-owner device nothing is spawned and the PC asks; a prompt over the cap, empty or not text
is refused before the desktop is asked; the desktop is called with principal `owner` and the device as origin (a spy on
`_spawn_task`); the receipt carries no prompt text; global stop refuses; the (N+1)th concurrent device job is refused in
words; the origin appears on the job row.

## Questions for Dispatch / Stephen

* Default cap N for concurrent device-started jobs (proposal 3)?
* Should a phone-started job be allowed to use cloud models by default, or inherit the conversation's policy only
  (proposal: inherit)?
* Is it acceptable that the task's tool set is the full default (every tool still gated), as an owner turn's spawned
  task has today?
