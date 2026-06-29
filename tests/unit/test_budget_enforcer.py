"""Unit tests for services/budget_enforcer.py"""
import pytest
from agent_friday.services import budget_enforcer as be

be._ensure_schema()


def _ws(suffix=""):
    return f"test-budget-ws-{suffix}"


class TestPolicy:
    def test_get_policy_default(self):
        policy = be.get_policy(_ws("default-new"))
        assert "monthly_cap_mψ" in policy
        assert policy["monthly_cap_mψ"] > 0

    def test_set_and_get_policy(self):
        ws = _ws("set-get")
        be.set_policy(ws, monthly_cap_mψ=500_000, per_task_cap_mψ=50_000, warn_pct=70)
        p = be.get_policy(ws)
        assert p["monthly_cap_mψ"] == 500_000
        assert p["per_task_cap_mψ"] == 50_000
        assert p["warn_pct"] == 70

    def test_get_all_policies_returns_list(self):
        ws = _ws("all-list")
        be.set_policy(ws, monthly_cap_mψ=200_000)
        policies = be.get_all_policies()
        assert isinstance(policies, list)
        assert any(p["workspace"] == ws for p in policies)

    def test_update_existing_policy(self):
        ws = _ws("update")
        be.set_policy(ws, monthly_cap_mψ=100_000)
        be.set_policy(ws, monthly_cap_mψ=999_000)
        p = be.get_policy(ws)
        assert p["monthly_cap_mψ"] == 999_000


class TestReserveRelease:
    def test_reserve_succeeds_within_cap(self):
        ws = _ws("reserve-ok")
        be.set_policy(ws, monthly_cap_mψ=1_000_000)
        ok = be.reserve_budget(ws, 10_000)
        assert ok is True

    def test_reserve_fails_over_cap(self):
        ws = _ws("reserve-fail")
        be.set_policy(ws, monthly_cap_mψ=5_000)
        ok = be.reserve_budget(ws, 10_000)
        assert ok is False

    def test_reserve_zero_always_succeeds(self):
        ws = _ws("reserve-zero")
        be.set_policy(ws, monthly_cap_mψ=0)
        assert be.reserve_budget(ws, 0) is True

    def test_check_remaining_after_reserve(self):
        ws = _ws("remaining")
        be.set_policy(ws, monthly_cap_mψ=100_000)
        be.reserve_budget(ws, 30_000)
        remaining = be.check_remaining(ws)
        assert remaining <= 70_000  # at most 70k left

    def test_release_increases_remaining(self):
        ws = _ws("release")
        be.set_policy(ws, monthly_cap_mψ=100_000)
        be.reserve_budget(ws, 50_000)
        before = be.check_remaining(ws)
        be.release_budget(ws, 20_000)
        after = be.check_remaining(ws)
        assert after >= before  # should not decrease


class TestBudgetStatus:
    def test_budget_status_has_required_fields(self):
        ws = _ws("status-fields")
        be.set_policy(ws, monthly_cap_mψ=200_000)
        status = be.budget_status(ws)
        for field in ("workspace", "monthly_cap_mψ", "spent_mψ", "remaining_mψ",
                      "per_task_cap_mψ", "warn_pct", "pct_used"):
            assert field in status

    def test_budget_status_pct_used(self):
        ws = _ws("pct-used")
        be.set_policy(ws, monthly_cap_mψ=100_000)
        be.reserve_budget(ws, 50_000)
        status = be.budget_status(ws)
        assert status["pct_used"] >= 50.0

    def test_monthly_spend_returns_int(self):
        ws = _ws("monthly-spend")
        spend = be.monthly_spend(ws)
        assert isinstance(spend, int)
        assert spend >= 0


class TestHardStop:
    def test_hard_stop_unknown_worker(self):
        # Should not raise — just returns False
        ok = be.enforce_hard_stop("no-such-worker-id")
        assert isinstance(ok, bool)
