"""Phase 1 ground truth - dispatcher honesty (Defects A and B).

Defect A: the empty-response fault apology the harness itself generates
(services/agent.py, the "[Friday returned an empty response twice in a row.
That is a fault on this end, not an answer ...]" string) is not listed in
_CHAIN_FAILURE_SIGNATURES, so a chain step carrying it advances as
"complete". Both overnight RSI runs (2026-09-20/21) died exactly this way.
These tests pin the contract: the harness's own fault apologies are provider
failures, never work product.

Defect B: nothing records per-step receipts on task records, so a
"completed" chain step cannot be judged shipped vs hollow after the fact.
These tests pin the contract for a task_receipts service:
record_task_receipt() accumulates what a step actually did, and
step_verdict() judges it: "shipped" (completed with side-effecting
receipts), "hollow" (completed with none), or "failed".
"""
from __future__ import annotations

import pytest

from agent_friday.services.agent import (
    _CHAIN_FAILURE_SIGNATURES,
    _looks_like_provider_failure,
)

# The exact fault text the harness generates when a seat returns empty
# content twice in a row. Kept verbatim so the signature must match
# production output, not a paraphrase.
PRODUCTION_FAULT = (
    "[Friday returned an empty response twice in a row. "
    "That is a fault on this end, not an answer - please try again.]"
)


class TestDefectAFaultSignatures:
    def test_fault_apology_in_signatures(self):
        assert any(
            "fault on this end" in sig for sig in _CHAIN_FAILURE_SIGNATURES
        ), "harness's own fault apology is not a recognized failure signature"

    def test_empty_response_in_signatures(self):
        assert any(
            "returned an empty response" in sig
            for sig in _CHAIN_FAILURE_SIGNATURES
        )

    def test_detects_production_fault_string(self):
        assert _looks_like_provider_failure(PRODUCTION_FAULT)

    def test_detects_fault_case_insensitively(self):
        assert _looks_like_provider_failure(PRODUCTION_FAULT.upper())

    def test_existing_signatures_still_detected(self):
        # Regression guard: the original list must keep working, and real
        # work product must not be flagged.
        assert _looks_like_provider_failure("Error: model not found")
        assert _looks_like_provider_failure("connection refused")
        assert not _looks_like_provider_failure(
            "Wrote services/task_receipts.py and ran the suite: all green."
        )


class TestDefectBTaskReceipts:
    def test_module_exists(self):
        import agent_friday.services.task_receipts  # noqa: F401

    def test_record_task_receipt_api(self):
        from agent_friday.services.task_receipts import record_task_receipt

        record_task_receipt(
            "task-api", tool="write_file", detail="services/x.py", ok=True
        )

    def test_step_verdict_shipped(self):
        from agent_friday.services.task_receipts import (
            record_task_receipt,
            step_verdict,
        )

        record_task_receipt(
            "task-shipped", tool="write_file", detail="services/x.py", ok=True
        )
        assert step_verdict("task-shipped", status="complete") == "shipped"

    def test_step_verdict_hollow_when_no_receipts(self):
        from agent_friday.services.task_receipts import step_verdict

        # Completed step, zero recorded receipts: hollow, never shipped.
        assert step_verdict("task-hollow-none", status="complete") == "hollow"

    def test_step_verdict_hollow_when_reads_only(self):
        from agent_friday.services.task_receipts import (
            record_task_receipt,
            step_verdict,
        )

        # A step that only read files did no work: hollow.
        record_task_receipt(
            "task-reads", tool="read_file",
            detail="services/agent.py", ok=True,
        )
        assert step_verdict("task-reads", status="complete") == "hollow"

    def test_step_verdict_failed(self):
        from agent_friday.services.task_receipts import step_verdict

        assert step_verdict("task-failed", status="failed") == "failed"


@pytest.mark.skip(
    reason="Phase 0 scope hardening not shipped (docs/rsi/phase0-verdict.md:"
    " FAILED); the workflow-step scope does not exist yet"
)
class TestPhase0Dependency:
    def test_workflow_step_scope_exists(self):
        from agent_friday.services.subagents import SCOPES  # type: ignore

        assert "workflow-step" in SCOPES
