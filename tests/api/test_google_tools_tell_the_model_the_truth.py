"""The Google tools must not tell the model something untrue.

2026-09-09. Two failures stacked here, and the second is the worse one.

  1. Every tool gated on ga.has_accounts() -- a record existing -- and then
     emitted "connected": True. Stephen's calendar came back empty and
     confident on a day holding two job interviews.

  2. When a fetch failed, the payload carried a note INSTRUCTING the model:
     "do not say Calendar 'needs connecting' (it's already connected)". That
     was false. Stephen asked Friday directly whether his Google accounts were
     connected and was told yes, both. The model did not hallucinate -- it
     faithfully repeated an instruction the system gave it. A claim-verifier
     cannot catch this class: nothing is fabricated. The only defence is that
     a note telling the model what to assert is emitted ONLY in the state
     where that assertion is true.

These tests pin both. They are all unhappy-path on purpose.
"""

import json

import pytest

from agent_friday.services import google_accounts as ga
from agent_friday.services import agent as ag


def _write_accounts(*records):
    ga.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    ga.ACCOUNTS_INDEX.write_text(
        json.dumps({"version": 1, "accounts": list(records)}, indent=2),
        encoding="utf-8")
    ga._MIGRATION_DONE = True


def _rec(**kw):
    base = {"id": "a1", "email": "stephen@example.com", "label": "Work",
            "status": "connected", "services": {}, "color": "#fff",
            "created": "2026-08-26T00:00:00+00:00",
            "last_sync": "2026-09-09T09:00:00+00:00", "scopes": [],
            "enc_method": "vault"}
    base.update(kw)
    return base


def _days_ago(n):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


@pytest.fixture(autouse=True)
def clean():
    import shutil
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)
    ga._MIGRATION_DONE = False
    yield
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)


THE_PRODUCTION_STATE = (
    _rec(id="a1", email="primary@example.com", label="Personal",
         status="needs_reauth", last_sync=_days_ago(9)),
    _rec(id="a2", email="stephen@futurespeak.ai", label="Work",
         status="needs_reauth", last_sync=_days_ago(9)),
)


class TestCalendarDoesNotClaimConnected:

    def test_the_interview_day(self, monkeypatch):
        """The exact call that reported an empty schedule as a real one."""
        _write_accounts(*THE_PRODUCTION_STATE)
        # If the gate is right this is never reached; make it loud if it is.
        monkeypatch.setattr(ga, "merged_calendar",
                            lambda **kw: pytest.fail("fetched with no working account"))
        d = json.loads(ag._tool_query_calendar({}))
        assert d["connected"] is False
        assert d["accounts_working"] == 0
        assert d["accounts_total"] == 2
        assert set(d["needs_reauth"]) == {"primary@example.com",
                                          "stephen@futurespeak.ai"}
        assert "9 days ago" in d["note"]

    def test_note_never_tells_the_model_it_is_connected(self):
        _write_accounts(*THE_PRODUCTION_STATE)
        note = json.loads(ag._tool_query_calendar({}))["note"]
        assert "already connected" not in note
        assert "do not say" not in note.lower()
        # and it must say the opposite, actionably
        assert "reconnect" in note.lower() or "Do NOT report" in note


class TestEmailDoesNotClaimConnected:

    def test_broken_store_does_not_fall_back_to_cache(self, monkeypatch):
        """Cached mail served as current is the same bug in another hat."""
        _write_accounts(*THE_PRODUCTION_STATE)
        monkeypatch.setattr(ga, "merged_gmail",
                            lambda **kw: pytest.fail("fetched with no working account"))
        d = json.loads(ag._tool_search_email({"query": ""}))
        assert d["connected"] is False
        assert d["messages"] == []
        assert "reconnect" in d["note"].lower() or "Do NOT report" in d["note"]


class TestDegradedSurvivesAsAFact:
    """A boolean cannot say "some work and some do not"; it must not try."""

    def test_partial_store_is_connected_but_degraded(self, monkeypatch):
        _write_accounts(_rec(id="a1", email="ok@x.com", status="connected"),
                        _rec(id="a2", email="dead@x.com", status="needs_reauth",
                             last_sync=_days_ago(9)))
        monkeypatch.setattr(ga, "merged_calendar",
                            lambda **kw: {"events": [{"title": "Standup"}],
                                          "accounts": [{"id": "a1", "label": "ok"}]})
        d = json.loads(ag._tool_query_calendar({}))
        assert d["connected"] is True
        assert d["degraded"] is True
        assert d["accounts_working"] == 1 and d["accounts_total"] == 2
        assert d["needs_reauth"] == ["dead@x.com"]
        # the model has to be able to STATE the incompleteness
        assert "INCOMPLETE" in d["note"]
        assert "dead@x.com" in d["note"]

    def test_healthy_store_says_nothing_alarming(self, monkeypatch):
        _write_accounts(_rec(id="a1", status="connected"))
        monkeypatch.setattr(ga, "merged_calendar",
                            lambda **kw: {"events": [], "accounts": [{"id": "a1", "label": "W"}]})
        d = json.loads(ag._tool_query_calendar({}))
        assert d["connected"] is True and d["degraded"] is False
        assert d.get("note", "") == "" or "INCOMPLETE" not in d.get("note", "")


class TestTheAlreadyConnectedClaimIsConditional:
    """The old note was unconditional. It may only appear when it is true."""

    def test_claim_allowed_when_every_account_really_is_authorized(self):
        summary = {"note": "", "healthy": 2, "needs_attention": []}
        note = ag._google_note(summary, "Calendar",
                               errored=[{"label": "W", "error": "429 rate limit"}],
                               no_items=True)
        assert "IS authorized" in note
        assert "429 rate limit" in note

    def test_claim_withheld_when_any_account_is_broken(self):
        summary = {"note": "Some Google accounts work and some do not.",
                   "healthy": 1,
                   "needs_attention": [{"email": "dead@x.com"}]}
        note = ag._google_note(summary, "Calendar",
                               errored=[{"label": "W", "error": "boom"}],
                               no_items=True)
        assert "IS authorized" not in note
        assert "already connected" not in note
        assert "boom" in note


class TestOfflineCacheIsNotAConnection:

    def test_never_connected_cache_path_says_it_is_cache(self, monkeypatch):
        # no accounts at all -> legacy cache fallback
        monkeypatch.setattr(
            "agent_friday.services.calendar_engine._collect_messages",
            lambda *a, **k: ([{"sender": "x@y.com", "subject": "hi"}], "cache"),
            raising=False)
        d = json.loads(ag._tool_search_email({"query": ""}))
        assert d["connected"] is False
        assert "cache" in d.get("note", "").lower()


class TestTheQueryReachesGmail:
    """2026-09-22. "Yeah I def see email you did not pick up."

    On "start my day" Friday reported every query coming back 0 unread --
    is:unread, in:inbox, in:primary, after:2026-09-21 -- while Gmail itself
    held 50 unread across the two accounts.

    Nothing was broken about OAuth, scopes, routing or pagination. The query
    was never sent to Gmail. `_tool_search_email` called merged_gmail() with
    NO query, took back the default unread/recent window, and then filtered
    those cards with a word-boundary text match over sender+subject+snippet.
    So `is:unread` was matched as LITERAL TEXT: it looked for the characters
    "is:unread" in the subject line, found them nowhere, and said 0.

    merged_gmail already took a `query` and already sent it to Gmail's own
    `q=`. It was simply never passed.
    """

    def test_the_query_is_handed_to_gmail(self, monkeypatch):
        """The regression itself: the operator must leave the process."""
        _write_accounts(_rec(id="a1", label="Personal", status="connected",
                             services={"gmail": True}))
        seen = {}

        def _fake(**kw):
            seen.update(kw)
            return {"accounts": [], "messages": [], "errors": []}

        monkeypatch.setattr(ga, "merged_gmail", _fake)
        ag._tool_search_email({"query": "is:unread"})
        assert seen.get("query") == "is:unread", (
            "the Gmail query never reached Gmail; it was %r" % seen.get("query"))

    @pytest.mark.parametrize("query", [
        "is:unread", "in:inbox", "after:2026-09-21", "from:jere",
        "subject:invoice", "has:attachment", "newer_than:7d",
    ])
    def test_gmail_operators_are_not_filtered_out_locally(self, monkeypatch, query):
        """Gmail did the matching, so every row it returned is a hit.

        The old code re-filtered Gmail's own results with a text match that
        no operator can satisfy, which zeroed every one of these.
        """
        _write_accounts(_rec(id="a1", label="Personal", status="connected",
                             services={"gmail": True}))
        monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
            "accounts": [], "errors": [],
            "messages": [{"sender": "Jere <j@example.com>",
                          "subject": "Ready to go live",
                          "snippet": "let me know", "unread": True,
                          "timestamp": "2026-09-22T09:00:00"}]})
        d = json.loads(ag._tool_search_email({"query": query}))
        assert d["count"] == 1, (
            "%r returned %d after Gmail had already matched it"
            % (query, d["count"]))


class TestABrokenSearchIsNeverZero:
    """Friday's honesty law, at the one place that broke it.

    "0 unread" and "the search did not run" are different facts and a user
    cannot tell them apart from a number. So a failed search does not get to
    report a number at all.
    """

    def test_every_account_failing_has_no_count_at_all(self, monkeypatch):
        """Not `count: 0` - no `count` key. An absent number cannot be
        misread as zero, which is exactly what a zero invites."""
        _write_accounts(
            _rec(id="a1", label="Personal", status="connected",
                 services={"gmail": True}),
            _rec(id="a2", label="Work", status="connected",
                 services={"gmail": True}))
        monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
            "accounts": [], "messages": [],
            "errors": [{"account_id": "a1", "label": "Personal",
                        "error": "Gmail fetch failed: quota exceeded"},
                       {"account_id": "a2", "label": "Work",
                        "error": "Gmail fetch failed: quota exceeded"}]})
        d = json.loads(ag._tool_search_email({"query": "is:unread"}))
        assert d.get("search_failed") is True
        assert "count" not in d, "a failed search reported a count: %r" % d.get("count")
        assert "messages" not in d
        assert "quota exceeded" in d["error"]
        assert "NOT zero" in d["error"]

    def test_an_exception_is_not_zero_either(self, monkeypatch):
        """merged_gmail raising is the same fact as it erroring."""
        _write_accounts(_rec(id="a1", label="Personal", status="connected",
                             services={"gmail": True}))

        def _boom(**kw):
            raise RuntimeError("token refresh exploded")

        monkeypatch.setattr(ga, "merged_gmail", _boom)
        d = json.loads(ag._tool_search_email({"query": "is:unread"}))
        assert d.get("search_failed") is True
        assert "count" not in d
        assert "token refresh exploded" in d["error"]

    def test_a_partial_result_says_it_is_partial(self, monkeypatch):
        """One dead account beside one working account used to report a
        confident total for both - `_google_note` only speaks when there are
        no items at all, and here there is one."""
        _write_accounts(
            _rec(id="a1", label="Personal", status="connected",
                 services={"gmail": True}),
            _rec(id="a2", label="Work", status="connected",
                 services={"gmail": True}))
        monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
            "accounts": [],
            "messages": [{"sender": "a@example.com", "subject": "hi",
                          "snippet": "", "unread": True,
                          "timestamp": "2026-09-22T09:00:00",
                          "account_label": "Personal"}],
            "errors": [{"account_id": "a2", "label": "Work",
                        "error": "Gmail fetch failed: quota exceeded"}]})
        d = json.loads(ag._tool_search_email({"query": "is:unread"}))
        assert d["count"] == 1
        assert d.get("partial") is True
        assert any(x["account"] == "Work" for x in d["not_searched"])
        assert "only 1 of 2" in d["error"]

    def test_a_healthy_empty_result_is_still_allowed_to_be_zero(self, monkeypatch):
        """The law must not make every zero an error. Gmail searching
        successfully and finding nothing is a real, reportable zero."""
        _write_accounts(_rec(id="a1", label="Personal", status="connected",
                             services={"gmail": True}))
        monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
            "accounts": [], "messages": [], "errors": []})
        d = json.loads(ag._tool_search_email({"query": "from:nobody"}))
        assert d["count"] == 0
        assert not d.get("search_failed")
        assert not d.get("partial")
