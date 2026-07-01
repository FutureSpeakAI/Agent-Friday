"""Unit tests for budget_enforcer — policy set/get, reservation against the
monthly cap, release of unused budget, remaining calculation, and hard stop.
"""
from __future__ import annotations

import uuid

import pytest

from agent_friday.services import budget_enforcer as be


def _ws(name):
    return f"budget-ws-{name}-{uuid.uuid4().hex[:8]}"


class TestPolicy:
    def test_default_policy(self):
        p = be.get_policy(_ws("default"))
        assert p["monthly_cap_mψ"] == be._DEFAULT_MONTHLY_CAP

    def test_set_policy_persists(self):
        ws = _ws("setpol")
        be.set_policy(ws, monthly_cap_mψ=50_000, per_task_cap_mψ=5_000, warn_pct=90)
        p = be.get_policy(ws)
        assert p["monthly_cap_mψ"] == 50_000
        assert p["warn_pct"] == 90

    def test_get_all_policies_includes_set(self):
        ws = _ws("allpol")
        be.set_policy(ws, monthly_cap_mψ=1234)
        all_ws = [p["workspace"] for p in be.get_all_policies()]
        assert ws in all_ws


class TestReservation:
    def test_reserve_within_cap(self):
        ws = _ws("reserve")
        be.set_policy(ws, monthly_cap_mψ=10_000)
        assert be.reserve_budget(ws, 5_000) is True
        assert be.monthly_spend(ws) == 5_000

    def test_reserve_exceeding_cap_fails(self):
        ws = _ws("overcap")
        be.set_policy(ws, monthly_cap_mψ=10_000)
        assert be.reserve_budget(ws, 5_000) is True
        # Second reservation would exceed the cap → rejected.
        assert be.reserve_budget(ws, 6_000) is False

    def test_reserve_zero_or_negative_is_noop_true(self):
        ws = _ws("zero")
        assert be.reserve_budget(ws, 0) is True
        assert be.reserve_budget(ws, -100) is True
        assert be.monthly_spend(ws) == 0

    def test_check_remaining(self):
        ws = _ws("remain")
        be.set_policy(ws, monthly_cap_mψ=10_000)
        be.reserve_budget(ws, 3_000)
        assert be.check_remaining(ws) == 7_000

    def test_release_returns_budget(self):
        ws = _ws("release")
        be.set_policy(ws, monthly_cap_mψ=10_000)
        be.reserve_budget(ws, 8_000)
        be.release_budget(ws, 8_000)
        # After releasing, remaining should recover.
        assert be.check_remaining(ws) >= 8_000

    def test_release_zero_noop(self):
        ws = _ws("relzero")
        be.reserve_budget(ws, 1_000)
        be.release_budget(ws, 0)  # no-op, no crash
        assert be.monthly_spend(ws) == 1_000


class TestStatus:
    def test_budget_status_shape(self):
        ws = _ws("status")
        be.set_policy(ws, monthly_cap_mψ=10_000)
        be.reserve_budget(ws, 2_000)
        s = be.budget_status(ws)
        assert s["spent_mψ"] == 2_000
        assert s["remaining_mψ"] == 8_000
        assert s["pct_used"] == 20.0


class TestHardStop:
    def test_enforce_hard_stop_delegates_to_orchestrator(self, monkeypatch):
        import agent_friday.services.orchestrator as orch
        calls = {}

        class FakeOrch:
            def cancel_worker(self, wid):
                calls["wid"] = wid
                return True
        monkeypatch.setattr(orch, "get_orchestrator", lambda: FakeOrch())
        assert be.enforce_hard_stop("worker-123") is True
        assert calls["wid"] == "worker-123"

    def test_enforce_hard_stop_swallows_errors(self, monkeypatch):
        import agent_friday.services.orchestrator as orch
        monkeypatch.setattr(orch, "get_orchestrator",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert be.enforce_hard_stop("w") is False
