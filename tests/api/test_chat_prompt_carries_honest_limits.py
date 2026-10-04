"""The main chat window carries the HONEST LIMITS rules, like background work.

Fail visibly, never substitute quietly. The two chat endpoints assemble their
own prompt from `_build_context_prompt` and never called
`_get_friday_system_prompt`, so the rules reached background tasks but not the
conversation the user is watching. This captures the `system` argument at the
model call on both routes.
"""
from __future__ import annotations

import pytest

from agent_friday.services.model_router import REFUSAL_HONESTY_DIRECTIVE


@pytest.fixture
def captured(patch_app):
    seen = []

    def fake_agent(messages, *a, **k):
        seen.append(k.get("system") if "system" in k else (a[0] if a else ""))
        return ("ok", [])

    def fake_text(*a, **k):
        seen.append(k.get("system") or "")
        return "ok"

    for name in ("_generate_agent", "_call_claude_agent", "_oai_agentic_loop"):
        patch_app(name, fake_agent)
    for name in ("_generate_text", "_call_claude", "_call_ollama", "_call_openai"):
        patch_app(name, fake_text)
    return seen


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/send"])
def test_chat_prompt_carries_the_honest_limits_once(client, captured, route):
    r = client.post(route, json={"message": "make me a picture of a lighthouse"})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    systems = [s for s in captured if isinstance(s, str) and s]
    assert systems, "the route never reached a model call"
    for s in systems:
        assert s.count("== HONEST LIMITS ==") == 1, "honest limits missing or duplicated"
        assert REFUSAL_HONESTY_DIRECTIVE in s
