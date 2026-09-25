"""A refusal from the router is no summary.

In local-only mode with no local model, `_generate_text` returned the
router's refusal text ("This request needs vault access, which requires a
local model...") as if it were generated text, and the local summarizer took
it as the summary: 113 of 153 chat messages were replaced by it. On the task
ledger path an unstructured "summary" also replaced every fact the ledger
held.
"""
from __future__ import annotations

import pytest

from agent_friday.services import compaction as comp
from agent_friday.services import model_router as mr
from agent_friday.services import task_ledger as tl

REFUSAL = ("This request needs vault access, which requires a local model. Load one "
           "in Settings -> Models, then retry.")


class _RefusingRouter:
    def route(self, messages, task_context=None):
        return {"refuse": True, "warning": REFUSAL, "provider": "local"}


@pytest.fixture
def refusing(monkeypatch):
    from agent_friday.routing import model_router as rmr
    monkeypatch.setattr(rmr, "get_router", lambda cfg=None: _RefusingRouter())
    try:
        from agent_friday.services import demo_mode
        monkeypatch.setattr(demo_mode, "is_demo", lambda: False)
    except ImportError:
        pass


def test_the_router_marks_its_refusal(refusing):
    out = mr._generate_text([{"role": "user", "content": "summarise"}])
    assert out == REFUSAL
    assert mr.is_refusal(out) is True
    assert mr.is_refusal("GOAL: keep going") is False


def test_the_local_summarizer_returns_no_summary_for_a_refusal(refusing):
    assert comp._default_summarizer("USER: a\nASSISTANT: b") == ""


def test_demo_mode_placeholder_is_no_summary(monkeypatch):
    from agent_friday.services import demo_mode
    monkeypatch.setattr(demo_mode, "is_demo", lambda: True)
    assert comp._default_summarizer("USER: a\nASSISTANT: b") == ""


def test_chat_history_is_not_replaced_by_a_refusal(refusing, monkeypatch):
    monkeypatch.setattr(mr, "_traj_char_limit_for", lambda model=None: 10_000)
    real, calls = comp._default_summarizer, []
    monkeypatch.setattr(comp, "_default_summarizer",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    msgs = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": "message %d " % i + "x" * 3000} for i in range(153)]
    out = mr._compress_trajectory(msgs)
    assert calls, "the summarizer was never consulted; the test proves nothing"
    assert all(REFUSAL not in str(m.get("content")) for m in out)
    assert out == msgs


def _round(i, size=2000):
    return [{"role": "assistant", "content": None, "tool_calls": [
                {"id": "c%d" % i, "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c%d" % i, "content": "fact-%d " % i + "x" * size}]


def test_the_ledger_keeps_its_facts_when_the_summarizer_refuses(refusing):
    led = {"goal": "g", "facts": ["batch 7 has 3 defects", "valve V-12 is stuck"], "done": []}
    convo = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for i in range(20):
        convo += _round(i)
    comp.maybe_compact(convo, window=6000, ledger=led, summarizer=comp._default_summarizer)
    assert led["facts"] == ["batch 7 has 3 defects", "valve V-12 is stuck"]
    assert all(REFUSAL not in str(f) for f in led["facts"])


def test_an_unstructured_summary_does_not_erase_the_ledger_facts():
    led = {"goal": "g", "facts": ["batch 7 has 3 defects"], "done": []}
    tl.absorb_summary(led, "Worked through the batches; nothing else to report.")
    assert "batch 7 has 3 defects" in led["facts"]
