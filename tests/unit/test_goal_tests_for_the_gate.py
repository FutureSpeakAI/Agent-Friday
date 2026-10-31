"""Goal tests for the release gate (north star §34.10; BD-12).

Rows covered here (the behaviours the earlier goal test files leave open):

* NS-34.10-2  typed blockers and continuation preconditions: a goal or step
  held by a typed blocker does not continue, by the tick or a direct run,
  until the precondition holds, and does continue once it holds.
* NS-34.10-3  goal-level no-progress detection: repeated attempts with no new
  evidence stop with a "no progress" blocker instead of looping.
* NS-34.10-6  plan revision and approval waiting: a revision is recorded
  (plan_revised_at) and lets a stopped step continue; a step waiting on a
  card holds and neither runs nor completes while it waits.
* NS-34.10-7  evidence-first completion, receipt-first evaluation and manual
  completion labelling.

Everything runs through the real goals service on synthetic fixtures in
tmp_path. Only the executor seam (goals.set_executor) and the grader
(qa_gates.evaluate_text) are stubbed, as the other goal tests do; nothing
reaches a model or the network.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import goal_state
from agent_friday.services import goals

PAST = "2020-01-01T00:00:00Z"
KEY = b"k" * 32
UNVERIFIED_WORDS = "marked met without automatic proof"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import off_record
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    monkeypatch.setattr(ag, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(ag, "_governance_key", lambda: KEY)
    yield
    goals.set_executor(None)


def _grader(monkeypatch, passes=lambda intent: True, critique=lambda n: f"critique {n}"):
    """A fake grader; records every intent it was asked to grade."""
    seen = []

    def grade(content, intent, **kw):
        seen.append(intent)
        ok = bool(passes(intent))
        return {"status": "ok", "passed": ok, "score": 0.9 if ok else 0.3, "threshold": 0.7,
                "critique": "" if ok else critique(len(seen)), "suggestions": ""}
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", grade)
    return seen


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


def _row(gid, i=0):
    return goals.summary(goals.get_goal(gid))["milestones"][i]


def _kinds(gid):
    return [r.get("kind") for r in goals.get_goal(gid)["receipts"]]


# ── NS-34.10-2 typed blockers and continuation preconditions ─────────────────

def test_a_paused_goal_does_not_continue_until_it_is_resumed(monkeypatch):
    _grader(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    goals.transition_goal(gid, "paused")

    goals.run_due_milestones()
    direct = goals.run_milestone(gid, mid)
    assert work.calls == 0, "a paused goal's step ran"
    assert direct["ok"] is False and "paused" in direct["error"]
    hold = goal_state.continuation_hold(goals.get_goal(gid), _m(gid))
    assert hold and hold["category"] == "policy_blocked"

    goals.transition_goal(gid, "active", reason="go on")
    goals.run_due_milestones()
    assert work.calls == 1 and _m(gid)["status"] == "done"


@pytest.mark.parametrize("category,kind", [
    ("run_failed", "run_failed"),
    ("needs_user_input", "plan_changed"),
    ("missing_evidence", "check_unavailable"),
    ("no_progress", "no_progress"),
    ("policy_blocked", "result_refused"),
])
def test_a_step_stopped_on_an_owner_blocker_is_not_continued_by_the_tick_or_a_direct_run(
        monkeypatch, category, kind):
    _grader(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    goals._block(gid, mid, goal_state.blocker(category, "synthetic stop", type=kind,
                                              needs_confirmation=True), note="synthetic")
    for _ in range(3):
        goals.run_due_milestones()
    out = goals.run_milestone(gid, mid)
    assert work.calls == 0, "a stopped step was continued without the owner"
    assert out["ok"] is False and out["status"] == "interrupted"
    m = _m(gid)
    assert m["status"] == "blocked" and m["blocker"]["type"] == kind
    row = _row(gid)
    assert row["category"] in goal_state.BLOCKERS and row["category"] == category
    assert row["can_run"] is False and row["can_restart"] is True
    assert goals.get_goal(gid)["status"] == "needs_owner"


def test_a_step_waiting_on_its_dependency_starts_once_the_dependency_is_done(monkeypatch):
    _grader(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(milestones=[{"name": "First"}, {"name": "Second", "due": PAST}])
    gid = g["goal_id"]
    first, second = _m(gid, 0)["milestone_id"], _m(gid, 1)["milestone_id"]
    goals._update_milestone(gid, second, depends_on=[first])

    goals.run_due_milestones()
    assert work.calls == 0
    assert _m(gid, 1)["hold"]["type"] == "dependency" and _m(gid, 1)["hold"]["waiting_for"] == [first]

    assert goals.run_milestone(gid, first)["status"] == "done"
    goals.run_due_milestones()
    assert work.calls == 2, "the step did not start once what it waited for was done"
    assert _m(gid, 1)["status"] == "done" and not _m(gid, 1).get("hold")


def test_a_step_held_behind_an_unanswered_card_moves_on_once_the_card_is_answered(monkeypatch):
    _grader(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(approval_required=True, milestones=[{"name": "First"}, {"name": "Second", "due": PAST}])
    gid = g["goal_id"]
    first = _m(gid, 0)["milestone_id"]
    asked = goals.run_milestone(gid, first)
    assert asked["status"] == "pending_approval"

    goals.run_due_milestones()
    assert work.calls == 0
    assert _m(gid, 1)["hold"]["type"] == "approval_pending"
    assert _m(gid, 1)["hold"]["category"] == "external_wait"

    goals.approvals.decide(asked["approval_id"], "approve", decided_by="owner")
    assert work.calls == 1 and _m(gid, 0)["status"] == "done"
    tick = goals.run_due_milestones()
    # The hold is gone; the second step now asks on its own card (the goal
    # requires approval) instead of running unasked.
    assert not _m(gid, 1).get("hold")
    assert [p["status"] for p in tick["processed"]] == ["pending_approval"]
    assert work.calls == 1


def test_a_step_out_of_runtime_waits_until_the_owner_raises_the_minutes(monkeypatch):
    _grader(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(definition={"budget": {"mψ": 0, "money_cents": 0, "runtime_minutes": 1}})
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    goals._mutate_goal(gid, lambda x: x.update(spent_runtime_s=120))

    out = goals.run_milestone(gid, mid)
    goals.run_due_milestones()
    assert work.calls == 0
    assert out["status"] == "budget_exceeded"
    m = _m(gid)
    assert m["status"] == "blocked" and m["blocker"]["category"] == "budget_exhausted"
    assert m["blocker"]["type"] == "runtime"

    goals.update_goal(gid, {"budget": {"runtime_minutes": 60}})
    assert _m(gid)["status"] == "pending" and not _m(gid).get("blocker")
    goals.run_due_milestones()
    assert work.calls == 1 and _m(gid)["status"] == "done"


# ── NS-34.10-3 no-progress detection ─────────────────────────────────────────

def test_a_no_progress_stop_does_not_loop_on_later_ticks(monkeypatch):
    _grader(monkeypatch, passes=lambda intent: False)
    work = _Work(texts=lambda n: "the very same synthetic draft")
    goals.set_executor(work)
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    assert goals.run_milestone(gid, mid)["status"] == "no_progress"
    calls, attempts, receipts = work.calls, _m(gid)["attempts"], len(_kinds(gid))

    for _ in range(3):
        goals.run_due_milestones()
    assert work.calls == calls, "a no-progress step was tried again by the tick"
    assert _m(gid)["attempts"] == attempts and len(_kinds(gid)) == receipts
    assert _m(gid)["blocker"]["category"] == "no_progress"
    assert goals.get_goal(gid)["status"] == "needs_owner"
    row = _row(gid)
    assert row["category"] == "no_progress" and row["can_run"] is False
    assert row["can_restart"] is True and row["restart_needs_confirmation"] is True
    assert "I stopped working on" in row["waits"]


def test_the_same_failure_on_every_attempt_stops_as_no_progress(monkeypatch):
    """Attempts that fail the same way bring no new evidence: the step stops
    with a no-progress blocker after the repeat, not after spending every
    retry and escalating as a failed check."""
    _grader(monkeypatch)
    calls = {"n": 0}

    def failing(goal, milestone, *, critique_hint=""):
        calls["n"] += 1
        return {"ok": False, "text": "", "cost_mψ": 0, "error": "the synthetic tool is down"}
    goals.set_executor(failing)
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    out = goals.run_milestone(gid, mid)
    assert calls["n"] == 2, "an attempt identical to the last failure was dispatched again"
    assert out["status"] == "no_progress"
    m = _m(gid)
    assert m["status"] == "blocked" and m["blocker"]["category"] == "no_progress"
    assert "failed the same way" in m["blocker"]["text"]
    assert m["verification"]["status"] == "error"
    assert "verification_failed_escalated" not in _kinds(gid)
    assert goals.approvals.list_approvals(kind="goal_milestone_escalation") == []


@pytest.mark.parametrize("errors", [
    lambda n: f"the synthetic tool is down ({n})",      # a different failure each time
    lambda n: "",                                      # failures that say nothing
])
def test_failures_that_differ_or_say_nothing_still_get_every_attempt(monkeypatch, errors):
    """Only the same stated failure again is no progress: different failures,
    or failures with no detail, use every attempt and then ask the owner."""
    _grader(monkeypatch)
    calls = {"n": 0}

    def failing(goal, milestone, *, critique_hint=""):
        calls["n"] += 1
        return {"ok": False, "text": "", "cost_mψ": 0, "error": errors(calls["n"])}
    goals.set_executor(failing)
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    out = goals.run_milestone(gid, mid)
    assert calls["n"] == 3 and out["status"] == "escalated"
    assert _m(gid)["status"] == "escalated"
    assert len(goals.approvals.list_approvals(kind="goal_milestone_escalation")) == 1


# ── NS-34.10-6 plan revision and approval waiting ────────────────────────────

def test_a_plan_revision_is_recorded_only_when_the_plan_changes():
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    goals._update_milestone(gid, mid, progress_marks=[{"output": "abc", "critique": None}],
                            blocker_history=["run_failed"])
    goals.update_goal(gid, {"max_repair_retries": 5})
    goals.update_goal(gid, {"success_criteria": "a synthetic draft exists"})   # same value
    assert goals.get_goal(gid).get("plan_revised_at") is None
    assert _m(gid)["progress_marks"]

    before = time.time()
    goals.update_goal(gid, {"success_criteria": "a shorter synthetic draft"})
    revised = goals.get_goal(gid)["plan_revised_at"]
    assert isinstance(revised, float) and revised >= before
    assert _m(gid)["progress_marks"] == [] and _m(gid)["blocker_history"] == []


def test_a_step_stopped_by_a_plan_change_continues_on_the_new_plan(monkeypatch):
    seen = _grader(monkeypatch, passes=lambda intent: "different" in intent)
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]

    def work(goal, milestone, *, critique_hint=""):
        work.calls += 1
        if work.calls == 1:
            time.sleep(0.05)
            goals.update_goal(gid, {"success_criteria": "a different synthetic draft"})
        return {"ok": True, "text": f"synthetic draft {work.calls}", "cost_mψ": 10}
    work.calls = 0
    goals.set_executor(work)
    started = time.time()
    assert goals.run_milestone(gid, mid)["status"] == "plan_changed"
    assert goals.get_goal(gid)["plan_revised_at"] > started
    assert work.calls == 1 and _m(gid)["blocker"]["type"] == "plan_changed"

    out = goals.restart_milestone(gid, mid, confirm_unknown_outcome=True)
    assert out["ok"] is True and out["run"]["status"] == "done"
    assert seen[-1].startswith("a different synthetic draft"), "the old plan was graded after the change"
    assert _m(gid)["verification"]["passed"] is True


def test_after_a_no_progress_stop_a_revised_plan_runs_only_on_the_owner_s_restart(monkeypatch):
    """A revision is recorded and clears the earlier results, but the stopped
    step's attempts did run: it goes on only when the owner starts it again,
    and then on the revised plan, without the old results counting."""
    _grader(monkeypatch, passes=lambda intent: "relaxed" in intent)
    work = _Work(texts=lambda n: "the very same synthetic draft")
    goals.set_executor(work)
    g = _goal()
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    assert goals.run_milestone(gid, mid)["status"] == "no_progress"
    assert "after a change, start it again" in _m(gid)["blocker"]["text"]
    before = work.calls

    goals.update_goal(gid, {"success_criteria": "a relaxed synthetic draft"})
    goals.run_due_milestones()
    assert work.calls == before, "a plan edit alone ran a step whose attempts had already run"
    assert _m(gid)["status"] == "blocked" and _m(gid)["progress_marks"] == []

    out = goals.restart_milestone(gid, mid, confirm_unknown_outcome=True)   # runs it
    assert out["ok"] and out["run"]["status"] == "done"
    assert work.calls > before and _m(gid)["status"] == "done"


def test_a_step_waiting_on_its_card_neither_runs_nor_completes_while_it_waits(monkeypatch):
    _grader(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(approval_required=True)
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    first = goals.run_due_milestones()
    assert [p["status"] for p in first["processed"]] == ["pending_approval"]
    approval_id = _m(gid)["approval_id"]

    for _ in range(3):
        goals.run_due_milestones()
    again = goals.run_milestone(gid, mid)
    assert again["status"] == "pending_approval" and again["approval_id"] == approval_id
    assert work.calls == 0
    m = _m(gid)
    assert m["status"] == "blocked" and m["done_at"] is None
    assert m["blocker"]["type"] == "approval_pending" and m["blocker"]["category"] == "external_wait"
    assert "milestone_complete" not in _kinds(gid)
    assert goals.get_goal(gid)["status"] == "waiting"
    assert len(goals.approvals.list_approvals(kind="goal_milestone")) == 1, "a second card was raised"

    goals.approvals.decide(approval_id, "approve", decided_by="owner")
    assert work.calls == 1 and _m(gid)["status"] == "done"


# ── NS-34.10-7 evidence first, receipts first, manual completion labelled ─────

class _Run:
    """A synthetic run that leaves a record (journal, ledger, files)."""

    def __init__(self, tmp_path, *, write=None, text="Done: saved notes.md", extra=None):
        self.tmp, self.write, self.text, self.extra = tmp_path, write, text, dict(extra or {})
        self.calls = 0

    def __call__(self, goal, milestone, *, critique_hint=""):
        from agent_friday.services import task_ledger as tl
        self.calls += 1
        tid = milestone["_task_id"]
        tl.ensure(tid, "synthetic")
        if self.write:
            path = self.tmp / self.write
            path.write_text("synthetic", encoding="utf-8")
            tl.note_tool(tid, "write_file", {"path": str(path)}, "ok", wrote=True)
        return dict({"ok": True, "text": self.text, "cost_mψ": 1, "task_id": tid}, **self.extra)


def test_the_models_own_claim_of_success_does_not_complete_a_step(tmp_path, monkeypatch):
    graded = _grader(monkeypatch)                          # would pass anything it saw
    run = _Run(tmp_path, text="I emailed the plan. It was sent and verified; all criteria met.",
               extra={"passed": True, "verified": True,
                      "verification": {"status": "ok", "passed": True, "score": 1.0}})
    goals.set_executor(run)
    # A plainly internal step name, so the Q3 gate lets it run and the check
    # (not a card) is what is exercised.
    g = _goal(milestones=[{"name": "Write it", "due": PAST,
                           "evidence": [{"kind": "message_sent", "what": "the plan emailed"}]}])
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    out = goals.run_milestone(gid, mid)
    assert out["status"] != "done"
    m = _m(gid)
    assert m["status"] != "done" and m["done_at"] is None
    assert m["verification"]["passed"] is False
    assert "no outward action went through with a signed receipt" in m["verification"]["critique"]
    assert graded == [], "the words were graded though the record lacked the proof"
    assert "milestone_complete" not in _kinds(gid)
    assert goals.get_goal(gid)["status"] not in goal_state.MET


def test_the_record_is_read_before_the_words_are_graded(tmp_path, monkeypatch):
    from agent_friday.services import goal_evidence as ge
    order = []
    real_collect = ge.collect

    def collect(*a, **k):
        order.append("record")
        return real_collect(*a, **k)
    monkeypatch.setattr(ge, "collect", collect)

    def grade(content, intent, **kw):
        order.append("words")
        return {"status": "ok", "passed": True, "score": 0.9, "threshold": 0.7, "critique": "", "suggestions": ""}
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", grade)
    goals.set_executor(_Run(tmp_path, write="notes.md"))
    g = _goal(milestones=[{"name": "Write notes", "due": PAST,
                           "evidence": [{"kind": "file", "what": "notes.md"}]}])
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    assert goals.run_milestone(gid, mid)["status"] == "done"
    assert order == ["record", "words"]
    v = _m(gid)["verification"]
    assert v["basis"] == "receipts+text"
    assert v["conditions"][0]["met"] is True and v["conditions"][0]["cites"][0]["path"].endswith("notes.md")
    receipt = goals.get_goal(gid)["receipts"][-1]
    assert receipt["kind"] == "milestone_complete" and receipt["verified"]["passed"] is True


def test_a_goal_whose_own_proof_is_on_record_is_met_and_verified(tmp_path, monkeypatch):
    _grader(monkeypatch)
    goals.set_executor(_Run(tmp_path, write="notes.md"))
    g = goals.create_goal(title="Synthetic", status="active", success_criteria="notes exist",
                          milestones=[{"name": "Write notes", "due": PAST,
                                       "evidence": [{"kind": "file", "what": "notes.md"}]}],
                          definition={"evidence_required": [{"kind": "file", "what": "notes.md"}]})
    gid = g["goal_id"]
    assert goals.run_milestone(gid, _m(gid)["milestone_id"])["status"] == "done"
    after = goals.get_goal(gid)
    assert after["status"] == "met_verified"
    assert after["completion"]["by"] == "verification" and after["completion"]["verified"] is True
    assert after["goal_check"]["met"] is True and after["goal_check"]["conditions"][0]["met"] is True


def test_a_manually_confirmed_step_is_receipted_and_its_goal_is_labelled_unverified(monkeypatch):
    seen = _grader(monkeypatch)
    work = _Work()
    goals.set_executor(work)
    g = _goal(verification_mode="manual")
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    first = goals.run_due_milestones()
    assert [p["status"] for p in first["processed"]] == ["pending_manual_confirmation"]

    # While the owner has not confirmed, nothing more runs and nothing completes.
    for _ in range(2):
        goals.run_due_milestones()
    assert goals.run_milestone(gid, mid)["status"] == "waits_on_card"
    assert work.calls == 1 and seen == [], "manual mode graded the result itself"
    m = _m(gid)
    assert m["status"] == "verifying" and m["done_at"] is None
    assert goals.get_goal(gid)["status"] == "waiting"

    goals.approvals.decide(m["approval_id"], "approve", decided_by="owner")
    m = _m(gid)
    assert m["status"] == "done" and m["verification"]["status"] == "manual"
    receipt = goals.get_goal(gid)["receipts"][-1]
    assert receipt["kind"] == "milestone_manual_confirmed"
    assert goals.verify_receipt(receipt) is True
    after = goals.get_goal(gid)
    assert after["status"] == "met_unverified"
    assert after["completion"]["verified"] is False
    s = goals.summary(after)
    assert s["state_words"] == UNVERIFIED_WORDS
    assert s["milestones"][0]["verified"] is False


def test_a_goal_the_owner_marks_met_is_labelled_apart_from_a_verified_one(monkeypatch):
    owner = _goal(milestones=[{"name": "Draft"}])
    marked = goals.transition_goal(owner["goal_id"], "met_unverified", reason="good enough for me")
    assert marked["status"] == "met_unverified"
    assert marked["completion"]["by"] == "owner" and marked["completion"]["verified"] is False
    assert marked["history"][-1]["by"] == "owner"
    owner_words = goals.summary(marked)["state_words"]
    assert owner_words == UNVERIFIED_WORDS

    _grader(monkeypatch)
    goals.set_executor(_Work())
    checked = _goal()
    goals.run_milestone(checked["goal_id"], _m(checked["goal_id"])["milestone_id"])
    verified = goals.get_goal(checked["goal_id"])
    assert verified["status"] == "met_verified"
    assert goals.summary(verified)["state_words"] != owner_words
    with pytest.raises(ValueError):
        goals.transition_goal(owner["goal_id"], "met_verified")
