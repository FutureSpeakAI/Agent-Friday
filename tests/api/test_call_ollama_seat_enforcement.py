"""Dispatch enforces NOTHING about which model may hold a seat (2026-08-15).

This file used to pin the opposite, in the live `_call_ollama` path: that a
"red" model was substituted with the last known green one, that a red model
with no fallback had its tools stripped entirely, and that a model which had
never been gated was refused the same as one that failed.

All of that is gone. Stephen, verbatim:

    "I absolutely want the user to be able to set any model they wish at any
     seat they wish, so this is non-negotiable."

and, on the gate itself:

    "This is not any kind of an industry standard practice. If the model is
     not emitting the tool calls, we are not prompting it correctly."

The evidence agreed with him twice over. The structural failures the gate
fired on were a broken harness — the same models scored 1/10 and 0/10, then
10/10 each once the harness was fixed. And on 2026-08-15 a dependent 5-call
tool chain scored 0/5 across every local model because of a type check in our
own argument parsing, not because of anything the models did; once fixed they
scored 15/15. "The model can't use tools" has now twice turned out to be
"we broke the tools".

What is pinned here is the same code path, asserting the inverse: whatever
model is asked for is the model that runs, with its tools intact.

Same manager-boundary-stub pattern as test_provider_loop_regression.py — the
real _call_ollama and _oai_agentic_loop run end to end and only the Ollama
HTTP call is faked, so this exercises the path a real request takes rather
than a function called in isolation.
"""
from __future__ import annotations

import pytest

from agent_friday.services import model_router as mr


class _FakeOllamaManager:
    base_url = "http://localhost:11434"

    def __init__(self):
        self.calls = []

    def is_available(self):
        return True

    # **kw so a new dispatch-time argument does not silently break the stub.
    # `num_ctx` arrived on 2026-08-15 when dispatch began applying the
    # residency plan's context on every call, not only at Arbiter boot.
    def chat_completion(self, messages, model, tools=None, temperature=0.7,
                        max_tokens=4096, **kw):
        self.calls.append({"messages": list(messages), "tools": tools,
                           "model": model, "num_ctx": kw.get("num_ctx")})
        return {
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "model": model,
        }


ANTHROPIC_TOOL = {
    "name": "read_file", "description": "Read a file.",
    "input_schema": {"type": "object",
                     "properties": {"path": {"type": "string"}},
                     "required": ["path"]},
}


@pytest.fixture
def fake_manager(monkeypatch):
    import agent_friday.routing.ollama_manager as ollama_manager
    fake = _FakeOllamaManager()
    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **k: fake)
    return fake


class TestNoSeatEnforcementInRealDispatch:

    def test_the_model_asked_for_is_the_model_dispatched(self, fake_manager):
        mr._call_ollama([{"role": "user", "content": "hi"}],
                        model="nobody-ever-gated-this:70b",
                        tools=[ANTHROPIC_TOOL])
        assert fake_manager.calls, "the turn never reached the daemon"
        assert fake_manager.calls[0]["model"] == "nobody-ever-gated-this:70b"

    def test_tools_survive_the_turn_for_any_model(self, fake_manager):
        """The old behaviour stripped tools from an ungated model, which
        guaranteed the tool-calling failure the gate claimed to detect."""
        mr._call_ollama([{"role": "user", "content": "hi"}],
                        model="unknown-model:13b", tools=[ANTHROPIC_TOOL])
        sent = fake_manager.calls[0]["tools"]
        assert sent, "tools were dropped"
        assert sent[0]["function"]["name"] == "read_file"

    def test_a_model_with_a_failing_record_still_runs_with_its_tools(
            self, fake_manager, monkeypatch):
        """A red diagnostic is information. It was never a veto."""
        from agent_friday.services import model_seat_gate as gate
        monkeypatch.setattr(gate, "get_cached_status",
                            lambda m, provider="local": {"passed": False,
                                                         "score": "1/10"})
        mr._call_ollama([{"role": "user", "content": "hi"}],
                        model="scored-badly:7b", tools=[ANTHROPIC_TOOL])
        assert fake_manager.calls[0]["model"] == "scored-badly:7b"
        assert fake_manager.calls[0]["tools"]

    def test_dispatch_carries_the_plans_context_not_a_backend_default(
            self, fake_manager):
        """R7, applied per request.

        Without this the Arbiter would seat a model at the planned context and
        the first ordinary chat turn would reload it at Ollama's default —
        measured on 2026-08-15 as gemma4:12b resident at 262144 with 71% of it
        on the CPU, minutes after booting to a plan that said 32768.
        """
        mr._call_ollama([{"role": "user", "content": "hi"}],
                        model="anything:7b", tools=[ANTHROPIC_TOOL])
        assert fake_manager.calls[0]["num_ctx"], \
            "no explicit context was sent, so the daemon default wins"
