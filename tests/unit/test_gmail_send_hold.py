"""After approval a message waits (undo window, or its scheduled time) and can
be taken back; nothing goes before it is due, a cancelled one never goes, a
scheduled time is part of what was approved, and a message that fell due while
Friday was off is not sent late. Drafts need the mailbox permission."""
from __future__ import annotations

import base64
import email
import time
from datetime import datetime, timedelta, timezone

import pytest

from agent_friday.services import approvals
from agent_friday.services import gmail_send as gs

SENT: list = []
DRAFTS: list = []
NOTES: list = []


class _Svc:
    def users(self):
        class U:
            def messages(self):
                class M:
                    def send(self, userId=None, body=None):   # noqa: N803
                        SENT.append(body)

                        class E(dict):
                            def execute(self):
                                return self
                        return E(id="m%d" % len(SENT), threadId="t")
                return M()

            def drafts(self):
                class D:
                    def create(self, userId=None, body=None):   # noqa: N803
                        DRAFTS.append(body)

                        class E(dict):
                            def execute(self, num_retries=0):
                                return self
                        return E(id="d%d" % len(DRAFTS))
                return D()
        return U()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    SENT.clear(); DRAFTS.clear(); NOTES.clear()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(gs, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")
    monkeypatch.setattr(gs, "_held_path", lambda: tmp_path / "held.json")
    monkeypatch.setattr(gs, "_att_dir", lambda: tmp_path)
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [{"id": "acct_a", "email": "me@example.com"}])
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: True)
    monkeypatch.setattr(gs, "_notify", lambda title, body, **k: NOTES.append((title, body)))
    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", lambda *a, **k: _Svc(), raising=False)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    approvals.register_decision_hook(gs.APPROVAL_KIND, gs._on_decision)
    yield


def _ask(**kw):
    return gs.request_send(to="ada@example.com", subject="Plans", body="Hi.", **kw)["approval_id"]


def test_approval_holds_then_sends_when_due():
    aid = _ask()
    approvals.decide(aid, "approve")
    assert SENT == []                                        # not at once
    row = [r for r in gs.held() if r["approval_id"] == aid][0]
    assert 0 < row["seconds_left"] <= gs.UNDO_SECONDS
    assert gs._tick(now=time.time()) == []                   # still in the window
    assert gs._tick(now=row["due"] + 1) == [(aid, "sent")]
    assert len(SENT) == 1 and gs.held() == []


def test_undo_send_means_it_never_goes():
    aid = _ask()
    approvals.decide(aid, "approve")
    assert gs.cancel(aid)["sent"] is False
    assert gs._tick(now=time.time() + 3600) == [] and SENT == []
    with pytest.raises(gs.SendRefused):
        gs.send(aid)                                         # the approval is burned
    with pytest.raises(gs.SendRefused):
        gs.cancel(aid)                                       # nothing left to cancel


def test_send_later_waits_for_its_time_and_the_time_is_part_of_the_approval():
    when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    aid = _ask(send_at=when)
    card = approvals.get_approval(aid)
    assert "Scheduled" in card["action_description"]
    approvals.decide(aid, "approve")
    row = gs.held()[0]
    assert row["scheduled"] and row["due"] > time.time() + 7000
    assert gs._tick(now=time.time() + 60) == [] and SENT == []
    # moving the time after approval changes the message: refused
    recs = approvals._read_store()
    for r in recs:
        if r["approval_id"] == aid:
            r["payload"]["extras"]["send_at"] = datetime.now(timezone.utc).isoformat()
    approvals._write_store(recs)
    with pytest.raises(gs.SendRefused):
        gs.send(aid)
    assert SENT == []


def test_a_bad_or_past_send_time_is_refused_before_anyone_is_asked():
    with pytest.raises(gs.SendRefused):
        _ask(send_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
    with pytest.raises(gs.SendRefused):
        _ask(send_at="next tuesday-ish")


def test_due_while_friday_was_off_is_not_sent_late():
    aid = _ask()
    approvals.decide(aid, "approve")
    due = gs.held()[0]["due"]
    assert gs._tick(now=due + gs.LATE_LIMIT_S + 60) == [(aid, "missed")]
    assert SENT == [] and any("NOT sent" in t for t, _ in NOTES)
    with pytest.raises(gs.SendRefused):
        gs.send(aid)


def test_drafts_need_the_mailbox_permission(monkeypatch):
    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "get_account", lambda aid: {"id": aid, "email": "me@example.com", "scopes": [ga.GMAIL_READ, ga.GMAIL_SEND]})
    with pytest.raises(gs.SendRefused):
        gs.save_draft(account_id="acct_a", to="ada@example.com", subject="S", body="B")
    monkeypatch.setattr(ga, "get_account", lambda aid: {"id": aid, "email": "me@example.com", "scopes": [ga.GMAIL_READ, ga.GMAIL_MODIFY]})
    out = gs.save_draft(account_id="acct_a", to="ada@example.com", subject="Re: S", body="B",
                        thread_id="t9", in_reply_to="<a@m>")
    assert out["draft_id"] == "d1" and SENT == []
    msg = DRAFTS[0]["message"]
    assert msg["threadId"] == "t9"
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(msg["raw"]))
    assert parsed["To"] == "ada@example.com" and parsed["In-Reply-To"] == "<a@m>"
