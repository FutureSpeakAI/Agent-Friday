"""Sites authentication surfaces, malformed input and opaque-origin previews."""
import pytest
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def public_origin(monkeypatch):
    from agent_friday.services import sites_privacy, site_previews
    monkeypatch.setattr(sites_privacy, "capture", lambda: SimpleNamespace(generation=0))
    monkeypatch.setattr(sites_privacy, "require_generation", lambda generation: SimpleNamespace(generation=generation))
    monkeypatch.setattr(sites_privacy, "admit", lambda context: context["_sites_origin"].generation)
    monkeypatch.setattr(site_previews, "check_response", lambda result: None)


def headers(mode=None):
    from agent_friday import core
    return {"X-Friday-Token": core._current_api_token(), **({"X-Friday-Preview-Mode": mode} if mode else {})}


def test_sites_blueprint_is_registered(server_module):
    assert "sites" in server_module.ROUTE_MODULES
    assert "sites" in server_module.BLUEPRINT_REPORT["registered"]


@pytest.mark.parametrize("body", [[], ["bad"], "bad", {"action": "save", "args": []}])
def test_sites_action_rejects_non_object_arguments(client, body):
    response = client.post("/api/sites/action", json=body, headers=headers())
    assert response.status_code == 400


def test_route_derives_existing_owner_without_taking_owner_from_tool_arguments(client, monkeypatch):
    from agent_friday.services import sites_operations
    calls = []
    monkeypatch.setattr(sites_operations, "get_site", lambda _sid: {"conversation_id": "chat-owner"})
    monkeypatch.setattr(sites_operations, "execute", lambda action, args, context: calls.append((action, args, context)) or {"status": "ok"})
    response = client.post("/api/sites/action", json={"action": "inspect", "args": {"site_id": "site-example"}}, headers=headers())
    assert response.status_code == 200
    assert calls[0][2]["conversation_id"] == "chat-owner"
    assert calls[0][2]["_sites_origin"].generation == 0


def test_old_preview_route_cannot_serve_executable_build_bytes(client, monkeypatch):
    from agent_friday.services import sites_operations
    monkeypatch.setattr(sites_operations, "preview_file", lambda *args, **kwargs: pytest.fail("Legacy bytes served"))
    response = client.get("/api/sites/preview/site-example/build-example/index.html")
    assert response.status_code == 410
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_preview_requires_local_page_token_before_origin_or_snapshot(client, monkeypatch):
    from agent_friday.services import site_previews, sites_privacy
    monkeypatch.setattr(site_previews, "issue", lambda *args, **kwargs: pytest.fail("Unguarded preview"))
    monkeypatch.setattr(sites_privacy, "capture", lambda: pytest.fail("Unguarded origin capture"))
    response = client.post("/api/sites/preview", json={"mode": "manual", "site_id": "site-example", "site_revision": 2, "build_id": "build-example"})
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("body", [{"site_id": "site-example", "build_id": "build-example"},
                                  {"site_id": "site-example", "site_revision": 2, "build_id": "build-example", "origin": "forged"}])
def test_preview_route_requires_exact_revision_and_fields(client, monkeypatch, body):
    from agent_friday.services import site_previews
    monkeypatch.setattr(site_previews, "issue", lambda *args, **kwargs: pytest.fail("Malformed preview admitted"))
    assert client.post("/api/sites/preview", json=body, headers=headers("manual")).status_code == 409


def test_preview_route_returns_only_ephemeral_wrapper_and_original_origin(client, monkeypatch):
    from agent_friday.services import site_previews
    calls = []
    handle = "p" + "3" * 48
    result = {"status": "ok", "site_id": "site-example", "site_revision": 2, "build_id": "build-example",
              "preview_url": "/api/sites/preview-frame/" + handle, "expires_at": 1000}
    monkeypatch.setattr(site_previews, "issue", lambda *args, **kwargs: calls.append((args, kwargs)) or result)
    response = client.post("/api/sites/preview", json={"mode": "manual", **{key: result[key] for key in ("site_id", "site_revision", "build_id")}}, headers=headers("manual"))
    assert response.status_code == 200 and response.json == result
    assert calls[0][0] == ("site-example", 2, "build-example")
    assert calls[0][1]["origin"].generation == 0
    assert response.headers["Cache-Control"] == "no-store"


def test_preview_late_privacy_refusal_discards_unreturned_session(client, monkeypatch):
    from agent_friday.services import site_previews
    closed = []
    handle = "p" + "4" * 48
    monkeypatch.setattr(site_previews, "issue", lambda *args, **kwargs: {"preview_url": "/api/sites/preview-frame/" + handle})
    monkeypatch.setattr(site_previews, "check_response", lambda result: (_ for _ in ()).throw(ValueError("Ended")))
    monkeypatch.setattr(site_previews, "close", closed.append)
    response = client.post("/api/sites/preview", json={"mode": "manual", "site_id": "site-example", "site_revision": 2, "build_id": "build-example"}, headers=headers("manual"))
    assert response.status_code == 409 and closed == [handle]
    assert handle not in response.get_data(as_text=True)


def test_trusted_wrapper_keeps_exact_frame_source_through_core_sanitizer(client, monkeypatch):
    from agent_friday.services import site_previews
    source = "http://p" + "5" * 48 + ".localhost:12345"
    policy = "sandbox allow-scripts; default-src 'none'; script-src 'none'; frame-src " + source + "; frame-ancestors 'self'; webrtc 'block'"
    monkeypatch.setattr(site_previews, "wrapper", lambda *args, **kwargs:
                        ('<iframe sandbox="allow-scripts" src="' + source + '/"></iframe>',
                         {"Content-Security-Policy": policy, "Cache-Control": "no-store"}))
    response = client.get("/api/sites/preview-frame/p" + "6" * 48)
    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "frame-src " + source in csp and "script-src 'none'" in csp
    assert "webrtc 'block'" in csp
    assert "allow-same-origin" not in csp and "connect-src 'none'" in csp


def test_preview_close_needs_page_token_and_does_not_recapture_private_origin(client, monkeypatch):
    from agent_friday.services import site_previews, sites_privacy
    handle = "p" + "7" * 48
    closed = []
    monkeypatch.setattr(site_previews, "close", closed.append)
    monkeypatch.setattr(sites_privacy, "capture", lambda: pytest.fail("Cleanup recaptured authority"))
    assert client.delete("/api/sites/preview-frame/" + handle).status_code == 403
    assert closed == []
    assert client.delete("/api/sites/preview-frame/" + handle, headers=headers()).status_code == 200
    assert closed == [handle]


def test_navigation_preview_consumes_original_ticket_without_fresh_origin(client, monkeypatch):
    from agent_friday.services import site_previews, sites_privacy
    calls = []
    request_id = "n" + "8" * 48
    monkeypatch.setattr(sites_privacy, "capture", lambda: pytest.fail("Automatic preview recaptured authority"))
    monkeypatch.setattr(site_previews, "issue", lambda *args, **kwargs: pytest.fail("Automatic preview fell back to manual"))
    monkeypatch.setattr(site_previews, "issue_navigation", lambda *args, **kwargs:
                        calls.append((args, kwargs)) or {"preview_url": "/api/sites/preview-frame/p" + "9" * 48})
    response = client.post("/api/sites/preview", json={"mode": "navigation", "site_id": "site-example",
        "site_revision": 2, "build_id": "build-example", "request_id": request_id}, headers=headers("navigation"))
    assert response.status_code == 200
    assert calls[0][0] == ("site-example", 2, "build-example", request_id)
    assert "origin" not in calls[0][1]


@pytest.mark.parametrize("mode,extra", [("manual", {"request_id": "n" + "8" * 48}), ("navigation", {}), ("other", {})])
def test_preview_modes_cannot_mix_or_fall_back(client, monkeypatch, mode, extra):
    from agent_friday.services import site_previews, sites_privacy
    monkeypatch.setattr(site_previews, "issue", lambda *args, **kwargs: pytest.fail("Invalid mode admitted a preview"))
    monkeypatch.setattr(site_previews, "issue_navigation", lambda *args, **kwargs: pytest.fail("Invalid mode consumed ticket"))
    response = client.post("/api/sites/preview", json={"mode": mode, "site_id": "site-example",
        "site_revision": 2, "build_id": "build-example", **extra}, headers=headers(mode))
    assert response.status_code == 409


def test_preview_failure_responses_are_not_cached(client, monkeypatch):
    from agent_friday.services import site_previews
    monkeypatch.setattr(site_previews, "issue", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("internal")))
    response = client.post("/api/sites/preview", json={"mode": "manual", "site_id": "site-example",
        "site_revision": 2, "build_id": "build-example"}, headers=headers("manual"))
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    assert "internal" not in response.get_data(as_text=True)


def test_wrapper_head_rechecks_liveness_and_never_returns_active_after_revocation(client, monkeypatch):
    from agent_friday.services import site_previews
    current, checked = {"value": True}, []

    def wrapper(handle, **kwargs):
        checked.append(handle)
        if not current["value"]:
            raise ValueError("Original context ended")
        return "<p>Fixed wrapper</p>", {"X-Friday-Site-Preview": "active"}

    monkeypatch.setattr(site_previews, "wrapper", wrapper)
    path = "/api/sites/preview-frame/p" + "a" * 48
    response = client.head(path)
    assert response.status_code == 200 and response.data == b""
    assert response.headers["X-Friday-Site-Preview"] == "active"
    current["value"] = False
    response = client.head(path)
    assert response.status_code == 410 and response.data == b""
    assert "X-Friday-Site-Preview" not in response.headers
    assert response.headers["Cache-Control"] == "no-store" and len(checked) == 2


def test_manual_preview_admission_precedes_delayed_body_private_cycle(client, monkeypatch):
    from agent_friday.routes import sites as routes
    from agent_friday.services import site_previews, sites_privacy
    state, events = {"generation": 3}, []

    def capture():
        events.append("capture")
        return SimpleNamespace(generation=state["generation"])

    def body():
        events.append("body")
        state["generation"] = 5
        return {"mode": "manual", "site_id": "site-example", "site_revision": 2, "build_id": "build-example"}

    def issue(*args, origin, **kwargs):
        assert origin.generation == 3
        assert origin.generation != state["generation"]
        raise ValueError("The originally admitted context ended")

    monkeypatch.setattr(sites_privacy, "capture", capture)
    monkeypatch.setattr(routes, "_object", body)
    monkeypatch.setattr(site_previews, "issue", issue)
    response = client.post("/api/sites/preview", json={}, headers=headers("manual"))
    assert response.status_code == 409 and events == ["capture", "body"]


def test_navigation_header_cannot_fall_back_to_manual_body(client, monkeypatch):
    from agent_friday.services import site_previews, sites_privacy
    monkeypatch.setattr(sites_privacy, "capture", lambda: pytest.fail("Navigation header recaptured origin"))
    monkeypatch.setattr(site_previews, "issue", lambda *args, **kwargs: pytest.fail("Navigation header fell back"))
    response = client.post("/api/sites/preview", json={"mode": "manual", "site_id": "site-example",
        "site_revision": 2, "build_id": "build-example"}, headers=headers("navigation"))
    assert response.status_code == 409


def test_hosting_entry_does_not_echo_token_even_when_storage_fails(client, monkeypatch):
    from agent_friday.services import site_hosting, sites_operations
    monkeypatch.setattr(sites_operations, "_durable", lambda: None)
    monkeypatch.setattr(site_hosting, "connect", lambda data, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic token value")))
    response = client.post("/api/sites/hosting", json={"token": "synthetic token value"}, headers=headers())
    assert response.status_code == 503 and "synthetic token value" not in response.get_data(as_text=True)


def test_hosting_credentials_require_local_page_token(client, monkeypatch):
    from agent_friday.services import site_hosting
    monkeypatch.setattr(site_hosting, "connect", lambda data, **kwargs: pytest.fail("No unguarded credential save"))
    assert client.post("/api/sites/hosting", json={"token": "synthetic"}).status_code == 403


def test_hosting_rejects_proxy_even_with_page_token(app, monkeypatch):
    from agent_friday.services import site_hosting
    monkeypatch.setattr(site_hosting, "connect", lambda data, **kwargs: pytest.fail("No proxy credential save"))
    result = app.test_client().post("/api/sites/hosting", json={"token": "synthetic"},
        headers={**headers(), "CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert result.status_code in (401, 403)
