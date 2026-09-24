"""Dispatcher honesty: the harness's own fault text is a failure.

When a seat returns empty content twice in a row, services/agent.py writes a
fault apology into the step result. That text must match
_CHAIN_FAILURE_SIGNATURES, or a chain step carrying it advances as
"complete". The harness's own fault apologies are provider failures, never
work product.
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


@pytest.mark.skip(
    reason="Phase 0 scope hardening not shipped (docs/rsi/phase0-verdict.md:"
    " FAILED); the workflow-step scope does not exist yet"
)
class TestPhase0Dependency:
    def test_workflow_step_scope_exists(self):
        from agent_friday.services.subagents import SCOPES  # type: ignore

        assert "workflow-step" in SCOPES
