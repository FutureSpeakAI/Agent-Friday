"""Synthetic stream boundaries; no provider, listener, or real socket is used."""
import threading
import time
from types import SimpleNamespace

import pytest

from agent_friday.services import publish_adapters as adapters, sites_privacy


@pytest.fixture
def wire(monkeypatch):
    state = SimpleNamespace(body=b'{"sha":"synthetic"}', headers={}, status=200,
                            phase=None, sends=[], sockets=[], closed_responses=[], drips=0)
    monkeypatch.setattr(sites_privacy, "require_generation", lambda generation: None)
    class Socket:
        def __init__(self, *args):
            self.closed = threading.Event()
            state.sockets.append(self)
        def settimeout(self, timeout):
            assert timeout > 0
        def connect(self, address):
            assert address == ("192.0.2.10", 443)
        def do_handshake(self):
            drip(self, "handshake")
        def shutdown(self, how):
            self.closed.set()
        def close(self):
            self.closed.set()
    def drip(sock, phase):
        if state.phase == phase:
            # Every byte arrives promptly, but the overall stream never ends.
            proof_deadline = time.monotonic() + .5
            while not sock.closed.wait(.002):
                state.drips += 1
                if time.monotonic() >= proof_deadline:
                    pytest.fail("The absolute cutoff did not close the continuously active stream")
            raise OSError("Synthetic socket cutoff")
    class Response:
        status = 200
        def __init__(self, sock):
            self.sock = sock
            self.status = state.status
        def getheader(self, key, default=None):
            return state.headers.get(key, default)
        def read(self, size):
            drip(self.sock, "body")
            return state.body[:size]
        def close(self):
            state.closed_responses.append(self)
    class Connection:
        def __init__(self, host):
            assert host == "api.github.com"
            self.sock = None
        def request(self, method, target, body=None, headers=None):
            assert self.auto_open == 0
            state.sends.append((method, target, body, headers))
            drip(self.sock, "upload")
        def getresponse(self):
            drip(self.sock, "headers")
            return Response(self.sock)
        def close(self):
            self.sock.close()
    def wrap(raw, *, server_hostname, do_handshake_on_connect):
        assert server_hostname == "api.github.com" and do_handshake_on_connect is False
        return raw
    monkeypatch.setattr(adapters.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.0.2.10", 443))])
    monkeypatch.setattr(adapters.socket, "socket", Socket)
    monkeypatch.setattr(adapters.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=wrap))
    monkeypatch.setattr(adapters.http.client, "HTTPSConnection", Connection)
    return state


def request(*, duration=1, url="https://api.github.com/repos/example/site/git/commits", body=None):
    return adapters._site_http("POST" if body is not None else "GET", url,
        headers={"Authorization": "Bearer synthetic"}, json_body=body,
        deadline=time.monotonic() + duration, generation=3)


def test_json_response_and_header_only_credential_remain_compatible(wire):
    status, body, text = request(body={"message": "publish"})
    assert (status, body, text) == (200, {"sha": "synthetic"}, '{"sha":"synthetic"}')
    method, target, payload, headers = wire.sends[0]
    assert method == "POST" and "synthetic" not in target and b"synthetic" not in payload
    assert headers["Authorization"] == "Bearer synthetic"
    assert headers["Content-Type"] == "application/json" and headers["Accept-Encoding"] == "identity"
    assert all(sock.closed.is_set() for sock in wire.sockets) and len(wire.closed_responses) == 1


@pytest.mark.parametrize("status,body,expected", [(204, b"", None), (404, b'{"message":"missing"}', {"message": "missing"}),
                                                (302, b"redirect", None), (500, b"invalid-json", None)])
def test_empty_error_and_redirect_responses_keep_status_without_following(wire, status, body, expected):
    wire.status, wire.body = status, body
    result = request()
    assert result == (status, expected, body.decode())
    assert len(wire.sends) == 1 and len(wire.closed_responses) == 1


@pytest.mark.parametrize("phase", ["handshake", "upload", "headers", "body"])
def test_absolute_cutoff_interrupts_continuous_bytes_before_inactivity_timeout(wire, phase):
    wire.phase = phase
    started = time.monotonic()
    with pytest.raises(adapters.AdapterError, match="not confirmed|deadline"):
        request(duration=.08)
    assert time.monotonic() - started < 1
    assert wire.drips > 0 and all(sock.closed.is_set() for sock in wire.sockets)
    if phase == "body":
        assert len(wire.closed_responses) == 1


@pytest.mark.parametrize("announced", [True, False])
def test_oversized_body_is_refused_and_closed(wire, monkeypatch, announced):
    monkeypatch.setattr(adapters, "_SITE_RESPONSE_LIMIT", 10)
    wire.body = b"x" * 11
    if announced:
        wire.headers["Content-Length"] = "11"
    with pytest.raises(adapters.AdapterError, match="size limit"):
        request()
    assert len(wire.closed_responses) == 1 and all(sock.closed.is_set() for sock in wire.sockets)


def test_truncated_declared_response_is_not_accepted(wire):
    wire.headers["Content-Length"] = str(len(wire.body) + 1)
    with pytest.raises(adapters.AdapterError, match="incomplete"):
        request()
    assert len(wire.closed_responses) == 1


def test_late_dns_result_cannot_continue_to_tls_or_send(wire, monkeypatch):
    release, ended = threading.Event(), threading.Event()
    def resolve(*args, **kwargs):
        try:
            release.wait(2)
            return [(2, 1, 6, "", ("192.0.2.10", 443))]
        finally:
            ended.set()
    monkeypatch.setattr(adapters.socket, "getaddrinfo", resolve)
    try:
        with pytest.raises(adapters.AdapterError, match="deadline"):
            request(duration=.05)
    finally:
        release.set()
        assert ended.wait(1)
    assert wire.sockets == [] and wire.sends == []


@pytest.mark.parametrize("url", ["http://api.github.com/x", "https://other.example/x", "https://user:secret@api.github.com/x",
                                 "https://api.github.com:8443/x", "https://api.github.com/x#fragment"])
def test_noncanonical_provider_destinations_never_resolve_or_send(wire, monkeypatch, url):
    monkeypatch.setattr(adapters.socket, "getaddrinfo", lambda *a, **k: pytest.fail("Untrusted destination resolved"))
    with pytest.raises(adapters.AdapterError, match="destination"):
        request(url=url)
    assert wire.sends == []


def test_every_subrequest_receives_the_same_absolute_deadline(monkeypatch):
    monkeypatch.setattr(sites_privacy, "require_generation", lambda generation: None)
    seen = []
    monkeypatch.setattr(adapters, "_site_http", lambda *a, **k: seen.append((k["deadline"], k["generation"])) or (204, None, ""))
    deadline = time.monotonic() + 1
    origin = adapters._SITE_GENERATION.set(3)
    token = adapters._SITE_DEADLINE.set(deadline)
    try:
        adapters._http("GET", "https://api.github.com/x")
        adapters._http("PUT", "https://api.github.com/y", json_body={"enabled": True})
    finally:
        adapters._SITE_DEADLINE.reset(token)
        adapters._SITE_GENERATION.reset(origin)
    assert seen == [(deadline, 3), (deadline, 3)]
