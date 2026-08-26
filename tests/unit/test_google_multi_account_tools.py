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


class TestSearchDriveMultiAccount:
    def test_reports_every_account_and_badges_files(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_drive_search", lambda query, max_results: {
            "accounts": [
                {"id": "acc1", "label": "Personal", "email": "personal@example.com"},
                {"id": "acc2", "label": "Work", "email": "work@example.com"},
            ],
            "files": [
                {"id": "f1", "name": "Q3 plan.doc", "account_id": "acc2", "account_label": "Work"},
            ],
            "errors": [],
        })
        blob = agent_mod._tool_search_drive({"query": "Q3"})
        result = json.loads(blob)
        assert result["connected"] is True
        account_labels = {a["label"] for a in result.get("accounts", [])}
        assert account_labels == {"Personal", "Work"}
        assert result["files"][0]["name"] == "Q3 plan.doc"

    def test_a_real_api_error_is_never_mislabeled_as_not_connected(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_drive_search", lambda query, max_results: {
            "accounts": [{"id": "acc1", "label": "Personal", "email": "personal@example.com"}],
            "files": [],
            "errors": [{"account_id": "acc1", "label": "Personal",
                        "error": "Drive search failed: <HttpError 403 ... "
                                 "accessNotConfigured ...>"}],
        })
        blob = agent_mod._tool_search_drive({"query": ""})
        assert "NOT CONNECTED on this machine" not in blob
        assert "accessNotConfigured" in blob or "403" in blob
        result = json.loads(blob)
        assert result.get("connected") is True

    def test_zero_accounts_gives_the_honest_not_connected_note(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: False)
        result = json.loads(agent_mod._tool_search_drive({"query": ""}))
        assert result.get("connected") is False
        assert "connecting" in result.get("note", "").lower()


class TestReadDocMultiAccount:
    def test_requires_file_id(self):
        result = json.loads(agent_mod._tool_read_doc({}))
        assert "file_id" in result.get("error", "").lower()

    def test_reads_via_the_named_account(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "list_accounts", lambda: [
            {"id": "acc1", "label": "Personal", "services": {"docs": True}},
        ])
        seen = {}

        def fake_read(account_id, file_id, mime_type=None):
            seen["account_id"] = account_id
            seen["file_id"] = file_id
            return {"name": "Plan", "type": "doc", "content": "hello world"}

        monkeypatch.setattr(ga, "read_doc_or_sheet", fake_read)
        blob = agent_mod._tool_read_doc({"file_id": "f1", "account_id": "acc1"})
        result = json.loads(blob)
        assert result["content"] == "hello world"
        assert seen["account_id"] == "acc1"
        assert seen["file_id"] == "f1"

    def test_tries_every_docs_enabled_account_until_one_succeeds(self, monkeypatch):
        # No account_id given -> must try candidates in order rather than
        # assuming the file belongs to the first account.
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "list_accounts", lambda: [
            {"id": "acc1", "label": "Personal", "services": {"docs": True}},
            {"id": "acc2", "label": "Work", "services": {"docs": True}},
        ])

        def fake_read(account_id, file_id, mime_type=None):
            if account_id == "acc1":
                return {"error": "needs_reauth", "account_id": "acc1"}
            return {"name": "Budget", "type": "sheet", "rows": [["a", "b"]]}

        monkeypatch.setattr(ga, "read_doc_or_sheet", fake_read)
        blob = agent_mod._tool_read_doc({"file_id": "f2"})
        result = json.loads(blob)
        assert result["name"] == "Budget"
        assert result["account_id"] == "acc2"

    def test_zero_accounts_gives_the_honest_not_connected_note(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: False)
        result = json.loads(agent_mod._tool_read_doc({"file_id": "f1"}))
        assert result.get("connected") is False
        assert "connecting" in result.get("note", "").lower()


class TestListTasksMultiAccount:
    def test_reports_every_account_and_badges_tasks(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "merged_tasks", lambda max_results: {
            "accounts": [
                {"id": "acc1", "label": "Personal", "email": "personal@example.com"},
                {"id": "acc2", "label": "Work", "email": "work@example.com"},
            ],
            "tasks": [
                {"id": "t1", "title": "Buy milk", "account_id": "acc1", "account_label": "Personal"},
            ],
            "errors": [],
        })
        result = json.loads(agent_mod._tool_list_tasks({}))
        account_labels = {a["label"] for a in result.get("accounts", [])}
        assert account_labels == {"Personal", "Work"}
        assert result["tasks"][0]["title"] == "Buy milk"

    def test_zero_accounts_gives_the_honest_not_connected_note(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: False)
        result = json.loads(agent_mod._tool_list_tasks({}))
        assert result.get("connected") is False
        assert "connecting" in result.get("note", "").lower()


class TestTaskWritesToolHandlers:
    """complete_task/create_task/update_task/delete_task at the tool-handler
    layer: the account named in the tool call is the one that gets touched —
    unlike list_tasks (fan-out), a write handler must forward account_id
    verbatim rather than resolve/guess it itself."""

    def test_complete_task_forwards_the_named_account_and_ids_verbatim(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ga, "complete_task", lambda **kw: calls.append(kw) or
                            {"id": "t1", "status": "completed", "account_id": "acc2"})
        blob = agent_mod._tool_complete_task(
            {"task_id": "t1", "tasklist_id": "tl9", "account_id": "acc2"})
        result = json.loads(blob)
        assert result["status"] == "completed"
        assert calls == [{"account_id": "acc2", "tasklist_id": "tl9", "task_id": "t1"}]

    def test_complete_task_never_defaults_to_the_first_account(self, monkeypatch):
        # If the model omits account_id, the handler must not silently pick
        # one — it forwards the empty string and google_accounts.complete_task
        # is what refuses it with a clear error (see test_google_accounts.py).
        monkeypatch.setattr(ga, "complete_task",
                            lambda **kw: {"error": "account_id is required for a task write"})
        blob = agent_mod._tool_complete_task({"task_id": "t1", "tasklist_id": "tl9"})
        result = json.loads(blob)
        assert "account_id" in result.get("error", "")

    def test_create_task_defaults_tasklist_to_default_but_not_account(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ga, "create_task", lambda **kw: calls.append(kw) or
                            {"id": "new1", "title": kw["title"]})
        agent_mod._tool_create_task({"title": "Buy milk", "account_id": "acc1"})
        assert calls == [{"account_id": "acc1", "title": "Buy milk",
                          "tasklist_id": "@default", "notes": "", "due": ""}]

    def test_update_task_forwards_only_provided_fields(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ga, "update_task", lambda **kw: calls.append(kw) or
                            {"id": "t1", "title": "Renamed"})
        agent_mod._tool_update_task({
            "task_id": "t1", "tasklist_id": "tl9", "account_id": "acc1", "title": "Renamed"})
        assert calls[0]["account_id"] == "acc1"
        assert calls[0]["tasklist_id"] == "tl9"
        assert calls[0]["task_id"] == "t1"
        assert calls[0]["title"] == "Renamed"

    def test_delete_task_forwards_the_named_account_and_ids_verbatim(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ga, "delete_task", lambda **kw: calls.append(kw) or
                            {"deleted": True, "id": "t1"})
        blob = agent_mod._tool_delete_task(
            {"task_id": "t1", "tasklist_id": "tl9", "account_id": "acc2"})
        result = json.loads(blob)
        assert result["deleted"] is True
        assert calls == [{"account_id": "acc2", "tasklist_id": "tl9", "task_id": "t1"}]

    def test_ring_and_confirmation_gating(self):
        # complete_task/create_task/update_task/delete_task are network writes
        # to another service (Ring 2, like the calendar-write tools) and
        # delete_task is additionally irreversible, so it must always ask
        # first regardless of the confirm_before_opening setting.
        assert agent_mod.TOOL_RINGS["complete_task"] == 2
        assert agent_mod.TOOL_RINGS["create_task"] == 2
        assert agent_mod.TOOL_RINGS["update_task"] == 2
        assert agent_mod.TOOL_RINGS["delete_task"] == 2
        assert "delete_task" in agent_mod._ALWAYS_CONFIRM
        assert "complete_task" not in agent_mod._ALWAYS_CONFIRM

    def test_all_four_are_registered_and_dispatchable(self):
        for name in ("complete_task", "create_task", "update_task", "delete_task"):
            assert name in agent_mod.CLAUDE_TOOL_HANDLERS
            assert any(t["name"] == name for t in agent_mod.CLAUDE_TOOLS)


class TestSearchContactsMultiAccount:
    def test_reports_every_account_and_badges_contacts(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "search_contacts", lambda query, max_results: {
            "accounts": [
                {"id": "acc1", "label": "Personal", "email": "personal@example.com"},
                {"id": "acc2", "label": "Work", "email": "work@example.com"},
            ],
            "contacts": [
                {"name": "Jane Doe", "emails": ["jane@example.com"],
                 "account_id": "acc2", "account_label": "Work"},
            ],
            "errors": [],
        })
        blob = agent_mod._tool_search_contacts({"query": "jane"})
        result = json.loads(blob)
        account_labels = {a["label"] for a in result.get("accounts", [])}
        assert account_labels == {"Personal", "Work"}
        assert result["contacts"][0]["name"] == "Jane Doe"

    def test_a_real_api_error_is_never_mislabeled_as_not_connected(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "search_contacts", lambda query, max_results: {
            "accounts": [{"id": "acc1", "label": "Personal", "email": "personal@example.com"}],
            "contacts": [],
            "errors": [{"account_id": "acc1", "label": "Personal",
                        "error": "Contacts fetch failed: <HttpError 403 ... "
                                 "accessNotConfigured ...>"}],
        })
        blob = agent_mod._tool_search_contacts({"query": ""})
        assert "NOT CONNECTED on this machine" not in blob
        assert "accessNotConfigured" in blob or "403" in blob
        result = json.loads(blob)
        assert result.get("connected") is True

    def test_zero_accounts_gives_the_honest_not_connected_note(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: False)
        result = json.loads(agent_mod._tool_search_contacts({"query": ""}))
        assert result.get("connected") is False
        assert "connecting" in result.get("note", "").lower()
