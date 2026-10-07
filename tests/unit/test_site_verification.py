"""A provider response, HTTP 200, redirect or wrong marker cannot claim live."""
from types import SimpleNamespace
import threading

import pytest

from agent_friday.services import site_verification as verify


@pytest.fixture(autouse=True)
def public_generation(monkeypatch):
    monkeypatch.setattr(verify.sites_privacy, "require_generation", lambda generation: None)


def operation():
    return {"site_id": "site-example", "operation_id": "op-example", "build_id": "build-example", "output_hash": "hash-example",
            "connection": {"adapter": "github_pages", "repo": "example/site"}, "domain": {"hostname": "www.example.test"}}


def test_http_success_with_wrong_content_is_not_verified(monkeypatch):
    monkeypatch.setattr(verify, "_fetch_marker", lambda _url: {"http_status": 200, "addresses": ["93.184.216.34"], "marker": {}})
    result = verify.verify({"site_id": "site-example"}, operation(), generation=0)
    assert result["status"] == "content_pending" and result["verified"] is False


def test_exact_marker_is_required_for_verified_live(monkeypatch):
    op = operation()
    expected = {key: op[key] for key in ("site_id", "operation_id", "build_id", "output_hash")}
    monkeypatch.setattr(verify, "_fetch_marker", lambda _url: {"http_status": 200, "addresses": ["93.184.216.34"], "marker": expected})
    assert verify.verify({"site_id": "site-example"}, op, generation=0)["status"] == "verified_live"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1"])
def test_private_dns_never_opens_a_socket(monkeypatch, address):
    monkeypatch.setattr(verify.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 443))])
    monkeypatch.setattr(verify.socket, "create_connection", lambda *a, **kw: pytest.fail("Private IP must not be contacted"))
    with pytest.raises(ValueError, match="private"):
        verify._fetch_marker("https://www.example.test/")


def test_verified_dns_address_is_pinned_and_redirect_is_not_followed(monkeypatch):
    calls = []
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, value):
            pass
        def sendall(self, request):
            calls.append(request)
    class Reply:
        status = 302
        def __init__(self, sock):
            pass
        def begin(self):
            pass
    monkeypatch.setattr(verify.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(verify.socket, "create_connection", lambda address, **kw: calls.append(address) or Socket())
    monkeypatch.setattr(verify.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=lambda raw, server_hostname: calls.append(server_hostname) or Socket()))
    monkeypatch.setattr(verify.http.client, "HTTPResponse", Reply)
    result = verify._fetch_marker("https://www.example.test/")
    assert calls[0] == ("93.184.216.34", 443) and calls[1] == "www.example.test"
    assert len([call for call in calls if isinstance(call, tuple)]) == 1
    assert result["http_status"] == 302 and result["marker"] is None
    assert b"Authorization" not in calls[2] and b"Cookie" not in calls[2]


def test_slow_dns_is_bounded_and_cannot_begin_http_later(monkeypatch):
    release = threading.Event()
    completed = threading.Event()
    def resolve(*args, **kwargs):
        release.wait(1)
        completed.set()
        return [(2, 1, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(verify, "DNS_TIMEOUT", .01)
    monkeypatch.setattr(verify.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(verify.socket, "create_connection", lambda *a, **kw: pytest.fail("Expired DNS cannot start HTTP"))
    try:
        with pytest.raises(OSError, match="deadline"):
            verify._fetch_marker("https://www.example.test/")
    finally:
        release.set()
        assert completed.wait(1)


def test_slow_drip_response_is_interrupted_by_whole_http_deadline(monkeypatch):
    closed = threading.Event()
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, seconds):
            pass
        def sendall(self, data):
            pass
        def shutdown(self, mode):
            closed.set()
        def close(self):
            closed.set()
    class Reply:
        status = 200
        def __init__(self, socket):
            pass
        def begin(self):
            pass
        def read(self, size):
            if closed.wait(.2):
                raise OSError("socket deadline")
            return b"{}"
    monkeypatch.setattr(verify, "HTTP_DEADLINE", .01)
    monkeypatch.setattr(verify, "_addresses", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(verify.socket, "create_connection", lambda *a, **kw: Socket())
    monkeypatch.setattr(verify.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=lambda *a, **kw: Socket()))
    monkeypatch.setattr(verify.http.client, "HTTPResponse", Reply)
    with pytest.raises(OSError, match="deadline"):
        verify._fetch_marker("https://www.example.test/")
    assert closed.is_set()


def test_generation_expiring_after_dns_cannot_open_a_socket(monkeypatch):
    current = {"valid": True}
    def require(generation):
        if not current["valid"]:
            raise ValueError("privacy ended")
    def addresses(hostname):
        current["valid"] = False
        return ["93.184.216.34"]
    monkeypatch.setattr(verify.sites_privacy, "require_generation", require)
    monkeypatch.setattr(verify, "_addresses", addresses)
    monkeypatch.setattr(verify.socket, "create_connection", lambda *a, **kw: pytest.fail("Stale DNS cannot start HTTPS"))
    with pytest.raises(ValueError, match="privacy ended"):
        verify.verify({"site_id": "site-example"}, operation(), generation=0)
    assert verify._GENERATION.get() is None


def test_valid_marker_from_an_expired_request_is_not_returned(monkeypatch):
    current = {"valid": True}
    op = operation()
    def require(generation):
        if not current["valid"]:
            raise ValueError("privacy ended")
    def response(url):
        current["valid"] = False
        return {"http_status": 200, "addresses": ["93.184.216.34"],
                "marker": {key: op[key] for key in ("site_id", "operation_id", "build_id", "output_hash")}}
    monkeypatch.setattr(verify.sites_privacy, "require_generation", require)
    monkeypatch.setattr(verify, "_fetch_marker", response)
    with pytest.raises(ValueError, match="privacy ended"):
        verify.verify({"site_id": "site-example"}, op, generation=0)