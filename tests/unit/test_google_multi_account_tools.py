"""Multi-account Google read path for the chat-facing built-in tools (docs:
fix/toolcall-integrity-v5, 2026-08-13).

Both of Stephen's accounts landed correctly in the multi-account store
(verified live against production data: accounts.json lists both,
credentials_for() returns valid credentials for both). But
_tool_query_calendar / _tool_search_email / connectors._status_for_google
only ever consulted the PRIMARY account via the single-account bridge
(calendar_engine._google_credentials() -> google_accounts.primary_credentials())
— a second connected account was invisible to chat entirely, and a REAL API
error (Calendar/Gmail API not enabled in the new GCP project — a genuine
live 403 accessNotConfigured, confirmed by direct testing) got collapsed by
_google_section_error() into the same generic "needs connecting" message a
genuinely-unlinked account would produce. Both accounts ARE connected; the
tool must say so and report the real error, not claim no token exists.

Both the OLD single-account functions and the NEW multi-account functions
are mocked in every test here — this is deliberate: it makes the tests
inherently safe (zero real network calls regardless of which code path is
exercised) AND lets each test prove, unambiguously, WHICH path the tool
actually used by giving the two paths different, incompatible data shapes.
"""
from __future__ import annotations

import json

import agent_friday.services.agent as agent_mod
from agent_friday.services import calendar_engine as ce
from agent_friday.services import google_accounts as ga


class TestQueryCalendarMultiAccount:
    def test_reports_every_account_not_just_the_primary(self, monkeypatch):
        # OLD path: single stale event, no per-account attribution at all —
        # if the tool used this, "Work" would never appear anywhere.
        monkeypatch.setattr(ce, "_fetch_calendar_today",
                            lambda: [{"title": "STALE single-account event"}])
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_calendar", lambda days=2: {
            "accounts": [
                {"id": "acc1", "label": "Personal", "email": "personal@example.com"},
                {"id": "acc2", "label": "Work", "email": "work@example.com"},
            ],
            "events": [
                {"title": "Standup", "start_time": "2026-08-13T09:00:00",
                 "end_time": "2026-08-13T09:30:00", "account_id": "acc1",
                 "account_label": "Personal"},
            ],
            "errors": [],
        })
        result = json.loads(agent_mod._tool_query_calendar({}))
        assert "STALE single-account event" not in json.dumps(result)
        account_labels = {a["label"] for a in result.get("accounts", [])}
        assert account_labels == {"Personal", "Work"}

    def test_a_real_api_error_is_never_mislabeled_as_not_connected(self, monkeypatch):
        # The actual live bug: both accounts ARE connected, but the Calendar
        # API isn't enabled in the GCP project -> a real 403, not a missing-
        # OAuth situation. The tool must say so, not "needs connecting".
        monkeypatch.setattr(ce, "_fetch_calendar_today",
                            lambda: [{"error": "OLD single-account path — must not be used"}])
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_calendar", lambda days=2: {
            "accounts": [{"id": "acc1", "label": "Personal", "email": "personal@example.com"}],
            "events": [],
            "errors": [{"account_id": "acc1", "label": "Personal",
                        "error": "Calendar fetch failed: <HttpError 403 ... "
                                 "accessNotConfigured ...>"}],
        })
        blob = agent_mod._tool_query_calendar({})
        # "not connected" phrasing unique to the genuinely-unlinked template
        # must never appear here — the account IS connected, only the API
        # call failed. (The fix's own honest note legitimately says "do NOT
        # say needs connecting", so checking for that substring would be
        # backwards — check for the old template's distinctive marker instead.)
        assert "NOT CONNECTED on this machine" not in blob
        assert "OLD single-account path" not in blob
        assert "accessNotConfigured" in blob or "403" in blob
        result = json.loads(blob)
        assert result.get("connected") is True  # the account genuinely IS connected

    def test_zero_accounts_still_gives_the_honest_not_connected_note(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: False)
        result = json.loads(agent_mod._tool_query_calendar({}))
        assert result.get("connected") is False
        assert "connecting" in result.get("note", "").lower()

    def test_names_which_store_was_checked(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_calendar", lambda days=2: {
            "accounts": [{"id": "acc1", "label": "Personal", "email": "personal@example.com"}],
            "events": [], "errors": [],
        })
        result = json.loads(agent_mod._tool_query_calendar({}))
        assert "multi-account" in result.get("store", "").lower()


class TestSearchEmailMultiAccount:
    def test_reports_every_account_not_just_the_primary(self, monkeypatch):
        monkeypatch.setattr(ce, "_collect_messages",
                            lambda limit=25: ([{"subject": "STALE single-account cache hit"}], "cache"))
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_gmail", lambda limit_per_account=15: {
            "accounts": [
                {"id": "acc1", "label": "Personal", "email": "personal@example.com"},
                {"id": "acc2", "label": "Work", "email": "work@example.com"},
            ],
            "messages": [
                {"sender": "boss@example.com", "subject": "Re: launch",
                 "snippet": "...", "timestamp": "2026-08-13T09:00:00",
                 "account_id": "acc2", "account_label": "Work"},
            ],
            "errors": [],
        })
        blob = agent_mod._tool_search_email({"query": ""})
        assert "STALE single-account cache hit" not in blob
        result = json.loads(blob)
        account_labels = {a["label"] for a in result.get("accounts", [])}
        assert account_labels == {"Personal", "Work"}

    def test_a_real_api_error_is_never_mislabeled_as_not_connected(self, monkeypatch):
        monkeypatch.setattr(ce, "_collect_messages",
                            lambda limit=25: ([], "empty"))
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_gmail", lambda limit_per_account=15: {
            "accounts": [{"id": "acc1", "label": "Personal", "email": "personal@example.com"}],
            "messages": [],
            "errors": [{"account_id": "acc1", "label": "Personal",
                        "error": "Gmail fetch failed: <HttpError 403 ... "
                                 "accessNotConfigured ...>"}],
        })
        blob = agent_mod._tool_search_email({"query": ""})
        assert "NOT CONNECTED on this machine" not in blob
        assert "accessNotConfigured" in blob or "403" in blob
        result = json.loads(blob)
        assert result.get("connected") is True

    def test_zero_accounts_falls_back_to_the_legacy_cache_path(self, monkeypatch):
        # No accounts at all -> preserve the existing offline-cache behavior
        # rather than regressing it; this is the one case where the OLD path
        # is still the right path.
        monkeypatch.setattr(ga, "has_accounts", lambda: False)
        monkeypatch.setattr(ce, "_collect_messages",
                            lambda limit=25: ([{"subject": "cached", "sender": "x", "snippet": "y"}], "cache"))
        blob = agent_mod._tool_search_email({"query": ""})
        result = json.loads(blob)
        assert result.get("connected") is True
        assert result.get("source") == "cache"

    def test_names_which_store_was_checked(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_gmail", lambda limit_per_account=15: {
            "accounts": [{"id": "acc1", "label": "Personal", "email": "personal@example.com"}],
            "messages": [], "errors": [],
        })
        result = json.loads(agent_mod._tool_search_email({"query": ""}))
        assert "multi-account" in result.get("store", "").lower()


class TestConnectorStatusMultiAccount:
    def test_status_is_per_account_and_names_the_store(self, monkeypatch):
        from agent_friday.services import connectors as conn

        monkeypatch.setattr(ga, "list_accounts", lambda: [
            {"id": "acc1", "label": "Personal", "email": "personal@example.com"},
            {"id": "acc2", "label": "Work", "email": "work@example.com"},
        ])
        monkeypatch.setattr(ga, "credentials_for", lambda aid: object())  # both "connected"

        status = conn._status_for_google(conn.CONNECTOR_DEFS["google"])
        assert "multi-account" in status.get("store", "").lower()
        labels = {a["label"] for a in status.get("accounts", [])}
        assert labels == {"Personal", "Work"}
        assert all(a["status"] == "connected" for a in status["accounts"])

    def test_status_flags_a_needs_reauth_account_individually(self, monkeypatch):
        from agent_friday.services import connectors as conn

        monkeypatch.setattr(ga, "list_accounts", lambda: [
            {"id": "acc1", "label": "Personal", "email": "personal@example.com"},
            {"id": "acc2", "label": "Work", "email": "work@example.com"},
        ])
        monkeypatch.setattr(
            ga, "credentials_for",
            lambda aid: object() if aid == "acc1" else None)  # acc2 needs reauth

        status = conn._status_for_google(conn.CONNECTOR_DEFS["google"])
        by_label = {a["label"]: a["status"] for a in status["accounts"]}
        assert by_label["Personal"] == "connected"
        assert by_label["Work"] == "needs_reauth"
        # Overall status still reflects SOME account working.
        assert status["status"] == "connected"
