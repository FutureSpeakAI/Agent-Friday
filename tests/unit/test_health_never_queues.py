"""/api/health answers within a couple of seconds even while one health
computation hangs: callers that cannot wait for it get the last good payload
marked stale, or a minimal "computing" answer, and a computation that runs
past its cap is abandoned so a later caller can compute afresh.

The blueprint is mounted on a bare Flask app; ``_health_payload`` is replaced
by a stub that blocks on an Event for as long as the test wants it to hang.
"""
from __future__ import annotations

import threading
import time

import pytest
from flask import Flask


@pytest.fixture
def app_and_cr(monkeypatch):
    from agent_friday.routes import core_routes as cr
    cr._reset_health_cache_for_tests()
    monkeypatch.setattr(cr, "_HEALTH_WAIT_S", 2.0, raising=False)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(cr.core_bp)
    yield app, cr
    cr._reset_health_cache_for_tests()


class _Hanging:
    """A health computation that blocks until released, counting its calls."""

    def __init__(self):
        self.release = threading.Event()
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.calls += 1
            n = self.calls
        self.release.wait(30.0)
        return {"status": "ok", "boot_status": "ok", "computation": n}


def _poll_concurrently(app, n=20, budget=10.0):
    results = []
    lock = threading.Lock()

    def _poll():
        t0 = time.monotonic()
        r = app.test_client().get("/api/health")
        with lock:
            results.append((time.monotonic() - t0, r.status_code, r.get_json()))

    threads = [threading.Thread(target=_poll, daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(budget)
    return results


def test_twenty_callers_return_fast_while_the_first_computation_hangs(app_and_cr, monkeypatch):
    app, cr = app_and_cr
    hang = _Hanging()
    monkeypatch.setattr(cr, "_health_payload", hang)
    try:
        t0 = time.monotonic()
        results = _poll_concurrently(app)
        wall = time.monotonic() - t0
        assert len(results) == 20, (
            "only %d of 20 health callers returned within 10 s while one "
            "computation hung" % len(results))
        assert wall < 3.5, "20 callers took %.1f s while one computation hung" % wall
        for took, code, body in results:
            assert code == 200
            assert took < 3.5
            assert body.get("computing") is True, body
            assert "boot_status" in body
        assert hang.calls == 1, "a hanging computation was started %d times" % hang.calls
    finally:
        hang.release.set()


def test_a_slow_refresh_serves_the_last_good_payload_marked_stale(app_and_cr, monkeypatch):
    app, cr = app_and_cr
    monkeypatch.setattr(cr, "_health_payload",
                        lambda: {"status": "ok", "boot_status": "ok", "fresh": 1})
    assert app.test_client().get("/api/health").get_json()["fresh"] == 1
    monkeypatch.setattr(cr, "_HEALTH_TTL_S", 0.0)
    hang = _Hanging()
    monkeypatch.setattr(cr, "_health_payload", hang)
    try:
        results = _poll_concurrently(app, n=5)
        assert len(results) == 5
        for took, code, body in results:
            assert code == 200 and took < 3.5
            assert body["stale"] is True
            assert body["fresh"] == 1
            assert isinstance(body["stale_age_seconds"], (int, float))
    finally:
        hang.release.set()


def test_a_computation_past_its_cap_is_abandoned_and_a_later_call_computes_fresh(app_and_cr, monkeypatch):
    app, cr = app_and_cr
    monkeypatch.setattr(cr, "_HEALTH_COMPUTE_CAP_S", 0.5, raising=False)
    hang = _Hanging()
    monkeypatch.setattr(cr, "_health_payload", hang)
    try:
        body = app.test_client().get("/api/health").get_json()
        assert body.get("computing") is True
        time.sleep(0.6)
        fresh = {"n": 0}

        def _fast():
            fresh["n"] += 1
            return {"status": "ok", "boot_status": "ok", "fresh": True}

        monkeypatch.setattr(cr, "_health_payload", _fast)
        body = app.test_client().get("/api/health").get_json()
        assert body.get("fresh") is True, body
        assert fresh["n"] == 1
    finally:
        hang.release.set()
    # The abandoned computation finishing late does not overwrite the fresh
    # answer.
    time.sleep(0.2)
    body = app.test_client().get("/api/health").get_json()
    assert body.get("fresh") is True and "computation" not in body


def test_a_failed_computation_is_not_cached(app_and_cr, monkeypatch):
    app, cr = app_and_cr
    state = {"fail": True, "calls": 0}

    def _flaky():
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("transient")
        return {"status": "ok", "boot_status": "ok"}

    monkeypatch.setattr(cr, "_health_payload", _flaky)
    with pytest.raises(RuntimeError):
        cr._health_cached()
    state["fail"] = False
    assert cr._health_cached()["status"] == "ok"
    assert state["calls"] == 2
