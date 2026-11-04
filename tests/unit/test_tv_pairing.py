"""Pairing a TV (D2): six digits on both screens, a press on this PC, nothing else.

The TV side is simulated here exactly as friday-tv's pair/DesktopLink does it:
check the commitment, compute the same six digits from the pin it met.
"""
import base64
import hashlib
import os
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import pairing, tls
from tests.desk_press_support import pressed, voice_call_on_this_desk


def tv_key():
    k = ec.generate_private_key(ec.SECP256R1())
    return k.public_key().public_bytes(serialization.Encoding.DER,
                                       serialization.PublicFormat.SubjectPublicKeyInfo)


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    device_pairing._path().unlink(missing_ok=True)   # each test starts with no devices
    device_pairing._reset_for_tests()
    pairing._reset_for_tests()
    yield
    pairing._reset_for_tests()
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


class TvSide:
    """What the TV does, given the pin it met on the TLS connection."""

    def __init__(self, client, pin_seen=None):
        self.client, self.spki = client, tv_key()
        self.pin_seen = pin_seen or tls.spki_sha256()

    def run(self):
        r = self.client.post("/tv/v1/pair/start", json={
            "device_spki": base64.b64encode(self.spki).decode(), "name": "Living room TV"})
        assert r.status_code == 200, r.get_json()
        st = r.get_json()
        self.pid, commit = st["pairing_id"], st["commit"]
        nt = os.urandom(32)
        r = self.client.post("/tv/v1/pair/reveal", json={
            "pairing_id": self.pid, "nt": base64.b64encode(nt).decode()})
        assert r.status_code == 200, r.get_json()
        nd = base64.b64decode(r.get_json()["nd"])
        self.commit_ok = pairing.commitment(self.pin_seen, self.spki, nd) == commit
        self.code = pairing.sas(self.pin_seen, self.spki, nt, nd)
        return self

    def card(self):
        return ap.get_approval(pairing.pending_card(self.pid))

    def state(self):
        return self.client.get("/tv/v1/pair/status", query_string={"pairing_id": self.pid}).get_json()


def test_both_screens_show_the_same_six_digits(client):
    tv = TvSide(client).run()
    card = tv.card()
    assert tv.commit_ok
    assert card["payload"]["sas"] == tv.code
    assert pairing.spoken(tv.code) in card["title"]
    assert len(tv.code) == 6 and tv.code.isdigit()


def test_a_press_on_this_pc_pairs_the_tv_with_the_least_rights(client):
    tv = TvSide(client).run()
    with pressed():
        rec, won = ap.decide_with_outcome(tv.card()["approval_id"], "approve", decided_by="owner:ui")
    assert won and rec["status"] == "approved"
    st = tv.state()
    assert st["state"] == "paired"
    dev = device_pairing.get_device(st["device_id"])
    assert dev is not None and dev["rights"] == ["chat_read"]


@pytest.mark.parametrize("label", ["owner", "owner:chat", "owner:/voice/", "tv_gateway", "owner:ui"])
def test_nothing_but_a_verified_press_pairs(client, label):
    """A label in code, a typed yes, a spoken yes or the gateway itself never pairs,
    including a bare "owner:ui" claimed outside a verified press."""
    tv = TvSide(client).run()
    rec, won = ap.decide_with_outcome(tv.card()["approval_id"], "approve", decided_by=label)
    assert rec["status"] == "pending"
    assert tv.state()["state"] == "awaiting_press"
    assert device_pairing.list_devices() == []


def test_a_spoken_yes_on_this_desk_still_does_not_pair(client):
    tv = TvSide(client).run()
    with voice_call_on_this_desk():
        rec, _ = ap.decide_with_outcome(tv.card()["approval_id"], "approve", decided_by="owner:voice")
    assert rec["status"] == "pending" and device_pairing.list_devices() == []


def test_declining_refuses_and_records_nothing(client):
    tv = TvSide(client).run()
    ap.decide(tv.card()["approval_id"], "deny", decided_by="owner")
    assert tv.state()["state"] == "refused"
    assert device_pairing.list_devices() == []


def test_a_machine_in_the_middle_makes_the_screens_disagree(client):
    """The TV met another certificate (the middle's), so its digits are computed
    from another pin. The PC's card names different digits, and the TV's check of
    the commitment fails too."""
    impostor_pin = hashlib.sha256(b"someone else's certificate").hexdigest()
    tv = TvSide(client, pin_seen=impostor_pin).run()
    assert tv.card()["payload"]["sas"] != tv.code
    assert tv.commit_ok is False


def test_the_commitment_binds_nd_before_nt_is_seen(client):
    spki = tv_key()
    st = pairing.start(spki, "TV")
    nd = pairing.reveal(st["pairing_id"], os.urandom(32))
    assert pairing.commitment(tls.spki_sha256(), spki, nd) == st["commit"]
    assert pairing.commitment(tls.spki_sha256(), spki, os.urandom(32)) != st["commit"]


def test_each_step_happens_once(client):
    tv = TvSide(client).run()
    r = client.post("/tv/v1/pair/reveal", json={
        "pairing_id": tv.pid, "nt": base64.b64encode(os.urandom(32)).decode()})
    assert r.status_code == 409


def test_one_pairing_at_a_time(client):
    TvSide(client).run()
    r = client.post("/tv/v1/pair/start", json={"device_spki": base64.b64encode(tv_key()).decode()})
    assert r.status_code == 409


def test_the_fourth_attempt_in_an_hour_is_refused(client, monkeypatch):
    for _ in range(pairing.MAX_STARTS_PER_HOUR):
        st = pairing.start(tv_key(), "TV")
        pairing._PENDING[st["pairing_id"]]["state"] = "expired"
    with pytest.raises(pairing.PairingRefused):
        pairing.start(tv_key(), "TV")


def test_a_press_after_five_minutes_pairs_nothing(client, monkeypatch):
    tv = TvSide(client).run()
    pairing._PENDING[tv.pid]["expires"] = time.time() - 1
    with pressed():
        ap.decide_with_outcome(tv.card()["approval_id"], "approve", decided_by="owner:ui")
    assert device_pairing.list_devices() == []
    assert tv.state()["state"] == "expired"


def test_an_already_paired_tv_must_be_removed_before_pairing_again(client):
    tv = TvSide(client).run()
    with pressed():
        ap.decide_with_outcome(tv.card()["approval_id"], "approve", decided_by="owner:ui")
    with pytest.raises(pairing.PairingRefused):
        pairing.start(tv.spki, "TV")


def test_a_short_nonce_is_refused(client):
    st = pairing.start(tv_key(), "TV")
    with pytest.raises(pairing.PairingRefused):
        pairing.reveal(st["pairing_id"], b"\x00" * 8)


def test_the_pairing_card_is_screen_only_and_never_posted():
    from agent_friday.services import tasks_inbox
    assert ap.SCREEN_ONLY_KINDS.get("device_pair") == frozenset({"owner:ui"})
    assert "device_pair" in tasks_inbox.NEVER_POSTED_KINDS


def test_pairing_routes_carry_no_session_and_grant_none(client):
    tv = TvSide(client).run()
    assert client.get("/tv/v1/whoami").status_code == 401
    assert "session" not in tv.state()


# ── rights after pairing: one card each, a press each ──────────────────────

def _paired(client):
    tv = TvSide(client).run()
    with pressed():
        ap.decide_with_outcome(tv.card()["approval_id"], "approve", decided_by="owner:ui")
    dev_id = tv.state()["device_id"]
    cards = {c["payload"]["right"]: c for c in ap.list_approvals(status="pending")
             if c.get("kind") == pairing.RIGHT_KIND and c["payload"]["device_id"] == dev_id}
    return dev_id, cards


def test_pairing_offers_each_wider_right_as_its_own_pending_card(client):
    dev_id, cards = _paired(client)
    assert set(cards) == {"tv_brain", "studio"}
    assert device_pairing.get_device(dev_id)["rights"] == ["chat_read"]


def test_the_studio_card_says_it_spends_and_that_the_daily_stop_does_not_apply(client):
    _, cards = _paired(client)
    words = (cards["studio"]["title"] + " " + cards["studio"]["description"]).lower()
    assert "spend" in words and "daily spend stop does not apply" in words and "receipt" in words


def test_a_press_grants_exactly_the_right_on_its_card(client):
    dev_id, cards = _paired(client)
    with pressed():
        ap.decide_with_outcome(cards["studio"]["approval_id"], "approve", decided_by="owner:ui")
    assert device_pairing.get_device(dev_id)["rights"] == ["chat_read", "studio"]


@pytest.mark.parametrize("label", ["owner", "owner:chat", "owner:voice", "tv_gateway", "owner:ui"])
def test_no_voice_chat_device_or_claimed_label_grants_a_right(client, label):
    dev_id, cards = _paired(client)
    for c in cards.values():
        ap.decide_with_outcome(c["approval_id"], "approve", decided_by=label)
    assert device_pairing.get_device(dev_id)["rights"] == ["chat_read"]


def test_declining_a_right_leaves_chat_and_read(client):
    dev_id, cards = _paired(client)
    ap.decide(cards["tv_brain"]["approval_id"], "deny", decided_by="owner")
    assert device_pairing.get_device(dev_id)["rights"] == ["chat_read"]


def test_a_right_pressed_after_the_device_was_removed_grants_nothing(client):
    dev_id, cards = _paired(client)
    device_pairing.remove_device(dev_id)
    with pressed():
        ap.decide_with_outcome(cards["tv_brain"]["approval_id"], "approve", decided_by="owner:ui")
    assert device_pairing.get_device(dev_id) is None


def test_right_cards_are_screen_only_and_never_posted():
    from agent_friday.services import tasks_inbox
    assert ap.SCREEN_ONLY_KINDS.get("device_right") == frozenset({"owner:ui"})
    assert "device_right" in tasks_inbox.NEVER_POSTED_KINDS
