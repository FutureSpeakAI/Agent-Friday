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

# The real function, taken at collection time: tests/conftest.py's autouse
# _no_local_seat_is_serving replaces scheduler._resolve_local_seat during tests.
from agent_friday.services import scheduler as _scheduler  # noqa: E402
_REAL_RESOLVE_LOCAL_SEAT = _scheduler._resolve_local_seat


@pytest.fixture(autouse=True)
def no_watch_leaks_in_or_out():
    """A seat watch left running by an earlier test (an Arbiter boot starts one)
    would make start_seat_watch() return that thread instead of this test's, and
    one started here must not outlive the test either."""
    ra.stop_seat_watch()
    yield
    ra.stop_seat_watch()


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
    """The real _resolve_local_seat (tests/conftest.py stubs it for every test
    once the scheduler is imported) adopts before anything else; the adoption
    stub stops it there, so nothing probes a real model server."""
    import sys
    calls = []

    class _Stop(BaseException):
        pass

    def _adopt():
        calls.append(1)
        raise _Stop()
    mod = sys.modules.get("agent_friday.services.residency_arbiter", ra)
    monkeypatch.setattr(mod, "adopt_live_seats", _adopt)
    with pytest.raises(_Stop):
        _REAL_RESOLVE_LOCAL_SEAT()
    assert calls, "the podcast/routine path did not try to adopt a live seat first"


def test_a_boot_in_a_test_process_starts_no_watch():
    """Arbiter.boot() starts the watch only outside tests, so no test leaves one behind."""
    import inspect
    src = inspect.getsource(ra.Arbiter.boot)
    i = src.index("start_seat_watch()")
    assert 'os.environ.get("FRIDAY_TESTING") != "1"' in src[max(0, i - 200):i]


def test_the_seat_watch_adopts_within_its_interval(backend):
    try:
        ra.start_seat_watch(interval_s=0.05)
        deadline = time.monotonic() + 3
        while "bonsai2:27b" not in backend.procs and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "bonsai2:27b" in backend.procs, "the watch did not adopt the seat"
    finally:
        ra.stop_seat_watch()
