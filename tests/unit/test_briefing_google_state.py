"""A briefing, its prompt and the template behind it say Google is not connected only when the live capability state says
so (services/briefing_google, services/capability_state).

The legacy single-account fetch reports "Google not connected" whenever it finds no credentials: while no route is
chosen, or while its first account needs a new sign-in though another works. The briefing printed that sentence, the
shipped connectors instruction told the model the services "may not be connected yet", and the model repeated it in
every briefing while the live capability state had both accounts working. Synthetic accounts and events only."""
from __future__ import annotations

import pytest

from agent_friday.services import briefing_google as bg
from agent_friday.services import capability_state as cap
from agent_friday.services import google_accounts, news_engine

SENTINEL = [{"error": "Google not connected. Visit /api/google/auth to link Calendar (read-only)."}]
EVENT = {"title": "Standup with the team", "start_time": "2026-11-22T09:00:00", "end_time": "", "location": "",
         "attendees": []}
MAIL = {"sender": "Sam Sample", "subject": "Quarterly sample", "snippet": "a synthetic preview", "labels": ["UNREAD"]}


def _google(state, health=""):
    return cap.CapabilityState("google", "Google accounts (Gmail, Calendar, Drive)", state, (), "", "synthetic",
                               health=health)


@pytest.fixture
def google(monkeypatch):
    """Both accounts working in the live capability state, the legacy fetches failing with the stale sentence."""
    live = {"state": _google(cap.WORKING), "held": ""}
    monkeypatch.setattr(cap, "google_accounts_state", lambda: live["state"])
    monkeypatch.setattr(google_accounts, "merged_calendar", lambda days=2: {"events": [dict(EVENT)]})
    monkeypatch.setattr(google_accounts, "merged_gmail", lambda *a, **k: {"messages": [dict(MAIL)]})
    from agent_friday.services import cloud_intent
    monkeypatch.setattr(cloud_intent, "outbound_held", lambda kind, what="": live["held"])
    monkeypatch.setattr(news_engine, "_fetch_calendar_today", lambda: list(SENTINEL))
    monkeypatch.setattr(news_engine, "_fetch_gmail_recent", lambda limit=15: [{"error": "Google not connected."}])
    monkeypatch.setattr(news_engine, "_load_briefing_prefs", lambda: {
        "sections_enabled": {"Calendar": True, "News": False, "Email": True}, "section_order": ["Calendar", "Email"]})
    monkeypatch.setattr(news_engine, "_load_banned_sources", lambda: [])
    monkeypatch.setattr(news_engine, "_load_boosted_sources", lambda: [])
    monkeypatch.setattr(news_engine, "_build_tasks_section", lambda: ("", False), raising=False)
    import agent_friday.services.connectors as connectors
    monkeypatch.setattr(connectors, "connector_intelligence", lambda: {}, raising=False)
    return live


def test_with_google_connected_no_not_connected_line_appears_in_the_briefing_data(google):
    text = news_engine._gather_live_briefing_context()
    assert "not connected" not in text.lower()
    assert "Standup with the team" in text and "Quarterly sample" in text


def test_the_prompt_carries_the_live_state_and_not_a_stale_claim(google):
    """The text the model is given (the header of the live data) states Google's state, read at generation time."""
    text = news_engine._gather_live_briefing_context()
    assert "Google Calendar connected and working; Gmail connected and working." in text
    google["state"] = _google(cap.UNCONFIGURED)
    later = news_engine._gather_live_briefing_context()
    assert "Google Calendar not connected; Gmail not connected." in later         # read again, not remembered


def test_the_afternoon_job_instructions_defer_to_the_live_state():
    from agent_friday.services import scheduler
    text = scheduler._AFTERNOON_BRIEFING_JOB.instructions
    assert "stated in that data" in text and "only when it says so" in text


def test_the_shipped_connectors_instruction_does_not_say_the_services_may_not_be_connected():
    from pathlib import Path
    from agent_friday.services import instruction_files
    text = (Path(instruction_files.SHIPPED_DIR) / "40_connectors.md").read_text(encoding="utf-8")
    assert "may not be CONNECTED yet" not in text and "CAPABILITY STATE" in text


def test_the_states_and_their_sentences(google):
    assert bg.state("calendar") == bg.CONNECTED
    google["state"] = _google(cap.PRESENT_FAILING, health="degraded")                 # one account works
    assert bg.state("calendar") == bg.CONNECTED
    google["state"] = _google(cap.PRESENT_FAILING)
    assert bg.state("calendar") == bg.NEEDS_SIGN_IN and "sign in again" in bg.recover("calendar")[1]
    google["state"] = _google(cap.UNCONFIGURED)
    assert bg.state("calendar") == bg.NOT_CONNECTED
    assert bg.recover("calendar") == (None, "Google Calendar is not connected.")
    google["held"] = "route not chosen"
    assert bg.state("gmail") == bg.HELD and "choose where" in bg.recover("gmail")[1]


def test_connected_but_unreadable_says_so_and_never_says_not_connected(google, monkeypatch):
    monkeypatch.setattr(google_accounts, "merged_calendar", lambda days=2: {"events": []})
    items, note = bg.recover("calendar")
    assert items is None and "connected but could not be read" in note
    text = news_engine._gather_live_briefing_context()
    assert "Google Calendar is connected but could not be read just now." in text
    assert "Google Calendar is not connected" not in text


def test_when_google_really_is_not_connected_the_briefing_says_so(google):
    google["state"] = _google(cap.UNCONFIGURED)
    text = news_engine._gather_live_briefing_context()
    assert "Google Calendar is not connected." in text
