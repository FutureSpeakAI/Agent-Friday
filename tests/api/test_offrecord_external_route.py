"""GET /api/off-record/external: what off the record does not cover, for the
owner at this PC only (NS-15.8-3). Synthetic connection list only."""
from __future__ import annotations

from types import SimpleNamespace

ROUTE = "/api/off-record/external"


def _isolate(monkeypatch):
    from agent_friday.services import off_record_external as ext
    monkeypatch.setattr(ext, "_items", lambda: [
        {"id": "google", "group": "google", "label": "Google", "status": "connected", "connect": {}},
        {"id": "twilio", "group": "phone", "label": "Phone (Twilio)", "status": "not_connected", "connect": {}},
    ])
    monkeypatch.setattr(ext, "_settings", lambda: {"off_record": True,
                                                   "model_routing": {"mode": "local_only"}})
    return ext


def test_the_local_owner_reads_the_lines(client, monkeypatch):
    ext = _isolate(monkeypatch)
    resp = client.get(ROUTE)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and body["on"] is True
    assert body["lines"][-1] == ext.ALWAYS
    assert body["short"].startswith("Still recorded elsewhere: Gmail, Google Calendar")
    assert "Twilio" not in " ".join(body["lines"])


def test_a_request_from_another_computer_is_refused(client, monkeypatch):
    _isolate(monkeypatch)
    assert client.get(ROUTE).status_code == 200
    import agent_friday.core as core
    monkeypatch.setattr(core, "_is_local_request", lambda: False)
    assert client.get(ROUTE).status_code == 403


def test_a_cross_site_page_cannot_read_it(client, monkeypatch):
    _isolate(monkeypatch)
    assert client.get(ROUTE, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_the_read_only_observer_cannot_read_it(client, monkeypatch):
    _isolate(monkeypatch)
    assert client.get(ROUTE).status_code == 200
    from agent_friday.routes import privacy_map as route
    monkeypatch.setattr(route, "g", SimpleNamespace(friday_principal="observer"))
    assert client.get(ROUTE).status_code == 403
