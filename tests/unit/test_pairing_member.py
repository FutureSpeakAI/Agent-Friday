"""The member a device is signed in as is chosen at pairing time, by the PC, and bound by the press.

Stephen, 2026-11-12 (via Dispatch): "owner = Stephen for the tablet, family for the TV", and one
pairing is the whole setup. The promises guarded here:

* the PC picks the member from the device KIND (tablet, phone, watch -> the owner, Stephen; tv ->
  the household's shared "family" member). The device declares only its kind, never a member;
* the screen-only pairing card names the member and what the device will receive;
* the binding is written only by the verified press on this PC; a refused or ignored card binds nothing;
* GET /tv/v1/inherit is mounted on the gateway app, so a paired device asks once and gets its own
  member's setup, never another member's.
"""
import base64
import os

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing
from tests.desk_press_support import pressed
from tests.unit.test_tv_gateway import FakeTv, auth, open_session


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    pairing._reset_for_tests()
    yield
    pairing._reset_for_tests()
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()      # inherit is mounted by create_app, not by the test


def begin(client, dev, **body):
    r = client.post("/tv/v1/pair/start", json=dict(
        {"device_spki": base64.b64encode(dev.spki).decode(), "name": "A device"}, **body))
    if r.status_code != 200:
        return r, None
    pid = r.get_json()["pairing_id"]
    r2 = client.post("/tv/v1/pair/reveal", json={
        "pairing_id": pid, "nt": base64.b64encode(os.urandom(32)).decode()})
    assert r2.status_code == 200, r2.get_json()
    return r, ap.get_approval(pairing.pending_card(pid))


def press(card):
    with pressed():
        rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")
    assert won and rec["status"] == "approved"


def seed():
    household.ensure_default_members()
    household.set_area("stephen", "chats", [{"id": "s1", "title": "STEPHEN-ONLY-CHAT"}])
    household.set_area("stephen", "settings", {"theme": "owner-theme"})
    household.set_area("family", "settings", {"text_size": "large"})
    household.set_shared("arcade", {"library": ["pacman"]})


@pytest.mark.parametrize("kind", ["tablet", "phone", "watch"])
def test_a_personal_device_is_signed_in_as_the_owner(client, kind):
    seed()
    dev = FakeTv()
    _, card = begin(client, dev, kind=kind)
    assert "Stephen" in card["description"]
    press(card)
    assert household.member_for_device(dev.id) == "stephen"
    b = client.get("/tv/v1/inherit", headers=auth(open_session(client, dev))).get_json()
    assert b["member"]["id"] == "stephen" and [c["id"] for c in b["chats"]] == ["s1"]
    assert b["shared"]["arcade"]["library"] == ["pacman"]


@pytest.mark.parametrize("body", [{}, {"kind": "tv"}])
def test_the_tv_is_signed_in_as_the_family_member_and_never_sees_the_owners_data(client, body):
    seed()
    dev = FakeTv()
    _, card = begin(client, dev, **body)
    assert "family" in card["description"].lower()
    press(card)
    assert household.member_for_device(dev.id) == "family"
    r = client.get("/tv/v1/inherit", headers=auth(open_session(client, dev)))
    b = r.get_json()
    assert b["member"]["id"] == "family" and b["settings"] == {"text_size": "large"}
    text = r.get_data(as_text=True)
    assert "STEPHEN-ONLY-CHAT" not in text and "owner-theme" not in text


def test_a_device_cannot_choose_its_member_by_asking(client):
    seed()
    dev = FakeTv()
    _, card = begin(client, dev, kind="tv", member="stephen", member_id="stephen", principal="owner")
    press(card)
    assert household.member_for_device(dev.id) == "family"


def test_an_unknown_kind_is_refused_before_any_card_is_raised(client):
    seed()
    r, card = begin(client, FakeTv(), kind="toaster")
    assert r.status_code == 400 and card is None


@pytest.mark.parametrize("label", ["owner", "owner:chat", "tv_gateway"])
def test_an_unpressed_or_unverified_card_binds_nothing(client, label):
    seed()
    dev = FakeTv()
    _, card = begin(client, dev, kind="tablet")
    ap.decide_with_outcome(card["approval_id"], "approve", decided_by=label)
    assert household.member_for_device(dev.id) is None
    assert device_pairing.list_devices() == []


def test_a_declined_card_binds_nothing(client):
    seed()
    dev = FakeTv()
    _, card = begin(client, dev, kind="tablet")
    ap.decide_with_outcome(card["approval_id"], "deny", decided_by="owner:ui")
    assert household.member_for_device(dev.id) is None


def test_default_members_are_created_once_and_keep_an_owners_edits(client):
    household.ensure_default_members()
    household.set_area("stephen", "settings", {"keep": "me"})
    household.ensure_default_members()
    ids = {m["id"]: m["role"] for m in household.list_members()}
    assert ids == {"stephen": "owner", "family": "family"}
    assert household.bundle_for("stephen")["settings"] == {"keep": "me"}


def test_the_declared_kind_is_recorded_on_the_device(client):
    seed()
    dev = FakeTv()
    _, card = begin(client, dev, kind="tablet")
    press(card)
    assert device_pairing.get_device(dev.id)["kind"] == "tablet"
