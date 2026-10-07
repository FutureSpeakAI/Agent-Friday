"""SSE timeouts retain their cause even at the socket/clock boundary."""
import json
from types import SimpleNamespace

import pytest

from agent_friday import mcp_client as mc


ANSWER = {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}


def _stream(monkeypatch, chunks, *, error=None, elapsed=1.995):
    clock = {"now": 100.0}
    pending = list(chunks)

    class Response:
        headers = {"Content-Type": "text/event-stream"}
        status = 200
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def read1(self, size):
            assert size == mc._READ_CHUNK
            if pending:
                return pending.pop(0)
            clock["now"] = 100.0 + elapsed
            if error is not None:
                raise error
            return b""

    response = Response()
    monkeypatch.setattr(mc, "time", SimpleNamespace(time=lambda: clock["now"]))
    monkeypatch.setattr(mc.urllib.request, "urlopen", lambda *a, **k: response)
    server = mc.MCPServerHTTP("example", "http://127.0.0.1:1/mcp")
    monkeypatch.setattr(server, "_bearer", lambda: None)
    return server, response


def _post(server):
    return server._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
                        timeout=2.0, want_id=1)


@pytest.mark.parametrize("error_type", [TimeoutError, OSError])
@pytest.mark.parametrize("partial_event", [False, True], ids=["no-event", "pending-event"])
def test_early_socket_timeout_is_not_eof_or_a_completed_event(monkeypatch,
                                                            error_type, partial_event):
    # A socket timeout five milliseconds before the deadline must remain a
    # timeout. A pending data line without an event boundary cannot flush as
    # though EOF were received and accidentally become a successful reply.
    chunks = [("data: " + json.dumps(ANSWER) + "\n").encode()] if partial_event else []
    server, response = _stream(monkeypatch, chunks, error=error_type("timed out"))
    with pytest.raises(TimeoutError, match="took longer than 2s"):
        _post(server)
    assert response.closed


def test_real_eof_before_the_deadline_remains_a_missing_response(monkeypatch):
    server, response = _stream(monkeypatch, [])
    with pytest.raises(TimeoutError, match="SSE stream ended without a response to id 1"):
        _post(server)
    assert response.closed


def test_real_eof_still_flushes_a_valid_final_data_line(monkeypatch):
    server, response = _stream(monkeypatch, [("data: " + json.dumps(ANSWER) + "\n").encode()])
    assert _post(server) == ANSWER
    assert response.closed


def test_unrelated_io_error_keeps_its_original_cause(monkeypatch):
    error = OSError("connection reset")
    server, response = _stream(monkeypatch, [], error=error, elapsed=1.0)
    with pytest.raises(OSError) as caught:
        _post(server)
    assert caught.value is error
    assert response.closed

