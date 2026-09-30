"""Interactive model choices do not wait for catalogue or process surveys."""
import threading
import time

import pytest
from flask import Flask

from agent_friday.routes import intelligence, conversations
from agent_friday.services import model_catalog, swr_cache


@pytest.fixture(autouse=True)
def clear_snapshots():
    swr_cache.invalidate("intelligence:picker")
    swr_cache.invalidate("conversations:live-seats")
    yield
    swr_cache.invalidate("intelligence:picker")
    swr_cache.invalidate("conversations:live-seats")


def test_picker_answers_while_catalogue_is_still_loading(monkeypatch):
    started, release, finished = (threading.Event() for _ in range(3))
    calls = []

    def slow_catalogue(**_kwargs):
        calls.append(1)
        started.set()
        try:
            release.wait(2)
            return {"models": []}
        finally:
            finished.set()

    monkeypatch.setattr(model_catalog, "build_catalog", slow_catalogue)
    app = Flask(__name__)
    app.register_blueprint(intelligence.intelligence_bp)
    client = app.test_client()
    try:
        before = time.monotonic()
        first = client.get("/api/intelligence?view=picker")
        elapsed = time.monotonic() - before
        assert elapsed < 0.75, "opening a picker waited for catalogue discovery"
        assert started.is_set()
        assert first.get_json()["catalog_reading"] == "unknown"
        for _ in range(3):
            assert client.get("/api/intelligence?view=picker").status_code == 200
        assert len(calls) == 1, "polls must share the background discovery"
    finally:
        release.set()
        finished.wait(3)


def test_binding_does_not_wait_for_a_process_survey(monkeypatch):
    from agent_friday.services import local_seats, residency_arbiter

    release, finished = threading.Event(), threading.Event()

    def slow_survey():
        try:
            release.wait(2)
            return {}
        finally:
            finished.set()

    monkeypatch.setattr(local_seats, "_is_local_name", lambda _: True)
    monkeypatch.setattr(residency_arbiter, "survey_live_seats", slow_survey)
    try:
        before = time.monotonic()
        reason = conversations._why_this_seat_cannot_be_bound({"model": "test:local"})
        elapsed = time.monotonic() - before
        assert elapsed < 0.75, "saving a selection waited for a process survey"
        assert "still checking" in reason
    finally:
        release.set()
        finished.wait(3)


def test_picker_deduplicates_providers_without_hardware_planning(monkeypatch):
    monkeypatch.setattr(model_catalog, "build_catalog", lambda **_: {"models": [
        {"id": "model-a", "provider": "first", "local": False,
         "available": False, "roles": ["orchestrator"], "modalities": ["text"]},
        {"id": "model-a", "provider": "second", "local": False,
         "available": True, "roles": ["subagent"], "modalities": ["tools"]},
        {"id": "model-b", "provider": "local", "local": True,
         "available": True, "roles": ["orchestrator"]},
    ]})
    data = intelligence._model_picker_payload()
    by_id = {m["id"]: m for m in data["models"]}
    assert len(by_id) == 2
    assert by_id["model-a"]["providers"] == ["first", "second"]
    assert by_id["model-a"]["available"] is True
    assert by_id["model-a"]["modalities"] == ["text", "tools"]
    assert by_id["model-b"]["state"] == "unknown"


def test_a_delayed_old_selection_cannot_overwrite_the_newer_one(tmp_path, monkeypatch):
    from agent_friday.services import conversations as store

    monkeypatch.setattr(store, "_root", lambda: tmp_path / "conversations")
    cid = store.create(title="Model switching test")["id"]
    app = Flask(__name__)
    app.register_blueprint(conversations.conversations_bp)
    entered, release = threading.Event(), threading.Event()
    results = []

    def validate(seat):
        if seat["model"] == "first":
            entered.set()
            release.wait(2)
        return None

    monkeypatch.setattr(conversations, "_why_this_seat_cannot_be_bound", validate)

    def first():
        with app.test_client() as client:
            results.append(client.patch("/api/conversations/" + cid,
                                        json={"seat": {"model": "first"}}).get_json())

    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert entered.wait(1)
        with app.test_client() as client:
            latest = client.patch("/api/conversations/" + cid,
                                  json={"seat": {"model": "second"}})
        assert latest.get_json()["conversation"]["seat"]["model"] == "second"
    finally:
        release.set()
        worker.join(3)
    assert store.load(cid)["seat"]["model"] == "second"
    assert results[0].get("superseded") is True


def test_picker_reports_discovery_failure(monkeypatch):
    def broken():
        raise RuntimeError("fixture discovery failed")

    monkeypatch.setattr(intelligence, "_model_picker_payload", broken)
    app = Flask(__name__)
    app.register_blueprint(intelligence.intelligence_bp)
    data = app.test_client().get("/api/intelligence?view=picker").get_json()
    assert "could not be read" in data["catalog_error"]


def test_pending_retry_cannot_replace_a_newer_choice_from_another_window(tmp_path, monkeypatch):
    from agent_friday.services import conversations as store

    monkeypatch.setattr(store, "_root", lambda: tmp_path / "conversations")
    cid = store.create(title="Pending selection")['id']
    app = Flask(__name__)
    app.register_blueprint(conversations.conversations_bp)
    monkeypatch.setattr(conversations, "_why_this_seat_cannot_be_bound",
                        lambda _: conversations._SeatSurveyPending("Checking"))
    client = app.test_client()
    url = "/api/conversations/" + cid
    pending = client.patch(url, json={"seat": {"model": "first"}}).get_json()
    assert pending["seat_pending"] is True
    token = pending["seat_change_id"]
    monkeypatch.setattr(conversations, "_why_this_seat_cannot_be_bound", lambda _: None)
    client.patch(url, json={"seat": {"model": "second"}})
    old = client.patch(url, json={"seat": {"model": "first"},
                                 "seat_change_id": token}).get_json()
    assert old["superseded"] is True
    assert store.load(cid)["seat"]["model"] == "second"


def test_stale_occupancy_requests_retry_without_claiming_a_model_is_still_serving(monkeypatch):
    from agent_friday.services import machine_probe, local_seats

    monkeypatch.setattr(local_seats, "_is_local_name", lambda _: True)
    monkeypatch.setattr(machine_probe, "snapshot", lambda *a, **k:
                        ({"live": {"previous:local": {}}, "failed": False},
                         time.time() - 60, "stale"))
    reason = conversations._why_this_seat_cannot_be_bound({"model": "next:local"})
    assert "try again" in reason
    assert "previous:local" not in reason


def test_catalogue_summary_does_not_repeat_a_slow_provider_probe(monkeypatch):
    from types import SimpleNamespace
    from agent_friday.services import model_discovery

    started, release, finished = (threading.Event() for _ in range(3))
    calls = []
    provider = {"name": "test-slow-provider", "type": "ollama", "models": []}

    def slow(_):
        calls.append(1)
        started.set()
        try:
            release.wait(2)
            return True
        finally:
            finished.set()

    registry = SimpleNamespace(get_enabled_providers=lambda: [provider],
                               is_provider_available=slow)
    monkeypatch.setattr(model_catalog, "get_provider_registry", lambda: registry)
    monkeypatch.setattr(model_catalog, "_arbiter_seat_entries", lambda: [])
    monkeypatch.setattr(model_catalog, "_friday_store_entries", lambda **_: [])
    monkeypatch.setattr(model_catalog, "_live_ollama_models", lambda *_: None)
    monkeypatch.setattr(model_catalog, "_custom_models", lambda: [])
    monkeypatch.setattr(model_catalog, "_overlay_uncached", lambda p: p)
    monkeypatch.setattr(model_discovery, "ensure_background_refresh", lambda: None)
    swr_cache.invalidate("models:provider_available:test-slow-provider")
    app = Flask(__name__)
    try:
        with app.test_request_context():
            before = time.monotonic()
            data = model_catalog.build_catalog(include_engines=False)
            assert time.monotonic() - before < 0.75
        assert started.wait(0.5)
        assert calls == [1]
        assert data["providers"][0]["available"] is False
        assert data["providers"][0]["availability_reading"] == "unknown"
    finally:
        release.set()
        finished.wait(3)
        swr_cache.invalidate("models:provider_available:test-slow-provider")
