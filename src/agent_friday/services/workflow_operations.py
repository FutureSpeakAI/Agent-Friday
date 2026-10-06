"""Shared workflow actions for the desktop, chat and voice.

Definitions remain in the workflow store and timing remains in the scheduler.
This module resolves ownership, validates the user's contract, and provides
the same actions to every surface. It never executes a tool on an agent's
behalf or grants authority to the work it starts.
"""
from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
import tempfile

from agent_friday.paths import safe_name
from agent_friday.user_errors import UserFacingValueError

LOCK = threading.RLock()
CONTRACT_FIELDS = ("project_id", "conversation_id", "inputs", "success_criteria", "output", "notify")
NOTIFY = ("on_complete", "on_change", "never")
OUTPUT_KINDS = ("reply", "artifact", "file", "code")
ACTIVE = ("running", "queued", "queued-for-seat", "waiting", "waiting_approval", "waiting_for_approval")


def atomic_write(target, data):
    """Replace a local record only after all bytes reach its temporary file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _text(value, name, limit, *, optional=True):
    if value is None and optional:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise UserFacingValueError(f"{name} must be text of at most {limit} characters.")
    return value.strip()


def validate_contract(draft, previous=None):
    """Validate every field before either authoritative store is changed."""
    from agent_friday.services import conversations, projects
    prior = previous or {}
    merged = {key: copy.deepcopy(draft.get(key, prior.get(key))) for key in CONTRACT_FIELDS}
    pid = _text(merged["project_id"], "Project", 100) or None
    cid = _text(merged["conversation_id"], "Conversation", 100) or None
    if pid and (not projects.load(pid) or projects.load(pid).get("archived")):
        raise UserFacingValueError("That project is unavailable. Choose an existing project.")
    conv = conversations.load(cid) if cid else None
    if cid and (not conv or conv.get("status") == "archived"):
        raise UserFacingValueError("That conversation is unavailable. Choose an existing chat.")
    if pid and conv and conv.get("project") != pid:
        raise UserFacingValueError("The destination chat must belong to the selected project.")
    if conv and not pid:
        pid = conv.get("project") or None
    inputs = merged["inputs"] or []
    if not isinstance(inputs, list) or len(inputs) > 30:
        raise UserFacingValueError("Sources must be a list of at most 30 references or notes.")
    inputs = [_text(item, "Source", 2000, optional=False) for item in inputs]
    output = merged["output"] or {"kind": "reply"}
    if not isinstance(output, dict) or set(output) - {"kind", "title", "path"}:
        raise UserFacingValueError("Choose a supported result type, with an optional title or path.")
    kind = output.get("kind") or "reply"
    if kind not in OUTPUT_KINDS:
        raise UserFacingValueError("Choose a reply, document, file, or code change as the result.")
    output = {"kind": kind, **{k: _text(output[k], "Result " + k, 2000 if k == "path" else 200)
                               for k in ("title", "path") if output.get(k)}}
    notify = merged["notify"] or "on_complete"
    if notify not in NOTIFY:
        raise UserFacingValueError("Choose notifications for completion, changes, or neither.")
    return {"project_id": pid, "conversation_id": cid, "inputs": [i for i in inputs if i],
            "success_criteria": _text(merged["success_criteria"], "Completion criteria", 4000),
            "output": output, "notify": notify}


def validate_run_owner(record):
    """A persisted invocation retains its exact project, including no project.

    Drafts may infer a destination's project. Delivery and recovery cannot use
    that inference to accept a conversation refiled after the run began.
    """
    recorded_project = record.get("project_id") or None
    contract = validate_contract(dict(record.get("workflow_definition") or {},
        conversation_id=record.get("conversation_id"), project_id=recorded_project))
    if contract["project_id"] != recorded_project:
        raise UserFacingValueError("The saved destination chat has moved since this run started.")
    return contract


def validate_timing(when):
    if when is None:
        return None
    if not isinstance(when, dict) or not isinstance(when.get("spec", {}), dict):
        raise UserFacingValueError("Choose a supported schedule and its timing.")
    trigger, spec = when.get("trigger"), copy.deepcopy(when.get("spec") or {})
    if trigger not in ("daily", "weekly", "interval", "once"):
        raise UserFacingValueError("That timing isn't one Friday can keep.")
    def number(key, default, low, high, integer=True):
        value = spec.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise UserFacingValueError(f"Schedule {key} must be a number.")
        if (integer and int(value) != value) or not low <= value <= high:
            raise UserFacingValueError(f"Schedule {key} must be between {low} and {high}.")
        return int(value) if integer else value
    if trigger in ("daily", "weekly"):
        spec["hour"] = number("hour", 8, 0, 23)
        spec["minute"] = number("minute", 0, 0, 59)
    if trigger == "weekly":
        days = spec.get("weekdays", [spec.get("weekday", 0)])
        if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
            raise UserFacingValueError("Select at least one valid weekday.")
        spec["weekdays"] = sorted(set(days))
    if trigger == "interval":
        spec["every_minutes"] = number("every_minutes", 60, 1, 525600)
    if trigger == "once":
        spec["at"] = number("at", 0, 0, 253402300799, integer=False)
        if spec["at"] <= time.time():
            raise UserFacingValueError("That time has already passed.")
    return {"trigger": trigger, "spec": spec}


def require_recording():
    from agent_friday.core import _load_settings
    from agent_friday.services import off_record
    if off_record.active(_load_settings() or {}):
        raise UserFacingValueError("Saved workflows need a durable record. Leave off-the-record mode to save or run one.")


def _history_dir(slug):
    from agent_friday.services import agent
    return agent._workflows_dir() / "_history" / safe_name(slug, what="workflow name")


def remember_definition(definition, schedule=None):
    """Keep immutable revisions beside the definitions, outside the list of workflows."""
    if not definition:
        return
    version = int(definition.get("revision") or 1)
    target = _history_dir(definition["slug"]) / f"v{version}.json"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {"definition": copy.deepcopy(definition), "when": None, "enabled": True}
    if schedule:
        body.update(when={"trigger": schedule["trigger"], "spec": schedule.get("spec") or {}},
                    enabled=bool(schedule.get("enabled", True)))
    # Readers only see complete versions; an existing immutable revision wins.
    temp = target.with_suffix(".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(body, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


def revisions(slug):
    rows = []
    root = _history_dir(slug)
    if root.exists():
        for path in root.glob("v*.json"):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                definition = saved["definition"]
                rows.append({"revision": definition.get("revision", 1), "name": definition["name"],
                             "updated": definition.get("updated"), "when": saved.get("when")})
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return sorted(rows, key=lambda row: row["revision"], reverse=True)


def starters():
    """Runnable starting procedures. Choosing one only fills the existing editor."""
    return [
        {"id": "research-brief", "name": "Research brief", "description": "Turn selected sources into a checked, editable brief.",
         "inputs": [], "output": {"kind": "artifact", "title": "Research brief"},
         "success_criteria": "Cite the sources used, state their dates, and name missing or unavailable sources.",
         "steps": [{"name": "Read the sources", "prompt": "Read the sources provided for this workflow using available governed tools. Record source references, dates, key findings and coverage gaps. Ask for sources if none are selected; do not invent them."},
                   {"name": "Prepare the brief", "prompt": "Create an editable markdown artifact with the findings, supporting source references, dates, and a short section on what remains uncertain. Save it to this conversation with artifact_put, carrying the task id and source_refs. Read it back to verify its content."}]},
        {"id": "daily-brief", "name": "Daily briefing", "description": "A concise briefing from the sources that matter to a project.",
         "inputs": [], "when": {"trigger": "weekly", "spec": {"weekdays": [0, 1, 2, 3, 4], "hour": 8, "minute": 0}},
         "notify": "on_complete", "output": {"kind": "artifact", "title": "Daily briefing"},
         "success_criteria": "Show the date, source coverage, significant developments, and actionable next steps.",
         "steps": [{"name": "Gather today's context", "prompt": "Read the selected sources and relevant project context. Use mail or calendar only when selected and connected. Check source dates, distinguish new information, and name any connection or source that is unavailable."},
                   {"name": "Write the briefing", "prompt": "Save an editable dated briefing with artifact_put in this conversation, carrying task id and source_refs. Separate developments, decisions needing attention and source gaps. Check the saved artifact before reporting completion."}]},
        {"id": "change-monitor", "name": "Watch for changes", "description": "Compare selected sources and notify only when something meaningful changes.",
         "inputs": [], "notify": "on_change", "when": {"trigger": "interval", "spec": {"every_minutes": 60}},
         "output": {"kind": "reply"}, "success_criteria": "Use the previous successful result as a baseline; identify source gaps and avoid repeated alerts.",
         "steps": [{"name": "Compare sources", "prompt": "Check the selected sources with the available read tools. Compare their facts against the previous run supplied in workflow context; avoid incidental timestamps or wording changes. On the first run, establish a concise factual baseline. If nothing meaningful changed, return exactly NO CHANGE. Otherwise explain the change with source references. Do not claim no change when a source could not be checked."}]},
        {"id": "notes-document", "name": "Notes to document", "description": "Turn dictated or written notes into a finished, editable document.",
         "inputs": [], "output": {"kind": "artifact", "title": "Finished document"},
         "success_criteria": "Preserve the meaning of the source notes, label missing information, and check the saved document.",
         "steps": [{"name": "Shape the notes", "prompt": "Read the notes selected for this workflow. Determine the requested audience, purpose and structure from the project and conversation. Preserve the user's meaning and list any missing decisions. Do not fabricate facts."},
                   {"name": "Finish the document", "prompt": "Write the finished document as an editable artifact in this conversation using artifact_put, carrying task id and source_refs. Review it against the notes and reread the saved result. Keep missing facts clearly labeled."}]},
        {"id": "reviewed-change", "name": "Specification to reviewed change", "description": "Use a connected codebase to prepare a tested change for review.",
         "inputs": [], "output": {"kind": "code", "title": "Reviewed change"},
         "success_criteria": "Work in an isolated checkout, include the diff and actual check results, and leave publication for the existing approval path.",
         "steps": [{"name": "Understand the change", "prompt": "Read the specification and the connected project codebase. Discover the codebase tools and repository instructions. State acceptance criteria and identify relevant checks. If no codebase is connected, report that requirement without claiming work began."},
                   {"name": "Build and verify", "prompt": "Use the governed codebase tools to implement the specification in an isolated worktree. Run the applicable checks. Produce a reviewable diff and honest test results, recording task id and evidence in a diff or markdown artifact. Do not publish or deploy without the existing authority path."}]},
    ]


def _definition(slug):
    from agent_friday.services import agent
    if not isinstance(slug, str) or not slug.strip():
        raise UserFacingValueError("Choose the workflow to act on.")
    found = agent.load_workflow_chain(slug)
    if not found:
        raise UserFacingValueError("That workflow no longer exists.")
    return found


def _owner(definition, context):
    from agent_friday.services import conversations
    pid = definition.get("project_id")
    cid = definition.get("conversation_id") or (context or {}).get("conversation_id")
    if cid:
        # A project routine must not silently deliver into an unrelated open chat.
        conv = conversations.load(cid)
        if not conv or conv.get("status") == "archived" or (pid and conv.get("project") != pid):
            if definition.get("conversation_id"):
                raise UserFacingValueError("The saved destination chat is unavailable or moved. Choose its replacement before running.")
            cid = None
    if not cid:
        conv = conversations.create(title=definition["name"])
        cid = conv["id"]
        if pid:
            conversations.patch(cid, project=pid)
    return cid


def execute(action, args=None, context=None):
    """Operate on the existing workflow/scheduler/task stores; return a UI/tool envelope."""
    from agent_friday.services import agent, scheduler, workflow_overview as overview
    args, context = dict(args or {}), dict(context or {})
    action = str(action or "").lower()
    nested = any(context.get(key) for key in
                 ("nested_execution", "is_background_task", "task_id", "agent_id", "schedule_id"))
    if nested and action not in ("starters", "draft"):
        raise UserFacingValueError("A scoped background task cannot inspect or change separate workflows. Ask from its chat or voice conversation so the work keeps the correct authority.")
    if action in ("list", "describe"):
        return {"status": "ok", **overview.overview(), "starters": starters(),
                "note": "Workflows run on this computer while Friday is running. Each step uses the existing tool permissions."}
    if action == "starters":
        return {"status": "ok", "starters": starters()}
    if action == "draft":
        draft = overview.draft_from_text(args.get("text"))
        draft.update({k: args[k] for k in CONTRACT_FIELDS if k in args})
        if context.get("conversation_id") and "conversation_id" not in draft:
            draft["conversation_id"] = context["conversation_id"]
        return {"status": "ok", "draft": draft, "note": "Draft prepared; nothing has been saved or scheduled."}
    if action not in ("inspect", "create", "update", "run", "repeat", "pause", "resume", "stop", "learn", "restore", "delete", "retry_delivery"):
        raise UserFacingValueError("That workflow action is not supported.")
    if action != "inspect":
        require_recording()
    with LOCK:
        if action in ("create", "update"):
            draft = dict(args.get("draft") or args)
            if action == "update":
                previous = _definition(draft.get("slug"))
                row = next(w for w in overview.overview()["workflows"] if w.get("slug") == previous["slug"])
                draft = dict(row, **draft)
            if context.get("conversation_id") and "conversation_id" not in draft:
                draft["conversation_id"] = context["conversation_id"]
            saved = overview.save(draft)
            return {"status": "ok", **saved, "workflow": _definition(saved["slug"]),
                    "note": "Workflow saved."}
        definition = _definition(args.get("slug"))
        slug = definition["slug"]
        schedule = overview._linked_schedule(slug, args.get("schedule_id"))
        if action == "inspect":
            run = agent.chain_run_status(slug, run_id=args["run_id"]) if args.get("run_id") else agent.chain_run_status(slug)
            return {"status": "ok", "workflow": definition, "run": run,
                    "schedule": schedule, "revisions": revisions(slug),
                    "note": "Current workflow and execution state."}
        if action == "delete":
            overview.delete(slug, (schedule or {}).get("id"))
            return {"status": "ok", "note": "Workflow and timing removed. Existing runs and their records are retained."}
        if action == "retry_delivery":
            run = agent.retry_workflow_delivery(slug, run_id=args.get("run_id"))
            return {"status": "ok", "run": run,
                    "note": "Delivery retried using the existing result. No workflow step was run again."}
        if action in ("pause", "resume"):
            if not schedule:
                raise UserFacingValueError("This workflow runs on demand. Add timing before pausing or resuming its schedule.")
            if not scheduler.update_schedule(schedule["id"], {"enabled": action == "resume"}):
                raise UserFacingValueError("The schedule disappeared; reload before trying again.")
            return {"status": "ok", "note": "Schedule resumed." if action == "resume" else "Schedule paused. Work already running is unchanged."}
        if action == "stop":
            from agent_friday.services import task_journal
            run = agent.chain_run_status(slug, run_id=args["run_id"]) if args.get("run_id") else agent.chain_run_status(slug)
            stopped = []
            for step in (run or {}).get("steps", []):
                tid = step.get("task_id")
                if tid and step.get("status") in ACTIVE:
                    task_journal.request_stop(tid)
                    task_journal.steer("Stop this workflow after the current tool finishes.", source="user", task_id=tid)
                    stopped.append(tid)
            if not stopped:
                raise UserFacingValueError("That run has no active step to stop.")
            return {"status": "ok", "task_ids": stopped, "note": "Stop requested. The current tool can finish; no further step should start."}
        if action == "restore":
            try:
                version = int(args.get("revision"))
                path = _history_dir(slug) / f"v{version}.json"
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (TypeError, ValueError, OSError):
                raise UserFacingValueError("That saved workflow version is unavailable.") from None
            restored = dict(saved["definition"], slug=slug, name=definition["name"],
                            revision=definition.get("revision", 1),
                            when=saved.get("when"), enabled=saved.get("enabled", True))
            if schedule:
                restored["schedule_id"] = schedule["id"]
            result = overview.save(restored)
            return {"status": "ok", **result, "note": f"Restored version {version} as a new revision; history is retained."}
        if action == "learn":
            return _learn(definition, args)
        # Starting work is separate from changing its reusable definition or timing.
        validate_contract(definition)
        run = agent.chain_run_status(slug)
        if (run or {}).get("state") in ACTIVE:
            raise UserFacingValueError("This workflow is already running. Open that run or stop it before starting another.")
        cid = _owner(definition, context)
        if not definition.get("conversation_id"):
            definition = dict(definition, conversation_id=cid)
            definition = agent.save_workflow_chain(definition)
            remember_definition(definition, schedule)
        tid = agent.run_workflow_chain(slug, conversation_id=cid, project_id=definition.get("project_id"))
        if not tid:
            raise UserFacingValueError("The workflow could not start. Its record has been kept.")
        task = agent._task_snapshot(tid) or {}
        return {"status": "ok", "task_id": tid, "conversation_id": cid,
                "run_id": task.get("run_id"), "run": agent.chain_run_status(slug),
                "note": "Workflow started. Its result will return to the saved chat; completion has not been verified yet."}


def _learn(definition, args):
    from agent_friday import skill_registry
    from agent_friday.services import agent
    slug = definition["slug"]
    run = agent.chain_run_status(slug, run_id=args["run_id"]) if args.get("run_id") else agent.chain_run_status(slug)
    verification = (run or {}).get("verification") or {}
    if (run or {}).get("state") not in ("completed", "complete") or verification.get("status") != "verified":
        raise UserFacingValueError("Verify a successful run before saving its procedure as a learned skill.")
    run_revision = int((run or {}).get("workflow_revision") or 0)
    if run_revision != int(definition.get("revision") or 1):
        raise UserFacingValueError("The workflow changed after that run. Check the current revision before learning it.")
    name = "workflow-" + slug
    previous = skill_registry.get_skill(name)
    if previous and previous.source != "workflow":
        raise UserFacingValueError("A different skill already uses that name. Rename the workflow first.")
    # Repeating an explicit learn action is idempotent for the same revision.
    version = run_revision
    if previous and previous.version >= version:
        return {"status": "ok", "skill": name, "note": "This procedure is already saved from the checked run."}
    body = [f"# {definition['name']}", "", definition.get("description") or "",
            "Use the workflow's selected sources and destination; ask for any missing input.",
            "Keep the existing tool permissions. A stored procedure never grants new authority.",
            "The source run's deliverable was structurally checked. Review the quality criteria on each reuse.", ""]
    for number, step in enumerate(definition["steps"], 1):
        body.extend([f"## {number}. {step['name']}", step["prompt"], ""])
    body.extend(["## Completion", definition.get("success_criteria") or "Check the requested output before claiming completion.",
                 "Expected result: " + (definition.get("output") or {}).get("kind", "reply")])
    skill_registry.save_skill(name, description=definition.get("description") or definition["name"],
                              body="\n".join(body), triggers=[definition["name"]],
                              success_criteria=[definition.get("success_criteria") or "Verify the requested output."],
                              source="workflow", version=version)
    learned = skill_registry.get_skill(name)
    if not learned or learned.version != version:
        raise UserFacingValueError("The learned procedure could not be read back. No successful save is claimed.")
    return {"status": "ok", "skill": name, "version": version,
            "note": "Procedure saved from the checked run. Quality criteria still need review on reuse; permissions are unchanged."}
