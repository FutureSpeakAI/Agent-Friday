"""Unit tests for security-boundary.md §19 row 7: `calendar_write.py`'s
`create_event`/`update_event` send title, location, and description to
Google Calendar with no gate call. The write itself already sits behind the
approvals queue's outward-action card (consent is covered); this closes
CLASSIFICATION — a TIER_2/3 span in the event text must not reach Google.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.services import calendar_write as cw
from agent_friday.services import egress_gate

TIER3_DESC = "my custody hearing is on the 14th, SSN 123-45-6789"  # pragma: allowlist secret


class _FakeEvents:
    def __init__(self):
        self.inserted = []
        self.patched = []
        self.gotten = {"summary": "old", "location": "", "description": ""}

    def insert(self, calendarId, body):
        self.inserted.append(body)
        class _Exec:
            def execute(_self):
                return {"id": "evt1", "summary": body.get("summary"),
                        "htmlLink": "http://cal/evt1"}
        return _Exec()

    def get(self, calendarId, eventId):
        got = self.gotten
        class _Exec:
            def execute(_self):
                return got
        return _Exec()

    def patch(self, calendarId, eventId, body):
        self.patched.append(body)
        class _Exec:
            def execute(_self):
                return {"id": eventId, "summary": body.get("summary")}
        return _Exec()


class _FakeService:
    def __init__(self):
        self._events = _FakeEvents()

    def events(self):
        return self._events


def _patch_common(monkeypatch, svc):
    monkeypatch.setattr(cw, "write_ready", lambda: (True, ""))
    monkeypatch.setattr(cw, "_service", lambda: (svc, None))


def test_create_event_gates_description(monkeypatch):
    svc = _FakeService()
    _patch_common(monkeypatch, svc)
    out = cw.create_event(title="Doctor visit", start="2026-09-10T09:00:00",
                          description=TIER3_DESC)
    assert svc._events.inserted, "event should still be created (title is benign)"
    body = svc._events.inserted[0]
    assert "123-45-6789" not in body.get("description", "")  # pragma: allowlist secret
    assert "custody and divorce" not in body.get("description", "")


def test_create_event_refuses_when_title_is_never_send(monkeypatch):
    svc = _FakeService()
    _patch_common(monkeypatch, svc)
    monkeypatch.setattr(
        egress_gate, "_gate_text",
        lambda *a, **k: (_ for _ in ()).throw(
            egress_gate.NeverSendBlocked("blocked for a test")))
    out = cw.create_event(title=TIER3_DESC, start="2026-09-10T09:00:00")
    assert "error" in out
    assert not svc._events.inserted, (
        "a never-send title must block the write entirely, not create an "
        "event with garbled or missing content"
    )


def test_benign_event_still_creates(monkeypatch):
    svc = _FakeService()
    _patch_common(monkeypatch, svc)
    out = cw.create_event(title="Team standup", start="2026-09-10T09:00:00",
                          location="Zoom", description="weekly sync")
    assert out.get("ok") is True
    body = svc._events.inserted[0]
    assert body["summary"] == "Team standup"
    assert body["location"] == "Zoom"
    assert body["description"] == "weekly sync"


def test_update_event_refuses_rather_than_silently_clears(monkeypatch):
    """A TIER_3 description classifies SENSITIVE (dropped to "" by the
    gate, not redacted) — update_event must REFUSE rather than silently
    write an empty description, because this module's own design (the
    would_clear check just above) treats an unrequested blank as data loss
    the receipt cannot restore. Silently gate-emptying a field would be
    exactly the unconsented clear that design already guards against."""
    svc = _FakeService()
    _patch_common(monkeypatch, svc)
    out = cw.update_event("evt1", description=TIER3_DESC)
    assert "error" in out
    assert not svc._events.patched, (
        "a field the gate emptied must not be silently written as a clear"
    )


def test_update_event_redacts_partial_tier2_description(monkeypatch):
    """A TIER_2 (redact, not drop) description should still update — the
    gate substitutes a placeholder rather than emptying the field."""
    svc = _FakeService()
    _patch_common(monkeypatch, svc)
    monkeypatch.setattr(
        egress_gate, "_gate_text",
        lambda text, provider, field, log_path=None: "[EGRESS-GATE: PRIVATE withheld]"
            if field == "calendar.description" else text)
    out = cw.update_event("evt1", description="some private note")
    assert out.get("ok") is True
    assert svc._events.patched
    body = svc._events.patched[0]
    assert "some private note" not in body.get("description", "")


def test_ungated_calendar_write_reproduction_is_falsifiable():
    """Reproduce the pre-fix shape: description passed straight into the
    request body with no gate call anywhere in the module."""
    def _old_no_gate(description):
        return {"summary": "x", "description": description}

    leaked = _old_no_gate(TIER3_DESC)
    assert TIER3_DESC in leaked["description"], (
        "the reproduction should leak the raw description; it did not, so "
        "this comparison is not meaningful"
    )
