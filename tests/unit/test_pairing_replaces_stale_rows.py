"""A device with no valid pairing can pair again and replaces its own stale row; the same phone is not listed twice.

TV lane, 2026-11-13: a TV that reported "not paired" was refused with "this TV is already paired; remove it
on the PC to pair again" (its key was still in the store, its token was not valid) and could never recover.
The store also held two rows for Stephen's one phone (the app re-paired with a new key and a new name:
"Friday on Pixel 10", then "Agent Friday on Pixel 10"). The promises guarded here:

* pair/start no longer refuses a key that is already in the store; the owner's screen press is the confirmation,
  and the new pairing replaces that row (same id, rights as a fresh pairing, bound again);
* a pairing for the same household member, of the same kind, with the same device name (the "Agent " prefix
  aside) replaces the older row, and the card the owner presses names what it replaces before the press;
* a row of another kind, another member or another name is never replaced;
* nothing is replaced without the verified press (an unverified or denied card changes nothing).
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
    household.ensure_default_members()
    yield
    pairing._reset_for_tests()
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def begin(client, dev, name="A device", **body):
    r = client.post("/tv/v1/pair/start", json=dict({"device_spki": base64.b64encode(dev.spki).decode(), "name": name}, **body))
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["pairing_id"]
    r2 = client.post("/tv/v1/pair/reveal", json={"pairing_id": pid, "nt": base64.b64encode(os.urandom(32)).decode()})
    assert r2.status_code == 200, r2.get_json()
    return pid, ap.get_approval(pairing.pending_card(pid))


def press(card, how="approve", by="owner:ui"):
    with pressed():
        ap.decide_with_outcome(card["approval_id"], how, decided_by=by)


def paired(client, name, kind, dev=None):
    dev = dev or FakeTv()
    _pid, card = begin(client, dev, name, kind=kind)
    press(card)
    assert device_pairing.get_device(dev.id) is not None
    return dev


def ids():
    return {d["id"] for d in device_pairing.list_devices()}


def test_a_key_already_in_the_store_can_pair_again_and_replaces_its_own_row(client):
    tv = paired(client, "Living-room TV", "tv")
    device_pairing.set_right(tv.id, "studio", True)
    pid, card = begin(client, tv, "Living-room TV", kind="tv")        # was: 409 "already paired; remove it on the PC"
    assert "replaces the old pairing" in card["description"] and "Living-room TV" in card["description"]
    press(card)
    assert ids() == {tv.id}
    assert device_pairing.get_device(tv.id)["rights"] == ["chat_read"]            # a fresh pairing, rights reset
    assert household.member_for_device(tv.id) == "family"


def test_the_same_phone_paired_again_with_a_new_key_and_name_replaces_the_old_row(client):
    old = paired(client, "Friday on Pixel 10", "phone")
    new = FakeTv()
    _pid, card = begin(client, new, "Agent Friday on Pixel 10", kind="phone")
    assert "Friday on Pixel 10" in card["description"] and "replaces the old pairing" in card["description"]
    press(card)
    assert ids() == {new.id}                                                       # one phone, not two
    assert household.member_for_device(new.id) == "stephen"
    assert device_pairing.get_device(old.id) is None


def test_the_replaced_rows_sessions_die_with_it(client):
    old = paired(client, "Friday on Pixel 10", "phone")
    headers = auth(open_session(client, old))
    _pid, card = begin(client, FakeTv(), "Friday on Pixel 10", kind="phone")
    press(card)
    assert client.get("/tv/v1/status", headers=headers).status_code == 401


@pytest.mark.parametrize("other_name,other_kind", [("Friday on Pixel 10", "tablet"), ("Kitchen tablet", "phone")])
def test_a_row_of_another_kind_or_name_is_never_replaced(client, other_name, other_kind):
    keep = paired(client, other_name, other_kind)
    _pid, card = begin(client, FakeTv(), "Friday on Pixel 10", kind="phone")
    assert "replaces" not in card["description"]
    press(card)
    assert keep.id in ids() and len(ids()) == 2


def test_a_row_of_another_member_is_never_replaced(client):
    tv = paired(client, "Pixel", "tv")                                             # family
    _pid, card = begin(client, FakeTv(), "Pixel", kind="phone")                    # the owner's
    press(card)
    assert tv.id in ids() and len(ids()) == 2


@pytest.mark.parametrize("how,by", [("approve", "owner"), ("approve", "tv_gateway"), ("deny", "owner:ui")])
def test_nothing_is_replaced_without_the_verified_press(client, how, by):
    old = paired(client, "Friday on Pixel 10", "phone")
    _pid, card = begin(client, FakeTv(), "Friday on Pixel 10", kind="phone")
    press(card, how, by)
    assert ids() == {old.id}
