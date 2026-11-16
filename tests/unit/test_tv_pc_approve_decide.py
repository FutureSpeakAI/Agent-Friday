"""Approving and declining cards from an owner's phone or tablet (brief: approvals from the owner's phone, PR B).

Rules guarded here, each with a case that fails if the check is removed:
* a device approves only with an active key and a signature from it over the card as challenged; the nonce is
  spent first, the clock window is 120 s, an edited card is refused, and five bad signatures withdraw the right;
* a device never approves a device_right or pairing card, nor the cards the PC keeps to their own button;
* a deny needs only an owner phone or tablet; nothing a device sends can name who decided;
* the list and the feed cut private cards down to their generic title unless the device can approve.
"""
import base64
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import approval_feed, approvals as ap, device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import approval_verify as av
from agent_friday.tv_gateway import household, pairing, pc_approve
from tests.desk_press_support import pressed
from tests.ledger_support import rows
from tests.unit.test_tv_gateway import FakeTv, auth, open_session


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(pairing, "_OFFERED", {})
    monkeypatch.setattr(pc_approve, "_STAGED_AT", {})
    av.reset_for_tests()
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()
    av.reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


class Enrolled:
    """An owner device that holds pc_read and pc_approve with an active approval key."""

    def __init__(self, client, kind="phone", biometric=True):
        self.client = client
        self.token = None
        self.tv = FakeTv()
        device_pairing.add_device(self.tv.spki, name="Stephen's phone", kind=kind)
        household.bind_device(self.tv.id, "stephen")
        device_pairing.set_right(self.tv.id, "pc_read", True)
        self.id = self.tv.id
        self.key = ec.generate_private_key(ec.SECP256R1())
        spki = self.key.public_key().public_bytes(serialization.Encoding.DER,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo)
        self.post("/tv/v1/approvals/key", {"spki": base64.b64encode(spki).decode(), "biometric": biometric})
        [card] = [c for c in ap.list_approvals(status="pending") if c.get("kind") == pairing.RIGHT_KIND]
        with pressed():
            ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")

    def session(self):
        self.token = self.token or open_session(self.client, self.tv)
        return auth(self.token)

    def post(self, path, body):
        return self.client.post(path, json=body, headers=self.session())

    def get(self, path):
        return self.client.get(path, headers=self.session())

    def challenge(self, approval_id):
        return self.post("/tv/v1/approvals/%s/challenge" % approval_id, {})

    def sign(self, challenge, when=None, **over):
        message = av.signing_message(over.get("approval_id", challenge["approval_id"]),
                                     over.get("action_fp", challenge["action_fp"]),
                                     over.get("nonce", challenge["nonce"]), over.get("device_id", self.id),
                                     when if when is not None else int(time.time()))
        return base64.b64encode(self.key.sign(message, ec.ECDSA(hashes.SHA256()))).decode()

    def approve(self, approval_id, when=None, **body):
        got = self.challenge(approval_id).get_json()
        when = when if when is not None else int(time.time())
        payload = {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": self.sign(got, when)}
        payload.update(body)
        return self.post("/tv/v1/approvals/%s/decide" % approval_id, payload)


def card(kind="desk_test_card", **payload):
    return ap.create_approval(kind=kind, subject_type="desk_test", subject_id=uuid.uuid4().hex,
                              title="Send the test message", description="to Alex", action_description="send it",
                              payload=payload or None, force_gate=True)


def status(approval_id):
    return ap.get_approval(approval_id)["status"]


# ── accepted ────────────────────────────────────────────────────────────────

def test_one_good_signature_approves_once_and_the_record_names_the_device(client):
    phone = Enrolled(client)
    pending = card()
    answer = phone.approve(pending["approval_id"])
    assert answer.status_code == 200 and answer.get_json()["won"] is True and answer.get_json()["status"] == "approved"
    rec = ap.get_approval(pending["approval_id"])
    assert rec["decided_by"] == "device:%s" % phone.id and rec["decided_source"] == "device_signed"
    assert rec["decided_device"] == "owner_device"
    [receipt] = rows("device_approved")
    assert receipt["approval_id"] == pending["approval_id"] and receipt["via"] == "lan" and receipt["signature"]
    assert receipt["attested"] is False and receipt["biometric"] is True


def test_a_second_approve_of_the_same_card_changes_nothing(client):
    phone = Enrolled(client)
    pending = card()
    assert phone.approve(pending["approval_id"]).get_json()["won"] is True
    again = phone.challenge(pending["approval_id"]).get_json()
    assert again["won"] is False and again["already_decided"] is True and len(rows("device_approved")) == 1


def test_a_card_decided_elsewhere_first_returns_won_false(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    ap.decide(pending["approval_id"], "deny", decided_by="owner")
    when = int(time.time())
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                        {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)})
    assert answer.get_json()["won"] is False and status(pending["approval_id"]) == "denied"


def test_a_body_that_names_who_decided_is_ignored(client):
    phone = Enrolled(client)
    pending = card()
    phone.approve(pending["approval_id"], decided_by="owner:ui", decided_source="desktop_press")
    rec = ap.get_approval(pending["approval_id"])
    assert rec["decided_by"] == "device:%s" % phone.id and rec["decided_source"] == "device_signed"


def test_the_tailnet_listener_approves_the_same_way_and_the_receipt_says_so(client):
    phone = Enrolled(client)
    pending = card()
    tail = gateway.create_app(tailnet=True).test_client()
    env = {"REMOTE_ADDR": "100.70.222.100"}
    nonce = tail.post("/tv/v1/session/challenge", environ_overrides=env).get_json()["nonce"]
    token = tail.post("/tv/v1/session/open", environ_overrides=env,
                      json={"device_id": phone.id, "nonce": nonce, "sig": phone.tv.sign(nonce)}).get_json()["session"]
    got = tail.post("/tv/v1/approvals/%s/challenge" % pending["approval_id"], environ_overrides=env,
                    headers=auth(token), json={}).get_json()
    when = int(time.time())
    answer = tail.post("/tv/v1/approvals/%s/decide" % pending["approval_id"], environ_overrides=env,
                       headers=auth(token), json={"decision": "approve", "nonce": got["nonce"], "time": when,
                                                  "sig": phone.sign(got, when)})
    assert answer.get_json()["won"] is True and rows("device_approved")[-1]["via"] == "tailnet"


def test_a_device_source_needs_a_device_label():
    pending = card()
    rec, won = ap.decide_with_outcome(pending["approval_id"], "deny", decided_by="owner", source="device_signed")
    assert won and rec["decided_source"] != "device_signed" and rec["decided_device"] != "owner_device"


# ── deny ────────────────────────────────────────────────────────────────────

def test_deny_needs_only_an_owner_phone_with_no_extra_right(client):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Phone", kind="phone")
    household.bind_device(tv.id, "stephen")
    pending = card()
    answer = client.post("/tv/v1/approvals/%s/decide" % pending["approval_id"], json={"decision": "deny"},
                         headers=auth(open_session(client, tv)))
    assert answer.get_json()["won"] is True and status(pending["approval_id"]) == "denied"
    assert ap.get_approval(pending["approval_id"])["decided_by"] == "device:%s" % tv.id
    assert len(rows("device_denied")) == 1


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_deny_either(client, kind, member):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Other", kind=kind)
    if member:
        household.bind_device(tv.id, member)
    pending = card()
    answer = client.post("/tv/v1/approvals/%s/decide" % pending["approval_id"], json={"decision": "deny"},
                         headers=auth(open_session(client, tv)))
    assert answer.status_code == 403 and status(pending["approval_id"]) == "pending"


# ── refused: who ────────────────────────────────────────────────────────────

def test_a_device_without_the_right_cannot_get_a_challenge_or_approve(client):
    phone = Enrolled(client)
    pending = card()
    device_pairing.set_right(phone.id, "pc_approve", False)
    assert phone.challenge(pending["approval_id"]).get_json()["code"] == "needs_right"
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                        {"decision": "approve", "nonce": "x", "time": int(time.time()), "sig": "x"})
    assert answer.status_code == 403 and status(pending["approval_id"]) == "pending"


def test_a_key_still_waiting_for_its_press_cannot_approve(client):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Phone", kind="phone")
    household.bind_device(tv.id, "stephen")
    device_pairing.set_right(tv.id, "pc_read", True)
    pending = card()
    answer = client.post("/tv/v1/approvals/%s/challenge" % pending["approval_id"], json={},
                         headers=auth(open_session(client, tv)))
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


def test_a_watch_cannot_get_a_challenge(client):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Watch", kind="watch")
    household.bind_device(tv.id, "stephen")
    answer = client.post("/tv/v1/approvals/x/challenge", json={}, headers=auth(open_session(client, tv)))
    assert answer.status_code == 403 and answer.get_json()["code"] == "pc_approve_not_for_this_device"


def test_a_device_removed_between_challenge_and_decide_cannot_approve(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    when = int(time.time())
    body = {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)}
    token = open_session(client, phone.tv)
    device_pairing.remove_device(phone.id)
    answer = client.post("/tv/v1/approvals/%s/decide" % pending["approval_id"], json=body, headers=auth(token))
    assert answer.status_code in (401, 403) and status(pending["approval_id"]) == "pending"


def test_the_right_withdrawn_between_challenge_and_decide_cannot_approve(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    when = int(time.time())
    body = {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)}
    device_pairing.set_right(phone.id, "pc_approve", False)
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"], body)
    assert answer.status_code == 403 and status(pending["approval_id"]) == "pending"


# ── refused: which card ─────────────────────────────────────────────────────

def test_a_device_right_card_is_never_approved_from_a_device(client):
    phone = Enrolled(client)
    other = FakeTv()
    device_pairing.add_device(other.spki, name="Tablet", kind="tablet")
    household.bind_device(other.id, "stephen")
    rights_card = ap.get_approval(pairing.offer_right(other.id, "pc_read"))
    assert rights_card["kind"] == pairing.RIGHT_KIND
    assert phone.challenge(rights_card["approval_id"]).get_json()["code"] == "card_not_for_devices"
    answer = phone.post("/tv/v1/approvals/%s/decide" % rights_card["approval_id"],
                        {"decision": "approve", "nonce": "n", "time": int(time.time()), "sig": "s"})
    assert answer.status_code == 422 and status(rights_card["approval_id"]) == "pending"
    assert not device_pairing.has_right(device_pairing.get_device(other.id), "pc_read")


def test_a_pairing_card_is_never_approved_from_a_device(client):
    phone = Enrolled(client)
    pending = card(kind=pairing.KIND)
    answer = phone.challenge(pending["approval_id"])
    assert answer.status_code == 422 and answer.get_json()["code"] == "card_not_for_devices"


@pytest.mark.parametrize("kind,payload", [
    ("mcp_host", {}),
    ("desk_test_card", {"handler": "cloud_release"}),
    ("desk_test_card", {"public_query": {"q": "x"}}),
    ("desk_test_card", {"origin": "laya_safe_write"}),
])
def test_the_cards_the_pc_keeps_to_their_own_button_are_not_approved_from_a_device(client, kind, payload):
    phone = Enrolled(client)
    pending = card(kind=kind, **payload)
    assert phone.challenge(pending["approval_id"]).status_code == 422
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                        {"decision": "approve", "nonce": "n", "time": int(time.time()), "sig": "s"})
    assert answer.status_code == 422 and status(pending["approval_id"]) == "pending"


def test_an_unknown_card_is_a_plain_404(client):
    phone = Enrolled(client)
    assert phone.challenge("no-such-card").status_code == 404


# ── refused: the nonce, the clock, the signature ────────────────────────────

def test_a_replayed_nonce_is_refused(client):
    phone = Enrolled(client)
    first, second = card(), card()
    got = phone.challenge(first["approval_id"]).get_json()
    when = int(time.time())
    body = {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)}
    assert phone.post("/tv/v1/approvals/%s/decide" % first["approval_id"], body).get_json()["won"] is True
    replay = phone.post("/tv/v1/approvals/%s/decide" % first["approval_id"], body)
    assert replay.get_json().get("code") == "bad_nonce" or replay.get_json().get("already_decided") is True
    assert phone.post("/tv/v1/approvals/%s/decide" % second["approval_id"], body).get_json()["code"] == "bad_nonce"
    assert status(second["approval_id"]) == "pending"


def test_a_nonce_for_another_card_is_refused(client):
    phone = Enrolled(client)
    one, other = card(), card()
    got = phone.challenge(one["approval_id"]).get_json()
    when = int(time.time())
    answer = phone.post("/tv/v1/approvals/%s/decide" % other["approval_id"],
                        {"decision": "approve", "nonce": got["nonce"], "time": when,
                         "sig": phone.sign(got, when, approval_id=other["approval_id"])})
    assert answer.get_json()["code"] == "bad_nonce" and status(other["approval_id"]) == "pending"


def test_a_nonce_for_another_device_is_refused(client):
    phone, tablet = Enrolled(client), Enrolled(client, kind="tablet")
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    when = int(time.time())
    answer = tablet.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                         {"decision": "approve", "nonce": got["nonce"], "time": when,
                          "sig": tablet.sign(got, when, device_id=tablet.id)})
    assert answer.get_json()["code"] == "bad_nonce" and status(pending["approval_id"]) == "pending"


def test_an_expired_nonce_is_refused(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    old = av._LIVE[phone.id][0]
    av._LIVE[phone.id] = [av.Challenge(old.nonce, old.device_id, old.approval_id, old.action_fp, time.time() - 1)]
    when = int(time.time())
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                        {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)})
    assert answer.get_json()["code"] == "bad_nonce" and status(pending["approval_id"]) == "pending"


@pytest.mark.parametrize("skew", [-200, 200])
def test_a_time_outside_the_window_is_refused(client, skew):
    phone = Enrolled(client)
    pending = card()
    answer = phone.approve(pending["approval_id"], when=int(time.time()) + skew)
    assert answer.get_json()["code"] == "bad_time" and status(pending["approval_id"]) == "pending"


def test_a_signature_over_a_different_action_is_refused_and_counted(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    when = int(time.time())
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                        {"decision": "approve", "nonce": got["nonce"], "time": when,
                         "sig": phone.sign(got, when, action_fp="0" * 32)})
    assert answer.get_json()["code"] == "bad_signature" and status(pending["approval_id"]) == "pending"
    assert device_pairing.get_device(phone.id)["approval_fail_count"] == 1
    assert rows("device_approval_refused")[-1]["reason"] == "bad_signature"


def test_the_session_key_cannot_sign_an_approval(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    when = int(time.time())
    message = av.signing_message(pending["approval_id"], got["action_fp"], got["nonce"], phone.id, when)
    sig = base64.b64encode(phone.tv.key.sign(message, ec.ECDSA(hashes.SHA256()))).decode()
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                        {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": sig})
    assert answer.get_json()["code"] == "bad_signature" and status(pending["approval_id"]) == "pending"


def test_a_card_edited_between_challenge_and_decide_is_refused_and_not_counted(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    recs = ap._read_store()
    next(r for r in recs if r["approval_id"] == pending["approval_id"])["payload"] = {"edited": True}
    ap._write_store(recs)
    when = int(time.time())
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                        {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)})
    assert answer.status_code == 409 and answer.get_json()["code"] == "card_changed"
    assert status(pending["approval_id"]) == "pending"
    assert not device_pairing.get_device(phone.id).get("approval_fail_count")


def test_a_bad_signature_still_spends_the_nonce(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    when = int(time.time())
    bad = {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when, action_fp="1" * 32)}
    good = dict(bad, sig=phone.sign(got, when))
    assert phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"], bad).get_json()["code"] == "bad_signature"
    assert phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"], good).get_json()["code"] == "bad_nonce"


def test_five_live_challenges_at_most_per_device(client):
    phone = Enrolled(client)
    ids = [card()["approval_id"] for _ in range(6)]
    codes = [phone.challenge(i).status_code for i in ids]
    assert codes == [200] * 5 + [429]


# ── counters ────────────────────────────────────────────────────────────────

def _bad(phone, pending):
    got = phone.challenge(pending["approval_id"]).get_json()
    when = int(time.time())
    return phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                      {"decision": "approve", "nonce": got["nonce"], "time": when,
                       "sig": phone.sign(got, when, action_fp="2" * 32)})


def test_the_fifth_bad_signature_withdraws_the_right(client):
    phone = Enrolled(client)
    pending = card()
    for _ in range(4):
        _bad(phone, pending)
    assert device_pairing.can_approve(device_pairing.get_device(phone.id))
    _bad(phone, pending)
    assert not device_pairing.has_right(device_pairing.get_device(phone.id), "pc_approve")
    assert device_pairing.approval_key(device_pairing.get_device(phone.id)) is None


def test_a_good_signature_resets_the_count(client):
    phone = Enrolled(client)
    for _ in range(4):
        _bad(phone, card())
    assert phone.approve(card()["approval_id"]).get_json()["won"] is True
    for _ in range(4):
        _bad(phone, card())
    assert device_pairing.can_approve(device_pairing.get_device(phone.id))


# ── the list and the feed ───────────────────────────────────────────────────

def test_the_list_says_which_cards_can_be_approved_here(client):
    phone = Enrolled(client)
    ordinary, search = card(), card(public_query={"q": "x"})
    listing = {c["approval_id"]: c for c in phone.get("/tv/v1/approvals?status=pending").get_json()["cards"]}
    assert listing[ordinary["approval_id"]]["can_approve_here"] is True
    assert listing[search["approval_id"]]["can_approve_here"] is False
    assert "cannot be approved from here" in listing[search["approval_id"]]["approve_blocked_because"]


def test_a_private_card_shows_only_its_generic_title_without_pc_approve(client):
    phone = Enrolled(client)
    private = ap.create_approval(kind="tool_confirm", subject_type="t", subject_id=uuid.uuid4().hex,
                                 title="Send the invoice to alex@example.com", description="secret words",
                                 action_description="send", payload={"tool": "send_email"}, force_gate=True)
    shown = phone.get("/tv/v1/approvals?status=pending").get_json()["cards"][0]
    assert "alex@example.com" in shown["title"]
    device_pairing.set_right(phone.id, "pc_approve", False)
    hidden = phone.get("/tv/v1/approvals?status=pending").get_json()["cards"][0]
    assert hidden["approval_id"] == private["approval_id"]
    assert "alex@example.com" not in json.dumps(hidden) and "secret words" not in json.dumps(hidden)
    assert hidden["can_approve_here"] is False


def test_the_list_needs_the_read_right(client):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Phone", kind="phone")
    household.bind_device(tv.id, "stephen")
    answer = client.get("/tv/v1/approvals?status=pending", headers=auth(open_session(client, tv)))
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


def test_the_feed_starts_with_the_list_and_ends_when_the_right_is_withdrawn(client):
    phone = Enrolled(client)
    pending = card()
    response = client.get("/tv/v1/approvals/events", headers=auth(open_session(client, phone.tv)), buffered=False)
    first = json.loads(next(response.response).decode().split("data: ", 1)[1])
    assert first["type"] == "snapshot" and first["cards"][0]["approval_id"] == pending["approval_id"]
    device_pairing.set_right(phone.id, "pc_read", False)
    approval_feed.publish({"type": "beat"})
    ended = json.loads(next(response.response).decode().split("data: ", 1)[1])
    assert ended["type"] == "revoked"
    response.close()


def test_a_card_resealed_between_challenge_and_decide_is_refused_by_the_signed_fingerprint(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    ap._patch(pending["approval_id"], payload={"edited": True})          # edited by this module: seal and fingerprint hold
    when = int(time.time())
    answer = phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
                        {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)})
    assert answer.status_code == 409 and answer.get_json()["code"] == "card_changed"
    assert status(pending["approval_id"]) == "pending"


def test_the_signed_fingerprint_is_checked_under_the_decision_lock():
    pending = card()
    rec, won = ap.decide_with_outcome(pending["approval_id"], "approve", decided_by="owner", expect_fp="0" * 32)
    assert won is False and rec["status"] == "pending"


def test_a_decision_the_pc_declines_is_not_reported_as_already_decided(client, monkeypatch):
    phone = Enrolled(client)
    pending = card()
    monkeypatch.setattr(ap, "_everything_stopped", lambda: True)
    answer = phone.approve(pending["approval_id"])
    assert answer.status_code == 409 and answer.get_json()["code"] == "not_approved"
    assert status(pending["approval_id"]) == "pending" and rows("device_approval_refused")[-1]["reason"] == "not_approved"


def test_the_receipt_names_the_device_and_its_key(client):
    phone = Enrolled(client)
    pending = card()
    phone.approve(pending["approval_id"])
    receipt = rows("device_approved")[-1]
    assert receipt["device_name"] == "Stephen's phone" and receipt["key_id"] == device_pairing.approval_key(
        device_pairing.get_device(phone.id)).key_id


def test_the_signature_is_kept_on_the_decision_record(client):
    phone = Enrolled(client)
    pending = card()
    got = phone.challenge(pending["approval_id"]).get_json()
    when = int(time.time())
    signature = phone.sign(got, when)
    phone.post("/tv/v1/approvals/%s/decide" % pending["approval_id"],
               {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": signature})
    assert ap.get_approval(pending["approval_id"])["decided_proof"] == {
        "approval_id": pending["approval_id"], "action_fp": got["action_fp"], "nonce": got["nonce"],
        "signed_time": when, "signature_b64": signature,
        "key_id": device_pairing.approval_key(device_pairing.get_device(phone.id)).key_id}


def test_a_card_that_vanishes_after_verification_is_a_404_not_already_decided(client, monkeypatch):
    phone = Enrolled(client)
    pending = card()
    monkeypatch.setattr(ap, "decide_with_outcome", lambda *args, **kwargs: (None, False))
    answer = phone.approve(pending["approval_id"])
    assert answer.status_code == 404 and answer.get_json()["code"] == "unknown_card"
