"""/api/health computes once per five seconds however many callers poll it,
and the provider sweep behind it reuses one bounded executor.

The UI, the tray watchdog and the settings panels poll /api/health about 16
times a minute. Each computation walked several directories, ran the boot
health report and swept the providers through a freshly built thread pool.
The blueprint is mounted on a bare Flask app; the expensive halves of the
computation are stubs that count their calls.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time

import pytest
from flask import Flask


@pytest.fixture
def health(monkeypatch):
    from agent_friday.routes import core_routes as cr
    from agent_friday.services import provider_health, health_check, capability_preflight

    calls = {"n": 0}

    def _inference():
        calls["n"] += 1
        time.sleep(0.2)          # long enough for concurrent callers to overlap
        return {"status": "ok", "providers": []}

    monkeypatch.setattr(provider_health, "inference_health", _inference)
    monkeypatch.setattr(health_check, "boot_critical_report", lambda **k: {
        "health_schema_version": 1, "boot_critical_ok": True, "boot_status": "ok",
        "subsystems": {}, "deployment": "test"})
    monkeypatch.setattr(capability_preflight, "status",
                        lambda: {"missing_required": [], "detail": "ok"})
    reset = getattr(cr, "_reset_health_cache_for_tests", None)
    if reset:
        reset()
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(cr.core_bp)
    yield app, calls
    if reset:
        reset()


def test_twenty_polls_within_five_seconds_compute_once(health):
    app, calls = health
    results = []

    def _poll():
        r = app.test_client().get("/api/health")
        results.append((r.status_code, r.get_json()["status"]))

    threads = [threading.Thread(target=_poll) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for _ in range(10):
        _poll()
    assert len(results) == 20
    assert all(r == (200, "ok") for r in results)
    assert calls["n"] == 1, "health computed %d times for 20 polls" % calls["n"]


def test_the_answer_is_recomputed_after_five_seconds(health, monkeypatch):
    app, calls = health
    from agent_friday.routes import core_routes as cr
    c = app.test_client()
    c.get("/api/health")
    monkeypatch.setattr(cr, "_HEALTH_TTL_S", 0.0)
    c.get("/api/health")
    assert calls["n"] == 2


def test_a_failed_computation_is_not_cached(health, monkeypatch):
    app, calls = health
    from agent_friday.routes import core_routes as cr
    real = cr._health_payload
    boom = {"left": 1}

    def _flaky():
        if boom["left"]:
            boom["left"] -= 1
            raise RuntimeError("transient")
        return real()

    monkeypatch.setattr(cr, "_health_payload", _flaky)
    with pytest.raises(RuntimeError):
        cr._health_cached()
    assert cr._health_cached()["status"] == "ok"


def test_probe_all_reuses_one_executor(monkeypatch):
    from agent_friday.services import machine_probe as mp

    mp.probe_all({"warm": lambda: 1})          # the shared pool may start here
    built = {"n": 0}
    real = concurrent.futures.ThreadPoolExecutor

    class _Counting(real):
        def __init__(self, *a, **k):
            built["n"] += 1
            super().__init__(*a, **k)

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", _Counting)
    monkeypatch.setattr(mp, "ThreadPoolExecutor", _Counting, raising=False)
    for i in range(10):
        out = mp.probe_all({"a": lambda: 1, "b": lambda: 2})
        assert out["a"]["value"] == 1 and out["b"]["value"] == 2
    assert built["n"] == 0, "probe_all built %d executors for 10 sweeps" % built["n"]


def test_probe_all_threads_stay_bounded():
    from agent_friday.services import machine_probe as mp
    for _ in range(30):
        mp.probe_all({str(i): (lambda: time.sleep(0.01)) for i in range(8)})
    live = [t for t in threading.enumerate() if t.name.startswith("probe_all")]
    assert len(live) <= mp.PROBE_POOL_WORKERS


def test_a_slow_probe_still_times_out():
    from agent_friday.services import machine_probe as mp
    out = mp.probe_all({"slow": lambda: time.sleep(1.0), "fast": lambda: 3},
                       timeout=0.2)
    assert out["slow"]["timed_out"] is True
    assert out["fast"]["value"] == 3
