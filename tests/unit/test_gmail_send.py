"""Falsification tests for services/gmail_send.py.

WHAT THESE TESTS ARE FOR. Friday gained the ability to send mail on
2026-09-20, on one condition: only with an explicit human decision behind
each message. Every other property of this module is a convenience. This one
is the reason it was allowed to exist, so the tests are written to try to get
a message out WITHOUT that decision, not to confirm that the happy path
works.

A passing test is only evidence if it could have failed. So `_FakeGmail`
below records every delivery, and the refusal tests assert the recorder is
still empty — an assertion that fails loudly the moment a refusal stops
refusing. `test_the_fake_can_actually_send` exists to prove the recorder is
not vacuously empty because the plumbing is broken.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import approvals
from agent_friday.services import gmail_send as gs


SENT: list = []


class _FakeMessages:
    def send(self, userId=None, body=None):        # noqa: N803 (Google's API)
        SENT.append({"userId": userId, "raw": (body or {}).get("raw")})
        return _FakeExecute({"id": "msg_%d" % len(SENT), "threadId": "thr_1"})


class _FakeExecute(dict):
    def execute(self):
        return self


class _FakeUsers:
    def messages(self):
        return _FakeMessages()


class _FakeService:
    def users(self):
        return _FakeUsers()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """No real queue, no real outbox, no real network, no real ~/.friday."""
    SENT.clear()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(gs, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")

    # One account, granted send.
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [
        {"id": "acct_a", "email": "stephen@example.com", "label": "main"}])
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: True)

    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())

    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build",
                        lambda *a, **k: _FakeService(), raising=False)

    # The decision hook sends on a background thread, which would make these
    # tests race. Each test drives send() itself, so the hook is unhooked.
    monkeypatch.setattr(approvals, "_HOOKS", {})
    yield


def _queue(to="someone@example.com", subject="Hello", body="Body text."):
    return gs.request_send(to=to, subject=subject, body=body)["approval_id"]


# ═══════════════════════════════════════════════════════════════════════════
#  The recorder works — without this, every assertion below is vacuous
# ═══════════════════════════════════════════════════════════════════════════

def test_the_fake_can_actually_send():
    approval_id = _queue()
    approvals.decide(approval_id, "approve")
    result = gs.send(approval_id)
    assert result["ok"] is True
    assert len(SENT) == 1, "the fake Gmail never recorded a delivery"


# ═══════════════════════════════════════════════════════════════════════════
#  Asking is not sending
# ═══════════════════════════════════════════════════════════════════════════

def test_requesting_sends_nothing():
    _queue()
    assert SENT == []


def test_request_leaves_the_card_pending_not_approved():
    approval_id = _queue()
    assert approvals.get_approval(approval_id)["status"] == "pending"


def test_send_refuses_a_pending_card():
    approval_id = _queue()
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


def test_send_refuses_a_denied_card():
    approval_id = _queue()
    approvals.decide(approval_id, "deny")
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


def test_send_refuses_an_unknown_approval():
    with pytest.raises(gs.SendRefused):
        gs.send("appr_doesnotexist")
    assert SENT == []


def test_send_refuses_an_expired_card():
    approval_id = _queue()
    rec = approvals.get_approval(approval_id)
    rec["status"] = "expired"
    approvals._upsert(rec)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


# ═══════════════════════════════════════════════════════════════════════════
#  The policy table cannot be edited into an auto-send
# ═══════════════════════════════════════════════════════════════════════════

def test_ungating_external_message_does_not_ungate_mail(monkeypatch):
    """The owner may set any policy class to ungated. Mail ignores that.

    `force_gate=True` is what makes this true, and it is the difference
    between a setting that speeds up chores and a setting that quietly grants
    an unattended process the power to mail people.
    """
    table = {k: dict(v) for k, v in approvals.POLICY_TABLE_DEFAULTS.items()}
    table["external_message"]["gated"] = False
    monkeypatch.setattr(approvals, "effective_policy_table", lambda: table)
    approval_id = _queue()
    assert approvals.get_approval(approval_id)["status"] == "pending"
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


def test_send_refuses_a_hand_forged_auto_approval():
    """Even a card that says auto_approved is refused. Only `approved` sends."""
    approval_id = _queue()
    rec = approvals.get_approval(approval_id)
    rec["status"] = "auto_approved"
    approvals._upsert(rec)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


# ═══════════════════════════════════════════════════════════════════════════
#  One decision buys one message, of exactly the text that was decided on
# ═══════════════════════════════════════════════════════════════════════════

def test_an_approval_cannot_be_spent_twice():
    approval_id = _queue()
    approvals.decide(approval_id, "approve")
    gs.send(approval_id)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert len(SENT) == 1


def test_editing_the_body_after_approval_refuses():
    approval_id = _queue(body="Please review the draft.")
    approvals.decide(approval_id, "approve")
    rec = approvals.get_approval(approval_id)
    rec["payload"]["body"] = "Wire the money to account 4471."
    approvals._upsert(rec)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


def test_rewriting_body_and_fingerprint_together_still_refuses():
    """The tampering case a single self-consistent hash would wave through.

    An attacker editing the payload edits the fingerprint alongside it. The
    copy of the fingerprint in `subject_id` — written at creation, never
    rewritten — is what catches this.
    """
    approval_id = _queue(body="Please review the draft.")
    approvals.decide(approval_id, "approve")
    rec = approvals.get_approval(approval_id)
    rec["payload"]["body"] = "Wire the money to account 4471."
    rec["payload"]["fingerprint"] = gs.message_fingerprint(
        rec["payload"]["to"], rec["payload"]["subject"],
        rec["payload"]["body"], rec["payload"].get("cc"),
        rec["payload"].get("bcc"))
    approvals._upsert(rec)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


def test_swapping_the_recipient_after_approval_refuses():
    approval_id = _queue(to="colleague@example.com")
    approvals.decide(approval_id, "approve")
    rec = approvals.get_approval(approval_id)
    rec["payload"]["to"] = ["journalist@example.com"]
    approvals._upsert(rec)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


def test_re_requesting_an_identical_message_reuses_the_live_card():
    """Retrying must not spam the queue with duplicates..."""
    first = _queue()
    second = _queue()
    assert first == second
    pending = approvals.list_approvals(kind=gs.APPROVAL_KIND, status="pending")
    assert len(pending) == 1


def test_re_requesting_after_it_was_sent_asks_again():
    """...but a spent decision is spent. Sending it twice needs asking twice."""
    first = _queue()
    approvals.decide(first, "approve")
    gs.send(first)
    second = _queue()
    assert second != first
    assert approvals.get_approval(second)["status"] == "pending"
    assert len(SENT) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Permission is what Google granted, not what Friday asked for
# ═══════════════════════════════════════════════════════════════════════════

def test_no_sendable_account_refuses_at_request_time(monkeypatch):
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [])
    with pytest.raises(gs.SendRefused):
        _queue()
    assert SENT == []


def test_revoking_the_grant_between_approval_and_send_refuses(monkeypatch):
    approval_id = _queue()
    approvals.decide(approval_id, "approve")
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: False)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


def test_two_sendable_accounts_refuse_to_guess(monkeypatch):
    """Which address a message comes from cannot be corrected afterwards."""
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [
        {"id": "a", "email": "stephen@example.com", "label": "personal"},
        {"id": "b", "email": "stephen@work.example", "label": "work"}])
    with pytest.raises(gs.SendRefused):
        _queue()
    assert SENT == []


def test_naming_the_account_resolves_the_ambiguity(monkeypatch):
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [
        {"id": "a", "email": "stephen@example.com", "label": "personal"},
        {"id": "b", "email": "stephen@work.example", "label": "work"}])
    result = gs.request_send(to="x@example.com", subject="Hi", body="Text.",
                             account_id="b")
    payload = result["approval"]["payload"]
    assert payload["from_email"] == "stephen@work.example"
    assert "stephen@work.example" in result["approval"]["action_description"]


def test_a_read_only_account_cannot_be_named_into_sending(monkeypatch):
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [
        {"id": "a", "email": "stephen@example.com", "label": "personal"}])
    with pytest.raises(gs.SendRefused):
        gs.request_send(to="x@example.com", subject="Hi", body="Text.",
                        account_id="readonly_account")
    assert SENT == []


# ═══════════════════════════════════════════════════════════════════════════
#  Input the owner would not want repaired
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad", ["notanaddress", "a@b", "@example.com",
                                 "someone@example.com extra"])
def test_malformed_recipients_are_rejected_not_repaired(bad):
    with pytest.raises(gs.SendRefused):
        gs.request_send(to=bad, subject="Hi", body="Text.")


def test_empty_body_is_refused():
    with pytest.raises(gs.SendRefused):
        gs.request_send(to="x@example.com", subject="Hi", body="   ")


def test_no_recipients_is_refused():
    with pytest.raises(gs.SendRefused):
        gs.request_send(to=[], subject="Hi", body="Text.")


# ═══════════════════════════════════════════════════════════════════════════
#  The outcome is recorded, including when it fails
# ═══════════════════════════════════════════════════════════════════════════

def test_a_successful_send_is_recorded():
    approval_id = _queue()
    approvals.decide(approval_id, "approve")
    gs.send(approval_id)
    rows = gs.outbox()
    assert len(rows) == 1 and rows[0]["ok"] is True


def test_a_failed_send_is_recorded_too(monkeypatch):
    """An approved card whose send failed must not look like one that worked."""
    approval_id = _queue()
    approvals.decide(approval_id, "approve")

    def _boom(*a, **k):
        raise RuntimeError("Gmail said no")
    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", _boom, raising=False)

    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    rows = gs.outbox()
    assert len(rows) == 1
    assert rows[0]["ok"] is False and "Gmail said no" in (rows[0]["error"] or "")


def test_a_failed_send_does_not_leave_a_reusable_approval(monkeypatch):
    """Fail closed: ask again rather than risk sending the same thing twice."""
    approval_id = _queue()
    approvals.decide(approval_id, "approve")

    def _boom(*a, **k):
        raise RuntimeError("network down")
    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", _boom, raising=False)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)

    monkeypatch.undo()
    monkeypatch.setattr(_disc, "build",
                        lambda *a, **k: _FakeService(), raising=False)
    with pytest.raises(gs.SendRefused):
        gs.send(approval_id)
    assert SENT == []


# ═══════════════════════════════════════════════════════════════════════════
#  The hook only claims the cards it created
# ═══════════════════════════════════════════════════════════════════════════

def test_the_hook_ignores_other_external_messages():
    """Approving an unrelated external_message card must not reach send()."""
    other = approvals.create_approval(
        kind=gs.APPROVAL_KIND, subject_type="slack", subject_id="chan_1",
        title="Post to #general", action_description="Post an update.",
        force_gate=True, payload={"text": "hello"})
    gs._on_decision(dict(other, status="approved"))
    assert SENT == []


def test_the_hook_ignores_a_denial():
    approval_id = _queue()
    rec = approvals.get_approval(approval_id)
    gs._on_decision(dict(rec, status="denied"))
    assert SENT == []


# ═══════════════════════════════════════════════════════════════════════════
#  The agent tool asks; it does not send
# ═══════════════════════════════════════════════════════════════════════════

def test_no_agent_tool_can_deliver_a_message():
    """The model's reachable surface contains no send.

    This is the property that matters for the unattended loop: whatever the
    model decides at 3am, the tools it can call stop at "ask".
    """
    from agent_friday.services import agent
    names = set(agent.CLAUDE_TOOL_HANDLERS)
    assert "draft_email" in names
    for name in names:
        handler = agent.CLAUDE_TOOL_HANDLERS[name]
        assert gs.send is not handler, f"{name} is gmail_send.send"


def test_draft_email_tool_queues_and_says_it_did_not_send():
    from agent_friday.services import agent
    out = json.loads(agent.CLAUDE_TOOL_HANDLERS["draft_email"]({
        "to": "someone@example.com", "subject": "Hi", "body": "Text."}))
    assert out["sent"] is False and out["queued"] is True
    assert SENT == []
    assert approvals.get_approval(out["approval_id"])["status"] == "pending"
