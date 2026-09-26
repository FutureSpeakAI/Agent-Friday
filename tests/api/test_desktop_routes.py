"""The desktop command channel and the situation over HTTP (routes/desktop.py).

End to end through the real routes: a page opens the event stream, reports
itself, receives a command, answers it, and the caller of /api/desktop/open is
told what the page confirmed.
"""
import json
import threading
import time

import pytest

from agent_friday.services import desktop_bus


@pytest.fixture(autouse=True)
def _clean():
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def _events(resp):
    """Parsed `data:` events from a streaming SSE response, as they arrive."""
    for chunk in resp.response:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in text.split("\n"):
            if line.startswith("data: "):
                yield json.loads(line[6:])


def test_the_stream_needs_a_client_id(client):
    assert client.get("/api/desktop/events").status_code == 400
    assert client.get("/api/desktop/events?client=../x").status_code == 400


def test_a_page_is_greeted_then_sent_a_command_and_its_answer_is_reported(client):
    resp = client.get("/api/desktop/events?client=desk-test-1&kind=desktop", buffered=False)
    assert resp.status_code == 200 and resp.mimetype == "text/event-stream"
    events = _events(resp)
    assert next(events) == {"type": "hello"}
    r = client.post("/api/desktop/state", json={"client": "desk-test-1", "state": {
        "kind": "desktop", "visible": True, "focused": True,
        "open": [{"workspace": "calendar", "label": "Calendar"}]}})
    assert r.get_json() == {"status": "ok", "want_manifest": True}

    result = {}

    def open_it():
        result["r"] = client.post("/api/desktop/open", json={
            "kind": "calendar", "query": "2026-10-02"}).get_json()

    th = threading.Thread(target=open_it, daemon=True)
    th.start()
    cmd = next(events)
    assert cmd["type"] == "command"
    assert cmd["actions"] == [{"type": "navigate", "workspace": "calendar", "date": "2026-10-02"}]
    assert cmd["verify"] == {"workspace": "calendar", "key": "date", "value": "2026-10-02"}
    ack = client.post("/api/desktop/ack", json={"id": cmd["id"], "result": {
        "opened": True, "matched": True, "label": "Calendar", "shown": "date=2026-10-02"}})
    assert ack.get_json() == {"status": "ok"}
    th.join(5)
    assert result["r"]["status"] == "opened"
    assert result["r"]["text"].startswith("NAV_OK:calendar — opened the calendar for Friday 02 October")
    assert result["r"]["target"] == {"workspace": "calendar", "date": "2026-10-02"}
    resp.close()
    assert desktop_bus.pick_client() is None, "a closed stream must not be sent commands"


def test_opening_with_no_page_says_so(client):
    r = client.post("/api/desktop/open", json={"kind": "calendar", "query": "tomorrow"}).get_json()
    assert r["status"] == "no_desktop"
    assert r["text"].startswith("NAV_FAIL: no Friday desktop page is open to show it in")


def test_an_unknown_answer_is_refused(client):
    assert client.post("/api/desktop/ack", json={"id": "nope"}).get_json() == {"status": "unknown"}


def test_the_manifest_and_state_are_read_back(client):
    m = {"workspaces": {"news": {"label": "News", "sections": [{"id": "feed", "label": "Feed"}]}}}
    r = client.post("/api/desktop/state", json={"client": "desk-test-2", "state": {
        "kind": "desktop", "manifest": m, "focused_window": {"workspace": "news", "label": "News"}}})
    assert r.get_json()["want_manifest"] is False
    got = client.get("/api/desktop/state").get_json()
    assert got["manifest"] == m
    assert got["desktop"]["focused"] == {"workspace": "news", "label": "News"}
    assert client.post("/api/desktop/state", json={"state": {}}).status_code == 400


def test_the_situation_answers_fast_in_both_forms(client):
    t0 = time.time()
    full = client.get("/api/situation").get_json()
    brief = client.get("/api/situation?format=brief").get_json()
    assert time.time() - t0 < 5
    assert set(full["situation"]) >= {"desktop", "machine", "models", "activity", "spend", "took_ms"}
    assert brief["brief"].startswith("Situation at ") and "Machine:" in brief["brief"]
    assert brief["took_ms"] < 1000


@pytest.mark.parametrize("method,path", [
    ("get", "/api/desktop/events?client=abcd1234"), ("post", "/api/desktop/state"),
    ("post", "/api/desktop/ack"), ("get", "/api/desktop/state"),
    ("post", "/api/desktop/open"), ("get", "/api/situation")])
def test_every_route_needs_the_owner(client, method, path):
    r = getattr(client, method)(path, json={} if method == "post" else None,
                                environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code in (401, 403), (path, r.status_code)
