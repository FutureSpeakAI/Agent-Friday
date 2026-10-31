"""Two sends at once must not both go out (store-lane hardening).

- An approval is burned by a check-and-set against the STORED record. Two senders that each read
  the card before either burned it used to both be told they were first, and both messages went
  (mail, approved texts and calls alike).
- Two identical requests at once make one card, not two.
- Two texts to the owner at once cannot both pass the hourly cap.
- A send Gmail did not confirm (a timeout, a server error) is recorded as "may have been sent",
  never as a refusal that invites a blind resend.
Network faked throughout; synthetic addresses only.
"""
from __future__ import annotations

import threading

import pytest

from agent_friday.services import approvals
from agent_friday.services import gmail_send as gs
from tests.unit.phone_fakes import (OWNER, STRANGER, fake_twilio, phone_home, quiet,  # noqa: F401
                                    verify_owner)

SENT: list = []


class _Exec(dict):
    def __init__(self, data, boom=None):
        super().__init__(data)
        self._boom = boom

    def execute(self):
        if self._boom is not None:
            raise self._boom
        return self


class _Msgs:
    boom = None

    def send(self, userId=None, body=None):        # noqa: N803 (Google's API)
        SENT.append((body or {}).get("raw"))
        return _Exec({"id": "msg_%d" % len(SENT), "threadId": "thr_1"}, _Msgs.boom)


class _Users:
    def messages(self):
        return _Msgs()


class _Svc:
    def users(self):
        return _Users()


@pytest.fixture
def mail(tmp_path, monkeypatch):
    SENT.clear()
    _Msgs.boom = None
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(gs, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [
        {"id": "acct_a", "email": "owner@example.test", "label": "main"}])
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: True)
    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", lambda *a, **k: _Svc(), raising=False)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "record_external", lambda *a, **k: None)
    yield action_gate
    _Msgs.boom = None


def _approved(to="someone@example.test", subject="Hello", body="Synthetic body."):
    aid = gs.request_send(to=to, subject=subject, body=body)["approval_id"]
    approvals.decide(aid, "approve")
    return aid


# ── the burn is a compare-and-set on the stored record ──────────────────────

def test_a_stale_copy_of_an_approval_cannot_burn_it_a_second_time(mail):
    aid = _approved()
    snapshot = approvals.get_approval(aid)
    _rec, first = approvals._consume(dict(snapshot))
    assert first is True
    _rec, second = approvals._consume(dict(snapshot))
    assert second is False, "two holders of the same unburned copy were both told they were first"


def test_a_burn_that_did_not_reach_the_disk_does_not_count(mail, monkeypatch):
    """The store logs a failed write and carries on; a burn that was never saved would let the
    next sender win too, so it sends nothing."""
    aid = _approved()
    monkeypatch.setattr(approvals, "_write_store", lambda recs: None)
    with pytest.raises(gs.SendRefused):
        gs.send(aid)
    assert SENT == []


def test_a_send_that_never_left_is_not_called_unconfirmed(mail, monkeypatch):
    aid = _approved()

    def _no_client(*a, **k):
        raise RuntimeError("discovery document unavailable")
    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", _no_client, raising=False)
    with pytest.raises(gs.SendRefused) as err:
        gs.send(aid)
    assert not getattr(err.value, "unconfirmed", False)
    assert "may have been sent" not in str(err.value).lower()


def test_two_mail_sends_of_one_approval_at_once_send_one_message(mail, monkeypatch):
    """The second sender arrives after the first read the card and before it burned it (the
    receipt is written in between): the window the old burn left open."""
    aid = _approved()
    inner = []

    def receipt(*a, **k):
        if not inner:
            inner.append("racing")
            try:
                gs.send(aid)
                inner.append("sent")
            except gs.SendRefused:
                inner.append("refused")
    monkeypatch.setattr(mail, "record_external", receipt)
    outcome = None
    try:
        gs.send(aid)
        outcome = "sent"
    except gs.SendRefused:
        outcome = "refused"
    assert inner[:1] == ["racing"], "the race was not set up"
    assert len(SENT) == 1, "one decision sent %d messages" % len(SENT)
    assert sorted([inner[1], outcome]) == ["refused", "sent"]


def test_two_identical_requests_at_once_make_one_card(mail, monkeypatch):
    real = gs._live_card
    entered = threading.Event()
    calls = []

    def slow_live(fp):
        out = real(fp)
        calls.append(1)
        if len(calls) == 1:
            entered.wait(2.0)            # the other request gets here meanwhile, unless serialised
        else:
            entered.set()
        return out
    monkeypatch.setattr(gs, "_live_card", slow_live)
    ids = []

    def ask():
        ids.append(gs.request_send(to="someone@example.test", subject="Same", body="Same text.")
                   ["approval_id"])
    threads = [threading.Thread(target=ask) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(ids) == 2 and ids[0] == ids[1], "two cards for one message"
    assert len(approvals.list_approvals(kind=gs.APPROVAL_KIND, status="pending")) == 1


# ── a send Gmail did not confirm ────────────────────────────────────────────

def test_a_send_gmail_did_not_confirm_says_it_may_have_gone(mail):
    aid = _approved()
    _Msgs.boom = TimeoutError("the read timed out")
    with pytest.raises(gs.SendRefused) as err:
        gs.send(aid)
    assert len(SENT) == 1                                  # it reached Gmail
    assert getattr(err.value, "unconfirmed", False)
    assert "may have been sent" in str(err.value).lower()
    row = gs.outbox()[0]
    assert row["ok"] is False and row.get("unconfirmed") is True


def test_a_refusal_from_gmail_is_still_a_plain_not_sent(mail):
    aid = _approved()

    class _Resp(dict):
        status = 400
        reason = "Bad Request"
    from googleapiclient.errors import HttpError
    _Msgs.boom = HttpError(_Resp(), b'{"error": {"message": "invalid"}}')
    with pytest.raises(gs.SendRefused) as err:
        gs.send(aid)
    assert not getattr(err.value, "unconfirmed", False)
    assert not gs.outbox()[0].get("unconfirmed")


# ── texts and calls ─────────────────────────────────────────────────────────

def test_two_approved_texts_of_one_card_at_once_send_one(phone_home, fake_twilio, quiet,  # noqa: F811
                                                         monkeypatch):
    from agent_friday.phone import service
    verify_owner(monkeypatch)
    out = service.request_sms(to=STRANGER, body="running late",
                              owner_instruction="text 512-555-0199")  # pragma: allowlist secret
    monkeypatch.setattr(approvals, "_HOOKS", {})
    approvals.decide(out["approval_id"], "approve")
    real = service.checkpoint
    inner = []

    def racing(action, target, **kw):
        real(action, target, **kw)
        if not inner:
            inner.append("racing")
            try:
                service.send_approved_sms(out["approval_id"])
            except service.PhoneRefused:
                pass
    monkeypatch.setattr(service, "checkpoint", racing)
    try:
        service.send_approved_sms(out["approval_id"])
    except service.PhoneRefused:
        pass
    assert inner and fake_twilio.sent_bodies() == ["running late"], fake_twilio.sent_bodies()


def test_two_texts_to_the_owner_at_once_cannot_pass_the_cap(phone_home, fake_twilio, quiet,  # noqa: F811
                                                           monkeypatch):
    from agent_friday.phone import config, service
    verify_owner(monkeypatch)
    config.update({"owner_sms_per_hour": 1})
    passed = threading.Event()
    reached = threading.Event()
    real_cp = service.checkpoint
    real_req = fake_twilio.request

    def cp(*a, **k):
        real_cp(*a, **k)
        if threading.current_thread().name == "second":
            passed.set()
    monkeypatch.setattr(service, "checkpoint", cp)

    def req(method, url, **kw):
        if threading.current_thread().name == "first" and url.endswith("/Messages.json"):
            reached.set()
            passed.wait(2.0)             # the second text checks the cap meanwhile, unless serialised
        return real_req(method, url, **kw)
    monkeypatch.setattr(fake_twilio, "request", req)

    def text():
        try:
            service.text_owner("synthetic alert")
        except service.PhoneRefused:
            pass
    first = threading.Thread(target=text, name="first")
    second = threading.Thread(target=text, name="second")
    first.start()
    assert reached.wait(10), "the first text never reached Twilio"
    second.start()
    first.join(10)
    second.join(10)
    assert len(fake_twilio.sent_bodies()) == 1, "two texts went past a cap of one"


# ── any card spent by a call that runs on it ────────────────────────────────

def test_one_approved_card_lets_one_call_through_even_when_two_read_it_at_once(mail, monkeypatch):
    """action_gate.authorize_external read the card, then marked it used: a second caller holding
    the same unspent copy was let through too."""
    from agent_friday.governance import action_gate as ag8
    monkeypatch.setattr(ag8, "verify_claws", lambda: (True, ""), raising=False)
    monkeypatch.setattr(ag8, "outward_hold", lambda: "", raising=False)
    monkeypatch.setattr(ag8, "_receipt", lambda *a, **k: None, raising=False)
    detail = {"what": "synthetic outward change"}
    v = ag8.authorize_external("synthetic_action", detail, requested_by="synthetic")
    assert v.action == "card"
    card = [a for a in approvals.list_approvals(status="pending") if a.get("kind") == "governed_action"][0]
    approvals.decide(card["approval_id"], "approve")
    stale = approvals.get_approval(card["approval_id"])          # both callers read this copy
    monkeypatch.setattr(approvals, "find_for_subject", lambda *a, **k: dict(stale))
    first = ag8.authorize_external("synthetic_action", detail, requested_by="synthetic")
    second = ag8.authorize_external("synthetic_action", detail, requested_by="synthetic")
    assert [first.action, second.action].count("allow") == 1, (first, second)


def test_spend_once_is_true_for_exactly_one_caller(mail):
    aid = _approved()
    assert approvals.spend_once(aid, "first") is True
    assert approvals.spend_once(aid, "second") is False
    assert approvals.get_approval(aid)["used_by"] == "first"