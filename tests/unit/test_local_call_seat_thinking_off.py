"""local_call asks for an answer without a reasoning preamble on BOTH paths.

The Ollama path has always sent `"think": False`. The path to a seat the
Arbiter serves (llama.cpp, OpenAI-compatible) sent nothing, so a reasoning
model there thought at its own default. Measured on Bonsai 2 27B with a
14,000-character podcast outline prompt: 8,000 tokens and 30,828 characters
of reasoning, finish_reason "length", and an empty answer. With thinking off,
the same prompt answered in 36 s with valid JSON.
"""
from __future__ import annotations

from agent_friday.services import local_call


class _Resp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "{\"ok\": true}"}}]}


def test_a_seat_call_turns_thinking_off(monkeypatch):
    sent = {}
    import requests
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: "http://127.0.0.1:8090/v1")
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: sent.update(json) or _Resp())
    assert local_call.call("s", "u", "bonsai2:27b", json_mode=True) == "{\"ok\": true}"
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["reasoning_effort"] == "none"


def test_the_daemon_call_still_turns_thinking_off(monkeypatch):
    sent = {}
    import requests

    class R:
        status_code = 200

        def json(self):
            return {"message": {"content": "x"}}
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: sent.update(json) or R())
    local_call.call("s", "u", "gemma4:12b")
    assert sent["think"] is False
