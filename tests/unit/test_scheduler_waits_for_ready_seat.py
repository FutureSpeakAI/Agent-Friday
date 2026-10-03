"""A 503 "Loading model" from our own seat delays the call; it never degrades it.

llama-server answers 503 "Loading model" from the moment it binds its port
until the weights are read (11.9 s median, 31.3 s p90 for the 27B). The
07:00 routine once ran against a still-loading seat, took the 503 as a
failed call and shipped a degraded Front Page. The wait lives at the
transport (`model_router._call_openai`), bounded by SEAT_LOADING_WAIT_S, so
every routine, briefing and chat turn inherits it; a cloud 503 and a local
503 that is not a loading notice raise exactly as before.
"""
from __future__ import annotations

import pytest

from agent_friday.services import local_call
import agent_friday.services.model_router as mr

_SEAT = {
    "name": "test-seat",
    "base_url": "http://127.0.0.1:8090/v1",
    "auth": {"type": "none"},
    "classification": "local",
    "adapter": "openai-compatible",
    "features": {},
}


class _Resp:
    headers = {"Content-Type": "application/json"}
    reason = "Service Unavailable"
    url = "http://127.0.0.1:8090/v1/chat/completions"

    def __init__(self, status, text="", body=None):
        self.status_code = status
        self.text = text
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        return None

    def close(self):
        return None


OK_BODY = {"model": "bonsai2:27b",
           "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 3, "completion_tokens": 1},
           "timings": {"prompt_n": 3, "cache_n": 0}}


def _drive(monkeypatch, answers, provider=None, **kw):
    import requests
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return answers.pop(0) if len(answers) > 1 else answers[0]

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    monkeypatch.setattr(mr, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(mr, "SEAT_LOADING_POLL_S", 0.0)
    text, _ = mr._call_openai([{"role": "user", "content": "go"}], model="bonsai2:27b",
                              provider=provider or dict(_SEAT), stream=False, **kw)
    return text, calls


def test_a_loading_seat_is_waited_for_and_then_answers(monkeypatch):
    answers = [_Resp(503, '{"error":{"message":"Loading model","code":503}}'),
               _Resp(503, '{"error":{"message":"Loading model","code":503}}'),
               _Resp(200, "", OK_BODY)]
    text, calls = _drive(monkeypatch, answers)
    assert text == "ready"
    assert len(calls) == 3, "the transport should have re-posted until the seat answered"


def test_the_wait_is_bounded(monkeypatch):
    monkeypatch.setattr(mr, "SEAT_LOADING_WAIT_S", 0.0)
    answers = [_Resp(503, '{"error":{"message":"Loading model"}}')]
    with pytest.raises(Exception) as e:
        _drive(monkeypatch, answers)
    assert "503" in str(e.value)


def test_a_local_503_that_is_not_loading_raises_at_once(monkeypatch):
    import requests
    seen = []
    orig = requests.post
    answers = [_Resp(503, '{"error":{"message":"server is busy"}}'), _Resp(200, "", OK_BODY)]

    def counting(url, **kwargs):
        seen.append(1)
        return answers.pop(0)
    monkeypatch.setattr(requests, "post", counting)
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    monkeypatch.setattr(mr, "_load_settings", lambda *a, **k: {})
    with pytest.raises(Exception):
        mr._call_openai([{"role": "user", "content": "go"}], model="bonsai2:27b",
                        provider=dict(_SEAT), stream=False)
    assert len(seen) == 1
    assert orig is not counting


def test_a_cloud_503_is_the_fallback_chains_business(monkeypatch):
    """Only a seat we serve ourselves is waited for."""
    import requests
    seen = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")

    def cloud_post(url, **kwargs):
        seen.append(1)
        return _Resp(503, '{"error":{"message":"Loading model"}}')
    monkeypatch.setattr(requests, "post", cloud_post)
    with pytest.raises(Exception):
        mr._call_openai([{"role": "user", "content": "hi"}], model="openrouter/auto",
                        tools=None, provider="openrouter", stream=False)
    assert len(seen) == 1


def test_the_body_check_reads_llama_servers_notice():
    assert mr._body_says_loading(_Resp(503, '{"error":{"message":"Loading model"}}'))
    assert not mr._body_says_loading(_Resp(503, '{"error":{"message":"slot unavailable"}}'))
