"""Friday's own front door (services/local_proxy): TLS on agent.<name>, piped to
Friday unchanged.

Run on ephemeral loopback ports against a tiny stand-in server, with a
certificate from a throwaway authority that only this test trusts. Nothing
listens on 443 or 80 and nothing is installed.
"""
from __future__ import annotations

import http.client
import http.server
import socket
import ssl
import threading
import time

import pytest

from agent_friday.services import local_ca, local_proxy

HOST = "agent.pwtest"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(3):
                data = f"data: {i}\n\n".encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"0\r\n\r\n")
            return
        body = (f"host={self.headers.get('Host')} peer={self.client_address[0]} "
                f"xff={self.headers.get('X-Forwarded-For')}").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def upstream():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.fixture
def certs(tmp_path):
    local_ca.ensure(tmp_path, HOST)
    return tmp_path


def _proxy(upstream_port, certs, redirect=False, **kw):
    p = local_proxy.LocalProxy(
        host=HOST, upstream_port=upstream_port, https_port=kw.pop("https_port", _free_port()),
        http_port=kw.pop("http_port", _free_port()),
        cert_file=str(certs / local_ca.LEAF_CERT), key_file=str(certs / local_ca.LEAF_KEY),
        redirect_http=lambda: redirect, addrs=("127.0.0.1",), **kw)
    p.start()
    return p


def _https_get(port, certs, path="/x", host=HOST):
    ctx = ssl.create_default_context(cafile=str(certs / local_ca.CA_CERT))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ctx)
    # SNI and certificate check against the NAME, while connecting to loopback
    conn.sock = ctx.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=10),
                                server_hostname=host)
    conn.request("GET", path, headers={"Host": host})
    r = conn.getresponse()
    return r.status, r.read().decode(), dict(r.getheaders())


def _http_get(port, path="/x", host=HOST):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Host": host})
    r = conn.getresponse()
    return r.status, r.read().decode(), dict(r.getheaders())


def test_https_reaches_friday_as_a_plain_local_request(upstream, certs):
    p = _proxy(upstream, certs)
    try:
        status, body, _ = _https_get(p.https_port, certs)
        assert status == 200
        # Friday sees what a direct loopback request would show it: the
        # browser's Host, a loopback peer, and no forwarding header to explain.
        assert body == f"host={HOST} peer=127.0.0.1 xff=None"
        st = p.status()
        assert st["https"]["listening"] and st["http"]["listening"]
    finally:
        p.stop()


def test_streamed_replies_arrive_whole(upstream, certs):
    p = _proxy(upstream, certs)
    try:
        status, body, _ = _https_get(p.https_port, certs, "/stream")
        assert status == 200
        assert body == "data: 0\n\ndata: 1\n\ndata: 2\n\n"
    finally:
        p.stop()


def test_plain_http_is_relayed_until_the_secure_address_is_trusted(upstream, certs):
    p = _proxy(upstream, certs, redirect=False)
    try:
        status, body, _ = _http_get(p.http_port)
        assert status == 200 and body.startswith(f"host={HOST} ")
    finally:
        p.stop()


def test_plain_http_goes_to_https_once_trusted_and_only_temporarily(upstream, certs):
    p = _proxy(upstream, certs, redirect=True)
    try:
        status, _body, headers = _http_get(p.http_port, "/w/news?tab=x")
        assert status == 307       # not 301/308: a browser would remember those
        assert headers["Location"] == f"https://{HOST}:{p.https_port}/w/news?tab=x"
        assert headers.get("Cache-Control") == "no-store"
        # another name on the same port is not redirected, just relayed
        status, body, _ = _http_get(p.http_port, host="localhost")
        assert status == 200 and body.startswith("host=localhost ")
    finally:
        p.stop()


@pytest.mark.parametrize("redirect", [False, True])
def test_plain_http_refuses_any_other_websites_name(upstream, certs, redirect):
    """A page on another site that points its own name at this PC (DNS
    rebinding) must not reach Friday through the plain port."""
    p = _proxy(upstream, certs, redirect=redirect)
    try:
        status, body, _ = _http_get(p.http_port, host="evil.example")
        assert status == 421 and "Friday's own address only" in body
        status, _, _ = _http_get(p.http_port, host=f"evil.example:{p.http_port}")
        assert status == 421
        status, body, _ = _http_get(p.http_port, host=f"[::1]:{p.http_port}")
        assert status == 200 and body.startswith("host=[::1]")
    finally:
        p.stop()


def test_bare_host():
    assert local_proxy.bare_host("Agent.Friday:80") == "agent.friday"
    assert local_proxy.bare_host("[::1]:3280") == "[::1]"
    assert local_proxy.bare_host("localhost") == "localhost"


def test_a_busy_port_is_reported_not_fatal(upstream, certs):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    try:
        p = _proxy(upstream, certs, https_port=busy)
        try:
            st = p.status()
            assert st["https"]["listening"] is False
            reason = st["https"]["addrs"]["127.0.0.1"]
            assert f"port {busy}" in reason
            assert st["http"]["listening"] is True          # the other one still opened
        finally:
            p.stop()
    finally:
        blocker.close()


def test_a_client_that_never_finishes_its_handshake_blocks_nobody(upstream, certs):
    p = _proxy(upstream, certs)
    try:
        silent = socket.create_connection(("127.0.0.1", p.https_port), timeout=5)
        try:
            t = time.monotonic()
            status, _, _ = _https_get(p.https_port, certs)
            assert status == 200 and time.monotonic() - t < 5
        finally:
            silent.close()
    finally:
        p.stop()


def test_friday_not_answering_is_said_plainly(certs, monkeypatch):
    monkeypatch.setattr(local_proxy, "UPSTREAM_WAIT_S", 0.3)
    p = _proxy(_free_port(), certs)
    try:
        status, body, _ = _https_get(p.https_port, certs)
        assert status == 502 and "Friday is not answering" in body
    finally:
        p.stop()


def test_it_listens_on_loopback_only(upstream, certs):
    p = _proxy(upstream, certs)
    try:
        assert set(p.status()["https"]["addrs"]) == {"127.0.0.1"}
        assert local_proxy.LocalProxy(host=HOST, upstream_port=1).addrs == ("127.0.0.1", "::1")
    finally:
        p.stop()
