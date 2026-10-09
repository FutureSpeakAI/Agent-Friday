"""web_safety: the SSRF guard and the host comparisons built on it.

No test here touches the network. DNS is replaced by a stub where a hostname
has to resolve, and `requests.get` is replaced wherever a fetch would happen,
so a guard that failed open shows up as a recorded call rather than a request.
"""
import pytest

from agent_friday.services import web_safety as ws


# ── check_url ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:3000/api/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://100.64.0.1/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://0.0.0.0/",
    "http://localhost/",
    "http://printer.local/",
    "http://thing.internal/",
])
def test_check_url_refuses_this_machine_and_its_network(url):
    ok, why = ws.check_url(url)
    assert not ok, why


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "gopher://example.com/", "ftp://example.com/", "",
    "http://user:pw@example.com/",  # pragma: allowlist secret
])
def test_check_url_refuses_other_schemes_credentials_and_nothing(url):
    assert not ws.check_url(url)[0]


def test_check_url_allows_a_public_ip_literal():
    assert ws.check_url("https://93.184.215.14/") == (True, "ok")


def test_a_host_with_one_private_record_is_refused(monkeypatch):
    monkeypatch.setattr(ws, "resolve_all", lambda h: ["93.184.215.14", "127.0.0.1"])
    ok, why = ws.check_url("https://mixed.example/")
    assert not ok and "127.0.0.1" in why


def test_a_host_with_only_public_records_is_allowed(monkeypatch):
    monkeypatch.setattr(ws, "resolve_all", lambda h: ["93.184.215.14"])
    assert ws.check_url("https://public.example/")[0]


def test_an_unresolvable_host_is_refused(monkeypatch):
    def _fail(h):
        raise OSError("no such host")
    monkeypatch.setattr(ws, "resolve_all", _fail)
    assert not ws.check_url("https://nowhere.example/")[0]


# ── safe_get ─────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}


def _stub_get(monkeypatch, responses):
    import requests
    calls = []

    def _get(url, **kw):
        calls.append((url, kw))
        return responses.pop(0)
    monkeypatch.setattr(requests, "get", _get)
    return calls


def test_safe_get_never_requests_a_refused_url(monkeypatch):
    calls = _stub_get(monkeypatch, [_Resp(200)])
    with pytest.raises(ws.UnsafeURLError):
        ws.safe_get("http://127.0.0.1:3000/api/settings")
    assert calls == []


def test_safe_get_refuses_a_redirect_into_loopback(monkeypatch):
    monkeypatch.setattr(ws, "resolve_all", lambda h: ["93.184.215.14"])
    calls = _stub_get(monkeypatch, [_Resp(302, "http://127.0.0.1:3000/")])
    with pytest.raises(ws.UnsafeURLError, match="redirect"):
        ws.safe_get("https://public.example/story")
    assert [c[0] for c in calls] == ["https://public.example/story"]
    assert calls[0][1]["allow_redirects"] is False


def test_safe_get_follows_a_public_redirect(monkeypatch):
    monkeypatch.setattr(ws, "resolve_all", lambda h: ["93.184.215.14"])
    final = _Resp(200)
    calls = _stub_get(monkeypatch, [_Resp(301, "/moved"), final])
    assert ws.safe_get("https://public.example/story") is final
    assert [c[0] for c in calls] == ["https://public.example/story",
                                     "https://public.example/moved"]


def test_safe_get_stops_an_endless_redirect(monkeypatch):
    monkeypatch.setattr(ws, "resolve_all", lambda h: ["93.184.215.14"])
    _stub_get(monkeypatch, [_Resp(302, "/again") for _ in range(ws.MAX_REDIRECT_HOPS + 2)])
    with pytest.raises(ws.UnsafeURLError, match="too many redirects"):
        ws.safe_get("https://public.example/loop")


# ── host comparisons ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("host,expected", [
    ("duckduckgo.com", True),
    ("html.duckduckgo.com", True),
    ("DuckDuckGo.com.", True),
    ("evilduckduckgo.com", False),
    ("duckduckgo.com.evil.example", False),
    ("", False),
])
def test_hostname_matches(host, expected):
    assert ws.hostname_matches(host, "duckduckgo.com") is expected


@pytest.mark.parametrize("url,expected", [
    ("https://news.google.com/rss/articles/abc", True),
    ("//news.google.com/x", True),
    ("https://example.com/?next=news.google.com", False),
    ("https://news.google.com.evil.example/", False),
    ("not a url", False),
])
def test_url_host_matches(url, expected):
    assert ws.url_host_matches(url, "news.google.com") is expected


# ── check_peer_endpoint ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://192.168.1.20:3000", "https://peer.example", "http://localhost:19998",
])
def test_peer_endpoints_on_the_lan_are_allowed(url):
    assert ws.check_peer_endpoint(url) == (True, "ok")


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/win.ini", "ftp://peer.example", "not-a-url", "",
    "http://user:pw@peer.example",  # pragma: allowlist secret
    "http://",
])
def test_peer_endpoints_that_are_not_plain_http_are_refused(url):
    assert not ws.check_peer_endpoint(url)[0]


# ── pinning: the judged address is the dialled address ───────────────────────

@pytest.fixture
def rebinding_resolver(monkeypatch):
    """A system resolver that answers every host name with a local listener
    (what a rebinding DNS server does on the second lookup) and records the
    names it was asked about. The vetting step is told the name is public."""
    import http.server
    import socket
    import threading

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b"reached-the-listener"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    asked: list[str] = []
    real = socket.getaddrinfo

    def _gai(host, p, *a, **k):
        asked.append(str(host))
        return real("127.0.0.1", p, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", _gai)
    monkeypatch.setattr(ws, "resolve_all", lambda h: ["93.184.215.14"])
    yield port, asked
    srv.shutdown()


def test_without_pinning_the_client_would_resolve_again(rebinding_resolver):
    import requests
    port, asked = rebinding_resolver
    r = requests.get(f"http://rebind.example:{port}/", timeout=5)
    assert r.text == "reached-the-listener" and "rebind.example" in asked


def test_safe_get_dials_the_judged_address_not_a_second_lookup(rebinding_resolver):
    port, asked = rebinding_resolver
    r = ws.safe_get(f"http://rebind.example:{port}/")
    assert r.status_code == 200
    assert "rebind.example" not in asked
    assert asked == ["93.184.215.14"]


def test_pinned_does_not_leak_to_the_next_request(rebinding_resolver):
    import requests
    port, asked = rebinding_resolver
    with ws.pinned(f"http://rebind.example:{port}/"):
        pass
    requests.get(f"http://other.example:{port}/", timeout=5)
    assert asked == ["other.example"]


def test_pinned_refuses_a_name_with_a_private_record(monkeypatch):
    monkeypatch.setattr(ws, "resolve_all", lambda h: ["93.184.215.14", "169.254.169.254"])
    with pytest.raises(ws.UnsafeURLError):
        with ws.pinned("https://mixed.example/"):
            raise AssertionError("must not run")


def test_pinned_is_thread_local(rebinding_resolver):
    import threading
    import requests
    port, asked = rebinding_resolver
    seen = []

    def _other():
        requests.get(f"http://rebind.example:{port}/", timeout=5)
        seen.append(list(asked))

    with ws.pinned(f"http://rebind.example:{port}/"):
        t = threading.Thread(target=_other)
        t.start()
        t.join()
    assert "rebind.example" in seen[0]
