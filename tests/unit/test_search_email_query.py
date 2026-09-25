"""search_email() query plumbing (docs: gmail-query-fix).

Two independent defects in the live (multi-account) search_email path:

1. The query string the model passes to search_email() never reaches Gmail's
   own `q=` search parameter. `_gmail_for_creds()` always builds its query
   from a fixed `is:unread`/`newer_than:Nd` window and ignores the caller's
   search term entirely, so anything outside that small recent/unread window
   is invisible no matter what is searched for.

2. The local "filter" that DOES look at the query text (the substring check
   in `_tool_search_email`) has no word-boundary check: searching for "test"
   matches "latest", "contest", "protest", etc. — false positives stacked on
   top of the false negatives from defect 1.

These tests lock in both defects with fail-before/pass-after cases. They
mock `_gmail_for_creds`'s underlying Gmail service build so no real network
call is made, and assert on what the ACTUAL `q=` string built for the Gmail
API contains.
"""
from __future__ import annotations

import json

import agent_friday.services.agent as agent_mod
from agent_friday.services import google_accounts as ga


def _summary(connected=True, total=1, healthy=1, degraded=False,
             attention=(), note=""):
    return {"total": total, "healthy": healthy, "connected": connected,
            "degraded": degraded, "needs_attention": list(attention),
            "note": note}


class _FakeMessagesResource:
    """Records every `q=` string passed to messages().list(...)."""

    def __init__(self, queries_seen, message_ids_for_query=None):
        self._queries_seen = queries_seen
        self._message_ids_for_query = message_ids_for_query or {}

    def list(self, userId="me", q="", maxResults=15):  # noqa: N803
        self._queries_seen.append(q)
        ids = self._message_ids_for_query.get(q, [])
        return _FakeExecutable({"messages": [{"id": i} for i in ids]})

    def get(self, userId="me", id="", format="metadata", metadataHeaders=None):  # noqa: N803
        # Minimal metadata payload keyed by fake message id.
        headers = [
            {"name": "From", "value": "sender@example.com"},
            {"name": "Subject", "value": _SUBJECTS_BY_ID.get(id, id)},
            {"name": "Date", "value": "2026-09-19"},
        ]
        return _FakeExecutable({
            "id": id, "threadId": id, "snippet": "",
            "internalDate": "1758000000000",
            "labelIds": [],
            "payload": {"headers": headers},
        })


class _FakeExecutable:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeUsers:
    def __init__(self, queries_seen, message_ids_for_query):
        self._messages = _FakeMessagesResource(queries_seen, message_ids_for_query)

    def messages(self):
        return self._messages


class _FakeGmailService:
    def __init__(self, queries_seen, message_ids_for_query):
        self._users = _FakeUsers(queries_seen, message_ids_for_query)

    def users(self):
        return self._users

    def new_batch_http_request(self, callback):
        # The real client's batch: run each queued request, report through callback.
        class _Batch:
            def __init__(self):
                self._items = []

            def add(self, request, request_id):
                self._items.append((request_id, request))

            def execute(self):
                for rid, req in self._items:
                    callback(rid, req.execute(), None)
        return _Batch()


# Fake message ids -> subject text, so a test can tell which message a query
# actually surfaced.
_SUBJECTS_BY_ID = {
    "msg-old-relevant": "quarterly test results attached",
    "msg-false-positive": "the latest updates from the team",
}


class TestGmailQueryReachesTheApi:
    """Defect 1: the query never reached Gmail's own q= parameter."""

    def test_query_string_is_passed_into_gmails_q_parameter(self, monkeypatch):
        queries_seen = []

        def fake_build(service_name, version, credentials=None, cache_discovery=False):
            assert service_name == "gmail"
            return _FakeGmailService(queries_seen, {})

        monkeypatch.setattr(
            "googleapiclient.discovery.build", fake_build, raising=False)

        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
        monkeypatch.setattr(
            ga, "_accounts_with",
            lambda service: [{"id": "acc1", "label": "Personal",
                              "email": "a@example.com", "color": "#000"}])

        result = ga.merged_gmail(limit_per_account=15, query="budget forecast")

        assert queries_seen, "Gmail messages().list was never called"
        # The FIX: the caller's search term must appear in at least one of
        # the q= strings actually sent to the Gmail API.
        assert any("budget forecast" in q for q in queries_seen), (
            f"query text never reached Gmail's q= parameter; saw: {queries_seen}")

    def test_a_message_outside_the_recent_unread_window_is_found_by_query(self, monkeypatch):
        """The concrete symptom: a message that is neither unread nor within
        the recency window must still be found when the caller searches for
        it by subject text, because Gmail's own q= does the filtering
        instead of a fixed local window."""
        queries_seen = []

        def fake_build(service_name, version, credentials=None, cache_discovery=False):
            # Only the query that actually embeds the search term returns
            # the older, read message. Any other query is empty — proving
            # the message would be invisible under the OLD unread/recent-
            # only windowing.
            ids_for_query = {}

            def register(q):
                if "budget forecast" in q:
                    ids_for_query[q] = ["msg-old-relevant"]

            svc = _FakeGmailService(queries_seen, ids_for_query)
            # Patch list() to compute membership lazily since q strings are
            # only known at call time in the real implementation too.
            orig_list = svc.users()._messages.list

            def list_patched(userId="me", q="", maxResults=15):  # noqa: N803
                queries_seen.append(q)
                ids = ["msg-old-relevant"] if "budget forecast" in q else []
                return _FakeExecutable({"messages": [{"id": i} for i in ids]})

            svc.users()._messages.list = list_patched
            return svc

        monkeypatch.setattr(
            "googleapiclient.discovery.build", fake_build, raising=False)
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
        monkeypatch.setattr(
            ga, "_accounts_with",
            lambda service: [{"id": "acc1", "label": "Personal",
                              "email": "a@example.com", "color": "#000"}])

        result = ga.merged_gmail(limit_per_account=15, query="budget forecast")
        subjects = [m.get("subject") for m in result.get("messages", [])]
        assert "quarterly test results attached" in subjects, (
            f"the old/read message matching the query was not returned; got: {subjects}")


class TestSearchEmailWordBoundary:
    """Defect 2: substring filter has no word-boundary check.

    A live search sends the query to Gmail and does no local filtering, so
    the word-boundary rule applies where Friday still matches text itself:
    the offline cache a never-connected install searches."""

    def test_the_live_search_is_gmails_and_is_not_refiltered(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(agent_mod, "_google_connectivity",
                             lambda: (_summary(), {"connected": True}))
        seen = []

        def merged(**kw):
            seen.append(kw.get("query"))
            return {
                "accounts": [{"id": "acc1", "label": "Personal", "email": "a@example.com"}],
                # Gmail matched this one (on its body, say); it is Gmail's answer.
                "messages": [
                    {"sender": "x@example.com", "subject": "the latest updates from the team",
                     "snippet": "nothing relevant here", "timestamp": "2026-09-19",
                     "account_id": "acc1", "account_label": "Personal"},
                ],
                "errors": [],
            }
        monkeypatch.setattr(ga, "merged_gmail", merged)
        result = json.loads(agent_mod._tool_search_email({"query": "test"}))
        assert seen == ["test"], "the query must be Gmail's q=, not a local filter"
        assert [m["subject"] for m in result["messages"]] == [
            "the latest updates from the team"]

    def test_query_test_does_not_match_latest_in_the_offline_cache(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: False)
        from agent_friday.services import calendar_engine
        monkeypatch.setattr(calendar_engine, "_collect_messages", lambda limit=25: ([
            {"sender": "x@example.com", "subject": "the latest updates from the team",
             "snippet": "nothing relevant here"},
            {"sender": "y@example.com", "subject": "quarterly test results attached",
             "snippet": "see attached"},
        ], "cache"))
        result = json.loads(agent_mod._tool_search_email({"query": "test"}))
        subjects = [m["subject"] for m in result.get("messages", [])]
        assert subjects == ["quarterly test results attached"], (
            "searching 'test' matched 'latest' — no word-boundary check")

    def test_query_test_does_match_a_real_word_boundary_hit(self, monkeypatch):
        monkeypatch.setattr(ga, "has_accounts", lambda: True)
        monkeypatch.setattr(agent_mod, "_google_connectivity",
                             lambda: (_summary(), {"connected": True}))
        monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
            "accounts": [{"id": "acc1", "label": "Personal", "email": "a@example.com"}],
            "messages": [
                {"sender": "x@example.com", "subject": "quarterly test results attached",
                 "snippet": "see attached", "timestamp": "2026-09-19",
                 "account_id": "acc1", "account_label": "Personal"},
            ],
            "errors": [],
        })
        blob = agent_mod._tool_search_email({"query": "test"})
        result = json.loads(blob)
        subjects = [m["subject"] for m in result.get("messages", [])]
        assert "quarterly test results attached" in subjects
