"""Approval cards reach every open page, and exactly one decision wins.

Every open Friday page shows a pending card and drops it the moment it is
decided anywhere. That rests on two properties pinned here: each status change
is published once, with the stored card unaltered; and deciding is race-safe,
so two pages (or a page and a voice command) deciding at once cannot both act.
"""
from __future__ import annotations

import threading

import pytest

from agent_friday.services import approval_feed as feed
from agent_friday.services import approvals as ap


@pytest.fixture(autouse=True)
def clean(friday_dir):
    if ap.APPROVALS_FILE.exists():
        ap.APPROVALS_FILE.unlink()
    feed.reset()
    yield
    feed.reset()


def _card(n=1, **kw):
    return ap.create_approval(kind="test_feed", subject_type="test", subject_id="s%d" % n,
                              title="Email the memo to your editor",
                              action_description="gmail_send memo", force_gate=True, **kw)


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_a_new_pending_card_reaches_every_page_unaltered():
    pages = [feed.subscribe() for _ in range(3)]
    rec = _card()
    for q in pages:
        (evt,) = _drain(q)
        assert evt["type"] == "pending"
        assert evt["approval"] == ap.get_approval(rec["approval_id"])


def test_a_card_that_is_not_pending_is_not_pushed():
    q = feed.subscribe()
    ap.create_approval(kind="test_feed", subject_type="test", subject_id="soft",
                       title="Read a page", action_description="read the page")
    assert [e for e in _drain(q) if e["type"] == "pending" and
            e["approval"]["status"] != "pending"] == []


def test_the_same_card_raised_again_is_not_pushed_twice():
    q = feed.subscribe()
    _card(1)
    _card(1)
    assert len(_drain(q)) == 1


def test_deciding_drops_the_card_everywhere():
    rec = _card()
    pages = [feed.subscribe() for _ in range(3)]
    ap.decide(rec["approval_id"], "approve", decided_by="owner:voice")
    for q in pages:
        (evt,) = _drain(q)
        assert evt == {"type": "resolved", "approval_id": rec["approval_id"],
                       "status": "approved", "decided_by": "owner:voice",
                       "decided_at": evt["decided_at"]}


def test_expiry_drops_the_card_everywhere(monkeypatch):
    rec = _card()
    ap._patch(rec["approval_id"], expires_at=1.0)
    q = feed.subscribe()
    assert ap.expire_stale() == 1
    (evt,) = _drain(q)
    assert evt["type"] == "resolved" and evt["status"] == "expired"


def test_deciding_a_decided_card_publishes_nothing_and_does_not_win():
    rec = _card()
    first, won1 = ap.decide_with_outcome(rec["approval_id"], "approve")
    q = feed.subscribe()
    second, won2 = ap.decide_with_outcome(rec["approval_id"], "deny")
    assert (won1, won2) == (True, False)
    assert second["status"] == "approved"            # the loser changed nothing
    assert _drain(q) == []


def test_decide_keeps_its_old_return_shape():
    rec = _card()
    out = ap.decide(rec["approval_id"], "deny")
    assert out["status"] == "denied"
    assert ap.decide("appr_missing", "deny") is None


def test_many_simultaneous_decisions_have_exactly_one_winner():
    rec = _card()
    fired = []
    ap.register_decision_hook("test_feed", fired.append)
    try:
        q = feed.subscribe()
        start = threading.Barrier(12)
        results = []

        def click(i):
            start.wait()
            results.append(ap.decide_with_outcome(
                rec["approval_id"], "approve" if i % 2 else "deny", decided_by="tab%d" % i))

        threads = [threading.Thread(target=click, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [r for r, won in results if won]
        assert len(winners) == 1
        final = ap.get_approval(rec["approval_id"])
        assert final["decided_by"] == winners[0]["decided_by"]
        assert all(r["status"] == final["status"] for r, _ in results)
        assert len(fired) == 1                      # the action resumes once
        assert len([e for e in _drain(q) if e["type"] == "resolved"]) == 1
    finally:
        ap._HOOKS.get("test_feed", []).clear()


def test_a_page_that_falls_behind_is_told_to_resync():
    q = feed.subscribe()
    for i in range(feed.QUEUE_MAX + 5):
        feed.publish({"type": "pending", "approval": {"approval_id": str(i)}})
    got = _drain(q)
    # What it missed is replaced by one resync; what came after follows it.
    assert got[0] == {"type": "resync"}
    assert [e["approval"]["approval_id"] for e in got[1:]] == \
        [str(i) for i in range(feed.QUEUE_MAX + 1, feed.QUEUE_MAX + 5)]


def test_a_failing_feed_never_fails_an_approval(monkeypatch):
    monkeypatch.setattr(feed, "card_pending", lambda r: 1 / 0)
    monkeypatch.setattr(feed, "card_resolved", lambda r: 1 / 0)
    rec = _card()
    assert rec["status"] == "pending"
    assert ap.decide(rec["approval_id"], "approve")["status"] == "approved"


def test_an_unsubscribed_page_gets_nothing():
    q = feed.subscribe()
    feed.unsubscribe(q)
    _card()
    assert _drain(q) == [] and feed.subscribers() == 0
