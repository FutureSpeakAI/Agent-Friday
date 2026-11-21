"""A paired device must be reconfirmed by the owner every 30 days (threat assessment 2026-11-21, fix 3).

Before: every paired row stored ``reconfirm_by`` (30 days after pairing) and nothing ever read it, so a phone paired
once stayed paired for good, with every right it was given, until the owner thought to remove it.

Now: a device within 7 days of its date gets a screen-only "Keep this device paired?" card on the PC; a press
extends it 30 days; past the date the device cannot open a session (403 ``reconfirm_needed``, said in plain words
so the app can tell its user) and a live session ends. Only the owner's press at the PC counts. Rows paired before
this shipped get one fresh week, so nothing is locked out the moment it ships.
"""
import time

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from tests.desk_press_support import pressed
from tests.unit.test_tv_gateway import FakeTv, auth

DAY = 86400


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def pair(due_in_days: float):
    """A paired device whose reconfirm date is ``due_in_days`` from now (negative: already past)."""
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Living room TV")
    body = device_pairing._load()
    body["reconfirm_grace_done"] = True                 # the one-time week for old rows is not this test's business
    body["devices"][tv.id]["reconfirm_by"] = time.time() + due_in_days * DAY
    device_pairing._save(body)
    return tv


def try_open(client, tv):
    nonce = client.post("/tv/v1/session/challenge").get_json()["nonce"]
    return client.post("/tv/v1/session/open", json={"device_id": tv.id, "nonce": nonce, "sig": tv.sign(nonce)})


def cards(tv):
    return [c for c in ap.list_approvals(status="pending", kind=getattr(device_pairing, "RECONFIRM_CARD_KIND", "x"))
            if (c.get("payload") or {}).get("device_id") == tv.id]


def press(card):
    with pressed():
        rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")
    assert won and rec["status"] == "approved"


def test_a_device_well_inside_its_30_days_opens_a_session_and_raises_no_card(client):
    tv = pair(due_in_days=20)
    assert try_open(client, tv).status_code == 200
    assert cards(tv) == []


def test_a_device_in_its_last_week_still_opens_a_session_and_the_owner_is_asked_once(client):
    tv = pair(due_in_days=5)
    assert try_open(client, tv).status_code == 200
    assert try_open(client, tv).status_code == 200
    [card] = cards(tv)                                        # asked once, not once per connection
    assert "Living room TV" in card["title"] and "30" in card["description"]


def test_a_device_past_its_date_cannot_open_a_session_and_is_told_why(client):
    tv = pair(due_in_days=-1)
    refused = try_open(client, tv)
    assert refused.status_code == 403
    body = refused.get_json()
    assert body["code"] == "reconfirm_needed" and "press" in body["error"] and "PC" in body["error"]
    assert len(cards(tv)) == 1                                # and the card is waiting on the PC


def test_someone_who_cannot_sign_does_not_learn_that_a_device_has_expired(client):
    tv = pair(due_in_days=-1)
    nonce = client.post("/tv/v1/session/challenge").get_json()["nonce"]
    stranger = FakeTv()
    refused = client.post("/tv/v1/session/open",
                          json={"device_id": tv.id, "nonce": nonce, "sig": stranger.sign(nonce, tv.id)})
    assert refused.status_code == 401 and refused.get_json().get("code") != "reconfirm_needed"
    assert cards(tv) == []


def test_the_owners_press_on_the_card_gives_it_30_more_days(client):
    tv = pair(due_in_days=-1)
    try_open(client, tv)
    press(cards(tv)[0])
    left = (device_pairing.get_device(tv.id)["reconfirm_by"] - time.time()) / DAY
    assert 29.9 < left <= 30.0
    assert try_open(client, tv).status_code == 200


@pytest.mark.parametrize("label", ["owner:voice", "owner:chat", "tv_gateway", "device:abc"])
def test_only_a_press_on_the_screen_counts(client, label):
    tv = pair(due_in_days=-1)
    try_open(client, tv)
    ap.decide_with_outcome(cards(tv)[0]["approval_id"], "approve", decided_by=label)
    assert try_open(client, tv).status_code == 403


def test_the_decision_hook_checks_the_label_itself_whatever_the_approvals_layer_did():
    from agent_friday.tv_gateway import pairing
    tv = pair(due_in_days=-1)
    before = device_pairing.get_device(tv.id)["reconfirm_by"]
    for label in ("owner:voice", "tv_gateway", "device:abc"):
        pairing._on_reconfirm_decision({"status": "approved", "decided_by": label, "approval_id": "x",
                                        "payload": {"device_id": tv.id}})
    pairing._on_reconfirm_decision({"status": "denied", "decided_by": "owner:ui", "approval_id": "x",
                                    "payload": {"device_id": tv.id}})
    assert device_pairing.get_device(tv.id)["reconfirm_by"] == before


def test_declining_the_card_changes_nothing(client):
    tv = pair(due_in_days=-1)
    try_open(client, tv)
    with pressed():
        ap.decide_with_outcome(cards(tv)[0]["approval_id"], "deny", decided_by="owner:ui")
    assert try_open(client, tv).status_code == 403


def test_a_live_session_ends_when_the_device_lapses(client):
    tv = pair(due_in_days=1)
    token = try_open(client, tv).get_json()["session"]
    assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 200
    body = device_pairing._load()
    body["devices"][tv.id]["reconfirm_by"] = time.time() - 1          # the date passes while it is connected
    device_pairing._save(body)
    assert client.get("/tv/v1/whoami", headers=auth(token)).status_code == 401


def test_a_restart_does_not_stack_a_second_card_for_the_same_device(client):
    tv = pair(due_in_days=-1)
    try_open(client, tv)
    device_pairing._RECONFIRM_ASKED.clear()                   # the in-memory cooldown is gone after a restart
    try_open(client, tv)
    assert len(cards(tv)) == 1


def test_a_card_the_owner_has_not_answered_is_not_joined_by_a_second_after_a_restart_and_hours(client, monkeypatch):
    tv = pair(due_in_days=-1)
    try_open(client, tv)
    later = time.time() + 7 * 3600                              # past the ask window, and the process restarted
    monkeypatch.setattr(time, "time", lambda: later)
    device_pairing._RECONFIRM_ASKED.clear()
    try_open(client, tv)
    assert len(cards(tv)) == 1


def test_a_card_that_could_not_be_made_is_tried_again_not_held_off_for_hours(client, monkeypatch):
    tv = pair(due_in_days=-1)
    real = ap.create_approval
    monkeypatch.setattr(ap, "create_approval", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    try_open(client, tv)
    assert cards(tv) == []
    monkeypatch.setattr(ap, "create_approval", real)
    try_open(client, tv)
    assert len(cards(tv)) == 1


def test_a_stored_device_name_cannot_put_quotes_or_new_lines_into_the_card(client):
    tv = pair(due_in_days=-1)
    body = device_pairing._load()
    body["devices"][tv.id]["name"] = 'TV"' + chr(10) + "Approve me <now>"
    device_pairing._save(body)
    try_open(client, tv)
    [card] = cards(tv)
    for text in (card["title"], card["description"]):
        assert chr(10) not in text and "<" not in text and text.count(chr(34)) <= 2


def test_a_device_can_be_kept_again_every_cycle_not_only_the_first_time(client, monkeypatch):
    tv = pair(due_in_days=-1)
    try_open(client, tv)
    press(cards(tv)[0])
    assert try_open(client, tv).status_code == 200
    month_later = time.time() + 31 * DAY
    monkeypatch.setattr(time, "time", lambda: month_later)            # the 30 days pass and it is due again
    assert try_open(client, tv).status_code == 403
    [second] = cards(tv)                                              # a NEW card, not the approved one again
    press(second)
    assert try_open(client, tv).status_code == 200
    assert (device_pairing.get_device(tv.id)["reconfirm_by"] - month_later) / DAY > 29.9


def test_after_a_decline_the_owner_is_asked_again_later(client, monkeypatch):
    tv = pair(due_in_days=-1)
    try_open(client, tv)
    with pressed():
        ap.decide_with_outcome(cards(tv)[0]["approval_id"], "deny", decided_by="owner:ui")
    assert cards(tv) == []
    assert try_open(client, tv).status_code == 403 and cards(tv) == []     # not again within the cooldown
    later = time.time() + device_pairing.RECONFIRM_ASK_EVERY_S + 60
    monkeypatch.setattr(time, "time", lambda: later)
    assert try_open(client, tv).status_code == 403
    assert len(cards(tv)) == 1                                        # a changed mind has a card to press


def test_removing_the_device_takes_its_reconfirm_card_with_it(client):
    tv = pair(due_in_days=-1)
    try_open(client, tv)
    assert len(cards(tv)) == 1
    device_pairing.remove_device(tv.id)
    assert cards(tv) == []


def test_old_rows_get_one_fresh_week_the_first_time_and_never_again():
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Old phone")
    body = device_pairing._load()
    body["devices"][tv.id]["reconfirm_by"] = time.time() - 40 * DAY      # paired long before enforcement
    body.pop("reconfirm_grace_done", None)
    device_pairing._save(body)
    first = device_pairing.get_device(tv.id)["reconfirm_by"] - time.time()
    assert 6.9 * DAY < first <= 7 * DAY
    body = device_pairing._load()
    assert body["reconfirm_grace_done"] is True
    body["devices"][tv.id]["reconfirm_by"] = time.time() - DAY           # lapsed later: no second grace
    device_pairing._save(body)
    assert device_pairing.get_device(tv.id)["reconfirm_by"] < time.time()


def test_a_store_made_after_this_shipped_never_excuses_a_lapse(client):
    assert device_pairing._empty()["reconfirm_grace_done"] is True      # a new store is born with the flag set
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="New phone")
    body = device_pairing._load()
    body["devices"][tv.id]["reconfirm_by"] = time.time() - DAY
    device_pairing._save(body)
    assert try_open(client, tv).status_code == 403


def test_a_row_with_no_date_gets_30_days_from_when_it_was_paired():
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="Phone")
    body = device_pairing._load()
    body["devices"][tv.id].pop("reconfirm_by")
    body.pop("reconfirm_grace_done", None)
    paired_at = body["devices"][tv.id]["paired_at"] = time.time() - 10 * DAY
    device_pairing._save(body)
    assert abs(device_pairing.get_device(tv.id)["reconfirm_by"] - (paired_at + 30 * DAY)) < 5
