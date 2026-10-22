"""Goal checking on receipts (north star §17.12; BD-12 slice G7): a milestone
is checked against its run's own record (tool calls that went through,
signed receipts that allowed an outward action, files it wrote) before its
words, and the verdict cites what met each condition.
Synthetic fixtures; nothing reaches a model."""
from __future__ import annotations

import json
import os
import time

import pytest

from agent_friday.services import goals

PAST = "2020-01-01T00:00:00Z"
KEY = b"k" * 32


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


@pytest.fixture
def graded(monkeypatch):
    calls = []

    def grade(content, intent, **kw):
        calls.append(intent)
        return {"status": "ok", "passed": True, "score": 0.9, "threshold": 0.7, "critique": "", "suggestions": ""}
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", grade)
    return calls


def _signed(task_id, tool, decision="allow", klass="outward"):
    from agent_friday.governance import action_gate as ag
    ag._receipt({"tool": tool, "class": klass, "decision": decision, "task_id": task_id})


class _Run:
    """A synthetic milestone run that leaves a record."""

    def __init__(self, tmp_path, *, write=None, calls=(), receipts=(), text="Done: saved itinerary.md"):
        self.tmp, self.write, self.calls, self.receipts, self.text = tmp_path, write, calls, receipts, text

    def __call__(self, goal, milestone, *, critique_hint=""):
        from agent_friday.services import task_journal as tj
        from agent_friday.services import task_ledger as tl
        tid = milestone["_task_id"]
        tl.ensure(tid, "synthetic")
        for name, ok, summary in self.calls:
            tj.append(tid, "tool_call", name=name, ok=ok, result_summary=summary)
        for tool, decision, *klass in self.receipts:
            _signed(tid, tool, decision, *(klass or ["outward"]))
        if self.write:
            path = self.tmp / self.write
            path.write_text("synthetic", encoding="utf-8")
            tl.note_tool(tid, "write_file", {"path": str(path)}, "ok", wrote=True)
        return {"ok": True, "text": self.text, "cost_mψ": 1, "task_id": tid}


def _goal(evidence, **kw):
    return goals.create_goal(title="Synthetic", status="active", success_criteria="a synthetic itinerary exists",
                             milestones=[{"name": "Write it", "due": PAST, "evidence": evidence}], **kw)


def _run(g):
    return goals.run_milestone(g["goal_id"], g["milestones"][0]["milestone_id"])


def _v(g):
    return goals.get_goal(g["goal_id"])["milestones"][0]["verification"]


def test_a_file_the_run_wrote_meets_the_proof_then_the_words_are_graded(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, write="itinerary.md"))
    g = _goal([{"kind": "file", "what": "itinerary.md in Documents"}])
    assert _run(g)["status"] == "done"
    v = _v(g)
    assert v["basis"] == "receipts+text" and v["passed"]
    assert v["conditions"][0]["met"] and v["conditions"][0]["cites"][0]["path"].endswith("itinerary.md")
    assert len(graded) == 1, "the success criteria were not graded once the record held"


def test_saying_it_saved_a_file_is_not_proof_and_the_words_are_not_graded(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, write=None, text="I saved itinerary.md, all done!"))
    g = _goal([{"kind": "file", "what": "itinerary.md"}])
    assert _run(g)["status"] != "done"
    v = _v(g)
    assert v["passed"] is False and "no file named itinerary.md was written by the run" in v["critique"]
    assert graded == []


def test_a_file_the_run_only_read_does_not_count(tmp_path, graded):
    busy = tmp_path / "itinerary.md"

    class _Reader(_Run):
        def __call__(self, goal, milestone, *, critique_hint=""):
            from agent_friday.services import task_ledger as tl
            busy.write_text("fresh but not written by the run", encoding="utf-8")
            tl.ensure(milestone["_task_id"], "synthetic")
            tl.note_tool(milestone["_task_id"], "read_file", {"path": str(busy)}, "contents")
            return {"ok": True, "text": self.text, "cost_mψ": 1, "task_id": milestone["_task_id"]}
    goals.set_executor(_Reader(tmp_path))
    g = _goal([{"kind": "file", "what": "itinerary.md"}])
    assert _run(g)["status"] != "done"


def test_a_file_from_before_the_run_does_not_count(tmp_path, graded):
    old = tmp_path / "itinerary.md"

    class _Old(_Run):
        def __call__(self, goal, milestone, *, critique_hint=""):
            from agent_friday.services import task_ledger as tl
            old.write_text("old", encoding="utf-8")
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            tl.ensure(milestone["_task_id"], "synthetic")
            tl.note_tool(milestone["_task_id"], "write_file", {"path": str(old)}, "ok", wrote=True)
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            return {"ok": True, "text": self.text, "cost_mψ": 1, "task_id": milestone["_task_id"]}
    goals.set_executor(_Old(tmp_path))
    g = _goal([{"kind": "file", "what": "itinerary.md"}])
    assert _run(g)["status"] != "done"


def test_a_send_needs_the_call_to_go_through_and_a_receipt_that_allowed_it(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, calls=[("send_email", True, "sent 123")], receipts=[("send_email", "allow")]))
    g = _goal([{"kind": "message_sent", "what": "the plan emailed"}])
    assert _run(g)["status"] == "done"
    for calls, receipts in (([("send_email", True, "sent")], [("send_email", "card")]),      # card, not allowed
                            ([("send_email", False, "[APPROVAL CARD RAISED]")], [("send_email", "allow")]),
                            ([("read_message", True, "a message")], [("read_message", "allow", "internal")])):
        goals.set_executor(_Run(tmp_path, calls=calls, receipts=receipts))
        g2 = _goal([{"kind": "message_sent", "what": "the plan emailed"}])
        assert _run(g2)["status"] != "done", (calls, receipts)


def test_a_card_raised_or_an_unknown_tool_is_journaled_as_not_through(tmp_path, monkeypatch):
    """The real record path: whether a call went through is Friday's reading
    of its result, never the model's."""
    from agent_friday.services import agent
    from agent_friday.services import task_journal as tj
    tid = "goal-synthetic-journal"
    monkeypatch.setattr(tj, "current_task", lambda: tid)
    agent._orb_tool_trace(None, "send_email", {}, "[APPROVAL CARD RAISED] waits for you", 1)
    agent._orb_tool_trace(None, "send_confirmation", {}, "TOOL CALL FAILED: unknown tool", 1)
    agent._orb_tool_trace(None, "search_web", {"query": "x"}, "three results", 1)
    oks = {e["name"]: e.get("ok") for e in tj.read(tid) if e.get("kind") == "tool_call"}
    assert oks == {"send_email": False, "send_confirmation": False, "search_web": True}


def test_a_denied_or_forged_receipt_is_not_proof(tmp_path, graded):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import goal_evidence as ge
    _signed("goal-synthetic-1", "send_email", "deny")
    ev = ge.collect(["goal-synthetic-1"], None)
    assert ge.judge({"kind": "receipt", "what": "a receipt"}, ev, "")["met"] is False
    _signed("goal-synthetic-1", "send_email", "allow")
    assert ge.judge({"kind": "receipt", "what": "a receipt"}, ge.collect(["goal-synthetic-1"], None), "")["met"]
    p = ag.receipts_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    row["tool"] = "send_money"
    lines[-1] = json.dumps(row)
    lines[0] = json.dumps(dict(json.loads(lines[0]), decision="allow"))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ge.judge({"kind": "receipt", "what": "a receipt"}, ge.collect(["goal-synthetic-1"], None), "")["met"] is False


def test_a_goal_with_no_typed_proof_is_graded_on_words_and_says_so(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, calls=[("read_file", True, "x")]))
    g = _goal([])
    assert _run(g)["status"] == "done"
    v = _v(g)
    assert v["basis"] == "text" and v["record"]["tool_calls"] == 1 and len(graded) == 1


def test_the_goals_card_shows_what_the_check_rested_on(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, write="itinerary.md"))
    g = _goal([{"kind": "file", "what": "itinerary.md"}])
    _run(g)
    row = goals.summary(goals.get_goal(g["goal_id"]))["milestones"][0]
    assert row["check"] and row["check"][0].startswith("Met: itinerary.md (on record:")


def test_proof_only_the_owner_can_give_never_makes_the_goal_verified(tmp_path, graded):
    goals.set_executor(_Run(tmp_path, write="itinerary.md"))
    g = _goal([{"kind": "file", "what": "itinerary.md"}, {"kind": "owner_confirms", "what": "I like it"}])
    assert _run(g)["status"] == "done"
    assert goals.get_goal(g["goal_id"])["status"] == "met_unverified"


def test_the_goals_own_proof_is_judged_at_completion(tmp_path, graded):
    from agent_friday.services import goal_definition  # noqa: F401  (schema-2 goal)
    goals.set_executor(_Run(tmp_path, write="notes.md"))
    g = goals.create_goal(title="Synthetic", status="active", success_criteria="x",
                          milestones=[{"name": "Write it", "due": PAST, "evidence": [{"kind": "file", "what": "notes.md"}]}],
                          definition={"evidence_required": [{"kind": "file", "what": "final-report.pdf"}]})
    assert _run(g)["status"] == "done"
    after = goals.get_goal(g["goal_id"])
    assert after["status"] == "met_unverified", "met as verified though its own proof is missing"
    assert after["goal_check"]["conditions"][0]["met"] is False


def test_a_check_that_cannot_read_its_record_says_so(tmp_path, graded, monkeypatch):
    from agent_friday.services import goal_evidence as ge

    def broken(task_id):
        raise ge._Unreadable("the run's journal (synthetic)")
    monkeypatch.setattr(ge, "_journal_calls", broken)
    goals.set_executor(_Run(tmp_path))
    g = _goal([{"kind": "message_sent", "what": "the plan emailed"}])
    _run(g)
    v = _v(g)
    assert v["status"] == "skipped" and "could not read the run's journal" in v["critique"]
