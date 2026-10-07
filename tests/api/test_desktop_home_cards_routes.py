"""Authenticated Home card CRUD with truthful persistence failures."""
import pytest

from agent_friday.services import desktop_cards, off_record


@pytest.fixture(autouse=True)
def isolated_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_cards, "CARDS_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(off_record, "active", lambda: False)


def test_cards_roundtrip_through_the_real_routes(client):
    card = {"id": "quick-library", "title": "Review your notes", "body": "Choose a passage to discuss.",
            "actions": [{"label": "Open Library", "workspace": "library"}]}
    response = client.post("/api/desktop/cards", json=card)
    assert response.status_code == 200
    assert response.get_json()["card"]["id"] == card["id"]
    assert client.get("/api/desktop/cards").get_json()["cards"][0]["title"] == card["title"]
    assert client.delete("/api/desktop/cards/quick-library").get_json() == {"status": "ok", "removed": True}
    assert client.get("/api/desktop/cards").get_json()["cards"] == []


@pytest.mark.parametrize("body", [None, [], {"id": "bad", "title": "Bad", "actions": [{"label": "Open", "url": "https://example.com"}]}])
def test_bad_shapes_are_refused_without_mutation(client, body):
    assert client.post("/api/desktop/cards", json=body).status_code == 400
    assert not desktop_cards.CARDS_PATH.exists()


def test_write_failure_reports_failure_and_no_internal_path(client, monkeypatch):
    def fail(*args):
        raise OSError("private path detail must stay in log")
    monkeypatch.setattr(desktop_cards, "_write", fail)
    response = client.post("/api/desktop/cards", json={"id": "save", "title": "A card"})
    assert response.status_code == 500
    assert response.get_json()["status"] == "error"
    assert "private path" not in response.get_data(as_text=True)


@pytest.mark.parametrize("method,path", [("get", "/api/desktop/cards"), ("post", "/api/desktop/cards"), ("delete", "/api/desktop/cards/example")])
def test_cards_require_owner_authentication(client, method, path):
    response = getattr(client, method)(path, json={} if method == "post" else None,
                                      environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert response.status_code in (401, 403)
