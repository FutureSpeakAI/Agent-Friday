"""Goal checking on receipts (north star §17.12; BD-12 slice G7): a milestone
is checked against its run's own record (tool calls, signed receipts, files
on disk) before its words, and the verdict cites what met each condition.
Synthetic fixtures; nothing reaches a model."""
from __future__ import annotations

import os
import time

import pytest

from agent_friday.services import goals

PAST = "2020-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
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
    yield
    goals.set_executor(None)


@pytest.fixture
def graded(monkeypatch):
    calls = []

    def grade(content, intent, **kw):
        calls.append(intent)
        return {"status": "ok", "passed": True, "score": 0.9, "threshold": 0.7, "critique": "", "suggestions": ""}
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", grade)
    return calls


class _Run:
    """A synthetic milestone run that leaves a record: tool calls in its
    task's journal and, optionally, a file it wrote."""

    def __init__(self, tmp_path, *, write=None, calls=(), text="Done: saved itinerary.md"):
        self.tmp, self.write, self.calls, self.text = tmp_path, write, calls, text

    def __call__(self, goal, milestone, *, critique_hint=""):
        from agent_friday.services import task_journal as tj
        from agent_friday.services import task_ledger as tl
        tid = milestone["_task_id"]
        for name, ok, summary in self.calls:
            tj.append(tid, "tool_call", name=name, ok=ok, result_summary=summary)
        if self.write:
            path = self.tmp / self.write
            path.write_text("synthetic", encoding="utf-8")
            led = tl.ensure(tid, "synthetic")
            led["files"].append(str(path))
            tl.save(tid, led)
        return {"ok": True, "text": self.text, "cost_mψ": 1, "task_id": tid}


def _goal(evidence):
    return goals.create_goal(title="Synthetic", status="active", success_criteria="a synthetic itinerary exists",
                             milestones=[{"name": "Write it", "due": PAST, "evidence": evidence}])


def _run(g):
    return goals.run_milestone(g["goal_id"], g["milestones"][0]["milestone_id"])


def test_a_file_on_disk_meets_the_proof_and_the_words_are_not_graded(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, write="itinerary.md", calls=[("write_file", True, "wrote itinerary.md")]))
    g = _goal([{"kind": "file", "what": "itinerary.md in Documents"}])
    out = _run(g)
    assert out["status"] == "done", out
    v = goals.get_goal(g["goal_id"])["milestones"][0]["verification"]
    assert v["basis"] == "receipts" and v["passed"] and v["score"] == 1.0
    assert v["conditions"][0]["met"] and v["conditions"][0]["cites"][0]["path"].endswith("itinerary.md")
    assert graded == [], "the words were graded though the record answered"


def test_saying_it_saved_a_file_is_not_proof(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, write=None, text="I saved itinerary.md, all done!"))
    g = _goal([{"kind": "file", "what": "itinerary.md"}])
    out = _run(g)
    assert out["status"] != "done"
    v = goals.get_goal(g["goal_id"])["milestones"][0]["verification"]
    assert v["passed"] is False and "no file named itinerary.md" in v["critique"]
    assert graded == []


def test_a_file_from_before_the_run_does_not_count(tmp_path, graded):
    old = tmp_path / "itinerary.md"
    old.write_text("old", encoding="utf-8")
    os.utime(old, (time.time() - 3600, time.time() - 3600))

    class _Old(_Run):
        def __call__(self, goal, milestone, *, critique_hint=""):
            from agent_friday.services import task_ledger as tl
            led = tl.ensure(milestone["_task_id"], "synthetic")
            led["files"].append(str(old))
            tl.save(milestone["_task_id"], led)
            return {"ok": True, "text": self.text, "cost_mψ": 1, "task_id": milestone["_task_id"]}
    goals.set_executor(_Old(tmp_path))
    g = _goal([{"kind": "file", "what": "itinerary.md"}])
    assert _run(g)["status"] != "done"


def test_a_send_that_went_through_meets_message_sent(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, calls=[("send_email", True, "sent message 123")]))
    g = _goal([{"kind": "message_sent", "what": "the plan emailed to a synthetic friend"}])
    assert _run(g)["status"] == "done"
    goals.set_executor(_Run(tmp_path, calls=[("send_email", False, "refused")]))
    g2 = _goal([{"kind": "message_sent", "what": "the plan emailed"}])
    assert _run(g2)["status"] != "done"


def test_words_are_graded_only_after_the_record_holds(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, write="itinerary.md"))
    g = _goal([{"kind": "file", "what": "itinerary.md"}, {"kind": "text_check", "what": "it covers both days"}])
    assert _run(g)["status"] == "done"
    assert len(graded) == 1 and "Also: it covers both days" in graded[0]
    v = goals.get_goal(g["goal_id"])["milestones"][0]["verification"]
    assert v["basis"] == "receipts+text"


def test_a_goal_with_no_typed_proof_is_graded_on_words_and_says_so(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, calls=[("read_file", True, "x")]))
    g = _goal([])
    assert _run(g)["status"] == "done"
    v = goals.get_goal(g["goal_id"])["milestones"][0]["verification"]
    assert v["basis"] == "text" and v["record"]["tool_calls"] == 1 and len(graded) == 1


def test_the_goals_card_shows_what_the_check_rested_on(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, write="itinerary.md"))
    g = _goal([{"kind": "file", "what": "itinerary.md"}])
    _run(g)
    row = goals.summary(goals.get_goal(g["goal_id"]))["milestones"][0]
    assert row["check"] and row["check"][0].startswith("Met: itinerary.md (on record:")


def test_a_signed_receipt_counts_and_a_tampered_one_does_not(tmp_path, graded, monkeypatch):
    import json
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import goal_evidence as ge
    key = b"k" * 32
    monkeypatch.setattr(ag, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(ag, "_governance_key", lambda: key)
    ag.authorize("write_file", {}, {"is_background_task": True, "task_id": "goal-synthetic-1"})
    ev = ge.collect("goal-synthetic-1")
    assert ge.judge({"kind": "receipt", "what": "a receipt"}, ev, "")["met"] is True
    p = ag.receipts_path()
    row = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    row["decision"] = "allow-forged"
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert ge.judge({"kind": "receipt", "what": "a receipt"}, ge.collect("goal-synthetic-1"), "")["met"] is False
