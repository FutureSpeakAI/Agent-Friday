"""When the multi-account fan-out finds nothing, the legacy single-account
path's messages reach the inbox. _collect_messages returns (cards, source);
treating that pair as the message list produced an empty inbox."""
from agent_friday.services import calendar_engine as ce
from agent_friday.services import message_triage as mt


def test_legacy_messages_are_not_lost(monkeypatch):
    card = {"id": "m1", "thread_id": "t1", "sender": "Ada Example <ada@example.test>",
            "subject": "Sample", "snippet": "hello", "timestamp": "Wed, 16 Sep 2026 10:00:00 +0000",
            "labels": ["INBOX", "UNREAD"]}
    monkeypatch.setattr(ce, "_collect_messages", lambda limit=40: ([card], "cache"))
    monkeypatch.setattr(ce, "_load_cached_messages", lambda: [])
    import agent_friday.services.google_accounts as ga
    monkeypatch.setattr(ga, "list_accounts", lambda *a, **k: [], raising=False)
    mt._collect_cache.clear()
    out = mt.collect(limit_per_account=5)
    assert [m["subject"] for m in out["messages"]] == ["Sample"]
    assert out["source"] == "cache"
