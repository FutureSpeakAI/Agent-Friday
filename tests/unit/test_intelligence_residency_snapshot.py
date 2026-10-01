"""Settings > Intelligence reads residency on a background thread, so what it
computes there must not need a request context.

The snapshot runs its compute on a worker thread. Computing through the
login-gated /api/residency/status view there failed every time with "Working
outside of request context", so the seats block was never filled. This test
captures the compute the route hands to the snapshot and runs it on a plain
thread, exactly as the snapshot's worker does.
"""
from __future__ import annotations

import threading
import types

import pytest
from flask import Flask


class _Stop(BaseException):
    """Ends the request once the compute is captured."""


class _FakeArbiter:
    state = "steady"
    lease = None
    seat_problems = {}
    transitions = []
    llama = types.SimpleNamespace(procs={})
    ollama = types.SimpleNamespace(resident=lambda: {})

    @staticmethod
    def plan_fresh():
        return {"seats": {"brain": {"model_id": "fake-brain-4b", "status": "resident",
                                    "device": "cuda", "num_ctx": 8192}},
                "budgets": {"vram_mib": 12000}, "refusals": [],
                "pinned_vram_mib": {}}


def test_the_residency_compute_works_off_the_request_thread(monkeypatch):
    from agent_friday.routes import intelligence as I
    from agent_friday.services import machine_probe, model_catalog, residency_arbiter

    monkeypatch.setattr(model_catalog, "build_catalog",
                        lambda *a, **k: {"roles": {}, "models": [], "providers": []})
    monkeypatch.setattr(residency_arbiter, "get_arbiter", lambda: _FakeArbiter())

    captured = {}

    def _snapshot(key, compute, **kw):
        if key != "intelligence:residency":
            return kw.get("default"), 0.0, "unknown"
        box = {}

        def _run():
            try:
                box["value"] = compute()
            except Exception as e:  # noqa: BLE001 - recorded for the assertion
                box["error"] = "%s: %s" % (type(e).__name__, e)

        t = threading.Thread(target=_run)
        t.start()
        t.join()
        captured.update(box)
        raise _Stop()

    monkeypatch.setattr(machine_probe, "snapshot", _snapshot)

    app = Flask(__name__)
    app.secret_key = "test-only"  # pragma: allowlist secret
    app.config.update(TESTING=True)
    app.register_blueprint(I.intelligence_bp)
    with pytest.raises(_Stop):
        app.test_client().get("/api/intelligence")

    assert "error" not in captured, captured.get("error")
    st = captured["value"]
    assert st["governing"] is True
    assert st["seats"]["brain"]["model_id"] == "fake-brain-4b"
