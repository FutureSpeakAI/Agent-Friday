"""POST /api/subagents/scopes (north star §17.14-3, BD-12 slice G5): a scope
that may start subagents, or a change to one that exists, is the owner's click
in Friday's window; a built-in scope is never redefined. Synthetic only."""
from __future__ import annotations


def _screen():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def test_a_scope_that_starts_subagents_needs_a_click(client):
    body = {"name": "synthetic-lead", "max_ring": 1, "allow_subagents": True}
    assert client.post("/api/subagents/scopes", json=body).status_code == 403
    assert client.post("/api/subagents/scopes", json=body, headers=_screen()).status_code == 200


def test_an_existing_scope_changes_only_on_a_click(client):
    assert client.post("/api/subagents/scopes", json={"name": "synthetic-plain", "max_ring": 0},
                       headers=_screen()).status_code == 200
    assert client.post("/api/subagents/scopes", json={"name": "synthetic-plain", "max_ring": 2}).status_code == 403


def test_a_built_in_scope_is_never_redefined(client):
    resp = client.post("/api/subagents/scopes", json={"name": "goal-milestone", "max_ring": 2}, headers=_screen())
    assert resp.status_code == 400
