"""A briefing says Google is not connected only when the live account state says so (services/briefing_google).

The legacy single-account fetch reports "Google not connected" whenever it finds no credentials: while no route is
chosen, or while its first account needs a new sign-in though another works. The briefing printed that sentence and the
model repeated it in every briefing, while the live capability state had both accounts working. Synthetic accounts and
events only."""
from __future__ import annotations

import pytest

from agent_friday.services import briefing_google as bg
from agent_friday.services import google_accounts, news_engine

SENTINEL = [{"error": "Google not connected. Visit /api/google/auth to link Calendar (read-only)."}]
EVENT = {"title": "Standup with the team", "start_time": "2026-11-22T09:00:00", "end_time": "", "location": "", "attendees": []}
MAIL = {"sender": "Sam Sample", "subject": "Quarterly sample", "snippet": "a synthetic preview", "labels": ["UNREAD"]}


@pytest.fixture
def google(monkeypatch):
    """Both accounts usable, the legacy fetches failing with the stale sentence."""
    live = {"usable": {"calendar": [{"id": "a"}, {"id": "b"}], "gmail": [{"id": "a"}, {"id": "b"}]}, "any": True,
            "held": ""}
    monkeypatch.setattr(google_accounts, "_accounts_with", lambda service: live["usable"].get(service, []))
    monkeypatch.setattr(google_accounts, "has_accounts", lambda: live["any"])
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


def test_with_google_connected_no_not_connected_line_appears(google):
    text = news_engine._gather_live_briefing_context()
    assert "not connected" not in text.lower()
    assert "Standup with the team" in text and "Quarterly sample" in text


def test_the_states_and_their_sentences(google):
    assert bg.state("calendar") == bg.CONNECTED
    google["usable"]["calendar"] = []
    assert bg.state("calendar") == bg.NEEDS_SIGN_IN and "sign in again" in bg.recover("calendar")[1]
    google["any"] = False
    assert bg.state("calendar") == bg.NOT_CONNECTED and bg.recover("calendar") == (None, "Google Calendar is not connected.")
    google["held"] = "route not chosen"
    assert bg.state("gmail") == bg.HELD and "choose where" in bg.recover("gmail")[1]


def test_connected_but_unreadable_says_so_and_never_says_not_connected(google, monkeypatch):
    monkeypatch.setattr(google_accounts, "merged_calendar", lambda days=2: {"events": []})
    items, note = bg.recover("calendar")
    assert items is None and "connected but could not be read" in note and "not connected" not in note.replace("is connected", "")
    text = news_engine._gather_live_briefing_context()
    assert "Google Calendar is connected but could not be read just now." in text


def test_when_google_really_is_not_connected_the_briefing_says_so(google):
    google["usable"] = {"calendar": [], "gmail": []}
    google["any"] = False
    text = news_engine._gather_live_briefing_context()
    assert "Google Calendar is not connected." in text
