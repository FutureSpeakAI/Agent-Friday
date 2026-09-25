"""A routine either runs or says why. It never claims to have launched.

Launching a routine from the 'Home' workspace must start real work. Writing a
dict entry and returning success is not a launch:

    VIBE_TERMINALS[tid] = {"id": tid, "task": task_desc, "status": "pending", ...}
    status[routine_id] = {"last_run": stamp, "last_status": "launched", ...}
    return jsonify({"status": "ok", ..., "message": f"{reg['label']} launched"})

That starts no thread, no subprocess and no task, and nothing consumes a
"pending" VIBE_TERMINALS entry, so the row would sit there forever while the UI
reports a launch. The real launcher, `routes/code.vibe_code_launch`, starts a
`threading.Thread(target=_run_claude_terminal, ...)`.

An API that says "launched" while no work occurs is an invisible success and a
no-receipt claim of completion, which this codebase forbids everywhere.

`run_routine` dispatches to the REAL job where one exists -- the scheduler owns
`daily_creation`, `news_morning`, `afternoon_briefing` and `repo_sync` -- and
REFUSES, with the reason, for the registry entries that have no handler in this
build. A routine with no implementation is allowed to exist and say so; it is not
allowed to claim it ran.
"""

import pytest


def test_the_registry_and_the_handler_map_agree():
    """Every routine either maps to a real builtin task or is listed as having
    no handler. A registry entry that is in neither set would fall through to
    a success with no work behind it."""
    from agent_friday.services.misc_engine import ROUTINE_REGISTRY
    from agent_friday.routes import workflows

    ids = {r["id"] for r in ROUTINE_REGISTRY}
    covered = set(workflows.ROUTINE_TASKS) | set(workflows.ROUTINES_WITHOUT_HANDLERS)
    missing = ids - covered
    assert not missing, (
        "these routines are neither wired nor declared unimplemented: %s"
        % sorted(missing))


def test_daily_creation_maps_to_the_real_scheduled_job():
    from agent_friday.routes import workflows
    assert workflows.ROUTINE_TASKS.get("daily-creation") == "daily_creation"


def test_every_mapped_task_exists_in_the_scheduler():
    """A mapping to a ref the scheduler does not have would be the same defect
    wearing a nicer hat.

    Checked against the scheduler's SOURCE, not `BUILTIN_TASKS`: that dict is
    populated by `_register_default_builtin_tasks()` when the scheduler starts,
    so at import time in a test it is empty and this would pass vacuously in one
    direction and fail spuriously in the other.
    """
    import pathlib
    from agent_friday.routes import workflows

    src = (pathlib.Path(__file__).resolve().parents[2] / "src" / "agent_friday"
           / "services" / "scheduler.py").read_text(encoding="utf-8",
                                                    errors="replace")
    for rid, ref in workflows.ROUTINE_TASKS.items():
        assert ('"%s"' % ref) in src or ("'%s'" % ref) in src, (
            "routine %r maps to builtin task %r, which the scheduler never "
            "registers" % (rid, ref))


def test_running_daily_creation_actually_calls_the_job(client, monkeypatch):
    """The whole point. A 200 must mean work started."""
    from agent_friday.services import scheduler

    ran = []
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "daily_creation",
                        {"fn": lambda: ran.append("ran"),
                         "label": "Daily Creation"})

    # Run it inline instead of on a thread so the assertion is deterministic.
    from agent_friday.routes import workflows
    monkeypatch.setattr(workflows, "_start_routine_thread",
                        lambda ref, fn: fn(), raising=False)

    r = client.post("/api/routines/daily-creation/run")
    assert r.status_code == 200, r.get_json()
    assert ran == ["ran"], "the endpoint returned ok without running the job"


def test_a_routine_with_no_handler_is_refused_not_faked(client):
    """It may say it has no handler. It may not say it launched."""
    from agent_friday.routes import workflows
    rid = sorted(workflows.ROUTINES_WITHOUT_HANDLERS)[0]

    r = client.post("/api/routines/%s/run" % rid)
    assert r.status_code == 501, (
        "%s returned HTTP %s; an unimplemented routine must refuse"
        % (rid, r.status_code))
    body = r.get_json() or {}
    assert body.get("status") != "ok"
    msg = (body.get("message") or "").lower()
    assert "launch" not in msg or "not" in msg, (
        "the refusal still reads like a launch: %r" % body.get("message"))
    assert "no handler" in msg or "not implemented" in msg or "no automated" in msg


def test_an_unknown_routine_is_still_a_404(client):
    assert client.post("/api/routines/not-a-routine/run").status_code == 404


def test_a_refused_routine_is_not_recorded_as_launched(client, monkeypatch):
    """The status file must not carry `last_status: launched` for a run that
    never happened -- that makes the Home panel look like it had worked."""
    from agent_friday.routes import workflows
    from agent_friday.services import misc_engine

    saved = {}
    monkeypatch.setattr(misc_engine, "_save_routine_status",
                        lambda st: saved.update(st))
    monkeypatch.setattr(workflows, "_save_routine_status",
                        lambda st: saved.update(st), raising=False)

    rid = sorted(workflows.ROUTINES_WITHOUT_HANDLERS)[0]
    client.post("/api/routines/%s/run" % rid)

    rec = saved.get(rid) or {}
    assert rec.get("last_status") != "launched", (
        "a refused routine was recorded as launched: %r" % rec)


def test_a_routine_is_refused_while_stood_down(client, tmp_path, monkeypatch):
    """Background work does not run when the user has taken the machine, and the
    refusal says so rather than silently doing nothing."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    from agent_friday.services import stand_down as sd
    monkeypatch.setattr(sd, "_release_gpu", lambda: None)
    sd._invalidate()
    sd.stand_down(requested_by="owner")
    try:
        r = client.post("/api/routines/daily-creation/run")
        assert r.status_code == 409, (
            "a routine ran while stood down (HTTP %s)" % r.status_code)
        assert "stood down" in (r.get_json() or {}).get("message", "").lower()
    finally:
        sd.resume(requested_by="test")
        sd._invalidate()
