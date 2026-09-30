"""Presence frames from the chat surface: a failed turn is an error, and an
instant command is a reflex (avatar-visual-genome.md §13.5)."""
import pytest

from agent_friday.services import approval_feed, presence


@pytest.fixture
def frames():
    approval_feed.reset()
    presence.reset()
    q = approval_feed.subscribe()

    def read(state):
        out = []
        while not q.empty():
            f = q.get_nowait()
            if f.get("type") == "presence" and f["state"] == state:
                out.append(f)
        return out
    yield read
    approval_feed.reset()
    presence.reset()


def test_a_failed_chat_turn_is_an_error_frame(client, frames, monkeypatch):
    from agent_friday.routes import chat as chat_routes

    def boom():
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(chat_routes, "chat", boom)
    client.post("/api/chat/stream", json={"message": "hi"}).get_data()
    (f,) = frames("error")
    assert f["phase"] == "once"


def test_a_turn_that_succeeds_is_not_an_error(client, frames, monkeypatch):
    from flask import jsonify
    from agent_friday.routes import chat as chat_routes
    monkeypatch.setattr(chat_routes, "chat", lambda: jsonify({"response": "ok"}))
    client.post("/api/chat/stream", json={"message": "hi"}).get_data()
    assert frames("error") == []


def test_an_instant_command_is_a_reflex(client, frames, monkeypatch):
    from agent_friday.routes import chat as chat_routes
    monkeypatch.setattr(chat_routes, "_maybe_handle_navigate_intent",
                        lambda message: ("Opening Settings.", "settings"))
    monkeypatch.setattr(chat_routes, "_persist_turn", lambda *a, **k: None)
    monkeypatch.setattr(chat_routes, "_save_chat_history", lambda *a, **k: None)
    client.post("/api/chat", json={"message": "open settings"})
    assert len(frames("reflex")) == 1
