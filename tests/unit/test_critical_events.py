"""Critical events beyond the tray are on disk before anyone is told (BD-16 C9; NS-31.7-1).

C6 put new owner-facing tray cards in the canonical event log before they are saved. This adds
a decided, expired or withdrawn approval (before any page or tray hears of it), Stop everything
and its resume (right after their receipts, before the lanes are walked or the owner is answered),
and a grant created or revoked. Ids and kinds only; never the owner's words.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def ev(tmp_path, monkeypatch):
    from agent_friday.services import events
    monkeypatch.setattr(events, "_dir", lambda: tmp_path / "events")
    monkeypatch.setattr(events, "_key", lambda: b"test-governance-key-not-real")
    return events


def _types(ev):
    return [e["type"] for e in ev.replay()]


def test_a_resolved_approval_is_recorded_before_any_page_hears_of_it(ev, monkeypatch, tmp_path):
    from agent_friday.services import approval_feed, approvals as ap
    seen = []
    monkeypatch.setattr(approval_feed, "card_resolved",
                        lambda rec: seen.append([e["related_ids"].get("approval") for e in ev.replay()
                                                 if e["type"] == "approval.resolved"]))
    ap._feed("card_resolved", {"approval_id": "appr_9", "status": "denied", "decided_by": "owner",
                               "title": "Pay Dana's landlord"})
    assert seen == [["appr_9"]], "a page heard of the decision before it was recorded"
    [e] = [x for x in ev.replay() if x["type"] == "approval.resolved"]
    assert e["payload"]["status"] == "denied" and "landlord" not in json.dumps(e)


def test_stop_everything_and_its_resume_are_recorded(ev, monkeypatch):
    from agent_friday.services import global_stop
    monkeypatch.setattr(global_stop, "_walk_lanes", lambda gen: {})
    global_stop._reset_for_tests()
    try:
        global_stop.stop(by="owner", source="button")
        assert "safety.stop" in _types(ev)
        global_stop.resume(by="owner", source="button")
    finally:
        global_stop._reset_for_tests()
    assert _types(ev).count("safety.stop") == 1 and "safety.resume" in _types(ev)


def test_a_grant_created_and_revoked_is_recorded(ev, monkeypatch, tmp_path):
    from agent_friday.governance import action_gate as ag
    monkeypatch.setattr(ag, "_grants_file", lambda: tmp_path / "grants.json")
    g = ag.create_grant(tools=["send_email"], scope="sched_1", expires_in_seconds=600, max_uses=2)
    ag.revoke_grant(g["grant_id"])
    types = _types(ev)
    assert "grant.created" in types and "grant.revoked" in types
    [c] = [e for e in ev.replay() if e["type"] == "grant.created"]
    assert c["related_ids"]["grant"] == g["grant_id"] and c["payload"]["tools"] == ["send_email"]


def test_a_second_stop_is_not_recorded_again_and_a_failed_record_never_holds_stop(ev, monkeypatch):
    from agent_friday.services import global_stop
    monkeypatch.setattr(global_stop, "_walk_lanes", lambda gen: {})
    global_stop._reset_for_tests()
    try:
        global_stop.stop(by="owner", source="button")
        global_stop.stop(by="owner", source="button")
        assert _types(ev).count("safety.stop") == 1

        def broken(*a, **k):
            raise ev.EventNotPersisted("disk full")
        monkeypatch.setattr(ev, "persist", broken)
        global_stop.resume(by="owner", source="button")
        assert not global_stop.active(), "a failed event record held the resume"
        out = global_stop.stop(by="owner", source="button")
        assert out.get("stopped") and global_stop.active(), "a failed event record held the stop"
    finally:
        global_stop._reset_for_tests()


def test_a_card_raised_off_the_record_leaves_no_event_when_it_resolves(ev):
    from agent_friday.services import approvals as ap
    ap._feed("card_resolved", {"approval_id": "appr_off", "status": "expired", "off_record": True})
    assert "approval.resolved" not in _types(ev)
