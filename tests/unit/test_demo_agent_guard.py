"""Regression: the agentic primitive must short-circuit in demo mode.

Before this guard, /api/chat/send and the background-task workers called
services.agent._generate_agent, which exhausted every provider and raised
RuntimeError("No model provider could run the agent") on a fresh keyless
install — surfacing as HTTP 500. With no keys and no local Ollama, the
agentic path must return a labelled [DEMO] placeholder instead.

Lives in tests/unit (not tests/api) because the api conftest autouse-stubs
_generate_agent itself, which would mask the real guard under test.
"""
from services import demo_mode as dm


def test_generate_agent_short_circuits_in_demo(monkeypatch):
    # Force the AUTO demo state: pretend no provider (no keys, no Ollama).
    monkeypatch.setattr(dm, "_any_provider_available", lambda: False)
    assert dm.is_demo() is True

    import services.agent as ag
    text, trace = ag._generate_agent([{"role": "user", "content": "hi"}])

    assert isinstance(text, str) and text.startswith("[DEMO]")
    assert trace == []
