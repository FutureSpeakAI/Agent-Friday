"""GET /api/voices/slate returns the voice slate the remote devices read."""
from __future__ import annotations


def test_the_voice_slate_route_serves_the_slate(client, monkeypatch):
    from agent_friday.services import voice_slate
    monkeypatch.setattr(voice_slate, "slate", lambda: {"engines": [{"id": "local", "can_serve": True}],
                                                       "selected": "local", "routing_mode": None})
    resp = client.get("/api/voices/slate")
    data = resp.get_json()
    assert resp.status_code == 200 and data["status"] == "ok"
    assert data["engines"][0]["id"] == "local" and data["selected"] == "local"
