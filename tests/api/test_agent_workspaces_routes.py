"""Owner-only controls never borrow a surface's authority from request JSON."""
from types import SimpleNamespace

import pytest

from agent_friday.user_errors import UserFacingPermissionError


@pytest.fixture
def workspace_api(monkeypatch, tmp_path):
    from agent_friday.routes import agent_workspaces as routes
    from agent_friday.services import agent_workspace_permissions as permissions
    state = SimpleNamespace(private=False, generation=10, local=True, screen=True, token_valid=True,
                            controls=[], inputs=[], stops=[], validated=[])
    state.real_screen_session = routes.screen_click.screen_session
    state.real_token_valid = routes.core._api_token_valid
    monkeypatch.setattr(routes.core, "_is_local_request", lambda: state.local)
    monkeypatch.setattr(routes.screen_click, "screen_session", lambda req: state.screen)
    monkeypatch.setattr(routes.core, "_api_token_valid", lambda token: state.token_valid)
    monkeypatch.setattr(routes.off_record, "active", lambda: state.private)
    monkeypatch.setattr(routes.off_record, "generation", lambda: state.generation)
    monkeypatch.setattr(permissions, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(permissions, "_INITIALIZED", set())
    monkeypatch.setattr(permissions, "_FAULTED", set())
    state.permission = permissions.set_enabled(True)
    state.owner = object()
    state.row = {"surface_id": "surface-example", "generation": 3, "mode": "agent",
                 "conversation_id": "chat-example", "title": "Example page", "url": "https://example.com/"}

    def validate(owner, *, purpose="act"):
        assert owner is state.owner
        state.validated.append(purpose)
        return True

    def listing(validator):
        validator(state.owner, purpose="view")
        return [dict(state.row)]

    def status(sid, validator):
        assert sid == state.row["surface_id"]
        validator(state.owner, purpose="view")
        return dict(state.row)

    def control(sid, operation, generation, validator):
        if operation not in {"close", "revoke"}:
            validator(state.owner, purpose="control")
        state.controls.append((sid, operation, generation))
        return dict(state.row)

    def user_input(sid, generation, event, validator):
        validator(state.owner, purpose="control")
        state.inputs.append((sid, generation, event))
        return dict(state.row)

    monkeypatch.setattr(routes.browser_authority, "validate_owner", validate)
    monkeypatch.setattr(routes.browser_session, "list_owned_sessions", listing)
    monkeypatch.setattr(routes.browser_session, "owned_status", status)
    monkeypatch.setattr(routes.browser_session, "control_owned_session", control)
    monkeypatch.setattr(routes.browser_session, "owned_human_input", user_input)
    monkeypatch.setattr(routes.browser_session, "owned_frame", lambda sid, generation, validator:
                        {**status(sid, validator), "image": "data:image/jpeg;base64,ZXhhbXBsZQ=="})
    monkeypatch.setattr(routes.browser_session, "stop_all_owned_sessions", lambda: state.stops.append(True))
    state.routes = routes
    return state


def test_permission_is_explicit_versioned_local_owner_consent(client, workspace_api):
    state = workspace_api
    response = client.get("/api/browser/workspaces/permission")
    assert response.get_json()["permission"] == state.permission
    assert response.headers["Cache-Control"] == "no-store"
    assert client.put("/api/browser/workspaces/permission", json={"enabled": False}).status_code == 400
    response = client.put("/api/browser/workspaces/permission", json={
        "enabled": False, "generation": state.permission["generation"]})
    assert response.status_code == 200
    assert response.get_json()["permission"]["enabled"] is False
    assert state.stops == [True]


@pytest.mark.parametrize("boundary", ["local", "screen"])
def test_permission_cannot_be_changed_by_a_remote_or_non_screen_caller(client, workspace_api, boundary):
    setattr(workspace_api, boundary, False)
    response = client.put("/api/browser/workspaces/permission", json={
        "enabled": False, "generation": workspace_api.permission["generation"]})
    assert response.status_code == 403
    assert workspace_api.stops == []


def test_actual_screen_gate_requires_token_and_same_origin_browser_headers(client, workspace_api, monkeypatch):
    state = workspace_api
    monkeypatch.setattr(state.routes.screen_click, "screen_session", state.real_screen_session)
    monkeypatch.setattr(state.routes.core, "_api_token_valid", state.real_token_valid)
    body = {"enabled": False, "generation": state.permission["generation"]}
    assert client.put("/api/browser/workspaces/permission", json=body).status_code == 403
    headers = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
               "X-Friday-Token": state.routes.core._current_api_token()}
    response = client.put("/api/browser/workspaces/permission", json=body, headers=headers)
    assert response.status_code == 200
    assert state.stops == [True]


def test_body_cannot_supply_another_owner_or_task(client, workspace_api):
    response = client.post("/api/browser/workspaces/surface-example/control", json={
        "operation": "takeover", "generation": 3, "actor_id": "other-agent"})
    assert response.status_code == 400
    assert workspace_api.controls == []


def test_list_filter_preserves_server_validated_scope(client, workspace_api):
    response = client.get("/api/browser/workspaces?conversation_id=chat-example")
    assert response.status_code == 200
    assert response.get_json()["workspaces"] == [workspace_api.row]
    assert workspace_api.validated == ["view", "view"]
    assert client.get("/api/browser/workspaces?conversation_id=another").get_json()["workspaces"] == []
    assert client.get("/api/browser/workspaces?conversation_id=../other").status_code == 400


def test_surface_operations_carry_current_validator_and_input_generation(client, workspace_api):
    response = client.post("/api/browser/workspaces/surface-example/control", json={
        "operation": "takeover", "generation": 3})
    assert response.status_code == 200
    event = {"type": "click", "x": 10, "y": 20, "page_generation": 2, "frame_sequence": 4}
    response = client.post("/api/browser/workspaces/surface-example/input", json={"generation": 3, "event": event})
    assert response.status_code == 200
    assert workspace_api.inputs == [("surface-example", 3, event)]
    assert workspace_api.validated == ["control", "view", "control", "view"]


def test_privacy_change_during_body_read_prevents_dispatch(client, workspace_api, monkeypatch):
    state = workspace_api
    original = state.routes._body
    def delayed(keys):
        result = original(keys)
        state.generation += 1
        return result
    monkeypatch.setattr(state.routes, "_body", delayed)
    response = client.post("/api/browser/workspaces/surface-example/input", json={
        "generation": 3, "event": {"type": "text", "text": "example"}})
    assert response.status_code == 403
    assert state.inputs == []


def test_privacy_change_during_permission_body_read_cannot_enable(client, workspace_api, monkeypatch):
    state = workspace_api
    before = state.routes.permissions.set_enabled(False)
    original = state.routes._body
    def delayed(keys):
        result = original(keys)
        state.generation += 1
        return result
    monkeypatch.setattr(state.routes, "_body", delayed)
    response = client.put("/api/browser/workspaces/permission", json={
        "enabled": True, "generation": before["generation"]})
    assert response.status_code == 403
    assert state.routes.permissions.snapshot()["enabled"] is False


def test_frame_is_not_published_after_owner_revocation_during_capture(client, workspace_api, monkeypatch):
    state = workspace_api
    def revoked(owner, *, purpose="act"):
        raise UserFacingPermissionError("Assignment changed", status=403)
    def capture(sid, generation, validator):
        validator(state.owner, purpose="view")
        monkeypatch.setattr(state.routes.browser_authority, "validate_owner", revoked)
        return {"image": "never-publish-these-pixels"}
    monkeypatch.setattr(state.routes.browser_session, "owned_frame", capture)
    response = client.get("/api/browser/workspaces/surface-example/frame?generation=3")
    assert response.status_code == 403
    assert "never-publish" not in response.get_data(as_text=True)


def test_stop_all_and_close_remain_available_when_private(client, workspace_api):
    workspace_api.private = True
    assert client.get("/api/browser/workspaces").status_code == 403
    response = client.post("/api/browser/workspaces/stop-all", json={})
    assert response.status_code == 200 and workspace_api.stops == [True]
    response = client.post("/api/browser/workspaces/surface-example/control", json={
        "operation": "close", "generation": 3})
    assert response.status_code == 200
    assert "title" not in response.get_json()["workspace"]
    assert workspace_api.validated == []


def test_cross_site_read_and_non_json_input_are_refused(client, workspace_api):
    assert client.get("/api/browser/workspaces", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/browser/workspaces/surface-example/input", data="text").status_code == 415


def test_frame_generation_cannot_be_omitted_duplicated_or_coerced(client, workspace_api):
    for query in ("", "?generation=true", "?generation=3&generation=3", "?generation=0"):
        assert client.get("/api/browser/workspaces/surface-example/frame" + query).status_code == 400


def test_stale_surface_is_a_conflict_and_not_an_invented_success(client, workspace_api, monkeypatch):
    def changed(*args):
        raise workspace_api.routes.browser_session.BrowserRefused("stale generation")
    monkeypatch.setattr(workspace_api.routes.browser_session, "control_owned_session", changed)
    response = client.post("/api/browser/workspaces/surface-example/control", json={
        "operation": "takeover", "generation": 3})
    assert response.status_code == 409
    assert response.get_json()["ok"] is False


@pytest.mark.parametrize("operation", [[], {}, None, 42, True])
def test_malformed_control_operation_is_a_client_error(client, workspace_api, operation):
    response = client.post("/api/browser/workspaces/surface-example/control", json={
        "operation": operation, "generation": 3})
    assert response.status_code == 400
    assert workspace_api.controls == []


def test_frame_is_discarded_when_privacy_changes_during_serialization(client, workspace_api, monkeypatch):
    state = workspace_api
    original = state.routes.jsonify
    def delayed(*args, **kwargs):
        response = original(*args, **kwargs)
        state.generation += 1
        return response
    monkeypatch.setattr(state.routes, "jsonify", delayed)
    response = client.get("/api/browser/workspaces/surface-example/frame?generation=3")
    assert response.status_code == 403
    assert "data:image" not in response.get_data(as_text=True)


@pytest.mark.parametrize("path,body", [
    ("/input", {"generation": 3, "event": {"type": "text", "text": "private"}}),
    ("/control", {"generation": 3, "operation": "close"}),
    ("/control", {"generation": 3, "operation": "takeover"}),
])
def test_expired_screen_token_during_body_read_prevents_dispatch(client, workspace_api, monkeypatch, path, body):
    state = workspace_api
    original = state.routes._body
    def delayed(keys):
        data = original(keys)
        state.token_valid = False
        return data
    monkeypatch.setattr(state.routes, "_body", delayed)
    response = client.post("/api/browser/workspaces/surface-example" + path, json=body,
                           headers={"X-Friday-Token": "admitted-screen-session"})
    assert response.status_code == 403
    assert state.controls == [] and state.inputs == []


def test_expired_screen_token_cannot_commit_workspace_consent(client, workspace_api, monkeypatch):
    state = workspace_api
    before = state.routes.permissions.set_enabled(False)
    original = state.routes.permissions._write
    def delayed(path, data, authorize=None):
        if authorize is not None:
            state.token_valid = False
        return original(path, data, authorize)
    monkeypatch.setattr(state.routes.permissions, "_write", delayed)
    response = client.put("/api/browser/workspaces/permission", json={
        "enabled": True, "generation": before["generation"]},
        headers={"X-Friday-Token": "admitted-screen-session"})
    assert response.status_code == 403
    assert state.routes.permissions.snapshot() == before


def test_worker_validator_retains_screen_authority_without_request_context(client, workspace_api, monkeypatch):
    state = workspace_api
    captured = []
    def dispatch(sid, generation, event, validator):
        captured.append(validator)
        return dict(state.row)
    monkeypatch.setattr(state.routes.browser_session, "owned_human_input", dispatch)
    response = client.post("/api/browser/workspaces/surface-example/input", json={
        "generation": 3, "event": {"type": "key", "key": "Enter"}},
        headers={"X-Friday-Token": "admitted-screen-session"})
    assert response.status_code == 200
    assert captured[0](state.owner, purpose="control") is True
    state.token_valid = False
    with pytest.raises(UserFacingPermissionError):
        captured[0](state.owner, purpose="control")


def test_stop_all_checks_screen_authority_after_body_wait(client, workspace_api, monkeypatch):
    state = workspace_api
    original = state.routes._body
    def delayed(keys):
        data = original(keys)
        state.token_valid = False
        return data
    monkeypatch.setattr(state.routes, "_body", delayed)
    response = client.post("/api/browser/workspaces/stop-all", json={},
                           headers={"X-Friday-Token": "admitted-screen-session"})
    assert response.status_code == 403
    assert state.stops == []


@pytest.mark.parametrize("method,path,body", [
    ("get", "", None),
    ("get", "/surface-example/frame?generation=3", None),
    ("post", "/surface-example/input", {"generation": 3, "event": {"type": "key", "key": "Enter"}}),
    ("post", "/surface-example/control", {"generation": 3, "operation": "takeover"}),
])
def test_owner_revocation_during_serialization_drops_all_page_data(client, workspace_api, monkeypatch, method, path, body):
    state = workspace_api
    original = state.routes.jsonify
    def revoked(owner, *, purpose="view"):
        raise UserFacingPermissionError("Assignment changed", status=403)
    def delayed(*args, **kwargs):
        response = original(*args, **kwargs)
        monkeypatch.setattr(state.routes.browser_authority, "validate_owner", revoked)
        return response
    monkeypatch.setattr(state.routes, "jsonify", delayed)
    response = getattr(client, method)("/api/browser/workspaces" + path, json=body)
    assert response.status_code == 403
    assert "Example page" not in response.get_data(as_text=True)
    assert "data:image" not in response.get_data(as_text=True)


@pytest.mark.parametrize("field", ["generation", "page_generation"])
def test_frame_transition_during_serialization_cannot_publish_old_pixels(client, workspace_api, monkeypatch, field):
    state = workspace_api
    state.row["page_generation"] = 1
    original = state.routes.jsonify
    def delayed(*args, **kwargs):
        response = original(*args, **kwargs)
        state.row[field] += 1
        return response
    monkeypatch.setattr(state.routes, "jsonify", delayed)
    response = client.get("/api/browser/workspaces/surface-example/frame?generation=3")
    assert response.status_code == 409
    assert "data:image" not in response.get_data(as_text=True)
