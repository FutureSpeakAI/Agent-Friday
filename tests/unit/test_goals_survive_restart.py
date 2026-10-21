"""Goals survive restarts, crashes and updates, and pick up again only through
the honest-resume rules (north star §17, burn-down BD-12 slice G1).

A milestone that was mid-run when Friday stopped is never simply run again:
its work lives in a background task, and that task already has honest-resume
rules (services/task_resume.py: a step whose outcome is unknown, such as a
send, is never repeated without the owner's explicit yes). The goal follows
its task: finished work is checked, not redone; work that never started runs
again; anything else waits for the owner, and a restart after an unknown
outcome needs the owner's explicit confirmation.

Every fixture is synthetic. No real agent, model or network is used: the
executor, the task status reader and the task-resume verdict are fakes.
"""
from __future__ import annotations

import json
import sys
import time
import types

import pytest

NOW = time.time()
PAST = "2020-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import goals as goals_mod
    from agent_friday.services import approvals as approvals_mod
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(goals_mod, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals_mod, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals_mod, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(approvals_mod, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(goals_mod.qa_gates, "evaluate_text",
                        lambda content, intent, **kw: {
                            "status": "ok", "passed": True, "score": 0.9,
                            "threshold": 0.7, "critique": "", "suggestions": ""})
    yield
    goals_mod.set_executor(None)


class _Counter:
    """A milestone executor that counts how often the work is dispatched."""

    def __init__(self):
        self.calls = 0

    def __call__(self, goal, milestone, *, critique_hint=""):
        self.calls += 1
        return {"ok": True, "text": "the synthetic newsletter draft", "cost_mψ": 10}


def _goal_with_milestone(goals, *, status="pending", run=None, due=PAST):
    goal = goals.create_goal(title="Synthetic newsletter", status="active",
                             success_criteria="mentions the newsletter",
                             milestones=[{"name": "Draft issue", "due": due}])
    gid = goal["goal_id"]
    mid = goal["milestones"][0]["milestone_id"]
    fields = {"status": status}
    if run is not None:
        fields["run"] = run
    goals._update_milestone(gid, mid, **fields)
    return gid, mid


def _milestone(goals, gid, mid):
    return goals._find_milestone(goals.get_goal(gid), mid)


def _tasks(monkeypatch, goals, table):
    """Point the goal module's task-status reader at a synthetic table
    {task_id: (status, result)}. raising=False: the reader is new with this
    slice, so on the old code the patch lands harmlessly and the test fails
    on its assertions, not on the patch."""
    monkeypatch.setattr(goals, "_task_status", lambda tid: table.get(tid, (None, "")),
                        raising=False)


def _verdict(monkeypatch, *, resumable=True, needs_confirmation=True,
             reason="send_email was in flight when Friday stopped."):
    from agent_friday.services import task_resume
    monkeypatch.setattr(task_resume, "resumability", lambda tid: {
        "resumable": resumable, "needs_confirmation": needs_confirmation,
        "reason": reason, "iteration": 3, "pending_tool": "send_email", "attempts": 0})


def _reconcile(goals):
    fn = getattr(goals, "reconcile_on_boot", None)
    if fn is not None:
        return fn(background=False)
    return None


# ── Survives a crash mid-write and an unreadable file ──────────────────────

def test_a_corrupt_goal_file_falls_back_to_its_last_good_copy():
    from agent_friday.services import goals
    goal = goals.create_goal(title="Synthetic goal", status="active")
    goals.update_goal(goal["goal_id"], {"description": "second write"})
    goals.update_goal(goal["goal_id"], {"description": "third write"})
    goals._goal_path(goal["goal_id"]).write_text('{"goal_id": "torn', encoding="utf-8")

    back = goals.get_goal(goal["goal_id"])
    assert back is not None, "a torn goal file must not make the goal vanish"
    assert back["title"] == "Synthetic goal"
    assert goal["goal_id"] in {g["goal_id"] for g in goals.list_goals()}


def test_an_unreadable_goal_is_reported_not_silently_dropped():
    from agent_friday.services import goals
    goal = goals.create_goal(title="Synthetic goal", status="active")
    goals._goal_path(goal["goal_id"]).write_text("not json", encoding="utf-8")
    for leftover in goals.GOALS_DIR.glob("*.bak"):
        leftover.unlink()

    unreadable = getattr(goals, "unreadable_goals", lambda: [])()
    assert [u["goal_id"] for u in unreadable] == [goal["goal_id"]]


def test_every_goal_write_carries_a_schema_version():
    from agent_friday.services import goals
    goal = goals.create_goal(title="Synthetic goal")
    raw = json.loads(goals._goal_path(goal["goal_id"]).read_text(encoding="utf-8"))
    assert raw.get("schema") == 1


# ── The milestone knows its task before the work ends ──────────────────────

def test_the_milestone_records_its_task_before_the_work_finishes(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals)
    seen = {}

    def spawn(name, prompt, **kw):
        return "task_synthetic_1"

    def snapshot(tid=None):
        m = _milestone(goals, gid, mid)
        seen["task_id"] = (m.get("run") or {}).get("task_id")
        return {"status": "completed", "result": "the synthetic newsletter draft"}

    fake = types.ModuleType("agent_friday.services.agent")
    fake._spawn_task = spawn
    fake._task_snapshot = snapshot
    monkeypatch.setitem(sys.modules, "agent_friday.services.agent", fake)
    goals.set_executor(None)

    out = goals.run_milestone(gid, mid)
    assert out["status"] == "done"
    assert seen.get("task_id") == "task_synthetic_1", (
        "the goal must point at its task while the task runs, or a restart "
        "cannot tie the two back together")


# ── After a restart: never repeat work whose outcome is unknown ────────────

def test_an_interrupted_milestone_with_an_unknown_send_is_never_run_again_on_its_own(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_old", "boot": "previous-process",
                                         "started_at": NOW - 60})
    _tasks(monkeypatch, goals, {"task_old": ("interrupted", "")})
    _verdict(monkeypatch, needs_confirmation=True)
    work = _Counter()
    goals.set_executor(work)

    _reconcile(goals)
    goals.run_due_milestones()
    goals.run_milestone(gid, mid)

    assert work.calls == 0, "an interrupted milestone was dispatched again without the owner"
    m = _milestone(goals, gid, mid)
    assert m["status"] == "blocked"
    assert m["blocker"]["type"] == "interrupted"
    assert m["blocker"]["needs_confirmation"] is True
    assert m["blocker"]["task_id"] == "task_old"


def test_restarting_after_an_unknown_outcome_needs_the_owners_explicit_yes(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_old", "boot": "previous-process",
                                         "started_at": NOW - 60})
    _tasks(monkeypatch, goals, {"task_old": ("interrupted", "")})
    _verdict(monkeypatch, needs_confirmation=True)
    work = _Counter()
    goals.set_executor(work)
    _reconcile(goals)

    restart = getattr(goals, "restart_milestone", None)
    assert restart is not None, "the owner has no way to start an interrupted milestone again"
    refused = restart(gid, mid)
    assert refused["ok"] is False and refused["status"] == "needs_confirmation"
    assert work.calls == 0

    done = restart(gid, mid, confirm_unknown_outcome=True)
    assert done["ok"] is True
    assert work.calls == 1
    receipts = goals.get_receipts(gid)
    assert any(r["kind"] == "milestone_restarted_by_owner" for r in receipts)


def test_a_task_that_finished_before_the_crash_is_checked_not_redone(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_done", "boot": "previous-process",
                                         "started_at": NOW - 60})
    _tasks(monkeypatch, goals, {"task_done": ("completed", "the synthetic newsletter draft")})
    work = _Counter()
    goals.set_executor(work)

    _reconcile(goals)

    assert work.calls == 0
    m = _milestone(goals, gid, mid)
    assert m["status"] == "done"
    kinds = [r["kind"] for r in goals.get_receipts(gid)]
    assert "milestone_complete" in kinds
    assert any("restart" in (r.get("note") or "") for r in goals.get_receipts(gid))


def test_work_that_never_started_runs_again(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": None, "boot": "previous-process",
                                         "started_at": NOW - 60})
    _tasks(monkeypatch, goals, {})
    work = _Counter()
    goals.set_executor(work)

    _reconcile(goals)
    assert _milestone(goals, gid, mid)["status"] == "pending"
    goals.run_due_milestones()
    assert work.calls == 1
    assert _milestone(goals, gid, mid)["status"] == "done"


def test_a_task_the_owner_resumes_is_picked_up_by_the_goal(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_old", "boot": "previous-process",
                                         "started_at": NOW - 60})
    table = {"task_old": ("interrupted", "")}
    _tasks(monkeypatch, goals, table)
    _verdict(monkeypatch, needs_confirmation=True)
    work = _Counter()
    goals.set_executor(work)
    _reconcile(goals)
    assert _milestone(goals, gid, mid)["status"] == "blocked"

    # The owner pressed Resume on the task in the Task Tray and it finished.
    table["task_old"] = ("completed", "the synthetic newsletter draft")
    adopt = getattr(goals, "adopt_resumed", None)
    assert adopt is not None
    adopt(background=False)

    assert work.calls == 0
    assert _milestone(goals, gid, mid)["status"] == "done"


def test_a_failed_task_leaves_a_typed_blocker_for_the_owner(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_bad", "boot": "previous-process",
                                         "started_at": NOW - 60})
    _tasks(monkeypatch, goals, {"task_bad": ("failed", "provider error")})
    work = _Counter()
    goals.set_executor(work)

    _reconcile(goals)
    goals.run_due_milestones()

    assert work.calls == 0
    m = _milestone(goals, gid, mid)
    assert m["status"] == "blocked"
    assert m["blocker"]["type"] == "run_failed"


def test_a_milestone_running_in_this_process_is_left_alone(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_live", "boot": getattr(goals, "BOOT_ID", "x"),
                                         "started_at": NOW})
    _tasks(monkeypatch, goals, {"task_live": ("running", "")})
    adopt = getattr(goals, "adopt_resumed", None)
    assert adopt is not None
    adopt(background=False)
    assert _milestone(goals, gid, mid)["status"] == "in_progress"


def test_a_task_that_only_names_the_milestone_is_never_adopted(monkeypatch):
    """A task is the goal's own only by the id the goal wrote down before the
    task existed. A task whose description merely names the goal and
    milestone (the model can spawn one) is never adopted as its result, and a
    run whose task is unknown waits for the owner instead of running again."""
    from agent_friday.services import goals
    from agent_friday.services import task_journal as tj
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": None, "stage": "dispatching",
                                         "boot": "previous-process", "started_at": NOW - 60})
    tj.write_state("task_planted", {"task_id": "task_planted", "name": "Goal · Draft issue",
                                    "description": f"goal:{gid}:milestone:{mid}",
                                    "status": "completed", "result": "the synthetic newsletter",
                                    "created": NOW - 30})
    tj.index_put("task_planted", "Goal · Draft issue", "completed", NOW - 30, NOW - 20)
    _tasks(monkeypatch, goals, {"task_planted": ("completed", "the synthetic newsletter")})
    work = _Counter()
    goals.set_executor(work)

    _reconcile(goals)
    goals.run_due_milestones()

    assert work.calls == 0
    m = _milestone(goals, gid, mid)
    assert m["status"] == "blocked"
    assert m["blocker"]["task_id"] is None
    assert not any(r["kind"] == "milestone_complete" for r in goals.get_receipts(gid))


def test_a_milestone_left_running_with_no_record_waits_for_the_owner(monkeypatch):
    """A milestone written before run records existed says only that it was
    running: what it did is unknown, so it is never simply run again."""
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress")
    _tasks(monkeypatch, goals, {})
    work = _Counter()
    goals.set_executor(work)

    _reconcile(goals)
    goals.run_due_milestones()

    assert work.calls == 0
    assert _milestone(goals, gid, mid)["status"] == "blocked"


def test_a_task_requeued_at_boot_is_followed_to_its_successor(monkeypatch):
    from agent_friday.services import goals
    from agent_friday.services import task_journal as tj
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_waiting", "stage": "dispatching",
                                         "boot": "previous-process", "started_at": NOW - 60})
    tj.append("task_waiting", "decision", point="readmit", chosen="task_successor",
              reason="synthetic re-queue")
    _tasks(monkeypatch, goals, {"task_waiting": ("superseded", ""),
                                "task_successor": ("completed", "the synthetic newsletter draft")})
    work = _Counter()
    goals.set_executor(work)

    _reconcile(goals)

    assert work.calls == 0
    m = _milestone(goals, gid, mid)
    assert m["status"] == "done"
    assert "task_successor" in (m.get("last_run") or {}).get("task_id", "")


def test_no_attempt_is_dispatched_over_a_task_being_picked_up(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_live", "stage": "dispatching",
                                         "boot": "previous-process", "started_at": NOW - 60})
    _tasks(monkeypatch, goals, {"task_live": ("running", "")})
    monkeypatch.setattr(goals, "_adopt", lambda *a, **k: None, raising=False)
    work = _Counter()
    goals.set_executor(work)

    _reconcile(goals)
    assert _milestone(goals, gid, mid)["status"] == "in_progress"
    out = goals.run_milestone(gid, mid)
    goals.run_due_milestones()

    assert work.calls == 0
    assert out.get("status") == "already_running"


def test_a_stale_adoption_cannot_judge_the_result_twice(monkeypatch):
    from agent_friday.services import goals
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_done", "stage": "dispatching",
                                         "boot": "previous-process", "started_at": NOW - 60})
    _tasks(monkeypatch, goals, {"task_done": ("completed", "the synthetic newsletter draft")})
    _reconcile(goals)
    assert _milestone(goals, gid, mid)["status"] == "done"
    before = len(goals.get_receipts(gid))

    out = goals._adopt_job(gid, mid, "task_done", "a-stale-token")

    assert out["status"] == "superseded"
    assert len(goals.get_receipts(gid)) == before


# ── On the desktop ─────────────────────────────────────────────────────────

def test_home_shows_a_goal_and_what_it_waits_on(monkeypatch):
    from agent_friday.services import goals
    from agent_friday.services import desktop_card_sources as sources
    gid, mid = _goal_with_milestone(goals, status="in_progress",
                                    run={"task_id": "task_old", "boot": "previous-process",
                                         "started_at": NOW - 60})
    _tasks(monkeypatch, goals, {"task_old": ("interrupted", "")})
    _verdict(monkeypatch, needs_confirmation=True)
    _reconcile(goals)
    for kind in list(sources.KINDS):
        if kind != "goal":
            monkeypatch.setitem(sources._READERS, kind, lambda now: ([], None, "Empty fixture"))

    observed = sources.snapshot(NOW)
    assert "goal" in observed, "Home has no goals source"
    rows = list(observed["goal"]["records"].values())
    assert [r["title"] for r in rows] == ["Synthetic newsletter"]
    assert "stopped" in rows[0]["body"].lower()
    cards = sources.automatic(observed, NOW)
    assert any(c["type"] == "goal" for c in cards)
