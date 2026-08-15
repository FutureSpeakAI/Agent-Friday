"""A5 — seat-gate API: nomination/gate runs trigger BOTH axes with visible
progress; statuses feed the picker chips; fail-closed defaults."""
from __future__ import annotations

import agent_friday.routes.seat_gate as sg_mod


class TestStatuses:
    def test_statuses_shape_and_fail_closed(self, client, monkeypatch):
        class FakeMgr:
            def list_models(self):
                return [{"name": "gemma4:latest"}, {"name": "qwen3:8b"}]

        import agent_friday.routing.ollama_manager as om
        monkeypatch.setattr(om, "get_manager", lambda url=None: FakeMgr())
        resp = client.get("/api/seat-gate/statuses")
        assert resp.status_code == 200
        statuses = resp.get_json()["statuses"]
        assert "gemma4:latest" in statuses
        st = statuses["qwen3:8b"]
        # Informational only — 2026-08-15 the gate was removed, so this
        # reports a diagnostic result and gates nothing.
        assert st["structural"] in ("ungated", "green", "red")
        assert st["running"] is False
        assert st["gates"] is False
        assert "honesty" not in st and "dual_green" not in st


class TestRun:
    def test_run_triggers_the_structural_diagnostic_only(self, client,
                                                         monkeypatch):
        """One axis. The honesty battery module no longer exists."""
        calls = []
        import agent_friday.services.model_seat_gate as gate_mod
        monkeypatch.setattr(
            gate_mod, "run_conformance_gate",
            lambda model, **kw: calls.append(("structural", model)) or {})
        resp = client.post("/api/seat-gate/run", json={"model": "qwen3:8b"})
        assert resp.status_code == 200
        assert resp.get_json()["started"] is True
        assert calls == [("structural", "qwen3:8b")]

    def test_run_requires_model(self, client):
        assert client.post("/api/seat-gate/run", json={}).status_code == 400
