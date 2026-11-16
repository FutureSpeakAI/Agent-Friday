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
from agent_friday.tv_gateway import household, pairing, pc_approve
from tests.desk_press_support import pressed
from tests.ledger_support import rows
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

ROUTE = "/tv/v1/approvals/key"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(pc_approve, "_STAGED_AT", {})
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


def test_the_session_key_in_another_encoding_is_still_the_session_key(client):
    dev = paired()
    pub = serialization.load_der_public_key(dev.spki)
    point = pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint)
    der = bytes.fromhex("3039301306072a8648ce3d020106082a8648ce3d030107032200") + point
    assert der != dev.spki
    r = register(client, dev, spki=der)
    assert r.status_code == 422 and r.get_json()["code"] == "session_key_reused"


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


def test_a_new_key_replaces_the_pending_card(client):
    dev = paired()
    register(client, dev)
    [first] = cards()
    register(client, dev)
    [second] = cards()
    assert second["approval_id"] != first["approval_id"]
    press(first)                                            # the older card is gone: nothing activates
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)) is None
    press(second)
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)) is not None


def test_a_flood_of_new_keys_leaves_one_card_on_the_screen(client):
    dev = paired()
    for _ in range(6):
        register(client, dev)
    assert len(cards()) == 1


def test_the_same_key_twice_before_the_press_raises_one_card(client):
    dev = paired()
    spki = new_key()
    register(client, dev, spki=spki)
    register(client, dev, spki=spki)
    assert len(cards()) == 1


def test_a_stale_fingerprint_card_cannot_activate_a_no_fingerprint_key(client):
    dev = paired()
    spki = new_key()
    register(client, dev, spki=spki, biometric=True)
    [with_fingerprint] = cards()
    register(client, dev, spki=spki, biometric=False)
    press(with_fingerprint)
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)) is None
    press(cards()[0])
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)).biometric is False


def test_activation_itself_refuses_a_different_fingerprint_choice(client):
    dev = paired()
    j = register(client, dev, biometric=False).get_json()
    assert device_pairing.activate_approval_key(dev.id, j["key_id"], True) is False
    assert device_pairing.activate_approval_key(dev.id, j["key_id"], False) is True


def test_only_the_card_hook_activates_a_key():
    import pathlib
    root = pathlib.Path(device_pairing.__file__).resolve().parents[1]
    callers = [p.relative_to(root).as_posix() for p in root.rglob("*.py")
               if "activate_approval_key(" in p.read_text(encoding="utf-8") and p.name != "device_pairing.py"]
    assert callers == ["tv_gateway/pairing.py"]


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


def test_a_device_may_register_three_keys_an_hour(client):
    dev = paired()
    codes = [register(client, dev).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert register(client, dev).get_json()["code"] == "too_many_keys"
    assert len(cards()) == 1


def test_the_active_key_registered_again_takes_a_pending_card_away(client):
    dev = paired()
    spki = new_key()
    register(client, dev, spki=spki)
    press(cards()[0])
    register(client, dev)
    assert len(cards()) == 1
    register(client, dev, spki=spki)
    assert cards() == []


def test_withdrawing_the_right_takes_its_pending_card_away(client):
    dev = paired()
    register(client, dev)
    device_pairing.set_right(dev.id, "pc_approve", False)
    assert cards() == []


def test_a_right_without_a_key_cannot_approve(client):
    dev = paired()
    register(client, dev)
    assert not device_pairing.can_approve(device_pairing.get_device(dev.id))
    press(cards()[0])
    assert device_pairing.can_approve(device_pairing.get_device(dev.id))
    body = device_pairing._load()
    body["devices"][dev.id].pop("approval_spki")
    device_pairing._save(body)
    assert not device_pairing.can_approve(device_pairing.get_device(dev.id))


def test_the_same_key_and_choice_again_after_its_card_was_replaced_raises_a_new_card(client):
    dev = paired()
    first_key = new_key()
    register(client, dev, spki=first_key)
    register(client, dev)                                   # a second key replaces the first card
    answer = register(client, dev, spki=first_key).get_json()
    assert answer["status"] == "staged" and len(cards()) == 1
    press(cards()[0])
    assert device_pairing.approval_key(device_pairing.get_device(dev.id)).key_id == answer["key_id"]


def test_the_same_key_after_the_owner_declined_its_card_can_be_offered_again(client):
    dev = paired()
    spki = new_key()
    register(client, dev, spki=spki)
    ap.decide(cards()[0]["approval_id"], "deny", decided_by="owner")
    register(client, dev, spki=spki)
    assert len(cards()) == 1


def test_the_same_key_after_the_right_was_withdrawn_can_be_granted_again(client):
    dev = paired()
    spki = new_key()
    register(client, dev, spki=spki)
    press(cards()[0])
    device_pairing.set_right(dev.id, "pc_approve", False)
    register(client, dev, spki=spki)
    press(cards()[0])
    assert device_pairing.can_approve(device_pairing.get_device(dev.id))


def test_the_fingerprint_choice_flipped_back_raises_a_card(client):
    dev = paired()
    spki = new_key()
    for choice in (True, False, True):
        register(client, dev, spki=spki, biometric=choice)
    [card] = cards()
    assert card["payload"]["biometric"] is True


def test_an_older_key_registering_late_raises_no_card(client):
    dev = paired()
    older = new_key()
    register(client, dev, spki=older)
    newer_id = register(client, dev).get_json()["key_id"]
    late = device_pairing.spki_id(device_pairing.canonical_spki(older))
    assert pairing.offer_right(dev.id, "pc_approve", key_id=late, biometric=True, detail="x") is None
    [card] = cards()
    assert card["payload"]["key_id"] == newer_id


def test_removing_the_device_takes_its_pending_card_away(client):
    dev = paired()
    register(client, dev)
    device_pairing.remove_device(dev.id)
    assert cards() == []


def test_a_staged_key_always_has_a_card_on_the_screen(client, monkeypatch):
    dev = paired()
    register(client, dev)
    assert pairing.key_card_in_step(dev.id)
    ap.expire_matching(lambda rec: rec.get("kind") == pairing.RIGHT_KIND, "test", "gone")
    assert not pairing.key_card_in_step(dev.id)
    monkeypatch.setattr(pairing, "offer_right", lambda *a, **k: None)
    answer = register(client, dev)
    assert answer.status_code == 503 and answer.get_json()["code"] == "card_not_raised"
    assert device_pairing.pending_approval_key(dev.id) is None


def test_the_services_pairing_module_does_not_import_the_gateway():
    import pathlib
    source = pathlib.Path(device_pairing.__file__).read_text(encoding="utf-8")
    assert "tv_gateway import" not in source and "import agent_friday.tv_gateway" not in source
