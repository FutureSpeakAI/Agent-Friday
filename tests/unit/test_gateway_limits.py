"""The gateway's doors have limits: body size, JSON depth, connections, and a request rate for paired devices
(threat assessment 2026-11-21, fix 3).

Before: any machine on the home network, with no session at all, could post a body of any size to pair/start,
send JSON nested thousands deep, open hundreds of connections and hold them half-finished (the TLS handshake
ran on the accepting thread, so one silent connection stopped every later one), and a paired device had no rate
limit at all. Only the unauthenticated paths had a 30-a-minute limit.

Now: a request body over 16 KB on an open path, or 2 MB on any other JSON path, is refused 413 before any
handler reads it (the TV's audio and channel uploads keep their own, larger, checks); JSON nested deeper than 32
is read as "no JSON"; the server serves at most 16 connections per address and 96 in all and closes one idle for
20 seconds; the handshake no longer blocks the accept loop; a paired device gets 600 requests a minute and then
429 with Retry-After.
"""
import io
import socket
import ssl
import threading
import time

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import tls
from tests.unit.test_tv_gateway import FakeTv, auth, open_session


def bucket(rate: int):
    """A request-rate bucket of ``rate`` a minute (the old, rate-less bucket on a tree that has none)."""
    try:
        return gateway._Bucket(rate)
    except TypeError:
        return gateway._Bucket()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(gateway, "_AUTH_BUCKET", bucket(getattr(gateway, "_AUTH_RATE", 600)), raising=False)
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


@pytest.fixture
def session(client):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Living room TV")
    return open_session(client, tv), tv


# ── body size ────────────────────────────────────────────────────────────────

def test_an_open_path_refuses_a_big_body_before_any_handler_reads_it(client):
    big = b'{"name": "' + b"x" * (gateway.MAX_OPEN_BODY_BYTES + 1) + b'"}'
    refused = client.post("/tv/v1/pair/start", data=big, content_type="application/json")
    assert refused.status_code == 413 and refused.get_json()["code"] == "body_too_large"


def test_a_session_path_refuses_a_body_over_the_json_limit(client, session):
    token, _ = session
    big = b'{"text": "' + b"x" * (gateway.MAX_JSON_BODY_BYTES + 1) + b'"}'
    refused = client.post("/tv/v1/messages", data=big, content_type="application/json", headers=auth(token))
    assert refused.status_code == 413 and refused.get_json()["code"] == "body_too_large"


def test_a_chunked_body_with_no_length_is_cut_off_at_the_same_limit(client):
    big = b'{"name": "' + b"x" * (gateway.MAX_OPEN_BODY_BYTES * 4) + b'"}'
    refused = client.post("/tv/v1/pair/start", input_stream=io.BytesIO(big), content_type="application/json",
                          environ_overrides={"HTTP_TRANSFER_ENCODING": "chunked", "wsgi.input_terminated": True})
    assert refused.status_code == 413


def test_the_uploads_that_check_their_own_size_are_not_cut_by_the_json_limit():
    assert "/tv/v1/stt" in gateway.RAW_BODY_PATHS and "/tv/v1/channels" in gateway.RAW_BODY_PATHS


def test_an_ordinary_small_request_is_untouched(client, session):
    token, _ = session
    assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 200


# ── JSON depth ───────────────────────────────────────────────────────────────

def test_json_nested_deeper_than_the_limit_is_read_as_no_json():
    app = gateway.create_app()
    ok = "[" * gateway.MAX_JSON_DEPTH + "]" * gateway.MAX_JSON_DEPTH
    deep = "[" * (gateway.MAX_JSON_DEPTH + 1) + "]" * (gateway.MAX_JSON_DEPTH + 1)
    assert app.json.loads(ok) is not None
    with pytest.raises(ValueError):
        app.json.loads(deep)


def test_brackets_inside_a_string_are_not_depth():
    app = gateway.create_app()
    assert app.json.loads('{"t": "' + "[" * 500 + '"}') == {"t": "[" * 500}


def test_a_very_deep_body_gets_an_answer_not_a_crash(client):
    deep = b'{"a":' * 9000 + b"1" + b"}" * 9000
    answer = client.post("/tv/v1/pair/start", data=deep, content_type="application/json")
    assert answer.status_code in (400, 409, 413)


# ── request rate ─────────────────────────────────────────────────────────────

def test_a_paired_device_that_asks_too_often_is_told_to_wait(client, session, monkeypatch):
    token, _ = session
    monkeypatch.setattr(gateway, "_AUTH_BUCKET", bucket(5))
    for _ in range(5):
        assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 200
    slow = client.get("/tv/v1/whoami", headers=auth(token))
    assert slow.status_code == 429 and int(slow.headers["Retry-After"]) >= 1


def test_the_rate_is_per_device_so_one_device_cannot_use_up_another(client, session, monkeypatch):
    token, _ = session
    other = FakeTv()
    device_pairing.add_device(other.spki, name="Bedroom TV")
    other_token = open_session(client, other)
    monkeypatch.setattr(gateway, "_AUTH_BUCKET", bucket(3))
    for _ in range(4):
        client.get("/tv/v1/whoami", headers=auth(token))
    assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 429
    assert client.get("/tv/v1/whoami", headers=auth(other_token)).status_code == 200


def test_the_default_rate_leaves_room_for_an_app_that_polls(client, session):
    token, _ = session
    for _ in range(100):
        assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 200


def test_the_open_path_limit_also_says_when_to_come_back(client, monkeypatch):
    monkeypatch.setattr(gateway, "_BUCKET", bucket(2))
    for _ in range(2):
        client.get("/tv/v1/hello")
    slow = client.get("/tv/v1/hello")
    assert slow.status_code == 429 and int(slow.headers["Retry-After"]) >= 1


# ── connections ──────────────────────────────────────────────────────────────

def serve(monkeypatch, ssl_context=None, **limits):
    for name, value in limits.items():
        monkeypatch.setattr(gateway, name, value)
    srv = gateway.make_bounded_server("127.0.0.1", 0, gateway.create_app(), ssl_context=ssl_context)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def closed_by_server(sock, within=3.0) -> bool:
    sock.settimeout(within)
    try:
        first = sock.recv(16)
    except (socket.timeout, ConnectionError):
        return False
    return first == b"" or first.startswith(b"HTTP/1.0 503")      # closed, or told "busy" and closed


def test_one_address_cannot_hold_more_than_its_share_of_connections(monkeypatch):
    srv = serve(monkeypatch, MAX_PER_SOURCE=3)
    try:
        port = srv.server_address[1]
        held = [socket.create_connection(("127.0.0.1", port)) for _ in range(3)]
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", port))
        assert closed_by_server(extra)                     # refused at once, not queued behind the three
        held[0].close()
        time.sleep(0.5)
        again = socket.create_connection(("127.0.0.1", port))
        again.sendall(b"GET /tv/v1/hello HTTP/1.0\r\n\r\n")
        again.settimeout(3)
        assert b"200" in again.recv(64)                    # a freed place is usable again
        for s in held[1:] + [again]:
            s.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_all_addresses_together_stop_at_the_total(monkeypatch):
    srv = serve(monkeypatch, MAX_PER_SOURCE=50, MAX_CONNECTIONS=4)
    try:
        port = srv.server_address[1]
        held = [socket.create_connection(("127.0.0.1", port)) for _ in range(4)]
        time.sleep(0.3)
        assert closed_by_server(socket.create_connection(("127.0.0.1", port)))
        for s in held:
            s.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_connection_that_says_nothing_is_closed_after_the_idle_time(monkeypatch):
    srv = serve(monkeypatch, IDLE_TIMEOUT_S=1)
    try:
        silent = socket.create_connection(("127.0.0.1", srv.server_address[1]))
        assert closed_by_server(silent, within=4.0)
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_silent_connection_does_not_stop_the_next_tls_client(monkeypatch):
    srv = serve(monkeypatch, ssl_context=tls.server_context())
    try:
        port = srv.server_address[1]
        stalled = socket.create_connection(("127.0.0.1", port))      # connects, never starts the handshake
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.minimum_version = ssl.TLSVersion.TLSv1_3                 # the gateway is TLS 1.3 only
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        with client.wrap_socket(raw, server_hostname="friday") as tls_sock:
            tls_sock.sendall(b"GET /tv/v1/hello HTTP/1.0\r\n\r\n")
            assert b"200" in tls_sock.recv(64)
        stalled.close()
    finally:
        srv.shutdown()
        srv.server_close()
