"""The owner opens pairing at the PC: a press on Friday's own page, never a script, a tunnel or the LAN.

Opening pairing lets a new phone, tablet or watch ask for every right, so it is the owner's act, shown on this PC's own
screen. Closing it is always safe and needs no press. See tests/unit/test_pairing_window.py for what the window does.
"""
import pytest

from tests.desk_press_support import desk_headers, page_headers


@pytest.fixture(autouse=True)
def _window_starts_closed():
    from agent_friday.services import device_pairing
    device_pairing._reset_for_tests()
    yield
    device_pairing._reset_for_tests()


def test_a_plain_request_does_not_open_pairing(client):
    refused = client.post("/api/pairing-window", json={"seconds": 300})
    assert refused.status_code == 403 and refused.get_json()["code"] == "pairing_window_needs_press"
    assert client.get("/api/pairing-window").get_json()["open"] is False


def test_a_page_that_has_the_token_but_not_this_pcs_desk_key_does_not_open_it(client):
    refused = client.post("/api/pairing-window", json={"seconds": 300}, headers=page_headers())
    assert refused.status_code == 403 and refused.get_json()["code"] == "pairing_window_needs_press"
    assert client.get("/api/pairing-window").get_json()["open"] is False


def test_a_press_on_this_pcs_own_page_opens_it_for_the_time_asked(client):
    opened = client.post("/api/pairing-window", json={"seconds": 120}, headers=desk_headers())
    assert opened.status_code == 200, opened.data
    assert 100 <= opened.get_json()["remaining_s"] <= 120
    state = client.get("/api/pairing-window").get_json()
    assert state["open"] is True and state["remaining_s"] <= 120


def test_a_press_through_a_tunnel_or_forward_does_not_open_it(client):
    headers = dict(desk_headers(), **{"X-Forwarded-For": "203.0.113.9"})
    refused = client.post("/api/pairing-window", json={"seconds": 300}, headers=headers)
    assert refused.status_code == 403 and client.get("/api/pairing-window").get_json()["open"] is False


def test_closing_needs_no_press(client):
    client.post("/api/pairing-window", json={"seconds": 300}, headers=desk_headers())
    closed = client.delete("/api/pairing-window")
    assert closed.status_code == 200 and closed.get_json()["open"] is False
    assert client.get("/api/pairing-window").get_json()["open"] is False
