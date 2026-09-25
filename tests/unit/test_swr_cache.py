"""Stale-while-revalidate cache: what a panel gets back, and when."""
import threading
import time

import pytest

from agent_friday.services import swr_cache


@pytest.fixture(autouse=True)
def _clean():
    swr_cache.invalidate("")
    yield
    swr_cache.invalidate("")


@pytest.fixture
def clock(monkeypatch):
    """The cache's clock, advanced only by the test.

    Staleness is `now - computed_at > fresh_for`. On Windows the wall clock
    advances in ~15 ms steps, so two back-to-back reads can see the same
    `now` and a fresh_for=0 value is not yet stale; the tests that depend on
    time passing say how much passes."""
    import types
    now = [1_758_000_000.0]
    monkeypatch.setattr(swr_cache, "time",
                        types.SimpleNamespace(time=lambda: now[0]))

    def tick(seconds=1.0):
        now[0] += seconds
    return tick


def _counter(values):
    calls = []

    def compute():
        calls.append(1)
        return values[min(len(calls), len(values)) - 1]
    return compute, calls


def test_cold_read_computes_once_then_serves_from_cache():
    compute, calls = _counter(["a"])
    v1, t1 = swr_cache.get("k", compute, fresh_for=60)
    v2, t2 = swr_cache.get("k", compute, fresh_for=60)
    assert (v1, v2) == ("a", "a")
    assert t1 == t2
    assert len(calls) == 1


def test_stale_read_returns_immediately_and_refreshes_in_background(clock):
    gate = threading.Event()
    state = {"n": 0}

    def compute():
        state["n"] += 1
        if state["n"] > 1:
            gate.wait(5)          # the refresh is slow
        return state["n"]

    swr_cache.get("k", compute, fresh_for=0)
    clock()
    t0 = time.perf_counter()
    v, _ = swr_cache.get("k", compute, fresh_for=0)
    assert v == 1                              # the old value, not a wait
    assert time.perf_counter() - t0 < 0.5
    gate.set()
    for _ in range(100):
        if swr_cache.peek("k")[0] == 2:
            break
        time.sleep(0.02)
    assert swr_cache.peek("k")[0] == 2


def test_concurrent_cold_callers_share_one_computation():
    gate = threading.Event()
    calls = []

    def compute():
        calls.append(1)
        gate.wait(5)
        return "v"

    out = []
    threads = [threading.Thread(target=lambda: out.append(swr_cache.get("k", compute, 60)[0]))
               for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join(5)
    assert out == ["v"] * 5
    assert len(calls) == 1


def test_invalidate_forces_recompute():
    compute, calls = _counter(["old", "new"])
    swr_cache.get("repos.scan", compute, fresh_for=60)
    assert swr_cache.invalidate("repos.") == 1
    assert swr_cache.get("repos.scan", compute, fresh_for=60)[0] == "new"


def test_refresh_started_before_invalidate_does_not_write_back(clock):
    gate = threading.Event()
    state = {"n": 0}

    def compute():
        state["n"] += 1
        if state["n"] == 2:
            gate.wait(5)          # background refresh of pre-mutation state
            return "pre-mutation"
        return "v%d" % state["n"]

    swr_cache.get("k", compute, fresh_for=0)          # v1
    clock()
    swr_cache.get("k", compute, fresh_for=0)          # stale -> refresh #2 starts
    swr_cache.invalidate("k")
    gate.set()
    time.sleep(0.2)
    assert swr_cache.peek("k") is None
    assert swr_cache.get("k", compute, fresh_for=60)[0] == "v3"


def test_max_age_bounds_how_stale_a_served_value_can_be(clock):
    compute, calls = _counter(["a", "b"])
    swr_cache.get("k", compute, fresh_for=0)
    clock(0.05)
    assert swr_cache.get("k", compute, fresh_for=0, max_age=0.01)[0] == "b"


def test_compute_error_propagates_on_cold_read_and_caches_nothing():
    def boom():
        raise RuntimeError("nope")
    with pytest.raises(RuntimeError):
        swr_cache.get("k", boom, fresh_for=60)
    assert swr_cache.peek("k") is None
