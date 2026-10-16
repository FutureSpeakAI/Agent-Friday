"""A calendar event can be deleted, and never on the model's say-so alone.

There was no delete: asked to remove an event, Friday had no tool for it. Now calendar_write.delete_event
reads the event first and deletes it, returning what it was (`before`) for the record; the
delete_calendar_event tool is outward, so the gate asks before it runs, and the question names the event by
its title and time read from the calendar, never an opaque id. Google is a fake here: nothing leaves.

Also part of the same report: when no tool does what the user asks, Friday says plainly it can't do that
yet, rather than attempting it with something else (the HONEST LIMITS directive).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate as ag
from agent_friday.services import approvals, calendar_write as cw, taint

ROOT = Path(__file__).resolve().parents[2]
SID = "2026-10-15"
EVENT = {"id": "ev42", "summary": "Dentist check-up", "status": "confirmed",
         "start": {"dateTime": "2026-10-21T10:00:00-05:00"}, "end": {"dateTime": "2026-10-21T10:45:00-05:00"},
         "location": "12 Example Street", "description": "Bring the form", "attendees": [{"email": "a@example.com"}]}


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Events:
    def __init__(self, store):
        self.store, self.deleted, self.reads = store, [], []

    def get(self, calendarId, eventId):
        self.reads.append((calendarId, eventId))

        def run():
            if eventId not in self.store:
                raise RuntimeError("404 Not Found")
            return dict(self.store[eventId])
        return _Exec(run)

    def delete(self, calendarId, eventId, **kw):
        def run():
            self.deleted.append((calendarId, eventId))
            self.store.pop(eventId, None)
            return ""
        return _Exec(run)


class _Svc:
    def __init__(self, store):
        self.ev = _Events(store)

    def events(self):
        return self.ev


@pytest.fixture
def google(monkeypatch):
    store = {"ev42": dict(EVENT), "gone1": dict(EVENT, id="gone1", status="cancelled")}
    svc = _Svc(store)
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    monkeypatch.setattr(cw, "writable_accounts", lambda: [])
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (svc, None))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda: None)
    return svc.ev


@pytest.fixture
def gate(tmp_path, monkeypatch, google):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    yield google
    taint.reset()
    agent._PENDING_CONFIRMATIONS.clear()


def _turn(message):
    return agent.prepare_confirmation_ctx(SID, message, {"authenticated": True, "owner_surface": True})


# ── calendar_write.delete_event: the contract the Calendar page builds on ──────

def test_delete_reads_the_event_then_deletes_it_and_returns_what_it_was(google):
    out = cw.delete_event("ev42")
    assert out["ok"] is True and out["id"] == "ev42" and out["title"] == "Dentist check-up"
    b = out["before"]
    assert b["title"] == "Dentist check-up" and b["start"] == "2026-10-21T10:00:00-05:00"
    assert b["location"] == "12 Example Street" and b["guests"] == 1 and b["calendar_id"] == "primary"
    assert google.reads[0] == ("primary", "ev42") and google.deleted == [("primary", "ev42")]


def test_delete_names_the_calendar_it_acts_on(google):
    google.store["ev42"] = dict(EVENT)
    out = cw.delete_event("ev42", calendar_id="family@group.example", account_id=None)
    assert out["ok"] and google.deleted == [("family@group.example", "ev42")]
    assert out["before"]["calendar_id"] == "family@group.example"


def test_an_event_that_cannot_be_read_is_not_deleted(google):
    out = cw.delete_event("nope")
    assert out.get("error") and not out.get("ok") and google.deleted == []
    out = cw.delete_event("gone1")
    assert "already deleted" in out["error"] and google.deleted == []
    assert cw.delete_event("").get("error") and google.deleted == []


def test_without_a_write_token_nothing_is_deleted(google, monkeypatch):
    monkeypatch.setattr(cw, "write_ready", lambda: (False, "read-only token"))
    out = cw.delete_event("ev42")
    assert out["needs_reconnect"] and google.deleted == []


# ── the tool: outward, asks first, by the event's name and time ────────────────

def test_the_tool_is_outward_with_the_ring_of_its_sibling_and_is_registered_like_it():
    assert ag.classify("delete_calendar_event", {"event_id": "x"})[0] == ag.OUTWARD
    assert "delete_calendar_event" in ag.OUTWARD_TOOLS
    assert agent.TOOL_RINGS["delete_calendar_event"] == agent.TOOL_RINGS["update_calendar_event"] == 2
    assert "delete_calendar_event" in agent.CLAUDE_TOOL_HANDLERS
    schema = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "delete_calendar_event")
    assert set(schema["input_schema"]["properties"]) == {"event_id", "calendar_id", "account_id", "notify_guests"}
    groups = json.loads((ROOT / "src/agent_friday/resources/tool_groups.json").read_text(encoding="utf-8"))
    assert groups["tools"]["delete_calendar_event"] == groups["tools"]["update_calendar_event"] == ["calendar"]
    from agent_friday.routes import goals
    assert goals._GRANT_LABELS["delete_calendar_event"] == "Delete calendar events"


def test_the_tool_asks_first_and_the_question_names_the_event(gate):
    ctx = _turn("delete my dentist appointment")
    out = agent._execute_tool("delete_calendar_event", {"event_id": "ev42"}, session_ctx=ctx)
    assert "CONFIRMATION REQUIRED" in out, out
    assert "Dentist check-up" in out and "2026-10-21T10:00" in out, out
    assert gate.deleted == [], "nothing is deleted before the yes"
    out = agent._execute_tool("delete_calendar_event", {"event_id": "ev42"}, session_ctx=_turn("yes"))
    assert gate.deleted == [("primary", "ev42")]
    res = json.loads(out)
    assert res["ok"] and res["before"]["title"] == "Dentist check-up"


def test_a_no_deletes_nothing(gate):
    agent._execute_tool("delete_calendar_event", {"event_id": "ev42"}, session_ctx=_turn("delete the dentist"))
    agent._execute_tool("delete_calendar_event", {"event_id": "ev42"}, session_ctx=_turn("no, keep it"))
    assert gate.deleted == []


def test_with_nobody_to_ask_it_is_a_card_that_names_the_event(gate):
    ctx = {"authenticated": True, "is_background_task": True}
    out = agent._execute_tool("delete_calendar_event", {"event_id": "ev42"}, session_ctx=ctx)
    assert "APPROVAL CARD RAISED" in out and gate.deleted == []
    (card,) = approvals.list_approvals(status="pending")
    assert "Dentist check-up" in card["title"] and "2026-10-21T10:00" in card["title"]


def test_a_card_for_an_event_that_cannot_be_read_says_so():
    words = agent._taint_title("delete_calendar_event", {"event_id": "zzz"})
    assert words.startswith("Delete the calendar event") and "zzz" in words


# ── voice parity, manifest ─────────────────────────────────────────────────────

def test_voice_reaches_it_the_way_it_reaches_the_other_calendar_writes():
    from agent_friday.services import voice_engine as ve
    live = {t[0] for t in ve._VOICE_LIVE_TOOLS}
    shared = set(ve._VOICE_SHARED_TOOLS)
    # Like update_calendar_event: not a native voice tool; a spoken request is handed to the full agent,
    # where the same tool and the same question run.
    for name in ("update_calendar_event", "delete_calendar_event"):
        assert name not in live and name not in shared
    assert "delegate_to_friday" in live | shared or "ask_friday" in live | shared


def test_the_calendar_manifest_has_delete_as_a_verb_not_a_gap():
    m = json.loads((ROOT / "src/agent_friday/resources/laya_manifests/calendar.json").read_text(encoding="utf-8"))
    assert "calendar.delete_event" not in {n["action"] for n in m["not_for_laya"]}
    verb = next(v for v in m["verbs"] if v["action"] == "calendar.delete_event")
    assert verb["tool"] == "delete_calendar_event"
    assert verb["gate"] == {"class": "outward", "ring": 2, "card": "required"}


# ── saying plainly what Friday cannot do ───────────────────────────────────────

def test_the_honest_limits_say_plainly_when_no_tool_does_it():
    from agent_friday.services import model_router as mr
    block = mr.honest_limits_block()
    assert "none of your tools does it" in block and "I can't do that yet" in block
    assert "do not say it is done or under way" in block
    assert "Do not attempt it with a tool made for something else" in block
