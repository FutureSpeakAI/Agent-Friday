"""Per-connector fixtures for Google: pagination, rate limits, auth expiry and partial reads
(BD-06, NS-34.11-1). Each fixture catches a defect that was on main:

* the calendar answer kept the first 20 events and said nothing, and each account read only
  its first 50, so a full day read as free in the evening;
* a Gmail count was at most 25 per account and said nothing about the rest;
* Tasks read only the first 10 lists (and 50 tasks), so a task in the 11th list was "not due";
* one message deleted between Gmail's list and its read failed the whole account ("Gmail could
  not be searched on ANY account", 24 real hits dropped);
* Drive, Tasks and Contacts answers were not marked partial when one account failed beside
  a working one.

The last fixture (an expired account named, the other still read) is a guard: that already
held on main and must keep holding. Google is a fake here: googleapiclient's build() returns
synthetic pages. Synthetic data only.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import google_accounts as ga


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self, *a, **k):
        return self._fn()


class _Paged:
    """A list endpoint answering page by page (pageToken = page index)."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def list(self, **kw):
        self.calls.append(kw)
        i = int(kw.get("pageToken") or 0)
        page = dict(self.pages[i])
        if i + 1 < len(self.pages):
            page["nextPageToken"] = str(i + 1)
        return _Req(lambda: page)


def _events(n, start=0):
    base = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)
    return [{"id": "e%d" % i, "summary": "Event %d" % i,
             "start": {"dateTime": (base + timedelta(minutes=10 * i)).isoformat()},
             "end": {"dateTime": (base + timedelta(minutes=10 * i + 5)).isoformat()}}
            for i in range(start, start + n)]


@pytest.fixture
def fake_build(monkeypatch):
    services = {}

    def build(name, version, credentials=None, cache_discovery=False):
        return services[name]
    import googleapiclient.discovery as disc
    monkeypatch.setattr(disc, "build", build)
    return services


@pytest.fixture
def one_account(monkeypatch):
    rec = {"id": "acct0001", "label": "Personal", "email": "me@example.invalid", "status": "connected",
           "services": {"gmail": True, "calendar": True, "tasks": True, "drive": True, "contacts": True}}
    monkeypatch.setattr(ga, "_migrate_legacy_if_needed", lambda: None)
    monkeypatch.setattr(ga, "_accounts_with", lambda svc: [rec])
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    monkeypatch.setattr(ga, "list_accounts", lambda: [rec])
    monkeypatch.setattr(ga, "has_accounts", lambda: True)
    monkeypatch.setattr(ag, "_google_connectivity", lambda: (
        {"connected": True, "note": "", "healthy": 1, "needs_attention": [], "total": 1}, {"connected": True}))
    return rec


# ── pagination ──────────────────────────────────────────────────────────────

def test_the_calendar_reads_every_page_and_says_when_there_was_more(fake_build, one_account):
    class Cal:
        def __init__(self, pages):
            self._ev = _Paged(pages)

        def events(self):
            return self._ev
    fake_build["calendar"] = Cal([{"items": _events(250)}, {"items": _events(30, 250)}])
    got = ga.merged_calendar(days=2)
    assert len(got["events"]) == 280, "only the first page of the calendar was read"
    assert not got.get("incomplete")
    fake_build["calendar"] = Cal([{"items": _events(1, i)} for i in range(ga.MAX_LIST_PAGES + 1)])
    got = ga.merged_calendar(days=2)
    assert got.get("incomplete"), "a calendar with more pages than were read was presented as all of it"


def test_the_calendar_answer_never_cuts_the_day_silently(one_account, monkeypatch):
    evs = [{"title": "E%d" % i, "start_time": "2026-10-29T%02d:00:00" % (8 + i // 3),
            "end_time": "", "account_label": "Personal"} for i in range(26)]
    monkeypatch.setattr(ga, "merged_calendar", lambda days=2: {
        "accounts": [{"id": "acct0001", "label": "Personal"}], "events": evs, "errors": []})
    d = json.loads(ag._tool_query_calendar({}))
    assert d["count"] == 20
    assert d.get("truncated") is True and d.get("total") == 26, "26 events were answered as 20 with no word"


def test_a_gmail_count_says_when_gmail_has_more(fake_build, one_account, monkeypatch):
    from agent_friday.services import gmail_api

    class Msgs:
        def list(self, **kw):
            return _Req(lambda: {"messages": [{"id": "m%d" % i} for i in range(25)],
                                 "nextPageToken": "next", "resultSizeEstimate": 140})

    class Users:
        def messages(self):
            return Msgs()

    class Gmail:
        def users(self):
            return Users()
    fake_build["gmail"] = Gmail()
    monkeypatch.setattr(gmail_api, "execute", lambda req, **k: req.execute())
    monkeypatch.setattr(gmail_api, "batch_get", lambda svc, ids, **k: (
        {i: {"id": i, "labelIds": ["UNREAD"], "payload": {"headers": [{"name": "Subject", "value": i}]}}
         for i in ids}, []))
    d = json.loads(ag._tool_search_email({"query": "from:bank is:unread"}))
    assert d["count"] == 25
    assert d.get("incomplete"), "25 of about 140 was answered as the total"
    assert d["incomplete"][0].get("estimated_total") == 140
    assert "error" not in d, "having more mail than was read is not a failed search"


def test_tasks_reads_every_list(fake_build, one_account):
    lists = [{"id": "L%d" % i, "title": "List %d" % i} for i in range(11)]

    class Tasks:
        def tasklists(self):
            return _Paged([{"items": lists[:10]}, {"items": lists[10:]}])

        def tasks(self):
            class T:
                def list(self, **kw):
                    items = [{"id": "t-" + kw["tasklist"], "title": "Pay tax"}] if kw["tasklist"] == "L10" else []
                    return _Req(lambda: {"items": items})
            return T()
    fake_build["tasks"] = Tasks()
    got = ga.merged_tasks()
    assert any(t["list"] == "List 10" for t in got["tasks"]), "the 11th task list was never read"


# ── partial reads, rate limits, auth expiry ──────────────────────────────────

def test_a_message_deleted_between_list_and_read_does_not_fail_the_search(fake_build, one_account, monkeypatch):
    from agent_friday.services import gmail_api

    class Msgs:
        def list(self, **kw):
            return _Req(lambda: {"messages": [{"id": "m1"}, {"id": "m2"}]})

    class Users:
        def messages(self):
            return Msgs()

    class Gmail:
        def users(self):
            return Users()
    fake_build["gmail"] = Gmail()
    monkeypatch.setattr(gmail_api, "execute", lambda req, **k: req.execute())
    monkeypatch.setattr(gmail_api, "batch_get", lambda svc, ids, **k: (
        {"m1": {"id": "m1", "payload": {"headers": [{"name": "Subject", "value": "kept"}]}}},
        [{"id": "m2", "kind": "not_found", "message": "Requested entity was not found."}]))
    d = json.loads(ag._tool_search_email({"query": "subject:kept"}))
    assert not d.get("search_failed"), "one deleted message failed the whole search"
    assert d["count"] == 1


def _two_accounts(monkeypatch):
    work = {"id": "acct0002", "label": "Work", "email": "w@example.invalid", "status": "connected",
            "services": {"tasks": True, "calendar": True}}
    home = {"id": "acct0001", "label": "Personal", "email": "me@example.invalid", "status": "connected",
            "services": {"tasks": True, "calendar": True}}
    monkeypatch.setattr(ga, "_accounts_with", lambda svc: [home, work])
    monkeypatch.setattr(ga, "list_accounts", lambda: [home, work])
    return home, work


def test_one_busy_account_beside_a_working_one_is_a_partial_answer(one_account, monkeypatch):
    _two_accounts(monkeypatch)
    monkeypatch.setattr(ga, "_tasks_for_creds", lambda creds, n: (
        [{"error": "Tasks fetch failed: <HttpError 429 rateLimitExceeded>"}] if creds == "w" else
        [{"id": "t1", "title": "Water plants", "list": "Home"}]))
    monkeypatch.setattr(ga, "credentials_for", lambda aid: "w" if aid == "acct0002" else "h")
    d = json.loads(ag._tool_list_tasks({}))
    assert d["count"] == 1
    assert d.get("partial") is True and "only 1 of 2" in d.get("error", ""), \
        "a task list with one account unread was answered as complete"


def test_an_expired_account_is_named_and_the_other_is_still_read(one_account, monkeypatch):
    _two_accounts(monkeypatch)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: None if aid == "acct0002" else "h")
    monkeypatch.setattr(ga, "_calendar_for_creds", lambda creds, s, e: [
        {"id": "e1", "title": "Dentist", "start_time": "2026-10-29T09:00:00", "end_time": ""}])
    got = ga.merged_calendar(days=2)
    assert [e["title"] for e in got["events"]] == ["Dentist"]
    assert got["errors"] == [{"account_id": "acct0002", "label": "Work", "error": "needs_reauth"}]


def test_a_search_where_no_message_could_be_read_is_a_failed_search_not_zero(fake_build, one_account, monkeypatch):
    from agent_friday.services import gmail_api

    class Msgs:
        def list(self, **kw):
            return _Req(lambda: {"messages": [{"id": "m1"}, {"id": "m2"}]})

    class Users:
        def messages(self):
            return Msgs()

    class Gmail:
        def users(self):
            return Users()
    fake_build["gmail"] = Gmail()
    monkeypatch.setattr(gmail_api, "execute", lambda req, **k: req.execute())
    monkeypatch.setattr(gmail_api, "batch_get", lambda svc, ids, **k: (
        {}, [{"id": i, "kind": "rate_limited", "message": "Rate limit exceeded"} for i in ids]))
    d = json.loads(ag._tool_search_email({"query": "from:bank"}))
    assert d.get("search_failed") is True and "count" not in d


def test_voice_says_there_may_be_more_and_counts_the_whole_day():
    from agent_friday.services import voice_spoken as vs
    data = {"count": 20, "total": 26, "truncated": True, "events": [], "accounts": []}
    assert vs._total(data, 20) == 26, "the spoken day counted only the 20 shown"
    pt = getattr(vs, "_partial_text", None)
    assert pt is not None and vs._incomplete(data), "voice drops 'there may be more'"
    assert pt(data) == vs._MORE
    failed = {"not_searched": [{"account": "Work"}], "accounts": []}
    assert pt(failed) == vs._PARTIAL
    from agent_friday.services import voice_front as vf
    assert "incomplete" in vf._META_LISTS
