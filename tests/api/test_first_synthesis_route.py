"""Routes for 'What I see so far' and the onboarding status (routes/first_synthesis, core_routes).

A mark is the owner's press on this PC: without the desk proof it is refused. On the old tree
the routes do not exist, so these fail by assertion on the status code.
"""
from __future__ import annotations

from tests.desk_press_support import desk_headers, page_headers


def _with_an_item(monkeypatch):
    from agent_friday.services import relationship_memory as rm
    monkeypatch.setattr(rm, "list_follow_ups", lambda status="open": [
        {"id": "route-f1", "person": "Route Person", "note": "call back", "status": "open"}])


def test_the_briefing_is_served_with_its_marks(client, monkeypatch):
    _with_an_item(monkeypatch)
    r = client.get("/api/first-synthesis", headers=page_headers())
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["ok"] and len(d["items"]) <= 5 and "remember" in d["marks"]
    assert any(i["kind"] == "follow_up" for i in d["items"])


def test_a_mark_needs_the_press_on_this_pc(client, monkeypatch):
    _with_an_item(monkeypatch)
    items = client.get("/api/first-synthesis", headers=page_headers()).get_json()["items"]
    item = next(i for i in items if i["kind"] == "follow_up")
    refused = client.post("/api/first-synthesis/mark", json={"id": item["id"], "mark": "useful"},
                          headers=page_headers())
    assert refused.status_code == 403 and refused.get_json()["code"] == "desk_proof_required"
    ok = client.post("/api/first-synthesis/mark", json={"id": item["id"], "mark": "useful"},
                     headers=desk_headers())
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["ok"] is True


def test_the_onboarding_status_is_not_setup_complete(client):
    r = client.get("/api/onboarding/status", headers=page_headers())
    assert r.status_code == 200
    d = r.get_json()
    assert set(c["id"] for c in d["checks"]) == {"reviewed", "action", "receipt", "where_ran", "memory", "goal"}
    assert "setup_complete" in d and isinstance(d["done"], bool)


def test_the_goal_skip_and_where_it_ran_are_acks(client):
    import agent_friday.core as core
    from pathlib import Path
    path = Path(core.FRIDAY_DIR) / "onboarding.json"
    before = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        r = client.post("/api/onboarding/acks", json={"goal_skipped": True, "action_place_ack": True},
                        headers=page_headers())
        assert r.status_code == 200
        acks = r.get_json()["acks"]
        assert acks["goal_skipped"] is True and acks["action_place_ack"] is True
    finally:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(before, encoding="utf-8")
