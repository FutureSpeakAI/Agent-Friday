"""Opening pairing from the PC: the /pairing page and "Friday, add a device" typed into chat.

Both are the owner's, at this PC. The same words or page reached through a tunnel, a forward or another machine
do not open pairing and say, in plain words, where to go. See tests/unit/test_pairing_window.py for the window.
"""
import pytest

from agent_friday.services import add_device_command, device_pairing
from tests.desk_press_support import page_headers


@pytest.fixture(autouse=True)
def _window_starts_closed():
    device_pairing._reset_for_tests()
    yield
    device_pairing._reset_for_tests()


@pytest.mark.parametrize("words", [
    "Friday, add a device", "add a device", "Hey Friday, add a new phone", "friday add a tablet.",
    "Pair a new watch", "please add a device", "Friday, set up another device"])
def test_the_words_that_open_pairing(words):
    assert add_device_command.is_add_device(words)


@pytest.mark.parametrize("words", [
    "please don't add a device", "add a device to my calendar", "Friday, what devices are paired?",
    "remove a device", "add a device " + "x" * 200, "", None, 5])
def test_other_words_are_left_to_the_model(words):
    assert not add_device_command.is_add_device(words)


def chat(client, words, headers=None, route="/api/chat"):
    return client.post(route, json={"message": words}, headers=headers if headers is not None else page_headers())


def test_typed_on_this_pcs_page_it_opens_pairing_and_says_so(client):
    answer = chat(client, "Friday, add a device")
    assert answer.status_code == 200
    assert "Pairing is open for 5 minutes" in answer.get_json()["response"]
    assert device_pairing.pairing_window()["open"] is True


def test_the_send_route_does_the_same(client):
    assert "Pairing is open" in chat(client, "add a device", route="/api/chat/send").get_json()["response"]


def test_through_a_tunnel_or_forward_it_does_not_open_and_says_where_to_go(client):
    headers = dict(page_headers(), **{"X-Forwarded-For": "203.0.113.9"})
    answer = chat(client, "Friday, add a device", headers=headers)
    assert "at this PC" in answer.get_json()["response"] and "/pairing" in answer.get_json()["response"]
    assert device_pairing.pairing_window()["open"] is False


def test_without_the_pages_token_it_does_not_open(client):
    answer = chat(client, "Friday, add a device", headers={})
    assert device_pairing.pairing_window()["open"] is False
    assert answer.status_code in (200, 401, 403)


def test_the_page_opens_on_this_pc_with_the_token_in_it(client):
    page = client.get("/pairing", headers=page_headers())
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Open pairing for 5 minutes" in text and "Close pairing" in text and "__FRIDAY_API_TOKEN" in text
    assert "no-store" in page.headers["Cache-Control"]


def test_the_page_does_not_open_through_a_forward(client):
    headers = dict(page_headers(), **{"X-Forwarded-For": "203.0.113.9"})
    assert client.get("/pairing", headers=headers).status_code == 403
