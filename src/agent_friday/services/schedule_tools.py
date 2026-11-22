"""`schedule_job`: the model's way to put a saved job on a recurring timetable.

One tool, the same in chat and voice (voice borrows it verbatim), over the one
scheduler (services/scheduler.py) the Workflows screen shows:

  list    every schedule with its model, status and next run
  create  a recurring schedule for a saved workflow, or for a prompt
  pause / resume / delete  one schedule, by id
  update   change one schedule's timing, enabled state or prompt (a built-in's too)
  restore_default / revert  put a built-in job back as shipped, or step it back one edit

Honesty: `create` answers only after the schedule is read back from the store,
and its result carries the schedule id and the next run. A reply that says
something "is scheduled" is checked against that receipt
(services/completion_receipts.py, the "scheduled" claim): without a successful
create from this tool (or a calendar/workflow tool that wrote a timetable) the
claim is flagged.

Governance: listing, pausing and deleting change nothing outside, and are
internal. Creating (or resuming) a schedule whose job will SEND, post, buy or
change things later is outward (`classify`, read by governance/action_gate.py):
the owner is asked before the timetable exists, because nobody will be
watching when it fires. The sends themselves are still gated one by one when
the job runs.
"""
from __future__ import annotations

import json
from datetime import datetime

ACTIONS = ("list", "get", "create", "pause", "resume", "delete", "update", "restore_default", "revert")
TRIGGERS = ("daily", "weekly", "monthly", "interval", "on_idle")


def _when_text(rec):
    from agent_friday.services.workflow_overview import describe_when
    return describe_when({"trigger": rec.get("trigger"), "spec": rec.get("spec") or {}})


def _local(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%A %d %B %Y, %H:%M")
    except Exception:
        return None


def _row(rec) -> dict:
    return {"schedule_id": rec.get("id"), "name": rec.get("name"),
            "when": _when_text(rec), "enabled": bool(rec.get("enabled", True)),
            "status": rec.get("status"), "status_reason": rec.get("status_reason") or "",
            "model": rec.get("model"), "model_pinned": bool(rec.get("model_pinned")),
            "next_run": rec.get("next_run"), "next_run_local": _local(rec.get("next_run")),
            "missed": rec.get("missed_text") or "",
            "builtin": rec.get("source") == "builtin",
            "job": (rec.get("task") or {}).get("ref") or (rec.get("task") or {}).get("kind")}


#: A list answer is never cut: it carries whole rows up to this many characters, then says how many it left out and the
#: offset of the next page (guarded by tests/unit/test_schedule_get_and_list.py).
MAX_LIST_CHARS = 12000
MAX_ROW_REASON = 90


def _short_row(rec) -> dict:
    """One schedule for a list: short, no prompt and no workflow body (``get`` returns the whole definition)."""
    task = rec.get("task") or {}
    return {"schedule_id": rec.get("id"), "name": rec.get("name"), "when": _when_text(rec),
            "enabled": bool(rec.get("enabled", True)), "status": rec.get("status"),
            "status_reason": str(rec.get("status_reason") or "")[:MAX_ROW_REASON],
            "next_run_local": _local(rec.get("next_run")), "builtin": rec.get("source") == "builtin",
            "job": task.get("ref") or task.get("kind")}


def _list(data) -> dict:
    """The schedules, optionally filtered by ``name_contains`` and ``builtin`` (true: Friday's own; false: the owner's),
    paged by ``offset``. Whole rows only: when the next row would pass MAX_LIST_CHARS the list stops there and says how
    many were left out and where to go on."""
    from agent_friday.services import scheduler as _sched
    needle = str(data.get("name_contains") or "").strip().lower()
    builtin = data.get("builtin")
    rows = []
    for rec in _sched.list_schedules():
        if needle and needle not in str(rec.get("name") or "").lower():
            continue
        if isinstance(builtin, bool) and (rec.get("source") == "builtin") != builtin:
            continue
        rows.append(_short_row(rec))
    try:
        offset = max(0, int(data.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    kept, used = [], 2
    for row in rows[offset:]:
        size = len(json.dumps(row, ensure_ascii=False, default=str)) + 2
        if kept and used + size > MAX_LIST_CHARS:
            break
        kept.append(row)
        used += size
    out = {"schedules": kept, "count": len(rows), "returned": len(kept), "offset": offset}
    left = len(rows) - offset - len(kept)
    if left > 0:
        out["omitted"] = left
        out["next_offset"] = offset + len(kept)
        out["note"] = ("%d more schedule(s) were left out so no entry is cut; ask again with offset=%d, or narrow the "
                       "list with name_contains or builtin." % (left, offset + len(kept)))
    return out


def _full(rec) -> dict:
    """The whole definition of one schedule, enough to inspect, copy or recreate it without losing its instructions: the
    prompt or the workflow slug, the timing, the model and whether it is pinned, the missed-run policy, and for a built-in
    the version history of the owner's edits (services/builtin_overrides)."""
    from agent_friday.services import builtin_overrides as _bo
    from agent_friday.services import scheduler as _sched
    task = dict(rec.get("task") or {})
    out = _row(rec)
    out.update({
        "kind": task.get("kind"), "prompt": task.get("prompt"),
        "workflow": task.get("ref") if task.get("kind") == "workflow" else None,
        "task": task, "trigger": rec.get("trigger"), "spec": rec.get("spec") or {},
        "notify": rec.get("notify"), "timeout_seconds": rec.get("timeout_seconds"), "retry": rec.get("retry"),
        "missed_policy": rec.get("missed") if rec.get("missed") else _sched._missed_policy(rec),
        "missed_policy_is_own": bool(rec.get("missed")),
        "created": rec.get("created"), "updated": rec.get("updated"), "source": rec.get("source")})
    if rec.get("source") == "builtin":
        history = _bo.history(rec.get("id"))
        out["versions"] = {"count": len(history), "latest": history[-1]["version"] if history else 0,
                           "has_owner_edits": _bo.has_edits(rec.get("id")),
                           "recent": [{k: h.get(k) for k in ("version", "kind", "by", "at")} for h in history[-5:]],
                           "shipped_default": _sched.builtin_defaults(rec.get("id"))}
    return out


def _listed(sid):
    from agent_friday.services import scheduler as _sched
    return next((r for r in _sched.list_schedules() if r.get("id") == sid), None)


def _job_texts(data) -> list:
    """The words of the job a create would schedule: its prompt, or the saved
    workflow's step prompts."""
    texts = [str(data.get("prompt") or "")]
    slug = str(data.get("workflow") or "").strip()
    if slug:
        try:
            from agent_friday.services import agent as _agent
            chain = _agent.load_workflow_chain(slug) or {}
            texts += [str((s or {}).get("prompt") or "") for s in chain.get("steps") or []]
        except Exception:
            pass
    return [t for t in texts if t.strip()]


def _record_texts(rec) -> list:
    task = (rec or {}).get("task") or {}
    if task.get("kind") == "workflow":
        return _job_texts({"workflow": task.get("ref")})
    return [str(task.get("prompt") or "")]


def sends_later(texts) -> list:
    """Plain names for the outward things these job words will do when the job
    runs (send email, post online, ...), or []."""
    from agent_friday.services.workflow_overview import asks_first
    return asks_first(texts)


def classify(args) -> tuple:
    """(class, why) for governance/action_gate. Never raises."""
    from agent_friday.governance.action_gate import INTERNAL, OUTWARD
    a = args or {}
    action = str(a.get("action") or "").strip().lower()
    try:
        if action == "create":
            acts = sends_later(_job_texts(a))
        elif action == "resume":
            from agent_friday.services import scheduler as _sched
            acts = sends_later(_record_texts(_sched.get_schedule(str(a.get("schedule_id") or ""))))
        elif action == "update":
            from agent_friday.services import scheduler as _sched
            target = _sched.get_schedule(str(a.get("schedule_id") or "")) or {}
            changes_text = a.get("prompt") is not None or a.get("task") is not None
            if changes_text and target.get("source") == "builtin":
                return OUTWARD, ("it changes what a built-in job does (its prompt): that is an edit the owner makes, "
                                 "so the owner is asked first")
            acts = sends_later([str(a.get("prompt") or "")]) if changes_text else []
        else:
            return INTERNAL, "it lists, pauses or removes a timetable; nothing leaves the computer"
    except Exception as e:
        return OUTWARD, "the job could not be checked for what it sends (%s)" % e
    if acts:
        return OUTWARD, ("it sets up a job that will %s later, on its own, with nobody watching"
                         % " and ".join(acts))
    return INTERNAL, "it sets up a timetable for a job that sends nothing"


def _caller_may_schedule():
    """A scheduled or background run cannot create or re-arm schedules: only
    the owner's own chat or voice can."""
    try:
        from agent_friday.services import agent as _agent
        ctx = _agent._workflow_caller_context()
    except Exception:
        return True
    return not any(ctx.get(k) for k in ("nested_execution", "is_background_task", "task_id",
                                        "agent_id", "schedule_id"))


_PUBLISHER_SCHEDULE_ID = "sch_content_publisher"


def _caller_is_owner_chat():
    """True only for the owner's own chat or voice turn. Fails closed: when
    the caller cannot be told apart, it is not the owner's."""
    try:
        from agent_friday.services import agent as _agent
        ctx = _agent._workflow_caller_context()
    except Exception:
        return False
    return not any(ctx.get(k) for k in ("nested_execution", "is_background_task", "task_id",
                                        "agent_id", "schedule_id"))


def _announce_publisher_paused():
    """Say that the Content publisher was paused from a chat, and what that
    means for scheduled posts. Best-effort."""
    try:
        from agent_friday.services import publisher as _pub
        _pub._notify("The Content publisher was paused from a chat",
                     "Scheduled posts won't go out while it is paused. Scheduling a post "
                     "switches it back on; to keep it off, switch it off in Workflows.",
                     priority="high", actions=_pub._workflows_action(),
                     dedupe="content-publisher-paused-by-tool")
    except Exception:
        pass


def _create(data) -> dict:
    from agent_friday.services import scheduler as _sched
    from agent_friday.services import workflow_operations as _ops
    from agent_friday.user_errors import UserFacingValueError
    trigger = str(data.get("trigger") or "").strip()
    if trigger not in TRIGGERS:
        raise UserFacingValueError("Choose how often: " + ", ".join(TRIGGERS) + ".")
    when = _ops.validate_timing({"trigger": trigger, "spec": data.get("spec") or {}})
    slug = str(data.get("workflow") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    if bool(slug) == bool(prompt):
        raise UserFacingValueError("Name a saved workflow to schedule, or give the prompt to run; not both.")
    model = str(data.get("model") or "").strip()
    missed = data.get("missed") or _sched.DEFAULT_MISSED
    if missed not in _sched.MISSED_POLICIES:
        raise UserFacingValueError("A missed run is either run_once or skip.")
    if slug:
        from agent_friday.services import agent as _agent
        chain = _agent.load_workflow_chain(slug)
        if not chain:
            raise UserFacingValueError("There is no saved workflow called %r." % slug)
        slug = chain.get("slug") or slug
        for r in _sched.list_schedules():
            t = r.get("task") or {}
            if t.get("kind") == "workflow" and t.get("ref") == slug:
                raise UserFacingValueError(
                    "That workflow already has a schedule (%s, %s). Pause, resume or delete it, or change "
                    "its timing with workflow_action." % (r.get("id"), _when_text(r)))
        task = {"kind": "workflow", "ref": slug}
        name = str(data.get("name") or chain.get("name") or slug).strip()[:120]
        from agent_friday.services.workflow_overview import WORKFLOW_TIMEOUT_S
        timeout = WORKFLOW_TIMEOUT_S
    else:
        task = {"kind": "agent_prompt", "prompt": prompt[:4000]}
        name = str(data.get("name") or "").strip()[:120] or prompt[:60]
        timeout = 1800
    if model:
        task["model"] = model[:200]
    rec = _sched.register_schedule({
        "name": name, "trigger": when["trigger"], "spec": when["spec"], "task": task,
        "notify": data.get("notify") if data.get("notify") in ("on_complete", "on_change", "never") else "on_complete",
        "missed": missed, "timeout_seconds": timeout, "enabled": True})
    # Read it back: the answer describes the schedule that exists, not the one asked for.
    stored = _listed(rec["id"])
    if not stored:
        raise RuntimeError("the schedule was not saved; nothing is scheduled")
    out = dict(_row(stored), created=True)
    if model:
        from agent_friday.services import cloud_intent as _ci
        r = _ci.pinned_job_route(model)
        out["model_now"] = ("available" if r.route in ("local", "cloud")
                            else "not available right now: " + r.reason +
                            ". The run will be held, not moved, unless it is back by then")
    acts = sends_later(_record_texts(stored))
    if acts:
        out["sends_later"] = acts
        out["note"] = ("Each send still needs your approval when the job runs, unless you pre-approve "
                       "it in Settings; without that, the run waits for you and says so.")
    return out


def _change(action, sid, data) -> dict:
    """update / restore_default / revert on one schedule, read back from the store. A built-in's edit is a version the owner
    can step back (services/builtin_overrides); only a built-in has a shipped default to go back to."""
    from agent_friday.services import scheduler as _sched
    from agent_friday.services import workflow_operations as _ops
    rec = _sched.get_schedule(sid) or {}
    if action in ("restore_default", "revert"):
        done = (_sched.restore_builtin_default if action == "restore_default" else _sched.revert_builtin_edit)(sid, by="tool")
        if done is None:
            raise ValueError("Only a built-in job has a shipped default, and revert needs an earlier edit to step back to."
                             if action == "restore_default" else
                             "There is no earlier edit of %s to step back to." % sid)
        return dict(_row(_listed(sid) or {}), **{"restored" if action == "restore_default" else "reverted": True})
    patch = {}
    if data.get("trigger") or data.get("spec") is not None:
        when = _ops.validate_timing({"trigger": data.get("trigger") or rec.get("trigger"),
                                     "spec": data.get("spec") if data.get("spec") is not None else (rec.get("spec") or {})})
        patch.update(trigger=when["trigger"], spec=when["spec"])
    if "enabled" in data:
        patch["enabled"] = bool(data.get("enabled"))
    if data.get("prompt") is not None:
        task = dict(rec.get("task") or {})
        if task.get("kind") != "agent_prompt":
            raise ValueError("This job has no prompt to change.")
        task["prompt"] = str(data.get("prompt"))[:4000]
        patch["task"] = task
    if not patch:
        raise ValueError("Say what to change: the timing (trigger and spec), enabled, or the prompt.")
    _sched.update_schedule(sid, patch, by="tool")
    return dict(_row(_listed(sid) or {}), updated=True)


def schedule_job(inp):
    from agent_friday.services import scheduler as _sched
    data = dict(inp or {})
    action = str(data.get("action") or "").strip().lower()
    if action not in ACTIONS:
        raise ValueError("Choose one of: " + ", ".join(ACTIONS))
    if action == "list":
        return json.dumps(_list(data), ensure_ascii=False, default=str)
    if action in ("create", "resume") and not _caller_may_schedule():
        raise ValueError("A scheduled or background job cannot set up or re-arm schedules; "
                         "ask from the owner's chat or voice conversation.")
    if action in ("update", "restore_default", "revert") and not _caller_may_schedule():
        raise ValueError("A scheduled or background job cannot change schedules; "
                         "ask from the owner's chat or voice conversation.")
    if action == "create":
        return json.dumps(_create(data), ensure_ascii=False, default=str)
    sid = str(data.get("schedule_id") or "").strip()
    if not sid or not _sched.get_schedule(sid):
        raise ValueError("There is no schedule %r. List the schedules for their ids." % sid)
    if action == "get":
        return json.dumps(_full(_listed(sid) or _sched.get_schedule(sid)), ensure_ascii=False, default=str)
    if action in ("update", "restore_default", "revert"):
        return json.dumps(_change(action, sid, data), ensure_ascii=False, default=str)
    if action == "delete":
        if not _sched.delete_schedule(sid):
            raise ValueError("Friday's built-in routines can be paused, not deleted.")
        return json.dumps({"deleted": True, "schedule_id": sid,
                           "still_exists": _sched.get_schedule(sid) is not None})
    publisher = sid == _PUBLISHER_SCHEDULE_ID
    if action == "pause" and publisher and not _caller_is_owner_chat():
        # Pausing the job that sends scheduled posts hides from the owner that
        # their posts stopped going out: a visibility-reducing action, so only
        # the owner's own conversation may ask for it.
        raise ValueError("A scheduled or background job cannot pause the Content publisher; "
                         "the owner switches it in Workflows.")
    # A model's tool call, even in the owner's chat: not the owner's own off
    # (scheduler.builtin_switched_off_by_owner).
    _sched.update_schedule(sid, {"enabled": action == "resume"}, by="tool")
    if action == "pause" and publisher:
        _announce_publisher_paused()
    return json.dumps(dict(_row(_listed(sid) or {}), **{"paused" if action == "pause" else "resumed": True}),
                      ensure_ascii=False, default=str)


TOOL_SCHEMAS = [
    {"name": "schedule_job", "description":
     "Put a saved job on a recurring timetable, or list/pause/resume/delete one. create needs a "
     "saved workflow (workflow=<slug>) or a prompt, a trigger and spec; model pins it to one model. "
     "Only say something is scheduled after create returns its schedule_id and next_run.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)},
         "schedule_id": {"type": "string", "description": "For get, pause, resume, delete, update, restore_default, revert."},
         "enabled": {"type": "boolean", "description": "update: switch the job on or off."},
         "name_contains": {"type": "string", "description": "list: only schedules whose name contains this."},
         "builtin": {"type": "boolean", "description": "list: true for Friday's own built-in jobs, false for the owner's."},
         "offset": {"type": "integer", "description": "list: skip this many (the note of a cut-short list gives it)."},
         "workflow": {"type": "string", "description": "Saved workflow slug to schedule."},
         "prompt": {"type": "string", "description": "What to do each time, when not a saved workflow."},
         "name": {"type": "string"},
         "trigger": {"type": "string", "enum": list(TRIGGERS)},
         "spec": {"type": "object", "properties": {
             "hour": {"type": "integer"}, "minute": {"type": "integer"},
             "weekdays": {"type": "array", "items": {"type": "integer"},
                          "description": "Monday=0 ... Sunday=6 (weekly)."},
             "day": {"type": "integer", "description": "Day of month 1-31, or -1 for the last day (monthly)."},
             "every_minutes": {"type": "integer"},
             "idle_minutes": {"type": "integer",
                              "description": "on_idle: run after this many minutes with no input from the owner."},
             "min_gap_minutes": {"type": "integer",
                                 "description": "on_idle: at least this long between runs (default 60)."}}},
         "model": {"type": "string", "description": "Pin every run to this model; held, never moved, if it is unavailable."},
         "missed": {"type": "string", "enum": ["run_once", "skip"],
                    "description": "A run missed while Friday was closed: run once at the next start (default) or skip."}},
         "required": ["action"]}},
]

TOOL_HANDLERS = {"schedule_job": schedule_job}
