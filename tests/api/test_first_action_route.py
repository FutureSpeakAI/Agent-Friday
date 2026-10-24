"""The first governed action through the real approval card (A27, BD-09 I5).

The card is the normal one: a page without the desk press cannot approve it; the press runs it
once with a receipt; the skip is the owner's press. The reminder itself is replaced by a
recorder (it would write the test home's follow-ups). On the old tree the routes do not exist,
so these fail by assertion on the status code.
"""
from __future__ import annotations

import pytest

from tests.desk_press_support import desk_headers, page_headers


@pytest.fixture
def fresh(monkeypatch):
    from agent_friday import paths
    state = paths.friday_home() / "first_synthesis" / "first_action.json"
    state.unlink(missing_ok=True)
    from agent_friday.services import relationship_memory as rm
    monkeypatch.setattr(rm, "cold_threads", lambda now=None, days=None: [
        {"account_id": "a", "thread_id": "t", "email": "route-dana@example.org", "name": "Dana",
         "days": 4, "at": "2026-10-19T10:00:00Z"}])
    set_ = []
    monkeypatch.setattr(rm, "set_follow_up", lambda person, note="", due=None, in_days=None, now=None:
                        set_.append(person) or {"ok": True, "follow_up": {"id": "fu_r", "due": "2026-10-25"}})
    yield set_
    state.unlink(missing_ok=True)


def _propose(client):
    r = client.post("/api/onboarding/first-action/propose", json={}, headers=page_headers())
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:200])
    return r.get_json()


def test_the_card_is_approved_only_by_the_press_and_runs_once_with_a_receipt(client, fresh):
    out = _propose(client)
    assert out["state"] == "proposed" and out["approval_id"]
    aid = out["approval_id"]
    refused = client.post("/api/approvals/%s/decide" % aid,
                          json={"decision": "approve", "decided_by": "owner:ui"}, headers=page_headers())
    assert refused.status_code == 403 and fresh == []
    ok = client.post("/api/approvals/%s/decide" % aid,
                     json={"decision": "approve", "decided_by": "owner:ui"}, headers=desk_headers())
    assert ok.status_code == 200, ok.get_json()
    assert fresh == ["route-dana@example.org"]
    st = client.get("/api/onboarding/first-action", headers=page_headers()).get_json()
    assert st["state"] == "done" and isinstance(st["receipt_seq"], int)
    status = client.get("/api/onboarding/status", headers=page_headers()).get_json()
    checks = {c["id"]: c for c in status["checks"]}
    assert checks["action"]["met"] and checks["receipt"]["met"] and not checks["action"]["skipped"]
    assert status["receipt"]["seq"] == st["receipt_seq"]


def test_the_skip_is_the_owners_press_and_is_recorded(client, fresh):
    _propose(client)
    refused = client.post("/api/onboarding/answer", json={"answer": "skip_first_action"}, headers=page_headers())
    assert refused.status_code == 403
    ok = client.post("/api/onboarding/answer", json={"answer": "skip_first_action"}, headers=desk_headers())
    assert ok.status_code == 200, ok.get_json()
    checks = {c["id"]: c for c in ok.get_json()["status"]["checks"]}
    assert checks["action"]["met"] and checks["action"]["skipped"]
    st = client.get("/api/onboarding/first-action", headers=page_headers()).get_json()
    assert st["state"] == "skipped" and st["skipped_at"]
    assert fresh == []
