"""BD-11a / NS-32.21-1: a misnamed tool call is never resolved to a tool that acts outside.

Before: a call whose name was not registered ran the one registered tool that matched it
(`create-calendar-event` ran create_calendar_event, `zz.get.sender` ran zz_sender), whatever
that tool did. A model's slip of the name could send, buy or invite under a name nobody chose.
Now the guess is only taken when the matched tool is internal for these arguments
(governance/action_gate.classify); otherwise nothing runs and the answer names the tool, so
the model can call it by its exact name, and that call passes every gate as itself.
Synthetic tools only, plus one real outward tool name.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import agent


@pytest.fixture
def probes(monkeypatch):
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "zz_sender", lambda inp: ran.append("sender") or "sent")
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "zz_reader", lambda inp: ran.append("reader") or "read")
    real = ag.classify

    def classify(name, args, ctx=None):
        if name == "zz_sender":
            return ag.OUTWARD, "sends to someone"
        if name == "zz_reader":
            return ag.INTERNAL, "reads this PC"
        return real(name, args, ctx)
    monkeypatch.setattr(ag, "classify", classify)
    return ran


def test_a_misnamed_outward_tool_does_not_run(probes):
    out = agent._execute_tool("zz.get.sender", {}, session_ctx={"authenticated": True, "is_background_task": True})
    assert probes == [], "an outward tool ran under a guessed name"
    assert out.startswith("TOOL CALL FAILED") and "'zz_sender'" in out and "exact name" in out
    assert agent._tool_call_status(out) == "error"


def test_a_misnamed_internal_tool_may_still_resolve(probes):
    assert agent._resolution_may_run("zz_reader", {}) is True
    assert agent._resolution_may_run("zz_sender", {}) is False


def test_a_real_outward_tool_is_not_run_under_a_guessed_name(monkeypatch):
    assert agent._resolve_tool_name("create-calendar-event")[0] == "create_calendar_event"
    out = agent._execute_tool("create-calendar-event", {"title": "Lunch", "start": "2026-11-02T12:00"},
                              session_ctx={"authenticated": True, "is_background_task": True})
    assert out.startswith("TOOL CALL FAILED") and "'create_calendar_event'" in out


def test_a_classification_that_cannot_run_is_not_a_yes(monkeypatch):
    monkeypatch.setattr(ag, "classify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert agent._resolution_may_run("search_web", {"query": "x"}) is False
