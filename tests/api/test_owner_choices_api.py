"""The owner's standing choices through Friday's own routes (see tests/unit/test_owner_choices.py).

* the Undo window of an email a scheduled job sends under a pre-approval is the owner's setting (2 minutes
  by default, 10 s to 10 min, refused outside it), the notice says how long is left and Undo works within
  it; a send the owner approves in person keeps 10 s;
* the money-paragraph card is answered with Send / Keep on this PC and the remember ticks, and the
  remembered choices are listed and forgotten in Settings;
* Calendar's delete passes the notify-guests box to Google (sendUpdates) and says whether guests were told;
* choosing two hosts on the Podcasts page asks the co-host's name once.
Synthetic addresses only (example.test).
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import approvals, gmail_send

TEAM = "team@example.test"
SCHEDULED = {"is_background_task": True, "schedule_id": "sch_weekly_status", "task_id": "task_weekly_1"}


@pytest.fixture
def client(app):
    import agent_friday.core as core
    c = app.test_client()
    c.environ_base["HTTP_X_FRIDAY_TOKEN"] = core._current_api_token()
    return c


@pytest.fixture
def mail(tmp_path, monkeypatch, friday_dir):
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)
    monkeypatch.setattr(gmail_send, "sendable_accounts",
                        lambda: [{"id": "acct_owner", "email": "owner@example.test", "label": "Owner"}])
    held = friday_dir / "mail_outbox" / "held.json"
    if held.exists():
        held.unlink()
    told = []
    monkeypatch.setattr(gmail_send, "_notify", lambda title, body, **kw: told.append(dict(kw, title=title, body=body)))
    from agent_friday.services import taint
    taint.reset()
    yield told
    taint.reset()


def _send(to, ctx):
    from agent_friday.services import agent
    out = agent._execute_tool("draft_email", {"to": to, "subject": "Weekly status " + uuid.uuid4().hex[:6],
                                              "body": "Synthetic weekly status.", "send": True},
                              session_ctx=dict(ctx))
    return json.loads(out)


def _held(approval_id):
    return next(h for h in gmail_send.held() if h["approval_id"] == approval_id)


def test_an_unattended_pre_approved_send_waits_the_owners_undo_window(client, mail):
    r = client.post("/api/governance/send-grants", json={"scope": "recipients", "recipients": [TEAM]})
    assert r.status_code == 201, r.get_data(as_text=True)
    res = _send(TEAM, SCHEDULED)
    assert res.get("pre_approved"), res
    row = _held(res["approval_id"])
    assert 110 <= row["seconds_left"] <= 120, "an unattended send waits %ss, not the 2 minutes" % row["seconds_left"]
    assert row.get("undo_seconds") == 120, row
    notice = next(n for n in mail if n["title"].startswith("Email going out"))
    assert "to undo it" in notice["body"] and "minute" in notice["body"]
    assert any(a["label"] == "Undo" for a in notice["actions"])
    # Undo works within the window: the message never goes.
    assert client.post("/api/mail/held/%s/cancel" % res["approval_id"]).status_code == 200
    assert res["approval_id"] not in [h["approval_id"] for h in gmail_send.held()]


def test_the_undo_window_is_a_setting_with_bounds(client, mail):
    from agent_friday.core import _save_settings
    assert client.post("/api/settings", json={"settings": {"unattended_send_undo_s": 5}}).status_code == 400
    assert client.post("/api/settings", json={"settings": {"unattended_send_undo_s": 900}}).status_code == 400
    assert client.post("/api/settings", json={"settings": {"unattended_send_undo_s": 300}}).status_code == 200
    try:
        client.post("/api/governance/send-grants", json={"scope": "recipients", "recipients": [TEAM]})
        res = _send(TEAM, SCHEDULED)
        assert 290 <= _held(res["approval_id"])["seconds_left"] <= 300
        # The owner present (no job, no background task): the 10-second window stays.
        res = _send(TEAM, {"session_id": "chat-1", "authenticated": True})
        if res.get("pre_approved"):
            assert _held(res["approval_id"])["undo_seconds"] == gmail_send.UNDO_SECONDS == 10
    finally:
        _save_settings({"unattended_send_undo_s": 120})


# ── the money-paragraph card's answer and the Settings list ─────────────────────

def test_the_card_answer_records_the_ticks_and_settings_lists_and_forgets_them(client, tmp_path, monkeypatch, friday_dir):
    import importlib
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    store = friday_dir / "cloud_release.json"
    if store.exists():
        store.unlink()
    # A card in the shared data contract: {what, why, scope: {thread|sender|once}} (+ the items it covers).
    card = approvals.create_approval(
        kind="cloud_release", subject_type="cloud_release", subject_id="synthetic-h1", force_gate=True,
        title="Send 1 paragraph about money to the cloud model?", action_class="internal",
        payload={"handler": "cloud_release", "what": ["The deposit of $400 is due."], "why": "synthetic",
                 "scope": {"once": True, "thread": "mail:a:t1", "sender": "robin@example.test"},
                 "items": [{"hash": "h1", "text": "The deposit of $400 is due.", "thread": "mail:a:t1",
                            "sender": "robin@example.test"}]})
    assert card["status"] == "pending"
    r = client.post("/api/privacy/cloud-release/%s/decide" % card["approval_id"],
                    json={"decision": "approve", "remember": {"thread": False, "sender": True}})
    assert r.status_code == 200, "the card's Send with its ticks has no answer route"
    assert r.get_json()["approval"]["status"] == "approved"
    cr = importlib.import_module("agent_friday.services.cloud_release")
    listed = client.get("/api/privacy/cloud-release").get_json()
    assert [(x["scope"], x["value"]) for x in listed["remembered"]] == [("sender", "robin@example.test")]
    assert "h1" in cr._once_hashes()
    rid = listed["remembered"][0]["id"]
    assert client.delete("/api/privacy/cloud-release/" + rid).status_code == 200
    assert client.get("/api/privacy/cloud-release").get_json()["remembered"] == []
    assert client.post("/api/privacy/cloud-release/nope/decide", json={"decision": "approve"}).status_code == 404


# ── Calendar's delete: the notify-guests box ────────────────────────────────────

def test_the_calendar_delete_route_passes_the_notify_guests_box(client, monkeypatch):
    from agent_friday.services import calendar_write as cw
    calls = []

    class _X:
        def __init__(self, fn):
            self.fn = fn

        def execute(self):
            return self.fn()

    class _Ev:
        def get(self, calendarId, eventId):
            return _X(lambda: {"id": eventId, "summary": "Synthetic sync", "start": {"dateTime": "2026-10-22T12:00:00Z"},
                               "end": {"dateTime": "2026-10-22T13:00:00Z"}, "attendees": [{"email": "a@example.test"}]})

        def delete(self, calendarId, eventId, **kw):
            calls.append(kw)
            return _X(lambda: "")
    svc = type("S", (), {"events": lambda self: _Ev()})()
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    monkeypatch.setattr(cw, "writable_accounts", lambda: [])
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (svc, None))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda: None)
    monkeypatch.setattr(action_gate, "verify_claws", lambda: (True, "ok"))
    # Posted as the Calendar page does (the owner's click).
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)
    d = client.post("/api/calendar/events/ev9/delete", json={}).get_json()
    assert calls[-1].get("sendUpdates") == "none" and d["guests_notified"] is False
    assert "not told" in d.get("guests_told", "")
    d = client.post("/api/calendar/events/ev9/delete", json={"notify_guests": True}).get_json()
    assert calls[-1].get("sendUpdates") == "all", "the ticked box did not tell the guests"
    assert d["guests_notified"] is True and "cancellation" in d["guests_told"]


# ── Podcasts: two hosts with an unnamed co-host asks once ───────────────────────

def test_choosing_two_hosts_on_the_page_asks_the_cohost_name_once(client, monkeypatch):
    import agent_friday.core as core
    box = {"agent_name": "AGENT FRIDAY"}

    def merge(base, over):
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                merge(base[k], v)
            else:
                base[k] = json.loads(json.dumps(v))
    monkeypatch.setattr(core, "_load_settings", lambda: box)
    monkeypatch.setattr(core, "_save_settings", lambda d, **kw: merge(box, json.loads(json.dumps(d))))
    d = client.put("/api/podcasts/formats", json={"routine": "weekly", "format": "duo"}).get_json()
    assert d["status"] == "ok" and d.get("ask_cohost_name"), "two hosts chosen and the co-host's name not asked"
    assert d["cohost_suggestion"] == "Nova"
    d = client.put("/api/podcasts/formats", json={"routine": "editorial", "format": "duo"}).get_json()
    assert "ask_cohost_name" not in d
