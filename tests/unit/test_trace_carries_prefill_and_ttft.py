"""A turn's reasoning trace says what the seat read, what it kept, and how long the user waited.

The baseline that motivated the speed work had to be scraped from the
llama-server log because nothing in Friday's own records carried a local
call's prefill size, cache hit or time to first token. Now the transport
stamps each response with its wall time and first-token delay, the seat's
own `timings` (prompt_n, cache_n) ride to the trace, and a rolling audit
says when the prefix cache stops hitting.
"""
from __future__ import annotations

import json
import logging
import time

from agent_friday.services import prompt_cache as pc
from agent_friday.services import reasoning_trace as rt
import agent_friday.services.model_router as smr


class _FakeResp:
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self, timings):
        self._lines = [
            "data: " + json.dumps({"model": "bonsai2:27b",
                                   "choices": [{"delta": {"reasoning_content": "hm"}}]}),
            "data: " + json.dumps({"model": "bonsai2:27b",
                                   "choices": [{"delta": {"content": "Hi"}}]}),
            "data: " + json.dumps({"model": "bonsai2:27b",
                                   "choices": [{"delta": {}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 1200, "completion_tokens": 1},
                                   "timings": timings}),
            "data: [DONE]",
        ]

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def raise_for_status(self):
        return None

    def close(self):
        return None


TIMINGS = {"prompt_n": 1200, "cache_n": 18000, "prompt_ms": 2400.0,
           "predicted_n": 1, "predicted_ms": 30.0}


def test_the_stream_consumer_stamps_time_to_first_token():
    t0 = time.time() - 0.5
    out = smr._consume_sse_completion(_FakeResp(TIMINGS), started_at=t0)
    assert out["_ttft_ms"] >= 500
    assert out["timings"]["cache_n"] == 18000


def test_no_started_at_means_no_ttft_claim():
    out = smr._consume_sse_completion(_FakeResp(TIMINGS))
    assert "_ttft_ms" not in out


def test_the_trace_record_sums_prefill_cache_duration_and_keeps_first_ttft(monkeypatch):
    monkeypatch.setattr(rt, "_capture_enabled", lambda: True)
    tid = rt.start("chat", "t", model="bonsai2:27b", seat="brain", provider="local")
    tok = rt._CURRENT.set(tid) if hasattr(rt, "_CURRENT") else None
    try:
        resp = {"usage": {"prompt_tokens": 1200, "completion_tokens": 5},
                "timings": dict(TIMINGS), "_duration_ms": 2500, "_ttft_ms": 2400}
        rt.after_oai_round(resp, {"content": "Hi"}, model="bonsai2:27b",
                           seat="brain", provider="local", local=True, )
        resp2 = {"usage": {"prompt_tokens": 300, "completion_tokens": 5},
                 "timings": {"prompt_n": 300, "cache_n": 19200}, "_duration_ms": 900,
                 "_ttft_ms": 700}
        rt.after_oai_round(resp2, {"content": "Hi"}, model="bonsai2:27b",
                           seat="brain", provider="local", local=True)
        tr = rt._TRACES[tid]
        assert tr["timing"] == {"duration_ms": 3400, "prefill_tokens": 1500,
                                "cache_tokens": 37200, "ttft_ms": 2400, "calls": 2}
        events = [e for e in tr["events"] if e["type"] == "model_call"]
        assert events[0]["prefill_tokens"] == 1200 and events[0]["cache_tokens"] == 18000
        assert "timing" in rt._header(tr)
    finally:
        if tok is not None:
            rt._CURRENT.reset(tok)
        rt._TRACES.pop(tid, None)


def test_a_small_reread_is_a_hit_and_a_large_one_is_a_miss():
    pc._reset_seat_audit()
    assert pc.observe_seat_timings({"prompt_n": 1200, "cache_n": 18000})["hit"] is True
    assert pc.observe_seat_timings({"prompt_n": 15300, "cache_n": 0})["hit"] is False
    assert pc.observe_seat_timings({}) == {}
    assert pc.seat_cache_hit_rate() == {"hits": 1, "calls": 2, "rate": 0.5}


def test_the_alarm_fires_once_per_window_when_hits_fall_under_80_percent(caplog):
    pc._reset_seat_audit()
    with caplog.at_level(logging.WARNING, logger="agent_friday.services.prompt_cache"):
        verdicts = []
        for i in range(pc.SEAT_AUDIT_WINDOW):
            hit = i % 2 == 0                  # 50% hit rate
            verdicts.append(pc.observe_seat_timings(
                {"prompt_n": 500 if hit else 20000, "cache_n": 0}))
        assert sum(1 for v in verdicts if v.get("alarm")) == 1
        for i in range(pc.SEAT_AUDIT_WINDOW - 1):   # not yet a full window later
            pc.observe_seat_timings({"prompt_n": 20000, "cache_n": 0})
        assert not pc.observe_seat_timings({"prompt_n": 500})["hit"] is False
    assert sum("prefix-cache hit rate" in r.getMessage() for r in caplog.records) >= 1


def test_a_healthy_seat_never_alarms():
    pc._reset_seat_audit()
    out = [pc.observe_seat_timings({"prompt_n": 800, "cache_n": 20000})
           for _ in range(pc.SEAT_AUDIT_WINDOW * 2)]
    assert not any(v.get("alarm") for v in out)
    assert pc.seat_cache_hit_rate()["rate"] == 1.0
