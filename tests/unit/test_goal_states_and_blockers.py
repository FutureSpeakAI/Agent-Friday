"""Goal states, typed blockers, continuation checks and the no-progress
breaker (north star §17.3, §17.8, §17.9, §17.10; BD-12 slice G2).

Every fixture is synthetic; the milestone executor and the verification
check are fakes, so nothing reaches a model.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import goals

PAST = "2020-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    yield
    goals.set_executor(None)


def _verdicts(monkeypatch, passed=True, critique=lambda n: f"critique {n}"):
    n = {"i": 0}

    def fake(content, intent, **kw):
        n["i"] += 1
        return {"status": "ok", "passed": passed, "score": 0.9 if passed else 0.3, "threshold": 0.7,
                "critique": "" if passed else critique(n["i"]), "suggestions": ""}
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", fake)


class _Work:
    def __init__(self, texts=None):
        self.calls = 0
        self.texts = texts

    def __call__(self, goal, milestone, *, critique_hint=""):
        self.calls += 1
        text = self.texts(self.calls) if self.texts else f"synthetic draft {self.calls}"
        return {"ok": True, "text": text, "cost_mψ": 10}


def _goal(**kw):
    args = dict(title="Synthetic goal", status="active", success_criteria="a synthetic draft exists",
                milestones=[{"name": "Draft", "due": PAST}])
    args.update(kw)
    return goals.create_goal(**args)


def _m(gid, i=0):
    return goals.get_goal(gid)["milestones"][i]


# ── §17.3 states ─────────────────────────────────────────────────────────

def test_every_state_the_spec_names_exists():
    want = {"active", "waiting", "needs_owner", "at_risk", "paused", "met_unverified",
            "met_verified", "abandoned", "failed", "archived"}
    assert want <= set(goals.STATUSES)


def test_a_goal_waiting_on_an_approval_is_waiting(monkeypatch):
    _verdicts(monkeypatch)
    goals.set_executor(_Work())
    g = _goal(approval_required=True)
    goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    assert goals.get_goal(g["goal_id"])["status"] == "waiting"
    assert _m(g["goal_id"])["blocker"]["category"] == "external_wait"


def test_a_goal_whose_check_failed_needs_the_owner(monkeypatch):
    _verdicts(monkeypatch, passed=False)
    goals.set_executor(_Work())
    g = _goal()
    goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    assert goals.get_goal(g["goal_id"])["status"] == "needs_owner"
    assert _m(g["goal_id"])["blocker"]["category"] == "missing_evidence"


def test_a_goal_near_its_deadline_with_work_open_is_at_risk():
    soon = goals._iso_from_ts(time.time() + 3600)
    g = _goal(deadline=soon, milestones=[{"name": "Draft"}])
    assert goals.get_goal(g["goal_id"])["status"] == "at_risk"


def test_only_the_goals_own_checks_mark_it_met_and_verified(monkeypatch):
    g = _goal()
    with pytest.raises(ValueError):
        goals.transition_goal(g["goal_id"], "met_verified")
    owner = goals.transition_goal(g["goal_id"], "met_unverified", reason="good enough for me")
    assert owner["status"] == "met_unverified"
    assert owner["completion"] == {**owner["completion"], "by": "owner", "verified": False}

    _verdicts(monkeypatch)
    goals.set_executor(_Work())
    g2 = _goal()
    goals.run_milestone(g2["goal_id"], _m(g2["goal_id"])["milestone_id"])
    done = goals.get_goal(g2["goal_id"])
    assert done["status"] == "met_verified" and done["completion"]["by"] == "verification"


def test_a_goal_met_on_an_overridden_check_is_not_called_verified(monkeypatch):
    _verdicts(monkeypatch, passed=False)
    goals.set_executor(_Work())
    g = _goal()
    out = goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    goals.approvals.decide(out["approval_id"], "approve", decided_by="owner")
    assert goals.get_goal(g["goal_id"])["status"] == "met_unverified"


def test_a_met_goal_can_be_archived_and_reopened():
    g = _goal(milestones=[{"name": "Draft"}])      # not late, so not at risk
    goals.transition_goal(g["goal_id"], "met_unverified")
    archived = goals.transition_goal(g["goal_id"], "archived")
    assert archived["status"] == "archived"
    reopened = goals.transition_goal(g["goal_id"], "active", reason="one more thing")
    assert reopened["status"] == "active" and reopened.get("completion") is None
    abandoned = goals.transition_goal(g["goal_id"], "abandoned")
    assert goals.transition_goal(abandoned["goal_id"], "active")["status"] == "active"


def test_older_goal_files_read_in_todays_names():
    g = _goal()
    goals._mutate_goal(g["goal_id"], lambda x: x)  # write once
    raw = goals._goal_path(g["goal_id"])
    import json
    data = json.loads(raw.read_text(encoding="utf-8"))
    data["status"] = "cancelled"
    raw.write_text(json.dumps(data), encoding="utf-8")
    assert goals.get_goal(g["goal_id"])["status"] == "abandoned"
    data["status"] = "completed"
    raw.write_text(json.dumps(data), encoding="utf-8")
    assert goals.get_goal(g["goal_id"])["status"] == "met_unverified"


# ── §17.8 typed blockers ─────────────────────────────────────────────────

def test_a_denied_milestone_is_policy_blocked(monkeypatch):
    _verdicts(monkeypatch)
    goals.set_executor(_Work())
    g = _goal(approval_required=True)
    out = goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    goals.approvals.decide(out["approval_id"], "deny", decided_by="owner")
    goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    assert _m(g["goal_id"])["blocker"]["category"] == "policy_blocked"


def test_a_milestone_over_budget_says_so(monkeypatch):
    _verdicts(monkeypatch)
    goals.set_executor(_Work())
    g = _goal(budget_cap_mψ=10, milestones=[{"name": "Draft", "cost_estimate_mψ": 500}])
    goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    assert _m(g["goal_id"])["blocker"]["category"] == "budget_exhausted"
    assert goals.get_goal(g["goal_id"])["status"] == "needs_owner"


# ── §17.9 continuation ───────────────────────────────────────────────────

def test_nothing_starts_on_its_own_without_an_expected_result(monkeypatch):
    _verdicts(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(success_criteria="")
    out = goals.run_due_milestones()
    assert work.calls == 0
    hold = _m(g["goal_id"])["hold"]
    assert hold["category"] == "needs_user_input" and hold["type"] == "no_expected_evidence"
    assert out["held"]
    # The owner says what result they expect: the next tick runs it.
    goals.update_goal(g["goal_id"], {"success_criteria": "a synthetic draft exists"})
    goals.run_due_milestones()
    assert work.calls == 1 and not _m(g["goal_id"]).get("hold")


def test_nothing_more_starts_while_an_answer_is_pending(monkeypatch):
    _verdicts(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(milestones=[{"name": "First", "due": PAST}, {"name": "Second", "due": PAST}])
    gid = g["goal_id"]
    goals._update_milestone(gid, _m(gid, 0)["milestone_id"], status="blocked", approval_id="appr_x",
                            blocker=goals.goal_state.blocker("external_wait", "waits for you"))
    goals.run_due_milestones()
    assert work.calls == 0
    assert _m(gid, 1)["hold"]["type"] == "approval_pending"


def test_nothing_starts_while_another_piece_is_running(monkeypatch):
    work = _Work()
    goals.set_executor(work)
    g = _goal(milestones=[{"name": "First", "due": PAST}, {"name": "Second", "due": PAST}])
    gid = g["goal_id"]
    goals._update_milestone(gid, _m(gid, 0)["milestone_id"], status="in_progress",
                            run={"boot": goals.BOOT_ID, "task_id": "t", "started_at": time.time()})
    goals.run_due_milestones()
    assert work.calls == 0
    assert _m(gid, 1)["hold"]["type"] == "checkpoint"


def test_a_step_waits_for_the_steps_it_depends_on(monkeypatch):
    _verdicts(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(milestones=[{"name": "First"}, {"name": "Second", "due": PAST}])
    gid = g["goal_id"]
    first = _m(gid, 0)["milestone_id"]
    goals._update_milestone(gid, _m(gid, 1)["milestone_id"], depends_on=[first])
    goals.run_due_milestones()
    assert work.calls == 0 and _m(gid, 1)["hold"]["type"] == "dependency"


def test_nothing_starts_that_would_go_over_budget(monkeypatch):
    work = _Work()
    goals.set_executor(work)
    g = _goal(budget_cap_mψ=100, milestones=[{"name": "Draft", "due": PAST, "cost_estimate_mψ": 500}])
    goals.run_due_milestones()
    assert work.calls == 0
    assert _m(g["goal_id"])["hold"]["category"] == "budget_exhausted"


def test_a_repair_stops_when_the_owner_changes_the_plan_mid_attempt(monkeypatch):
    _verdicts(monkeypatch, passed=False)
    g = _goal()
    gid = g["goal_id"]

    def work(goal, milestone, *, critique_hint=""):
        work.calls += 1
        time.sleep(0.01)
        goals.update_goal(gid, {"success_criteria": "a different result entirely"})
        return {"ok": True, "text": f"draft {work.calls}", "cost_mψ": 10}
    work.calls = 0
    goals.set_executor(work)
    out = goals.run_milestone(gid, _m(gid)["milestone_id"])
    assert work.calls == 1
    assert out["status"] == "plan_changed"
    assert _m(gid)["blocker"]["category"] == "needs_user_input"


# ── §17.10 no progress ───────────────────────────────────────────────────

def test_the_same_result_twice_stops_with_a_no_progress_blocker(monkeypatch):
    _verdicts(monkeypatch, passed=False)
    work = _Work(texts=lambda n: "the very same synthetic draft")
    goals.set_executor(work)
    g = _goal()
    out = goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    assert work.calls == 2, "an identical third attempt was dispatched"
    assert out["status"] == "no_progress"
    m = _m(g["goal_id"])
    assert m["status"] == "blocked" and m["blocker"]["category"] == "no_progress"
    assert "same result" in m["blocker"]["text"]
    assert goals.get_goal(g["goal_id"])["status"] == "needs_owner"


def test_the_same_critique_twice_stops_with_a_no_progress_blocker(monkeypatch):
    _verdicts(monkeypatch, passed=False, critique=lambda n: "the dates are wrong")
    work = _Work()
    goals.set_executor(work)
    g = _goal()
    out = goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    assert work.calls == 2 and out["status"] == "no_progress"
    assert "same problem" in _m(g["goal_id"])["blocker"]["text"]


def test_no_progress_holds_across_runs_until_the_owner_says_go(monkeypatch):
    _verdicts(monkeypatch, passed=False)
    work = _Work(texts=lambda n: "the very same synthetic draft")
    goals.set_executor(work)
    g = _goal()
    gid = g["goal_id"]
    mid = _m(gid)["milestone_id"]
    goals.run_milestone(gid, mid)
    before = work.calls
    goals.run_due_milestones()
    goals.run_milestone(gid, mid)
    assert work.calls == before, "a stopped milestone ran again without the owner"
    # Its attempts did run, so starting again is the owner's explicit yes.
    refused = goals.restart_milestone(gid, mid)
    assert refused["status"] == "needs_confirmation" and work.calls == before
    out = goals.restart_milestone(gid, mid, confirm_unknown_outcome=True)
    assert out["ok"] is True
    assert work.calls > before


def test_a_new_plan_is_a_new_start_for_the_no_progress_check(monkeypatch):
    calls = {"n": 0}

    def verdict(content, intent, **kw):
        calls["n"] += 1
        ok = "relaxed" in intent
        return {"status": "ok", "passed": ok, "score": 0.9 if ok else 0.3, "threshold": 0.7,
                "critique": "" if ok else f"critique {calls['n']}", "suggestions": ""}
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", verdict)
    goals.set_executor(_Work(texts=lambda n: "the very same synthetic draft"))
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    assert goals.run_milestone(gid, mid)["status"] == "no_progress"
    goals.update_goal(gid, {"success_criteria": "a relaxed synthetic draft"})
    out = goals.restart_milestone(gid, mid, confirm_unknown_outcome=True)
    assert out["run"]["status"] == "done", "the same output was stopped against the old plan's marks"


def test_a_skipped_check_never_counts_as_verified(monkeypatch):
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", lambda c, i, **kw: {
        "status": "skipped", "passed": True, "score": None, "critique": "", "suggestions": ""})
    goals.set_executor(_Work())
    g = _goal()
    goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    assert goals.get_goal(g["goal_id"])["status"] == "met_unverified"


def test_a_yes_to_a_card_raised_before_the_plan_changed_does_not_run_the_new_plan(monkeypatch):
    _verdicts(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(approval_required=True)
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    out = goals.run_milestone(gid, mid)
    time.sleep(0.01)
    goals.update_goal(gid, {"success_criteria": "something else entirely"})
    goals.approvals.decide(out["approval_id"], "approve", decided_by="owner")
    assert work.calls == 0
    assert _m(gid)["blocker"]["type"] == "approval_stale"


def test_starting_again_after_a_refused_card_asks_on_a_new_card(monkeypatch):
    _verdicts(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(approval_required=True)
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    first = goals.run_milestone(gid, mid)
    goals.approvals.decide(first["approval_id"], "deny", decided_by="owner")
    again = goals.restart_milestone(gid, mid)
    assert again["ok"] is True and again["run"]["status"] == "pending_approval"
    assert again["run"]["approval_id"] != first["approval_id"]
    assert work.calls == 0


def test_a_stop_tied_to_a_task_keeps_its_type_however_often(monkeypatch):
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    for _ in range(4):
        goals._block(gid, mid, goals.goal_state.blocker(
            "needs_user_input", "synthetic", type="interrupted", task_id="task_x",
            needs_confirmation=True), note="synthetic")
    assert _m(gid)["blocker"]["type"] == "interrupted"


def test_a_milestone_waiting_on_its_card_is_not_run_again(monkeypatch):
    _verdicts(monkeypatch, passed=False)
    work = _Work()
    goals.set_executor(work)
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    goals.run_milestone(gid, mid)
    before = work.calls
    assert goals.run_milestone(gid, mid)["status"] == "waits_on_card"
    assert work.calls == before


def test_a_hold_is_shown_but_does_not_move_the_goal(monkeypatch):
    work = _Work()
    goals.set_executor(work)
    g = _goal(success_criteria="")
    goals.run_due_milestones()
    assert _m(g["goal_id"])["hold"]
    # Its step is late by design (that is what makes it due), so the goal is
    # at risk; the hold itself moves it to neither "waiting" nor "needs you".
    assert goals.get_goal(g["goal_id"])["status"] in ("active", "at_risk")


def test_stopping_for_the_same_reason_again_and_again_is_no_progress():
    g = _goal()
    gid = g["goal_id"]
    mid = _m(gid)["milestone_id"]
    for _ in range(3):
        goals._block(gid, mid, goals.goal_state.blocker("run_failed", "synthetic failure",
                                                         needs_confirmation=False),
                     note="synthetic")
    assert _m(gid)["blocker"]["category"] == "no_progress"


def test_home_says_what_a_stopped_goal_waits_on(monkeypatch):
    from agent_friday.services import desktop_card_sources as sources
    _verdicts(monkeypatch, passed=False)
    goals.set_executor(_Work(texts=lambda n: "the very same synthetic draft"))
    g = _goal()
    goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    for kind in list(sources.KINDS):
        if kind != "goal":
            monkeypatch.setitem(sources._READERS, kind, lambda now: ([], None, "Empty fixture"))
    rec = list(sources.snapshot(time.time())["goal"]["records"].values())[0]
    assert rec["state"] == "needs_owner"
    assert "I stopped working on" in rec["body"]
