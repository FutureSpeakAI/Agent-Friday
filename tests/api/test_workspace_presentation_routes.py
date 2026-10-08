"""Appearance preview/apply HTTP contracts use the real local versioned store."""
import json

import pytest

from agent_friday import core
from agent_friday.services import boot_guard, desktop_bus, off_record, workspace_registry
from agent_friday.services import workspace_studio as studio


URL = "/api/workspace/library/appearance"


@pytest.fixture(autouse=True)
def isolated_presentation(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "WS_STUDIO_DIR", tmp_path / "studio")
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    monkeypatch.setattr(off_record, "generation", lambda: 0)
    monkeypatch.setattr(boot_guard, "safe_mode", lambda: False)
    monkeypatch.setattr(desktop_bus, "broadcast", lambda *_args, **_kwargs: None)


def test_preview_apply_and_existing_revert_roundtrip(client):
    first = client.get(URL)
    assert first.status_code == 200 and first.get_json()["revision"] == "new"
    patch = {"note": "Synthetic note", "density": "compact"}
    preview = client.post(URL, json={"patch": patch, "expected_revision": "new"})
    assert preview.status_code == 200 and preview.get_json()["preview"] == patch
    assert not preview.get_json()["applied"] and not studio.WS_STUDIO_DIR.exists()
    saved = client.post(URL, json={"patch": patch, "expected_revision": "new", "apply": True})
    assert saved.status_code == 200 and saved.get_json()["applied"]
    result = saved.get_json()
    assert result["revision"] != "new" and client.get(URL).get_json()["customization"] == patch
    reverted = client.post("/api/workspace/library/revert", json={"version_id": result["revert_to"]})
    assert reverted.status_code == 200 and reverted.get_json()["customization"] == {}
    assert client.get(URL).get_json()["revision"] != result["revision"]


def test_stale_review_is_409_and_preserves_newer_changes(client):
    assert client.post(URL, json={"patch": {"note": "Newer"}, "expected_revision": "new", "apply": True}).status_code == 200
    before = (studio.WS_STUDIO_DIR / "library.json").read_bytes()
    response = client.post(URL, json={"patch": {"note": "Older"}, "expected_revision": "new", "apply": True})
    assert response.status_code == 409 and response.get_json()["status"] == "conflict"
    assert (studio.WS_STUDIO_DIR / "library.json").read_bytes() == before


@pytest.mark.parametrize("body", [None, [], {}, {"patch": {}, "expected_revision": "new"},
    {"patch": {"css": ".ws-custom-root{display:none}"}, "expected_revision": "new"},
    {"patch": {"note": "A note"}, "expected_revision": "new", "apply": "true"},
    {"patch": {"note": "A note"}, "expected_revision": "new", "apply": 1},
    {"patch": {"note": "A note"}, "expected_revision": "new", "extra": True},
    {"patch": {"actions": [{"label": "Go", "prompt": "Discuss", "script": "run()"}]}, "expected_revision": "new"}])
def test_bad_shapes_and_unreviewable_changes_are_400(client, body):
    response = client.post(URL, data=json.dumps(body), content_type="application/json")
    assert response.status_code == 400 and response.get_json()["status"] == "error"
    assert not studio.WS_STUDIO_DIR.exists()


def test_oversized_or_unsized_appearance_body_is_refused(client):
    response = client.post(URL, data=" " * (32 * 1024 + 1), content_type="application/json")
    assert response.status_code == 413
    assert client.post(URL).status_code == 413
    assert not studio.WS_STUDIO_DIR.exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_appearance_requires_owner_authentication(client, method):
    response = getattr(client, method)(URL, json={} if method == "post" else None,
        environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert response.status_code in (401, 403)


@pytest.mark.parametrize("held,boundary", [(True, "native"), (False, "bundle")])
def test_unavailable_or_bundle_workspace_is_refused_by_real_route(client, monkeypatch, held, boundary):
    monkeypatch.setattr(workspace_registry, "get", lambda key: {"id": key, "boundary": {"kind": boundary}})
    monkeypatch.setattr(workspace_registry, "is_held", lambda target: held)
    assert client.get(URL).status_code == 400
    assert client.post(URL, json={"patch": {"note": "No"}, "expected_revision": "new", "apply": True}).status_code == 400
    assert not studio.WS_STUDIO_DIR.exists()


def test_private_session_can_preview_but_cannot_apply(client, monkeypatch):
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    request = {"patch": {"note": "Temporary private preview"}, "expected_revision": "new"}
    assert client.post(URL, json=request).status_code == 200
    refused = client.post(URL, json={**request, "apply": True})
    assert refused.status_code == 403 and refused.get_json()["status"] == "error"
    assert not studio.WS_STUDIO_DIR.exists()


def test_ended_private_generation_during_request_is_refused(client, monkeypatch):
    epoch = [0]
    monkeypatch.setattr(off_record, "generation", lambda: epoch[0])
    clean = studio._presentation_patch
    def changed(patch):
        result = clean(patch)
        epoch[0] += 1
        return result
    monkeypatch.setattr(studio, "_presentation_patch", changed)
    response = client.post(URL, json={"patch": {"note": "No"}, "expected_revision": "new", "apply": True})
    assert response.status_code == 403 and "privacy context" in response.get_json()["message"]
    assert not studio.WS_STUDIO_DIR.exists()


def test_store_failure_is_503_without_leaking_private_path(client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("private synthetic path")
    monkeypatch.setattr(studio, "save_ws_doc", fail)
    response = client.post(URL, json={"patch": {"note": "No"}, "expected_revision": "new", "apply": True})
    assert response.status_code == 503 and response.get_json()["status"] == "error"
    assert "private synthetic path" not in response.get_data(as_text=True)
    assert not studio.WS_STUDIO_DIR.exists()


@pytest.mark.parametrize("path,payload", [
    ("chat", {"message": "Private request"}), ("chat/clear", {}),
    ("reset", {}), ("revert", {"version_id": "synthetic-version"}),
])
def test_legacy_studio_mutation_routes_refuse_private_admission(client, monkeypatch, path, payload):
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    response = client.post("/api/workspace/library/" + path, json=payload)
    assert response.status_code == 403 and response.get_json()["status"] == "error"
    assert not studio.WS_STUDIO_DIR.exists()


def test_chat_route_captures_generation_before_system_preparation(client, monkeypatch):
    from agent_friday.routes import workspace_studio as routes
    epoch, generated = [0], []
    monkeypatch.setattr(off_record, "generation", lambda: epoch[0])
    def prepare(**_kwargs):
        epoch[0] += 1
        return "Synthetic system"
    monkeypatch.setattr(routes, "_get_friday_system_prompt", prepare)
    real_turn = studio.workspace_chat_turn
    def turn(*args, **kwargs):
        kwargs["generate"] = lambda *_: generated.append(True) or "Should not run"
        return real_turn(*args, **kwargs)
    monkeypatch.setattr(routes, "workspace_chat_turn", turn)
    response = client.post("/api/workspace/library/chat", json={"message": "Captured original request"})
    assert response.status_code == 403 and "privacy context" in response.get_json()["message"]
    assert generated == [] and not studio.WS_STUDIO_DIR.exists()


def test_generation_failure_is_a_failed_http_request_without_history_mutation(client, monkeypatch):
    from agent_friday.routes import workspace_studio as routes
    studio.apply_customization("library", {"note": "Original"})
    path = studio.WS_STUDIO_DIR / "library.json"
    before = path.read_bytes()
    real_turn = studio.workspace_chat_turn
    def fail(*_args):
        raise RuntimeError("Private synthetic provider detail")
    def turn(*args, **kwargs):
        return real_turn(*args, **kwargs, generate=fail)
    monkeypatch.setattr(routes, "workspace_chat_turn", turn)
    response = client.post("/api/workspace/library/chat", json={"message": "New request"})
    assert response.status_code >= 500 and response.get_json()["status"] == "error"
    assert "Private synthetic provider detail" not in response.get_data(as_text=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("action", ["appearance", "chat", "revert"])
def test_studio_post_keeps_pre_body_authority_across_a_complete_privacy_cycle(client, monkeypatch, action):
    from agent_friday.routes import workspace_studio as routes
    _, version = studio.apply_customization("library", {"note": "Keep original"})
    path = studio.WS_STUDIO_DIR / "library.json"
    before = path.read_bytes()
    revision = studio.read_presentation("library")["revision"]
    state = {"private": False, "generation": 0}
    parsed, generated, prepared, events = [], [], [], []
    monkeypatch.setattr(off_record, "active", lambda settings=None: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    monkeypatch.setattr(desktop_bus, "broadcast", lambda *args, **kwargs: events.append(args))
    monkeypatch.setattr(routes, "_get_friday_system_prompt", lambda **kwargs: prepared.append(True) or "Synthetic system")
    monkeypatch.setattr(routes, "_predict_route_provider", lambda **kwargs: "local")
    monkeypatch.setattr(routes, "_gated_vault_control", lambda: None)
    real_turn = studio.workspace_chat_turn

    def turn(*args, **kwargs):
        return real_turn(*args, **kwargs, generate=lambda *_: generated.append(True) or "Synthetic reply")

    monkeypatch.setattr(routes, "workspace_chat_turn", turn)
    request_class = client.application.request_class
    original = request_class.get_json
    url = "/api/workspace/library/" + action

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
    payload = {
        "appearance": {"patch": {"note": "Must not replace original"}, "expected_revision": revision, "apply": True},
        "chat": {"message": "Must not generate or save"},
        "revert": {"version_id": version["id"]},
    }[action]
    response = client.post(url, json=payload)
    assert parsed == [True] and state["generation"] == 1
    assert response.status_code == 403 and "privacy context" in response.get_json()["message"]
    assert path.read_bytes() == before
    assert generated == [] and prepared == [] and events == []


@pytest.mark.parametrize("action,payload", [
    ("chat", {"message": "Private message"}), ("revert", {"version_id": "synthetic-version"}),
])
def test_private_studio_mutation_is_refused_before_reading_the_body(client, monkeypatch, action, payload):
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    parsed = []
    request_class = client.application.request_class
    original = request_class.get_json

    def counted_json(request, *args, **kwargs):
        parsed.append(request.path)
        return original(request, *args, **kwargs)

    monkeypatch.setattr(request_class, "get_json", counted_json)
    response = client.post("/api/workspace/library/" + action, json=payload)
    assert response.status_code == 403 and "Off the record" in response.get_json()["message"]
    assert parsed == [] and not studio.WS_STUDIO_DIR.exists()


@pytest.mark.parametrize("apply", [False, True])
def test_appearance_keeps_private_origin_when_body_finishes_in_public(client, monkeypatch, apply):
    state = {"private": True, "generation": 0}
    parsed = []
    monkeypatch.setattr(off_record, "active", lambda settings=None: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    request_class = client.application.request_class
    original = request_class.get_json

    def delayed_json(request, *args, **kwargs):
        body = original(request, *args, **kwargs)
        if request.path == URL:
            with core._SETTINGS_WRITE_LOCK:
                state["generation"] += 1
                state["private"] = False
            parsed.append(True)
        return body

    monkeypatch.setattr(request_class, "get_json", delayed_json)
    response = client.post(URL, json={"patch": {"note": "Private temporary preview"},
        "expected_revision": "new", "apply": apply})
    assert parsed == [True]
    if apply:
        assert response.status_code == 403 and "privacy context" in response.get_json()["message"]
    else:
        assert response.status_code == 200 and response.get_json()["applied"] is False
        assert response.get_json()["preview"] == {"note": "Private temporary preview"}
    assert not studio.WS_STUDIO_DIR.exists()
