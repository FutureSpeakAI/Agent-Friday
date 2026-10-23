"""Routes for 'What I see so far' and the onboarding status (routes/first_synthesis).

A mark, and each of the owner's onboarding answers, is the owner's press on this PC: without the
desk proof it is refused. On the old tree the routes do not exist, so these fail by assertion on
the status code.
"""
from __future__ import annotations

from tests.desk_press_support import desk_headers, page_headers


def _with_an_item(monkeypatch):
    from agent_friday.services import relationship_memory as rm
    monkeypatch.setattr(rm, "list_follow_ups", lambda status="open": [
        {"id": "route-f1", "person": "Route Person", "note": "call back", "status": "open"}])


def _briefing(client):
    r = client.get("/api/first-synthesis", headers=page_headers())
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:200])
    return r.get_json()


def test_the_briefing_is_served_with_its_marks(client, monkeypatch):
    _with_an_item(monkeypatch)
    d = _briefing(client)
    assert d["ok"] and len(d["items"]) <= 5 and "remember" in d["marks"]
    item = next(i for i in d["items"] if i["kind"] == "follow_up")
    assert item["remember_text"] == "I mean to follow up with Route Person." and "remember" in item["marks"]


def test_a_mark_needs_the_press_on_this_pc_and_the_text_that_was_shown(client, monkeypatch):
    _with_an_item(monkeypatch)
    item = next(i for i in _briefing(client)["items"] if i["kind"] == "follow_up")
    body = {"id": item["id"], "mark": "useful", "observation": item["observation"]}
    refused = client.post("/api/first-synthesis/mark", json=body, headers=page_headers())
    assert refused.status_code == 403 and refused.get_json()["code"] == "desk_proof_required"
    stale = client.post("/api/first-synthesis/mark", json=dict(body, observation="something else"),
                        headers=desk_headers())
    assert stale.status_code == 400
    ok = client.post("/api/first-synthesis/mark", json=body, headers=desk_headers())
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["ok"] is True


def test_the_onboarding_status_is_not_setup_complete(client):
    r = client.get("/api/onboarding/status", headers=page_headers())
    assert r.status_code == 200, r.status_code
    d = r.get_json()
    assert [c["id"] for c in d["checks"]] == ["reviewed", "action", "receipt", "where_ran", "memory", "goal"]
    assert "setup_complete" in d and isinstance(d["done"], bool)


def test_skipping_the_goal_is_the_owners_press(client):
    from agent_friday import paths
    p = paths.friday_home() / "first_synthesis" / "onboarding.json"
    before = p.read_text(encoding="utf-8") if p.exists() else None
    try:
        refused = client.post("/api/onboarding/answer", json={"answer": "skip_goal"}, headers=page_headers())
        assert refused.status_code == 403
        ok = client.post("/api/onboarding/answer", json={"answer": "skip_goal"}, headers=desk_headers())
        assert ok.status_code == 200, ok.get_json()
        goal = next(c for c in ok.get_json()["status"]["checks"] if c["id"] == "goal")
        assert goal["met"] is True
        assert client.post("/api/onboarding/answer", json={"answer": "done_everything"},
                           headers=desk_headers()).status_code == 400
    finally:
        if before is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(before, encoding="utf-8")

