"""BD-11a / NS-31.1-4, NS-31.4-1: a client-supplied Idempotency-Key makes the same request run once.

Before: a retried POST (a page that lost its connection, a script that resent) ran again, and
an action ran twice. Now the first request with a key runs; the same key and request again gets
the kept answer (Idempotent-Replayed: true) without running anything; the same key for a different
request is refused (422); a failed first attempt is not kept, so a retry runs; keys are scoped to
the caller. Uses /api/messages/learn (a small state change) with its store replaced by a counter.
Synthetic senders only.
"""
from __future__ import annotations

import pytest

from agent_friday.services import message_triage

BODY = {"sender": "news@example.test", "lane": "career"}


@pytest.fixture
def runs(monkeypatch):
    calls = []
    monkeypatch.setattr(message_triage, "record_signal",
                        lambda sender, lane: calls.append((sender, lane)) or {"count": len(calls)})
    try:
        from agent_friday.services import idempotency
        idempotency.reset_for_tests()
    except ImportError:
        pass
    return calls


def test_the_same_key_twice_runs_once(client, runs):
    h = {"Idempotency-Key": "retry-0001-abcdef"}
    r1 = client.post("/api/messages/learn", json=BODY, headers=h)
    r2 = client.post("/api/messages/learn", json=BODY, headers=h)
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(runs) == 1, "the retried request ran again"
    assert r2.get_json() == r1.get_json()
    assert r2.headers.get("Idempotent-Replayed") == "true"
    assert r1.headers.get("Idempotent-Replayed") is None


def test_without_a_key_every_request_runs(client, runs):
    client.post("/api/messages/learn", json=BODY)
    client.post("/api/messages/learn", json=BODY)
    assert len(runs) == 2


def test_a_key_names_one_request(client, runs):
    h = {"Idempotency-Key": "retry-0002-abcdef"}
    client.post("/api/messages/learn", json=BODY, headers=h)
    r = client.post("/api/messages/learn", json=dict(BODY, lane="family"), headers=h)
    assert r.status_code == 422 and r.get_json()["code"] == "idempotency_key_reused"
    assert len(runs) == 1


def test_a_failed_attempt_is_not_kept_so_a_retry_runs(client, runs, monkeypatch):
    h = {"Idempotency-Key": "retry-0003-abcdef"}
    bad = client.post("/api/messages/learn", json={"sender": "x@example.test", "lane": "nope"}, headers=h)
    assert bad.status_code == 400
    again = client.post("/api/messages/learn", json={"sender": "x@example.test", "lane": "nope"}, headers=h)
    assert again.status_code == 400 and again.headers.get("Idempotent-Replayed") is None
    ok = client.post("/api/messages/learn", json=BODY, headers={"Idempotency-Key": "retry-0004-abcdef"})
    assert ok.status_code == 200 and len(runs) == 1


def test_a_malformed_key_runs_nothing(client, runs):
    r = client.post("/api/messages/learn", json=BODY, headers={"Idempotency-Key": "bad key with spaces"})
    assert r.status_code == 400 and r.get_json()["code"] == "bad_idempotency_key"
    assert runs == []


def test_reads_are_not_affected(client, runs):
    r = client.get("/api/health", headers={"Idempotency-Key": "read-0001-abcdef"})
    assert r.status_code == 200 and r.headers.get("Idempotent-Replayed") is None


def test_a_route_that_reads_its_own_body_still_sees_it_after_the_key_check():
    """The idempotency check reads the body first (to fingerprint it); a route that read the raw
    stream itself (the browser workspaces) then saw nothing and refused the request."""
    import agent_friday.core as core
    from agent_friday.routes import agent_workspaces
    with core.app.test_request_context("/api/browser/workspaces/x", method="POST",
                                       json={"workspace_id": "w1"}):
        from flask import request
        request.get_data(cache=True)                       # what the key check does
        assert agent_workspaces._body(("workspace_id",)) == {"workspace_id": "w1"}


def test_a_key_is_scoped_to_its_caller():
    from agent_friday.services import idempotency as idem
    idem.reset_for_tests()
    fp = idem.fingerprint("POST", "/api/x", b"{}")
    assert idem.begin("shared-key-0001", "user", "token-a", fp)[0] == "run"
    assert idem.begin("shared-key-0001", "user", "token-b", fp)[0] == "run", "another caller replayed"
    assert idem.begin("shared-key-0001", "user", "token-a", fp)[0] == "running"
    idem.reset_for_tests()
