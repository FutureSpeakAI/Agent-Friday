"""The cumulative token budget must never kill a live turn (2026-09-22).

This is the seam that actually broke, not the unit underneath it. On
2026-09-22 two Sonnet chat turns died inside ``_call_claude_agent`` because
``prompt_cache.task_budget.charge`` raised from the egress chokepoint
(friday.log:53032, :56076). The user saw ``[Friday offline] '6 am and 4 pm,
but only ' has sent 4,090,829 input tokens ... The task is stopped rather
than billed further``.

Walking ``~/.friday/costs.db`` back from that kill: 25 calls over five
minutes, 4,090,886 tokens presented — the counter was accurate to 57 tokens
— of which 3,945,193 were CACHE READS billed at 0.1x. The turn cost $3.14.

So these tests drive the real loop, over the real chokepoint, with the real
setting, and assert the turn finishes. A unit test on ``charge`` alone would
not have caught the raise escaping through ``_seal_or_block``.
"""

import types

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import prompt_cache as pc


@pytest.fixture
def tiny_budget(monkeypatch):
    """A budget so small that any real call blows it on the first send."""
    monkeypatch.setattr(pc, "_settings",
                        lambda: {"max_task_input_tokens": 500})
    pushed = []
    import agent_friday.notifications_engine as _ne
    monkeypatch.setattr(_ne, "push", lambda **kw: pushed.append(kw) or {})
    return pushed


def _client(monkeypatch, rounds):
    """Fake Anthropic: ``rounds`` tool_use turns, then a final answer."""
    calls = {"n": 0}

    class _Msgs:
        def create(self, **kw):
            calls["n"] += 1
            txt = types.SimpleNamespace(type="text", text=f"step {calls['n']}")
            usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
            if calls["n"] > rounds:
                return types.SimpleNamespace(content=[txt],
                                             stop_reason="end_turn",
                                             usage=usage)
            blk = types.SimpleNamespace(type="tool_use", id=f"t{calls['n']}",
                                        name="search_web", input={"q": "x"})
            return types.SimpleNamespace(content=[txt, blk],
                                         stop_reason="tool_use", usage=usage)

    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=_Msgs()))
    return calls


def test_a_turn_past_its_token_budget_still_returns_its_answer(monkeypatch,
                                                               tiny_budget):
    """The exact failure Stephen hit: the turn must not come back as
    ``[Friday offline] ... The task is stopped``."""
    calls = _client(monkeypatch, rounds=0)
    text, _trace = ag._call_claude_agent(
        [{"role": "user", "content": "6 am and 4 pm, but only on weekdays"}],
        system="s" * 8000, orb_label="6 am and 4 pm, but only ")
    assert calls["n"] == 1
    assert text == "step 1"
    assert "stopped rather than billed further" not in text
    assert "offline" not in text.lower()


def test_the_loop_keeps_iterating_after_it_crosses(monkeypatch, tiny_budget):
    """Crossing on iteration 1 must not prevent iterations 2..N. The old
    implementation raised on EVERY charge after the first, so the turn could
    not survive its own tool calls."""
    calls = _client(monkeypatch, rounds=3)
    text, trace = ag._call_claude_agent(
        [{"role": "user", "content": "go"}],
        system="s" * 8000, orb_label="long turn")
    assert calls["n"] == 4, "three tool rounds plus the final answer"
    assert len(trace) == 3
    assert text == "step 4"


def test_crossing_notifies_the_user_exactly_once(monkeypatch, tiny_budget):
    """Stephen always knows what is happening — but a notification per
    iteration is noise, and this loop runs up to 999 of them."""
    _client(monkeypatch, rounds=3)
    ag._call_claude_agent([{"role": "user", "content": "go"}],
                          system="s" * 8000, orb_label="long turn")
    assert len(tiny_budget) == 1
    n = tiny_budget[0]
    assert n["kind"] == "budget_advisory"
    assert n["priority"] == "medium"          # advisory, not a halt
    assert "STILL RUNNING" in n["body"]
    assert "long turn" in n["body"]


def test_an_ordinary_turn_does_not_warn(monkeypatch):
    """A test that cannot pass for the wrong reason: with the default 4M
    budget the same turn must produce no advisory at all."""
    monkeypatch.setattr(pc, "_settings", lambda: {})
    pushed = []
    import agent_friday.notifications_engine as _ne
    monkeypatch.setattr(_ne, "push", lambda **kw: pushed.append(kw) or {})
    _client(monkeypatch, rounds=1)
    ag._call_claude_agent([{"role": "user", "content": "go"}],
                          system="s" * 8000, orb_label="short turn")
    assert pushed == []
