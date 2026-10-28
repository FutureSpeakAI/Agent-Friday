"""A waiting card's notice says something is waiting, not what (owner ruling 5, 2026-10-28).

Event and task titles are sealed at rest; receipts and the ledger name none. The
notice Friday raises for a waiting card is kept in notifications.json, and it carried
the card's own title and description ("Approval needed: Move 'Dentist with Dr Okafor'
to Friday"). Now it carries a generic line by kind ("a calendar change") and points to
Friday; the card keeps its words on the screen. The same for the notice about a held
card whose time ran out.

All values are synthetic.
"""
from __future__ import annotations

import pytest

from agent_friday.services import approvals as ap

SECRET = "Dentist with Dr Okafor"


@pytest.fixture
def pushed(tmp_path, monkeypatch):
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    got = []
    monkeypatch.setattr(ne, "push", lambda **kw: got.append(kw) or kw)
    return got


def _card(**kw):
    base = dict(kind="governed_action", subject_type="calendar_event", subject_id="ev1",
                title="Move '%s' to Friday 3 pm" % SECRET,
                description="Moves %s; two guests are not notified." % SECRET,
                action_description="update_calendar_event %s" % SECRET,
                payload={"tool": "update_calendar_event", "handler": "calendar"},
                force_gate=True, requested_by="test")
    base.update(kw)
    return ap.create_approval(**base)


def _shown(n):
    return " ".join(str(n.get(k) or "") for k in ("title", "body"))


def test_a_waiting_cards_notice_does_not_carry_its_title(pushed):
    card = _card()
    notes = [p for p in pushed if p.get("kind") == "approval_pending"]
    assert notes, "no notice was raised for the waiting card"
    assert SECRET not in _shown(notes[-1]), "the notice names what the card is about"
    assert "calendar" in notes[-1]["title"].lower()
    assert SECRET in ap.get_approval(card["approval_id"])["title"]   # the card keeps its words


@pytest.mark.parametrize("tool,word", [("create_task", "task"), ("gmail_send", "email"),
                                       ("send_sms", "message"), ("write_file", "file"),
                                       ("something_new", "an action")])
def test_every_kind_gets_a_generic_line(pushed, tool, word):
    _card(subject_type="tool_action", payload={"tool": tool}, title="Do it for %s" % SECRET,
          description="about %s" % SECRET)
    note = [p for p in pushed if p.get("kind") == "approval_pending"][-1]
    assert SECRET not in _shown(note) and word in note["title"].lower()


def test_the_notice_for_a_held_card_that_ran_out_is_generic_too(pushed):
    card = _card()
    rec = dict(ap.get_approval(card["approval_id"]), held_by_policy=True)
    ap._notify_expired_while_held(rec)
    note = [p for p in pushed if p.get("kind") == "approval_expired_held"][-1]
    assert "expired before you saw it" in note["title"] and SECRET not in _shown(note)
