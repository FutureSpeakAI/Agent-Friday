"""Room endpoints preserve conflicts, authentication and dispatch identity."""
import pytest


@pytest.fixture
def runtime():
    from agent_friday.services import crew_runtime
    return crew_runtime


def test_crew_room_routes_are_registered_and_authenticated(client):
    from agent_friday.routes import crew_rooms
    rules = {r.rule for r in client.application.url_map.iter_rules()}
    assert "/api/crew/rooms/<conversation_id>" in rules
    assert "/api/crew/rooms/<conversation_id>/turns" in rules
    assert hasattr(crew_rooms.crew_room, "__wrapped__")
    assert hasattr(crew_rooms.crew_turns, "__wrapped__")


def test_room_routes_forward_state_and_conflict_without_retry(client, runtime, monkeypatch):
    room = {"conversation_id": "chat", "revision": 1, "member_ids": [], "enabled": False}
    monkeypatch.setattr(runtime, "get_room", lambda cid: room)
    assert client.get("/api/crew/rooms/chat").get_json()["room"] == room
    def conflict(cid, data):
        raise runtime.CrewConflict("Refresh this room")
    monkeypatch.setattr(runtime, "update_room", conflict)
    response = client.put("/api/crew/rooms/chat", json={"revision": 0})
    assert response.status_code == 409
    assert response.get_json()["message"] == "Refresh this room"


def test_turn_dispatch_returns_accepted_id_and_history(client, runtime, monkeypatch):
    monkeypatch.setattr(runtime, "dispatch", lambda cid, data: {
        "task_id": "task", "agent_id": "agent", "request_id": data["request_id"]})
    response = client.post("/api/crew/rooms/chat/turns", json={"request_id": "request"})
    assert response.status_code == 202
    assert response.get_json() == {"status": "ok", "task_id": "task", "agent_id": "agent", "request_id": "request"}
    monkeypatch.setattr(runtime, "turns", lambda cid: {"tasks": [], "messages": [{"id": "one"}]})
    assert client.get("/api/crew/rooms/chat/turns").get_json()["messages"] == [{"id": "one"}]
