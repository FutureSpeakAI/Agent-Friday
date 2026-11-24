"""A goal milestone whose run an earlier process left behind gets one recovery receipt in the action
ledger when the boot pass settles it (BD-16 C3 gap: "goal milestones"). Synthetic goals, tasks and
verdicts; no model, no network."""
from __future__ import annotations

import time

import pytest

from tests import ledger_support

NOW = time.time()
PAST = "2020-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import approvals as approvals_mod
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import goals as goals_mod
    from agent_friday.services import recovery_receipts as rr
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(goals_mod, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals_mod, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals_mod, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(approvals_mod, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    rr._reset_for_tests()
    yield
    rr._reset_for_tests()
    goals_mod.set_executor(None)


def _stopped_milestone(run):
    from agent_friday.services import goals
    goal = goals.create_goal(title="Synthetic newsletter", status="active", success_criteria="x",
                             milestones=[{"name": "Draft issue", "due": PAST}])
    gid, mid = goal["goal_id"], goal["milestones"][0]["milestone_id"]
    goals._update_milestone(gid, mid, status="in_progress", run=run)
    return gid, mid


def _tasks(monkeypatch, table):
    from agent_friday.services import goals
    monkeypatch.setattr(goals, "_task_status", lambda tid: table.get(tid, (None, "")), raising=False)


def _verdict(monkeypatch, **kw):
    from agent_friday.services import task_resume
    monkeypatch.setattr(task_resume, "resumability", lambda tid: dict(
        {"resumable": True, "needs_confirmation": True, "iteration": 3, "pending_tool": "send_email"}, **kw))


def _mine(gid, mid):
    return [d for d in ledger_support.decisions() if d.get("subject") == "milestone:%s:%s" % (gid, mid)]


def _boot():
    from agent_friday.services import goals
    goals.reconcile_on_boot(background=False)


def test_a_milestone_stopped_after_a_send_began_leaves_one_receipt_that_says_it_may_have_acted(monkeypatch):
    gid, mid = _stopped_milestone({"task_id": "task_old", "boot": "prev", "started_at": NOW - 60})
    _tasks(monkeypatch, {"task_old": ("interrupted", "")})
    _verdict(monkeypatch)
    _boot()
    [r] = _mine(gid, mid)
    assert r["decision"] == "recovered" and r["tool"] == "recovery" and r["what"] == "Draft issue"
    assert r["may_have_acted"] is True and r["repeated"] is False
    assert r["next"] == "asks_the_owner_before_resuming" and r["task"] == "task_old"


def test_a_second_start_writes_no_second_receipt(monkeypatch):
    from agent_friday.services import recovery_receipts as rr
    gid, mid = _stopped_milestone({"task_id": "task_old", "boot": "prev", "started_at": NOW - 60})
    _tasks(monkeypatch, {"task_old": ("interrupted", "")})
    _verdict(monkeypatch)
    _boot()
    rr._reset_for_tests()
    _boot()
    assert len(_mine(gid, mid)) == 1


def test_work_that_never_started_says_nothing_ran_and_runs_again(monkeypatch):
    gid, mid = _stopped_milestone({"task_id": None, "boot": "prev", "started_at": NOW - 60, "reserved_mψ": 0})
    _tasks(monkeypatch, {})
    _boot()
    [r] = _mine(gid, mid)
    assert r["may_have_acted"] is False and r["next"] == "runs_again_when_due"


def test_a_task_that_ended_in_error_and_a_run_with_no_record_are_not_repeated(monkeypatch):
    gid1, mid1 = _stopped_milestone({"task_id": "task_bad", "boot": "prev", "started_at": NOW - 60})
    gid2, mid2 = _stopped_milestone({"task_id": None, "boot": "prev", "started_at": NOW - 30, "stage": "dispatching"})
    _tasks(monkeypatch, {"task_bad": ("failed", "provider error")})
    _boot()
    for gid, mid in ((gid1, mid1), (gid2, mid2)):
        [r] = _mine(gid, mid)
        assert r["may_have_acted"] is True and r["next"] == "not_repeated_owner_told"


def test_a_task_that_finished_or_is_running_again_gets_no_milestone_receipt(monkeypatch):
    gid, mid = _stopped_milestone({"task_id": "task_done", "boot": "prev", "started_at": NOW - 60})
    _tasks(monkeypatch, {"task_done": ("done", "the draft")})
    _boot()
    assert _mine(gid, mid) == []


def test_the_receipt_writer_itself_writes_one_entry_for_the_same_interruption():
    """The dedupe a second start relies on, tried directly: the same (milestone, interruption) twice is one entry."""
    from agent_friday.services import recovery_receipts as rr
    goal, m = {"goal_id": "g_dd"}, {"milestone_id": "m_dd", "name": "Draft issue"}
    run = {"task_id": "task_x", "started_at": NOW - 60}
    kw = dict(boundary="its task was interrupted", may_have_acted=True, next=rr.NOT_REPEATED)
    assert rr.milestone_interrupted(goal, m, run, **kw) is True
    assert rr.milestone_interrupted(goal, m, run, **kw) is False
    assert len(_mine("g_dd", "m_dd")) == 1


def test_a_task_the_pc_cannot_find_is_not_called_interrupted(monkeypatch):
    gid, mid = _stopped_milestone({"task_id": "task_gone", "boot": "prev", "started_at": NOW - 60})
    _tasks(monkeypatch, {})                                    # no such task: the outcome is "missing"
    _boot()
    [r] = _mine(gid, mid)
    assert "interrupted" not in r["last_boundary"] and "not be found" in r["last_boundary"]
    assert r["may_have_acted"] is True and r["next"] == "not_repeated_owner_told"


def test_the_receipt_is_written_before_the_milestone_is_settled(monkeypatch):
    """A crash between the two must lose the settle (which the next boot redoes), never the receipt."""
    from agent_friday.services import goals
    gid, mid = _stopped_milestone({"task_id": "task_old", "boot": "prev", "started_at": NOW - 60})
    _tasks(monkeypatch, {"task_old": ("interrupted", "")})
    _verdict(monkeypatch)

    def crash(*a, **k):
        raise RuntimeError("the process was killed here")
    monkeypatch.setattr(goals, "_block", crash)
    try:
        _boot()
    except RuntimeError:
        pass
    assert len(_mine(gid, mid)) == 1
