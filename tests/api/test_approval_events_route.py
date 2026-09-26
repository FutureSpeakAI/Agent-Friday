"""The approvals feed over HTTP: what a page receives, and who wins a race.

Two clients deciding the same card at the same instant stand in for two open
tabs: exactly one of them is told it decided, the other is told the card was
already decided, and the action behind the card resumes once.
"""
from __future__ import annotations

import json
import threading

import pytest

from agent_friday.services import approval_feed as feed
from agent_friday.services import approvals as ap

_REMOTE = {"REMOTE_ADDR": "203.0.113.5"}      # TEST-NET-3, never loopback


@pytest.fixture(autouse=True)
def clean(friday_dir):
    if ap.APPROVALS_FILE.exists():
        ap.APPROVALS_FILE.unlink()
    feed.reset()
    yield
    feed.reset()


def _card(n=1):
    return ap.create_approval(kind="test_route", subject_type="test", subject_id="r%d" % n,
                              title="Post the summary to Bluesky",
                              action_description="post_social summary", force_gate=True)


def _frames(resp, n):
    """The first `n` data frames of an SSE response, then close it."""
    out, buf = [], ""
    it = resp.response
    try:
        for chunk in it:
            buf += chunk.decode() if isinstance(chunk, bytes) else chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                if frame.startswith("data: "):
                    out.append(json.loads(frame[6:]))
                    if len(out) >= n:
                        return out
    finally:
        resp.close()
    return out


def test_two_tabs_racing_exactly_one_decides(app):
    rec = _card()
    fired = []
    ap.register_decision_hook("test_route", fired.append)
    try:
        start = threading.Barrier(2)
        answers = {}

        def tab(name, decision):
            client = app.test_client()
            start.wait()
            r = client.post("/api/approvals/%s/decide" % rec["approval_id"],
                            json={"decision": decision, "decided_by": name})
            answers[name] = (r.status_code, r.get_json())

        ts = [threading.Thread(target=tab, args=("tab-a", "approve")),
              threading.Thread(target=tab, args=("tab-b", "deny"))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert {c for c, _ in answers.values()} == {200}
        won = [n for n, (_, b) in answers.items() if b["won"]]
        lost = [n for n, (_, b) in answers.items() if b["already_decided"]]
        assert len(won) == 1 and len(lost) == 1 and won != lost
        final = ap.get_approval(rec["approval_id"])
        assert final["decided_by"] == won[0]
        assert answers[lost[0]][1]["approval"]["status"] == final["status"]
        assert len(fired) == 1
    finally:
        ap._HOOKS.get("test_route", []).clear()


def test_a_page_that_opens_later_gets_the_waiting_cards_first(client):
    a, b = _card(1), _card(2)
    ap.decide(b["approval_id"], "deny")
    resp = client.get("/api/approvals/events", buffered=False)
    assert resp.mimetype == "text/event-stream"
    (snap,) = _frames(resp, 1)
    assert snap["type"] == "snapshot"
    assert [c["approval_id"] for c in snap["pending"]] == [a["approval_id"]]
    assert snap["pending"][0] == json.loads(json.dumps(ap.get_approval(a["approval_id"])))


def test_a_connected_page_hears_new_and_decided_cards(client):
    resp = client.get("/api/approvals/events", buffered=False)
    it = iter(resp.response)
    first = next(it)                       # the snapshot, sent on connect
    assert b"snapshot" in (first if isinstance(first, bytes) else first.encode())
    rec = _card()
    ap.decide(rec["approval_id"], "approve")
    got = []
    for chunk in it:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        if text.startswith("data: "):
            got.append(json.loads(text[6:].strip()))
        if len(got) == 2:
            break
    resp.close()
    assert [e["type"] for e in got] == ["pending", "resolved"]
    assert got[1]["approval_id"] == rec["approval_id"] and got[1]["status"] == "approved"
    assert feed.subscribers() == 0          # closing the page unsubscribes it


def test_a_quiet_stream_sends_a_heartbeat_the_tabs_can_see(client, monkeypatch):
    # A data frame, not an SSE comment: the tabs sharing one stream use it to
    # tell a live holder from a frozen one.
    from agent_friday.routes import goals as goals_routes
    monkeypatch.setattr(goals_routes, "BEAT_S", 0.05)
    snap, beat = _frames(client.get("/api/approvals/events", buffered=False), 2)
    assert snap["type"] == "snapshot" and beat == {"type": "beat"}


def test_the_feed_is_authenticated_like_any_other_request(app):
    client = app.test_client()
    r = client.get("/api/approvals/events", environ_base=_REMOTE)
    assert r.status_code in (401, 403)
    assert feed.subscribers() == 0


def test_a_single_decision_still_says_it_won(client):
    rec = _card()
    body = client.post("/api/approvals/%s/decide" % rec["approval_id"],
                       json={"decision": "approve"}).get_json()
    assert body["ok"] and body["won"] and not body["already_decided"]
    again = client.post("/api/approvals/%s/decide" % rec["approval_id"],
                        json={"decision": "deny"}).get_json()
    assert again["ok"] and again["already_decided"] and again["approval"]["status"] == "approved"
