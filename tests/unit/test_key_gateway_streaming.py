"""A streamed answer passes through the key gateway as it arrives (the chat leg streams tokens as server-sent events).

Rule guarded here: the gateway hands each piece of a provider's answer to the caller as soon as it is read, not when
8 KB have collected or the answer has ended; otherwise every streamed reply would stall until it finished.
"""
import http.server
import threading
import time

import pytest
import requests

from agent_friday.services import key_gateway as gw

REAL = "sk-stream-real-key-for-tests"  # pragma: allowlist secret
HOLD_SECONDS = 2.0


class SlowStream:
    """A provider that sends one event, waits, then sends the next, noting when the second went out."""

    def __init__(self):
        self.second_sent_at = None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self._chunk(b'data: {"n": 1}\n\n')
                time.sleep(HOLD_SECONDS)
                outer.second_sent_at = time.monotonic()
                self._chunk(b'data: {"n": 2}\n\n')
                self.wfile.write(b"0\r\n\r\n")

            def _chunk(self, data):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = "http://127.0.0.1:%d" % self.server.server_address[1]


@pytest.fixture
def stream(monkeypatch):
    fake = SlowStream()
    monkeypatch.setenv("TEST_STREAM_KEY", REAL)
    monkeypatch.setenv("NO_PROXY", "example.com")   # registered so the test's change to it is undone
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(gw, "_is_cloud_endpoint", lambda address: True)
    gw.stop_all()
    yield fake
    gw.stop_all()
    fake.server.shutdown()


def test_the_first_event_reaches_the_caller_before_the_second_is_sent(stream):
    base = gw.descriptor_url({"name": "streamco", "base_url": stream.origin + "/v1",
                              "auth": {"type": "env_var", "key": "TEST_STREAM_KEY"}})
    with requests.post(base + "/chat/completions", json={"stream": True}, stream=True, timeout=20) as answer:
        lines = answer.iter_lines()
        first = next(line for line in lines if line)
        first_at = time.monotonic()
        second = next(line for line in lines if line)
    assert first == b'data: {"n": 1}' and second == b'data: {"n": 2}'
    assert first_at < stream.second_sent_at, "the first event waited for the second"
