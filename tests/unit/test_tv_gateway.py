"""The TV gateway (D1): a separate door that trusts only paired devices.

Each test names the promise it guards; the plan is
ftv/program/specs/tv_desktop_pairing_SLICE_PLAN_2026-11-02.md.
"""
import base64
import hashlib
import json
import socket
import ssl

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import discovery, tls


class FakeTv:
    """A TV's Keystore key: P-256, signs SHA256withECDSA in DER like Android."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.spki = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.id = device_pairing.spki_id(self.spki)

    def sign(self, nonce: str, device_id: str = None) -> str:
        msg = device_pairing.session_message(nonce, device_id or self.id)
        return base64.b64encode(self.key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    device_pairing._path().unlink(missing_ok=True)   # each test starts with no devices
    device_pairing._reset_for_tests()
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    yield
    device_pairing._reset_for_tests()
    gateway.stop()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


@pytest.fixture
def tv():
    t = FakeTv()
    device_pairing.add_device(t.spki, name="Living room TV", rights="tv_brain")
    return t


def open_session(client, tv):
    nonce = client.post("/tv/v1/session/challenge").get_json()["nonce"]
    r = client.post("/tv/v1/session/open",
                    json={"device_id": tv.id, "nonce": nonce, "sig": tv.sign(nonce)})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["session"]


def auth(token):
    return {"Authorization": gateway.AUTH_SCHEME + token}


# ── the store ───────────────────────────────────────────────────────────────

def test_a_paired_device_is_listed_without_its_key(tv):
    [row] = device_pairing.list_devices()
    assert row["id"] == tv.id and row["rights"] == "tv_brain"
    assert "spki" not in row


def test_an_edited_store_grants_nothing(tv):
    p = device_pairing._path()
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["body"]["devices"][tv.id]["rights"] = "everything"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert device_pairing.list_devices() == []
    assert device_pairing.gateway_config()["enabled"] is False


def test_a_store_written_without_a_signature_grants_nothing(tv):
    p = device_pairing._path()
    body = json.loads(p.read_text(encoding="utf-8"))["body"]
    p.write_text(json.dumps({"body": body, "sig": "0" * 64}), encoding="utf-8")
    assert device_pairing.get_device(tv.id) is None


def test_only_the_two_named_rights_exist():
    with pytest.raises(ValueError):
        device_pairing.add_device(FakeTv().spki, name="x", rights="everything")


def test_a_non_p256_key_is_refused():
    other = ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(ValueError):
        device_pairing.add_device(other, name="x", rights="chat_read")


# ── sessions ────────────────────────────────────────────────────────────────

def test_a_device_signing_a_live_challenge_gets_a_session(client, tv):
    token = open_session(client, tv)
    assert token.startswith("fdev_")
    r = client.get("/tv/v1/whoami", headers=auth(token))
    assert r.status_code == 200
    assert r.get_json()["principal"] == "device:" + tv.id


def test_the_session_token_is_not_stored_in_plaintext(client, tv):
    token = open_session(client, tv)
    assert token not in device_pairing._path().read_text(encoding="utf-8")
    assert all(token not in k for k in device_pairing._SESSIONS)


def test_a_challenge_is_single_use(client, tv):
    nonce = client.post("/tv/v1/session/challenge").get_json()["nonce"]
    body = {"device_id": tv.id, "nonce": nonce, "sig": tv.sign(nonce)}
    assert client.post("/tv/v1/session/open", json=body).status_code == 200
    assert client.post("/tv/v1/session/open", json=body).status_code == 401


def test_an_invented_challenge_is_refused(client, tv):
    nonce = "ab" * 32
    r = client.post("/tv/v1/session/open",
                    json={"device_id": tv.id, "nonce": nonce, "sig": tv.sign(nonce)})
    assert r.status_code == 401


def test_another_key_cannot_open_a_session_for_a_paired_device(client, tv):
    impostor = FakeTv()
    nonce = client.post("/tv/v1/session/challenge").get_json()["nonce"]
    r = client.post("/tv/v1/session/open",
                    json={"device_id": tv.id, "nonce": nonce, "sig": impostor.sign(nonce, tv.id)})
    assert r.status_code == 401


def test_an_unpaired_device_cannot_open_a_session(client):
    stranger = FakeTv()
    nonce = client.post("/tv/v1/session/challenge").get_json()["nonce"]
    r = client.post("/tv/v1/session/open",
                    json={"device_id": stranger.id, "nonce": nonce, "sig": stranger.sign(nonce)})
    assert r.status_code == 401


def test_removing_a_device_ends_its_live_session_on_the_next_call(client, tv):
    token = open_session(client, tv)
    assert device_pairing.remove_device(tv.id) is True
    assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 401


def test_a_session_outlives_nothing_the_store_forgets(client, tv):
    """Even with the in-memory session intact, a device gone from the signed
    store (Stop all, or a store that no longer verifies) is refused."""
    token = open_session(client, tv)
    p = device_pairing._path()
    p.write_text("{}", encoding="utf-8")
    assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 401


def test_stop_all_ends_every_session(client, tv):
    token = open_session(client, tv)
    assert device_pairing.stop_all() == 1
    assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 401


# ── the door itself ─────────────────────────────────────────────────────────

def test_loopback_is_not_trusted(client):
    r = client.get("/tv/v1/whoami", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 401


@pytest.mark.parametrize("header", ["X-Friday-Token", "X-Friday-Observer", "Cookie"])
def test_main_app_credentials_are_refused_even_beside_a_valid_session(client, tv, header):
    token = open_session(client, tv)
    h = auth(token)
    h[header] = "session=abc" if header == "Cookie" else "anything"
    raw = gateway.create_app().test_client(use_cookies=False)   # sends the Cookie header as given
    assert raw.get("/tv/v1/whoami", headers=h).status_code == 401


@pytest.mark.parametrize("path", ["/", "/index.html", "/api/health", "/api/chat",
                                  "/friday-live", "/api/token"])
def test_the_gateway_serves_no_page_and_no_main_route(client, tv, path):
    token = open_session(client, tv)
    r = client.get(path, headers=auth(token))
    assert r.status_code == 404
    assert b"__FRIDAY_API_TOKEN" not in r.data and b"<html" not in r.data.lower()


def test_the_gateway_sets_no_cookie(client, tv):
    token = open_session(client, tv)
    r = client.get("/tv/v1/whoami", headers=auth(token))
    assert "Set-Cookie" not in r.headers


def test_open_paths_are_rate_limited(client):
    codes = [client.post("/tv/v1/session/challenge").status_code for _ in range(gateway._RATE + 1)]
    assert codes[-1] == 429 and codes[0] == 200


def test_hello_names_the_pin_and_nothing_else(client):
    body = client.get("/tv/v1/hello").get_json()
    assert set(body) == {"ok", "service", "v", "spki_sha256"}
    assert body["spki_sha256"] == tls.spki_sha256()


# ── TLS and binding ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["0.0.0.0", "", "::"])
def test_the_gateway_never_binds_a_wildcard(host):
    with pytest.raises(ValueError):
        gateway.start(host, 0)


def test_the_gateway_speaks_tls13_with_the_pinned_certificate():
    info = gateway.start("127.0.0.1", 0)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE           # the TV pins the SPKI instead of a CA
    with socket.create_connection(("127.0.0.1", info["port"]), timeout=5) as raw:
        with ctx.wrap_socket(raw) as s:
            assert s.version() == "TLSv1.3"
            from cryptography import x509
            cert = x509.load_der_x509_certificate(s.getpeercert(binary_form=True))
            spki = cert.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            assert hashlib.sha256(spki).hexdigest() == tls.spki_sha256()


def test_the_gateway_refuses_tls12():
    info = gateway.start("127.0.0.1", 0)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection(("127.0.0.1", info["port"]), timeout=5) as raw:
        with pytest.raises(ssl.SSLError):
            ctx.wrap_socket(raw)


def test_the_identity_is_made_once():
    assert tls.spki_sha256() == tls.spki_sha256()
    first = tls.ensure_identity()[0].read_bytes()
    assert tls.ensure_identity()[0].read_bytes() == first


def test_off_by_default_nothing_listens():
    assert device_pairing.gateway_config()["enabled"] is False
    assert gateway.boot() is None
    assert gateway.running() == {"running": False}


def test_a_store_that_does_not_verify_turns_the_gateway_off():
    device_pairing.set_gateway(True, 47810)
    assert device_pairing.gateway_config()["enabled"] is True
    device_pairing._path().write_text('{"body": {"gateway": {"enabled": true}}, "sig": "x"}',
                                      encoding="utf-8")
    assert gateway.boot() is None


# ── discovery ───────────────────────────────────────────────────────────────

def test_discovery_answers_only_its_probe():
    assert discovery.reply_for(b"hello", 47810, "ab") is None
    out = json.loads(discovery.reply_for(discovery.PROBE, 47810, "ab"))
    assert out == {"service": "agent-friday-tv-gateway", "v": 1, "port": 47810, "spki_sha256": "ab"}


def test_discovery_never_binds_a_wildcard():
    with pytest.raises(ValueError):
        discovery.Responder("0.0.0.0", 47810, "ab", port=0)


def test_discovery_round_trip_on_loopback():
    r = discovery.Responder("127.0.0.1", 47810, "cd", port=0).start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(5)
        s.sendto(discovery.PROBE, ("127.0.0.1", r.port))
        data, _ = s.recvfrom(512)
        assert json.loads(data)["spki_sha256"] == "cd"
        s.close()
    finally:
        r.stop()


# ── receipts ────────────────────────────────────────────────────────────────

def test_opening_a_session_and_removing_a_device_leave_device_receipts(client, tv):
    from tests.ledger_support import rows
    open_session(client, tv)
    device_pairing.remove_device(tv.id)
    [opened] = rows("device_session_opened")
    [removed] = rows("device_removed")
    assert opened["principal"] == removed["principal"] == "device:" + tv.id
    assert "owner" not in opened["principal"]
