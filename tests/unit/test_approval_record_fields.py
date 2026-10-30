"""An approval record carries what it authorises as a fingerprint, where it stands (including
revoked and consumed), and which device decided it (NS-30.11-1).

Before: a card had no action fingerprint, so one whose payload was changed outside the approvals
module after the owner approved it (a bug, a stray edit) could still be spent on the changed action; "revoked" and "consumed" were not states a
reader could see; and nothing said from where a decision came. Synthetic cards only.
"""
from __future__ import annotations

import pytest

from agent_friday.services import approvals


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(approvals, "_HOOKS", {})


def _card(**payload):
    rec = approvals.create_approval(kind="tool_confirm", subject_type="tool", subject_id="synthetic",
                                    title="Run the synthetic action", action_description="run it",
                                    force_gate=True, payload=payload or {"tool": "synthetic", "input": {"a": 1}})
    return rec["approval_id"]


def test_a_card_carries_a_fingerprint_of_what_it_authorises():
    aid = _card()
    assert approvals.get_approval(aid).get("action_fingerprint")


def test_a_card_changed_on_disk_after_approval_is_never_run_or_spent():
    aid = _card()
    approvals.decide(aid, "approve")
    rec = approvals.get_approval(aid)
    rec["payload"]["input"] = {"a": 999}              # changed outside the approvals module
    approvals._upsert(rec)
    assert approvals.claim_for_execution(aid) is False, "a changed card ran"
    assert approvals.spend_once(aid, "synthetic") is False, "a changed card was spent"


def test_a_change_the_module_makes_itself_keeps_the_card_usable():
    aid = _card()
    approvals._patch(aid, payload={"tool": "synthetic", "input": {"a": 2}})
    approvals.decide(aid, "approve")
    assert approvals.spend_once(aid, "synthetic") is True


def test_the_lifecycle_says_consumed_and_revoked():
    used = _card()
    approvals.decide(used, "approve")
    assert approvals.spend_once(used, "synthetic")
    assert approvals.get_approval(used).get("lifecycle") == "consumed"
    taken = _card(tool="synthetic", input={"b": 1})
    approvals.decide(taken, "approve")
    approvals.withdraw_unused_approved(by="authority", note="revoked")
    assert approvals.get_approval(taken).get("lifecycle") == "revoked"
    assert {r["approval_id"]: r.get("lifecycle") for r in approvals.list_approvals()}[used] == "consumed"


def test_the_record_says_which_device_decided():
    aid = _card()
    approvals.decide(aid, "deny")
    assert approvals.get_approval(aid).get("decided_device")
    assert approvals.decided_device("text_message") == "owner_phone"
    assert approvals.decided_device("desktop_press") == "this_pc"
