"""A message sent under a pre-approval is not sent again when it is retried (store-lane hardening).

A pre-approval lets a send go without a card. A retry of the very same message (the model asked
again after a timeout, or after an unclear "not sent") used to go out a second time, because once
the first one was spent nothing remembered it. Now the repeat asks on a card that says why, and the
owner decides. Through the real tool path; Gmail faked; synthetic addresses only.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import approvals, gmail_send

TEAM = "team@example.test"
SCHEDULED = {"is_background_task": True, "schedule_id": "sch_weekly_status", "task_id": "task_weekly_1"}
SENT: list = []


class _Exec(dict):
    boom = None

    def execute(self):
        if _Exec.boom is not None:
            raise _Exec.boom
        return self


class _Msgs:
    def send(self, userId=None, body=None):        # noqa: N803 (Google's API)
        SENT.append((body or {}).get("raw"))
        return _Exec({"id": "msg_%d" % len(SENT), "threadId": "thr_1"})


class _Svc:
    def users(self):
        class _U:
            def messages(self):
                return _Msgs()
        return _U()


@pytest.fixture
def client(app):
    from tests.desk_press_support import desk_headers
    c = app.test_client()
    for k, v in desk_headers().items():
        c.environ_base["HTTP_" + k.upper().replace("-", "_")] = v
    return c


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, friday_dir):
    SENT.clear()
    _Exec.boom = None
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(gmail_send, "sendable_accounts",
                        lambda: [{"id": "acct_owner", "email": "owner@example.test", "label": "Owner"}])
    monkeypatch.setattr(gmail_send, "scope_granted", lambda account_id=None: True)
    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", lambda *a, **k: _Svc(), raising=False)
    monkeypatch.setattr(gmail_send, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")
    held = friday_dir / "mail_outbox" / "held.json"
    if held.exists():
        held.unlink()
    from agent_friday.services import scheduler
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    scheduler.register_schedule({"id": "sch_weekly_status", "name": "Synthetic weekly", "trigger": "weekly",
                                 "spec": {"hour": 7, "minute": 0, "weekdays": [0]},
                                 "task": {"kind": "agent_prompt", "prompt": "Write the weekly status."}})
    from agent_friday.services import taint
    from agent_friday.governance import outside_content
    taint.reset()
    outside_content.reset_cache()
    yield
    _Exec.boom = None
    taint.reset()
    outside_content.reset_cache()


OTHER = "lead@example.test"


def _grant(client, recipients=(TEAM,)):
    r = client.post("/api/governance/send-grants", json={"scope": "recipients", "recipients": list(recipients)})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["grant"]


def _send(to=TEAM, body="Synthetic weekly status."):
    from agent_friday.services import agent
    out = agent._execute_tool("draft_email", {"to": to, "subject": "Weekly status",
                                              "body": body, "send": True},
                              session_ctx=dict(SCHEDULED))
    return json.loads(out)


def _deliver_due():
    rows = gmail_send.held()
    if rows:
        gmail_send._tick(now=max(r["due"] for r in rows) + 1)


def test_the_first_send_goes_under_the_pre_approval(client):
    g = _grant(client)
    res = _send()
    assert res.get("pre_approved") == g["grant_id"], res
    assert "do not call this tool again" in res["note"].lower()
    _deliver_due()
    assert len(SENT) == 1


def test_a_retry_of_the_same_message_is_not_sent_again_without_asking(client):
    _grant(client)
    assert _send().get("pre_approved")
    _deliver_due()
    assert len(SENT) == 1
    again = _send()
    assert not again.get("pre_approved"), "the retry went out under the pre-approval again"
    card = approvals.get_approval(again["approval_id"])
    assert card["status"] == "pending", "the repeat must ask the owner"
    assert "identical message" in card["action_description"]
    assert "identical message" in (again.get("pre_approval_withheld") or "")
    _deliver_due()
    assert len(SENT) == 1, "a second copy went out"


def test_a_retry_after_gmail_did_not_confirm_is_not_sent_again(client):
    _grant(client)
    assert _send().get("pre_approved")
    _Exec.boom = TimeoutError("the read timed out")          # Gmail took it, the answer was lost
    _deliver_due()
    assert len(SENT) == 1
    _Exec.boom = None
    again = _send()
    assert not again.get("pre_approved")
    _deliver_due()
    assert len(SENT) == 1, "a second copy went out after an unconfirmed send"


def test_a_different_message_to_the_same_people_still_goes_under_it(client):
    from agent_friday.services import agent
    _grant(client)
    assert _send().get("pre_approved")
    _deliver_due()
    other = json.loads(agent._execute_tool(
        "draft_email", {"to": TEAM, "subject": "Another subject", "body": "Other synthetic text.",
                        "send": True}, session_ctx=dict(SCHEDULED)))
    assert other.get("pre_approved"), other


def test_a_retry_with_the_recipients_reordered_is_still_a_retry(client):
    _grant(client, recipients=(TEAM, OTHER))
    assert _send(to=TEAM + ", " + OTHER).get("pre_approved")
    _deliver_due()
    again = _send(to=OTHER + ", " + TEAM, body="Synthetic weekly status.\n")
    assert not again.get("pre_approved"), "a reordered retry went out under the pre-approval again"
    _deliver_due()
    assert len(SENT) == 1


def test_a_retry_while_the_first_is_still_in_its_undo_window_is_the_same_message(client):
    g = _grant(client)
    first = _send()
    again = _send()
    assert again["approval_id"] == first["approval_id"]
    assert again.get("pre_approved") == g["grant_id"], "a held pre-approved message was described as waiting on a card"
    _deliver_due()
    assert len(SENT) == 1