"""Registering a phone's approval key (brief: approvals from the owner's phone, PR A). Nothing here approves a card.

Rules guarded here, each with a case that fails if the check is removed:
* only an owner-bound phone or tablet may register a key (not a watch, TV, family or unbound device);
* the key is P-256, is never the device's session key, and a tablet's key must ask for the fingerprint;
* a registered key is only staged: the right and the key become active together, from one screen press on the
  card that names that key; no other label, and no card for an older key, activates anything;
* withdrawing the right ends the key; the same key asked for twice raises no second card;
* pairing never raises this card, and the tailnet listener serves the same route.
"""
import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing
from tests.desk_press_support import pressed
from tests.ledger_support import rows
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

ROUTE = "/tv/v1/approvals/key"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(pairing, "_OFFERED", {})
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def new_key():
    pub = ec.generate_private_key(ec.SECP256R1()).public_key()
    return pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def paired(kind="phone", member="stephen"):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    return dev


def register(client, dev, spki=None, biometric=True):
    body = {"spki": base64.b64encode(spki or new_key()).decode(), "biometric": biometric}
    return client.post(ROUTE, json=body, headers=auth(open_session(client, dev)))


def cards(right="pc_approve"):
    return [c for c in ap.list_approvals(status="pending")
            if c.get("kind") == pairing.RIGHT_KIND and c["payload"]["right"] == right]


def press(card):
    with pressed():
        return ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")


def test_a_session_is_required(client):
    assert client.post(ROUTE, json={}).status_code == 401


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("tv", "family"),
                                         ("phone", None), ("phone", "family")])
def test_only_an_owner_phone_or_tablet_may_register_a_key(client, kind, member):
    dev = paired(kind, member)
    r = register(client, dev)
    assert r.status_code == 403 and r.get_json()["code"] == "pc_approve_not_for_this_device"
    assert cards() == [] and device_pairing.pending_approval_key(dev.id) is None


@pytest.mark.parametrize("body", [{"biometric": True}, {"spki": "!!!", "biometric": True},
                                  {"spki": "AAAA", "biometric": "yes"}, {"spki": "A" * 500, "biometric": True}])
def test_a_malformed_request_is_refused_plainly(client, body):
    dev = paired()
    r = client.post(ROUTE, json=body, headers=auth(open_session(client, dev)))
    assert r.status_code == 400 and r.get_json()["code"] == "bad_request" and cards() == []


def test_a_key_that_is_not_p256_is_refused(client):
    rsa_der = rsa.generate_private_key(65537, 2048).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    r = register(client, paired(), spki=rsa_der[:300])
    assert r.status_code in (400, 422) and cards() == []


def test_the_session_key_cannot_be_the_approval_key(client):
    dev = paired()
    r = register(client, dev, spki=dev.spki)
    assert r.status_code == 422 and r.get_json()["code"] == "session_key_reused" and cards() == []


def test_a_tablet_key_must_ask_for_the_fingerprint(client):
    dev = paired("tablet")
    r = register(client, dev, biometric=False)
    assert r.status_code == 422 and r.get_json()["code"] == "tablet_needs_biometric" and cards() == []
    assert register(client, dev, biometric=True).status_code == 200


def test_a_phone_may_choose_no_fingerprint_and_the_card_says_so(client):
    r = register(client, paired("phone"), biometric=False)
    assert r.status_code == 200 and r.get_json()["biometric"] is False and r.get_json()["attested"] is False
    assert "NO fingerprint" in cards()[0]["description"]


def test_registering_stages_the_key_and_raises_one_card_that_names_the_choice(client):
    dev = paired()
    j = register(client, dev, biometric=True).get_json()
    assert j["status"] == "staged" and j["attested"] is False and j["next"] == "press the card on the PC"
    [card] = cards()
    assert "fingerprint on every approval" in card["description"] and card["payload"]["key_id"] == j["key_id"]
    assert not device_pairing.has_right(device_pairing.get_device(dev.id), "pc_approve")
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)) is None
    assert len(rows("device_approval_key_staged")) == 1


def test_one_screen_press_activates_the_key_and_grants_the_right_together(client):
    dev = paired()
    j = register(client, dev).get_json()
    press(cards()[0])
    rec = device_pairing.get_device(dev.id)
    assert device_pairing.has_right(rec, "pc_approve")
    key = device_pairing.approval_key(rec)
    assert key.key_id == j["key_id"] and key.biometric is True and key.attested is False
    assert device_pairing.public_view(rec)["approval_biometric"] is True
    assert device_pairing.public_view(rec)["approval_attested"] is False
    assert "approval_spki" not in device_pairing.public_view(rec)


@pytest.mark.parametrize("label", ["owner", "owner:chat", "owner:voice", "tv_gateway", "device:abc"])
def test_no_other_label_activates_the_key(client, label):
    dev = paired()
    register(client, dev)
    ap.decide_with_outcome(cards()[0]["approval_id"], "approve", decided_by=label)
    rec = device_pairing.get_device(dev.id)
    assert device_pairing.approval_key(rec) is None and not device_pairing.has_right(rec, "pc_approve")


def test_declining_the_card_leaves_the_device_without_the_right(client):
    dev = paired()
    register(client, dev)
    ap.decide(cards()[0]["approval_id"], "deny", decided_by="owner")
    assert not device_pairing.has_right(device_pairing.get_device(dev.id), "pc_approve")


def test_a_card_for_an_older_key_activates_nothing(client):
    dev = paired()
    register(client, dev)
    [first] = cards()
    register(client, dev)                                   # a second, different key replaces the staged one
    assert len(cards()) == 2
    press(first)
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)) is None
    press([c for c in cards() if c["approval_id"] != first["approval_id"]][0])
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)) is not None


def test_the_same_key_again_after_activation_raises_no_card(client):
    dev = paired()
    spki = new_key()
    register(client, dev, spki=spki)
    press(cards()[0])
    r = register(client, dev, spki=spki, biometric=True)
    assert r.get_json()["status"] == "already_active" and cards() == []


def test_turning_the_fingerprint_off_needs_a_new_press(client):
    dev = paired()
    spki = new_key()
    register(client, dev, spki=spki, biometric=True)
    press(cards()[0])
    r = register(client, dev, spki=spki, biometric=False)
    assert r.get_json()["status"] == "staged" and len(cards()) == 1
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)).biometric is True


def test_withdrawing_the_right_ends_the_key(client):
    dev = paired()
    register(client, dev)
    press(cards()[0])
    device_pairing.set_right(dev.id, "pc_approve", False)
    rec = device_pairing.get_device(dev.id)
    assert device_pairing.approval_key(rec) is None and not device_pairing.has_right(rec, "pc_approve")


def test_pairing_never_raises_the_approval_card_by_itself():
    dev_id = paired().id
    pairing._offer_rights(dev_id)
    assert cards() == []


def test_the_tailnet_listener_serves_the_route_and_the_receipt_says_so():
    dev = paired()
    c = gateway.create_app(tailnet=True).test_client()
    env = {"REMOTE_ADDR": "100.70.222.100"}
    nonce = c.post("/tv/v1/session/challenge", environ_overrides=env).get_json()["nonce"]
    tok = c.post("/tv/v1/session/open", environ_overrides=env,
                 json={"device_id": dev.id, "nonce": nonce, "sig": dev.sign(nonce)}).get_json()["session"]
    r = c.post(ROUTE, environ_overrides=env, headers=auth(tok),
               json={"spki": base64.b64encode(new_key()).decode(), "biometric": True})
    assert r.status_code == 200 and rows("device_approval_key_staged")[-1]["via"] == "tailnet"
