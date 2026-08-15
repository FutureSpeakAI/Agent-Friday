"""The seat gate is GONE — any model can take any seat (2026-08-15).

This file used to assert the opposite: that `POST /api/settings` rejected a
`model_routing.local_model` change when the model failed a structural
conformance gate, or had no honesty-battery record, or failed one.

Stephen's decision, and the evidence supported it. Gating a user-selected
model behind a homegrown eval is not standard practice; the structural
failures it fired on were a broken harness (the same models scored 1/10 and
0/10, then 10/10 once fixed); and the honesty record it refused `gemma4:26b`
on held eleven timeouts and one HTTP error — eleven empty answers, no model
output at all.

What these tests now pin is that nothing refuses a seat.
"""
from __future__ import annotations

import pytest

from agent_friday.services import model_seat_gate


@pytest.fixture
def preserve_local_model():
    from agent_friday.core import _load_settings, _save_settings
    before = (_load_settings().get("model_routing") or {}).get("local_model")
    yield
    s = _load_settings()
    s.setdefault("model_routing", {})["local_model"] = before
    _save_settings(s)


class TestNoSeatGate:
    def _set(self, client, model):
        return client.post("/api/settings", json={
            "settings": {"model_routing": {"local_model": model}}})

    def test_a_model_with_no_gate_record_is_accepted(self, client, monkeypatch,
                                                     preserve_local_model):
        """Previously: rejected, 'fail-closed until dual green'."""
        monkeypatch.setattr(model_seat_gate, "get_cached_status",
                            lambda m, provider="local": None)
        assert self._set(client, "brand-new:70b").status_code == 200

    def test_a_model_that_failed_the_diagnostic_is_accepted(
            self, client, monkeypatch, preserve_local_model):
        """A red structural record is information, never a veto."""
        monkeypatch.setattr(
            model_seat_gate, "get_cached_status",
            lambda m, provider="local": {"passed": False, "score": "3/10"})
        assert self._set(client, "chatty:7b").status_code == 200

    def test_the_gate_check_never_runs_a_battery_on_save(self, client,
                                                         monkeypatch,
                                                         preserve_local_model):
        """The save path must not block for minutes running an eval."""
        def _boom(*a, **k):
            raise AssertionError("no gate may run during a settings save")
        monkeypatch.setattr(model_seat_gate, "run_conformance_gate", _boom)
        assert self._set(client, "anything:13b").status_code == 200

    def test_the_guard_function_is_a_documented_no_op(self):
        from agent_friday.routes.core_routes import _check_local_model_seat_gate
        assert _check_local_model_seat_gate(
            {"model_routing": {"local_model": "whatever:1b"}}) is None


class TestNoHonestyBattery:
    def test_the_module_is_gone(self):
        with pytest.raises(ImportError):
            import agent_friday.services.honesty_battery  # noqa: F401

    def test_axis_status_reports_structural_only(self, monkeypatch):
        monkeypatch.setattr(model_seat_gate, "get_cached_status",
                            lambda m, provider="local": None)
        st = model_seat_gate.axis_status("x:1b", "local")
        assert st["structural"] == "ungated"
        assert st["gates"] is False
        assert "honesty" not in st
        assert "dual_green" not in st
