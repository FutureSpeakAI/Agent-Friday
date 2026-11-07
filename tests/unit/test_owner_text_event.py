"""A text to the owner is in the canonical event log before Twilio is asked (BD-16 C6b; NS-31.6-1,
NS-31.7-1).

C6 and C9 put the tray cards, a decided approval, Stop everything and the grants in the event log
before anyone is told. A text to the owner's verified cell (phone/service.text_owner) is the next
way Friday reaches the owner that was not routed through it. The event names the channel and the
reason, and the action the checkpoint decided; never the words. The owner is told either way: a
record that cannot be written does not stop the text, and off the record writes nothing.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.phone import service
from tests.unit.phone_fakes import fake_twilio, phone_home, quiet, verify_owner  # noqa: F401

TYPE = "phone.owner_text"
WORDS = "Dana's landlord wants 4,200 by Friday"


@pytest.fixture
def ev(tmp_path, monkeypatch):
    from agent_friday.services import events
    monkeypatch.setattr(events, "_dir", lambda: tmp_path / "events")
    monkeypatch.setattr(events, "_key", lambda: b"test-governance-key-not-real")
    return events


def _mine(ev):
    return [e for e in ev.replay() if e["type"] == TYPE]


def test_the_text_is_recorded_before_twilio_is_asked(phone_home, fake_twilio, quiet, ev, monkeypatch):
    verify_owner(monkeypatch)
    real = service._send_sms_now
    seen = []

    def spy(*a, **k):
        seen.append(len(_mine(ev)))               # what the log held when the send began
        return real(*a, **k)
    monkeypatch.setattr(service, "_send_sms_now", spy)
    service.text_owner(WORDS, reason="alert")
    assert seen == [1], "the text was sent before its event was on disk: %s" % seen
    assert fake_twilio.sent_bodies() == [WORDS]


def test_the_event_names_the_channel_the_reason_and_the_action_never_the_words(
        phone_home, fake_twilio, quiet, ev, monkeypatch):
    verify_owner(monkeypatch)
    service.text_owner(WORDS, reason="alert")
    events = _mine(ev)
    assert len(events) == 1, "the text left no %s event: %s" % (TYPE, events)
    e = events[0]
    assert e["payload"] == {"channel": "sms", "reason": "alert"}
    assert e["source"] == "phone"
    assert isinstance(e["related_ids"].get("action"), str) and e["related_ids"]["action"]
    assert "landlord" not in json.dumps(e) and "4,200" not in json.dumps(e)


def test_each_text_is_its_own_event(phone_home, fake_twilio, quiet, ev, monkeypatch):
    verify_owner(monkeypatch)
    service.text_owner("one", reason="alert")
    service.text_owner("two", reason="reply")
    events = _mine(ev)
    assert [e["payload"]["reason"] for e in events] == ["alert", "reply"]
    assert len({e["id"] for e in events}) == 2
    assert len({e["related_ids"]["action"] for e in events}) == 2


def test_a_text_the_checkpoint_refuses_records_nothing(phone_home, fake_twilio, quiet, ev):
    # the cell is not verified: nothing goes out, so nobody is being told anything
    with pytest.raises(service.PhoneRefused):
        service.text_owner(WORDS)
    assert _mine(ev) == [] and fake_twilio.calls == []


def test_off_the_record_writes_no_event_and_the_text_still_goes(
        phone_home, fake_twilio, quiet, ev, monkeypatch):
    verify_owner(monkeypatch)
    monkeypatch.setattr(ev, "_off_record", lambda: True)
    service.text_owner("hi", reason="alert")
    assert _mine(ev) == []
    assert fake_twilio.sent_bodies() == ["hi"]


def test_a_record_that_cannot_be_written_never_stops_the_text(
        phone_home, fake_twilio, quiet, ev, monkeypatch):
    verify_owner(monkeypatch)
    attempts = []

    def broken(event, **k):
        attempts.append(event.type)
        raise ev.EventNotPersisted("the event log could not be written (OSError)")
    monkeypatch.setattr(ev, "persist", broken)
    res = service.text_owner("hi", reason="alert")
    assert attempts == [TYPE], "the text was never recorded, so a failed record was never tried"
    assert fake_twilio.sent_bodies() == ["hi"] and res


def test_a_verification_code_is_never_an_event(phone_home, fake_twilio, quiet, ev):
    service.start_owner_verification("sms")
    assert fake_twilio.sent_bodies(), "the code text was not sent"
    assert _mine(ev) == []
    assert "code" not in json.dumps(list(ev.replay())).lower()
