"""The main app never accepts a TV gateway device session (the other direction
of tests/unit/test_tv_gateway.py's "main-app credentials are refused")."""
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway

TV_ADDR = "192.168.1.105"


@pytest.fixture
def flask_app():
    from agent_friday.server import app
    return app


def _device_session():
    device_pairing._reset_for_tests()
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    dev = device_pairing.add_device(spki, name="TV", rights="tv_brain")
    nonce = device_pairing.new_challenge()
    sig = key.sign(device_pairing.session_message(nonce, dev["id"]), ec.ECDSA(hashes.SHA256()))
    token = device_pairing.open_session(dev["id"], nonce, sig)
    assert token
    return token


def test_a_device_session_opens_nothing_in_the_main_app(flask_app):
    token = _device_session()
    fresh = flask_app.test_client()
    for headers in ({"Authorization": gateway.AUTH_SCHEME + token},
                    {"Authorization": "Bearer " + token},
                    {"X-Friday-Token": token}):
        r = fresh.get("/api/residency/status", headers=headers,
                      environ_base={"REMOTE_ADDR": TV_ADDR})
        assert r.status_code in (401, 403), (
            "the main app served a device-session request (HTTP %s, %s)" % (r.status_code, headers))
    device_pairing._reset_for_tests()

