"""A request never inherits another request's context variables.

The pooled server's workers are started once and serve every request. A context
variable set while one request ran (the current turn id is one) must not still be
set when the same worker serves the next request, or a later tool call is filed
under an earlier turn. One worker forces both requests onto the same thread.
"""
from __future__ import annotations

import contextvars
import socket
import threading

from agent_friday.services import pooled_server

_SEEN: contextvars.ContextVar = contextvars.ContextVar("test_seen", default="fresh")


def _app(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if path == "/set":
        _SEEN.set("from-the-previous-request")
    start_response("200 OK", [("Content-Type", "text/plain")])

    def body():
        if path == "/set-in-body":
            _SEEN.set("set-while-streaming")
        yield _SEEN.get().encode()
    return body()


def _get(port, path):
    s = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    try:
        s.sendall(("GET %s HTTP/1.0\r\nHost: x\r\n\r\n" % path).encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.split(b"\r\n\r\n", 1)[1].decode()
    finally:
        s.close()


def _serve():
    srv = pooled_server.make_pooled_server("127.0.0.1", 0, _app, workers=1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_a_variable_set_by_one_request_is_unset_in_the_next_on_the_same_worker():
    srv = _serve()
    try:
        assert _get(srv.server_port, "/set") == "from-the-previous-request"
        assert _get(srv.server_port, "/get") == "fresh"
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_variable_set_while_a_body_streams_is_unset_in_the_next_request():
    srv = _serve()
    try:
        assert _get(srv.server_port, "/set-in-body") == "set-while-streaming"
        assert _get(srv.server_port, "/get") == "fresh"
    finally:
        srv.shutdown()
        srv.server_close()
