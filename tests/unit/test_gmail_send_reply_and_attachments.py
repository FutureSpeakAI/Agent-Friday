"""Replies thread, formatting and attachments go out as approved, and any
change to them after approval refuses the send (nothing is delivered)."""
from __future__ import annotations

import base64
import email

import pytest

from agent_friday.services import approvals
from agent_friday.services import gmail_send as gs

SENT: list = []


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
                        return E(id="msg_%d" % len(SENT), threadId=(body or {}).get("threadId") or "new")
                return M()
        return U()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    SENT.clear()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(gs, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")
    monkeypatch.setattr(gs, "_att_dir", lambda: tmp_path)
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [{"id": "acct_a", "email": "me@example.com"}])
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: True)
    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", lambda *a, **k: _Svc(), raising=False)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    yield


def _parsed(i=0):
    return email.message_from_bytes(base64.urlsafe_b64decode(SENT[i]["raw"]))


def test_reply_threads_and_carries_formatting_and_attachment():
    att = gs.store_attachment(b"%PDF-1.4 plan", "plan.pdf", "application/pdf")
    aid = gs.request_send(to="ada@example.com", subject="Re: Plans", body="Sounds good.",
                          html="<p><b>Sounds good.</b></p>", thread_id="t9",
                          in_reply_to="<abc@mail>", references="<root@mail>",
                          attachments=[att])["approval_id"]
    card = approvals.get_approval(aid)
    assert "reply in an existing conversation" in card["action_description"]
    assert "plan.pdf" in card["action_description"]
    assert SENT == []                                  # asking sends nothing
    approvals.decide(aid, "approve")
    gs.send(aid)
    assert SENT[0]["threadId"] == "t9"
    m = _parsed()
    assert m["In-Reply-To"] == "<abc@mail>" and "<root@mail>" in m["References"] and "<abc@mail>" in m["References"]
    types = [p.get_content_type() for p in m.walk()]
    assert "text/plain" in types and "text/html" in types and "application/pdf" in types
    pdf = [p for p in m.walk() if p.get_content_type() == "application/pdf"][0]
    assert pdf.get_payload(decode=True) == b"%PDF-1.4 plan" and pdf.get_filename() == "plan.pdf"


def test_attachment_swapped_after_approval_is_refused(tmp_path):
    att = gs.store_attachment(b"original", "a.txt", "text/plain")
    aid = gs.request_send(to="ada@example.com", subject="S", body="B", attachments=[att])["approval_id"]
    approvals.decide(aid, "approve")
    (tmp_path / att["sha256"]).write_bytes(b"something else")
    with pytest.raises(gs.SendRefused):
        gs.send(aid)
    assert SENT == []


def test_thread_retargeted_after_approval_is_refused():
    aid = gs.request_send(to="ada@example.com", subject="Re: x", body="B", thread_id="t1",
                          in_reply_to="<a@m>")["approval_id"]
    approvals.decide(aid, "approve")
    recs = approvals._read_store()
    for r in recs:
        if r["approval_id"] == aid:
            r["payload"]["extras"]["thread_id"] = "someone-elses-thread"
    approvals._write_store(recs)
    with pytest.raises(gs.SendRefused):
        gs.send(aid)
    assert SENT == []


def test_plain_message_fingerprint_is_unchanged():
    # cards already waiting in the queue were hashed without extras
    assert gs.message_fingerprint(["a@b.co"], "s", "b") == gs.message_fingerprint(["a@b.co"], "s", "b", extras=None)
    assert gs.message_fingerprint(["a@b.co"], "s", "b") != gs.message_fingerprint(["a@b.co"], "s", "b", extras={"html": "<p>b</p>"})


def test_oversized_or_empty_attachments_are_refused():
    with pytest.raises(gs.SendRefused):
        gs.store_attachment(b"", "x", "text/plain")
    with pytest.raises(gs.SendRefused):
        gs.store_attachment(b"x" * (gs.MAX_ATTACHMENT_BYTES + 1), "x", "text/plain")
