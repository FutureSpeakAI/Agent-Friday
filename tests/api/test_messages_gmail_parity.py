"""The Messages workspace reads Gmail honestly: searches go to Gmail's own
query, a failure is reported as a failure (never "0"), and a thread opens
from whichever account it belongs to."""
import pytest

from agent_friday.services import gmail_api, gmail_read, message_triage as mt
from agent_friday.services import google_accounts as ga
from agent_friday.services import calendar_engine as ce


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    mt._collect_cache.clear()
    monkeypatch.setattr(ce, "_load_cached_messages", lambda: [
        {"id": "cached1", "sender": "Old <old@example.test>", "subject": "stale cached mail",
         "snippet": "x", "timestamp": "Mon, 1 Sep 2026 10:00:00 +0000", "labels": ["INBOX"]}])
    monkeypatch.setattr(ce, "_collect_messages", lambda limit=40: ([], "empty"))
    yield
    mt._collect_cache.clear()


def _raw(i, aid="acct-p", label="Personal"):
    return {"gmail_id": "g%d" % i, "sender": "Ada <ada@example.test>", "subject": "Hello %d" % i,
            "snippet": "s", "timestamp": "2026-09-22T10:0%d:00" % i, "thread_id": "t%d" % i,
            "labels": ["INBOX", "UNREAD"], "unread": True, "to": "me@example.test", "cc": "",
            "account_id": aid, "account_label": label}


def test_search_goes_to_gmail_and_cards_carry_gmail_ids(client, monkeypatch):
    seen = {}

    def fake_merged(limit_per_account=15, days=None, query=None):
        seen["query"] = query
        return {"accounts": [{"id": "acct-p"}], "messages": [_raw(1)], "errors": []}
    monkeypatch.setattr(ga, "merged_gmail", fake_merged)
    r = client.get("/api/messages?q=from:ada%20is:unread").get_json()
    assert seen["query"] == "from:ada is:unread"
    assert r["status"] == "ok" and r["total"] == 1
    m = r["messages"][0]
    assert m["gmail_id"] == "g1" and m["account_id"] == "acct-p" and m["thread_id"] == "t1"


def test_failed_search_is_an_error_not_zero_and_never_shows_cached_mail(client, monkeypatch):
    monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
        "accounts": [{"id": "acct-p"}], "messages": [],
        "errors": [{"account_id": "acct-p", "label": "Personal", "kind": "rate_limited",
                    "error": "Gmail is limiting how fast Friday can read this account right now"}]})
    r = client.get("/api/messages?q=is:unread").get_json()
    assert r["status"] == "error" and r["search_failed"] is True
    assert r["total"] is None and r["messages"] == []
    assert "Personal" in r["error"] and "limiting" in r["error"]
    assert r["rate_limited"] is True


def test_partial_result_says_it_is_partial(client, monkeypatch):
    monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
        "accounts": [{"id": "acct-p"}, {"id": "acct-w"}], "messages": [_raw(1)],
        "errors": [{"account_id": "acct-w", "label": "Work", "kind": "rate_limited", "error": "limited"}]})
    r = client.get("/api/messages").get_json()
    assert r["status"] == "ok" and r["partial"] is True and r["total"] == 1
    assert r["errors"][0]["label"] == "Work"


def test_badge_reports_failure_instead_of_zero(client, monkeypatch):
    monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
        "accounts": [{"id": "acct-p"}], "messages": [],
        "errors": [{"account_id": "acct-p", "label": "Personal", "kind": "auth", "error": "needs reconnecting"}]})
    monkeypatch.setattr(ce, "_load_cached_messages", lambda: [])
    r = client.get("/api/messages/stats").get_json()
    assert r["status"] == "error" and r["actionable"] is None and r["counts"] is None


def test_badge_poll_and_list_share_no_slot_and_do_not_refetch(client, monkeypatch):
    calls = []

    def fake_merged(limit_per_account=15, days=None, query=None):
        calls.append(limit_per_account)
        return {"accounts": [{"id": "acct-p"}], "messages": [_raw(1)], "errors": []}
    monkeypatch.setattr(ga, "merged_gmail", fake_merged)
    for _ in range(3):
        client.get("/api/messages/stats")
        client.get("/api/messages")
    assert sorted(calls) == [40, 80]


def test_rate_limited_result_is_reused_briefly_instead_of_hammering(client, monkeypatch):
    calls = []

    def fake_merged(**kw):
        calls.append(1)
        return {"accounts": [{"id": "acct-p"}], "messages": [_raw(1)],
                "errors": [{"account_id": "acct-w", "label": "Work", "kind": "rate_limited", "error": "limited"}]}
    monkeypatch.setattr(ga, "merged_gmail", fake_merged)
    for _ in range(4):
        client.get("/api/messages/stats")
    assert len(calls) == 1


class _Svc:
    """A Gmail service double where only one account holds the thread."""
    def __init__(self, has_thread):
        self.has = has_thread

    def users(self):
        svc = self

        class T:
            def get(self, **kw):
                class R:
                    def execute(_):
                        if not svc.has:
                            e = Exception("nf")
                            e.resp = type("R", (), {"status": 404, "get": lambda s, k, d=None: None})()
                            e.content = b'{"error":{"errors":[{"reason":"notFound"}]}}'
                            raise e
                        return {"messages": [{"id": "g1", "threadId": "t9", "labelIds": ["INBOX"],
                                              "internalDate": "1790000000000", "snippet": "hi",
                                              "payload": {"mimeType": "multipart/mixed", "headers": [
                                                  {"name": "From", "value": "Ada <ada@example.test>"},
                                                  {"name": "To", "value": "me@example.test"},
                                                  {"name": "Cc", "value": "bo@example.test"},
                                                  {"name": "Subject", "value": "Plans"},
                                                  {"name": "Message-ID", "value": "<abc@mail>"}],
                                                  "parts": [
                                                      {"mimeType": "text/html", "body": {"data": "PHA-SGk8L3A-"}},
                                                      {"mimeType": "application/pdf", "filename": "plan.pdf",
                                                       "body": {"attachmentId": "att1", "size": 1234}}]}}]}
                return R()

        class U:
            def threads(self):
                return T()
        return U()


def test_work_account_thread_opens(client, monkeypatch):
    monkeypatch.setattr(ga, "_accounts_with", lambda svc: [{"id": "acct-p", "label": "Personal"}, {"id": "acct-w", "label": "Work"}])
    monkeypatch.setattr(ga, "credentials_for", lambda aid: aid)
    monkeypatch.setattr(gmail_read, "_service", lambda creds: _Svc(creds == "acct-w"))
    r = client.get("/api/messages/t9").get_json()
    assert r["status"] == "ok" and r["account_id"] == "acct-w"
    m = r["messages"][0]
    assert m["cc"] == "bo@example.test" and m["message_id_header"] == "<abc@mail>"
    assert "<p>Hi</p>" in m["html"]
    assert m["attachments"][0]["filename"] == "plan.pdf" and "account=acct-w" in m["attachments"][0]["url"]


def test_thread_gmail_error_is_reported_not_swallowed(client, monkeypatch):
    monkeypatch.setattr(ga, "_accounts_with", lambda svc: [{"id": "acct-p", "label": "Personal"}])
    monkeypatch.setattr(ga, "credentials_for", lambda aid: aid)

    def boom(creds):
        raise gmail_api.GmailError("rate_limited", "Gmail is limiting how fast Friday can read this account right now")
    monkeypatch.setattr(gmail_read, "_service", lambda creds: type("S", (), {"users": lambda s: (_ for _ in ()).throw(gmail_api.GmailError("rate_limited", "Gmail is limiting"))})())
    r = client.get("/api/messages/t9?account=acct-p")
    assert r.status_code == 502 and r.get_json()["kind"] == "rate_limited"


def test_attachment_route_never_serves_active_content(client, monkeypatch):
    monkeypatch.setattr(gmail_read, "get_attachment", lambda a, m, i: b"<html><script>x</script>")
    r = client.get("/api/messages/attachment?account=a&message=m&id=i&name=x.html&mime=text/html")
    assert r.mimetype == "application/octet-stream"
    assert r.headers["Content-Disposition"].startswith("attachment")
    assert "sandbox" in r.headers["Content-Security-Policy"]
    r = client.get("/api/messages/attachment?account=a&message=m&id=i&name=p.png&mime=image/png")
    assert r.mimetype == "image/png" and r.headers["Content-Disposition"].startswith("inline")
