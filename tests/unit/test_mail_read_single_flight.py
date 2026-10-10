"""A mail read that does not answer is shown as an error, never as an empty
inbox. The server side: one live Gmail read at a time, the last good result
served at once while a refresh runs behind it, and the calendar cross-reference
built once per TTL. Gmail is a counting stub; nothing here touches a network."""
import threading
import time

import pytest

from agent_friday.services import calendar_engine as ce
from agent_friday.services import message_triage as mt
import agent_friday.services.google_accounts as ga

RAW = {"id": "m1", "thread_id": "t1", "account_id": "a1", "sender": "Ada Example <ada@example.test>",
       "subject": "Synthetic", "snippet": "hello", "timestamp": "Wed, 16 Sep 2026 10:00:00 +0000",
       "labels": ["INBOX", "UNREAD"]}


@pytest.fixture
def gmail(monkeypatch):
    state = {"calls": 0, "delay": 0.0, "release": None}

    def merged(limit_per_account=25, query=None):
        state["calls"] += 1
        if state["release"] is not None:
            state["release"].wait(10)
        time.sleep(state["delay"])
        return {"messages": [dict(RAW)], "errors": []}

    monkeypatch.setattr(ga, "merged_gmail", merged)
    monkeypatch.setattr(ga, "list_accounts", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(ce, "_load_message_rules", lambda: {})
    monkeypatch.setattr(ce, "_load_message_state", lambda: {})
    monkeypatch.setattr(mt, "_load_signals", lambda: {"version": 1, "senders": {}})
    mt._collect_cache.clear()
    mt._collect_inflight.clear()
    yield state
    mt._collect_cache.clear()
    mt._collect_inflight.clear()


def test_concurrent_collects_make_one_live_read(gmail):
    gmail["delay"] = 0.4
    out = []
    threads = [threading.Thread(target=lambda: out.append(mt.collect(limit_per_account=5))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert gmail["calls"] == 1
    assert len(out) == 4 and all(len(r["messages"]) == 1 for r in out)


def test_expired_cache_serves_last_good_at_once_and_refreshes_once(gmail):
    mt.collect(limit_per_account=5)
    assert gmail["calls"] == 1
    key = (5, None)
    mt._collect_cache[key]["fetched_monotonic"] -= 600      # the cache has expired
    gmail["release"] = threading.Event()                    # the live read hangs until released
    started = time.monotonic()
    first = mt.collect(limit_per_account=5)
    second = mt.collect(limit_per_account=5)
    assert time.monotonic() - started < 2.0, "served from the last good result, not from a live read"
    assert first["stale"] is True and second["stale"] is True
    assert first["cache_age_seconds"] >= 600
    assert [m["subject"] for m in first["messages"]] == ["Synthetic"]
    gmail["release"].set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and mt._collect_inflight:
        time.sleep(0.02)
    assert gmail["calls"] == 2, "one background refresh, however many callers asked"
    assert mt._collect_cache[key]["fetched_monotonic"] > time.monotonic() - 5


def test_a_hung_live_read_with_nothing_to_show_is_a_failure_not_an_empty_inbox(gmail, monkeypatch):
    monkeypatch.setattr(mt, "_COLLECT_LIVE_BUDGET_SECONDS", 0.2)
    gmail["release"] = threading.Event()
    out = mt.collect(limit_per_account=5)
    gmail["release"].set()
    assert out["search_failed"] is True and out["messages"] == []
    assert out["errors"] and out["errors"][0]["kind"] == "timeout"
