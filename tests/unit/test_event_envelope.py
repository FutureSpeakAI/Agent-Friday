"""The canonical event envelope, persisted before notification (BD-16; NS-31.6-1, NS-31.7-1).

NS-31.6-1: one envelope -- id, type, version, principal scope, related ids, payload, source,
time, integrity. NS-31.7-1: a critical event is on disk before anyone is told of it. The first
adopter is the notification tray: a card that needs the owner (an approval, a safety notice, a
needs-you card) is recorded in the envelope, ids and kinds only, before the card is saved.
"""
from __future__ import annotations

import json
import uuid

import pytest


@pytest.fixture
def ev(tmp_path, monkeypatch):
    try:
        from agent_friday.services import events
    except ImportError:
        pytest.fail("there is no canonical event envelope: services/events does not exist")
    monkeypatch.setattr(events, "_dir", lambda: tmp_path / "events")
    monkeypatch.setattr(events, "_key", lambda: b"test-governance-key-not-real")
    return events


def test_an_envelope_has_every_field_and_verifies(ev):
    e = ev.make("task.halt", source="task_journal", payload={"cause": "interrupted"},
                related_ids={"task": "t1"})
    d = e.__dict__ if hasattr(e, "__dict__") else {}
    for f in ("id", "type", "version", "principal_scope", "related_ids", "payload", "source",
              "created_at", "integrity"):
        assert hasattr(e, f), f
    uuid.UUID(e.id)
    assert e.version == 1 and e.principal_scope == "owner"
    ev.persist(e)
    [back] = list(ev.replay())
    assert back["id"] == e.id and ev.verified(back)
    assert d is not None


def test_nobody_is_told_before_the_event_is_on_disk(ev):
    seen = []

    def notify(e):
        on_disk = ev.log_path().read_text(encoding="utf-8")
        seen.append(e.id in on_disk)
    e = ev.make("notification.needs_you", source="t", payload={})
    ev.persist_then_notify(e, notify)
    assert seen == [True]


def test_if_the_event_cannot_be_written_nobody_is_told(ev, monkeypatch, tmp_path):
    blocker = tmp_path / "events"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    told = []
    with pytest.raises(ev.EventNotPersisted):
        ev.persist_then_notify(ev.make("notification.safety", source="t", payload={}), told.append)
    assert told == []


def test_a_torn_last_line_and_a_tampered_line_are_skipped(ev):
    a = ev.make("task.halt", source="t", payload={"n": 1})
    b = ev.make("task.halt", source="t", payload={"n": 2})
    ev.persist(a)
    ev.persist(b)
    p = ev.log_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[1])
    forged["payload"] = {"n": 99}
    p.write_text(lines[0] + "\n" + json.dumps(forged) + "\n" + '{"id": "torn', encoding="utf-8")
    got = list(ev.replay())
    assert [g["payload"]["n"] for g in got] == [1]
    assert any(g.get("_verified") is False for g in ev.replay(include_unverified=True))


# ── the first adopter: the notification tray ─────────────────────────────────

@pytest.fixture
def tray(ev, tmp_path, monkeypatch):
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "NOTIF_FILE", tmp_path / "notifications.json")
    return ne


def test_a_card_that_needs_the_owner_is_recorded_before_it_is_saved(tray, ev, monkeypatch):
    real_save = tray._save
    at_save = []

    def save(items, *a, **k):
        at_save.append([e["related_ids"].get("notification") for e in ev.replay()])
        return real_save(items, *a, **k)
    monkeypatch.setattr(tray, "_save", save)
    card = tray.push(title="Approve the transfer to Dana's landlord", body="Rent for November",
                     kind="approval_pending", source="approvals", priority="high",
                     meta={"approval_id": "appr_1"})
    assert card.get("event_id"), "the card has no recorded event"
    assert card["id"] in at_save[-1], "the card was saved before its event was recorded"
    [e] = [x for x in ev.replay() if x["related_ids"].get("notification") == card["id"]]
    text = json.dumps(e)
    assert "landlord" not in text and "Rent" not in text, "the event log carries the card's words"
    assert e["type"].startswith("notification.") and e["related_ids"].get("approval") == "appr_1"


def test_off_the_record_writes_no_event(tray, ev):
    tray.push(title="x", body="y", kind="approval_pending", source="approvals", off_record=True)
    assert list(ev.replay(include_unverified=True)) == []
