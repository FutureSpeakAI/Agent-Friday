# Visible jobs

Status: current. Code: `src/agent_friday/services/visible_jobs.py`; guard:
`scripts/check_visible_jobs.py`; tests: `tests/api/test_workspace_jobs_visible.py`,
`tests/api/test_workspace_job_card_ui.py`,
`tests/unit/test_workspace_jobs_visible_guard.py`.

## The rule

Whatever launches model work (a workspace button, a scheduler tick, a
background thread), the person can see it on the desktop: which model or seat
is doing it, by name, local or cloud; its status and steps; a reasoning trace
they can open; and its place in the Local AI queue while it waits. A job a
workspace launches is the same registry entry a chat-launched task is
(`agent.TASKS`), with the same journal, the same trace and the same orb, so it
gets the same task card.

## The door

`visible_job` is a context manager and a decorator:

```python
@news_bp.route("/api/briefing/generate", methods=["POST"])
@visible_job("Daily briefing", kind="briefing", workspace="news")
def generate_briefing(): ...

with visible_job("Wiki notes", kind="wiki_distill", workspace="wiki"):
    ...
```

While the block runs:

- a TASKS record exists (journaled, with a heartbeat), and a process orb under
  the same id, so the tray row and the desktop orb are one entry;
- the model router reports into it: `note_route` (the router's verdict),
  `note_leg` (each provider leg tried), `note_inference` (the model that is
  answering). A fallback renames the model on the card, so the card never
  names a model that did not answer. A primitive's own "Local Inference" /
  "Cloud Inference" orb is not opened a second time (`core.process_register`);
- the first local model call is admitted through the Local AI queue under the
  job's task id (`background_gate.before_local_model_call`), and
  `/api/tasks` adds `queue_position`, `queue_total` and `wait_reason` while it
  waits, with the status reading `queued`;
- the reasoning trace opens under the job (`trace_id`), so the card's
  reasoning button has content;
- "Stop after step" and "Cancel" stop it before its next model call
  (`JobCancelled`); a request already sent is never interrupted.

`eager=True` (default) shows the card from the first instant. `eager=False`
shows it from the first model call, for an endpoint that only sometimes
reaches a model (a cached read), so a read that never reaches one leaves no
card. A `visible_job` inside another is a step of the outer job. Inside a
TASKS worker the task itself is the job. `visible_task(task_id)` adopts a
record some other code created (a draft worker) so it gets the orb and the
model reports. Code that returns an error response instead of raising calls
`visible_jobs.fail_current(exc)`; the job ends `failed`.

A model call that does not go through the router (a speech model, a direct
HTTP call to a seat, a Gemini client) calls `visible_jobs.note_inference(model,
local=...)` right before it, so the card names that model too.

A local call that no job claims (nothing opened a `visible_job` or a queue job
around it, and no person's turn made it) is wrapped by the local work scheduler
(`services/local_scheduler.admit`) as a deferrable `visible_job` of kind
`background_call`, `eager=False`: it waits in the Local AI queue for idle like
any deferrable job, shows one row in the chip and, once a model answers, one
card that names it. Work that is time-bound (a schedule that is due, an episode
someone asked for) says so with `local_scheduler.work(label, cls="background",
deferrable=False)` and is not held for idle. The setting
`local_scheduler_wrap_background` turns the wrapping off.

The task card (`TaskCard` in `index.html` and `ui_parts/app.html`) shows the
model line (`data-testid="task-model"`), the queue line
(`data-testid="task-queue"`) and the reasoning button.

## The guard

`scripts/check_visible_jobs.py` parses `src/` (stdlib `ast`; it imports nothing)
and fails when a launch point reaches a model call outside the door.

- A *model call* is a router primitive (`_generate_text`, `_generate_agent`,
  `_call_claude`, `_call_claude_agent`, `_call_ollama`, `_call_openai`), an
  Anthropic `client.messages.create/stream`, a Gemini `generate_content`, a
  speech model's `.transcribe`, or a function that posts to a
  `/chat/completions` endpoint itself.
- A call is *covered* inside `with visible_job(...)` or in a function decorated
  with it. A function handed to a thread, an executor or a timer is reached
  through an uncovered edge whatever `with` block started it: the work runs
  after the block ends.
- A *launch point* is an exposed function nothing calls: a route handler, a
  thread target, a scheduler job. Each must be covered or allow-listed.
- Calls are resolved by name through imports, `self.`, nested functions and
  distinctively named methods. A call through an arbitrary object is not
  followed, so the guard under-reports there; a dynamic dispatcher is covered by
  decorating the function it dispatches to.

`ALLOW` in the script lists only interactive turns that already have their own
visible surface (the chat turn, the voice surface, a phone call, the setup
wizard, a workspace chat), the task machinery that is itself the registry, and
small infrastructure checks with their own result page (a key probe, the privacy
judge). Each entry says why. A job is wrapped, never allow-listed.

## Declaring a workspace's jobs

Each workspace's Laya manifest (`src/agent_friday/resources/laya_manifests/<id>.json`) lists
the jobs the workspace can launch in `jobs`. An entry names the job's `id`, what starts it
(`trigger`: a button, a schedule, an idle sweep, Laya or voice), the kind of model it needs
(`model_class`), and the `task_kind` and `activity_label` its `visible_job` call gives it,
with the `entry` function the call decorates. A scheduled job also names its `schedule_ref`
(the `register_builtin_task` ref). A launcher that is its own TASKS record (a Crew request)
has `helper: "task"` and is the allow-listed entry in `scripts/check_visible_jobs.py`.

`tests/test_laya_workspace_contracts.py` holds the manifests to the source: every declared job
is a real `visible_job` call with that kind and label, and every `visible_job` kind in the tree
belongs to a manifest (or to `UNOWNED_KINDS` there, with the reason). Add the job to the
manifest in the same change that adds the call.
