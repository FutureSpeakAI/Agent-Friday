"""A task stopped by an expired or revoked connector sign-in (or a missing permission) waits for the
owner to reconnect, with a typed blocker; it is never 'complete' (NS-32.7-1).

Before: a Google call refused because the account's sign-in had expired was noted "not done", but
the task around it still ended complete (any other tool that ran verified it) or plainly failed,
and its notice said "finished". Now the call records that only the owner can fix it, the task ends
'needs_reconnect' (the journal never runs it again on its own; its state is waiting_for_input),
and its notice says to reconnect. Synthetic data only.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent
from agent_friday.services import connector_outcome as co


def test_a_task_that_hit_an_expired_sign_in_waits_for_a_reconnect():
    trace = [{"name": "read_file", "input": {}, "result": "text"},
             {"name": "create_calendar_event", "input": {}, "result": co.NOT_DONE + "reconnect",
              "needs_owner": [{"kind": "revoked", "service": "Google Calendar"}]}]
    verified, summary, status = agent._evidence_verdict(trace)
    assert status == "needs_reconnect" and verified is False
    assert "Google Calendar" in summary


def test_an_ordinary_failure_is_not_a_reconnect():
    trace = [{"name": "read_file", "input": {}, "result": "text"}]
    assert agent._evidence_verdict(trace)[2] == "complete"


@pytest.fixture
def probe(monkeypatch):
    box = {"kind": "revoked"}

    def handler(inp):
        co.not_done(box["kind"], "Google Calendar")
        return "it did not go through"
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "reconnect_probe_tool", handler)
    monkeypatch.setitem(agent.TOOL_RINGS, "reconnect_probe_tool", 0)
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "INTERNAL_TOOLS", action_gate.INTERNAL_TOOLS | {"reconnect_probe_tool"})
    return box


def test_the_call_hands_a_reconnect_reason_to_the_loop(probe):
    agent._LAST_NEEDS_OWNER.set(None)
    agent._execute_tool("reconnect_probe_tool", {})
    assert agent._take_needs_owner() == [{"kind": "revoked", "service": "Google Calendar"}]


def test_a_call_that_failed_for_another_reason_hands_nothing(probe):
    probe["kind"] = "unreachable"
    agent._LAST_NEEDS_OWNER.set(None)
    agent._execute_tool("reconnect_probe_tool", {})
    assert agent._take_needs_owner() == []


def test_the_state_is_waiting_for_the_owner_and_never_rerun():
    from agent_friday.services import task_journal, task_states
    assert task_states.derive({"status": "needs_reconnect"}) == "waiting_for_input"
    assert task_journal.is_terminal("needs_reconnect"), "a restart would run it again on its own"


def test_the_notice_says_to_reconnect_not_finished(monkeypatch):
    tid = "task-reconnect-1"
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"task_id": tid, "name": "Book the room", "status": "running", "log": []}
    said = []
    from agent_friday.services import notice_registry as nreg
    monkeypatch.setattr(nreg, "deliver_completion", lambda cid, text=None, **kw: said.append(text))
    monkeypatch.setattr(agent, "_task_conversation_id", lambda task_id: "c1", raising=False)
    try:
        agent._report_task_completion(tid, "Book the room", "needs_reconnect", "Google Calendar needs you.")
    finally:
        with agent.TASKS_LOCK:
            agent.TASKS.pop(tid, None)
    assert said and "reconnect" in said[0].split("\n")[0].lower()
    assert "finished" not in said[0].split("\n")[0].lower()
