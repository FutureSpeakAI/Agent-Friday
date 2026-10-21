"""A task or chat turn that raised an approval card is WAITING for the owner, never complete.

BD-11a, NS-17.7-2 ("no completed state while an approval is pending"), NS-6.4-5, NS-6.5-1,
NS-21.23-2, NS-22.3-2. Before: a card raised beside one successful read made the task
"complete", the chat line, panel card and spoken notice said "finished", a workflow went on
to its next step as if the action had happened, a restart could treat the task as unfinished
work, and the chat turn's record said complete.

Now:
  * the evidence gate gives 'waiting_approval' whenever a card was raised;
  * task_journal counts it terminal (a restart never runs it again);
  * the notice says it is waiting for the owner's decision, in every channel;
  * a workflow holds at that step; the run's state is 'waiting';
  * the owner's decision on the last card settles it: approved and ran -> complete (and the
    workflow continues), approved but failed -> failed, declined -> 'declined';
  * a chat turn that raised a card is recorded as waiting, and its reply says so (`outcome`).
Synthetic tasks and cards only.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent

RAISED = "[APPROVAL CARD RAISED] 'send_email' was NOT executed. Sending it needs the owner's approval."


def _t(name, result="ok: done"):
    return {"name": name, "input": {}, "result": result}


def test_a_card_makes_the_task_wait_whatever_else_ran():
    for trace in ([_t("send_email", RAISED)],
                  [_t("read_file", "text"), _t("send_email", RAISED)],
                  [_t("search_web", "results"), _t("write_file", "Wrote 3 lines"), _t("send_email", RAISED)]):
        verified, summary, status = agent._evidence_verdict(trace)
        assert status == "waiting_approval" and verified is False, trace
        assert "waiting for your decision" in summary and "send_email" in summary


def test_a_waiting_task_is_finished_work_for_the_journal():
    from agent_friday.services import task_journal
    assert task_journal.is_terminal("waiting_approval")
    assert task_journal.is_terminal("declined")
    assert not str("waiting_approval").startswith("complete"), "nothing reads it as a success"


@pytest.fixture
def task(monkeypatch):
    """One background task record, its notices captured instead of delivered."""
    tid = "task-wait-1"
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"task_id": tid, "name": "Book the room", "status": "running", "log": []}
    said = []
    from agent_friday.services import notice_registry as nreg
    monkeypatch.setattr(nreg, "deliver_completion", lambda cid, text=None, **kw: said.append(text))
    monkeypatch.setattr(agent, "_task_conversation_id", lambda task_id: "c1", raising=False)
    yield tid, said
    with agent.TASKS_LOCK:
        agent.TASKS.pop(tid, None)


def test_the_notice_says_waiting_not_finished(task):
    tid, said = task
    agent._report_task_completion(tid, "Book the room", "waiting_approval", "A card is waiting for you.")
    assert said and "waiting for your decision" in said[0]
    assert "finished" not in said[0].split("\n")[0].lower()


def _cards(monkeypatch, rows):
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "list_approvals", lambda *a, **k: [dict(r) for r in rows])


def _card(status, *, ok=None, task_id="task-wait-1", tool="send_email"):
    row = {"approval_id": "ap-" + status, "status": status, "payload": {"task_id": task_id, "tool": tool}}
    if ok is not None:
        row.update(consumed=True, used_detail={"ok": ok})
    return row


@pytest.mark.parametrize("rows,want", [
    ([_card("approved", ok=True)], "complete"),
    ([_card("approved", ok=False)], "failed"),
    ([_card("denied")], "declined"),
    ([_card("expired")], "declined"),
], ids=["approved-ran", "approved-failed", "declined", "expired"])
def test_the_owners_decision_settles_the_waiting_task(task, monkeypatch, rows, want):
    tid, _said = task
    with agent.TASKS_LOCK:
        agent.TASKS[tid]["status"] = "waiting_approval"
    _cards(monkeypatch, rows)
    advanced = []
    monkeypatch.setattr(agent, "_advance_task_chain", lambda task_id, result: advanced.append(task_id))
    assert agent.settle_waiting_task(tid) == want
    with agent.TASKS_LOCK:
        rec = dict(agent.TASKS[tid])
    assert rec["status"] == want
    assert rec["verified"] is (want == "complete")
    assert advanced == ([tid] if want == "complete" else [])


def test_a_task_with_a_card_still_pending_stays_waiting(task, monkeypatch):
    tid, _said = task
    with agent.TASKS_LOCK:
        agent.TASKS[tid]["status"] = "waiting_approval"
    _cards(monkeypatch, [_card("approved", ok=True), dict(_card("pending"), approval_id="ap-2")])
    assert agent.settle_waiting_task(tid) is None
    _cards(monkeypatch, [{"approval_id": "ap-3", "status": "approved", "payload": {"task_id": tid}}])
    assert agent.settle_waiting_task(tid) is None, "an approved card still running is not settled"
    with agent.TASKS_LOCK:
        assert agent.TASKS[tid]["status"] == "waiting_approval"


def test_the_card_decision_settles_its_task(monkeypatch):
    from agent_friday.services import approval_executor
    settled = []
    monkeypatch.setattr(agent, "settle_waiting_task", lambda task_id: settled.append(task_id))
    approval_executor._on_decision({"approval_id": "ap-x", "kind": "tainted_action", "status": "denied",
                                    "payload": {"tool": "send_email", "input": {}, "task_id": "task-9"}})
    assert settled == ["task-9"]


def test_a_workflow_holds_at_a_waiting_step(monkeypatch):
    tid = "wf-step-1"
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"task_id": tid, "name": "step 1", "status": "waiting_approval", "chain": "morning-run",
                            "chain_step": 0, "log": [],
                            "workflow_definition": {"steps": [{"name": "a", "prompt": "x"}, {"name": "b", "prompt": "y"}]}}
    spawned = []
    monkeypatch.setattr(agent, "_spawn_task", lambda *a, **k: spawned.append(a) or "child", raising=False)
    try:
        assert agent._advance_task_chain_once(tid, "A card is waiting.") is None
        assert spawned == [], "the next step started while the card was waiting"
    finally:
        with agent.TASKS_LOCK:
            agent.TASKS.pop(tid, None)


def test_a_chat_turn_that_raised_a_card_is_waiting():
    from agent_friday.routes import chat
    from agent_friday.services import reasoning_trace as rt
    tid = rt.start("chat", "turn", parent_id=None)
    try:
        with rt.activate(tid):
            rt.tool_finished("read_file", {}, "text", ok=True)
            rt.tool_finished("send_email", {}, RAISED, ok=False)
        assert chat._turn_outcome(tid) == "waiting_approval"
    finally:
        rt.finish(tid, "complete")
    other = rt.start("chat", "turn", parent_id=None)
    try:
        with rt.activate(other):
            rt.tool_finished("read_file", {}, "text", ok=True)
        assert chat._turn_outcome(other) == "complete"
    finally:
        rt.finish(other, "complete")
