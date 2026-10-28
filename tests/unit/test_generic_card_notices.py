"""Notices about waiting cards say what KIND of thing is waiting, never its title, and the stored
notice keeps no words about the card once it is decided (owner ruling 5, 2026-10-28).

A card's title can name an event, a task, a recipient or a file. It stays on the card in the app;
the tray notice, the text-message offer and the expiry notice say only "A calendar change is
waiting for you" and where to decide it. Synthetic titles only.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import approvals
from tests.unit.phone_fakes import fake_twilio, phone_home, quiet, verify_owner  # noqa: F401

SECRET_EVENT = "Synthetic Dentist Visit"
SECRET_TASK = "Synthetic Divorce Papers"
SECRET_ADDR = "someone.private@example.test"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "NOTIF_FILE", tmp_path / "notifications.json")
    monkeypatch.setattr(approvals, "_HOOKS", {})
    return ne


def _pushed(monkeypatch):
    import agent_friday.notifications_engine as ne
    got = []
    real = ne.push
    monkeypatch.setattr(ne, "push", lambda **kw: got.append(kw) or real(**kw))
    return got


def _card(kind, title, payload, sid=None):
    return approvals.create_approval(kind=kind, subject_type="synthetic", subject_id=sid or "s:%s" % time.time(),
                                     title=title, action_description=title + " (details)",
                                     description=title, payload=payload, force_gate=True)


CASES = [
    ("tainted_action", 'Create calendar event “%s”' % SECRET_EVENT,
     {"tool": "create_calendar_event", "input": {"summary": SECRET_EVENT}}, "A calendar change is waiting for you"),
    ("governed_action", 'Friday wants to move “%s” to Friday' % SECRET_EVENT,
     {"domain": "calendar"}, "A calendar change is waiting for you"),
    ("tool_confirm", "Create task %s" % SECRET_TASK,
     {"tool": "create_task", "input": {"title": SECRET_TASK}}, "A task change is waiting for you"),
    ("external_message", "Send email to %s" % SECRET_ADDR,
     {"handler": "gmail_send", "to": [SECRET_ADDR]}, "An email is waiting for you"),
]


@pytest.mark.parametrize("kind,title,payload,words", CASES, ids=[c[0] + ":" + c[3].split()[1] for c in CASES])
def test_a_waiting_card_notice_says_what_kind_of_thing_not_its_title(store, monkeypatch, kind, title, payload,
                                                                    words):
    got = _pushed(monkeypatch)
    rec = _card(kind, title, payload)
    assert rec["status"] == "pending"
    mine = [n for n in got if (n.get("meta") or {}).get("approval_id") == rec["approval_id"]]
    assert mine, "no notice for a waiting card"
    shown = json.dumps(mine)
    assert mine[0]["title"] == words, mine[0]["title"]
    for secret in (SECRET_EVENT, SECRET_TASK, SECRET_ADDR):
        assert secret not in shown, "the notice carried the card's words"


def test_the_stored_notice_drops_the_title_once_the_card_is_decided(store):
    ne = store
    rec = _card("tainted_action", 'Create calendar event “%s”' % SECRET_EVENT,
                {"tool": "create_calendar_event", "input": {"summary": SECRET_EVENT}})
    # A notice stored before generic titles (or any text that reached it) carries the title.
    items = ne._load()
    mine = [n for n in items if n.get("dedupe_key") == "approval:%s" % rec["approval_id"]]
    assert mine, "no stored notice for the waiting card"
    mine[0].update(title="Approval needed: " + SECRET_EVENT, body=SECRET_EVENT,
                   meta={"approval_id": rec["approval_id"], "detail": SECRET_EVENT})
    ne._save(items)
    assert SECRET_EVENT in ne.NOTIF_FILE.read_text(encoding="utf-8")
    approvals.decide(rec["approval_id"], "deny")
    text = ne.NOTIF_FILE.read_text(encoding="utf-8")
    assert SECRET_EVENT not in text, "the decided card's words are still in the stored notice"
    items = [n for n in json.loads(text)["items"] if n.get("dedupe_key") == "approval:%s" % rec["approval_id"]]
    assert items and all(n["dismissed"] for n in items)
    assert all((n.get("meta") or {}).get("approval_id") == rec["approval_id"] for n in items)


def test_an_expired_card_notice_says_what_kind_not_its_title(store, monkeypatch):
    got = _pushed(monkeypatch)
    rec = _card("tool_confirm", "Create task %s" % SECRET_TASK,
                {"tool": "create_task", "input": {"title": SECRET_TASK}})
    approvals._notify_expired_while_held(dict(rec, status="expired"))
    expired = [n for n in got if n.get("kind") == "approval_expired_held"]
    assert expired and SECRET_TASK not in json.dumps(expired)
    assert "task change" in expired[-1]["title"]


def test_a_text_offering_a_card_says_what_kind_not_its_title(phone_home, fake_twilio, quiet,  # noqa: F811
                                                            monkeypatch):
    from agent_friday.phone import config, service
    verify_owner(monkeypatch)
    config.update({"sms_approvals": True})
    approvals.create_approval(kind="external_message", subject_type="email", subject_id="x:%s" % time.time(),
                              title="Send email to %s" % SECRET_ADDR, action_description="Send it",
                              payload={"handler": "gmail_send"}, force_gate=True)
    assert service.offer_pending_approvals() == 1
    body = fake_twilio.sent_bodies()[-1]
    assert SECRET_ADDR not in body and "An email is waiting for you" in body
    for p in phone_home.rglob("sms_approvals.json"):
        assert SECRET_ADDR not in p.read_text(encoding="utf-8")
