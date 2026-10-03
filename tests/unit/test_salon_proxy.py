"""The salon's key-injecting proxy (salon spec §4.7 "The proxy injects the key").

A coding agent in the box is pointed at the proxy with a dummy key. The proxy
swaps the dummy for the real key header, only on requests to the provider's
API, relays the answer (streamed or not), and records host, path and status
of every request, never a body and never a key. Anything else is refused.
"""
from __future__ import annotations

import http.server
import json
import socketserver
import threading
import time
import urllib.error
import urllib.request

import pytest

from agent_friday.services import salon_proxy as sp

REAL = "sk-ant-real-000000000000000"  # pragma: allowlist secret
DUMMY = "friday-proxy-dummy"


class _Upstream(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        _Upstream.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body.decode()})
        if self.path.endswith("/stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            for i in range(3):
                chunk = ("event: ping\ndata: {\"n\": %d}\n\n" % i).encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk)); self.wfile.flush(); time.sleep(0.05)
            self.wfile.write(b"0\r\n\r\n"); return
        out = json.dumps({"ok": True, "got_key": self.headers.get("x-api-key") == REAL, "echo": json.loads(body or b"{}")}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def do_GET(self):
        _Upstream.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
        out = b'{"models": []}'
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


@pytest.fixture
def upstream():
    _Upstream.seen = []
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Upstream)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture
def proxy(upstream):
    px = sp.KeyProxy(upstream=upstream, key_provider=lambda: REAL)
    px.start()
    yield px
    px.stop()


def _call(url, method="POST", body=None, headers=None):
    req = urllib.request.Request(url, data=(json.dumps(body).encode() if body is not None else None), method=method,
                                 headers=dict({"Content-Type": "application/json", "x-api-key": DUMMY, "anthropic-version": "2023-06-01"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_the_proxy_swaps_the_dummy_for_the_real_key_and_relays_the_answer(proxy):
    assert proxy.url.startswith("http://127.0.0.1:")
    status, body, _ = _call(proxy.url + "/v1/messages", body={"model": "claude-opus-5-5", "messages": []})
    assert status == 200
    out = json.loads(body)
    assert out["got_key"] is True and out["echo"]["model"] == "claude-opus-5-5"
    seen = _Upstream.seen[-1]
    assert seen["headers"]["x-api-key"] == REAL and DUMMY not in json.dumps(seen)
    assert seen["headers"].get("anthropic-version") == "2023-06-01"


def test_only_the_provider_api_paths_pass_and_nothing_else_reaches_upstream(proxy):
    before = len(_Upstream.seen)
    status, body, _ = _call(proxy.url + "/admin/keys", body={})
    assert status == 403 and b"refused" in body.lower()
    status, _, _ = _call(proxy.url + "/v1/messages", method="DELETE")
    assert status == 403
    assert len(_Upstream.seen) == before
    status, _, _ = _call(proxy.url + "/v1/models", method="GET")
    assert status == 200


def test_a_streamed_answer_arrives_as_it_is_sent(proxy):
    req = urllib.request.Request(proxy.url + "/v1/messages/stream", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "x-api-key": DUMMY})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.headers.get("Content-Type", "").startswith("text/event-stream")
        data = r.read().decode()
    assert data.count("event: ping") == 3 and '"n": 2' in data


def test_the_capture_names_hosts_paths_and_status_and_never_a_key_or_a_body(proxy):
    _call(proxy.url + "/v1/messages", body={"secret_prompt": "do not leak"})
    _call(proxy.url + "/nope", body={})
    cap = proxy.capture()
    assert [c["path"] for c in cap][-2:] == ["/v1/messages", "/nope"]
    assert cap[-2]["status"] == 200 and cap[-1]["status"] == 403 and cap[-1]["refused"] is True
    dump = json.dumps(cap)
    assert REAL not in dump and DUMMY not in dump and "do not leak" not in dump
    assert cap[-2]["host"] and cap[-2]["method"] == "POST" and cap[-2]["ms"] >= 0
    s = proxy.summary()
    assert s["requests"] == cap.__len__() and s["refused"] >= 1 and all("host" in x for x in s["hosts"])


def test_the_proxy_listens_on_loopback_only_and_stops_cleanly(upstream):
    px = sp.KeyProxy(upstream=upstream, key_provider=lambda: REAL)
    px.start()
    host, port = px.address
    assert host == "127.0.0.1" and port > 0
    px.stop()
    with pytest.raises(Exception):
        urllib.request.urlopen(px.url + "/v1/models", timeout=2)


def test_no_key_available_means_a_plain_refusal_not_a_call(upstream):
    px = sp.KeyProxy(upstream=upstream, key_provider=lambda: None)
    px.start()
    try:
        status, body, _ = _call(px.url + "/v1/messages", body={})
        assert status == 401 and b"no key" in body.lower()
        assert not _Upstream.seen
    finally:
        px.stop()
