"""GET /api/models/slate returns the slate the remote devices read."""
from __future__ import annotations


def test_the_slate_route_serves_the_slate(client, monkeypatch):
    from agent_friday.services import model_slate
    monkeypatch.setattr(model_slate, "slate", lambda **k: {"models": [{"id": "m", "can_serve": True}],
                                                           "selected": "m", "routing_mode": None})
    resp = client.get("/api/models/slate")
    data = resp.get_json()
    assert resp.status_code == 200 and data["status"] == "ok"
    assert data["models"][0]["id"] == "m" and data["selected"] == "m"
