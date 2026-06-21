"""Unit tests for dynamic_rings.py — zero-trust privilege state machine.

Tests the PrivilegeState tracker and DynamicPrivilegeManager for:
  - Initial ring state
  - Single-use elevation
  - Ring-3 user-confirmation requirement
  - check_and_consume single-use semantics
  - Insufficient-ring denial
  - drop_to_zero reset
  - Logging to a temp file

All tests use a temp dir so no ~/.friday/vault is touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from dynamic_rings import DynamicPrivilegeManager, PrivilegeState


# ════════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════════

@pytest.fixture
def log_file(tmp_path) -> Path:
    return tmp_path / "privilege-log.jsonl"


@pytest.fixture
def mgr(log_file) -> DynamicPrivilegeManager:
    """Fresh manager with isolated log file."""
    return DynamicPrivilegeManager(log_path=log_file)


# ════════════════════════════════════════════════════════════════════════
#  PrivilegeState (unit)
# ════════════════════════════════════════════════════════════════════════

class TestPrivilegeStateInitial:
    def test_starts_at_ring_zero(self):
        ps = PrivilegeState("task1")
        assert ps.current_ring == 0

    def test_max_ring_starts_zero(self):
        ps = PrivilegeState("task1")
        assert ps.max_ring == 0

    def test_not_elevated_initially(self):
        ps = PrivilegeState("task1")
        assert ps.is_elevated() is False

    def test_not_pending_confirm_initially(self):
        ps = PrivilegeState("task1")
        assert ps.needs_confirm() is False

    def test_consume_without_elevation_returns_current_ring(self):
        ps = PrivilegeState("task1")
        ring = ps.consume_elevation()
        assert ring == 0

    def test_set_confirmed_clears_pending(self):
        ps = PrivilegeState("task1")
        ps._pending_confirm = True
        ps.set_confirmed()
        assert ps.needs_confirm() is False


# ════════════════════════════════════════════════════════════════════════
#  DynamicPrivilegeManager — governance_elevate
# ════════════════════════════════════════════════════════════════════════

class TestGovernanceElevate:
    def test_ring1_elevation_granted(self, mgr):
        entry = mgr.governance_elevate(ring=1, reason="test", tool="read_file")
        assert entry["granted"] is True
        assert entry["ring"] == 1

    def test_ring2_elevation_granted(self, mgr):
        entry = mgr.governance_elevate(ring=2, reason="test", tool="write_file")
        assert entry["granted"] is True

    def test_ring3_without_confirm_not_granted(self, mgr):
        entry = mgr.governance_elevate(ring=3, reason="os_control",
                                        tool="run_cmd", user_confirmed=False)
        assert entry["granted"] is False

    def test_ring3_without_confirm_sets_pending(self, mgr):
        mgr.governance_elevate(ring=3, reason="os_control",
                                tool="run_cmd", user_confirmed=False)
        state = mgr.get_state("default")
        assert state.needs_confirm() is True

    def test_ring3_with_confirm_granted(self, mgr):
        entry = mgr.governance_elevate(ring=3, reason="os_control",
                                        tool="run_cmd", user_confirmed=True)
        assert entry["granted"] is True

    def test_ring3_with_confirm_clears_pending(self, mgr):
        mgr.governance_elevate(ring=3, reason="os_control",
                                tool="run_cmd", user_confirmed=False)
        # Now confirm
        mgr.governance_elevate(ring=3, reason="os_control",
                                tool="run_cmd", user_confirmed=True)
        state = mgr.get_state("default")
        assert state.needs_confirm() is False

    def test_elevation_sets_current_ring(self, mgr):
        mgr.governance_elevate(ring=2, reason="test", tool="write_file")
        state = mgr.get_state("default")
        assert state.current_ring == 2

    def test_elevation_updates_max_ring(self, mgr):
        mgr.governance_elevate(ring=2, reason="test", tool="t1")
        mgr.governance_elevate(ring=1, reason="test", tool="t2")
        state = mgr.get_state("default")
        assert state.max_ring == 2

    def test_entry_contains_task_id(self, mgr):
        entry = mgr.governance_elevate(ring=1, reason="test", tool="t",
                                        task_id="my_task")
        assert entry["task_id"] == "my_task"

    def test_entry_contains_op(self, mgr):
        entry = mgr.governance_elevate(ring=1, reason="r", tool="t")
        assert entry["op"] == "elevate"

    def test_pending_entry_op(self, mgr):
        entry = mgr.governance_elevate(ring=3, reason="r", tool="t",
                                        user_confirmed=False)
        assert entry["op"] == "elevate_pending"

    def test_multiple_task_ids_isolated(self, mgr):
        mgr.governance_elevate(ring=2, reason="r", tool="t", task_id="task_a")
        mgr.governance_elevate(ring=1, reason="r", tool="t", task_id="task_b")
        state_a = mgr.get_state("task_a")
        state_b = mgr.get_state("task_b")
        assert state_a.current_ring == 2
        assert state_b.current_ring == 1


# ════════════════════════════════════════════════════════════════════════
#  check_and_consume — single-use semantics
# ════════════════════════════════════════════════════════════════════════

class TestCheckAndConsume:
    def test_first_consume_works(self, mgr):
        mgr.governance_elevate(ring=2, reason="test", tool="write_file")
        allowed, reason, ring = mgr.check_and_consume("write_file", required_ring=2)
        assert allowed is True
        assert ring == 2

    def test_second_consume_fails(self, mgr):
        """Elevation is single-use — second call must be denied."""
        mgr.governance_elevate(ring=2, reason="test", tool="write_file")
        mgr.check_and_consume("write_file", required_ring=2)   # consumes
        allowed, reason, ring = mgr.check_and_consume("write_file", required_ring=2)
        assert allowed is False

    def test_consume_drops_back_to_ring_zero(self, mgr):
        mgr.governance_elevate(ring=2, reason="test", tool="write_file")
        mgr.check_and_consume("write_file", required_ring=2)
        state = mgr.get_state("default")
        assert state.current_ring == 0
        assert state.is_elevated() is False

    def test_elevated_to_2_cannot_cover_ring_3(self, mgr):
        mgr.governance_elevate(ring=2, reason="test", tool="cmd",
                                user_confirmed=True)
        allowed, reason, ring = mgr.check_and_consume("cmd", required_ring=3)
        assert allowed is False

    def test_elevated_to_3_covers_ring_2(self, mgr):
        mgr.governance_elevate(ring=3, reason="test", tool="cmd",
                                user_confirmed=True)
        allowed, reason, ring = mgr.check_and_consume("cmd", required_ring=2)
        assert allowed is True

    def test_elevated_to_3_covers_ring_1(self, mgr):
        mgr.governance_elevate(ring=3, reason="test", tool="cmd",
                                user_confirmed=True)
        allowed, reason, ring = mgr.check_and_consume("cmd", required_ring=1)
        assert allowed is True

    def test_not_elevated_ring_0_denied_for_ring_1(self, mgr):
        allowed, reason, ring = mgr.check_and_consume("some_tool", required_ring=1)
        assert allowed is False

    def test_returns_three_tuple(self, mgr):
        result = mgr.check_and_consume("any_tool", required_ring=1)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_reason_string_returned(self, mgr):
        mgr.governance_elevate(ring=2, reason="test", tool="t")
        allowed, reason, ring = mgr.check_and_consume("t", required_ring=2)
        assert isinstance(reason, str)
        assert len(reason) > 0

    def test_task_isolated_consume(self, mgr):
        mgr.governance_elevate(ring=2, reason="r", tool="t", task_id="t_a")
        # task_b has no elevation
        allowed, _, _ = mgr.check_and_consume("t", required_ring=2, task_id="t_b")
        assert allowed is False
        # task_a should still work
        allowed_a, _, _ = mgr.check_and_consume("t", required_ring=2, task_id="t_a")
        assert allowed_a is True

    def test_ring3_unconfirmed_then_consume_denied(self, mgr):
        """Pending confirmation means NOT elevated yet — consume should deny."""
        mgr.governance_elevate(ring=3, reason="r", tool="t", user_confirmed=False)
        allowed, reason, ring = mgr.check_and_consume("t", required_ring=3)
        assert allowed is False


# ════════════════════════════════════════════════════════════════════════
#  drop_to_zero
# ════════════════════════════════════════════════════════════════════════

class TestDropToZero:
    def test_drop_resets_current_ring(self, mgr):
        mgr.governance_elevate(ring=2, reason="r", tool="t")
        mgr.drop_to_zero()
        state = mgr.get_state("default")
        assert state.current_ring == 0

    def test_drop_clears_elevation_flag(self, mgr):
        mgr.governance_elevate(ring=2, reason="r", tool="t")
        mgr.drop_to_zero()
        state = mgr.get_state("default")
        assert state.is_elevated() is False

    def test_drop_after_elevation_consume_fails(self, mgr):
        mgr.governance_elevate(ring=2, reason="r", tool="t")
        mgr.drop_to_zero()
        allowed, _, _ = mgr.check_and_consume("t", required_ring=1)
        assert allowed is False

    def test_drop_on_default_task(self, mgr):
        mgr.governance_elevate(ring=3, reason="r", tool="t", user_confirmed=True)
        mgr.drop_to_zero()
        state = mgr.get_state("default")
        assert state.current_ring == 0

    def test_drop_then_elevate_again_works(self, mgr):
        mgr.governance_elevate(ring=2, reason="r", tool="t")
        mgr.drop_to_zero()
        mgr.governance_elevate(ring=2, reason="r2", tool="t2")
        allowed, _, _ = mgr.check_and_consume("t2", required_ring=2)
        assert allowed is True


# ════════════════════════════════════════════════════════════════════════
#  Logging
# ════════════════════════════════════════════════════════════════════════

class TestPrivilegeLogging:
    def test_log_file_created_on_elevate(self, mgr, log_file):
        mgr.governance_elevate(ring=1, reason="test", tool="t")
        assert log_file.exists()

    def test_log_contains_valid_jsonl(self, mgr, log_file):
        mgr.governance_elevate(ring=1, reason="test", tool="t")
        lines = [l.strip() for l in log_file.read_text("utf-8").splitlines() if l.strip()]
        for line in lines:
            json.loads(line)  # must not raise

    def test_log_entry_has_required_fields(self, mgr, log_file):
        mgr.governance_elevate(ring=1, reason="test", tool="my_tool")
        lines = [json.loads(l) for l in log_file.read_text("utf-8").splitlines() if l.strip()]
        entry = lines[0]
        for field in ("op", "task_id", "ring", "tool", "reason", "granted", "ts"):
            assert field in entry, f"Missing field: {field}"

    def test_get_privilege_log_empty_when_no_file(self, tmp_path):
        mgr = DynamicPrivilegeManager(log_path=tmp_path / "nonexistent.jsonl")
        logs = mgr.get_privilege_log()
        assert logs == []

    def test_get_privilege_log_returns_entries(self, mgr):
        mgr.governance_elevate(ring=1, reason="r", tool="t")
        logs = mgr.get_privilege_log()
        assert len(logs) >= 1

    def test_deny_logged_on_insufficient_ring(self, mgr, log_file):
        mgr.check_and_consume("t", required_ring=2)
        lines = [json.loads(l) for l in log_file.read_text("utf-8").splitlines() if l.strip()]
        deny_entries = [e for e in lines if e["op"] == "deny"]
        assert len(deny_entries) >= 1

    def test_drop_logged(self, mgr, log_file):
        mgr.drop_to_zero()
        lines = [json.loads(l) for l in log_file.read_text("utf-8").splitlines() if l.strip()]
        drop_entries = [e for e in lines if e["op"] == "drop"]
        assert len(drop_entries) >= 1

    def test_log_limit_respected(self, mgr):
        for i in range(5):
            mgr.governance_elevate(ring=1, reason="r", tool=f"t{i}")
        logs = mgr.get_privilege_log(limit=2)
        assert len(logs) <= 2


# ════════════════════════════════════════════════════════════════════════
#  end_task
# ════════════════════════════════════════════════════════════════════════

class TestEndTask:
    def test_end_task_removes_state(self, mgr):
        mgr.governance_elevate(ring=1, reason="r", tool="t", task_id="task_x")
        mgr.end_task("task_x")
        # After end_task, get_state creates a fresh PrivilegeState
        state = mgr.get_state("task_x")
        assert state.current_ring == 0
        assert state.is_elevated() is False

    def test_end_nonexistent_task_no_error(self, mgr):
        mgr.end_task("no_such_task")  # should not raise


# ════════════════════════════════════════════════════════════════════════
#  get_state — lazy creation
# ════════════════════════════════════════════════════════════════════════

class TestGetState:
    def test_new_task_returns_ring_zero_state(self, mgr):
        state = mgr.get_state("brand_new")
        assert state.current_ring == 0
        assert state.task_id == "brand_new"

    def test_same_task_id_returns_same_state(self, mgr):
        mgr.governance_elevate(ring=1, reason="r", tool="t", task_id="shared")
        state1 = mgr.get_state("shared")
        state2 = mgr.get_state("shared")
        assert state1 is state2


# ════════════════════════════════════════════════════════════════════════
#  Numeric / boundary edge cases
# ════════════════════════════════════════════════════════════════════════

class TestBoundaryEdgeCases:
    def test_ring_zero_elevation_still_granted(self, mgr):
        """Ring 0 elevation is trivially granted (no confirmation needed)."""
        entry = mgr.governance_elevate(ring=0, reason="r", tool="t")
        assert entry["granted"] is True

    def test_ring_4_requires_confirm_like_ring_3(self, mgr):
        """Any ring >= 3 requires user_confirmed."""
        entry = mgr.governance_elevate(ring=4, reason="r", tool="t",
                                        user_confirmed=False)
        assert entry["granted"] is False

    def test_ring_4_with_confirm_granted(self, mgr):
        entry = mgr.governance_elevate(ring=4, reason="r", tool="t",
                                        user_confirmed=True)
        assert entry["granted"] is True

    def test_consume_ring_covers_lower_rings(self, mgr):
        mgr.governance_elevate(ring=3, reason="r", tool="t", user_confirmed=True)
        allowed, _, eff = mgr.check_and_consume("t", required_ring=0)
        assert allowed is True

    def test_second_elevate_after_consume_works(self, mgr):
        mgr.governance_elevate(ring=2, reason="r", tool="t")
        mgr.check_and_consume("t", required_ring=2)
        # Re-elevate
        mgr.governance_elevate(ring=2, reason="r", tool="t")
        allowed, _, _ = mgr.check_and_consume("t", required_ring=2)
        assert allowed is True

    def test_elevated_is_true_after_confirm_grant(self, mgr):
        mgr.governance_elevate(ring=3, reason="r", tool="t", user_confirmed=True)
        state = mgr.get_state("default")
        assert state.is_elevated() is True

    def test_pending_confirm_is_not_elevated(self, mgr):
        mgr.governance_elevate(ring=3, reason="r", tool="t", user_confirmed=False)
        state = mgr.get_state("default")
        assert state.is_elevated() is False
        assert state.needs_confirm() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
