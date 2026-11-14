"""The owner's own devices are paired with every right: the six-digit press is the grant.

Found 2026-11-13: Stephen's tablet paired, then its voice said "not paired yet, a grown-up needs to
activate me from the PC". Devices started with "chat and read only", and widening to the TV brain
waits for a press on a Paired-devices screen that does not exist yet. Owner decision (via Dispatch):
owner-bound devices (Stephen's phone, tablet, watch) get full owner rights at pairing. The promises:

* a phone, tablet or watch the PC signs in as the owner holds every right once the press is verified,
  and no wider-right card is raised for it;
* the card the owner presses says so, plainly, before the press;
* the family member's devices (the TV) are unchanged: chat_read first, one screen-only card per wider right;
* nothing is granted without the verified press (an unpressed, unverified or declined card adds nothing).
"""
import base64
import os

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing
from tests.desk_press_support import pressed
from tests.unit.test_tv_gateway import FakeTv


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
    return gateway.create_app().test_client()


def begin(client, dev, **body):
    r = client.post("/tv/v1/pair/start", json=dict(
        {"device_spki": base64.b64encode(dev.spki).decode(), "name": "A device"}, **body))
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["pairing_id"]
    r2 = client.post("/tv/v1/pair/reveal", json={"pairing_id": pid, "nt": base64.b64encode(os.urandom(32)).decode()})
    assert r2.status_code == 200, r2.get_json()
    return ap.get_approval(pairing.pending_card(pid))


def press(card):
    with pressed():
        rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")
    assert won and rec["status"] == "approved"


def right_cards():
    return [a for a in ap.list_approvals() if a.get("kind") == pairing.RIGHT_KIND]


@pytest.mark.parametrize("kind", ["phone", "tablet", "watch"])
def test_the_owners_personal_device_holds_every_right_after_the_press(client, kind):
    dev = FakeTv()
    press(begin(client, dev, kind=kind))
    held = device_pairing.get_device(dev.id)
    assert sorted(held["rights"]) == sorted(device_pairing.RIGHTS)
    assert all(device_pairing.has_right(held, r) for r in ("tv_brain", "studio", "tv_channels"))
    assert right_cards() == []                      # nothing left to widen, no second press asked


def test_the_card_says_it_grants_everything_before_the_press(client):
    card = begin(client, FakeTv(), kind="tablet")
    d = card["description"]
    assert "all of your rights" in d and "no second press" in d.lower() and "Studio jobs that spend money" in d


@pytest.mark.parametrize("body", [{}, {"kind": "tv"}])
def test_the_tv_keeps_the_tiered_rights(client, body):
    dev = FakeTv()
    card = begin(client, dev, **body)
    assert "all of your rights" not in card["description"] and "under Paired devices" in card["description"]
    press(card)
    assert device_pairing.get_device(dev.id)["rights"] == ["chat_read"]
    assert {c["subject_id"].split(":")[1] for c in right_cards()} == {"tv_brain", "studio", "tv_channels"}


@pytest.mark.parametrize("label", ["owner", "owner:chat", "tv_gateway"])
def test_without_the_verified_press_nothing_is_granted(client, label):
    dev = FakeTv()
    card = begin(client, dev, kind="phone")
    ap.decide_with_outcome(card["approval_id"], "approve", decided_by=label)
    assert device_pairing.list_devices() == [] and right_cards() == []


def test_a_declined_card_grants_nothing(client):
    dev = FakeTv()
    card = begin(client, dev, kind="phone")
    ap.decide_with_outcome(card["approval_id"], "deny", decided_by="owner:ui")
    assert device_pairing.list_devices() == []


def test_a_device_cannot_ask_for_the_owners_rights(client):
    """The grant follows the kind the PC accepted and the member the PC chose, never a field the device sends."""
    dev = FakeTv()
    press(begin(client, dev, kind="tv", member="stephen", rights=["tv_brain", "studio", "tv_channels"], principal="owner"))
    assert device_pairing.get_device(dev.id)["rights"] == ["chat_read"]
