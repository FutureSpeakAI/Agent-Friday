"""Streaming a chat turn to the browser, and who gets the credit for it.

Text chat never streamed in this app, for any provider: /api/chat returned
one finished JSON body and no client could read a stream. These tests pin
the two halves of the fix and the one thing that must not regress -- that a
turn is described by the model which actually answered it.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.services.model_router as smr

pytestmark = pytest.mark.real_provider_paths

SEP = chr(10) + chr(10)


class _FakeResp:
    """A streamed OpenAI-compatible response."""

    status_code = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self, model="anthropic/claude-sonnet-5", pieces=("PO", "NG")):
        self._lines = []
        for piece in pieces:
            self._lines.append("data: " + json.dumps(
                {"model": model, "choices": [{"delta": {"content": piece}}]}))
        self._lines.append("data: " + json.dumps(
            {"model": model,
             "choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 2}}))
        self._lines.append("data: [DONE]")

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def raise_for_status(self):
        return None

    def close(self):
        return None


def _stub_transport(monkeypatch, model="anthropic/claude-sonnet-5"):
    import requests
    monkeypatch.setattr(
        requests, "post",
        lambda url, headers=None, json=None, timeout=None, **kw: _FakeResp(model))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")


# ── The sink: how tokens escape a 1200-line function ────────────────────────

def test_delta_sink_receives_tokens_without_a_callback(monkeypatch):
    """routes/chat.py::chat() cannot thread an on_delta down every branch, so
    the transport publishes to a context variable instead. Setting it is the
    entire integration surface."""
    _stub_transport(monkeypatch)
    seen = []
    token = smr.DELTA_SINK.set(seen.append)
    try:
        text, _ = smr._call_openai([{"role": "user", "content": "hi"}],
                                   model="openrouter/auto", tools=None,
                                   provider="openrouter")
    finally:
        smr.DELTA_SINK.reset(token)
    assert text == "PONG"
    assert seen == ["PO", "NG"], "tokens did not reach the sink"


def test_an_explicit_callback_still_wins(monkeypatch):
    _stub_transport(monkeypatch)
    sink, direct = [], []
    token = smr.DELTA_SINK.set(sink.append)
    try:
        smr._call_openai([{"role": "user", "content": "hi"}],
                         model="z-ai/glm-5.3", tools=None,
                         provider="openrouter", on_delta=direct.append)
    finally:
        smr.DELTA_SINK.reset(token)
    assert direct == ["PO", "NG"]
    assert sink == []


# ── Attribution: the badge must name the model that answered ────────────────

def test_attribution_records_the_served_model_not_the_router(monkeypatch):
    """services/attribution's contract is "the model id it truly ran".

    Under `openrouter/auto` the id we SEND is a router, not an answer. A
    badge reading "openrouter/auto" names no model at all — the exact class
    of lie that module exists to end.
    """
    from agent_friday.services import attribution
    _stub_transport(monkeypatch, model="anthropic/claude-sonnet-5")
    attribution.reset()
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="openrouter/auto", tools=None, provider="openrouter")
    gen = attribution.last_generation() or {}
    assert gen.get("model") == "anthropic/claude-sonnet-5", (
        "the badge would have named the router, not the model that answered")


def test_attribution_falls_back_to_the_requested_id(monkeypatch):
    """A provider that does not echo a model must not blank the badge."""
    import requests
    from agent_friday.services import attribution

    class _NoModel(_FakeResp):
        def __init__(self):
            super().__init__()
            self._lines = ["data: " + json.dumps(
                {"choices": [{"delta": {"content": "hi"},
                              "finish_reason": "stop"}]}), "data: [DONE]"]

    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: _NoModel())
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")
    attribution.reset()
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="z-ai/glm-5.3", tools=None, provider="openrouter")
    assert (attribution.last_generation() or {}).get("model") == "z-ai/glm-5.3"


# ── The endpoint ────────────────────────────────────────────────────────────

def test_chat_stream_emits_deltas_then_the_whole_payload(client, monkeypatch):
    """The done event carries chat()'s OWN payload verbatim, so a streaming
    client renders exactly what the blocking endpoint would have returned."""
    from agent_friday.routes import chat as chat_routes
    from flask import jsonify

    def fake_chat():
        sink = smr.DELTA_SINK.get()
        for piece in ("Hel", "lo"):
            if sink:
                sink(piece)
        return jsonify({"response": "Hello", "model": "anthropic/claude-sonnet-5",
                        "seat": "openai", "actions": []})

    monkeypatch.setattr(chat_routes, "chat", fake_chat)
    res = client.post("/api/chat/stream", json={"message": "hi"})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("Content-Type", "")

    deltas, payload = [], None
    for frame in res.get_data(as_text=True).split(SEP):
        frame = frame.strip()
        if not frame.startswith("data:"):
            continue
        ev = json.loads(frame[5:].strip())
        if "delta" in ev:
            deltas.append(ev["delta"])
        if ev.get("done"):
            payload = ev.get("payload")

    assert deltas == ["Hel", "lo"]
    assert payload is not None
    assert payload["response"] == "Hello"
    assert payload["model"] == "anthropic/claude-sonnet-5"


def test_chat_stream_reports_a_failure_instead_of_hanging(client, monkeypatch):
    from agent_friday.routes import chat as chat_routes

    def boom():
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(chat_routes, "chat", boom)
    res = client.post("/api/chat/stream", json={"message": "hi"})
    body = res.get_data(as_text=True)
    assert "provider exploded" in body
    assert res.status_code == 200          # the stream itself was fine
