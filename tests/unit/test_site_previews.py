"""Frozen preview authority, origin separation and bounded loopback serving."""
import copy
import http.client
import json
import re
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from agent_friday.services import publish_web, site_builds, site_previews, sites_operations, sites_privacy


@pytest.fixture
def preview_env(tmp_path, monkeypatch):
    origin = object()
    state = {"generation": 3, "private": False, "boot": "instance-a", "available": True}
    site = {"site_id": "site-" + "1" * 32, "revision": 2, "name": "Sample site",
            "conversation_id": "sample-chat", "project_id": "sample-project", "codebase_id": "sample-code"}
    files = {"index.html": b'<link rel="stylesheet" href="/assets/app.css"><script type="module" src="/assets/main.js"></script>',
             "assets/main.js": b"import './nested.js'; fetch('/data.json')", "assets/nested.js": b"export const sample=1;",
             "assets/app.css": b"body{color:blue}", "data.json": b'{"sample":true}',
             "module.wasm": b"\x00asm\x01\x00\x00\x00", "docs/index.html": b"Sample documentation"}
    build = {"site_id": site["site_id"], "build_id": "build-" + "2" * 32, "status": "built",
             "files": site_builds.manifest(files), "output_hash": site_builds.digest(files)}
    root = tmp_path / "sites" / site["site_id"] / "builds" / build["build_id"]
    for path, data in files.items():
        target = root / "output" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def admit(context):
        if context.get("_sites_origin") is not origin or state["private"] or state["generation"] != 3:
            raise ValueError("Original public origin ended")
        return 3

    def require(generation, boot):
        if generation != 3 or boot != state["boot"]:
            raise ValueError("Original instance ended")
        return admit({"_sites_origin": origin})

    def validate(value):
        if not state["available"] or value is None:
            raise ValueError("Owning chat unavailable")
        return value

    monkeypatch.setattr(sites_privacy, "admit", admit)
    monkeypatch.setattr(sites_privacy, "boot_id", lambda: state["boot"])
    monkeypatch.setattr(sites_privacy, "require_operation", require)
    monkeypatch.setattr(sites_operations, "get_site", lambda sid: copy.deepcopy(site) if sid == site["site_id"] else None)
    monkeypatch.setattr(sites_operations, "validate_site_owner", validate)
    monkeypatch.setattr(sites_operations, "_build", lambda current, bid: copy.deepcopy(build))
    monkeypatch.setattr(site_builds, "build_dir", lambda sid, bid: root)
    monkeypatch.setattr(publish_web, "scan", lambda bundle: {"ok": True})
    service = site_previews.PreviewService()
    monkeypatch.setattr(site_previews, "_SERVICE", service)
    monkeypatch.setattr(site_previews, "_NAVIGATION", {})
    env = dict(state=state, site=site, build=build, root=root, files=files, origin=origin, service=service)
    yield env
    service.shutdown()


def issue(env, **overrides):
    args = dict(site_id=env["site"]["site_id"], site_revision=2, build_id=env["build"]["build_id"],
                origin=env["origin"], parent_origin="http://127.0.0.1:5000/")
    args.update(overrides)
    return site_previews.issue(**args)


def opened(env):
    result = issue(env)
    handle = result["preview_url"].rsplit("/", 1)[-1]
    preview = env["service"].by_handle(handle)
    return result, preview


def get(preview, target="/", *, method="GET", host=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", int(preview.host.rsplit(":", 1)[1]), timeout=2)
    try:
        connection.request(method, target, headers={"Host": host or preview.host, **(headers or {})})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_root_assets_modules_data_and_wasm_use_only_frozen_capability_origin(preview_env):
    result, preview = opened(preview_env)
    assert re.fullmatch(r"p[a-f0-9]{48}\.localhost:[0-9]+", preview.host)
    assert result["site_revision"] == 2
    assert preview.host not in json.dumps(result)
    assert preview.handle != preview.host.split(".")[0]
    assert "preview_url" not in preview_env["build"]
    assert "preview_url" not in preview_env["site"]
    for path, expected in preview_env["files"].items():
        status, headers, body = get(preview, "/" + path)
        assert status == 200 and body == expected
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert "Access-Control-Allow-Credentials" not in headers and "Set-Cookie" not in headers
        assert headers["Cross-Origin-Resource-Policy"] == "cross-origin"
        assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
        csp = headers["Content-Security-Policy"]
        assert "sandbox allow-scripts" in csp and "allow-same-origin" not in csp
        assert "connect-src http://" + preview.host + ";" in csp
        assert "webrtc 'block';" in csp
        assert "http://127.0.0.1:5000" not in csp and "frame-src 'none'" in csp
        if path.endswith(".js"):
            assert headers["Content-Type"] == "application/javascript"
        if path.endswith(".wasm"):
            assert headers["Content-Type"] == "application/wasm"
    assert get(preview)[2] == preview_env["files"]["index.html"]
    assert get(preview, "/docs/")[2] == preview_env["files"]["docs/index.html"]
    assert get(preview, "/assets/main.js?v=1", method="HEAD")[2] == b""


def test_wrapper_is_scriptless_exact_source_and_independent_authenticated_handle(preview_env):
    result, preview = opened(preview_env)
    body, headers = site_previews.wrapper(preview.handle, parent_origin="http://127.0.0.1:5000/")
    assert result["preview_url"] == "/api/sites/preview-frame/" + preview.handle
    assert "<script" not in body and 'sandbox="allow-scripts"' in body
    assert 'src="http://' + preview.host + '/"' in body
    assert "frame-src http://" + preview.host + ";" in headers["Content-Security-Policy"]
    assert "script-src 'none'" in headers["Content-Security-Policy"]
    assert "webrtc 'block';" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Friday-Site-Preview"] == "active"
    with pytest.raises(ValueError):
        site_previews.wrapper(preview.handle, parent_origin="http://localhost:5000/")


@pytest.mark.parametrize("target", ["//assets/main.js", "/../index.html", "/%2e%2e/index.html", "/%2findex.html",
                                    "/%5cindex.html", "/C:/index.html", "/.env", "/api/settings", "/unknown.js"])
def test_paths_cannot_leave_exact_manifest(preview_env, target):
    _, preview = opened(preview_env)
    assert get(preview, target)[0] == 404


@pytest.mark.parametrize("method,host,headers", [
    ("POST", None, {}), ("OPTIONS", None, {}), ("GET", "localhost:5000", {}),
    ("GET", "127.0.0.1:5000", {}), ("GET", None, {"Upgrade": "websocket"}),
    ("GET", None, {"Transfer-Encoding": "chunked"}),
])
def test_no_proxy_mutations_upgrade_or_foreign_host(preview_env, method, host, headers):
    _, preview = opened(preview_env)
    assert get(preview, method=method, host=host, headers=headers)[0] == 404


@pytest.mark.parametrize("change", ["generation", "private", "boot", "revision", "conversation", "project", "codebase", "archived", "build"])
def test_existing_session_never_recaptures_after_authority_changes(preview_env, change):
    _, preview = opened(preview_env)
    if change in {"generation", "private", "boot"}:
        preview_env["state"][change] = {"generation": 4, "private": True, "boot": "instance-b"}[change]
    elif change == "revision":
        preview_env["site"]["revision"] = 3
    elif change in {"conversation", "project", "codebase"}:
        preview_env["site"][change + "_id"] = "different-owner"
    elif change == "archived":
        preview_env["state"]["available"] = False
    else:
        preview_env["build"]["output_hash"] = "changed"
    with pytest.raises(ValueError):
        preview_env["service"].lookup(preview.host, "/")
    assert preview.host not in preview_env["service"].previews


def test_changed_selection_or_build_refused_before_listener(preview_env):
    with pytest.raises(ValueError):
        issue(preview_env, site_revision=1)
    (preview_env["root"] / "output/index.html").write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="Build output changed"):
        issue(preview_env)
    assert preview_env["service"].listener is None


def test_privacy_change_during_snapshot_cannot_return_or_retain_session(preview_env, monkeypatch):
    collect = site_builds.collect

    def late(*args, **kwargs):
        files = collect(*args, **kwargs)
        preview_env["state"]["generation"] = 4
        return files

    monkeypatch.setattr(site_builds, "collect", late)
    with pytest.raises(ValueError):
        issue(preview_env)
    assert preview_env["service"].previews == {} and preview_env["service"].listener is None


def test_cached_output_does_not_reread_mutable_disk(preview_env):
    _, preview = opened(preview_env)
    (preview_env["root"] / "output/index.html").write_bytes(b"later unrelated content")
    assert get(preview)[2] == preview_env["files"]["index.html"]


def test_session_memory_count_and_nonrenewing_expiry_are_bounded(preview_env, monkeypatch):
    monkeypatch.setattr(site_previews, "MAX_SESSIONS", 1)
    _, preview = opened(preview_env)
    with pytest.raises(ValueError, match="Close an existing preview"):
        issue(preview_env)
    site_previews.close(preview.handle)
    monkeypatch.setattr(site_previews, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="memory limit"):
        issue(preview_env)
    assert preview_env["service"].previews == {}


def test_expiry_and_shutdown_remove_listener_and_owned_sockets(preview_env, monkeypatch):
    monkeypatch.setattr(site_previews, "LIFETIME", 0.08)
    _, preview = opened(preview_env)
    service = preview_env["service"]
    deadline = time.monotonic() + 1
    while service.listener is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.listener is None and service.previews == {}
    with pytest.raises(ValueError):
        service.by_handle(preview.handle)
    service.shutdown()
    assert not service.connections


def test_slow_header_drip_is_cut_off_by_total_request_deadline(preview_env, monkeypatch):
    monkeypatch.setattr(site_previews, "REQUEST_SECONDS", 0.15)
    _, preview = opened(preview_env)
    wire = socket.create_connection(("127.0.0.1", urlsplit("http://" + preview.host).port), timeout=1)
    wire.settimeout(1)
    start = time.monotonic()
    closed = False
    try:
        while time.monotonic() - start < 0.8:
            try:
                wire.sendall(b"G")
                time.sleep(0.02)
            except OSError:
                closed = True
                break
        if not closed:
            closed = wire.recv(1) == b""
    finally:
        wire.close()
    assert closed and time.monotonic() - start < 0.8


def test_preview_parent_rejects_nonlocal_credentials_or_fragments(preview_env):
    for value in ("https://example.com", "http://user@localhost:5000", "http://localhost:5000/#fragment"):
        with pytest.raises(ValueError):
            issue(preview_env, parent_origin=value)
    assert preview_env["service"].listener is None


def ticket(env):
    return site_previews.prepare_navigation(env["site"]["site_id"], 2, env["build"]["build_id"], origin=env["origin"])


def consume(env, request_id, **overrides):
    args = dict(site_id=env["site"]["site_id"], site_revision=2, build_id=env["build"]["build_id"],
                request_id=request_id, parent_origin="http://127.0.0.1:5000/")
    args.update(overrides)
    return site_previews.issue_navigation(**args)


def test_same_generation_navigation_uses_original_origin_once(preview_env, monkeypatch):
    request_id = ticket(preview_env)
    assert re.fullmatch(r"n[a-f0-9]{48}", request_id)
    monkeypatch.setattr(sites_privacy, "capture", lambda: pytest.fail("Navigation recaptured origin"))
    result = consume(preview_env, request_id)
    preview = preview_env["service"].by_handle(result["preview_url"].rsplit("/", 1)[-1])
    assert preview.owner.origin is preview_env["origin"]
    assert preview.owner.generation == 3 and preview.owner.boot == "instance-a"
    with pytest.raises(ValueError, match="already used"):
        consume(preview_env, request_id)
    assert len(preview_env["service"].previews) == 1


def test_other_window_private_cycle_cannot_renew_navigation_authority(preview_env, monkeypatch):
    request_id = ticket(preview_env)
    # The old page missed both transitions and the machine is public again.
    preview_env["state"].update(private=False, generation=5)
    monkeypatch.setattr(sites_privacy, "capture", lambda: pytest.fail("Private-ended navigation recaptured origin"))
    monkeypatch.setattr(site_builds, "preview_output", lambda *args, **kwargs: pytest.fail("Expired ticket read output"))
    with pytest.raises(ValueError):
        consume(preview_env, request_id)
    assert request_id not in site_previews._NAVIGATION
    assert preview_env["service"].listener is None


@pytest.mark.parametrize("overrides", [{"site_revision": 3}, {"build_id": "build-" + "9" * 32},
                                      {"site_id": "site-" + "9" * 32}, {"site_revision": True}])
def test_navigation_mismatch_consumes_ticket_without_reading_output(preview_env, monkeypatch, overrides):
    request_id = ticket(preview_env)
    monkeypatch.setattr(site_builds, "preview_output", lambda *args, **kwargs: pytest.fail("Mismatched ticket read output"))
    with pytest.raises(ValueError):
        consume(preview_env, request_id, **overrides)
    with pytest.raises(ValueError, match="already used"):
        consume(preview_env, request_id)


def test_navigation_expiry_and_pending_count_are_bounded(preview_env, monkeypatch):
    for _ in range(site_previews.MAX_NAVIGATION):
        ticket(preview_env)
    with pytest.raises(ValueError, match="Too many"):
        ticket(preview_env)
    request_id = next(iter(site_previews._NAVIGATION))
    owner, _expires = site_previews._NAVIGATION[request_id]
    site_previews._NAVIGATION[request_id] = (owner, time.monotonic() - 1)
    with pytest.raises(ValueError, match="expired"):
        consume(preview_env, request_id)
    assert re.fullmatch(r"n[a-f0-9]{48}", ticket(preview_env))


def test_navigation_rechecks_original_origin_after_snapshot_read(preview_env, monkeypatch):
    request_id = ticket(preview_env)
    collect = site_builds.collect

    def delayed(*args, **kwargs):
        files = collect(*args, **kwargs)
        preview_env["state"].update(generation=5)
        return files

    monkeypatch.setattr(site_builds, "collect", delayed)
    with pytest.raises(ValueError):
        consume(preview_env, request_id)
    assert preview_env["service"].previews == {} and request_id not in site_previews._NAVIGATION


def test_navigation_expiring_during_snapshot_is_not_extended(preview_env, monkeypatch):
    request_id = ticket(preview_env)
    clock = {"now": time.monotonic()}
    monkeypatch.setattr(site_previews.time, "monotonic", lambda: clock["now"])
    collect = site_builds.collect

    def delayed(*args, **kwargs):
        files = collect(*args, **kwargs)
        clock["now"] += site_previews.NAVIGATION_SECONDS + 1
        return files

    monkeypatch.setattr(site_builds, "collect", delayed)
    with pytest.raises(ValueError):
        consume(preview_env, request_id)
    assert preview_env["service"].previews == {} and preview_env["service"].listener is None


class _Wire:
    def __init__(self):
        self.closed = False

    def shutdown(self, _how):
        self.closed = True

    def close(self):
        self.closed = True

    def settimeout(self, _timeout):
        if self.closed:
            raise OSError("closed")

    def sendall(self, _data):
        if self.closed:
            raise OSError("closed")


class _Listener:
    def __init__(self, wire):
        self.wire, self.accepted, self.closed = wire, False, False

    def accept(self):
        if self.accepted:
            raise OSError("finished")
        self.accepted = True
        return self.wire, ("127.0.0.1", 12345)

    def close(self):
        self.closed = True


def _fake_listener_service(monkeypatch):
    service = site_previews.PreviewService()
    owned, foreign = _Wire(), _Wire()
    listener = _Listener(owned)
    service.listener = listener
    service.previews["fixture"] = SimpleNamespace(expires=time.monotonic() + 60)
    monkeypatch.setattr(service, "check", lambda *args, **kwargs: None)
    return service, listener, owned, foreign


@pytest.mark.parametrize("phase", ["timer-construction", "timer-start", "worker-start"])
def test_accepted_wire_setup_failure_closes_only_owned_wire_and_releases_slot(monkeypatch, phase):
    service, listener, owned, foreign = _fake_listener_service(monkeypatch)
    timers = []

    class Timer:
        def __init__(self, *args, **kwargs):
            if phase == "timer-construction":
                raise RuntimeError("timer construction failed")
            self.cancelled = False
            timers.append(self)

        def start(self):
            if phase == "timer-start":
                raise RuntimeError("timer start failed")

        def cancel(self):
            self.cancelled = True

    class Thread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("worker start failed")

    monkeypatch.setattr(site_previews.threading, "Timer", Timer)
    monkeypatch.setattr(site_previews.threading, "Thread", Thread)
    with pytest.raises(RuntimeError, match="failed"):
        service._serve(listener)
    assert owned.closed and listener.closed and not foreign.closed
    assert service.connections == set() and service.inflight == {}
    assert all(item.cancelled for item in timers)
    assert all(service.slots.acquire(blocking=False) for _ in range(site_previews.MAX_CONNECTIONS))
    assert not service.slots.acquire(blocking=False)


def test_acceptance_deadline_is_armed_before_contended_registration_or_worker_start(monkeypatch):
    service, listener, owned, foreign = _fake_listener_service(monkeypatch)
    events, timers = [], []
    real_lock = threading.RLock()

    class Timer:
        def __init__(self, delay, function, args):
            self.function, self.args = function, args
            timers.append(self)

        def start(self):
            events.append("deadline-armed")

        def cancel(self):
            events.append("deadline-cancelled")

    class Lock:
        entries = 0

        def __enter__(self):
            self.entries += 1
            if self.entries == 2:
                # Simulate the absolute timer firing while accepted-socket
                # registration waits, before any worker has begun to run.
                assert events == ["deadline-armed"]
                timers[0].function(*timers[0].args)
                events.append("registration-delayed")
            real_lock.acquire()

        def __exit__(self, *args):
            real_lock.release()

    class Thread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args

        def start(self):
            assert owned.closed
            events.append("worker-started")
            self.target(*self.args)

    service.lock = Lock()
    monkeypatch.setattr(site_previews.threading, "Timer", Timer)
    monkeypatch.setattr(site_previews.threading, "Thread", Thread)
    service._serve(listener)
    assert events[:3] == ["deadline-armed", "registration-delayed", "worker-started"]
    assert owned.closed and not foreign.closed and service.connections == set()
    assert all(service.slots.acquire(blocking=False) for _ in range(site_previews.MAX_CONNECTIONS))
