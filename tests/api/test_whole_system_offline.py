"""Every cloud provider unreachable: the local control plane still works (north star 6.2).

Before this file, offline behaviour was tested piece by piece (voice resilience, local
seats refusing the cloud). Nothing switched the cloud off for the whole app and then
used it: settings, setup, schedules, tasks, notifications, health, a chat turn in a
local mode, the reply's route record, and the app still answering after a cloud-only
turn failed.

The cut is made at every layer a cloud call can leave through:
  * the Anthropic SDK client raises a connection error (and counts the attempt);
  * requests.post / requests.get / urllib urlopen refuse every non-loopback URL;
  * sockets refuse every non-loopback connect, and name lookups of non-loopback
    hosts fail. Loopback stays open: a local model server lives there.

The local model itself is a stand-in (`_call_ollama` answers from the test), because a
CI runner has no model to run; everything around it is the real app.
"""
from __future__ import annotations

import copy
import ipaddress
import socket
import urllib.error

import pytest
import requests

from agent_friday import core
from agent_friday.routing.model_router import ModelRouter

LOCAL = "gemma4:e4b"
ASK = "what is on my list for today?"
_LOOP_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0", ""}


def _is_loopback_host(h) -> bool:
    h = (h.decode() if isinstance(h, bytes) else str(h or "")).strip().lower().strip("[]")
    if h in _LOOP_NAMES:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _is_loopback_url(url) -> bool:
    from urllib.parse import urlsplit
    try:
        return _is_loopback_host(urlsplit(str(url)).hostname or "")
    except Exception:
        return False


class _DownAnthropic:
    def __init__(self, attempts):
        class _M:
            def create(self, *a, **k):
                attempts.append(k.get("model"))
                raise ConnectionError("cloud provider unreachable (offline test)")

            def stream(self, *a, **k):
                attempts.append(k.get("model"))
                raise ConnectionError("cloud provider unreachable (offline test)")
        self.messages = _M()


@pytest.fixture
def cloud_down(monkeypatch, patch_app, offline_calls):
    """No cloud provider is reachable; loopback is. Returns what tried to leave.

    Threads the app starts during the test are joined (bounded) at teardown, while the
    cut is still in place, and what they tried is checked too."""
    seen = {"anthropic": [], "http": [], "socket": [], "dns": []}

    # A proxy on loopback would carry cloud traffic through the loopback exception.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)

    import threading
    started = []
    real_start = threading.Thread.start

    def _start(self, *a, **k):
        started.append(self)
        return real_start(self, *a, **k)
    monkeypatch.setattr(threading.Thread, "start", _start)

    patch_app("get_anthropic_client", lambda *a, **k: _DownAnthropic(seen["anthropic"]))

    real_session_request = requests.Session.request

    def _session_request(self, method, url, *a, **k):
        if not _is_loopback_url(url):
            seen["http"].append(str(url))
            raise requests.exceptions.ConnectionError("offline: %s" % url)
        return real_session_request(self, method, url, *a, **k)
    monkeypatch.setattr(requests.Session, "request", _session_request)

    inner_post, inner_get = requests.post, requests.get

    def _post(url, *a, **k):
        if not _is_loopback_url(url):
            seen["http"].append(str(url))
            raise requests.exceptions.ConnectionError("offline: %s" % url)
        return inner_post(url, *a, **k)

    def _get(url, *a, **k):
        if not _is_loopback_url(url):
            seen["http"].append(str(url))
            raise requests.exceptions.ConnectionError("offline: %s" % url)
        return inner_get(url, *a, **k)
    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(requests, "get", _get)

    import urllib.request as _ur
    inner_open = _ur.urlopen

    def _urlopen(req, *a, **k):
        url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
        if not _is_loopback_url(url):
            seen["http"].append(url)
            raise urllib.error.URLError("offline: %s" % url)
        return inner_open(req, *a, **k)
    monkeypatch.setattr(_ur, "urlopen", _urlopen)

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_create = socket.create_connection
    real_gai = socket.getaddrinfo

    def _addr_host(addr):
        return addr[0] if isinstance(addr, tuple) and addr else addr

    def _connect(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback_host(_addr_host(addr)):
            seen["socket"].append(repr(addr))
            raise OSError(101, "Network is unreachable (offline test)")
        return real_connect(self, addr)

    def _connect_ex(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback_host(_addr_host(addr)):
            seen["socket"].append(repr(addr))
            return 101
        return real_connect_ex(self, addr)

    def _create(address, *a, **k):
        if not _is_loopback_host(_addr_host(address)):
            seen["socket"].append(repr(address))
            raise OSError(101, "Network is unreachable (offline test)")
        return real_create(address, *a, **k)

    def _gai(host, *a, **k):
        if not _is_loopback_host(host):
            seen["dns"].append(str(host))
            raise socket.gaierror(socket.EAI_NONAME, "offline test: no name resolution")
        return real_gai(host, *a, **k)
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(socket, "create_connection", _create)
    monkeypatch.setattr(socket, "getaddrinfo", _gai)
    yield seen
    import time as _t
    deadline = _t.monotonic() + 15
    for th in started:
        th.join(timeout=max(0.0, deadline - _t.monotonic()))
    if not seen.get("_cloud_allowed"):
        assert _left(seen) == [], "a background thread tried the network: %r" % _left(seen)
    else:
        assert seen["socket"] == [] and seen["http"] == [] and seen["dns"] == [], _left(seen)


@pytest.fixture
def mode(monkeypatch, patch_app):
    def _set(m, local=(LOCAL,)):
        settings = copy.deepcopy(core.DEFAULT_SETTINGS)
        settings["model_routing"].update({"mode": m, "vault_local_only": False})
        patch_app("_load_settings", lambda: settings)
        patch_app("_index_chat_turn", lambda *a, **k: None)
        monkeypatch.setattr(ModelRouter, "_local_candidates",
                            lambda self: [{"name": n, "size_gb": 4.0} for n in local])
        monkeypatch.setattr(ModelRouter, "_pick_local_model",
                            lambda self, models, tt, mm, preferred=None: models[0]["name"])
        monkeypatch.setattr(ModelRouter, "_is_registry_local",
                            lambda self, x: ":" in str(x or "") and "claude" not in str(x))
        from agent_friday.routing import model_router as rr
        monkeypatch.setattr(rr, "_router_instance", None)
        return settings
    return _set


@pytest.fixture
def local_brain(patch_app):
    calls = []

    def _ollama(messages, *a, **k):
        calls.append(k.get("model"))
        return ("Here is your list, from the local model.", [])
    patch_app("_call_ollama", _ollama)
    return calls


CONTROL_PLANE = ("/api/health", "/api/settings", "/api/setup/status", "/api/onboarding/copy",
                 "/api/notifications", "/api/schedules", "/api/tasks")


def _left(seen) -> list:
    return seen["anthropic"] + seen["http"] + seen["socket"] + seen["dns"]


@pytest.mark.parametrize("routing", ["cloud_only", "local_preferred", "local_only"])
def test_the_control_plane_answers_with_every_cloud_provider_down(client, cloud_down, mode, routing):
    mode(routing)
    for path in CONTROL_PLANE:
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.get_data(as_text=True)[:300])
        assert r.get_json() is not None, path
    assert _left(cloud_down) == [], _left(cloud_down)


@pytest.mark.parametrize("routing", ["local_preferred", "local_only"])
@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/send"])
def test_a_local_mode_turn_is_answered_locally_and_its_record_says_why(
        client, cloud_down, mode, local_brain, routing, path):
    mode(routing)
    from agent_friday.services import conversations as conv
    r = client.post(path, json={"message": ASK, "conversation_id": conv.create()["id"]})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    d = r.get_json()
    assert local_brain, "the local model was never asked"
    assert "local model" in (d.get("response") or (d.get("friday_msg") or {}).get("text") or "")
    assert _left(cloud_down) == [], "something tried the cloud: %r" % _left(cloud_down)
    rec = d.get("route") or (d.get("friday_msg") or {}).get("route")
    assert rec, "the reply carries no route record"
    assert rec["seat"] == "local" and rec["model"] == LOCAL
    assert rec["expected_cost"]["usd"] == 0.0
    assert any(a["seat"] == "cloud" for a in rec["alternatives"])
    assert rec["fallback_policy"]["mode"] == routing
    assert isinstance(rec["latency_ms"], int)
    # The record is persisted with the reply, so it survives a reload.
    assert (d.get("friday_msg") or {}).get("route", {}).get("seat") == "local"


def test_a_cloud_only_turn_with_the_cloud_down_fails_honestly_and_the_app_keeps_working(
        client, cloud_down, mode, local_brain):
    mode("cloud_only")
    cloud_down["_cloud_allowed"] = True     # cloud_only may try the provider client; nothing else
    from agent_friday.services import conversations as conv
    r = client.post("/api/chat", json={"message": ASK, "conversation_id": conv.create()["id"]})
    assert r.status_code < 500, r.get_data(as_text=True)[:400]
    d = r.get_json() or {}
    text = d.get("response") or ""
    assert text, "no words for the owner"
    # cloud_only means the cloud: the local model is not used behind the owner's back.
    assert local_brain == []
    # Nothing beyond the provider client tried the network.
    assert cloud_down["socket"] == [] and cloud_down["http"] == [] and cloud_down["dns"] == []
    for p in CONTROL_PLANE:
        assert client.get(p).status_code == 200, p
