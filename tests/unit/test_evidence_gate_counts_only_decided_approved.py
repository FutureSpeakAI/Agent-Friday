"""The evidence gate: a raised approval card is not a completed task.

A task is 'verified' when it used a tool other than spawning another task. A tool that
raised a card and stopped ("[APPROVAL CARD RAISED] ... was NOT executed"), was held, was
declined or denied, or errored did nothing; counting it called a task whose only action
was waiting for the owner's click "complete". A card counts only once it is decided and
approved, and then the tool ran and its own result says so.
"""
from __future__ import annotations

from agent_friday.services import agent

RAISED = "[APPROVAL CARD RAISED] 'send_email' was NOT executed. Sending it needs the owner's approval."


def _t(name, result="ok: done"):
    return {"name": name, "input": {}, "result": result}


DID_NOTHING = [
    RAISED,
    "[CONFIRMATION REQUIRED] 'write_file' waits for a yes.",
    "[GOVERNANCE HOLD] held",
    "[GOVERNANCE DENY] no",
    "[DECLINED] the owner said no",
    "[NOT RUN] declined",
    "[SANDBOX DENY] outside the sandbox",
    "[BLOCKED by the taint gate]",
    "Tool error (send_email): boom",
    "TOOL CALL FAILED - no tool named 'x' exists",
]


def test_a_tool_that_did_nothing_is_not_evidence():
    for result in DID_NOTHING:
        verified, summary, status = agent._evidence_verdict([_t("send_email", result)])
        assert verified is False and status == "completed_unverified", (result, summary)


def test_a_raised_card_beside_a_real_read_is_verified_only_by_the_read():
    verified, summary, status = agent._evidence_verdict([_t("send_email", RAISED), _t("read_file", "text")])
    assert verified and status == "complete" and summary == "read_file"


def test_an_approved_and_executed_tool_counts():
    verified, _s, status = agent._evidence_verdict([_t("send_email", "Sent to Dana at 9:02.")])
    assert verified and status == "complete"


def test_spawning_and_nothing_still_do_not_count():
    assert agent._evidence_verdict([_t("spawn_task")])[0] is False
    assert agent._evidence_verdict([])[0] is False
