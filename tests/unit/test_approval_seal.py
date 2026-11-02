"""An approved card is a signed, one-time token (NS-18.6-1): it carries an HMAC with the governance
key over what it authorises, who decided it, from where and for which principal. Nothing spends a
card whose seal does not hold, and a card someone set to approved on disk is never sealed later.

Before: approval was a random id in approvals.json. Editing a pending card's status to "approved"
in the file, and rewriting its fingerprint to match a changed payload, made it spendable.
Synthetic cards in a scratch store only.
"""
from __future__ import annotations

import json

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


def _card(sid="seal-1", **payload):
    rec = approvals.create_approval(kind="tool_confirm", subject_type="tool", subject_id=sid,
                                    title="Run the synthetic action", action_description="run it",
                                    force_gate=True, payload=payload or {"tool": "synthetic", "input": {"a": 1}})
    return rec["approval_id"]


def _edit_on_disk(aid, **fields):
    """What another program writing approvals.json does: no approvals.py code runs."""
    rows = json.loads(approvals.APPROVALS_FILE.read_text(encoding="utf-8"))
    for r in rows:
        if r.get("approval_id") == aid:
            r.update(fields)
            if "payload" in fields:
                r["action_fingerprint"] = approvals._action_fp(r)    # the digest is not a secret
    approvals.APPROVALS_FILE.write_text(json.dumps(rows), encoding="utf-8")


def test_an_owner_approval_is_sealed_and_spends_once():
    aid = _card()
    approvals.decide(aid, "approve")
    assert approvals.get_approval(aid).get("approval_seal")
    assert approvals.seal_holds(approvals.get_approval(aid))
    assert approvals.spend_once(aid, "synthetic") is True
    assert approvals.spend_once(aid, "synthetic") is False


def test_a_card_set_to_approved_in_the_file_is_never_run_or_spent():
    aid = _card()
    _edit_on_disk(aid, status="approved", decided_by="owner", decided_source="desktop_press")
    assert approvals.claim_for_execution(aid) is False
    assert approvals.spend_once(aid, "synthetic") is False
    rec, first = approvals._consume(approvals.get_approval(aid))
    assert first is False


def test_a_later_write_does_not_launder_it():
    aid = _card()
    _edit_on_disk(aid, status="approved", decided_by="owner")
    other = _card("seal-2")
    approvals.decide(other, "approve")              # rewrites the whole store
    assert not approvals.get_approval(aid).get("approval_seal")
    assert approvals.spend_once(aid, "synthetic") is False
    assert approvals.spend_once(other, "synthetic") is True


def test_changing_what_an_approved_card_authorises_voids_it():
    aid = _card()
    approvals.decide(aid, "approve")
    _edit_on_disk(aid, payload={"tool": "synthetic", "input": {"a": 999}})
    assert approvals.spend_once(aid, "synthetic") is False


def test_the_seal_names_the_principal():
    aid = _card()
    approvals.decide(aid, "approve")
    _edit_on_disk(aid, principal="crew:helper")
    assert approvals.claim_for_execution(aid) is False


def test_a_change_this_module_makes_keeps_it_sealed():
    aid = _card()
    approvals.decide(aid, "approve")
    approvals._patch(aid, payload={"tool": "synthetic", "input": {"a": 2}})
    assert approvals.spend_once(aid, "synthetic") is True


def test_no_governance_key_means_nothing_is_spent(monkeypatch):
    aid = _card()
    approvals.decide(aid, "approve")

    def no_key():
        raise RuntimeError("synthetic: keychain unavailable")
    monkeypatch.setattr(approvals, "_seal_key", no_key)
    assert approvals.spend_once(aid, "synthetic") is False
