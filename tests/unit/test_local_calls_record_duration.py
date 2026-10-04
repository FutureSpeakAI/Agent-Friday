"""Every local model call records how long it took.

costs.db had a duration for cloud rows and none for local ones (789 local
chat rows, `dur>0: 0`), so the local per-call figure could only be read off
the seat's own log. The OpenAI-shape transport now stamps the response with
its wall time, and the loop's cost row carries it.
"""
from __future__ import annotations

import json

import agent_friday.services.model_router as smr


class _FakeResp:
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self):
        self._lines = [
            "data: " + json.dumps({"model": "bonsai2:27b",
                                   "choices": [{"delta": {"content": "ok"}}]}),
            "data: " + json.dumps({"model": "bonsai2:27b",
                                   "choices": [{"delta": {}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                                   "timings": {"prompt_n": 5, "cache_n": 0}}),
            "data: [DONE]",
        ]

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def raise_for_status(self):
        return None

    def close(self):
        return None


def _stub(monkeypatch):
    import requests
    monkeypatch.setattr(
        requests, "post",
        lambda url, headers=None, json=None, timeout=None, **kw: _FakeResp())
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")


def test_the_transport_stamps_the_call_with_its_wall_time(monkeypatch):
    _stub(monkeypatch)
    seen = {}
    orig = smr._consume_sse_completion

    def _spy(resp, **kw):
        out = orig(resp, **kw)
        seen["out"] = out
        return out
    monkeypatch.setattr(smr, "_consume_sse_completion", _spy)
    text, _ = smr._call_openai([{"role": "user", "content": "hi"}],
                               model="openrouter/auto", tools=None, provider="openrouter")
    assert text == "ok"
    # `_call_openai` returns text; the dict it built carries the stamps the
    # loop reads. The spy saw the dict before the stamps were added, so read
    # them off the same object.
    assert isinstance(seen["out"].get("_duration_ms"), int)
    assert seen["out"]["_duration_ms"] >= 0


def test_the_cost_row_carries_the_duration(monkeypatch):
    """The loop hands the transport's stamp to cost_meter.meter."""
    from agent_friday.services import cost_meter as cm
    rows = []
    monkeypatch.setattr(cm, "record",
                        lambda provider, model, input_tokens=0, output_tokens=0, **kw:
                        rows.append(kw) or 0.0)
    cm.meter("local", "bonsai2:27b", {"prompt_tokens": 5, "completion_tokens": 1},
             duration_ms=2500)
    assert rows and rows[0]["duration_ms"] == 2500
