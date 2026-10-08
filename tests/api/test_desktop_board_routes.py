"""Authenticated, revision-checked board operations through their real handlers."""
import copy

import pytest

from agent_friday import core
from agent_friday.services import desktop_cards as cards, desktop_card_sources as sources, desktop_bus, off_record


@pytest.fixture(autouse=True)
def isolated_board(tmp_path, monkeypatch):
    monkeypatch.setattr(cards, "CARDS_PATH", tmp_path / "board.json")
    monkeypatch.setattr(off_record, "active", lambda: False)
    monkeypatch.setattr(off_record, "generation", lambda: 0)
    monkeypatch.setattr(desktop_bus, "broadcast", lambda *_args, **_kwargs: None)
    rows = {kind: [] for kind in sources.KINDS}
    rows["task"] = [sources._record("synthetic-task", "Synthetic task", "Reported status: running.", updated=100, state="running")]
    monkeypatch.setattr(sources, "_READERS", {kind: (lambda now, k=kind: (copy.deepcopy(rows[k]), 100, "Synthetic local metadata")) for kind in rows})


def test_read_and_lifecycle_roundtrip_keep_native_source_unchanged(client):
    initial = client.get("/api/desktop/board")
    assert initial.status_code == 200 and initial.get_json()["revision"] == 0
    assert not cards.CARDS_PATH.exists()
    tracked = client.post("/api/desktop/board", json={"op": "track", "expected_revision": 0,
        "source": {"kind": "task", "id": "synthetic-task"}})
    assert tracked.status_code == 200
    result = tracked.get_json()
    card = next(c for c in result["cards"] if c["origin"] == "tracked")
    assert card["tracking"]["enabled"] and card["actions"][0]["view"] == "activity"
    hidden = client.post("/api/desktop/board", json={"op": "dismiss", "expected_revision": result["revision"], "id": card["id"]})
    assert hidden.status_code == 200 and hidden.get_json()["hidden_cards"][0]["tracking"]["enabled"]
    stopped = client.post("/api/desktop/board", json={"op": "stop_tracking", "expected_revision": hidden.get_json()["revision"], "id": card["id"]})
    assert stopped.status_code == 200 and not stopped.get_json()["hidden_cards"][0]["tracking"]["enabled"]
    assert stopped.get_json()["hidden_cards"][0]["body"] == "Reported status: running."
    assert client.get("/api/desktop/cards").get_json()["cards"] == []


def test_legacy_save_invalidates_pending_board_revision(client):
    before = client.get("/api/desktop/board").get_json()["revision"]
    assert client.post("/api/desktop/cards", json={"id": "note", "title": "Saved elsewhere"}).status_code == 200
    conflict = client.post("/api/desktop/board", json={"op": "save", "expected_revision": before,
        "card": {"id": "note", "title": "Stale overwrite"}})
    assert conflict.status_code == 409 and conflict.get_json()["code"] == "board_changed"
    assert cards.list_cards()[0]["title"] == "Saved elsewhere"


@pytest.mark.parametrize("payload", [None, [], {}, {"op": {}}, {"op": "track", "expected_revision": 0,
    "source": {"kind": "url", "id": "https://example.test"}}, {"op": "save", "expected_revision": 0,
    "card": {"id": "note", "title": "No action execution", "actions": [{"label": "Run", "script": "run()"}]}}])
def test_invalid_board_shapes_and_external_actions_are_refused(client, payload):
    response = client.post("/api/desktop/board", json=payload)
    assert response.status_code == 400 and response.get_json()["status"] == "error"
    assert not cards.CARDS_PATH.exists()


def test_failed_write_does_not_claim_success_or_expose_disk_details(client, monkeypatch):
    def fail(*_args):
        raise OSError("synthetic private disk detail")
    monkeypatch.setattr(cards, "_write", fail)
    response = client.post("/api/desktop/board", json={"op": "save", "expected_revision": 0,
        "card": {"id": "note", "title": "A note"}})
    assert response.status_code == 500 and response.get_json()["status"] == "error"
    assert "private disk" not in response.get_data(as_text=True)


@pytest.mark.parametrize("method", ["get", "post"])
def test_board_requires_owner_authentication(client, method):
    response = getattr(client, method)("/api/desktop/board", json={} if method == "post" else None,
        environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert response.status_code in (401, 403)


def test_off_record_refuses_durable_post_and_hides_personal_sources(client, monkeypatch):
    monkeypatch.setattr(off_record, "active", lambda: True)
    result = client.get("/api/desktop/board").get_json()
    assert result["summary"]["private"] and result["cards"] == []
    assert all(source["options"] == [] for source in result["sources"])
    refused = client.post("/api/desktop/board", json={"op": "save", "expected_revision": 0,
        "card": {"id": "note", "title": "No durable private card"}})
    assert refused.status_code == 400 and not cards.CARDS_PATH.exists()


@pytest.mark.parametrize("route", ["board", "cards"])
def test_card_post_keeps_pre_body_authority_across_a_complete_privacy_cycle(client, monkeypatch, route):
    cards.upsert_card({"id": "note", "title": "Keep original"})
    before = cards.CARDS_PATH.read_bytes()
    state = {"private": False, "generation": 0}
    parsed, events = [], []
    monkeypatch.setattr(off_record, "active", lambda: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    monkeypatch.setattr(desktop_bus, "broadcast", lambda *args, **kwargs: events.append(args))
    request_class = client.application.request_class
    original = request_class.get_json
    url = "/api/desktop/" + route

    def delayed_json(request, *args, **kwargs):
        body = original(request, *args, **kwargs)
        if request.path == url:
            with core._SETTINGS_WRITE_LOCK:
                state["private"] = True
                state["generation"] += 1
                state["private"] = False
            parsed.append(True)
        return body

    monkeypatch.setattr(request_class, "get_json", delayed_json)
    card = {"id": "note", "title": "Must not replace original"}
    payload = card if route == "cards" else {"op": "save", "expected_revision": 1, "card": card}
    response = client.post(url, json=payload)
    assert parsed == [True] and state["generation"] == 1
    assert response.status_code == 400 and "privacy session changed" in response.get_json()["message"]
    assert cards.CARDS_PATH.read_bytes() == before and events == []


@pytest.mark.parametrize("route", ["board", "cards"])
def test_private_card_post_is_refused_before_reading_the_body(client, monkeypatch, route):
    monkeypatch.setattr(off_record, "active", lambda: True)
    parsed = []
    request_class = client.application.request_class
    original = request_class.get_json

    def counted_json(request, *args, **kwargs):
        parsed.append(request.path)
        return original(request, *args, **kwargs)

    monkeypatch.setattr(request_class, "get_json", counted_json)
    card = {"id": "note", "title": "Private note"}
    payload = card if route == "cards" else {"op": "save", "expected_revision": 0, "card": card}
    response = client.post("/api/desktop/" + route, json=payload)
    assert response.status_code == 400 and "Off the record" in response.get_json()["message"]
    assert parsed == [] and not cards.CARDS_PATH.exists()
