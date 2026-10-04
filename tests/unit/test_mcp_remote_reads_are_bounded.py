"""A remote MCP server cannot make Friday read without limit.

`MCPServerHTTP._post` did `resp.read()` with no size cap, and its timeout was per socket
operation, so a server that answered with gigabytes, advertised a huge Content-Length, or
trickled bytes forever kept the call (and memory) going. The reply is now capped in size and
in total time, and an over-limit reply fails visibly: the tool result reads
"[mcp:<name> error] ...too large..." or "...took longer than...". A real local server on
127.0.0.1 is used; no other network is touched.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_friday import mcp_client as mc

OK = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "hi"}]}}


class _H(BaseHTTPRequestHandler):
    mode = "ok"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _begin(self, ctype, length=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        m = type(self).mode
        w = self.wfile
        try:
            if m == "ok":
                b = json.dumps(OK).encode()
                self._begin("application/json", len(b)); w.write(b)
            elif m == "ok_sse":
                b = ("data: " + json.dumps(OK) + "\n\n").encode()
                self._begin("text/event-stream"); w.write(b)
            elif m == "huge_body":                        # 9 MiB of valid JSON, honestly framed
                pad = "x" * (9 * 1024 * 1024)
                b = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"text": pad}}).encode()
                self._begin("application/json", len(b)); w.write(b)
            elif m == "lying_length":                     # promises 1 GiB, sends a little, then stalls
                self._begin("application/json", 1 << 30); w.write(b'{"jsonrpc":"2.0",'); w.flush(); time.sleep(6)
            elif m == "trickle":                          # valid JSON, one byte at a time, ~7 s in all
                b = json.dumps(OK).encode() + b" " * 20
                self._begin("application/json")
                for ch in b:
                    w.write(bytes([ch])); w.flush(); time.sleep(0.35)
            elif m == "sse_long_line":                    # 3 MiB with no newline
                self._begin("text/event-stream")
                w.write(b"data: " + b"x" * (3 * 1024 * 1024))
            elif m == "sse_trickle":                      # a line that never ends, one byte at a time
                self._begin("text/event-stream")
                for _ in range(40):
                    w.write(b"d"); w.flush(); time.sleep(0.3)
            elif m == "sse_endless_events":               # events that never answer our id, ~12 MiB
                self._begin("text/event-stream")
                chunk = ("data: " + json.dumps({"jsonrpc": "2.0", "method": "n", "params": {"p": "y" * 60000}}) + "\n\n").encode()
                for _ in range(200):
                    w.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def _server(mode):
    _H.mode = mode
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    srv = mc.MCPServerHTTP("probe", "http://127.0.0.1:%d/mcp" % httpd.server_address[1])
    return httpd, srv


def _post(mode, timeout=3.0):
    httpd, srv = _server(mode)
    t0 = time.time()
    try:
        try:
            return srv._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call"}, timeout, want_id=1), None, time.time() - t0
        except Exception as e:  # noqa: BLE001
            return None, e, time.time() - t0
    finally:
        httpd.shutdown(); httpd.server_close()


def test_a_normal_reply_still_works_in_both_framings():
    msg, err, _ = _post("ok")
    assert err is None and msg["result"]["content"][0]["text"] == "hi"
    msg, err, _ = _post("ok_sse")
    assert err is None and msg["id"] == 1


def test_a_reply_over_the_size_cap_fails_visibly():
    msg, err, _ = _post("huge_body", timeout=20)
    assert msg is None and err is not None, "a 9 MiB reply was read in full"
    assert "too large" in str(err), err


def test_a_content_length_over_the_cap_is_refused_before_it_is_read():
    msg, err, took = _post("lying_length", timeout=20)
    assert msg is None and "too large" in str(err), err
    assert took < 4, "it waited for a body that was never going to be accepted (%.1fs)" % took


def test_a_reply_that_trickles_past_the_time_limit_fails_visibly():
    msg, err, took = _post("trickle", timeout=2.0)
    assert msg is None and isinstance(err, TimeoutError) and "took longer" in str(err), (err, took)
    assert took < 4.5, took


def test_an_event_stream_line_over_the_cap_fails_visibly():
    msg, err, _ = _post("sse_long_line", timeout=20)
    assert msg is None and "too large" in str(err), err


def test_an_event_stream_that_trickles_past_the_time_limit_fails_visibly():
    msg, err, took = _post("sse_trickle", timeout=2.0)
    assert msg is None and isinstance(err, TimeoutError) and "took longer" in str(err), (err, took)
    assert took < 4.5, took


def test_an_event_stream_that_never_answers_stops_at_the_cap():
    msg, err, _ = _post("sse_endless_events", timeout=20)
    assert msg is None and "too large" in str(err), err


def test_the_caller_sees_the_failure_as_a_tool_result():
    httpd, srv = _server("huge_body")
    try:
        srv.status = "ready"
        out = srv.call_tool("anything", {}, timeout=20)
    finally:
        httpd.shutdown(); httpd.server_close()
    assert out.startswith("[mcp:probe error]") and "too large" in out, out[:200]
