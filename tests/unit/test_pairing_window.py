"""Pairing is only possible inside a window the owner opened at the PC, and a stranger cannot spend the real
device's budget (threat assessment 2026-11-21, fix 1).

Before: any machine on the home network could call pair/start at any time, raise a pairing card on the owner's
screen (claiming to be "Friday on Pixel 10"), and use up the three starts an hour so the real phone could not pair.
A phone, tablet or watch that is paired gets every right on one press, so the press is the only barrier and it
should only ever be asked for when the owner is the one who started things.

Now: ``pair/start`` is refused until the owner opens pairing at the PC (device_pairing.open_pairing_window; the
route is tests/api/test_pairing_window_route.py), the window closes by itself, the card says who asked and from
where, the hourly budget is per source address, and a start that never reveals frees its place after 30 seconds.
"""
import base64
import os
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import pairing

STRANGER = "192.168.1.77"
PHONE = "192.168.1.50"


def device_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
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


def begin(client, source=PHONE, name="Friday on Pixel 10", kind="phone"):
    return client.post("/tv/v1/pair/start", json={"device_spki": device_key(), "name": name, "kind": kind},
                       environ_base={"REMOTE_ADDR": source})


def finish_the_pairing_in_flight():
    """What a decline or an expiry does: the place is free again (the hourly budget is not refunded)."""
    for entry in pairing._PENDING.values():
        entry["state"] = "expired"


def test_pairing_is_refused_until_the_owner_opens_it_at_the_pc(client):
    refused = begin(client)
    assert refused.status_code == 409
    body = refused.get_json()
    assert body["code"] == "pairing_closed" and "add a device" in body["error"]
    assert not pairing._PENDING and not pairing._STARTS          # a refused start costs nothing and leaves nothing


def test_an_open_window_lets_a_device_start_and_closes_by_itself(client, monkeypatch):
    device_pairing.open_pairing_window(60)
    assert begin(client).status_code == 200
    finish_the_pairing_in_flight()
    later = time.time() + 61
    monkeypatch.setattr(time, "time", lambda: later)
    assert begin(client, source="192.168.1.60").get_json()["code"] == "pairing_closed"
    assert device_pairing.pairing_window()["open"] is False


def reveal(client, pairing_id, source=PHONE):
    return client.post("/tv/v1/pair/reveal", json={"pairing_id": pairing_id, "nt": base64.b64encode(
        os.urandom(32)).decode()}, environ_base={"REMOTE_ADDR": source})


def test_closing_the_window_ends_a_pairing_that_has_not_been_pressed(client):
    device_pairing.open_pairing_window(300)
    started = begin(client).get_json()["pairing_id"]
    device_pairing.close_pairing_window()
    assert begin(client, source="192.168.1.60").get_json()["code"] == "pairing_closed"
    assert reveal(client, started).status_code == 409           # a stranger's start does not outlive the close


def test_closing_the_window_also_ends_a_card_that_is_waiting_for_the_press(client):
    device_pairing.open_pairing_window(300)
    started = begin(client).get_json()["pairing_id"]
    assert reveal(client, started).status_code == 200
    device_pairing.close_pairing_window()
    assert pairing.status(started)["state"] == "expired"


def test_the_window_is_never_longer_than_fifteen_minutes_or_shorter_than_thirty_seconds():
    assert device_pairing.open_pairing_window(10 ** 6)["remaining_s"] <= 900
    assert device_pairing.open_pairing_window(1)["remaining_s"] >= 29
    assert device_pairing.pairing_window()["open"] is True


def test_the_hello_stays_as_it_was_and_does_not_tell_a_stranger_whether_pairing_is_open(client):
    device_pairing.open_pairing_window(60)
    assert "pairing_open" not in client.get("/tv/v1/hello").get_json()


def test_the_card_names_who_asked_and_from_where(client):
    device_pairing.open_pairing_window(300)
    pairing_id = begin(client, source=PHONE, name="Friday on Pixel 10").get_json()["pairing_id"]
    client.post("/tv/v1/pair/reveal", json={"pairing_id": pairing_id, "nt": base64.b64encode(os.urandom(32)).decode()},
                environ_base={"REMOTE_ADDR": PHONE})
    card = ap.get_approval(pairing.pending_card(pairing_id))
    assert PHONE in card["description"] and "Friday on Pixel 10" in card["description"]
    assert card["payload"]["remote"] == PHONE
    assert "did not ask for this" in card["description"]          # what to do if it was not the owner's phone


def test_a_strangers_starts_do_not_use_up_a_real_devices_budget(client):
    device_pairing.open_pairing_window(900)
    for _ in range(pairing.MAX_STARTS_PER_HOUR):
        assert begin(client, source=STRANGER).status_code == 200
        finish_the_pairing_in_flight()
    blocked = begin(client, source=STRANGER)
    assert blocked.status_code == 409 and "too many pairing attempts" in blocked.get_json()["error"]
    assert begin(client, source=PHONE).status_code == 200          # the owner's own phone is not locked out


def test_all_sources_together_stop_at_a_larger_hourly_ceiling_of_pairings_that_reached_a_card(client):
    device_pairing.open_pairing_window(900)
    for number in range(pairing.MAX_STARTS_PER_HOUR_ALL):
        source = "192.168.1.%d" % (100 + number)
        assert reveal(client, begin(client, source=source).get_json()["pairing_id"], source).status_code == 200
        finish_the_pairing_in_flight()
    assert begin(client, source="192.168.1.250").status_code == 409


def test_starts_that_never_reveal_cannot_use_up_the_shared_ceiling(client):
    device_pairing.open_pairing_window(900)
    for number in range(pairing.MAX_STARTS_PER_HOUR_ALL + 3):
        assert begin(client, source="192.168.1.%d" % (100 + number)).status_code == 200
        finish_the_pairing_in_flight()
    assert begin(client, source=PHONE).status_code == 200      # the owner's phone still gets in


def test_the_owner_opening_pairing_again_clears_what_strangers_used(client):
    device_pairing.open_pairing_window(900)
    for _ in range(pairing.MAX_STARTS_PER_HOUR):
        begin(client, source=STRANGER)
        finish_the_pairing_in_flight()
    assert begin(client, source=STRANGER).status_code == 409
    device_pairing.open_pairing_window(900)
    assert begin(client, source=STRANGER).status_code == 200


def test_a_device_name_cannot_put_words_or_quotes_into_the_card(client):
    device_pairing.open_pairing_window(300)
    newline, bell = chr(10), chr(7)
    evil = 'Phone' + chr(34) + newline + 'This is your phone, safe to Approve <b>now</b>' + bell
    pairing_id = begin(client, name=evil).get_json()["pairing_id"]
    reveal(client, pairing_id)
    card = ap.get_approval(pairing.pending_card(pairing_id))
    asked = card["description"].split("Asked by ", 1)[1].split(" from ", 1)[0].strip(chr(34))
    assert newline not in asked and chr(34) not in asked and "<" not in asked and bell not in asked
    assert pairing.clean_name("") == "TV" and len(pairing.clean_name("x" * 200)) == 60


def test_only_the_press_route_opens_pairing_and_nothing_else_calls_the_opening_functions():
    """set_gateway(True) and open_pairing_window carry no press check of their own: whoever calls them must."""
    import pathlib
    src = pathlib.Path(device_pairing.__file__).parents[1]
    callers = set()
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "open_pairing_window(" in text or ".set_gateway(" in text:
            callers.add(path.relative_to(src).as_posix())
    assert callers == {"routes/control.py", "services/device_pairing.py", "services/add_device_command.py"}, callers


def test_a_start_that_never_reveals_does_not_hold_the_place_for_five_minutes(client, monkeypatch):
    device_pairing.open_pairing_window(900)
    assert begin(client, source=STRANGER).status_code == 200
    assert begin(client, source=PHONE).get_json()["error"] == "another device is pairing right now"
    later = time.time() + pairing.REVEAL_WITHIN_S + 1
    monkeypatch.setattr(time, "time", lambda: later)
    assert begin(client, source=PHONE).status_code == 200


def test_turning_the_gateway_on_opens_pairing_for_ten_minutes_and_off_closes_it():
    device_pairing.set_gateway(True, 47810)
    assert 0 < device_pairing.pairing_window()["remaining_s"] <= 600
    device_pairing.close_pairing_window()
    device_pairing.set_gateway(True, 47810)                        # already on: not a reason to open pairing again
    assert device_pairing.pairing_window()["open"] is False
    device_pairing.open_pairing_window(300)
    device_pairing.set_gateway(False)
    assert device_pairing.pairing_window()["open"] is False


def test_opening_and_closing_the_window_are_receipted(monkeypatch):
    from agent_friday.services import action_ledger
    rows = []
    monkeypatch.setattr(action_ledger, "append", lambda kind, body, *a, **k: rows.append((kind, body)))
    device_pairing.open_pairing_window(120, by="owner:desk")
    device_pairing.close_pairing_window(by="owner:desk")
    assert [kind for kind, _ in rows] == ["pairing_window_opened", "pairing_window_closed"]
    assert rows[0][1]["seconds"] == 120 and rows[0][1]["principal"] == "owner:desk"
