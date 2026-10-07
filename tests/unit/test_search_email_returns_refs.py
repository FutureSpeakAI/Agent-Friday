"""search_email hits carry the ref organize_email takes (audit B1).

organize_email's schema says to pass thread_ids "from search_email", but a hit used to carry no id
at all. Each hit now has `ref == "mail:<account>:<thread>"`.
"""
from __future__ import annotations

import json

from agent_friday.services import agent as agent_mod
from agent_friday.services import google_accounts as ga


def _summary():
    return {"total": 1, "healthy": 1, "connected": True, "degraded": False, "needs_attention": [], "note": ""}


def test_every_hit_has_a_ref_with_its_account_and_thread(monkeypatch):
    monkeypatch.setattr(ga, "has_accounts", lambda: True)
    monkeypatch.setattr(agent_mod, "_google_connectivity", lambda: (_summary(), {"connected": True}))
    monkeypatch.setattr(ga, "merged_gmail", lambda **kw: {
        "accounts": [{"id": "acct_work", "label": "Work", "email": "a@example.com"}],
        "messages": [
            {"id": "m1", "thread_id": "tA", "sender": "x@example.com", "subject": "one", "snippet": "s",
             "timestamp": "2026-09-19", "account_id": "acct_work", "account_label": "Work"},
            {"id": "m2", "gmail_id": "gB", "sender": "y@example.com", "subject": "two", "snippet": "s",
             "timestamp": "2026-09-19", "account_id": "acct_home", "account_label": "Home"}],
        "errors": []})
    out = json.loads(agent_mod._tool_search_email({"query": "anything"}))
    assert [m["ref"] for m in out["messages"]] == ["mail:acct_work:tA", "mail:acct_home:gB"]


def test_a_ref_splits_back_into_what_organize_email_takes():
    from agent_friday.services.screen_stage import mail_ref, split_mail_ref
    assert split_mail_ref(mail_ref("acct_work", "tA")) == ("acct_work", "tA")
    assert split_mail_ref("acct_work:tA") is None and split_mail_ref("mail:acct_work") is None
