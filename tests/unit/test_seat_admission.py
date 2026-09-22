"""Contract for Defect F: honest seat admission and cancellation teardown.

Red-first ground truth for `agent_friday.services.seat_admission`.

Two invariants pinned here:

1. Admission honesty — the seat a task queues under must be the seat it will
   actually run on. An empty/missing/whitespace `model` field resolves through
   the router's real default-seat resolver (injected), never a hardcoded
   cloud assumption. A faulting resolver fails LOCAL (the constrained seat),
   so a fault can never exempt a task from queueing.

2. Cancellation teardown — cancelling a task sets terminal status
   'cancelled', stamps `ended_at`, and fires `on_task_end` exactly once so
   the seat frees and FIFO promotes. Idempotent; teardown errors still
   terminalize the record; completed records are never un-terminalized.
"""

import pytest

from agent_friday.services import seat_admission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_record(task_id="t-1", model="", status="running"):
    return {
        "id": task_id,
        "model": model,
        "status": status,
        "ended_at": None,
    }


def default_resolver_local():
    """Router default-seat resolver that says: default is the local seat."""
    return "local/bonsai2-local"


def default_resolver_cloud():
    return "cloud/sonnet"


# ---------------------------------------------------------------------------
# 1. Admission honesty
# ---------------------------------------------------------------------------

class TestResolveAdmissionSeat:
    def test_empty_model_uses_default_resolver(self):
        seat = seat_admission.resolve_admission_seat(
            make_record(model=""), default_seat_resolver=default_resolver_local
        )
        assert seat == "local/bonsai2-local"

    def test_missing_model_key_uses_default_resolver(self):
        record = make_record()
        del record["model"]
        seat = seat_admission.resolve_admission_seat(
            record, default_seat_resolver=default_resolver_local
        )
        assert seat == "local/bonsai2-local"

    def test_whitespace_model_uses_default_resolver(self):
        seat = seat_admission.resolve_admission_seat(
            make_record(model="   "), default_seat_resolver=default_resolver_local
        )
        assert seat == "local/bonsai2-local"

    def test_declared_local_model_resolves_local(self):
        seat = seat_admission.resolve_admission_seat(
            make_record(model="bonsai2:27b"),
            default_seat_resolver=default_resolver_cloud,
        )
        assert seat.startswith("local/")

    def test_declared_cloud_model_resolves_cloud(self):
        seat = seat_admission.resolve_admission_seat(
            make_record(model="claude-sonnet"),
            default_seat_resolver=default_resolver_local,
        )
        assert seat.startswith("cloud/")

    def test_default_resolver_returning_cloud_is_respected(self):
        # An honest cloud default is allowed — only *faults* must fail local.
        seat = seat_admission.resolve_admission_seat(
            make_record(model=""), default_seat_resolver=default_resolver_cloud
        )
        assert seat == "cloud/sonnet"

    def test_resolver_raising_fails_local(self):
        def broken_resolver():
            raise RuntimeError("resolver exploded")

        seat = seat_admission.resolve_admission_seat(
            make_record(model=""), default_seat_resolver=broken_resolver
        )
        assert seat.startswith("local/")

    def test_resolver_returning_none_fails_local(self):
        seat = seat_admission.resolve_admission_seat(
            make_record(model=""), default_seat_resolver=lambda: None
        )
        assert seat.startswith("local/")


# ---------------------------------------------------------------------------
# 2. Cancellation teardown
# ---------------------------------------------------------------------------

class TestCancelTask:
    def test_cancel_sets_terminal_status(self):
        record = make_record()
        seat_admission.cancel_task(record, on_task_end=lambda r: None)
        assert record["status"] == "cancelled"

    def test_cancel_stamps_ended_at(self):
        record = make_record()
        seat_admission.cancel_task(record, on_task_end=lambda r: None)
        assert record["ended_at"] is not None

    def test_cancel_fires_on_task_end_exactly_once(self):
        calls = []
        record = make_record()
        seat_admission.cancel_task(record, on_task_end=calls.append)
        assert len(calls) == 1
        assert calls[0] is record

    def test_cancel_is_idempotent(self):
        calls = []
        record = make_record()
        seat_admission.cancel_task(record, on_task_end=calls.append)
        seat_admission.cancel_task(record, on_task_end=calls.append)
        assert record["status"] == "cancelled"
        assert len(calls) == 1  # teardown fires once, not twice

    def test_teardown_raising_still_terminalizes(self):
        def broken_teardown(record):
            raise RuntimeError("teardown exploded")

        record = make_record()
        seat_admission.cancel_task(record, on_task_end=broken_teardown)
        # The zombie-killer: even if seat-freeing fails, the record must not
        # be left status='running'.
        assert record["status"] == "cancelled"
        assert record["ended_at"] is not None

    def test_completed_record_is_not_unterminalized(self):
        calls = []
        record = make_record(status="completed")
        record["ended_at"] = "2026-09-22T06:00:00"
        seat_admission.cancel_task(record, on_task_end=calls.append)
        assert record["status"] == "completed"  # terminal stays terminal
        assert calls == []  # no double teardown for an already-ended task
