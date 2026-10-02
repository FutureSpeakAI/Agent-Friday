"""A seat started outside the Arbiter is adopted, so nothing reports it missing.

The build-hours daemon restored the brain seat at 07:00 by launching
llama-server itself, after the server had booted. The Arbiter adopts seats only
at boot, so it neither owned nor published that one: local_seats.serving() was
empty, and the podcast engine, the routines and the capability state all said
"no local model is serving" while the same seat answered chat. The owner
watched the local model chase that phantom for 24 minutes.

Now the Arbiter adopts (never kills) any healthy seat on a known port that it
does not own: on demand when serving() or the scheduler finds none, and from a
periodic watch. A seat started over its context cap is left alone, as at boot.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import residency_arbiter as ra

LIVE = {"bonsai2:27b": (4242, 8090)}


@pytest.fixture
def backend(monkeypatch):
    published = []
    monkeypatch.setattr(ra, "survey_live_seats", lambda *a, **k: dict(LIVE))
    monkeypatch.setattr(ra, "_seat_num_ctx", lambda pid: None)
    monkeypatch.setattr(ra, "_publish_endpoints", lambda procs, drop=(): published.append(dict(procs)))

    def _never(*a, **k):
        raise AssertionError("adoption must never kill a process")
    monkeypatch.setattr(ra.subprocess, "run", _never)
    b = ra.LlamaServerBackend()

    class _Arb:
        llama = b
    monkeypatch.setattr(ra, "ARBITER", _Arb())
    b.published = published
    return b


def test_a_seat_started_outside_the_arbiter_is_adopted_and_published(backend):
    assert ra.adopt_live_seats() == ["bonsai2:27b on :8090 (pid 4242)"]
    assert "bonsai2:27b" in backend.procs and backend.procs["bonsai2:27b"][1] == 8090
    assert backend.published and "bonsai2:27b" in backend.published[-1]
    assert ra.adopt_live_seats() == [], "an owned seat is not adopted twice"


def test_a_seat_over_its_context_cap_is_left_alone(backend, monkeypatch):
    monkeypatch.setattr(ra, "_seat_num_ctx", lambda pid: 10 ** 9)
    assert ra.adopt_live_seats() == [] and "bonsai2:27b" not in backend.procs


def test_serving_adopts_on_demand_instead_of_reporting_no_seat(backend, monkeypatch):
    from agent_friday.services import local_seats
    monkeypatch.setattr(ra, "_read_published", lambda: {})

    def _no_daemon():
        raise RuntimeError("no Ollama daemon")
    import agent_friday.routing.ollama_manager as om
    monkeypatch.setattr(om, "get_manager", _no_daemon)
    out = local_seats.serving()
    assert out.get("bonsai2:27b") == "http://127.0.0.1:8090", out


def test_the_scheduler_adopts_before_it_asks_for_a_local_seat(monkeypatch):
    from agent_friday.services import scheduler
    calls = []
    monkeypatch.setattr(ra, "adopt_live_seats", lambda: calls.append(1) or [])
    scheduler._resolve_local_seat()
    assert calls, "the podcast/routine path did not try to adopt a live seat first"


def test_the_seat_watch_adopts_within_its_interval(backend):
    try:
        ra.start_seat_watch(interval_s=0.05)
        deadline = time.monotonic() + 3
        while "bonsai2:27b" not in backend.procs and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "bonsai2:27b" in backend.procs, "the watch did not adopt the seat"
    finally:
        ra.stop_seat_watch()
