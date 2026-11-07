"""A task's process row carries what its model calls cost (BD-16 C5; NS-29.1-1, NS-29.2-1:
"Every significant process registers ... cost ... receipt link").

core.process_update has taken cost_usd since C5, but no caller passed it, so every task orb read
$0.00 however much the turn spent. The agent loop already meters each round (cost_meter.meter);
it now hands that figure to the orb. These tests drive the real loop with a stand-in provider.
"""
from __future__ import annotations

import pytest

from agent_friday import core
from agent_friday.services import agent

PID = "p-cost-feed"


@pytest.fixture
def row():
    core.process_register(PID, name="Work")
    yield
    with core.PROCESSES_LOCK:
        core.PROCESSES.pop(PID, None)


def _answer(cost):
    return {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": cost}}


def _run(send, pid=PID):
    return agent._oai_agentic_loop_run([{"role": "user", "content": "hi"}], None, send,
                                       provider="openrouter", model="some/model", orb_id=pid,
                                       orb=lambda **kw: core.process_update(pid, **kw))


def _spent():
    with core.PROCESSES_LOCK:
        return core.PROCESSES[PID]["cost_usd"]


def test_a_metered_round_is_on_the_process_row(row):
    _run(lambda *a, **k: _answer(0.02))
    assert _spent() == pytest.approx(0.02), "the turn was metered but its row still reads $%s" % _spent()


def test_each_round_adds_to_the_row(row):
    calls = []

    def send(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            r = _answer(0.01)
            r["choices"][0]["message"] = {"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "no_such_tool", "arguments": "{}"}}]}
            r["choices"][0]["finish_reason"] = "tool_calls"
            return r
        return _answer(0.03)
    _run(send)
    assert len(calls) == 2
    assert _spent() == pytest.approx(0.04)


def test_a_free_local_round_leaves_the_row_at_zero(row):
    agent._oai_agentic_loop_run([{"role": "user", "content": "hi"}], None,
                                lambda *a, **k: _answer(0.0), provider="ollama", model="m",
                                seat="local", orb_id=PID,
                                orb=lambda **kw: core.process_update(PID, **kw))
    assert _spent() == 0.0
