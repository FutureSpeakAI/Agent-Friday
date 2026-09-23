"""Marking a to-do done from the 3D view can be undone: /restore puts back the
status it had, and refuses anything that is not a status to go back to."""
import pytest

from agent_friday.routes import todos as rt


@pytest.fixture
def store(monkeypatch):
    box = {"t": [{"id": "a", "title": "x", "status": "proposed"}]}
    monkeypatch.setattr(rt, "_load_todos", lambda: [dict(t) for t in box["t"]])
    monkeypatch.setattr(rt, "_save_todos", lambda ts: box.__setitem__("t", [dict(t) for t in ts]))
    return box


def test_done_then_undo_returns_the_old_status(client, store):
    assert client.post("/api/todos/a/complete").status_code == 200
    assert store["t"][0]["status"] == "completed"
    r = client.post("/api/todos/a/restore", json={"status": "proposed"})
    assert r.status_code == 200 and store["t"][0]["status"] == "proposed"


def test_restore_refuses_other_statuses_and_unknown_todos(client, store):
    assert client.post("/api/todos/a/restore", json={"status": "completed"}).status_code == 400
    assert client.post("/api/todos/a/restore", json={"status": "anything"}).status_code == 400
    assert client.post("/api/todos/zz/restore", json={"status": "proposed"}).status_code == 404
    assert store["t"][0]["status"] == "proposed"
