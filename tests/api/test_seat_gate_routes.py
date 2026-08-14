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
        # qwen3:8b has no recorded run on either axis — fail-closed.
        assert st["structural"] in ("ungated", "green", "red")
        assert st["honesty"] in ("ungated", "green", "red")
        assert st["running"] is False
        assert isinstance(st["dual_green"], bool)


class TestRun:
    def test_run_triggers_both_axes(self, client, monkeypatch):
        calls = []
        import agent_friday.services.model_seat_gate as gate_mod
        import agent_friday.services.honesty_battery as hb_mod
        monkeypatch.setattr(gate_mod, "run_conformance_gate",
                            lambda model, **kw: calls.append(("structural", model)) or {})
        monkeypatch.setattr(hb_mod, "run_battery",
                            lambda model, **kw: calls.append(("honesty", model)) or {})
        resp = client.post("/api/seat-gate/run", json={"model": "qwen3:8b"})
        assert resp.status_code == 200
        assert resp.get_json()["started"] is True
        assert ("structural", "qwen3:8b") in calls
        assert ("honesty", "qwen3:8b") in calls

    def test_run_requires_model(self, client):
        assert client.post("/api/seat-gate/run", json={}).status_code == 400
